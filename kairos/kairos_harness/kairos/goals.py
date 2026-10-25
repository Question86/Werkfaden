from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    EVIDENCE_DOCUMENT_TYPES,
    GOAL_SCHEMA_VERSION,
    MAX_CRITERIA_PER_GOAL,
    MAX_GOAL_FILES,
    MAX_GOAL_FILE_BYTES,
    MAX_GOAL_TEXT_CHARS,
    MAX_MILESTONES_PER_GOAL,
    MAX_TOTAL_GOAL_BYTES,
)
from .database import KnowledgeDatabase
from .util import atomic_write_json, json_dumps, read_json, utc_now, workspace_relative


class GoalError(ValueError):
    pass


@dataclass(frozen=True)
class GoalReconcileResult:
    changed: tuple[Path, ...]
    missing: tuple[str, ...]
    current_stats: dict[str, dict[str, int]]
    previous_manifest: dict[str, Any]


def goal_source_paths(workspace: Path) -> list[Path]:
    goal_dir = workspace / "goals"
    if not goal_dir.exists():
        return []
    paths = sorted(path.resolve() for path in goal_dir.glob("*.json") if path.is_file())
    if len(paths) > MAX_GOAL_FILES:
        raise GoalError(f"goal file count exceeds {MAX_GOAL_FILES}")
    for path in paths:
        if path.stat().st_size > MAX_GOAL_FILE_BYTES:
            raise GoalError(f"goal file exceeds {MAX_GOAL_FILE_BYTES} bytes: {path.name}")
    total_bytes = sum(path.stat().st_size for path in paths)
    if total_bytes > MAX_TOTAL_GOAL_BYTES:
        raise GoalError(f"goal source bytes exceed {MAX_TOTAL_GOAL_BYTES}")
    return paths


def reconcile_goal_files(workspace: Path) -> GoalReconcileResult:
    manifest_path = workspace / ".kairos" / "goal_manifest.json"
    previous = read_json(manifest_path, default={}) or {}
    previous_entries = previous.get("goals", {})
    current_stats: dict[str, dict[str, int]] = {}
    changed: list[Path] = []
    for path in goal_source_paths(workspace):
        relative = workspace_relative(path, workspace)
        stat = path.stat()
        signature = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        current_stats[relative] = signature
        old = previous_entries.get(relative)
        if not old or any(int(old.get(key, -1)) != value for key, value in signature.items()):
            changed.append(path)
    return GoalReconcileResult(
        changed=tuple(changed),
        missing=tuple(sorted(set(previous_entries) - set(current_stats))),
        current_stats=current_stats,
        previous_manifest=previous,
    )


def update_goal_manifest(
    workspace: Path,
    result: GoalReconcileResult,
    *,
    updated_at: str,
) -> None:
    if result.missing:
        raise GoalError("cannot accept a goal manifest with missing source files")
    if not result.changed and result.previous_manifest:
        return
    atomic_write_json(
        workspace / ".kairos" / "goal_manifest.json",
        {
            "schema": "kairos-goal-reconciliation-manifest/v1",
            "goals": result.current_stats,
            "missing_sources": [],
            "updated_at": updated_at,
        },
    )


