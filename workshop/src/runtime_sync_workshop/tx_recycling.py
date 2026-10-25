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
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json, sha256_bytes, utc_now

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
    if not root.is_dir() or root.is_symlink():
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
    root, state = engine._load_transaction(transaction_id)
    if str(state.get("transaction_id")) != transaction_id:
        raise WorkshopError("TX_RECYCLING_STATE_ID_MISMATCH", "transaction state identity is invalid")
    work_root = root / "work"
    if not work_root.is_dir():
        raise WorkshopError("TX_RECYCLING_WORK_MISSING", "transaction has no materialized candidate to inspect")
    work = _files(work_root)
    baseline = _files(root / "baseline")
    source_candidates = set(work)
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
        path.parent.mkdir(parents=True, exist_ok=True)
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


def inspect(engine: Any, transaction_id: str) -> dict[str, Any]:
    """Generate a human/agent-editable proposal without guessing dispositions."""
    data = inventory(engine, transaction_id, persist=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    if p.is_absolute() or ".." in p.parts or not rel or not p.parts:
        return False
    base = root if scope == "TX" else engine.config.kairos_workspace if scope == "KAIROS" else None
    if base is None:
        return False
    full = base / p
    try:
        if full.is_symlink() or not full.is_file() or not full.resolve().is_relative_to(base.resolve()):
            return False
        return True
    except (OSError, ValueError):
        return False


def _verify_analysis(engine: Any, root: Path, state: dict[str, Any],
                     manifest: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
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
        # A previously shadow-verified candidate that was never delivered is
        # conservatively retained. The JEV response is not release authority.
        if state.get("verified_work_package_sha256") and disp != "RETAIN_FOR_REUSE":
            raise WorkshopError("TX_RECYCLING_PROTECTED_CANDIDATE",
                                "a verified never-applied candidate cannot be classified as disposable")
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


def submit(engine: Any, transaction_id: str, analysis_path: Path) -> dict[str, Any]:
    from .tx_retention import _retention_lock, snapshot
    from .tx_documents import update_document, assert_projected
    with _retention_lock(engine):
        if engine.lease_path.exists():
            raise WorkshopError("TX_RECYCLING_LEASE_ACTIVE", "recycle only after the current Workshop lease is released")
        root, state = engine._load_transaction(transaction_id)
        if state.get("state") not in TERMINAL:
            raise WorkshopError("TX_RECYCLING_TX_NOT_TERMINAL",
                                "technical abort/apply/recovery must finish before recycling decision")
        proposal = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
        if not isinstance(proposal, dict):
            raise WorkshopError("TX_RECYCLING_ANALYSIS_SCHEMA", "analysis file must contain a JSON object")
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
        digest = sha256_bytes(_canonical(review))
        review_path = engine.config.state_directory / "tx-recycling" / "reviews" / f"{transaction_id}-{digest}.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
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
        from .util import atomic_write_json
        state["updated_at"] = utc_now()
        state.setdefault("journal", []).append({
            "event": "TX_RECYCLE_REVIEWED", "at": state["updated_at"],
            "details": {"analysis_sha256": digest, "inventory_sha256": manifest["inventory_sha256"]},
        })
        atomic_write_json(root / "state.json", state)
        update_document(engine, state, event="TX_RECYCLE_REVIEWED")
        assert_projected(engine, transaction_id)
        return {"transaction_id": transaction_id, "status": "REVIEWED",
                "inventory_sha256": manifest["inventory_sha256"],
                "analysis_sha256": digest, "unit_count": len(decisions),
                "vault_manifest": vault["manifest"]}


def assert_reviewed(engine: Any, transaction_id: str) -> dict[str, Any]:
    root, state = engine._load_transaction(transaction_id)
    review = state.get("recycling")
    if not isinstance(review, dict) or review.get("status") != "REVIEWED":
        raise WorkshopError("TX_RECYCLING_REVIEW_REQUIRED",
                            "recycling analysis must be documented and projected before compaction")
    current = inventory(engine, transaction_id, persist=False)
    if review.get("inventory_sha256") != current["inventory_sha256"]:
        raise WorkshopError("TX_RECYCLING_INVENTORY_STALE",
                            "transaction source files differ from the reviewed inventory")
    path = Path(str(review.get("review_path") or ""))
    if not path.is_file() or path.is_symlink() or _sha_file(path) != review.get("analysis_sha256"):
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
            if engine.config.raw.get("transaction_documents_required", False) is True:
                # Historical TX were often created before mandatory Markdown
                # records existed. Repair documentary coverage without pretending
                # that an old, untested approach was successful.
                from .tx_documents import assert_projected, publish, pending_path
                try:
                    assert_projected(engine, txid)
                except WorkshopError:
                    publish(engine, state)
                    pending_path(engine, txid).unlink(missing_ok=True)
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
