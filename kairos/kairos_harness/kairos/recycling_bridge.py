"""Read-only live Workshop recycling status for KAIROS session reorientation.

The Workshop remains the sole authority for admission. Cached current.json
and router projections are NEVER substituted for a live capacity decision.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _workshop_owner_path() -> Path | None:
    """Select only this source bundle or a Python installation's package root."""
    source = Path(__file__).resolve()
    try:
        root = source.parents[3]
    except IndexError:
        root = None
    if root is not None:
        expected = root / "kairos" / "kairos_harness" / "kairos" / "recycling_bridge.py"
        try:
            source_layout = expected.resolve(strict=True) == source and (root / "kairos_cli.py").is_file()
        except (OSError, ValueError):
            source_layout = False
        if source_layout:
            candidate = root / "workshop" / "src"
            package = candidate / "runtime_sync_workshop"
            try:
                owner = candidate.resolve(strict=True)
                valid = (owner.is_relative_to(root)
                         and all((package / name).resolve(strict=True).is_relative_to(owner)
                                 for name in ("__init__.py", "engine.py", "tx_recycling.py")))
            except (OSError, ValueError):
                valid = False
            if not valid:
                raise ModuleNotFoundError("the source bundle has no trusted Workshop owner", name="runtime_sync_workshop")
            return owner

    # A separately installed harness may use an installed Workshop, but a
    # workspace or current-directory package is never a source of authority.
    from importlib.machinery import PathFinder
    import site
    import sysconfig
    locations = [sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
    locations.extend(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        locations.append(site.getusersitepackages())
    for location in dict.fromkeys(value for value in locations if isinstance(value, str) and value):
        try:
            owner = Path(location).resolve(strict=True)
            spec = PathFinder.find_spec("runtime_sync_workshop", [str(owner)])
            if spec is None or not spec.origin:
                continue
            package = Path(spec.origin).resolve(strict=True).parent
            if (package.is_relative_to(owner)
                    and all((package / name).resolve(strict=True).is_relative_to(package)
                            for name in ("__init__.py", "engine.py", "tx_recycling.py"))):
                return owner
        except (OSError, ValueError, ImportError):
            continue
    return None


def _workshop_imports():
    owner = _workshop_owner_path()
    if owner is None:
        raise ModuleNotFoundError("no trusted Workshop package is installed", name="runtime_sync_workshop")
    package = (owner / "runtime_sync_workshop").resolve(strict=True)
    for name, module in list(sys.modules.items()):
        if name != "runtime_sync_workshop" and not name.startswith("runtime_sync_workshop."):
            continue
        origin = getattr(module, "__file__", None)
        try:
            matches = isinstance(origin, str) and Path(origin).resolve(strict=True).is_relative_to(package)
        except (OSError, ValueError):
            matches = False
        if not matches:
            raise ImportError(f"conflicting already-loaded Workshop module: {name}")
    # Put the selected owner first; never use the project working directory as
    # an executable framework source. Preserve lazy imports on success.
    previous_path = sys.path[:]
    owner_text = str(owner)
    sys.path[:] = [owner_text] + [entry for entry in sys.path if entry != owner_text]
    try:
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.corpus import load_config
        from runtime_sync_workshop.tx_recycling import pool_status
    except Exception:
        sys.path[:] = previous_path
        raise
    def read_only_pool_view(path: Path):
        # Pool observation needs no writer-engine initialization or methods.
        # Reuse the native loader and canonical-control-plane validation only.
        from types import SimpleNamespace
        config = load_config(path)
        view = SimpleNamespace(config=config, lease_path=config.state_directory / "lease.json")
        WorkshopEngine._assert_canonical_control_plane(view)
        return view

    return read_only_pool_view, pool_status


def live_recycling_status(workspace: Path) -> dict[str, Any]:
    canonical = workspace / ".kairos" / "workshop.config.json"
    if not canonical.is_file():
        return {"configured": False, "reconciliation_required": False}
    try:
        policy_bytes = canonical.read_bytes()
        policy_raw = json.loads(policy_bytes)
        if not isinstance(policy_raw, dict):
            raise ValueError("TX_POLICY_INVALID: canonical config must be an object")
        policy_fields = ("transaction_documents_required", "tx_retention_required")
        policy = {}
        for field in policy_fields:
            value = policy_raw.get(field, False)
            if type(value) is not bool:
                raise ValueError(f"TX_POLICY_INVALID: {field} must be a JSON boolean when present")
            policy[field] = {"present": field in policy_raw, "value": value}
        documents = policy["transaction_documents_required"]["value"]
        retention = policy["tx_retention_required"]["value"]
        if retention and not documents:
            raise ValueError("TX_POLICY_INVALID: retention requires transaction documentation")
        pool_view_factory, pool_status = _workshop_imports()
        engine = pool_view_factory(canonical)
        if engine.config.raw != policy_raw:
            raise ValueError("TX_POLICY_CHANGED: loaded config differs from observed bytes")
        current = pool_status(engine, publish=False)
        if not isinstance(current, dict) or current.get("schema") != "werkfaden-tx-recycling-pool/v1":
            raise ValueError("TX_POOL_UNVERIFIED: unsupported native pool contract")
        for field in ("enabled", "required"):
            if type(current.get(field)) is not bool:
                raise ValueError(f"TX_POOL_UNVERIFIED: {field} is not a boolean")
        for field in ("materialized_count", "trigger_at", "target_max", "need_to_retire"):
            if type(current.get(field)) is not int or current[field] < 0:
                raise ValueError(f"TX_POOL_UNVERIFIED: {field} is not a nonnegative integer")
        if current["trigger_at"] < 1 or current["target_max"] != current["trigger_at"] - 1:
            raise ValueError("TX_POOL_UNVERIFIED: inconsistent native thresholds")
        if not isinstance(current.get("rows"), list) or any(
                not isinstance(row, dict) or not isinstance(row.get("transaction_id"), str)
                for row in current["rows"]):
            raise ValueError("TX_POOL_UNVERIFIED: invalid native transaction observations")
        if current.get("active_lease") is not None and not isinstance(current["active_lease"], str):
            raise ValueError("TX_POOL_UNVERIFIED: invalid native lease observation")
        if current.get("next_action") not in {"TX_RECYCLING_DISABLED", "TX_RECYCLING_REQUIRED", "NORMAL_WORK_ALLOWED"}:
            raise ValueError("TX_POOL_UNVERIFIED: unsupported native next action")
        if canonical.read_bytes() != policy_bytes:
            raise ValueError("TX_POLICY_CHANGED: config changed during live observation")
        if current["enabled"] is not retention:
            raise ValueError("TX_POLICY_CHANGED: native enabled state differs from observed policy")
    except Exception as exc:
        return {
            "configured": True, "available": False,
            "policy_state": "UNVERIFIED",
            "reconciliation_required": True,
            "reason": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
            "next_action": "TX_POLICY_UNVERIFIED",
            "recovery_boundary": "Inspect native Workshop status; use supported owner-specific recovery, never edit a sealed config directly.",
        }
    cache_path = workspace / ".kairos" / "tx_recycling_frontier.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            cached = {}
    except (OSError, ValueError):
        cached = {}
    source_rows = [
        {"transaction_id": row["transaction_id"], "state": row.get("state"),
         "stage": row.get("stage")}
        for row in current["rows"]
    ]
    policy_sha = hashlib.sha256(policy_bytes).hexdigest()
    observation = {
        "config_sha256": policy_sha,
        "pool": {key: current[key] for key in (
            "enabled", "required", "materialized_count", "trigger_at", "target_max",
            "need_to_retire", "active_lease", "next_action")},
        "transactions": source_rows,
    }
    observed_epoch = hashlib.sha256(
        json.dumps(observation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    expected_cache = {key: current[key] for key in (
        "required", "materialized_count", "trigger_at", "target_max", "need_to_retire", "next_action")}
    expected_cache["schema"] = "werkfaden-tx-recycling-frontier/v1"
    mismatch = any(type(cached.get(key)) is not type(value) or cached.get(key) != value
                   for key, value in expected_cache.items())
    inactive = not (documents and retention)
    guide = "docs/KAIROS_WORKSPACE_TOOLS.md"
    policy_read = (guide + "#s-retention" if (workspace / guide).is_file()
                   else "_LOOP_GATE.md#s-verdict")
    return {
        "schema": "kairos-live-tx-recycling-status/v1",
        "configured": True,
        "available": True,
        "enabled": current["enabled"],
        "policy_state": "INACTIVE" if inactive else "ACTIVE",
        "policy": policy,
        "policy_config_sha256": policy_sha,
        "policy_activation_required": inactive,
        "policy_activation_boundary": "A supported verified sealed-policy migration is required; this read-only observation grants no config or seal edit authority.",
        "required": current["required"],
        "materialized_count": current["materialized_count"],
        "target_max": current["target_max"],
        "source_epoch": observed_epoch,
        "cached_frontier_fresh": not mismatch,
        "reconciliation_required": mismatch,
        "next_required_read": policy_read if inactive else None,
        "next_action": ("TX_POLICY_ACTIVATION_REQUIRED" if inactive else
                        "tx-recycle-status --refresh-loop" if mismatch else
                        "tx-recycle-inspect or tx-recycle-drain" if current["required"] else
                        "NORMAL_WORK_ALLOWED"),
    }