def validate_goal(payload: dict[str, Any]) -> None:
    if payload.get("schema") != GOAL_SCHEMA_VERSION:
        raise GoalError(f"goal schema must be {GOAL_SCHEMA_VERSION!r}")
    for key in ("id", "title", "objective", "state"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise GoalError(f"goal.{key} must be a non-empty string")
        if len(payload[key]) > MAX_GOAL_TEXT_CHARS:
            raise GoalError(f"goal.{key} exceeds {MAX_GOAL_TEXT_CHARS} characters")
    if not re.fullmatch(r"GOAL_[A-Z0-9_.:-]{1,122}", payload["id"]):
        raise GoalError("goal.id must be a bounded upper-case GOAL_* identifier")
    if len(payload["title"]) > 200 or len(payload["objective"]) > 2000:
        raise GoalError("goal title or objective exceeds its bounded schema length")
    if payload["state"] not in {"new", "active", "blocked", "completed", "superseded"}:
        raise GoalError(f"unsupported goal state: {payload['state']!r}")
    milestones = payload.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        raise GoalError("goal must contain at least one milestone")
    if len(milestones) > MAX_MILESTONES_PER_GOAL:
        raise GoalError(f"goal exceeds {MAX_MILESTONES_PER_GOAL} milestones")
    milestone_ids: set[str] = set()
    criterion_ids: set[str] = set()
    criterion_count = 0
    for index, milestone in enumerate(milestones, 1):
        if not isinstance(milestone, dict):
            raise GoalError(f"milestones[{index}] must be an object")
        for key in ("id", "title", "objective", "state"):
            if not isinstance(milestone.get(key), str) or not milestone[key].strip():
                raise GoalError(f"milestones[{index}].{key} must be a non-empty string")
            if len(milestone[key]) > MAX_GOAL_TEXT_CHARS:
                raise GoalError(
                    f"milestones[{index}].{key} exceeds {MAX_GOAL_TEXT_CHARS} characters"
                )
        if not re.fullmatch(r"MILESTONE_[A-Z0-9_.:-]{1,117}", milestone["id"]):
            raise GoalError(f"invalid milestone identifier: {milestone['id']!r}")
        if len(milestone["title"]) > 200 or len(milestone["objective"]) > 2000:
            raise GoalError(f"milestone {milestone['id']} exceeds title or objective limits")
        if milestone["state"] not in {"new", "active", "blocked", "completed", "superseded"}:
            raise GoalError(f"unsupported milestone state: {milestone['state']!r}")
        dependencies = milestone.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) for value in dependencies
        ):
            raise GoalError(f"milestone {milestone['id']} depends_on must be a string array")
        if len(dependencies) != len(set(dependencies)):
            raise GoalError(f"milestone {milestone['id']} contains duplicate dependencies")
        if milestone["id"] in milestone_ids:
            raise GoalError(f"duplicate milestone id: {milestone['id']}")
        milestone_ids.add(milestone["id"])
        criteria = milestone.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise GoalError(f"milestone {milestone['id']} must contain criteria")
        for criterion in criteria:
            if not isinstance(criterion, dict):
                raise GoalError(f"criterion in {milestone['id']} must be an object")
            criterion_count += 1
            if criterion_count > MAX_CRITERIA_PER_GOAL:
                raise GoalError(f"goal exceeds {MAX_CRITERIA_PER_GOAL} criteria")
            for key in ("id", "description", "state", "evidence_required"):
                if not isinstance(criterion.get(key), str) or not criterion[key].strip():
                    raise GoalError(f"criterion in {milestone['id']} has invalid {key}")
                if len(criterion[key]) > MAX_GOAL_TEXT_CHARS:
                    raise GoalError(
                        f"criterion {criterion.get('id', '<unknown>')}.{key} exceeds "
                        f"{MAX_GOAL_TEXT_CHARS} characters"
                    )
            if criterion["id"] in criterion_ids:
                raise GoalError(f"duplicate criterion id: {criterion['id']}")
            criterion_ids.add(criterion["id"])
            if not re.fullmatch(r"CRIT_[A-Z0-9_.:-]{1,122}", criterion["id"]):
                raise GoalError(f"invalid criterion identifier: {criterion['id']!r}")
            if len(criterion["description"]) > 2000 or len(criterion["evidence_required"]) > 2000:
                raise GoalError(f"criterion {criterion['id']} exceeds bounded text limits")
            if criterion["state"] not in {"open", "active", "blocked", "completed", "waived"}:
                raise GoalError(
                    f"unsupported criterion state for {criterion['id']}: {criterion['state']!r}"
                )
            types = criterion.get("required_artifact_types", [])
            if not isinstance(types, list) or any(not isinstance(value, str) for value in types):
                raise GoalError(f"criterion {criterion['id']} required_artifact_types must be strings")
            if not types:
                raise GoalError(f"criterion {criterion['id']} requires at least one artifact type")
            if len(types) != len(set(types)) or any(
                value not in EVIDENCE_DOCUMENT_TYPES for value in types
            ):
                raise GoalError(
                    f"criterion {criterion['id']} has duplicate or unsupported artifact types"
                )
        criteria_closed = all(
            criterion["state"] in {"completed", "waived"} for criterion in criteria
        )
        if milestone["state"] == "completed" and not criteria_closed:
            raise GoalError(
                f"completed milestone {milestone['id']} contains open criteria"
            )
        if criteria_closed and milestone["state"] not in {"completed", "superseded"}:
            raise GoalError(
                f"milestone {milestone['id']} must close when all criteria are closed"
            )
    for milestone in milestones:
        for dependency in milestone.get("depends_on", []):
            if dependency not in milestone_ids:
                raise GoalError(f"milestone {milestone['id']} depends on unknown milestone {dependency}")
    milestones_closed = all(
        milestone["state"] in {"completed", "superseded"} for milestone in milestones
    )
    if payload["state"] == "completed" and not milestones_closed:
        raise GoalError(f"completed goal {payload['id']} contains open milestones")
    if milestones_closed and payload["state"] not in {"completed", "superseded"}:
        raise GoalError(f"goal {payload['id']} must close when all milestones are closed")


