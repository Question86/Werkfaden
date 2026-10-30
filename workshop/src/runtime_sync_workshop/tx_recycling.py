"""Evidence-bound recycling of historical Workshop transactions.

This module makes *classification* separate from physical compaction.
No LLM statement is code/test authority; it is a declared, traceable decision.
Old workspaces may exceed the normal threshold. Maintenance operations remain
admitted and idempotent even when ordinary checkouts are prohibited.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json, sha256_bytes, utc_now, reject_link_components

SCHEMA = "werkfaden-tx-recycling-analysis/v1"
INVENTORY_SCHEMA = "werkfaden-tx-recycling-inventory/v1"
REVIEW_SCHEMA = "werkfaden-tx-recycling-review/v1"
DISPOSITIONS = {"RETAIN_FOR_REUSE", "RETAIN_EVIDENCE", "REJECT_FOR_REUSE"}
TERMINAL = {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE",
            "ABORTED_ORPHANED_CHECKOUT", "ROLLED_BACK"}
PRE_APPLY = {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}
MAX_UNITS_PER_REVIEW = 512
MIN_EXPLANATION = 28


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path) -> dict[str, Path]:
    reject_link_components(root, label="recycling source root")
    if not root.is_dir():
        return {}
    from .tx_retention import _safe_files
    return {path.relative_to(root).as_posix(): path for path in _safe_files(root)}


def _note(value: Any, field: str, *, minimum: int = MIN_EXPLANATION, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise WorkshopError("TX_RECYCLING_EXPLANATION_MISSING",
                            f"{field} must contain {minimum}-{maximum} characters of concrete explanation")
    return value.strip()


def trigger(engine: Any) -> int:
    raw = engine.config.raw.get("tx_recycle_trigger", 9)
    limit = engine.config.raw.get("tx_max_materialized", 10)
    if type(raw) is not int or type(limit) is not int or raw < 2 or raw > limit or limit > 1000:
        raise WorkshopError("TX_RECYCLING_THRESHOLD_INVALID",
                            "tx_recycle_trigger must be an integer from 2 through tx_max_materialized")
    return raw


def _state(root: Path) -> dict[str, Any]:
    try:
        raw = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkshopError("TX_RECYCLING_STATE_UNREADABLE",
                            f"TX lacks a readable state.json: {root.name}") from exc
    if not isinstance(raw, dict) or raw.get("transaction_id") != root.name:
        raise WorkshopError("TX_RECYCLING_STATE_ID_MISMATCH",
                            f"TX state identity does not match its directory: {root.name}")
    return raw


def pool_status(engine: Any, *, publish: bool = False) -> dict[str, Any]:
    from .tx_retention import materialized_worktrees
    roots = materialized_worktrees(engine)
    active_lease = None
    if engine.lease_path.is_file():
        try:
            active_lease = json.loads(engine.lease_path.read_text(encoding="utf-8")).get("transaction_id")
        except (OSError, ValueError, AttributeError):
            active_lease = "<unreadable>"
    rows = []
    for root in roots:
        try:
            state = _state(root)
            status = str(state.get("state", "UNKNOWN"))
            recycling = state.get("recycling") or {}
            stage = ("ACTIVE_OR_RECOVERY" if status not in TERMINAL else
                     "REVIEWED" if recycling.get("status") == "REVIEWED" else
                     "NEEDS_RECYCLING_ANALYSIS")
            row = {"transaction_id": root.name, "state": status, "stage": stage,
                   "created_at": state.get("created_at", ""),
                   "purpose": str(state.get("purpose") or "")[:180]}
        except (OSError, WorkshopError, ValueError) as exc:
            row = {"transaction_id": root.name, "stage": "UNREADABLE_NEEDS_RECOVERY",
                   "code": getattr(exc, "code", type(exc).__name__), "error": str(exc)[:200]}
        rows.append(row)
    # Deterministic candidate order, not hash-named directory order.
    rows.sort(key=lambda row: (row.get("created_at") or "9999", row["transaction_id"]))
    start = trigger(engine)
    enabled = engine.config.raw.get("tx_retention_required", False) is True
    result = {
        "schema": "werkfaden-tx-recycling-pool/v1",
        "enabled": enabled,
        "materialized_count": len(roots),
        "trigger_at": start,
        "target_max": start - 1,
        "required": enabled and len(roots) >= start,
        "need_to_retire": max(0, len(roots) - (start - 1)) if enabled else 0,
        "active_lease": active_lease,
        "rows": rows,
        "next_action": ("TX_RECYCLING_DISABLED" if not enabled else
                         "TX_RECYCLING_REQUIRED" if len(roots) >= start else "NORMAL_WORK_ALLOWED"),
    }
    if publish:
        result["frontier"] = publish_frontier(engine, result)
    return result


def publish_frontier(engine: Any, status: dict[str, Any]) -> dict[str, Any]:
    """Only a derived guidance snapshot. Engine admission rechecks live TX roots."""
    path = engine.config.kairos_workspace / ".kairos" / "tx_recycling_frontier.json"
    reject_link_components(path, label="recycling frontier")
    slim = {
        "schema": "werkfaden-tx-recycling-frontier/v1",
        "required": bool(status["required"]),
        "materialized_count": status["materialized_count"],
        "trigger_at": status["trigger_at"],
        "target_max": status["target_max"],
        "need_to_retire": status["need_to_retire"],
        "next_action": status["next_action"],
        "candidates": [row["transaction_id"] for row in status["rows"]
                       if row["stage"] in {"REVIEWED", "NEEDS_RECYCLING_ANALYSIS"}][:16],
        "blocked_count": sum(1 for row in status["rows"]
                             if row["stage"] in {"ACTIVE_OR_RECOVERY", "UNREADABLE_NEEDS_RECOVERY"}),
    }
    old = None
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if old != slim:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, slim)
    return {"path": str(path), "changed": old != slim}


def require_budget(engine: Any) -> dict[str, Any]:
    """The only normal-checkout capacity authority; no automatic eviction."""
    if engine.config.raw.get("tx_retention_required", False) is not True:
        return {"enabled": False}
    if engine.config.raw.get("transaction_documents_required", False) is not True:
        raise WorkshopError("TX_RETENTION_NEEDS_DOCUMENTS",
                            "recycling requires transaction_documents_required=true")
    result = pool_status(engine, publish=True)
    if result["required"]:
        raise WorkshopError("TX_RECYCLING_REQUIRED",
                            "materialized Workshop TX have reached the recycling threshold; review and retire existing transactions before checkout",
                            details={
                                "materialized": result["materialized_count"],
                                "target_max": result["target_max"],
                                "need_to_retire": result["need_to_retire"],
                                "candidates": result["frontier"],
                                "command": "tx-recycle-inspect / tx-recycle-submit / tx-recycle-drain",
                            })
    return result


def _preview(before: Path | None, after: Path | None) -> str:
    """Informative bounded diff only; never substitute it for preserved bytes."""
    try:
        if (before and before.stat().st_size > 128 * 1024) or (after and after.stat().st_size > 128 * 1024):
            return "(large file; inspect exact archived bytes)"
        a = before.read_text(encoding="utf-8").splitlines() if before else []
        b = after.read_text(encoding="utf-8").splitlines() if after else []
        text = "\n".join(difflib.unified_diff(a, b, fromfile="baseline", tofile="work", lineterm=""))
        return text[:1600] + ("\n[preview truncated]" if len(text) > 1600 else "")
    except (UnicodeError, OSError):
        return "(binary or unreadable text; compare recorded raw hashes)"


def inventory(engine: Any, transaction_id: str, *, persist: bool = True) -> dict[str, Any]:
    from .tx_retention import _checked_transaction_path
    _checked_transaction_path(engine, transaction_id)
    root, state = engine._load_transaction(transaction_id)
    if str(state.get("transaction_id")) != transaction_id:
        raise WorkshopError("TX_RECYCLING_STATE_ID_MISMATCH", "transaction state identity is invalid")
    work_root = root / "work"
    if not work_root.is_dir():
        raise WorkshopError("TX_RECYCLING_WORK_MISSING", "transaction has no materialized candidate to inspect")
    work = _files(work_root)
    baseline = _files(root / "baseline")
    framework_paths: set[str] = set()
    if "framework_projection" in state:
        from .corpus import _framework_scope
        scope = _framework_scope(root, state)
        expected = {"framework/" + row["path"]: row for row in scope["baseline_product"]}
        observed = {name for name in baseline if name.startswith("framework/")}
        if observed != set(expected) or any(
            _sha_file(baseline[name]) != row["sha256"]
            or baseline[name].stat().st_size != row["size"]
            for name, row in expected.items()
        ):
            raise WorkshopError("FRAMEWORK_RECYCLING_BASELINE_DRIFT",
                                "framework recycling requires its exact complete frozen baseline")
        framework_paths.update(expected)
        machine = {name.removeprefix("framework-machine/"): path for name, path in baseline.items()
                   if name.startswith("framework-machine/")}
        if set(machine) != set(scope["baseline_machine"]):
            raise WorkshopError("FRAMEWORK_RECYCLING_BASELINE_DRIFT",
                                "framework machine baseline membership changed")
        for name, digest in scope["baseline_machine"].items():
            preimage = machine[name]
            alias = "machine/" + name
            if (_sha_file(preimage) != digest
                    or (alias in baseline and _sha_file(baseline[alias]) != digest)):
                raise WorkshopError("FRAMEWORK_RECYCLING_BASELINE_DRIFT",
                                    "framework machine baseline changed or conflicts with another owner")
            baseline[alias] = preimage
    source_candidates = set(work) | framework_paths
    # Include missing Work files when the baseline had an ordinary source or
    # governed blueprint; exclude baseline-specific manifests and SQLite images.
    for relative in baseline:
        if relative in work or relative in {"manifest.json", "kairos.db"}:
            continue
        if relative.startswith(("runtime/", "blueprints/", "managed/", "documents/", "authority/")) or relative in state.get("sources", []) or relative in state.get("dependent_tests", []):
            source_candidates.add(relative)
    units: list[dict[str, Any]] = []
    for relative in sorted(source_candidates):
        wp, bp = work.get(relative), baseline.get(relative)
        before = _sha_file(bp) if bp else None
        after = _sha_file(wp) if wp else None
        if before == after and bp and wp:
            continue
        if wp is None:
            category = "deleted"
        elif bp is None:
            category = "baseline_unknown"
        else:
            category = "modified"
        uid = hashlib.sha256(_canonical({"tx": transaction_id, "path": relative,
                                         "before": before, "after": after})).hexdigest()[:32]
        units.append({
            "unit_id": "FILE_CHANGE_" + uid.upper(), "path": relative,
            "category": category, "baseline_sha256": before, "candidate_sha256": after,
            "candidate_size": wp.stat().st_size if wp else 0,
            "preview": _preview(bp, wp),
        })
    evidence = {
        "schema": INVENTORY_SCHEMA, "transaction_id": transaction_id,
        "tx_state": state.get("state"),
        "purpose": str(state.get("purpose") or ""),
        "baseline_package_sha256": str(state.get("baseline_package_sha256") or ""),
        "verified_work_package_sha256": str(state.get("verified_work_package_sha256") or ""),
        "units": units,
    }
    digest = sha256_bytes(_canonical(evidence))
    if persist:
        path = engine.config.state_directory / "tx-recycling" / "inventories" / f"{transaction_id}-{digest}.json"
        reject_link_components(path, label="recycling inventory")
        path.parent.mkdir(parents=True, exist_ok=True)
        reject_link_components(path, label="recycling inventory")
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(_canonical(evidence))
        if sha256_bytes(path.read_bytes()) != digest:
            raise WorkshopError("TX_RECYCLING_INVENTORY_CORRUPT", "recorded inventory bytes no longer match the inventory hash")
    else:
        path = None
    return {
        "transaction_id": transaction_id, "inventory_sha256": digest,
        "inventory_path": str(path) if path else None,
        "changed_unit_count": len(units),
        "candidate_previously_verified": bool(state.get("verified_work_package_sha256")),
        "tx_state": state.get("state"), "planned_purpose": evidence["purpose"],
        "units": units,
    }


CHUNK_SCHEMA = "werkfaden-tx-recycling-chunk/v1"
CHUNK_RECEIPT_SCHEMA = "werkfaden-tx-recycling-chunk-receipt/v1"
PAGED_SCHEMA = "werkfaden-tx-recycling-analysis/v2"
PROGRESS_SCHEMA = "werkfaden-tx-recycling-progress/v1"


def _page(manifest: dict[str, Any], chunk_index: int, bounds: list[int] | None = None) -> dict[str, Any]:
    """One deterministic half-open page; the global inventory remains authority."""
    units = manifest["units"]
    if type(chunk_index) is not int or chunk_index < 0:
        raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "chunk_index must be a nonnegative integer")
    start = chunk_index * MAX_UNITS_PER_REVIEW
    stop = min(start + MAX_UNITS_PER_REVIEW, len(units))
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != 2 or any(type(value) is not int for value in bounds):
            raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "range must contain two strict integer endpoints")
        start, stop = bounds
    if start < 0 or stop > len(units) or start >= stop or stop - start > MAX_UNITS_PER_REVIEW:
        raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "chunk_index is outside the exact inventory")
    identities = [row["unit_id"] for row in units[start:stop]]
    descriptor = {"inventory_sha256": manifest["inventory_sha256"],
                  "chunk_index": chunk_index, "range": [start, stop], "unit_ids": identities}
    return {**descriptor, "chunk_sha256": sha256_bytes(_canonical(descriptor))}


def _matches_page(value: dict[str, Any], descriptor: dict[str, Any]) -> bool:
    bounds = value.get("range")
    return (isinstance(bounds, list) and len(bounds) == 2
            and all(type(bound) is int for bound in bounds)
            and bounds == descriptor["range"]
            and value.get("chunk_sha256") == descriptor["chunk_sha256"])


def _chunk_path(root: Path, inventory_sha256: str, chunk_index: int) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", inventory_sha256) or type(chunk_index) is not int or chunk_index < 0:
        raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "invalid chunk receipt identity")
    path = root / "recycling-chunks" / inventory_sha256 / f"{chunk_index:08d}.json"
    reject_link_components(path, label="recycling chunk receipt")
    return path


def _progress_pages(root: Path, state: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    progress = state.get("recycling_progress")
    if progress is None:
        return []
    if (not isinstance(progress, dict) or progress.get("schema") != PROGRESS_SCHEMA
            or progress.get("inventory_sha256") != manifest["inventory_sha256"]
            or progress.get("global_unit_count") != len(manifest["units"])):
        raise WorkshopError("TX_RECYCLING_CHUNK_STALE", "partial review belongs to another inventory")
    unsigned = {key: value for key, value in progress.items() if key != "progress_sha256"}
    if progress.get("progress_sha256") != sha256_bytes(_canonical(unsigned)):
        raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "partial frontier hash is invalid")
    pages = progress.get("chunks")
    if not isinstance(pages, list):
        raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "partial frontier must contain chunk receipts")
    seen = set()
    for item in pages:
        if not isinstance(item, dict):
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "invalid partial chunk record")
        descriptor = _page(manifest, item.get("chunk_index"), item.get("range"))
        index = descriptor["chunk_index"]
        if index in seen or not _matches_page(item, descriptor):
            raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "partial pages overlap or have invalid membership")
        seen.add(index)
        path = _chunk_path(root, manifest["inventory_sha256"], index)
        try:
            raw = path.read_bytes()
            packet = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "immutable chunk receipt unavailable") from exc
        if (path.stat().st_nlink != 1 or raw != _canonical(packet)
                or sha256_bytes(raw) != item.get("receipt_sha256")
                or packet.get("schema") != CHUNK_RECEIPT_SCHEMA
                or packet.get("transaction_id") != state["transaction_id"]
                or packet.get("inventory_sha256") != manifest["inventory_sha256"]
                or any(packet.get(key) != descriptor[key] for key in ("chunk_index", "range", "chunk_sha256"))
                or packet.get("decisions") != item.get("decisions")):
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "partial source differs from its exact immutable receipt")
        if (not isinstance(item.get("decisions"), list)
                or any(not isinstance(row, dict) for row in item["decisions"])
                or [row.get("unit_id") for row in item["decisions"]] != descriptor["unit_ids"]):
            raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "chunk receipt must cover its exact ordered IDs")
    pages = sorted(pages, key=lambda item: item["chunk_index"])
    stop = 0
    for index, item in enumerate(pages):
        if item["chunk_index"] != index or item["range"][0] != stop:
            raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "partial progress must be a contiguous ordered prefix")
        stop = item["range"][1]
    return pages


def _verify_paged_analysis(engine: Any, root: Path, state: dict[str, Any],
                           manifest: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    if (proposal.get("transaction_id") != state["transaction_id"]
            or proposal.get("inventory_sha256") != manifest["inventory_sha256"]):
        raise WorkshopError("TX_RECYCLING_INVENTORY_STALE", "paged completion is not bound to this inventory")
    for key in ("planned_approach", "attempted_approach", "outcome_reason"):
        _note(proposal.get(key), key, minimum=30, maximum=2000)
    pages = _progress_pages(root, state, manifest)
    if not pages or pages[-1]["range"][1] != len(manifest["units"]):
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "completion requires every page exactly once")
    refs = [{"chunk_index": item["chunk_index"], "range": item["range"],
             "chunk_sha256": item["chunk_sha256"], "receipt_sha256": item["receipt_sha256"]} for item in pages]
    if proposal.get("chunks") != refs or "decisions" in proposal:
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "completion must name the exact ordered immutable page receipts")
    recorded = []
    for item in pages:
        start, stop = item["range"]
        bounded = {**proposal, "schema": SCHEMA, "decisions": item["decisions"]}
        _verify_analysis(engine, root, state, {**manifest, "units": manifest["units"][start:stop]}, bounded)
        # Preserve what the immutable partial receipt actually observed. The
        # recheck above grants no stronger claim than the existing validator.
        recorded.extend(item["decisions"])
    if [row["unit_id"] for row in recorded] != [row["unit_id"] for row in manifest["units"]]:
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "global File-Change coverage is incomplete or overlapping")
    return recorded


def _submit_chunk_locked(engine: Any, root: Path, state: dict[str, Any],
                          manifest: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    """Called only inside submit's native terminal/lease/retention barriers."""
    from .tx_documents import assert_projected
    descriptor = _page(manifest, proposal.get("chunk_index"), proposal.get("range"))
    if (proposal.get("transaction_id") != state["transaction_id"]
            or proposal.get("inventory_sha256") != manifest["inventory_sha256"]
            or not _matches_page(proposal, descriptor)):
        raise WorkshopError("TX_RECYCLING_CHUNK_STALE", "chunk proposal does not bind its exact inventory/range/hash")
    prior = _canonical(state)
    pages = _progress_pages(root, state, manifest)
    if descriptor["chunk_index"] > len(pages):
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "submit a contiguous prefix before the next chunk")
    if descriptor["chunk_index"] == len(pages) and descriptor["range"][0] != (pages[-1]["range"][1] if pages else 0):
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "the next range must start at the exact current frontier")
    start, stop = descriptor["range"]
    bounded = {**proposal, "schema": SCHEMA}
    decisions = _verify_analysis(engine, root, state,
                                 {**manifest, "units": manifest["units"][start:stop]}, bounded)
    if [row["unit_id"] for row in decisions] != descriptor["unit_ids"]:
        raise WorkshopError("TX_RECYCLING_CHUNK_COVERAGE", "chunk decisions must retain exact inventory order")
    proposal_sha = sha256_bytes(_canonical(proposal))
    accepted = next((row for row in pages if row["chunk_index"] == descriptor["chunk_index"]), None)
    path = _chunk_path(root, manifest["inventory_sha256"], descriptor["chunk_index"])
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_link_components(path, label="recycling chunk receipt")
    if path.exists():
        try:
            raw = path.read_bytes()
            packet = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "stored chunk receipt is unreadable") from exc
        if (path.stat().st_nlink != 1 or raw != _canonical(packet)
                or packet.get("schema") != CHUNK_RECEIPT_SCHEMA
                or packet.get("transaction_id") != state["transaction_id"]
                or packet.get("inventory_sha256") != manifest["inventory_sha256"]
                or type(packet.get("chunk_index")) is not int
                or packet["chunk_index"] != descriptor["chunk_index"]
                or not _matches_page(packet, descriptor)
                or packet.get("proposal_sha256") != proposal_sha):
            raise WorkshopError("TX_RECYCLING_CHUNK_CONFLICT", "an immutable chunk cannot be overwritten")
        if accepted is None:
            def observed(values: Any) -> Any:
                if isinstance(values, list):
                    return [observed(value) for value in values]
                if isinstance(values, dict):
                    return {key: observed(value) for key, value in values.items() if key != "observed_at"}
                return values
            if observed(packet.get("decisions")) != observed(decisions):
                raise WorkshopError("TX_RECYCLING_CHUNK_CONFLICT", "unacknowledged receipt differs from freshly verified decisions")
    else:
        engine._import_kairos()
        from kairos.frontmatter import split_frontmatter
        from kairos.transaction_document import document_path
        from kairos.workspace import load_config
        from .tx_documents import _sync_scope, load_scope_transition
        workspace = engine.config.kairos_workspace
        workspace_id = str(load_config(workspace)["workspace_id"])
        old = split_frontmatter((workspace / document_path(state["transaction_id"])).read_text(encoding="utf-8")).metadata
        goal, milestone, task, loop = _sync_scope(workspace, old, state, expected_workspace_id=workspace_id,
                                                  history_control=load_scope_transition(engine, state))
        if not all((goal, milestone, task)) or type(loop) is not int:
            raise WorkshopError("TX_RECYCLING_CHUNK_SCOPE_UNRESOLVED", "bind the original historical task scope through native intake before creating report fragments")
        packet = {"schema": CHUNK_RECEIPT_SCHEMA, "transaction_id": state["transaction_id"],
                  **{key: descriptor[key] for key in ("inventory_sha256", "chunk_index", "range", "chunk_sha256")},
                  "proposal_sha256": proposal_sha, "decisions": decisions, "created_at": utc_now(),
                  "scope": {"goal": goal, "milestone": milestone, "task": task, "loop": loop}}
        raw = _canonical(packet)
    engine._import_kairos()
    from kairos.transaction_document import render_recycling_chunk
    from kairos.constants import MAX_DOCUMENT_BYTES
    from kairos.workspace import load_config
    text = render_recycling_chunk(transaction_id=state["transaction_id"],
                                 workspace_id=str(load_config(engine.config.kairos_workspace)["workspace_id"]),
                                 packet=packet, receipt_sha256=sha256_bytes(raw))
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise WorkshopError("TX_RECYCLING_CHUNK_DOCUMENT_LIMIT", "reduce --chunk-size; this source fragment exceeds the unchanged document byte budget")
    if not path.exists():
        with path.open("xb") as output:
            output.write(raw)
        if path.read_bytes() != raw:
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "chunk receipt did not persist exactly")
    item = {key: packet[key] for key in ("chunk_index", "range", "chunk_sha256", "decisions")}
    item["receipt_sha256"] = sha256_bytes(raw)
    item["created_at"] = packet["created_at"]
    item["document_sha256"] = sha256_bytes(text.encode("utf-8"))
    existing = next((row for row in pages if row["chunk_index"] == item["chunk_index"]), None)
    if existing is not None:
        if existing != item:
            raise WorkshopError("TX_RECYCLING_CHUNK_CONFLICT", "accepted chunk differs from its immutable receipt")
        assert_projected(engine, state["transaction_id"])
        return {"transaction_id": state["transaction_id"], "status": "CHUNK_ALREADY_RECORDED",
                **{key: item[key] for key in ("chunk_index", "range", "chunk_sha256", "receipt_sha256")}}
    current = inventory(engine, state["transaction_id"], persist=False)
    if current["inventory_sha256"] != manifest["inventory_sha256"] or _canonical(engine._load_transaction(state["transaction_id"])[1]) != prior:
        raise WorkshopError("TX_RECYCLING_CANDIDATE_CHANGED", "inventory/state changed while recording the chunk")
    pages.append(item)
    pages.sort(key=lambda row: row["chunk_index"])
    progress = {"schema": PROGRESS_SCHEMA, "inventory_sha256": manifest["inventory_sha256"],
                "global_unit_count": len(manifest["units"]), "chunks": pages}
    progress["progress_sha256"] = sha256_bytes(_canonical(progress))
    state["recycling_progress"] = progress
    state["document_projection_required"] = True
    engine._save_terminal_history(root, state, event="TX_NOTE",
                                  details={"recycling_chunk_index": item["chunk_index"],
                                           "receipt_sha256": item["receipt_sha256"], "status": "PARTIAL"})
    assert_projected(engine, state["transaction_id"])
    return {"transaction_id": state["transaction_id"], "status": "CHUNK_RECORDED",
            "recorded_chunks": len(pages), "reviewed": False,
            **{key: item[key] for key in ("chunk_index", "range", "chunk_sha256", "receipt_sha256")}}


