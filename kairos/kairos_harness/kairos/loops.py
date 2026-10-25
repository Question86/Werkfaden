from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .backup import verify_backup
from .constants import MAX_GENERATED_RECEIPT_FILES
from .database import KnowledgeDatabase
from .goals import validate_goal
from .locking import workspace_write_lock
from .promoter import prepare_document
from .templates import task_document
from .util import (
    atomic_write_json,
    atomic_write_text,
    json_dumps,
    prune_oldest_files,
    read_json,
    sha256_bytes,
    sha256_text,
    utc_now,
)
from .workspace import database_path, load_config


class LoopTransitionError(RuntimeError):
    pass


MAX_PROJECT_KICKOFF_BYTES = 65536


def _require_identifier(value: str, prefix: str) -> None:
    if not re.fullmatch(rf"{re.escape(prefix)}_[A-Z0-9_.:-]+", value):
        raise LoopTransitionError(f"invalid {prefix} identifier: {value!r}")


def _ledger_counts(database: KnowledgeDatabase) -> dict[str, int]:
    connection = database.connect(read_only=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "artifacts",
                "artifact_revisions",
                "promotion_receipts",
                "heartbeat_receipts",
                "action_journal",
                "retrieval_traces",
            )
        }
    finally:
        connection.close()


def _verify_predecessor(
    workspace: Path,
    database: KnowledgeDatabase,
    finalization_id: str,
) -> dict[str, Any]:
    receipt_path = workspace / ".kairos" / "finalization" / f"{finalization_id}.json"
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict) or receipt.get("schema") != "kairos-finalization-result/v1":
        raise LoopTransitionError(f"missing or invalid predecessor finalization receipt: {receipt_path}")
    if receipt.get("status") != "FINALIZED" or receipt.get("finalization_id") != finalization_id:
        raise LoopTransitionError("predecessor finalization receipt is not a verified FINALIZED result")
    source_loop = int(receipt.get("loop", 0))
    expected_archive = f"ARCHIVE_L{source_loop:04d}"
    if source_loop < 1 or receipt.get("archive_id") != expected_archive:
        raise LoopTransitionError("predecessor finalization loop/archive identity is inconsistent")

    connection = database.connect(read_only=True)
    try:
        finalization_row = connection.execute(
            "SELECT * FROM finalization_receipts WHERE finalization_id=?",
            (finalization_id,),
        ).fetchone()
        heartbeat_row = connection.execute(
            "SELECT verified FROM heartbeat_receipts WHERE heartbeat_id=?",
            (receipt.get("heartbeat_id"),),
        ).fetchone()
        archive_row = connection.execute(
            """
            SELECT artifact_id,path,state,content_sha256
            FROM artifacts WHERE artifact_id=?
            """,
            (expected_archive,),
        ).fetchone()
        promotion_row = connection.execute(
            """
            SELECT verified,content_sha256
            FROM promotion_receipts
            WHERE receipt_id=? AND artifact_id=?
            """,
            (receipt.get("archive_receipt"), expected_archive),
        ).fetchone()
    finally:
        connection.close()
    if not finalization_row:
        raise LoopTransitionError("predecessor finalization is absent from the durable database ledger")
    if (
        int(finalization_row["loop"]) != source_loop
        or finalization_row["heartbeat_id"] != receipt.get("heartbeat_id")
        or finalization_row["archive_id"] != expected_archive
        or finalization_row["archive_receipt_id"] != receipt.get("archive_receipt")
        or finalization_row["backup_id"] != receipt.get("backup_id")
    ):
        raise LoopTransitionError("predecessor file and database finalization receipts disagree")
    if not heartbeat_row or int(heartbeat_row["verified"]) != 1:
        raise LoopTransitionError("predecessor final heartbeat is absent or unverified")
    if not archive_row or archive_row["state"] != "finalized":
        raise LoopTransitionError("predecessor archive is absent or not finalized")
    archive_path = workspace / str(archive_row["path"])
    if not archive_path.is_file():
        raise LoopTransitionError("predecessor archive source is missing")
    archive_document = prepare_document(archive_path, workspace)
    if (
        archive_document.frontmatter.metadata.get("id") != expected_archive
        or archive_document.content_sha256 != archive_row["content_sha256"]
        or receipt.get("archive_sha256") != archive_row["content_sha256"]
    ):
        raise LoopTransitionError("predecessor archive source and promoted hash disagree")
    if (
        not promotion_row
        or int(promotion_row["verified"]) != 1
        or promotion_row["content_sha256"] != archive_row["content_sha256"]
    ):
        raise LoopTransitionError("predecessor archive promotion receipt is absent or inconsistent")
    backup = verify_backup(workspace, str(receipt.get("backup_id", "")))
    if not backup["verified"]:
        raise LoopTransitionError(f"predecessor backup verification failed: {backup['failures']}")
    return receipt


