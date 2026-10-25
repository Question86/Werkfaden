from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .database import KnowledgeDatabase
from .goals import unmet_required_criteria
from .governance import governance_mode
from .workspace import load_config


_CLOSED_TASK_STATES = {"completed", "success", "superseded", "finalized"}
_CLOSED_GOAL_STATES = {"completed", "superseded"}


def evaluate_finalization_readiness(
    database: KnowledgeDatabase,
    *,
    source_check: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate the single finalization policy used by UI and execution.

    The caller supplies the freshness result because heartbeat owns reconciliation.
    Every other condition is read directly from the current metadata projection.
    """
    source = dict(source_check or {})
    source_checked = bool(source.get("checked"))
    source_fresh = source_checked and bool(source.get("fresh"))
    blockers: list[dict[str, Any]] = []
    if not source_fresh:
        blockers.append(
            {
                "type": "source_freshness",
                "checked": source_checked,
                "changed": list(source.get("changed", [])),
                "missing": list(source.get("missing", [])),
                "failures": list(source.get("failures", [])),
            }
        )

    pending, failed = database.pending_counts()
    if pending or failed:
        blockers.append(
            {
                "type": "promotion_state",
                "pending": pending,
                "failed": failed,
            }
        )

    unmet = unmet_required_criteria(database)
    if unmet:
        blockers.append({"type": "goal_coverage", "criteria": unmet})

    connection = database.connect(read_only=True)
    try:
        open_tasks = [
            dict(row)
            for row in connection.execute(
                """
                SELECT artifact_id,state FROM artifacts
                WHERE document_type='task'
                  AND state NOT IN ('completed','success','superseded','finalized')
                ORDER BY artifact_id
                """
            )
        ]
        open_goals = [
            dict(row)
            for row in connection.execute(
                """
                SELECT goal_id,state FROM goals
                WHERE state NOT IN ('completed','superseded')
                ORDER BY goal_id
                """
            )
        ]
        governance_state = connection.execute(
            "SELECT last_action_id FROM governance_state WHERE singleton_id=1"
        ).fetchone()
        current_action_id = (
            str(governance_state["last_action_id"])
            if governance_state and governance_state["last_action_id"]
            else None
        )
        quarantine_count = int(connection.execute(
            "SELECT count(*) FROM quarantine_entries WHERE status='OPEN'"
        ).fetchone()[0])
        active_permits = int(connection.execute(
            "SELECT count(*) FROM action_permits WHERE status='ACTIVE'"
        ).fetchone()[0])
        running_actions = []
        for row in connection.execute(
            "SELECT action_id,started_at FROM governed_action_receipts WHERE status='RUNNING'"
        ):
            action_id = str(row["action_id"])
            started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
            is_current = (
                action_id == current_action_id
                and (datetime.now(timezone.utc) - started).total_seconds() <= 300
            )
            if not is_current:
                running_actions.append(action_id)
    finally:
        connection.close()
    if open_tasks:
        blockers.append({"type": "task_closure", "tasks": open_tasks})
    if open_goals:
        blockers.append({"type": "goal_closure", "goals": open_goals})
    workspace = database.path.parent.parent
    config = load_config(workspace)
    governance = {
        "enforcement_mode": governance_mode(workspace),
        "audit_finalization_override": bool(
            config.get("governance_audit_allows_finalization", False)
        ),
        "open_quarantine": quarantine_count,
        "active_permits": active_permits,
        "unfinished_actions": running_actions,
        "current_action_id": current_action_id,
    }
    if (
        (
            governance["enforcement_mode"] != "required"
            and not governance["audit_finalization_override"]
        )
        or quarantine_count
        or active_permits
        or running_actions
    ):
        blockers.append({"type": "governance", **governance})

    return {
        "schema": "kairos-finalization-readiness/v1",
        "ready": not blockers,
        "source_checked": source_checked,
        "source_fresh": source_fresh,
        "pending": pending,
        "failed": failed,
        "unmet_criteria": unmet,
        "open_tasks": open_tasks,
        "open_goals": open_goals,
        "governance": governance,
        "blockers": blockers,
    }