def _inspect_page(engine: Any, transaction_id: str, data: dict[str, Any],
                  chunk_index: int | None, chunk_size: int) -> dict[str, Any]:
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_UNITS_PER_REVIEW:
        raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "chunk_size must be an integer from1 through512")
    root, state = engine._load_transaction(transaction_id)
    pages = _progress_pages(root, state, data)
    template = {"schema": PAGED_SCHEMA, "transaction_id": transaction_id,
                "inventory_sha256": data["inventory_sha256"],
                "planned_approach": data["planned_purpose"], "attempted_approach": "",
                "outcome_reason": "", "no_changes_reason": "",
                "chunks": [{key: row[key] for key in ("chunk_index", "range", "chunk_sha256", "receipt_sha256")} for row in pages]}
    suffix = "complete-" + sha256_bytes(_canonical(template["chunks"]))
    selected = []
    if chunk_index is not None:
        if type(chunk_index) is not int or chunk_index < 0 or chunk_index > len(pages):
            raise WorkshopError("TX_RECYCLING_CHUNK_RANGE", "inspect an existing chunk or the immediate next chunk")
        start = pages[-1]["range"][1] if pages else 0
        bounds = pages[chunk_index]["range"] if chunk_index < len(pages) else [start, min(start + chunk_size, len(data["units"]))]
        descriptor = _page(data, chunk_index, bounds)
        start, stop = descriptor["range"]
        selected = data["units"][start:stop]
        template.pop("chunks")
        template.update(schema=CHUNK_SCHEMA, **{key: descriptor[key] for key in ("chunk_index", "range", "chunk_sha256")},
                        decisions=[{"unit_id": row["unit_id"], "disposition": "", "reason": "", "evidence_refs": []} for row in selected])
        suffix = f"chunk-{chunk_index:08d}-{start:08d}-{stop:08d}"
    path = engine.config.state_directory / "tx-recycling" / "proposals" / f"{transaction_id}-{data['inventory_sha256']}-{suffix}.json"
    reject_link_components(path, label="recycling page proposal")
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_link_components(path, label="recycling page proposal")
    if not path.exists():
        with path.open("xb") as output:
            output.write(_canonical(template))
    return {**{key: value for key, value in data.items() if key != "units"},
            "units": selected, "proposal_path": str(path), "chunk_index": chunk_index,
            "remaining_unit_count": len(data["units"]) - (pages[-1]["range"][1] if pages else 0),
            "recorded_chunks": len(pages),
            "coverage_complete": bool(pages) and pages[-1]["range"][1] == len(data["units"]),
            "instructions": "Review each bounded chunk, submit it as PARTIAL, then inspect again without chunk_index and submit the exact complete receipt manifest. Partial work is not recycling clearance."}


