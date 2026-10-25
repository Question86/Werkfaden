from __future__ import annotations

from pathlib import Path
from typing import Any

from .heartbeat import run_heartbeat
from .goals import reconcile_goal_files
from .governance import GovernanceError
from .reconcile import reconcile_documents
from .util import workspace_relative


class FreshnessError(RuntimeError):
    pass


def ensure_workspace_fresh(workspace: Path, *, reason: str,
                           verify_content_hashes: bool = False) -> dict[str, Any]:
    workspace = workspace.resolve()
    reconciliation = reconcile_documents(workspace, verify_content_hashes=verify_content_hashes)
    goal_reconciliation = reconcile_goal_files(workspace)
    if (
        not reconciliation.changed
        and not reconciliation.missing
        and not goal_reconciliation.changed
        and not goal_reconciliation.missing
    ):
        return {
            "checked": True,
            "content_hashes_verified": verify_content_hashes,
            "refreshed": False,
            "reason": reason,
            "changed": [],
            "missing": [],
            "changed_goals": [],
            "missing_goals": [],
        }
    try:
        receipt = run_heartbeat(
            workspace,
            requested_mode="verify",
            changed_paths=[workspace_relative(path, workspace)
                           for path in reconciliation.changed],
            use_reconciliation=True,
            trigger=f"implicit:{reason}",
        )
    except GovernanceError as exc:
        raise FreshnessError(str(exc)) from exc
    result = {
        "checked": True,
        "content_hashes_verified": verify_content_hashes,
        "refreshed": True,
        "reason": reason,
        "changed": [workspace_relative(path, workspace) for path in reconciliation.changed],
        "missing": list(reconciliation.missing),
        "changed_goals": [
            workspace_relative(path, workspace) for path in goal_reconciliation.changed
        ],
        "missing_goals": list(goal_reconciliation.missing),
        "heartbeat_id": receipt["heartbeat_id"],
        "verified": receipt["verified"],
        "governance": receipt.get("governance_attribution", {}),
    }
    if not receipt["verified"]:
        raise FreshnessError(
            f"implicit freshness heartbeat {receipt['heartbeat_id']} failed closed: "
            f"{receipt['failures'] or receipt['missing_sources']}"
        )
    return result