def _sync_goal_payload(connection: Any, payload: dict[str, Any], now: str) -> dict[str, int | str]:
    validate_goal(payload)
    connection.execute(
            """
            INSERT INTO goals(goal_id,title,objective,state,metadata_json,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(goal_id) DO UPDATE SET
                title=excluded.title,
                objective=excluded.objective,
                state=excluded.state,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            WHERE goals.title<>excluded.title
               OR goals.objective<>excluded.objective
               OR goals.state<>excluded.state
               OR goals.metadata_json<>excluded.metadata_json
            """,
            (payload["id"], payload["title"], payload["objective"], payload["state"], json_dumps(payload, pretty=False), now),
    )
    milestone_count = 0
    criterion_count = 0
    milestone_ids: list[str] = []
    criterion_ids: list[str] = []
    for sequence, milestone in enumerate(payload["milestones"], 1):
        milestone_count += 1
        milestone_ids.append(milestone["id"])
        connection.execute(
                """
                INSERT INTO milestones(milestone_id,goal_id,title,objective,state,sequence,depends_on_json)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(milestone_id) DO UPDATE SET
                    goal_id=excluded.goal_id,
                    title=excluded.title,
                    objective=excluded.objective,
                    state=excluded.state,
                    sequence=excluded.sequence,
                    depends_on_json=excluded.depends_on_json
                WHERE milestones.goal_id<>excluded.goal_id
                   OR milestones.title<>excluded.title
                   OR milestones.objective<>excluded.objective
                   OR milestones.state<>excluded.state
                   OR milestones.sequence<>excluded.sequence
                   OR milestones.depends_on_json<>excluded.depends_on_json
                """,
                (
                    milestone["id"],
                    payload["id"],
                    milestone["title"],
                    milestone["objective"],
                    milestone["state"],
                    sequence,
                    json_dumps(milestone.get("depends_on", []), pretty=False),
                ),
        )
        for criterion in milestone["criteria"]:
            criterion_count += 1
            criterion_ids.append(criterion["id"])
            connection.execute(
                    """
                    INSERT INTO criteria(
                        criterion_id,milestone_id,description,state,evidence_required,required_artifact_types_json
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(criterion_id) DO UPDATE SET
                        milestone_id=excluded.milestone_id,
                        description=excluded.description,
                        state=excluded.state,
                        evidence_required=excluded.evidence_required,
                        required_artifact_types_json=excluded.required_artifact_types_json
                    WHERE criteria.milestone_id<>excluded.milestone_id
                       OR criteria.description<>excluded.description
                       OR criteria.state<>excluded.state
                       OR criteria.evidence_required<>excluded.evidence_required
                       OR criteria.required_artifact_types_json<>excluded.required_artifact_types_json
                    """,
                    (
                        criterion["id"],
                        milestone["id"],
                        criterion["description"],
                        criterion["state"],
                        criterion["evidence_required"],
                        json_dumps(criterion.get("required_artifact_types", []), pretty=False),
                    ),
            )
    criterion_placeholders = ",".join("?" for _ in criterion_ids)
    connection.execute(
        f"""
        DELETE FROM criteria
        WHERE milestone_id IN (SELECT milestone_id FROM milestones WHERE goal_id=?)
          AND criterion_id NOT IN ({criterion_placeholders})
        """,
        (payload["id"], *criterion_ids),
    )
    milestone_placeholders = ",".join("?" for _ in milestone_ids)
    connection.execute(
        f"DELETE FROM milestones WHERE goal_id=? AND milestone_id NOT IN ({milestone_placeholders})",
        (payload["id"], *milestone_ids),
    )
    return {"goal_id": payload["id"], "milestones": milestone_count, "criteria": criterion_count}