def inspect(engine: Any, transaction_id: str, *, chunk_index: int | None = None, chunk_size: int = MAX_UNITS_PER_REVIEW) -> dict[str, Any]:
    """Generate a human/agent-editable proposal without guessing dispositions."""
    data = inventory(engine, transaction_id, persist=True)
    _, state = engine._load_transaction(transaction_id)
    if (chunk_index is not None or data["changed_unit_count"] > MAX_UNITS_PER_REVIEW
            or state.get("recycling_progress") or (state.get("recycling") or {}).get("chunks")):
        return _inspect_page(engine, transaction_id, data, chunk_index, chunk_size)
    template = {
        "schema": SCHEMA,
        "transaction_id": transaction_id,
        "inventory_sha256": data["inventory_sha256"],
        "planned_approach": data["planned_purpose"],
        "attempted_approach": "",
        "outcome_reason": "",
        "no_changes_reason": "" if data["changed_unit_count"] else "",
        "decisions": [
            {
                "unit_id": item["unit_id"],
                "disposition": "",
                "reason": "",
                "evidence_refs": [],
            } for item in data["units"]
        ],
    }
    path = (engine.config.state_directory / "tx-recycling" / "proposals"
            / f"{transaction_id}-{data['inventory_sha256']}.json")
    reject_link_components(path, label="recycling proposal")
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_link_components(path, label="recycling proposal")
    if not path.exists():
        with path.open("xb") as out:
            out.write(json.dumps(template, ensure_ascii=False, sort_keys=True,
                                 indent=2).encode("utf-8") + b"\n")
    return {**data, "proposal_path": str(path),
            "instructions": "Fill the concrete planned/attempted approaches and outcome rationale. For each changed file choose a disposition, explain why, and cite an existing TX: or KAIROS: evidence path. Neither the TX status nor an LLM assertion alone proves usefulness."}