def _validate_next_sources(
    workspace: Path,
    *,
    source_loop: int,
    backup_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
) -> tuple[Path, dict[str, Any]]:
    goal_path = workspace / "goals" / f"{goal_id}.json"
    task_path = workspace / "tasks" / f"task_{task_id}.md"
    if not goal_path.is_file() or not task_path.is_file():
        raise LoopTransitionError("new-loop requires staged goal and task source files with exact canonical names")
    goal = json.loads(goal_path.read_text(encoding="utf-8"))
    validate_goal(goal)
    if goal.get("id") != goal_id or goal.get("state") not in {"new", "active"}:
        raise LoopTransitionError("next goal source identity or state is not eligible for activation")
    milestone = next((item for item in goal["milestones"] if item["id"] == milestone_id), None)
    if not milestone or milestone.get("state") not in {"new", "active"}:
        raise LoopTransitionError("next milestone is absent or not open")

    task = prepare_document(task_path, workspace)
    metadata = task.frontmatter.metadata
    if (
        metadata.get("id") != task_id
        or metadata.get("type") != "task"
        or metadata.get("state") not in {"new", "ready", "active", "in_progress"}
        or metadata.get("goal") != goal_id
        or metadata.get("milestone") != milestone_id
        or int(metadata.get("loop", 0)) != source_loop + 1
    ):
        raise LoopTransitionError("next task source does not match the requested loop, goal, and milestone")
    known_criteria = {item["id"] for item in milestone["criteria"]}
    criteria = list(metadata.get("criteria", []))
    if not criteria or not set(criteria).issubset(known_criteria):
        raise LoopTransitionError("next task criteria are empty or outside the requested milestone")

    manifest_path = workspace / "backups" / backup_id / "manifest.json"
    backup_manifest = read_json(manifest_path)
    sealed_sources = {
        str(item.get("path", "")).replace("\\", "/")
        for item in backup_manifest.get("sources", [])
        if isinstance(item, dict)
    }
    staged = {f"goals/{goal_id}.json", f"tasks/task_{task_id}.md"}
    if sealed_sources & staged:
        raise LoopTransitionError(
            "next-loop sources were already part of the predecessor seal; use fresh identifiers and sources"
        )
    return task_path, metadata


