from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import DYNAMIC_CANONICAL_FILES, RUNTIME_SCHEMA_VERSION
from .frontmatter import split_frontmatter
from .heartbeat import run_heartbeat
from .locking import workspace_write_lock
from .util import atomic_write_json, atomic_write_text, utc_now
from .workspace import _canonical_documents, database_path, load_config


class RecoveryError(RuntimeError):
    pass


_CLOSED_TASK_STATES = {"completed", "success", "superseded", "finalized"}


def _task_metadata(workspace: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted((workspace / "tasks").glob("task_*.md")):
        metadata = dict(split_frontmatter(path.read_text(encoding="utf-8")).metadata)
        if metadata.get("type") != "task":
            raise RecoveryError(f"non-task artifact in task root: {path.name}")
        tasks.append(metadata)
    if not tasks:
        raise RecoveryError("derived-state rebuild requires at least one task source")
    return tasks


def _derived_paths(workspace: Path) -> list[Path]:
    database = database_path(workspace)
    return [
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
        workspace / ".kairos" / "runtime_state.json",
        workspace / ".kairos" / "manifest.json",
        workspace / ".kairos" / "goal_manifest.json",
        workspace / ".kairos" / "canonical_projection.json",
        workspace / "current.json",
        *[workspace / relative for relative in sorted(DYNAMIC_CANONICAL_FILES)],
    ]


def rebuild_derived_state(
    workspace: Path,
    *,
    history_backup_id: str = "",
    history_references: list[str] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    history_references = list(history_references or [])
    if bool(history_backup_id) != bool(history_references):
        raise RecoveryError(
            "history recovery requires both one backup id and at least one exact revision identity"
        )
    with workspace_write_lock(workspace):
        config = load_config(workspace)
        occupied = [path for path in _derived_paths(workspace) if path.exists()]
        if occupied:
            raise RecoveryError(
                "derived-state rebuild refuses to overwrite existing runtime surfaces: "
                + ", ".join(str(path.relative_to(workspace)).replace("\\", "/") for path in occupied)
            )
        tasks = _task_metadata(workspace)
        open_tasks = [
            metadata for metadata in tasks if metadata["state"] not in _CLOSED_TASK_STATES
        ]
        explicitly_active = [
            metadata for metadata in open_tasks if metadata["state"] in {"active", "in_progress"}
        ]
        if len(explicitly_active) > 1:
            raise RecoveryError(
                "source-only recovery found multiple active task authorities: "
                + ", ".join(str(value["id"]) for value in explicitly_active)
            )
        active = explicitly_active[0] if explicitly_active else (open_tasks[0] if len(open_tasks) == 1 else None)
        if len(open_tasks) > 1 and active is None:
            raise RecoveryError(
                "source-only recovery cannot select one active task from multiple open task contracts"
            )
        fallback = active or sorted(tasks, key=lambda value: str(value["updated_at"]), reverse=True)[0]
        now = utc_now()
        runtime = {
            "schema": RUNTIME_SCHEMA_VERSION,
            "sequence": 0,
            "loop": int(fallback.get("loop", 1)),
            "lifecycle": "ACTIVE" if active else "READY",
            "mode": "verify",
            "mode_rationale": "source-only derived-state recovery",
            "active_goal": active.get("goal") if active else None,
            "active_milestone": active.get("milestone") if active else None,
            "active_task": active.get("id") if active else None,
            "active_criterion": (active.get("criteria") or [None])[0] if active else None,
            "evidence_gap_count": len(active.get("criteria", [])) if active else 0,
            "context_pressure": 0.0,
            "contradiction_count": 0,
            "unresolved_branches": 0,
            "pending_promotions": 0,
            "failed_promotions": 0,
            "last_promotion_receipt": None,
            "last_heartbeat_receipt": None,
            "updated_at": now,
        }
        current = {
            "schema": "kairos-current/v1",
            "state_revision": 0,
            "lifecycle": runtime["lifecycle"],
            "loop": int(fallback.get("loop", 1)),
            "active_goal": runtime["active_goal"],
            "active_milestone": runtime["active_milestone"],
            "active_task": runtime["active_task"],
            "active_criterion": runtime["active_criterion"],
            "context_frontier": [],
            "pending_promotions": 0,
            "failed_promotions": 0,
            "last_promotion_receipt": None,
            "last_heartbeat_receipt": None,
            "next_required_read": "NEURAL_CORTEX.md#s-active-frontier",
            "updated_at": now,
        }
        governance_action: dict[str, Any] | None = None
        try:
            atomic_write_json(workspace / ".kairos" / "runtime_state.json", runtime)
            atomic_write_json(workspace / "current.json", current)
            temporary = _canonical_documents(config["workspace_id"], str(fallback["id"]))
            for relative in DYNAMIC_CANONICAL_FILES:
                atomic_write_text(workspace / relative, temporary[relative])
            from .governance import (
                consume_action_permit,
                finish_cli_action,
                governance_mode,
                issue_action_permit,
            )
            from .reconcile import candidate_paths
            from .util import workspace_relative

            scope = {
                "goal_id": runtime["active_goal"],
                "milestone_id": runtime["active_milestone"],
                "task_id": runtime["active_task"],
                "criterion_id": runtime["active_criterion"],
            }
            recovery_paths = [
                workspace_relative(path, workspace)
                for path in candidate_paths(workspace)
                if workspace_relative(path, workspace) not in DYNAMIC_CANONICAL_FILES
            ]
            recovery_paths.extend(
                workspace_relative(path, workspace)
                for path in (workspace / "goals").glob("*.json")
                if path.is_file()
            )
            permit = issue_action_permit(
                workspace,
                phase="RECOVERY",
                command_name="rebuild-derived",
                scope=scope,
                paths=recovery_paths,
                reason="source-only recovery preflight",
                ttl_seconds=300,
            )
            governance_action = consume_action_permit(
                workspace,
                permit_id=permit["permit_id"],
                command_name="rebuild-derived",
                phase="RECOVERY",
                expected_scope=scope,
                enforce_transition=governance_mode(workspace) == "required",
            )
            receipt = run_heartbeat(
                workspace,
                requested_mode="verify",
                use_reconciliation=True,
                trigger="recovery:derived-rebuild",
            )
            if not receipt["verified"]:
                raise RecoveryError(
                    f"derived-state recovery heartbeat failed closed: {receipt['failures'] or receipt['missing_sources']}"
                )
            revision_recovery = None
            if history_backup_id:
                from .backup import recover_artifact_revisions

                revision_recovery = recover_artifact_revisions(
                    workspace,
                    backup_id=history_backup_id,
                    revision_identities=history_references,
                )
            finish_cli_action(
                workspace,
                governance_action,
                success=True,
                result={
                    "heartbeat_id": receipt["heartbeat_id"],
                    "revision_recovery": revision_recovery,
                    "verified": True,
                },
            )
        except Exception:
            if governance_action:
                try:
                    finish_cli_action(
                        workspace,
                        governance_action,
                        success=False,
                        result={"error": "derived-state recovery failed"},
                    )
                except Exception:
                    pass
            for path in _derived_paths(workspace):
                if path.is_file():
                    path.unlink()
            raise
        result = {
            "schema": "kairos-derived-rebuild/v1",
            "workspace": str(workspace),
            "workspace_id": config["workspace_id"],
            "active_task": runtime["active_task"],
            "heartbeat_id": receipt["heartbeat_id"],
            "promoted": len(receipt["promoted"]),
            "revision_recovery": revision_recovery,
            "verified": True,
            "rebuilt_at": utc_now(),
        }
        from .governance import record_recovery_review

        result["governance_recovery_review"] = record_recovery_review(
            workspace, result
        )
        return result