def _evidence_exists(engine: Any, root: Path, locator: str) -> bool:
    if not isinstance(locator, str) or ":" not in locator or len(locator) > 500:
        return False
    scope, rel = locator.split(":", 1)
    p = Path(rel.split("#", 1)[0].replace("\\", "/"))
    if p.is_absolute() or p.drive or ".." in p.parts or not rel or not p.parts:
        return False
    base = root if scope == "TX" else engine.config.kairos_workspace if scope == "KAIROS" else None
    if base is None:
        return False
    full = base / p
    reject_link_components(full, label="recycling evidence")
    try:
        if not full.is_file() or not full.resolve().is_relative_to(base.resolve()):
            return False
        return True
    except (OSError, ValueError):
        return False


def _ordinary_postcheck_proven(engine: Any, state: dict[str, Any]) -> bool:
    """Recognize only an ordinary TX with an exact, committed native postcheck."""
    if state.get("schema") != "runtime-sync-transaction/v1" or state.get("state") != "POSTCHECK_VERIFIED":
        return False
    tx = state.get("transaction_id")
    work = state.get("verified_work_package_sha256")
    postcheck = state.get("postcheck_package_sha256")
    if (not isinstance(tx, str) or not re.fullmatch(r"TXN_[0-9a-f]{24}", tx)
            or not isinstance(work, str) or not re.fullmatch(r"[0-9a-f]{64}", work)
            or not isinstance(postcheck, str) or not re.fullmatch(r"[0-9a-f]{64}", postcheck)):
        return False
    entry = state.get("terminal_receipt")
    if not isinstance(entry, dict):
        return False
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return False
    heartbeat = payload.get("heartbeat")
    if (entry.get("schema") != "werkfaden-tx-terminal-receipt/v1"
            or entry.get("status") != "COMMITTED" or entry.get("kind") != "postcheck"
            or entry.get("transaction_id") != tx
            or payload.get("schema") != "runtime-sync-postcheck/v1"
            or payload.get("transaction_id") != tx or payload.get("state") != "POSTCHECK_VERIFIED"
            or payload.get("work_package_sha256") != work
            or payload.get("postcheck_package_sha256") != postcheck
            or payload.get("bit_exact") is not True
            or not isinstance(heartbeat, dict) or heartbeat.get("verified") is not True):
        return False
    event_id = entry.get("postcheck_event_id")
    if not isinstance(event_id, str) or not re.fullmatch(r"TXE_[0-9A-F]{32}", event_id):
        return False
    try:
        payload_sha = sha256_bytes(_canonical(payload))
        if entry.get("payload_sha256") != payload_sha:
            return False
        identity = {"kind": "postcheck", "transaction_id": tx,
                    "postcheck_event_id": event_id, "payload_sha256": payload_sha}
        receipt_id = "POSTCHECK_" + sha256_bytes(_canonical(identity))[:32]
        if entry.get("receipt_id") != receipt_id or entry.get("path") != "receipts/" + receipt_id + ".json":
            return False
        destination = engine.config.state_directory / "receipts" / (receipt_id + ".json")
        reject_link_components(destination, label="recycling postcheck receipt")
        if not destination.is_file() or destination.stat().st_nlink != 1:
            return False
        record = {**payload, "receipt_id": receipt_id,
                  "terminal_receipt_schema": entry["schema"], "terminal_event_id": event_id,
                  "terminal_payload_sha256": payload_sha}
        expected = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return destination.read_bytes() == expected
    except (OSError, ValueError, TypeError, WorkshopError):
        return False


