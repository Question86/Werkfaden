"""Compatibility module for adaptive tomography's TypeSafe attempt gate import."""

from typing import Any

from jev_workshop_attempt_gate_typesafe import *  # noqa: F401,F403


def build_state(
    transaction_id: str,
    transaction_state: dict[str, Any],
    current: dict[str, Any] | None,
    changes: list[dict[str, Any]],
    graph: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose the current TypeSafe review envelope expected by tomography."""
    return {
        "review_contract": "WERKFADEN_JEV_WORKSHOP_PATCH_ATTEMPT_V1",
        "review_mode": "PRE_EXECUTION_PATCH_ATTEMPT",
        "transaction": {
            "transaction_id": transaction_id,
            "state": transaction_state.get("state"),
            "purpose": transaction_state.get("purpose"),
            "selected_sources": transaction_state.get("sources") or transaction_state.get("selected_sources"),
            "dependent_tests": transaction_state.get("dependent_tests"),
            "transaction_kind": transaction_state.get("transaction_kind", "normal"),
        },
        "workspace_state": current or {},
        "system_materialized_code_changes": [compact_review_change(change) for change in changes],
        "system_materialized_graph_receipts": graph,
    }
