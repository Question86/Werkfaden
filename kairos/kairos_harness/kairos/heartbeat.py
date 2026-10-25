from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .backup import create_backup
from .canonicals import commit_canonical_projection, refresh_dynamic_canonicals
from .constants import (
    ACQUISITION_MODES,
    DYNAMIC_CANONICAL_FILES,
    MAX_GENERATED_RECEIPT_FILES,
    RUNTIME_SCHEMA_VERSION,
)
from .database import KnowledgeDatabase
from .goals import reconcile_goal_files, sync_goal_files, unmet_required_criteria, update_goal_manifest
from .governance import guard_freshness_changes
from .locking import workspace_write_lock
from .promoter import prepare_document, promote_document
from .readiness import evaluate_finalization_readiness
from .reconcile import ReconcileResult, reconcile_documents, update_manifest
from .task_snapshots import (
    TASK_SNAPSHOT_START_LOOP,
    TaskSnapshotError,
    ensure_task_snapshot_package,
    parse_task_snapshot_binding,
)
from .templates import pointer, render_document
from .util import (
    atomic_write_json,
    atomic_write_text,
    json_dumps,
    prune_oldest_files,
    read_json,
    resolve_workspace_path,
    sha256_text,
    utc_now,
    workspace_relative,
)
from .workspace import database_path, load_config


class HeartbeatError(RuntimeError):
    pass


FINALIZATION_PENDING_SCHEMA = "kairos-finalization-pending-archive/v1"


def load_runtime_state(workspace: Path) -> dict[str, Any]:
    path = workspace / ".kairos" / "runtime_state.json"
    state = read_json(path)
    if not isinstance(state, dict) or state.get("schema") != RUNTIME_SCHEMA_VERSION:
        raise HeartbeatError(f"invalid or missing KAIROS runtime state: {path}")
    return state


def choose_mode(state: dict[str, Any], requested: str) -> tuple[str, str]:
    if requested not in ACQUISITION_MODES:
        raise HeartbeatError(f"unsupported mode: {requested}")
    if requested != "auto":
        return requested, "explicitly requested"
    if float(state.get("context_pressure", 0.0)) >= 0.75:
        return "breathe", "context pressure is at or above the checkpoint threshold"
    if int(state.get("contradiction_count", 0)) > 0 or int(state.get("unresolved_branches", 0)) > 3:
        return "breadth", "multiple branches or contradictions require comparative acquisition"
    if int(state.get("evidence_gap_count", 0)) <= 1 and state.get("active_criterion"):
        return "depth", "one bounded evidence gap remains on the active criterion"
    if state.get("lifecycle") == "READY":
        return "work", "prepared workspace requires its first bounded implementation or validation action"
    return "breadth", "default exploration keeps the goal frontier visible"