def _verify_analysis(engine: Any, root: Path, state: dict[str, Any],
                     manifest: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    if proposal.get("schema") == PAGED_SCHEMA:
        return _verify_paged_analysis(engine, root, state, manifest, proposal)
    if proposal.get("schema") != SCHEMA or proposal.get("transaction_id") != state["transaction_id"]:
        raise WorkshopError("TX_RECYCLING_ANALYSIS_SCHEMA", "recycling analysis schema or TX identity is invalid")
    if proposal.get("inventory_sha256") != manifest["inventory_sha256"]:
        raise WorkshopError("TX_RECYCLING_INVENTORY_STALE", "analysis is not bound to current exact changed file inventory")
    for key in ("planned_approach", "attempted_approach", "outcome_reason"):
        _note(proposal.get(key), key, minimum=30, maximum=2000)
    rows = proposal.get("decisions")
    if not isinstance(rows, list) or len(rows) > MAX_UNITS_PER_REVIEW:
        raise WorkshopError("TX_RECYCLING_DECISION_LIMIT", f"decisions must be an array of no more than {MAX_UNITS_PER_REVIEW} file-level atomics")
    expected = {item["unit_id"]: item for item in manifest["units"]}
    if len(expected) != len(rows):
        raise WorkshopError("TX_RECYCLING_COVERAGE", "every changed file must receive exactly one explicit decision")
    recorded: list[dict[str, Any]] = []
    seen: set[str] = set()
    evidence_cache: dict[str, dict[str, str]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise WorkshopError("TX_RECYCLING_DECISION_SCHEMA", "each disposition must be an object")
        uid = raw.get("unit_id")
        if uid not in expected or uid in seen:
            raise WorkshopError("TX_RECYCLING_COVERAGE", "unknown or repeated atomic identity in recycling analysis")
        seen.add(uid)
        atom = expected[uid]
        disp = raw.get("disposition")
        if disp not in DISPOSITIONS:
            raise WorkshopError("TX_RECYCLING_DECISION_SCHEMA", "unsupported atomic disposition")
        reason = _note(raw.get("reason"), f"decision {uid}", minimum=30, maximum=2000)
        refs = raw.get("evidence_refs")
        if not isinstance(refs, list) or not refs or len(refs) > 8 or any(not _evidence_exists(engine, root, ref) for ref in refs):
            raise WorkshopError("TX_RECYCLING_EVIDENCE_INVALID",
                                f"all decisions need bounded, existing TX: or KAIROS: evidence paths: {uid}")
        # A shadow-verified candidate remains protected unless an ordinary
        # postcheck is bound to its committed, byte-exact native receipt.
        if (state.get("verified_work_package_sha256") and disp != "RETAIN_FOR_REUSE"
                and not _ordinary_postcheck_proven(engine, state)):
            raise WorkshopError("TX_RECYCLING_PROTECTED_CANDIDATE",
                                "a verified candidate without proven committed postcheck requires RETAIN_FOR_REUSE")
        if atom["category"] == "baseline_unknown" and disp == "REJECT_FOR_REUSE":
            raise WorkshopError("TX_RECYCLING_UNPROVEN_BASELINE",
                                "unknown-baseline bytes require retention until recovered or superseded with proof")
        for locator in refs:
            if locator not in evidence_cache:
                scope, relative = locator.split(":", 1)
                evidence_path = ((root if scope == "TX" else engine.config.kairos_workspace)
                                 / relative.split("#", 1)[0].replace("\\", "/"))
                evidence_cache[locator] = {
                    "locator": locator,
                    "content_sha256": _sha_file(evidence_path),
                    "verification_kind": "EXISTENCE_AND_HASH_ONLY",
                    "observed_at": utc_now(),
                }
        recorded.append({
            "unit_id": uid, "path": atom["path"], "category": atom["category"],
            "baseline_sha256": atom["baseline_sha256"],
            "candidate_sha256": atom["candidate_sha256"],
            "disposition": disp, "reason": reason, "evidence_refs": refs,
            "evidence_proofs": [evidence_cache[locator] for locator in refs],
            "claim_level": "DECLARED_REASON",
            "source_transaction": str(raw.get("source_transaction") or ""),
            "successor_transaction": str(raw.get("successor_transaction") or ""),
            "protected": disp != "REJECT_FOR_REUSE",
        })
    if not expected:
        _note(proposal.get("no_changes_reason"), "no_changes_reason", minimum=30, maximum=2000)
    return recorded


def _review_intent_path(root: Path) -> Path:
    path = root / "recycling-review-intent.json"
    reject_link_components(path, label="recycling review intent")
    return path


def assert_no_review_intent(engine: Any, transaction_id: str | None = None) -> None:
    """Gate unresolved review intents globally or for one exact history owner."""
    from .tx_documents import PENDING_OUTCOME
    directory = engine.config.transaction_directory
    reject_link_components(directory, label="transaction intent pool")
    try:
        pool_stat = directory.stat()
        if not stat.S_ISDIR(pool_stat.st_mode):
            raise OSError("transaction intent pool is not a directory")
        if transaction_id is None:
            roots = sorted(directory.iterdir())
        else:
            from .tx_retention import _checked_transaction_path
            _checked_transaction_path(engine, transaction_id)
            roots = [directory / transaction_id]
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkshopError("TX_RECYCLING_INTENT_SCAN_FAILED", "cannot inspect the transaction intent pool", details=str(exc)) from exc
    for root in roots:
        reject_link_components(root, label="transaction intent pool entry")
        try:
            if not stat.S_ISDIR(root.stat().st_mode):
                continue
            path = _review_intent_path(root)
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WorkshopError("TX_RECYCLING_INTENT_SCAN_FAILED", "cannot inspect a transaction review intent", details=str(exc)) from exc
        observation: dict[str, Any] = {"transaction_id": root.name, "intent": str(path), "status": "UNVERIFIED"}
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > 32 * 1024 * 1024:
                raise ValueError("intent is not a bounded regular file")
            with path.open("rb") as handle:
                raw = handle.read(32 * 1024 * 1024 + 1)
            after = path.stat()
            if (len(raw) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ValueError("intent changed during inspection")
            intent = json.loads(raw)
            target = intent["target_state"]
            event_id = target["tx_event_id"]
            sequence = target["tx_event_sequence"]
            if (intent.get("schema") != "werkfaden-tx-review-intent/v1" or intent.get("transaction_id") != root.name
                    or target.get("transaction_id") != root.name or not isinstance(event_id, str)
                    or re.fullmatch(r"TXE_[0-9A-F]{32}", event_id) is None
                    or type(sequence) is not int or sequence < 1
                    or intent.get("target_state_sha256") != sha256_bytes(_canonical(target))):
                raise ValueError("intent event identity is invalid")
            observation.update(status="OBSERVED", event_id=event_id, event_sequence=sequence,
                               intent_sha256=sha256_bytes(raw))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass  # An unreadable or contradictory obligation still blocks checkout.
        raise WorkshopError(
            "TX_RECYCLING_INTENT_PENDING", "resume the original tx-recycle-submit for the referenced transaction before a conflicting native action",
            details=observation, outcome=PENDING_OUTCOME,
            references={"total": 1, "total_exact": False, "omitted": 0, "omitted_exact": False, "items": [observation]},
        )


def _persist_review_intent(root: Path, prior: dict[str, Any], target: dict[str, Any],
                           proposal_sha256: str) -> None:
    path = _review_intent_path(root)
    if path.exists():
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "an unresolved recycling review intent already exists")
    intent = {
        "schema": "werkfaden-tx-review-intent/v1",
        "transaction_id": str(prior.get("transaction_id") or ""),
        "proposal_sha256": proposal_sha256,
        "prior_state_sha256": sha256_bytes(_canonical(prior)),
        "target_state_sha256": sha256_bytes(_canonical(target)),
        "prior_state": prior,
        "target_state": target,
    }
    atomic_write_json(path, intent)
    if json.loads(path.read_text(encoding="utf-8")) != intent:
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "recycling review intent did not persist exactly")


def _remove_review_intent(path: Path, expected: bytes) -> None:
    reject_link_components(path, label="recycling review intent")
    if not path.is_file() or path.read_bytes() != expected:
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "recycling review intent changed before acknowledgement")
    path.unlink()