def _existing_transition(
    database: KnowledgeDatabase,
    predecessor_finalization_id: str,
) -> dict[str, Any] | None:
    connection = database.connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT * FROM loop_transition_receipts WHERE predecessor_finalization_id=?",
            (predecessor_finalization_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _kickoff_contract(
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
    if not isinstance(spec, dict) or spec.get("schema") != "kairos-project-kickoff/v1":
        raise LoopTransitionError("project kickoff schema must be 'kairos-project-kickoff/v1'")
    canonical_spec = json_dumps(spec, pretty=False)
    if len(canonical_spec.encode("utf-8")) > MAX_PROJECT_KICKOFF_BYTES:
        raise LoopTransitionError(
            f"project kickoff contract exceeds {MAX_PROJECT_KICKOFF_BYTES} bytes"
        )
    goal_value = spec.get("goal")
    task_value = spec.get("task")
    if not isinstance(goal_value, dict) or not isinstance(task_value, dict):
        raise LoopTransitionError("project kickoff requires goal and task objects")
    goal = json.loads(json_dumps(goal_value, pretty=False))
    task = json.loads(json_dumps(task_value, pretty=False))
    for key in ("id", "title", "objective"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise LoopTransitionError(f"project kickoff task.{key} must be a non-empty string")
    _require_identifier(str(goal.get("id", "")), "GOAL")
    _require_identifier(str(task["id"]), "TASK")
    milestone_id = task.get("milestone")
    if not isinstance(milestone_id, str):
        raise LoopTransitionError("project kickoff task.milestone must be an identifier")
    _require_identifier(milestone_id, "MILESTONE")
    criteria_ids = task.get("criteria")
    if (
        not isinstance(criteria_ids, list)
        or not criteria_ids
        or any(not isinstance(value, str) for value in criteria_ids)
        or len(criteria_ids) != len(set(criteria_ids))
    ):
        raise LoopTransitionError(
            "project kickoff task.criteria must be a non-empty unique string array"
        )
    if len(criteria_ids) > 16:
        raise LoopTransitionError("project kickoff first task may select at most 16 criteria")
    for criterion_id in criteria_ids:
        _require_identifier(criterion_id, "CRIT")
    if len(task["title"]) > 200 or len(task["objective"]) > 2000:
        raise LoopTransitionError("project kickoff task title or objective exceeds bounded limits")
    milestone = next(
        (
            item
            for item in goal.get("milestones", [])
            if isinstance(item, dict) and item.get("id") == milestone_id
        ),
        None,
    )
    if not milestone:
        raise LoopTransitionError("project kickoff task milestone is absent from the goal")
    known_criteria = {
        item.get("id"): item
        for item in milestone.get("criteria", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    unknown = sorted(set(criteria_ids) - set(known_criteria))
    if unknown:
        raise LoopTransitionError(
            f"project kickoff task criteria are outside the milestone: {', '.join(unknown)}"
        )
    selected_criteria = [known_criteria[value] for value in criteria_ids]
    return goal, milestone, task, selected_criteria, canonical_spec


def validate_project_kickoff_contract(
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
    """Validate and normalize a project-intent contract without mutating a workspace.

    Project initiation and later loop kickoff deliberately share one contract grammar so
    the LLM cannot invent a second goal/milestone/task schema for compiler-backed intake.
    """
    return _kickoff_contract(spec)


def _stage_kickoff_source(path: Path, content: str, expected_sha256: str) -> bool:
    if path.exists():
        if not path.is_file() or sha256_bytes(path.read_bytes()) != expected_sha256:
            raise LoopTransitionError(
                f"project kickoff source collision or replay hash mismatch: {path.name}"
            )
        return False
    atomic_write_text(path, content)
    if sha256_bytes(path.read_bytes()) != expected_sha256:
        raise LoopTransitionError(f"project kickoff source write verification failed: {path.name}")
    return True


def kickoff_project(workspace: Path, *, spec: dict[str, Any]) -> dict[str, Any]:
    workspace = workspace.resolve()
    goal, milestone, task, selected_criteria, canonical_spec = _kickoff_contract(spec)
    goal_id = str(goal["id"])
    milestone_id = str(milestone["id"])
    task_id = str(task["id"])
    with workspace_write_lock(workspace):
        config = load_config(workspace)
        database = KnowledgeDatabase(database_path(workspace))
        database.initialize()

        from .heartbeat import load_runtime_state

        state = load_runtime_state(workspace)
        finalization_id = state.get("last_finalization_id")
        if not isinstance(finalization_id, str) or not finalization_id:
            raise LoopTransitionError(
                "project-kickoff requires a recorded predecessor finalization identifier"
            )
        predecessor = _verify_predecessor(workspace, database, finalization_id)
        source_loop = int(predecessor["loop"])
        target_loop = source_loop + 1
        existing = _existing_transition(database, finalization_id)
        if existing:
            expected = (target_loop, goal_id, milestone_id, task_id)
            actual = (
                int(existing["target_loop"]),
                existing["goal_id"],
                existing["milestone_id"],
                existing["task_id"],
            )
            if actual != expected:
                raise LoopTransitionError(
                    "predecessor finalization is already bound to a different project kickoff"
                )
            prepared_receipt = json.loads(existing["receipt_json"])
            if prepared_receipt.get("kickoff_spec_sha256") != sha256_text(canonical_spec):
                raise LoopTransitionError(
                    "project kickoff replay contract differs from the prepared transition"
                )
            if existing["status"] == "VERIFIED":
                return start_new_loop(
                    workspace,
                    goal_id=goal_id,
                    milestone_id=milestone_id,
                    task_id=task_id,
                )
            if state.get("lifecycle") not in {"FINALIZED", "STARTING_LOOP"}:
                raise LoopTransitionError(
                    f"pending project kickoff cannot resume from lifecycle {state.get('lifecycle')!r}"
                )
            kickoff_created_at = str(prepared_receipt["kickoff_created_at"])
        else:
            if state.get("lifecycle") != "FINALIZED":
                raise LoopTransitionError(
                    f"project-kickoff requires lifecycle FINALIZED; current lifecycle is {state.get('lifecycle')!r}"
                )
            kickoff_created_at = utc_now()
            prepared_receipt = {}

        goal["created_at"] = (
            goal.get("created_at")
            if isinstance(goal.get("created_at"), str) and goal["created_at"].strip()
            else kickoff_created_at
        )
        validate_goal(goal)
        if goal.get("state") not in {"new", "active"}:
            raise LoopTransitionError("project kickoff goal must be new or active")
        if milestone.get("state") not in {"new", "active"}:
            raise LoopTransitionError("project kickoff task milestone must be new or active")
        goal_text = json_dumps(goal)
        task_text = task_document(
            task_id=task_id,
            title=str(task["title"]),
            objective=str(task["objective"]),
            workspace_id=str(config["workspace_id"]),
            goal_id=goal_id,
            milestone_id=milestone_id,
            criteria=selected_criteria,
            loop=target_loop,
            updated_at=kickoff_created_at,
        )
        goal_sha256 = sha256_text(goal_text)
        task_sha256 = sha256_text(task_text)
        spec_sha256 = sha256_text(canonical_spec)
        goal_path = workspace / "goals" / f"{goal_id}.json"
        task_path = workspace / "tasks" / f"task_{task_id}.md"

        if existing:
            if (
                prepared_receipt.get("goal_sha256") != goal_sha256
                or prepared_receipt.get("task_sha256") != task_sha256
            ):
                raise LoopTransitionError(
                    "project kickoff deterministic source hashes differ from the prepared transition"
                )
        else:
            if goal_path.exists() or task_path.exists():
                raise LoopTransitionError(
                    "project kickoff refuses pre-staged or colliding successor source files"
                )
            connection = database.connect(read_only=True)
            try:
                preexisting_goal = connection.execute(
                    "SELECT state FROM goals WHERE goal_id=?", (goal_id,)
                ).fetchone()
                preexisting_task = connection.execute(
                    "SELECT state FROM artifacts WHERE artifact_id=?", (task_id,)
                ).fetchone()
            finally:
                connection.close()
            if preexisting_goal or preexisting_task:
                raise LoopTransitionError(
                    "project kickoff identifiers already exist in the predecessor metadata"
                )
            transition_id = "LTR_" + sha256_text(
                f"{finalization_id}|{source_loop}|{target_loop}|{goal_id}|{milestone_id}|{task_id}"
            )[:32]
            prepared_receipt = {
                "schema": "kairos-loop-transition/v1",
                "status": "PENDING",
                "command": "project-kickoff",
                "transition_id": transition_id,
                "predecessor_finalization_id": finalization_id,
                "source_loop": source_loop,
                "target_loop": target_loop,
                "goal_id": goal_id,
                "milestone_id": milestone_id,
                "task_id": task_id,
                "kickoff_spec_sha256": spec_sha256,
                "kickoff_created_at": kickoff_created_at,
                "goal_sha256": goal_sha256,
                "task_sha256": task_sha256,
                "ledger_counts_before": _ledger_counts(database),
                "created_at": kickoff_created_at,
            }
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO loop_transition_receipts(
                        transition_id,predecessor_finalization_id,source_loop,target_loop,
                        goal_id,milestone_id,task_id,status,receipt_json,created_at,completed_at
                    ) VALUES(?,?,?,?,?,?,?,'PENDING',?,?,NULL)
                    """,
                    (
                        transition_id,
                        finalization_id,
                        source_loop,
                        target_loop,
                        goal_id,
                        milestone_id,
                        task_id,
                        json_dumps(prepared_receipt, pretty=False),
                        kickoff_created_at,
                    ),
                )

        _stage_kickoff_source(goal_path, goal_text, goal_sha256)
        _stage_kickoff_source(task_path, task_text, task_sha256)
        return start_new_loop(
            workspace,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )


def start_new_loop(
    workspace: Path,
    *,
    goal_id: str,
    milestone_id: str,
    task_id: str,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    with workspace_write_lock(workspace):
        load_config(workspace)
        for value, prefix in (
            (goal_id, "GOAL"),
            (milestone_id, "MILESTONE"),
            (task_id, "TASK"),
        ):
            _require_identifier(value, prefix)
        database = KnowledgeDatabase(database_path(workspace))
        database.initialize()

        from .heartbeat import _update_current, load_runtime_state, run_heartbeat

        state = load_runtime_state(workspace)
        finalization_id = state.get("last_finalization_id")
        if not isinstance(finalization_id, str) or not finalization_id:
            raise LoopTransitionError("new-loop requires a recorded predecessor finalization identifier")
        predecessor = _verify_predecessor(workspace, database, finalization_id)
        source_loop = int(predecessor["loop"])
        target_loop = source_loop + 1
        existing = _existing_transition(database, finalization_id)
        if existing:
            expected = (target_loop, goal_id, milestone_id, task_id)
            actual = (
                int(existing["target_loop"]),
                existing["goal_id"],
                existing["milestone_id"],
                existing["task_id"],
            )
            if actual != expected:
                raise LoopTransitionError("predecessor finalization is already bound to a different loop transition")
            if existing["status"] == "VERIFIED":
                receipt = json.loads(existing["receipt_json"])
                receipt_dir = workspace / ".kairos" / "loop_transitions"
                atomic_write_json(receipt_dir / f"{existing['transition_id']}.json", receipt)
                prune_oldest_files(receipt_dir, suffix=".json", keep=MAX_GENERATED_RECEIPT_FILES)
                return receipt
        elif state.get("lifecycle") != "FINALIZED":
            raise LoopTransitionError(
                f"new-loop requires lifecycle FINALIZED; current lifecycle is {state.get('lifecycle')!r}"
            )

        current = read_json(workspace / "current.json", default={}) or {}
        observed_loop = int(state.get("loop", current.get("loop", source_loop)))
        if observed_loop not in {source_loop, target_loop}:
            raise LoopTransitionError(
                f"loop state is inconsistent with predecessor: observed {observed_loop}, expected {source_loop} or {target_loop}"
            )
        task_path, task_metadata = _validate_next_sources(
            workspace,
            source_loop=source_loop,
            backup_id=str(predecessor["backup_id"]),
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )

        connection = database.connect(read_only=True)
        try:
            preexisting_goal = connection.execute(
                "SELECT state FROM goals WHERE goal_id=?", (goal_id,)
            ).fetchone()
            preexisting_task = connection.execute(
                "SELECT state FROM artifacts WHERE artifact_id=?", (task_id,)
            ).fetchone()
        finally:
            connection.close()
        if not existing and (preexisting_goal or preexisting_task):
            raise LoopTransitionError("next goal or task already exists in the predecessor metadata projection")

        transition_id = "LTR_" + sha256_text(
            f"{finalization_id}|{source_loop}|{target_loop}|{goal_id}|{milestone_id}|{task_id}"
        )[:32]
        prepared_at = utc_now()
        if not existing:
            prepared_receipt = {
                "schema": "kairos-loop-transition/v1",
                "status": "PENDING",
                "transition_id": transition_id,
                "predecessor_finalization_id": finalization_id,
                "source_loop": source_loop,
                "target_loop": target_loop,
                "goal_id": goal_id,
                "milestone_id": milestone_id,
                "task_id": task_id,
                "ledger_counts_before": _ledger_counts(database),
                "created_at": prepared_at,
            }
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO loop_transition_receipts(
                        transition_id,predecessor_finalization_id,source_loop,target_loop,
                        goal_id,milestone_id,task_id,status,receipt_json,created_at,completed_at
                    ) VALUES(?,?,?,?,?,?,?,'PENDING',?,?,NULL)
                    """,
                    (
                        transition_id,
                        finalization_id,
                        source_loop,
                        target_loop,
                        goal_id,
                        milestone_id,
                        task_id,
                        json_dumps(prepared_receipt, pretty=False),
                        prepared_at,
                    ),
                )
        else:
            transition_id = str(existing["transition_id"])
            prepared_receipt = json.loads(existing["receipt_json"])

        state.update(
            {
                "loop": target_loop,
                "lifecycle": "STARTING_LOOP",
                "active_goal": goal_id,
                "active_milestone": milestone_id,
                "active_task": task_id,
                "active_criterion": (task_metadata.get("criteria") or [None])[0],
                "context_pressure": 0.0,
                "contradiction_count": 0,
                "unresolved_branches": 0,
                "evidence_gap_count": len(task_metadata.get("criteria", [])),
                "mode": "work",
                "mode_rationale": "verified atomic loop transition",
                "pending_promotions": 0,
                "failed_promotions": 0,
                "updated_at": utc_now(),
            }
        )
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
        heartbeat = run_heartbeat(
            workspace,
            requested_mode="work",
            changed_paths=[str(task_path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
            trigger=f"loop-transition:{transition_id}",
        )
        if not heartbeat["verified"]:
            blocked = dict(prepared_receipt)
            blocked["heartbeat_id"] = heartbeat["heartbeat_id"]
            blocked["heartbeat_failures"] = heartbeat["failures"]
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE loop_transition_receipts SET receipt_json=? WHERE transition_id=?",
                    (json_dumps(blocked, pretty=False), transition_id),
                )
            return blocked

        after = _ledger_counts(database)
        before = {
            key: int(value)
            for key, value in prepared_receipt["ledger_counts_before"].items()
        }
        regressions = {
            key: {"before": before[key], "after": after[key]}
            for key in before
            if after[key] < before[key]
        }
        current_after = read_json(workspace / "current.json", default={}) or {}
        runtime_after = load_runtime_state(workspace)
        if regressions:
            raise LoopTransitionError(f"durable ledger regression detected: {regressions}")
        if (
            int(current_after.get("loop", 0)) != target_loop
            or int(runtime_after.get("loop", 0)) != target_loop
            or runtime_after.get("active_goal") != goal_id
            or runtime_after.get("active_milestone") != milestone_id
            or runtime_after.get("active_task") != task_id
        ):
            raise LoopTransitionError("post-transition loop or active route does not match the prepared contract")

        completed_at = utc_now()
        receipt = {
            "schema": "kairos-loop-transition/v1",
            "status": "VERIFIED",
            "transition_id": transition_id,
            "predecessor_finalization_id": finalization_id,
            "predecessor_archive_id": predecessor["archive_id"],
            "predecessor_backup_id": predecessor["backup_id"],
            "source_loop": source_loop,
            "target_loop": target_loop,
            "goal_id": goal_id,
            "milestone_id": milestone_id,
            "task_id": task_id,
            "heartbeat_id": heartbeat["heartbeat_id"],
            "ledger_counts_before": before,
            "ledger_counts_after": after,
            "transient_reset": {
                "context_pressure": runtime_after["context_pressure"],
                "contradiction_count": runtime_after["contradiction_count"],
                "unresolved_branches": runtime_after["unresolved_branches"],
            },
            "created_at": prepared_receipt["created_at"],
            "completed_at": completed_at,
        }
        for key in (
            "command",
            "kickoff_spec_sha256",
            "kickoff_created_at",
            "goal_sha256",
            "task_sha256",
        ):
            if key in prepared_receipt:
                receipt[key] = prepared_receipt[key]
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE loop_transition_receipts
                SET status='VERIFIED',receipt_json=?,completed_at=?
                WHERE transition_id=? AND status='PENDING'
                """,
                (json_dumps(receipt, pretty=False), completed_at, transition_id),
            )
            journal_id = "JRN_" + sha256_text(f"{transition_id}|VERIFIED")[:32]
            connection.execute(
                """
                INSERT OR IGNORE INTO action_journal(
                    journal_id,heartbeat_sequence,action_type,mode,goal_id,milestone_id,
                    task_id,artifact_id,summary,result,details_json,created_at
                ) VALUES(?,?,'loop_transition','work',?,?,?,NULL,?,'SUCCESS',?,?)
                """,
                (
                    journal_id,
                    heartbeat["sequence"],
                    goal_id,
                    milestone_id,
                    task_id,
                    f"Opened KAIROS loop {target_loop} from verified loop {source_loop}",
                    json_dumps(receipt, pretty=False),
                    completed_at,
                ),
            )
        runtime_after["last_loop_transition_id"] = transition_id
        runtime_after["updated_at"] = completed_at
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", runtime_after)
        _update_current(workspace, runtime_after)
        receipt_dir = workspace / ".kairos" / "loop_transitions"
        atomic_write_json(receipt_dir / f"{transition_id}.json", receipt)
        prune_oldest_files(receipt_dir, suffix=".json", keep=MAX_GENERATED_RECEIPT_FILES)
        return receipt
