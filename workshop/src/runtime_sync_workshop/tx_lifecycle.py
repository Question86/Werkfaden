"""One lifecycle owner for Workshop TX events and historical scope provenance.

Raw engine event strings remain journal diagnostics. Document publication is
driven by the persisted lifecycle state, not by competing name lists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json, utc_now

PHASES = {
    "CHECKED_OUT": "CREATED",
    "AWAITING_METADATA_REVIEW": "EDITING",
    "CHECKOUT_STAGING": "CREATED",
    "CHECKOUT_FAILED": "RECOVERY_REQUIRED",
    "PREPARED": "PREPARED",
    "SHADOW_VERIFIED": "VERIFIED",
    "APPLYING": "APPLYING",
    "POSTCHECK_VERIFIED": "POSTCHECKED",
    "ABORTED": "ABORTED",
    "ABORTED_STALE_BASELINE": "ABORTED",
    "ABORTED_ORPHANED_CHECKOUT": "ABORTED",
    "ROLLED_BACK": "ROLLED_BACK",
    "RECOVERY_REQUIRED": "RECOVERY_REQUIRED",
}
PROJECTED_STATES = frozenset(PHASES)
MATERIAL_ANNOTATIONS = frozenset({
    "TX_NOTE", "TX_COMPACTED", "TX_RECYCLE_REVIEWED",
    "TX_RECYCLING_ORPHAN_RETIRED",
})


def scope_from_checkout(workspace: Path) -> dict[str, Any] | None:
    """Capture current scope only at first TX creation, NEVER at legacy backfill."""
    current_path = workspace / "current.json"
    config_path = workspace / ".kairos" / "config.json"
    try:
        raw = current_path.read_bytes()
        current = json.loads(raw)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        scope = {
            "workspace_id": config["workspace_id"],
            "goal_id": current["active_goal"],
            "milestone_id": current["active_milestone"],
            "task_id": current["active_task"],
            "loop": current["loop"],
        }
        if (any(not isinstance(scope[key], str) or not scope[key]
                for key in ("workspace_id", "goal_id", "milestone_id", "task_id"))
                or type(scope["loop"]) is not int or scope["loop"] < 1):
            return None
        return {**scope,
                "observed_at": utc_now(),
                "source_path": "current.json",
                "source_receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "binding_kind": "CHECKOUT_OBSERVED"}
    except (OSError, KeyError, TypeError, ValueError):
        return None


def stage_lifecycle_event(engine: Any, root: Path, state: dict[str, Any], *,
                          event: str) -> dict[str, Any] | None:
    """Stamp monotone identity into authoritative state before its atomic save.

    The pending marker is created before the state save; an interrupted first
    save can only overblock checkout, never silently lose a required event.
    """
    old_path = root / "state.json"
    try:
        old = json.loads(old_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    if not old and "original_scope" not in state:
        original = scope_from_checkout(engine.config.kairos_workspace)
        if original:
            state["original_scope"] = original
    previous = str(old.get("state") or "")
    current = str(state.get("state") or "")
    if current not in PHASES:
        raise WorkshopError(
            "TX_LIFECYCLE_UNKNOWN_STATE",
            "transaction state has no supported lifecycle phase",
            details={"phase": "UNKNOWN_UNSUPPORTED", "state": current, "event": event},
        )
    required = ((current in PROJECTED_STATES and current != previous)
                or event in MATERIAL_ANNOTATIONS)
    if not required:
        return None
    txid = str(state.get("transaction_id") or "")
    seq = int(old.get("tx_event_sequence", 0)) + 1
    payload = {
        "schema": "werkfaden-tx-event/v1",
        "transaction_id": txid,
        "tx_kind": str(state.get("transaction_kind") or state.get("schema") or "normal"),
        "sequence": seq,
        "event": event,
        "phase": PHASES.get(current, "UNKNOWN_UNSUPPORTED"),
        "state_before": previous,
        "state_after": current,
        "candidate_sha256": str(state.get("verified_work_package_sha256") or state.get("prepared_work_package_sha256") or ""),
        "baseline_sha256": str(state.get("baseline_package_sha256") or ""),
        "occurred_at": utc_now(),
        "required_projection": engine.config.raw.get("transaction_documents_required", False) is True,
        "previous_event_id": str(old.get("tx_event_id") or ""),
        "scope_receipt_sha256": str((state.get("original_scope") or {}).get("source_receipt_sha256") or ""),
    }
    event_id = "TXE_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()
    state["tx_event_sequence"] = seq
    state["tx_event_id"] = event_id
    state["tx_event"] = {**payload, "event_id": event_id}
    if engine.config.raw.get("transaction_documents_required", False) is True:
        from .tx_documents import pending_path, pending_record
        marker = pending_path(engine, txid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, pending_record(
            state, event=event, reason="lifecycle event saved to outbox before state commit",
        ))
    return state["tx_event"]