def sync_goal_payload(database: KnowledgeDatabase, payload: dict[str, Any]) -> dict[str, int | str]:
    with database.transaction() as connection:
        return _sync_goal_payload(connection, payload, utc_now())


def sync_goal_files(workspace: Path, database: KnowledgeDatabase) -> list[dict[str, int | str]]:
    payloads: list[dict[str, Any]] = []
    milestone_owner: dict[str, str] = {}
    criterion_owner: dict[str, str] = {}
    for path in goal_source_paths(workspace):
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_goal(payload)
        for milestone in payload["milestones"]:
            previous = milestone_owner.setdefault(milestone["id"], payload["id"])
            if previous != payload["id"]:
                raise GoalError(
                    f"milestone {milestone['id']} is declared by both {previous} and {payload['id']}"
                )
            for criterion in milestone["criteria"]:
                previous = criterion_owner.setdefault(criterion["id"], payload["id"])
                if previous != payload["id"]:
                    raise GoalError(
                        f"criterion {criterion['id']} is declared by both {previous} and {payload['id']}"
                    )
        payloads.append(payload)
    now = utc_now()
    with database.transaction() as connection:
        return [_sync_goal_payload(connection, payload, now) for payload in payloads]


def coverage_report(database: KnowledgeDatabase) -> list[dict[str, Any]]:
    connection = database.connect(read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT
                g.goal_id,
                m.milestone_id,
                c.criterion_id,
                c.description,
                c.state AS declared_state,
                c.required_artifact_types_json,
                count(gc.artifact_id) AS evidence_count,
                max(CASE WHEN gc.coverage_state='evidenced' THEN 1 ELSE 0 END) AS evidenced,
                group_concat(DISTINCT a.document_type) AS present_artifact_types_csv
            FROM goals g
            JOIN milestones m ON m.goal_id=g.goal_id
            JOIN criteria c ON c.milestone_id=m.milestone_id
            LEFT JOIN goal_coverage gc ON gc.criterion_id=c.criterion_id
            LEFT JOIN artifacts a ON a.artifact_id=gc.artifact_id
            GROUP BY g.goal_id,m.milestone_id,c.criterion_id,c.description,c.state,
                     c.required_artifact_types_json
            ORDER BY m.sequence,c.criterion_id
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            required = json.loads(result.pop("required_artifact_types_json"))
            present_csv = result.pop("present_artifact_types_csv") or ""
            present = sorted(value for value in present_csv.split(",") if value)
            missing_types = sorted(set(required) - set(present))
            result["required_artifact_types"] = required
            result["present_artifact_types"] = present
            result["missing_artifact_types"] = missing_types
            result["requirements_met"] = bool(result["evidenced"]) and not missing_types
            results.append(result)
        return results
    finally:
        connection.close()


def unmet_required_criteria(database: KnowledgeDatabase) -> list[dict[str, Any]]:
    return [
        row
        for row in coverage_report(database)
        if row["declared_state"] != "waived" and not row["requirements_met"]
    ]