def _recover_review_intent(engine: Any, root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Replay the exact reviewed event after a precommit or publication interruption."""
    from .tx_documents import assert_projected, pending_path, pending_record, update_document
    from .tx_lifecycle import PHASES, SOURCE_EPOCH_KIND, source_epoch
    from .tx_retention import _object_path, vault_dir

    path = _review_intent_path(root)
    try:
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("missing or oversized intent")
        raw = path.read_bytes()
        intent = json.loads(raw)
        prior, target = intent["prior_state"], intent["target_state"]
        tx = intent["transaction_id"]
        if (intent.get("schema") != "werkfaden-tx-review-intent/v1"
                or tx != root.name or not isinstance(prior, dict) or not isinstance(target, dict)
                or prior.get("transaction_id") != tx or target.get("transaction_id") != tx
                or intent.get("prior_state_sha256") != sha256_bytes(_canonical(prior))
                or intent.get("target_state_sha256") != sha256_bytes(_canonical(target))
                or intent.get("proposal_sha256") != sha256_bytes(_canonical(proposal))):
            raise ValueError("intent identity, state or proposal hash mismatch")
        if prior.get("state") not in TERMINAL or target.get("state") != prior.get("state"):
            raise ValueError("intent is not bound to an unchanged terminal state")
        # Review is a mandatory native publication event. Only its exact
        # monotone boolean latch transition belongs to this owner.
        if (type(prior.get("document_projection_required", False)) is not bool
                or target.get("document_projection_required") is not True):
            raise ValueError("review documentation latch must become or remain true")
        allowed = {"updated_at", "journal", "recycling", "tx_event", "tx_event_id", "tx_event_sequence", "tx_event_history", "document_projection_required"}
        if ({k: v for k, v in prior.items() if k not in allowed}
                != {k: v for k, v in target.items() if k not in allowed}):
            raise ValueError("intent changes unrelated transaction state")
        review = target["recycling"]
        if not isinstance(review, dict) or review.get("status") != "REVIEWED":
            raise ValueError("intent has no reviewed decision")
        if review.get("proposal_sha256") != intent["proposal_sha256"]:
            raise ValueError("review proposal identity differs")
        before_journal = prior.get("journal", [])
        expected_entry = {"event": "TX_RECYCLE_REVIEWED", "at": target.get("updated_at"),
                          "details": {"analysis_sha256": review.get("analysis_sha256"),
                                      "inventory_sha256": review.get("inventory_sha256")}}
        if not isinstance(before_journal, list) or target.get("journal") != [*before_journal, expected_entry]:
            raise ValueError("review journal is not an exact append")
        from .tx_lifecycle import validate_event_chain
        validate_event_chain(target)
        event = target["tx_event"]
        if not isinstance(event, dict):
            raise ValueError("review event is missing")
        event_payload = {k: v for k, v in event.items() if k != "event_id"}
        event_id = "TXE_" + hashlib.sha256(json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32].upper()
        if (event.get("schema") != "werkfaden-tx-event/v1" or event.get("event") != "TX_RECYCLE_REVIEWED"
                or event.get("transaction_id") != tx or event.get("event_id") != event_id
                or target.get("tx_event_id") != event_id
                or event.get("sequence") != int(prior.get("tx_event_sequence", 0)) + 1
                or target.get("tx_event_sequence") != event.get("sequence")
                or event.get("previous_event_id") != str(prior.get("tx_event_id") or "")
                or event.get("state_before") != str(prior.get("state") or "")
                or event.get("state_after") != str(target.get("state") or "")
                or event.get("phase") != PHASES[target["state"]]
                or event.get("tx_kind") != str(target.get("transaction_kind") or target.get("schema") or "normal")
                or event.get("source_epoch") != source_epoch(target)
                or event.get("source_epoch_kind") != SOURCE_EPOCH_KIND
                or event.get("candidate_sha256") != str(target.get("verified_work_package_sha256") or target.get("prepared_work_package_sha256") or "")
                or event.get("baseline_sha256") != str(target.get("baseline_package_sha256") or "")
                or event.get("scope_receipt_sha256") != str((target.get("historical_scope") or {}).get("receipt_sha256")
                                                            or (target.get("original_scope") or {}).get("source_receipt_sha256") or "")
                or not isinstance(event.get("occurred_at"), str) or not event["occurred_at"]
                or event.get("required_projection") is not True):
            raise ValueError("review event does not match its exact source state")
        current = inventory(engine, tx, persist=False)
        if current["inventory_sha256"] != review.get("inventory_sha256"):
            raise ValueError("reviewed inventory changed")
        analysis_sha = str(review.get("analysis_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", analysis_sha):
            raise ValueError("invalid review digest")
        review_path = Path(str(review.get("review_path") or ""))
        expected_review = engine.config.state_directory / "tx-recycling" / "reviews" / f"{tx}-{analysis_sha}.json"
        reject_link_components(review_path, label="recycling review")
        if (review_path.resolve() != expected_review.resolve() or not review_path.is_file()
                or _sha_file(review_path) != analysis_sha):
            raise ValueError("immutable review is missing or changed")
        persisted = json.loads(review_path.read_text(encoding="utf-8"))
        if (not isinstance(persisted, dict) or persisted.get("schema") != REVIEW_SCHEMA
                or persisted.get("transaction_id") != tx
                or any(review.get(k) != v for k, v in persisted.items())):
            raise ValueError("review state differs from its immutable receipt")
        vault = vault_dir(engine)
        manifest_path = Path(str(review.get("vault_manifest") or ""))
        reject_link_components(manifest_path, label="recycling vault manifest")
        reject_link_components(vault / "manifests", label="recycling vault manifest directory")
        if (manifest_path.resolve().parent != (vault / "manifests").resolve()
                or not manifest_path.is_file() or _sha_file(manifest_path) != review.get("vault_manifest_sha256")):
            raise ValueError("review vault manifest is missing or changed")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("transaction_id") != tx or manifest.get("complete") is not True or not isinstance(manifest.get("files"), dict):
            raise ValueError("review vault manifest is incomplete")
        for record in manifest["files"].values():
            digest = str(record.get("sha256", ""))
            obj = _object_path(vault, digest)
            reject_link_components(obj, label="recycling vault object")
            if not obj.is_file() or obj.stat().st_size != record.get("size") or _sha_file(obj) != digest:
                raise ValueError("review vault object is missing or changed")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        raise WorkshopError("TX_RECYCLING_INTENT_INVALID", "recycling review intent or evidence is invalid", details=str(exc)) from exc

    actual_root, actual = engine._load_transaction(tx)
    if actual_root.resolve() != root.resolve():
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "review intent points to another transaction root")
    actual_sha = sha256_bytes(_canonical(actual))
    prior_sha, target_sha = intent["prior_state_sha256"], intent["target_state_sha256"]
    if actual_sha not in {prior_sha, target_sha}:
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "transaction state differs from both intent endpoints")

    marker = pending_path(engine, tx)
    reject_link_components(marker, label="recycling review outbox")
    expected_marker = pending_record(target, event="TX_RECYCLE_REVIEWED",
                                     reason="lifecycle event saved to outbox before state commit")
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "existing pending marker is unreadable") from exc
        keys = ("schema", "transaction_id", "event", "event_id", "event_sequence", "state",
                "candidate_sha256", "baseline_sha256", "scope_receipt_sha256", "pending")
        if not isinstance(existing, dict) or any(existing.get(key) != expected_marker.get(key) for key in keys):
            raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "pending marker does not match the intended review event")
    elif actual_sha == target_sha:
        try:
            assert_projected(engine, tx)
        except WorkshopError:
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(marker, expected_marker)
        else:
            _remove_review_intent(path, raw)
            return {"transaction_id": tx, "status": "ALREADY_REVIEWED",
                    "analysis_sha256": review["analysis_sha256"]}
    else:
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, expected_marker)
    if actual_sha == prior_sha:
        atomic_write_json(root / "state.json", target)
    update_document(engine, target, event="TX_RECYCLE_REVIEWED")
    assert_projected(engine, tx)
    _, confirmed = engine._load_transaction(tx)
    if sha256_bytes(_canonical(confirmed)) != target_sha:
        raise WorkshopError("TX_RECYCLING_INTENT_CONFLICT", "transaction changed before review acknowledgement")
    _remove_review_intent(path, raw)
    return {"transaction_id": tx, "status": "REVIEWED" if actual_sha == prior_sha else "ALREADY_REVIEWED",
            "inventory_sha256": review["inventory_sha256"], "analysis_sha256": review["analysis_sha256"],
            "unit_count": len(review["decisions"]), "vault_manifest": review["vault_manifest"]}


def submit(engine: Any, transaction_id: str, analysis_path: Path) -> dict[str, Any]:
    from .tx_retention import _retention_lock, snapshot
    from .tx_documents import update_document, assert_projected
    with _retention_lock(engine, allow_reentry=False):
        if engine.lease_path.exists():
            raise WorkshopError("TX_RECYCLING_LEASE_ACTIVE", "recycle only after the current Workshop lease is released")
        from .tx_lifecycle import assert_no_event_intents
        assert_no_event_intents(engine, transaction_id)
        root, state = engine._load_transaction(transaction_id)
        if state.get("state") not in TERMINAL:
            raise WorkshopError("TX_RECYCLING_TX_NOT_TERMINAL",
                                "technical abort/apply/recovery must finish before recycling decision")
        reject_link_components(Path(analysis_path), label="recycling analysis")
        proposal = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
        if not isinstance(proposal, dict):
            raise WorkshopError("TX_RECYCLING_ANALYSIS_SCHEMA", "analysis file must contain a JSON object")
        intent_path = _review_intent_path(root)
        if intent_path.exists():
            return _recover_review_intent(engine, root, proposal)
        assert_projected(engine, transaction_id)
        prior_state = json.loads(json.dumps(state))
        manifest = inventory(engine, transaction_id, persist=True)
        existing = state.get("recycling") or {}
        if existing.get("status") == "REVIEWED":
            # Replay is harmless; conflicting claims must be rejected explicitly.
            old_digest = existing.get("proposal_sha256")
            if old_digest != sha256_bytes(_canonical(proposal)):
                raise WorkshopError("TX_RECYCLING_ALREADY_REVIEWED", "an existing recycling decision cannot be overwritten")
            assert_projected(engine, transaction_id)
            return {"transaction_id": transaction_id, "status": "ALREADY_REVIEWED",
                    "analysis_sha256": existing.get("analysis_sha256")}
        if proposal.get("schema") == CHUNK_SCHEMA:
            return _submit_chunk_locked(engine, root, state, manifest, proposal)
        decisions = _verify_analysis(engine, root, state, manifest, proposal)
        vault = snapshot(engine, transaction_id, include_entire_tx=True)
        # Recheck source state after potentially expensive archival copies.
        current = inventory(engine, transaction_id, persist=False)
        if current["inventory_sha256"] != manifest["inventory_sha256"]:
            raise WorkshopError("TX_RECYCLING_CANDIDATE_CHANGED", "transaction bytes changed during analysis/archival")
        review = {
            "schema": REVIEW_SCHEMA, "transaction_id": transaction_id,
            "inventory_sha256": manifest["inventory_sha256"],
            "proposal_sha256": sha256_bytes(_canonical(proposal)),
            "vault_manifest_sha256": vault["sha256"],
            "planned_approach": proposal["planned_approach"].strip(),
            "attempted_approach": proposal["attempted_approach"].strip(),
            "outcome_reason": proposal["outcome_reason"].strip(),
            "no_changes_reason": str(proposal.get("no_changes_reason") or ""),
            "decisions": decisions,
        }
        if proposal.get("schema") == PAGED_SCHEMA:
            review["chunks"] = _progress_pages(root, state, manifest)
            review["global_unit_count"] = len(manifest["units"])
        digest = sha256_bytes(_canonical(review))
        review_path = engine.config.state_directory / "tx-recycling" / "reviews" / f"{transaction_id}-{digest}.json"
        reject_link_components(review_path, label="recycling review")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        reject_link_components(review_path, label="recycling review")
        if not review_path.exists():
            with review_path.open("xb") as handle:
                handle.write(_canonical(review))
        if sha256_bytes(review_path.read_bytes()) != digest:
            raise WorkshopError("TX_RECYCLING_REVIEW_CORRUPT", "recycling review receipt cannot be re-read")
        state["recycling"] = {
            **review, "status": "REVIEWED", "analysis_sha256": digest,
            "review_path": str(review_path), "vault_manifest": vault["manifest"],
        }
        # Terminal TX are no longer owned by the active Guardian session.
        # Only append an immutable history event; never revive the TX lease.
        _, unchanged = engine._load_transaction(transaction_id)
        if _canonical(unchanged) != _canonical(prior_state):
            raise WorkshopError("TX_RECYCLING_STATE_CHANGED", "transaction state changed during review archival")
        state["updated_at"] = utc_now()
        state.setdefault("journal", []).append({
            "event": "TX_RECYCLE_REVIEWED", "at": state["updated_at"],
            "details": {"analysis_sha256": digest, "inventory_sha256": manifest["inventory_sha256"]},
        })
        from .tx_lifecycle import stage_lifecycle_event
        stage_lifecycle_event(
            engine, root, state, event="TX_RECYCLE_REVIEWED", required_projection=True,
            before_outbox=lambda target: _persist_review_intent(root, prior_state, target, sha256_bytes(_canonical(proposal))),
        )
        return _recover_review_intent(engine, root, proposal)


def assert_reviewed(engine: Any, transaction_id: str) -> dict[str, Any]:
    from .tx_lifecycle import assert_no_event_intents
    assert_no_event_intents(engine, transaction_id)
    root, state = engine._load_transaction(transaction_id)
    intent = _review_intent_path(root)
    if intent.exists() or intent.is_symlink():
        raise WorkshopError("TX_RECYCLING_INTENT_PENDING", "resume the original recycling submit before compaction")
    review = state.get("recycling")
    if not isinstance(review, dict) or review.get("status") != "REVIEWED":
        raise WorkshopError("TX_RECYCLING_REVIEW_REQUIRED",
                            "recycling analysis must be documented and projected before compaction")
    current = inventory(engine, transaction_id, persist=False)
    if review.get("inventory_sha256") != current["inventory_sha256"]:
        raise WorkshopError("TX_RECYCLING_INVENTORY_STALE",
                            "transaction source files differ from the reviewed inventory")
    path = Path(str(review.get("review_path") or ""))
    reject_link_components(path, label="recycling review")
    if not path.is_file() or _sha_file(path) != review.get("analysis_sha256"):
        raise WorkshopError("TX_RECYCLING_REVIEW_CORRUPT", "immutable analysis receipt is unavailable or corrupted")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    if persisted.get("transaction_id") != transaction_id or persisted.get("inventory_sha256") != current["inventory_sha256"]:
        raise WorkshopError("TX_RECYCLING_REVIEW_MISMATCH", "review receipt does not match live TX identity")
    if review.get("vault_manifest_sha256") != persisted.get("vault_manifest_sha256"):
        raise WorkshopError("TX_RECYCLING_VAULT_MISMATCH", "review references a different archived candidate")
    from .tx_documents import assert_projected
    assert_projected(engine, transaction_id)
    return review


def retire_orphan(engine: Any, transaction_id: str, *, reason: str) -> dict[str, Any]:
    """Explicit operator decision for abandoned *pre-apply* TX with no lease.

    Cannot be used during an active Guardian patch session or after any
    attempted Apply/permit. Never rewrites an APPLYING/RECOVERY_REQUIRED TX.
    """
    _note(reason, "orphan retirement justification", minimum=40, maximum=2000)
    from .tx_retention import _retention_lock, snapshot
    with _retention_lock(engine):
        if engine.lease_path.exists():
            raise WorkshopError("TX_RECYCLING_ORPHAN_LEASE", "a Workshop lease exists; use the supported recovery path")
        if engine.config.raw.get("guardian_required", False) is True:
            raise WorkshopError("TX_RECYCLING_GUARDIAN_ACTIVE",
                                "Guardian-enforced orphan checkout requires Guardian reconciliation")
        root, state = engine._load_transaction(transaction_id)
        if state.get("state") not in PRE_APPLY:
            raise WorkshopError("TX_RECYCLING_ORPHAN_STATE",
                                "only unleased pre-apply transactions can be retired as abandoned")
        if (state.get("permit") or any(str(e.get("event", "")).endswith("APPLY_STARTED")
                                       for e in state.get("journal", []) if isinstance(e, dict)) or
                (root / "rollback" / "targets.json").exists()):
            raise WorkshopError("TX_RECYCLING_ORPHAN_APPLY_EVIDENCE",
                                "possible apply/rollback evidence exists; operator recovery is required")
        saved = snapshot(engine, transaction_id, include_entire_tx=True)
        state["state"] = "ABORTED_ORPHANED_CHECKOUT"
        state["orphan_retirement"] = {"reason": reason, "vault_manifest_sha256": saved["sha256"],
                                     "not_a_deployment_proof": True}
        state["updated_at"] = utc_now()
        state.setdefault("journal", []).append({"event": "TX_RECYCLING_ORPHAN_RETIRED",
                                                "at": state["updated_at"],
                                                "details": state["orphan_retirement"]})
        from .tx_lifecycle import stage_lifecycle_event
        stage_lifecycle_event(engine, root, state, event="TX_RECYCLING_ORPHAN_RETIRED")
        atomic_write_json(root / "state.json", state)
        from .tx_documents import update_document, assert_projected
        update_document(engine, state, event="TX_RECYCLING_ORPHAN_RETIRED")
        assert_projected(engine, transaction_id)
        return {"transaction_id": transaction_id, "state": state["state"],
                "archived": saved["manifest"], "reason": reason}


def drain(engine: Any, *, max_items: int = 100) -> dict[str, Any]:
    """Resumable catch-up, including old workspaces already above ten TX.

    Never fabricates an agent recycling analysis. Each valid approved TX
    may be compacted independently. Bad TX are reported and skipped.
    """
    from .tx_retention import compact
    if type(max_items) is not int or not 1 <= max_items <= 1000:
        raise WorkshopError("TX_RECYCLING_BATCH_INVALID", "max_items must be 1-1000")
    if engine.config.raw.get("tx_retention_required", False) is not True:
        raise WorkshopError("TX_RECYCLING_NOT_ENABLED", "enable governed retention before batch recycling")
    from .tx_retention import recover_interrupted_compactions
    interrupted = recover_interrupted_compactions(engine, max_items=max_items)
    before = pool_status(engine)
    if not before["required"]:
        return {**before, "processed": [], "blocked": [], "remaining": 0,
                "recovered_interrupted_compactions": interrupted}
    processed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    count = before["materialized_count"]
    # Cursor is an advisory position in the live inventory, never permission to
    # delete. Re-evaluate every candidate from state and evidence on each pass.
    cursor_path = engine.config.state_directory / "tx-recycling" / "drain_cursor.json"
    try:
        previous = json.loads(cursor_path.read_text(encoding="utf-8"))
        after_id = str(previous.get("last_tx_id", ""))
    except (OSError, ValueError, AttributeError):
        after_id = ""
    candidates = before["rows"]
    ids = [row["transaction_id"] for row in candidates]
    offset = (ids.index(after_id) + 1) % len(ids) if ids and after_id in ids else 0
    ordered = candidates[offset:] + candidates[:offset]
    examined = 0
    last_examined = None
    for item in ordered:
        if examined >= max_items or count <= before["target_max"]:
            break
        txid = item["transaction_id"]
        examined += 1
        last_examined = txid
        try:
            state = _state(engine._transaction_path(txid))
            if state.get("state") not in TERMINAL:
                raise WorkshopError("TX_RECYCLING_NEEDS_RECOVERY",
                                    "TX must first be technically closed or safely retired as an orphan")
            intent = _review_intent_path(engine._transaction_path(txid))
            if intent.exists() or intent.is_symlink():
                raise WorkshopError("TX_RECYCLING_INTENT_PENDING", "resume the original recycling submit before batch compaction")
            if engine.config.raw.get("transaction_documents_required", False) is True:
                # Historical TX were often created before mandatory Markdown
                # records existed. Repair documentary coverage without pretending
                # that an old, untested approach was successful.
                from .tx_documents import assert_projected
                try:
                    assert_projected(engine, txid)
                except WorkshopError:
                    engine.reconcile_transaction_document(txid)
                    assert_projected(engine, txid)
            if (state.get("recycling") or {}).get("status") != "REVIEWED":
                # Build an immutable manifest the agent can use for a concrete
                # decision. Never silently choose discard/reuse on its behalf.
                assessment = inspect(engine, txid)
                blocked.append({"transaction_id": txid, "code": "TX_RECYCLING_REVIEW_REQUIRED",
                                "inventory_path": assessment["inventory_path"],
                                "proposal_path": assessment["proposal_path"],
                                "changed_units": assessment["changed_unit_count"]})
                continue
            result = compact(engine, txid)
            processed.append(result)
            count -= 1
        except Exception as exc:
            # Isolate corrupt legacy records and third-party projection errors.
            # Record each failure rather than aborting the entire catch-up batch.
            blocked.append({"transaction_id": txid,
                            "code": getattr(exc, "code", type(exc).__name__),
                            "reason": str(exc)[:320]})
    # A crash before this checkpoint may replay a bounded subset; compaction
    # receipts and candidate hashes make that replay safe. Never persist a
    # "cursor says done" claim as authorization for a subsequent checkout.
    if last_examined is not None:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(cursor_path, {
            "schema": "werkfaden-tx-drain-cursor/v1",
            "last_tx_id": last_examined,
            "observed_candidate_count": len(candidates),
            "observed_inventory_sha256": sha256_bytes(_canonical(ids)),
            "processed_in_batch": examined,
        })
    after = pool_status(engine, publish=True)
    return {"schema": "werkfaden-tx-recycling-drain/v1",
            "initial_materialized": before["materialized_count"],
            "materialized": after["materialized_count"],
            "target_max": after["target_max"],
            "target_reached": not after["required"],
            "processed": processed, "blocked": blocked,
            "more_to_inspect": max(0, len(before["rows"]) - examined),
            "cursor": str(cursor_path) if last_examined is not None else None,
            "recovered_interrupted_compactions": interrupted,
            "next_action": "NORMAL_WORK_ALLOWED" if not after["required"] else "TX_RECYCLING_REQUIRED"}