def _validated_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if metrics is None:
        return {}
    if not isinstance(metrics, dict):
        raise HeartbeatError("heartbeat metrics must be a JSON object")
    allowed = {"context_pressure", "contradiction_count", "unresolved_branches"}
    unknown = sorted(set(metrics) - allowed)
    if unknown:
        raise HeartbeatError(f"unsupported heartbeat metric(s): {', '.join(unknown)}")
    result: dict[str, Any] = {}
    if "context_pressure" in metrics:
        value = metrics["context_pressure"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HeartbeatError("context_pressure must be a finite number between 0 and 1")
        pressure = float(value)
        if not math.isfinite(pressure) or not 0.0 <= pressure <= 1.0:
            raise HeartbeatError("context_pressure must be a finite number between 0 and 1")
        result["context_pressure"] = pressure
    for key in ("contradiction_count", "unresolved_branches"):
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
            raise HeartbeatError(f"{key} must be an integer between 0 and 1000000")
        result[key] = value
    return result


def _first_unmet_criterion_for_active_task(
    database: KnowledgeDatabase,
    active_task: str | None,
    unmet: list[dict[str, Any]],
) -> str | None:
    """Return the first declared unmet criterion owned by the active task.

    Global goal coverage remains useful for finalization, but it must not steer an
    active task toward a criterion declared by another task.
    """
    if not active_task:
        return None
    connection = database.connect(read_only=True)
    try:
        row = connection.execute(
            """
            SELECT metadata_json
            FROM artifacts
            WHERE artifact_id=? AND document_type='task'
            """,
            (active_task,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    metadata = json.loads(row["metadata_json"])
    unmet_ids = {str(item["criterion_id"]) for item in unmet}
    for criterion_id in metadata.get("criteria", []):
        if criterion_id in unmet_ids:
            return str(criterion_id)
    return None


def _update_current(workspace: Path, state: dict[str, Any]) -> None:
    path = workspace / "current.json"
    current = read_json(path, default={}) or {}
    tx_frontier = read_json(workspace / ".kairos" / "tx_recycling_frontier.json", default={}) or {}
    if not isinstance(tx_frontier, dict) or tx_frontier.get("schema") != "werkfaden-tx-recycling-frontier/v1":
        tx_frontier = {}
    recycling_required = tx_frontier.get("required") is True
    current.update(
        {
            "schema": "kairos-current/v1",
            "state_revision": int(current.get("state_revision", 0)) + 1,
            "lifecycle": state["lifecycle"],
            "loop": int(state.get("loop", current.get("loop", 1))),
            "active_goal": state.get("active_goal"),
            "active_milestone": state.get("active_milestone"),
            "active_task": state.get("active_task"),
            "active_criterion": state.get("active_criterion"),
            "context_frontier": (
                [
                    f"{state['active_task']}#s-objective",
                    f"{state['active_task']}#s-acceptance",
                ]
                if state.get("active_task")
                else []
            ),
            "pending_promotions": state.get("pending_promotions", 0),
            "failed_promotions": state.get("failed_promotions", 0),
            "last_promotion_receipt": state.get("last_promotion_receipt"),
            "last_heartbeat_receipt": state.get("last_heartbeat_receipt"),
            "tx_recycling": {
                "required": recycling_required,
                "materialized_count": tx_frontier.get("materialized_count", 0),
                "target_max": tx_frontier.get("target_max", 8),
            },
            "next_required_read": (
                "_LOOP_GATE.md#s-tx-recycling"
                if recycling_required else
                "_LOOP_GATE.md#s-verdict"
                if state["lifecycle"] == "BLOCKED"
                else "ACTIVE.md#s-next-action"
                if state.get("active_task")
                else "NEURAL_CORTEX.md#s-active-frontier"
            ),
            "updated_at": state["updated_at"],
        }
    )
    atomic_write_json(path, current)


def _journal(
    database: KnowledgeDatabase,
    *,
    sequence: int,
    mode: str,
    state: dict[str, Any],
    summary: str,
    result: str,
    details: dict[str, Any],
) -> None:
    now = utc_now()
    journal_id = "JRN_" + sha256_text(f"{sequence}|{mode}|{summary}|{now}")[:32]
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO action_journal(
                journal_id,heartbeat_sequence,action_type,mode,goal_id,milestone_id,
                task_id,artifact_id,summary,result,details_json,created_at
            ) VALUES(?,?,'heartbeat',?,?,?,?,NULL,?,?,?,?)
            """,
            (
                journal_id,
                sequence,
                mode,
                state.get("active_goal"),
                state.get("active_milestone"),
                state.get("active_task"),
                summary,
                result,
                json_dumps(details, pretty=False),
                now,
            ),
        )


def run_heartbeat(
    workspace: Path,
    *,
    requested_mode: str = "auto",
    changed_paths: list[str] | None = None,
    use_reconciliation: bool = True,
    metrics: dict[str, Any] | None = None,
    trigger: str = "explicit",
) -> dict[str, Any]:
    workspace = workspace.resolve()
    with workspace_write_lock(workspace):
        return _run_heartbeat_locked(
            workspace,
            requested_mode=requested_mode,
            changed_paths=changed_paths,
            use_reconciliation=use_reconciliation,
            metrics=metrics,
            trigger=trigger,
        )


def _run_heartbeat_locked(
    workspace: Path,
    *,
    requested_mode: str,
    changed_paths: list[str] | None,
    use_reconciliation: bool,
    metrics: dict[str, Any] | None,
    trigger: str,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    load_config(workspace)
    state = load_runtime_state(workspace)
    if state.get("lifecycle") == "FINALIZED":
        raise HeartbeatError("workspace is FINALIZED; create a new loop before further work")
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    goal_reconciliation = reconcile_goal_files(workspace)
    reconciliation = (
        reconcile_documents(workspace)
        if use_reconciliation
        else ReconcileResult((), (), {}, {})
    )
    governance_attribution = guard_freshness_changes(
        workspace,
        changed=(*reconciliation.changed, *goal_reconciliation.changed),
        missing=(*reconciliation.missing, *goal_reconciliation.missing),
        reason=f"heartbeat:{trigger}",
    )
    sync_goal_files(workspace, database)
    state.update(_validated_metrics(metrics))
    opening_unmet = unmet_required_criteria(database)
    state["evidence_gap_count"] = len(opening_unmet)
    state["active_criterion"] = _first_unmet_criterion_for_active_task(
        database,
        state.get("active_task"),
        opening_unmet,
    )
    mode, rationale = choose_mode(state, requested_mode)
    sequence = int(state.get("sequence", 0)) + 1
    lifecycle_before = str(state.get("lifecycle", "READY"))
    candidates: dict[str, Path] = {
        workspace_relative(path, workspace): path
        for path in reconciliation.changed
        if workspace_relative(path, workspace) not in DYNAMIC_CANONICAL_FILES
    }
    explicit_goal_sources: dict[str, Path] = {}
    for relative in changed_paths or []:
        path = resolve_workspace_path(workspace, relative)
        relative_path = workspace_relative(path, workspace)
        relative_parts = Path(relative_path).parts
        if (
            len(relative_parts) == 2
            and relative_parts[0] == "goals"
            and path.suffix.casefold() == ".json"
        ):
            explicit_goal_sources[relative_path] = path
            continue
        if relative_path not in DYNAMIC_CANONICAL_FILES:
            candidates[relative_path] = path
    receipts: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    successful_paths: list[Path] = []
    for relative, path in sorted(explicit_goal_sources.items()):
        if not path.is_file():
            failures.append(
                {"path": relative, "error": "explicit goal source does not exist"}
            )
    def promotion_order(item: tuple[str, Path]) -> tuple[int, str]:
        relative = item[0]
        if relative.startswith("tasks/"):
            return (0, relative)
        if relative == "AGENTS.md":
            return (1, relative)
        return (2, relative)

    for relative, path in sorted(candidates.items(), key=promotion_order):
        try:
            receipt = promote_document(path, workspace, database)
            receipts.append(receipt)
            successful_paths.append(path)
        except Exception as exc:
            failures.append({"path": relative, "error": str(exc)})
    canonical_signature: str | None = None
    if not failures:
        try:
            current_unmet = unmet_required_criteria(database)
            state["evidence_gap_count"] = len(current_unmet)
            state["active_criterion"] = _first_unmet_criterion_for_active_task(
                database,
                state.get("active_task"),
                current_unmet,
            )
            projection_state = dict(state)
            projection_state["lifecycle"] = "BLOCKED" if reconciliation.missing else "ACTIVE"
            projection_state["mode"] = mode
            canonical_paths, canonical_signature = refresh_dynamic_canonicals(
                workspace,
                database,
                projection_state,
                source_check={
                    "checked": use_reconciliation,
                    "fresh": use_reconciliation and not reconciliation.missing and not goal_reconciliation.missing,
                    "changed": [],
                    "missing": sorted((*reconciliation.missing, *goal_reconciliation.missing)),
                    "failures": [],
                },
            )
            for path in canonical_paths:
                relative = workspace_relative(path, workspace)
                candidates[relative] = path
                receipt = promote_document(path, workspace, database, allow_dynamic=True)
                receipts.append(receipt)
                successful_paths.append(path)
            dynamic_reconciled = [
                path
                for path in reconciliation.changed
                if workspace_relative(path, workspace) in DYNAMIC_CANONICAL_FILES
            ]
            if dynamic_reconciled:
                connection = database.connect(read_only=True)
                try:
                    for path in dynamic_reconciled:
                        relative = workspace_relative(path, workspace)
                        row = connection.execute(
                            "SELECT content_sha256 FROM artifacts WHERE path=?",
                            (relative,),
                        ).fetchone()
                        if row and sha256_text(
                            path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
                        ) == row["content_sha256"]:
                            successful_paths.append(path)
                finally:
                    connection.close()
        except Exception as exc:
            failures.append({"path": "<canonical-projection>", "error": str(exc)})
    now = utc_now()
    if use_reconciliation:
        update_manifest(workspace, reconciliation, successful_paths, updated_at=now)
    compiler_include_edge_projection: dict[str, Any] = {
        "schema": "kairos-compiler-include-edge-projection/v1",
        "configured": False,
        "verified": True,
        "edge_count": 0,
        "topology_sha256": None,
    }
    if not failures:
        try:
            from .include_edges import project_compiler_include_edges

            compiler_include_edge_projection = project_compiler_include_edges(workspace, database)
        except Exception as exc:
            failures.append({"path": "<compiler-include-edges>", "error": str(exc)})

    pending, failed = database.pending_counts()
    missing_sources = tuple(sorted((*reconciliation.missing, *goal_reconciliation.missing)))
    verified = not failures and not missing_sources and pending == 0 and failed == 0
    if use_reconciliation and verified:
        update_goal_manifest(workspace, goal_reconciliation, updated_at=now)
    lifecycle_after = "ACTIVE" if verified else "BLOCKED"
    heartbeat_id = "HB_" + sha256_text(
        f"{sequence}|{mode}|{now}|{len(receipts)}|{pending}|{failed}|{','.join(missing_sources)}"
    )[:32]
    receipt = {
        "schema": "kairos-heartbeat-receipt/v1",
        "heartbeat_id": heartbeat_id,
        "sequence": sequence,
        "lifecycle_before": lifecycle_before,
        "lifecycle_after": lifecycle_after,
        "mode": mode,
        "mode_rationale": rationale,
        "trigger": trigger,
        "governance_attribution": governance_attribution,
        "changed_candidates": sorted(candidates),
        "changed_goal_sources": sorted(
            {
                workspace_relative(path, workspace)
                for path in goal_reconciliation.changed
            }
            | set(explicit_goal_sources)
        ),
        "promoted": [value["artifact_id"] for value in receipts],
        "promotion_receipts": [value["receipt_id"] for value in receipts],
        "compiler_include_edges": compiler_include_edge_projection,
        "failures": failures,
        "missing_sources": list(missing_sources),
        "pending_count": pending,
        "failed_count": failed,
        "verified": verified,
        "created_at": now,
    }
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO heartbeat_receipts(
                heartbeat_id,sequence,lifecycle_before,lifecycle_after,mode,promoted_count,
                pending_count,failed_count,verified,receipt_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                heartbeat_id,
                sequence,
                lifecycle_before,
                lifecycle_after,
                mode,
                len(receipts),
                pending,
                failed,
                1 if verified else 0,
                json_dumps(receipt, pretty=False),
                now,
            ),
        )
    state.update(
        {
            "sequence": sequence,
            "lifecycle": lifecycle_after,
            "mode": mode,
            "mode_rationale": rationale,
            "pending_promotions": pending,
            "failed_promotions": failed,
            "last_promotion_receipt": receipts[-1]["receipt_id"] if receipts else state.get("last_promotion_receipt"),
            "last_heartbeat_receipt": heartbeat_id,
            "updated_at": now,
        }
    )
    atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
    if canonical_signature and verified:
        commit_canonical_projection(workspace, canonical_signature)
    heartbeat_directory = workspace / ".kairos" / "heartbeats"
    atomic_write_json(heartbeat_directory / f"{heartbeat_id}.json", receipt)
    prune_oldest_files(heartbeat_directory, suffix=".json", keep=MAX_GENERATED_RECEIPT_FILES)
    _update_current(workspace, state)
    _journal(
        database,
        sequence=sequence,
        mode=mode,
        state=state,
        summary=f"Heartbeat {sequence} promoted {len(receipts)} artifact(s)",
        result="SUCCESS" if verified else "BLOCKED",
        details={
            "heartbeat_id": heartbeat_id,
            "trigger": trigger,
            "changed_candidate_count": len(candidates),
            "changed_goal_source_count": len(goal_reconciliation.changed),
            "promoted_count": len(receipts),
            "failure_count": len(failures),
            "missing_sources": list(missing_sources),
            "pending_count": pending,
            "failed_count": failed,
        },
    )
    return receipt


def _pending_finalization_archive_path(workspace: Path, loop: int) -> Path:
    return workspace / ".kairos" / "finalization_pending" / f"ARCHIVE_L{loop:04d}.json"


def _record_pending_finalization_archive(
    workspace: Path,
    *,
    loop: int,
    archive_id: str,
    heartbeat_id: str,
) -> None:
    atomic_write_json(
        _pending_finalization_archive_path(workspace, loop),
        {
            "schema": FINALIZATION_PENDING_SCHEMA,
            "loop": loop,
            "archive_id": archive_id,
            "archive_path": f"archive/{archive_id}.md",
            "heartbeat_id": heartbeat_id,
            "created_at": utc_now(),
        },
    )


def _clear_pending_finalization_archive(workspace: Path, loop: int) -> None:
    marker = _pending_finalization_archive_path(workspace, loop)
    if marker.exists():
        marker.unlink()


def _recover_pending_finalization_archives(
    workspace: Path,
    database: KnowledgeDatabase,
) -> list[str]:
    """Discard only a finalizer-owned archive that never reached the artifact ledger.

    The marker is written before the archive source. It makes a crash or a failed
    promotion recoverable without treating an arbitrary file in archive/ as trusted.
    A promoted archive is immutable and is never removed here.
    """
    directory = workspace / ".kairos" / "finalization_pending"
    if not directory.is_dir():
        return []
    recovered: list[str] = []
    for marker in sorted(directory.glob("ARCHIVE_L*.json")):
        pending = read_json(marker, default={}) or {}
        loop = pending.get("loop")
        archive_id = pending.get("archive_id")
        archive_path = pending.get("archive_path")
        if (
            pending.get("schema") != FINALIZATION_PENDING_SCHEMA
            or not isinstance(loop, int)
            or loop < 1
            or archive_id != f"ARCHIVE_L{loop:04d}"
            or archive_path != f"archive/{archive_id}.md"
            or marker.name != f"{archive_id}.json"
        ):
            raise HeartbeatError(f"invalid pending finalization archive marker: {marker}")
        source = workspace / "archive" / f"{archive_id}.md"
        connection = database.connect(read_only=True)
        try:
            artifact = connection.execute(
                "SELECT artifact_id,path,state FROM artifacts WHERE artifact_id=? OR path=?",
                (archive_id, archive_path),
            ).fetchone()
        finally:
            connection.close()
        if artifact:
            if (
                artifact["artifact_id"] != archive_id
                or artifact["path"] != archive_path
                or artifact["state"] != "finalized"
                or not source.is_file()
            ):
                raise HeartbeatError(
                    f"pending finalization archive has inconsistent promoted state: {archive_id}"
                )
            marker.unlink()
            continue
        if source.exists():
            if not source.is_file():
                raise HeartbeatError(
                    f"pending finalization archive is not a file: {archive_path}"
                )
            source.unlink()
            recovered.append(archive_path)
        marker.unlink()
    return recovered


def _recover_orphaned_finalization_events(
    workspace: Path,
    database: KnowledgeDatabase,
) -> list[str]:
    """Supersede only retryable events for the reserved current-loop archive."""
    current = read_json(workspace / "current.json", default={}) or {}
    loop = int(current.get("loop", 1))
    archive_id = f"ARCHIVE_L{loop:04d}"
    archive_path = f"archive/{archive_id}.md"
    source = workspace / archive_path
    marker = _pending_finalization_archive_path(workspace, loop)
    if source.exists() or marker.exists():
        return []
    now = utc_now()
    with database.transaction() as connection:
        artifact = connection.execute(
            "SELECT 1 FROM artifacts WHERE artifact_id=? OR path=? LIMIT 1",
            (archive_id, archive_path),
        ).fetchone()
        if artifact:
            return []
        rows = connection.execute(
            """
            SELECT event_id
            FROM promotion_events
            WHERE artifact_id=? AND path=? AND revision=1
              AND status IN ('pending','failed')
              AND NOT EXISTS (
                  SELECT 1 FROM promotion_receipts pr
                  WHERE pr.event_id=promotion_events.event_id AND pr.verified=1
              )
            ORDER BY created_at,event_id
            """,
            (archive_id, archive_path),
        ).fetchall()
        event_ids = [str(row["event_id"]) for row in rows]
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            connection.execute(
                f"""
                UPDATE promotion_events
                SET status='superseded',applied_at=?,
                    error=coalesce(error || ' | ', '') || ?
                WHERE event_id IN ({placeholders})
                """,
                (
                    now,
                    "superseded during verified finalizer retry before archive regeneration",
                    *event_ids,
                ),
            )
    return event_ids


def _archive_document(
    workspace: Path,
    database: KnowledgeDatabase,
    loop: int,
    heartbeat: dict[str, Any],
    *,
    task_snapshot: dict[str, Any] | None = None,
) -> tuple[Path, str]:
    artifact_id = f"ARCHIVE_L{loop:04d}"
    path = workspace / "archive" / f"ARCHIVE_L{loop:04d}.md"
    connection = database.connect(read_only=True)
    try:
        existing_archive = connection.execute(
            "SELECT artifact_id,path,state,content_sha256 FROM artifacts WHERE artifact_id=? OR path=?",
            (artifact_id, f"archive/ARCHIVE_L{loop:04d}.md"),
        ).fetchone()
        if path.exists() or existing_archive:
            if not path.is_file() or not existing_archive:
                raise HeartbeatError(f"archive collision for loop {loop}: source/database parity is incomplete")
            prepared = prepare_document(path, workspace)
            if (
                prepared.frontmatter.metadata.get("id") != artifact_id
                or existing_archive["artifact_id"] != artifact_id
                or existing_archive["path"] != f"archive/ARCHIVE_L{loop:04d}.md"
                or existing_archive["state"] != "finalized"
                or existing_archive["content_sha256"] != prepared.content_sha256
            ):
                raise HeartbeatError(f"archive collision for loop {loop}: existing archive is not the verified immutable artifact")
            if loop >= TASK_SNAPSHOT_START_LOOP:
                if not task_snapshot or not task_snapshot.get("verified"):
                    raise HeartbeatError(
                        f"archive loop {loop} requires a verified task snapshot package"
                    )
                try:
                    binding = parse_task_snapshot_binding(prepared.text)
                except TaskSnapshotError as exc:
                    raise HeartbeatError(
                        f"archive loop {loop} has no valid task snapshot binding: {exc}"
                    ) from exc
                expected_binding = {
                    "path": task_snapshot.get("path"),
                    "package_sha256": task_snapshot.get("package_sha256"),
                    "previous_package_sha256": task_snapshot.get("previous_package_sha256"),
                }
                if binding != expected_binding:
                    raise HeartbeatError(
                        f"archive loop {loop} task snapshot binding differs from the verified package"
                    )
            return path, artifact_id
        artifact_count = int(connection.execute("SELECT count(*) FROM artifacts").fetchone()[0])
        artifacts = connection.execute(
            """
            SELECT artifact_id,path,document_type,state,capsule
            FROM artifacts ORDER BY promoted_at DESC,artifact_id LIMIT 25
            """
        ).fetchall()
        manifest_hash = hashlib.sha256()
        for row in connection.execute(
            """
            SELECT artifact_id,path,document_type,state,revision,content_sha256
            FROM artifacts ORDER BY artifact_id
            """
        ):
            manifest_hash.update(
                (json_dumps(dict(row), pretty=False) + "\n").encode("utf-8")
            )
        promotion_count = connection.execute(
            "SELECT count(*) FROM promotion_receipts WHERE verified=1"
        ).fetchone()[0]
        heartbeat_count = connection.execute(
            "SELECT count(*) FROM heartbeat_receipts WHERE verified=1"
        ).fetchone()[0]
        task_row = connection.execute(
            """
            SELECT artifact_id,path FROM artifacts
            WHERE document_type='task' ORDER BY updated_at DESC LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    refs = {}
    if task_row:
        refs["task"] = pointer(
            task_row["path"],
            section="s-objective",
            artifact_id=task_row["artifact_id"],
            relation="documents",
            tags=("task", "archive"),
            source="finalization",
            version="dynamic",
        )
    validation_question = f"Which receipts prove KAIROS loop {loop} finalization?"
    metadata = {
        "schema": "kairos-context/v1",
        "id": artifact_id,
        "type": "archive",
        "revision": 1,
        "state": "finalized",
        "authority": "loop_archive",
        "workspace": load_config(workspace)["workspace_id"],
        "route": f"LOOP_{loop:04d}/{artifact_id}",
        "loop": loop,
        "updated_at": utc_now(),
        "capsule": f"Immutable synthesis of KAIROS loop {loop} after verified promotion and criterion coverage gates.",
        "claim_boundary": "This archive proves only the receipts and criteria enumerated here; it is not external release authorization.",
        "entities": [artifact_id, heartbeat["heartbeat_id"], "promotion receipt", "finalization"],
        "facets": [
            "archive",
            "finalization",
            "experience",
            "evidence-manifest",
            *(["task-snapshot"] if loop >= TASK_SNAPSHOT_START_LOOP else []),
        ],
        "criteria": [],
        "does_not_answer": ["external deployment authorization"],
        "answers": [
            {"intent": "chronology", "question": f"What was finalized in KAIROS loop {loop}?", "target": "s-summary"},
            {"intent": "evidence", "question": validation_question, "target": "s-validation"},
            {"intent": "experience", "question": "Which reusable experience emerged from the loop?", "target": "s-experience"},
            *(
                [
                    {
                        "intent": "evidence",
                        "question": f"Which immutable task snapshot proves KAIROS loop {loop} task state?",
                        "target": "s-task-snapshot",
                    }
                ]
                if loop >= TASK_SNAPSHOT_START_LOOP
                else []
            ),
        ],
        "refs": refs,
        "search_contract": [
            {
                "query": validation_question,
                "expected": f"{artifact_id}#s-validation",
                "required_top_k": 5,
            }
        ],
    }
    manifest_lines = "\n".join(
        f"- `{row['artifact_id']}` — `{row['path']}` — {row['state']} — {row['capsule']}" for row in artifacts
    )
    if artifact_count > len(artifacts):
        manifest_lines += (
            f"\n- {artifact_count - len(artifacts)} additional artifact(s) remain in the verified metadata base; "
            "use chronology or exact-identifier search."
        )
    _record_pending_finalization_archive(
        workspace,
        loop=loop,
        archive_id=artifact_id,
        heartbeat_id=heartbeat["heartbeat_id"],
    )
    sections = [
        {
            "id": "s-summary",
            "title": "LOOP SUMMARY",
            "capsule": f"Loop {loop} closed only after a verified final heartbeat and complete mandatory goal coverage.",
            "content": f"Final heartbeat: `{heartbeat['heartbeat_id']}`. All finalization gates passed before archive creation.",
        },
        {
            "id": "s-manifest",
            "title": "ARTIFACT MANIFEST",
            "capsule": f"The finalization view contained {artifact_count} promoted artifacts; this bounded sample is bound by SHA-256 {manifest_hash.hexdigest()}.",
            "content": f"Logical manifest SHA-256: `{manifest_hash.hexdigest()}`\n\n{manifest_lines}",
        },
    ]
    if loop >= TASK_SNAPSHOT_START_LOOP:
        if not task_snapshot or not task_snapshot.get("verified"):
            raise HeartbeatError(f"archive loop {loop} requires a verified task snapshot package")
        previous = task_snapshot.get("previous_package_sha256") or "none"
        sections.append(
            {
                "id": "s-task-snapshot",
                "title": "IMMUTABLE TASK SNAPSHOT",
                "capsule": "The current-loop task contracts are captured as exact UTF-8 source bytes in a hash-bound package chained to the preceding finalized loop.",
                "content": (
                    f"Task snapshot package: `{task_snapshot['path']}` | "
                    f"SHA-256: `{task_snapshot['package_sha256']}` | "
                    f"previous SHA-256: `{previous}`\n\n"
                    f"Captured task contracts: `{len(task_snapshot.get('entries', []))}`. "
                    "This package is immutable evidence and is not part of the active Markdown search corpus."
                ),
            }
        )
    sections.extend(
        [
            {
                "id": "s-validation",
                "title": "VALIDATION RECEIPTS",
                "capsule": f"{promotion_count} promotion receipts and {heartbeat_count} verified heartbeat receipts existed before archive promotion.",
                "content": f"- Final heartbeat: `{heartbeat['heartbeat_id']}`\n- Verified promotions: `{promotion_count}`\n- Verified heartbeats: `{heartbeat_count}`",
            },
            {
                "id": "s-experience",
                "title": "REUSABLE EXPERIENCE",
                "capsule": "Context documents become reliable memory only when question handles, target sections, typed relations, and receipts are promoted together.",
                "content": "Situation: long autonomous loop. Action: document and promote every material heartbeat. Outcome: section-addressable, goal-linked context. Boundary: external authority remains separate.",
            },
            {
                "id": "s-next",
                "title": "NEXT LOOP SEED",
                "capsule": "Start a new loop by decomposing the next goal before adding unrelated work.",
                "content": "Create the new milestone graph, active task contract, and minimal session packet before changing lifecycle back to READY.",
            },
        ]
    )
    content = render_document(
        metadata,
        artifact_id,
        sections,
    )
    atomic_write_text(path, content)
    return path, artifact_id


def finalize_workspace(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    with workspace_write_lock(workspace):
        return _finalize_workspace_locked(workspace)


def _finalize_workspace_locked(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    recovered_archives = _recover_pending_finalization_archives(workspace, database)
    recovered_events = _recover_orphaned_finalization_events(workspace, database)
    heartbeat = run_heartbeat(workspace, requested_mode="verify", use_reconciliation=True)
    readiness = evaluate_finalization_readiness(
        database,
        source_check={
            "checked": True,
            "fresh": heartbeat["verified"] and not heartbeat["missing_sources"] and not heartbeat["failures"],
            "changed": heartbeat["changed_candidates"],
            "missing": heartbeat["missing_sources"],
            "failures": heartbeat["failures"],
        },
    )
    blockers = readiness["blockers"]
    if blockers:
        state = load_runtime_state(workspace)
        state["lifecycle"] = "FINALIZING_METADATA_BLOCKED"
        state["updated_at"] = utc_now()
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
        _update_current(workspace, state)
        return {
            "schema": "kairos-finalization-result/v1",
            "status": "BLOCKED",
            "heartbeat_id": heartbeat["heartbeat_id"],
            "recovered_finalization_events": recovered_events,
            "blockers": blockers,
            "created_at": utc_now(),
        }
    state = load_runtime_state(workspace)
    state["lifecycle"] = "FINALIZING"
    state["updated_at"] = utc_now()
    atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
    loop = int((read_json(workspace / "current.json", default={}) or {}).get("loop", 1))
    try:
        task_snapshot = ensure_task_snapshot_package(
            workspace,
            database,
            loop,
            heartbeat["heartbeat_id"],
        )
        archive_path, archive_id = _archive_document(
            workspace,
            database,
            loop,
            heartbeat,
            task_snapshot=task_snapshot,
        )
        archive_receipt = promote_document(archive_path, workspace, database)
        update_manifest(
            workspace,
            reconcile_documents(workspace),
            [archive_path],
            updated_at=utc_now(),
        )
        _clear_pending_finalization_archive(workspace, loop)
    except Exception as exc:
        state["lifecycle"] = "BLOCKED"
        state["updated_at"] = utc_now()
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
        _update_current(workspace, state)
        return {
            "schema": "kairos-finalization-result/v1",
            "status": "BLOCKED",
            "heartbeat_id": heartbeat["heartbeat_id"],
            "recovered_unpromoted_archives": recovered_archives,
            "recovered_finalization_events": recovered_events,
            "blockers": [
                {
                    "type": "task_snapshot_package" if isinstance(exc, TaskSnapshotError) else "archive_collision",
                    "error": str(exc),
                }
            ],
            "created_at": utc_now(),
        }
    pending, failed = database.pending_counts()
    if pending or failed:
        state["lifecycle"] = "BLOCKED"
        state["pending_promotions"] = pending
        state["failed_promotions"] = failed
        state["updated_at"] = utc_now()
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
        _update_current(workspace, state)
        return {
            "schema": "kairos-finalization-result/v1",
            "status": "BLOCKED",
            "heartbeat_id": heartbeat["heartbeat_id"],
            "blockers": [{"type": "archive_promotion", "pending": pending, "failed": failed}],
            "created_at": utc_now(),
        }
    try:
        backup = create_backup(workspace)
    except Exception as exc:
        state["lifecycle"] = "BLOCKED"
        state["updated_at"] = utc_now()
        atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
        _update_current(workspace, state)
        return {
            "schema": "kairos-finalization-result/v1",
            "status": "BLOCKED",
            "heartbeat_id": heartbeat["heartbeat_id"],
            "blockers": [{"type": "verified_backup", "error": str(exc)}],
            "created_at": utc_now(),
        }
    now = utc_now()
    finalization_id = "FIN_" + sha256_text(
        f"{heartbeat['heartbeat_id']}|{archive_receipt['receipt_id']}|{now}"
    )[:32]
    result = {
        "schema": "kairos-finalization-result/v1",
        "status": "FINALIZED",
        "finalization_id": finalization_id,
        "loop": loop,
        "heartbeat_id": heartbeat["heartbeat_id"],
        "recovered_unpromoted_archives": recovered_archives,
        "recovered_finalization_events": recovered_events,
        "archive_id": archive_id,
        "archive_path": workspace_relative(archive_path, workspace),
        "archive_sha256": archive_receipt["content_sha256"],
        "archive_receipt": archive_receipt["receipt_id"],
        "task_snapshot": task_snapshot,
        "backup_id": backup["backup_id"],
        "created_at": now,
    }
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO finalization_receipts(
                finalization_id,loop,heartbeat_id,archive_id,archive_receipt_id,
                backup_id,receipt_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                finalization_id,
                loop,
                heartbeat["heartbeat_id"],
                archive_id,
                archive_receipt["receipt_id"],
                backup["backup_id"],
                json_dumps(result, pretty=False),
                now,
            ),
        )
    state["lifecycle"] = "FINALIZED"
    state["loop"] = loop
    state["last_finalization_id"] = finalization_id
    state["last_archive_id"] = archive_id
    state["last_backup_id"] = backup["backup_id"]
    state["last_promotion_receipt"] = archive_receipt["receipt_id"]
    state["updated_at"] = now
    atomic_write_json(workspace / ".kairos" / "runtime_state.json", state)
    atomic_write_json(workspace / ".kairos" / "finalization" / f"{finalization_id}.json", result)
    _update_current(workspace, state)
    return result


def status_report(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    state = load_runtime_state(workspace)
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    connection = database.connect(read_only=True)
    try:
        counts = {
            "artifacts": connection.execute("SELECT count(*) FROM artifacts").fetchone()[0],
            "sections": connection.execute("SELECT count(*) FROM sections").fetchone()[0],
            "relations": connection.execute("SELECT count(*) FROM relations").fetchone()[0],
            "query_handles": connection.execute("SELECT count(*) FROM query_handles").fetchone()[0],
            "promotion_receipts": connection.execute("SELECT count(*) FROM promotion_receipts WHERE verified=1").fetchone()[0],
            "heartbeat_receipts": connection.execute("SELECT count(*) FROM heartbeat_receipts WHERE verified=1").fetchone()[0],
        }
    finally:
        connection.close()
    from .recycling_bridge import live_recycling_status
    tx = live_recycling_status(workspace)
    return {
        "workspace": str(workspace),
        "runtime": state,
        "counts": {key: int(value) for key, value in counts.items()},
        "unmet_criteria": unmet_required_criteria(database),
        "tx_recycling_live": tx,
        "next_required_read": (
            "_LOOP_GATE.md#s-tx-recycling" if tx.get("required") is True else
            "_LOOP_GATE.md#s-verdict" if tx.get("reconciliation_required") else
            (read_json(workspace / "current.json", default={}) or {}).get("next_required_read")
        ),
    }
