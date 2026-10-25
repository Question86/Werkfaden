"""Read-only live Workshop recycling status for KAIROS session reorientation.

The Workshop remains the sole authority for admission. Cached current.json
and router projections are NEVER substituted for a live capacity decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def live_recycling_status(workspace: Path) -> dict[str, Any]:
    canonical = workspace / ".kairos" / "workshop.config.json"
    if not canonical.is_file():
        return {"configured": False, "reconciliation_required": False}
    try:
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_recycling import pool_status, frontier_source_epoch
        engine = WorkshopEngine(canonical)
        current = pool_status(engine, publish=False)
    except Exception as exc:
        return {
            "configured": True, "available": False,
            "reconciliation_required": True,
            "reason": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
            "next_action": "Repair Workshop configuration or invoke tx-recycle-status",
        }
    cache_path = workspace / ".kairos" / "tx_recycling_frontier.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    observed_epoch = frontier_source_epoch(current)
    mismatch = (
        current["enabled"] and (
            cached.get("required") != current["required"]
            or cached.get("materialized_count") != current["materialized_count"]
            or cached.get("target_max") != current["target_max"]
            or cached.get("schema") != "werkfaden-tx-recycling-frontier/v1"
            or cached.get("source_epoch") != observed_epoch
        )
    )
    return {
        "schema": "kairos-live-tx-recycling-status/v1",
        "configured": True,
        "available": True,
        "enabled": current["enabled"],
        "required": current["required"],
        "materialized_count": current["materialized_count"],
        "target_max": current["target_max"],
        "source_epoch": observed_epoch,
        "cached_frontier_fresh": not mismatch,
        "reconciliation_required": mismatch,
        "next_action": ("tx-recycle-status --refresh-loop" if mismatch
                        else "tx-recycle-inspect or tx-recycle-drain" if current["required"]
                        else "NORMAL_WORK_ALLOWED"),
    }
