"""One lifecycle owner for Workshop TX events and historical scope provenance.

Raw engine event strings remain journal diagnostics. Document publication is
driven by the persisted lifecycle state, not by competing name lists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .util import WorkshopError, atomic_write_json, utc_now, reject_link_components

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
    "TX_RECYCLING_ORPHAN_RETIRED", "TX_HISTORY_INTAKE", "TX_SCOPE_BOUND",
})




SOURCE_EPOCH_KIND = "werkfaden-tx-material-state/v1"


def material_source(state: dict[str, Any]) -> dict[str, Any]:
    """Exclude diagnostic bookkeeping and event-derived self references only."""
    value = json.loads(json.dumps(state))
    for key in ("updated_at", "journal", "tx_event", "tx_event_id", "tx_event_sequence", "tx_event_history"):
        value.pop(key, None)
    history = value.get("historical_scope")
    if isinstance(history, dict):
        history.pop("event_id", None)
        history.pop("event_sequence", None)
    return value


def source_epoch(state: dict[str, Any]) -> str:
    payload = json.dumps(material_source(state), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _document_projection_latch(state: dict[str, Any]) -> bool:
    value = state.get("document_projection_required", False)
    if type(value) is not bool:
        raise WorkshopError(
            "TX_DOCUMENT_REQUIREMENT_INVALID",
            "transaction documentation requirement must be a boolean",
        )
    return value


def requires_projection(engine: Any, state: dict[str, Any]) -> bool:
    """A native TX obligation survives global policy changes and recovery."""
    latched = _document_projection_latch(state)
    event = state.get("tx_event")
    return (latched or engine.config.raw.get("transaction_documents_required", False) is True
            or state.get("historical_scope") is not None
            or (isinstance(event, dict) and event.get("required_projection") is True))


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
                          event: str, required_projection: bool = False,
                          before_outbox: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any] | None:
    """Stamp monotone identity into authoritative state before its atomic save.

    The pending marker is created before the state save; an interrupted first
    save can only overblock checkout, never silently lose a required event.
    """
    from .tx_retention import _checked_transaction_path
    txid = str(state.get("transaction_id") or "")
    _checked_transaction_path(engine, txid)
    if root.resolve() != engine._transaction_path(txid).resolve():
        raise intent_error(txid, "native writer root differs from its transaction identity")
    old_path = root / "state.json"
    try:
        old = json.loads(old_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        old = {}
    except (OSError, ValueError) as exc:
        raise intent_error(root.name, "prior native transaction state is unreadable") from exc
    if not isinstance(old, dict):
        raise intent_error(root.name, "prior native transaction state is not an object")
    prior_latched = _document_projection_latch(old)
    target_latched = _document_projection_latch(state)
    if prior_latched and not target_latched:
        raise WorkshopError(
            "TX_DOCUMENT_REQUIREMENT_DOWNGRADE",
            "a persisted transaction documentation requirement cannot be removed or disabled",
        )
    # Establish the obligation BEFORE computing the material epoch or outbox.
    # The native writer can force its first event before enabling global policy.
    # An older mandatory event is authoritative even when its pre-latch state
    # was written by an earlier release and policy has since changed.
    if required_projection or requires_projection(engine, old) or requires_projection(engine, state):
        state["document_projection_required"] = True
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
                or source_epoch(state) != source_epoch(old)
                or event in MATERIAL_ANNOTATIONS)
    if not required:
        from .tx_documents import assert_projection_lineage
        assert_projection_lineage(engine, old)
        return None
    txid = str(state.get("transaction_id") or "")
    gap = validate_prior_endpoint(engine, old, state, event=event)
    if gap is not None:
        state["tx_event_source_gap"] = gap
    history = json.loads(json.dumps(old.get("tx_event_history", [])))
    if old.get("tx_event"):
        history.append(old["tx_event"])
    state["tx_event_history"] = history
    high_water = int(old.get("tx_event_sequence", 0))
    # A safe native transition may overtake a state commit that never landed.
    # Retain that event's exact payload; do not claim its technical effects did
    # not happen merely because its state commit was not observed.
    resolutions = json.loads(json.dumps(old.get("tx_event_resolutions", [])))
    for _, intent, _ in event_intents(engine, txid):
        high_water = max(high_water, intent["target_state"]["tx_event_sequence"])
        if endpoint(old) != endpoint(intent["prior_state"]):
            if event_is_represented(old, intent["target_state"]["tx_event"]):
                continue
            raise intent_error(txid, "an unresolved intent has conflicting state endpoints")
        item = {"event": intent["target_state"]["tx_event"],
                "outcome": "STATE_COMMIT_NOT_OBSERVED",
                "prior_state_sha256": intent["prior_state_sha256"],
                "intent_sha256": digest(intent)}
        if item not in resolutions:
            resolutions.append(item)
    if resolutions:
        state["tx_event_resolutions"] = resolutions
    seq = high_water + 1
    payload = {
        "schema": "werkfaden-tx-event/v1",
        "history_sha256": digest(history),
        "source_epoch": source_epoch(state),
        "source_epoch_kind": SOURCE_EPOCH_KIND,
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
        "required_projection": required_projection or requires_projection(engine, state),
        "previous_event_id": str(old.get("tx_event_id") or ""),
        "scope_receipt_sha256": str((state.get("historical_scope") or {}).get("receipt_sha256")
                                    or (state.get("original_scope") or {}).get("source_receipt_sha256") or ""),
    }
    event_id = "TXE_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()
    state["tx_event_sequence"] = seq
    state["tx_event_id"] = event_id
    state["tx_event"] = {**payload, "event_id": event_id}
    if event in {"TX_HISTORY_INTAKE", "TX_SCOPE_BOUND"}:
        state["historical_scope"]["event_id"] = event_id
        state["historical_scope"]["event_sequence"] = seq
    # Compare the finalized proposed history before any owner intent/outbox or
    # state commit. Exact native scope-gap/legacy declarations remain validated;
    # they do not exempt an unrelated newer acknowledged event.
    from .tx_documents import assert_projection_lineage
    assert_projection_lineage(engine, state)
    if before_outbox is not None:
        if not state["tx_event"]["required_projection"]:
            raise WorkshopError("TX_LIFECYCLE_INTENT_REQUIRES_PROJECTION", "a pre-outbox intent requires mandatory projection")
        before_outbox(json.loads(json.dumps(state)))
    if state["tx_event"]["required_projection"] and before_outbox is None:
        persist_event_intent(engine, root, old, state)
    if state["tx_event"]["required_projection"]:
        from .tx_documents import pending_path, pending_record
        marker = pending_path(engine, txid)
        marker.parent.mkdir(parents=True, exist_ok=True)
        # A safe native Abort/Recovery may proceed while a legacy or corrupt
        # marker is unresolved. Preserve that independent obligation; the new
        # exact event remains discoverable in its own native intent.
        try:
            marker.lstat()
        except FileNotFoundError:
            pass
        else:
            from .tx_documents import _pending_marker_observation, pending_identity_matches
            observed, record = _pending_marker_observation(marker)
            identity = event_for_marker(state, str((record or {}).get("event_id") or ""))
            if observed.get("status") != "OBSERVED" or identity is None or not pending_identity_matches(record, identity):
                return state["tx_event"]
        atomic_write_json(marker, pending_record(
            state, event=event, reason="lifecycle event saved to outbox before state commit",
        ))
    return state["tx_event"]


# A native write-ahead intent is controlled state, not a caller's projection
# candidate. Only the lifecycle writer creates it after the owner's checks.
INTENT_SCHEMA = "werkfaden-tx-event-intent/v1"
MAX_INTENT_BYTES = 32 * 1024 * 1024
MAX_TX_INTENTS = 256


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def endpoint(state: dict[str, Any]) -> dict[str, Any]:
    # Diagnostic saves have always been outside material identity. Retain every
    # other field, including event history, scope self-references and receipts.
    return {k: v for k, v in state.items() if k not in {"updated_at", "journal"}}


def intent_error(txid: str, message: str, *, event: dict[str, Any] | None = None) -> WorkshopError:
    from .tx_documents import PENDING_OUTCOME
    item = {"transaction_id": txid, "status": "UNVERIFIED"}
    if event:
        item.update(event_id=event.get("event_id"), event_sequence=event.get("sequence"))
    return WorkshopError("TX_EVENT_INTENT_PENDING", message, outcome=PENDING_OUTCOME,
                         details=item, references={"total": 1, "total_exact": False,
                         "omitted": 0, "omitted_exact": False, "items": [item]})


def validate_event_chain(state: dict[str, Any], *, check_material: bool = True) -> None:
    current = state.get("tx_event")
    history = state.get("tx_event_history", [])
    if not isinstance(history, list):
        raise ValueError("event history is not a list")
    events = [*history, current] if current else history
    seen: set[str] = set()
    previous = None
    for number, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError("event history entry is not an object")
        payload = {k: v for k, v in event.items() if k != "event_id"}
        expected = "TXE_" + hashlib.sha256(canonical(payload)).hexdigest()[:32].upper()
        if (event.get("schema") != "werkfaden-tx-event/v1" or event.get("event_id") != expected
                or expected in seen or type(event.get("sequence")) is not int or event["sequence"] < 1
                or event.get("transaction_id") != state.get("transaction_id")
                or event.get("phase") != PHASES.get(event.get("state_after"))):
            raise ValueError("event history identity is invalid")
        if previous and (event.get("previous_event_id") != previous["event_id"]
                         or event["sequence"] <= previous["sequence"]):
            raise ValueError("event history is not an ordered exact chain")
        # The first entry may be an installed legacy anchor with no history
        # binding. All events generated by this protocol bind the exact prefix.
        if "history_sha256" in event and event["history_sha256"] != digest(history[:number]):
            raise ValueError("event history prefix hash differs")
        if number and "history_sha256" not in event:
            raise ValueError("only the legacy history anchor may lack a prefix hash")
        seen.add(expected)
        previous = event
    if current and (current.get("event_id") != state.get("tx_event_id")
                    or current.get("sequence") != state.get("tx_event_sequence")
                    or current.get("source_epoch_kind") != SOURCE_EPOCH_KIND
                    or current.get("state_after") != state.get("state")
                    or current.get("tx_kind") != str(state.get("transaction_kind") or state.get("schema") or "normal")
                    or current.get("candidate_sha256") != str(state.get("verified_work_package_sha256") or state.get("prepared_work_package_sha256") or "")
                    or current.get("baseline_sha256") != str(state.get("baseline_package_sha256") or "")
                    or (check_material and (current.get("source_epoch") != source_epoch(state)
                        or current.get("scope_receipt_sha256") != str((state.get("historical_scope") or {}).get("receipt_sha256") or (state.get("original_scope") or {}).get("source_receipt_sha256") or "")))):
        raise ValueError("current event does not match its material source")


def legacy_event_anchor_admission(state: dict[str, Any], inspection: dict[str, Any],
                                  previous_state_sha256: str) -> dict[str, Any]:
    """Describe two exact old formats, without attesting their missing material epoch.

    This is a read-only classifier. Only explicit native historical INTAKE may
    use its result, through the immutable receipt checked by the prior owner.
    """
    event = state.get("tx_event")
    anchor_keys = {"schema", "transaction_id", "event", "event_id", "sequence",
                   "previous_event_id", "state_before", "state_after", "phase",
                   "tx_kind", "candidate_sha256", "scope_receipt_sha256"}
    extended_keys = anchor_keys | {"baseline_sha256", "occurred_at", "required_projection"}
    if not isinstance(event, dict) or set(event) not in (anchor_keys, extended_keys):
        raise ValueError("unsupported legacy event field family")
    if "tx_event_history" in state and state["tx_event_history"] != []:
        raise ValueError("legacy format admission requires a single stored anchor")
    # Reuse the unchanged identity/prefix validator on the exact old anchor as
    # history. Deliberately do not manufacture a modern current endpoint.
    witness = dict(state)
    witness.pop("tx_event", None)
    witness["tx_event_history"] = [event]
    validate_event_chain(witness)
    original = state.get("original_scope")
    from .tx_retention import TERMINAL
    if (state.get("state") not in TERMINAL
            or inspection.get("transaction_id") != state.get("transaction_id")):
        raise ValueError("legacy event admission requires the exact terminal transaction")
    scope_keys = ("workspace_id", "goal_id", "milestone_id", "task_id", "loop")
    inspected_scope = inspection.get("scope")
    recorded_scope = original if isinstance(original, dict) else {}
    complete_original = all(recorded_scope.get(key) not in (None, "") for key in scope_keys)
    verified_origin = (complete_original
        and inspection.get("status") == "VERIFIED"
        and inspection.get("discovery_complete") is True
        and isinstance(inspected_scope, dict)
        and all(original.get(key) == inspected_scope.get(key) for key in scope_keys))
    # Keep the original v1 authority unchanged for a complete recorded owner.
    if (complete_original and inspection.get("status") == "VERIFIED"
            and not verified_origin):
        raise ValueError("legacy event admission cannot replace recorded scope")
    if not verified_origin:
        unresolved = (inspection.get("status") in {"MISSING", "CONFLICT"}
            and inspected_scope is None
            and inspection.get("scope_status") == "SCOPE_UNRESOLVED")
        witnessed_loss = (not complete_original
            and inspection.get("status") == "VERIFIED"
            and inspection.get("discovery_complete") is True
            and isinstance(inspected_scope, dict)
            and inspection.get("scope_status") == "RECORDED_SCOPE_VERIFIED")
        if (not (unresolved or witnessed_loss)
                or inspection.get("automatic_binding") is not False
                or type(inspection.get("discovery_complete")) is not bool):
            raise ValueError("legacy quarantine requires an exact native unresolved inspection")
    baseline = state.get("baseline_package_sha256")
    def sha(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    if not sha(previous_state_sha256) or not sha(baseline) or not sha(inspection.get("report_sha256")):
        raise ValueError("legacy event admission requires exact observed state and report hashes")
    expected_scope = str((state.get("historical_scope") or {}).get("receipt_sha256")
                         or recorded_scope.get("source_receipt_sha256") or "")
    scope_receipt = event.get("scope_receipt_sha256")
    if verified_origin:
        if scope_receipt != expected_scope:
            raise ValueError("legacy event scope receipt differs from its recorded proof")
    elif (not isinstance(scope_receipt, str)
            or (scope_receipt != "" and not sha(scope_receipt))
            or (expected_scope and scope_receipt != expected_scope)):
        raise ValueError("legacy event scope receipt differs from its recorded proof")
    if (event.get("event_id") != state.get("tx_event_id")
            or event.get("sequence") != state.get("tx_event_sequence")
            or type(state.get("tx_event_sequence")) is not int
            or event.get("state_after") != state.get("state")
            or event.get("state_before") not in PHASES
            or event.get("tx_kind") != str(state.get("transaction_kind") or state.get("schema") or "normal")
            or event.get("candidate_sha256") != str(state.get("verified_work_package_sha256") or state.get("prepared_work_package_sha256") or "")
            or not isinstance(event.get("event"), str) or not 1 <= len(event["event"]) <= 200
            or not isinstance(event.get("previous_event_id"), str)):
        raise ValueError("legacy event identity differs from the recorded endpoint")
    extended = set(event) == extended_keys
    if extended and (event["baseline_sha256"] != baseline
                     or type(event["required_projection"]) is not bool
                     or not isinstance(event["occurred_at"], str)
                     or not 1 <= len(event["occurred_at"]) <= 128):
        raise ValueError("legacy event has an invalid recorded baseline or obligation")
    admission = {
        "schema": "werkfaden-tx-legacy-event-admission/v1",
        "source_format": "pre-material-epoch-anchor/v1" if extended else "pre-material-epoch-anchor/v0",
        "target_epoch_kind": SOURCE_EPOCH_KIND,
        "previous_state_sha256": previous_state_sha256,
        "inspection_report_sha256": inspection["report_sha256"],
        "prior_event_id": event["event_id"],
        "prior_event_sequence": event["sequence"],
        "unattested_fields": ["source_epoch", "history_prefix_sha256"] + (
            [] if extended else ["baseline_sha256", "required_projection", "occurred_at"]),
        "claim_boundary": "Exact observed legacy bytes and recorded-scope consistency only; missing historical material bindings and execution remain unattested",
    }
    if not verified_origin:
        admission.update(
            schema="werkfaden-tx-legacy-event-admission/v2",
            admission_mode="QUARANTINE_ONLY",
            inspection_status=inspection["status"],
            scope_assignment_authority=False,
            claim_boundary="Exact observed legacy bytes for quarantine only; original provenance, material bindings and execution remain unattested. No ownership or binding authority.",
        )
        admission["unattested_fields"].append("original_scope_provenance")
        if scope_receipt and not expected_scope:
            admission["unattested_fields"].append("scope_receipt_binding")
    return admission


def validate_prior_endpoint(engine: Any, prior: dict[str, Any], target: dict[str, Any], *, event: str) -> dict[str, Any] | None:
    """A scope-loss intake needs exact archived bytes, never a blanket drift waiver."""
    try:
        validate_event_chain(prior)
        return None
    except ValueError as original:
        if event == "TX_HISTORY_INTAKE":
            from .tx_documents import load_scope_transition
            receipt = load_scope_transition(engine, target)
            if receipt and receipt.get("legacy_event_format") is not None:
                if json.loads(receipt["previous_state_utf8"]) != prior:
                    raise intent_error(str(prior.get("transaction_id") or ""), "legacy intake prior differs from its immutable receipt")
                admission = legacy_event_anchor_admission(
                    prior, receipt["inspection"], receipt["previous_state_sha256"])
                if admission != receipt["legacy_event_format"]:
                    raise intent_error(str(prior.get("transaction_id") or ""), "legacy event format declaration differs from its exact preimage")
                return {"reason": "EXPLICIT_LEGACY_EVENT_FORMAT_ADMISSION",
                        "scope_transition_receipt_sha256": target["historical_scope"]["receipt_sha256"],
                        "legacy_event_admission": admission}
        if event != "TX_HISTORY_INTAKE" or "original_scope" in prior:
            raise intent_error(str(prior.get("transaction_id") or ""), "prior native event or source is invalid") from original
    from .tx_documents import load_scope_transition
    from .tx_retention import TERMINAL, vault_dir, _object_path
    if prior.get("state") not in TERMINAL:
        raise intent_error(str(prior.get("transaction_id") or ""), "scope-loss recovery requires terminal history")
    # Identity/chain validity is still mandatory. Only the material epoch/scope
    # comparison can be explained by a proven single-field loss below.
    validate_event_chain(prior, check_material=False)
    receipt = load_scope_transition(engine, target)
    if (not receipt or receipt.get("operation") != "INTAKE"
            or json.loads(receipt["previous_state_utf8"]) != prior
            or receipt["inspection"].get("status") != "VERIFIED"):
        raise intent_error(str(prior.get("transaction_id") or ""), "scope-loss intake lacks its exact native receipt")
    vault = vault_dir(engine)
    txid = str(prior["transaction_id"])
    def read(path: Path, expected_sha: str, size: int | None = None) -> bytes:
        import stat
        reject_link_components(path, label="scope-loss archive witness")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INTENT_BYTES:
            raise ValueError("scope-loss witness is not a bounded regular file")
        raw = path.read_bytes()
        reject_link_components(path, label="scope-loss archive witness")
        after = path.stat()
        if (len(raw) != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_size != after.st_size or hashlib.sha256(raw).hexdigest() != expected_sha
                or (size is not None and len(raw) != size)):
            raise ValueError("scope-loss archive bytes changed")
        return raw
    try:
        for evidence in receipt["inspection"].get("evidence", []):
            source = evidence.get("source", {})
            manifest_source = source.get("manifest")
            if evidence.get("status") != "VERIFIED" or not isinstance(manifest_source, dict):
                continue
            manifest_path = vault / "manifests" / (txid + "-" + manifest_source["sha256"] + ".json")
            if Path(manifest_source["path"]).resolve() != manifest_path.resolve():
                raise ValueError("scope-loss manifest path differs from native vault authority")
            manifest = json.loads(read(manifest_path, manifest_source["sha256"], manifest_source["bytes"]))
            if (manifest.get("schema") != "werkfaden-transaction-vault/v1"
                    or manifest.get("transaction_id") != txid or manifest.get("complete") is not True):
                raise ValueError("scope-loss manifest is not complete native TX evidence")
            fact = manifest["files"]["state.json"]
            object_path = _object_path(vault, fact["sha256"])
            if Path(source["path"]).resolve() != object_path.resolve() or source["sha256"] != fact["sha256"]:
                raise ValueError("scope-loss state object differs from manifest")
            archived = json.loads(read(object_path, fact["sha256"], fact["size"]))
            validate_event_chain(archived)
            if "original_scope" not in archived:
                continue
            archived.pop("original_scope")
            if endpoint(archived) != endpoint(prior):
                continue
            return {"reason": "ARCHIVED_ORIGINAL_SCOPE_FIELD_LOSS",
                    "scope_transition_receipt_sha256": target["historical_scope"]["receipt_sha256"],
                    "archived_state_sha256": fact["sha256"],
                    "claimed_prior_source_epoch": prior["tx_event"]["source_epoch"],
                    "observed_prior_source_epoch": source_epoch(prior)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise intent_error(txid, "scope-loss archive proof cannot be verified") from exc
    raise intent_error(txid, "archive does not prove original_scope was the only material loss")


def event_is_represented(state: dict[str, Any], event: dict[str, Any]) -> bool:
    validate_event_chain(state)
    if any(value == event for value in [*state.get("tx_event_history", []), state.get("tx_event")]):
        return True
    return any(isinstance(item, dict) and item.get("event") == event
               and item.get("outcome") == "STATE_COMMIT_NOT_OBSERVED"
               for item in state.get("tx_event_resolutions", []))


def event_for_marker(state: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    validate_event_chain(state)
    events = [*state.get("tx_event_history", []), state.get("tx_event")]
    events += [item.get("event") for item in state.get("tx_event_resolutions", []) if isinstance(item, dict)]
    return next((item for item in events if isinstance(item, dict) and item.get("event_id") == event_id), None)


def _control_sha(path: Path) -> str | None:
    reject_link_components(path, label="event intent control boundary")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def intent_directory(engine: Any) -> Path:
    return engine.config.state_directory / "transaction-event-intents"


def event_intents(engine: Any, txid: str) -> list[tuple[Path, dict[str, Any], bytes]]:
    import re
    import stat
    directory = intent_directory(engine) / txid
    reject_link_components(directory, label="event intent directory")
    if not directory.exists():
        return []
    result = []
    try:
        for path in directory.iterdir():
            if len(result) >= MAX_TX_INTENTS:
                raise ValueError("transaction event-intent bound exceeded")
            reject_link_components(path, label="event intent")
            before = path.stat()
            if (re.fullmatch(r"TXE_[0-9A-F]{32}\.json", path.name) is None
                    or not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INTENT_BYTES):
                raise ValueError("invalid or oversized event-intent entry")
            with path.open("rb") as stream:
                raw = stream.read(MAX_INTENT_BYTES + 1)
            after = path.stat()
            if (len(raw) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ValueError("event intent changed during observation")
            intent = json.loads(raw)
            prior, target = intent["prior_state"], intent["target_state"]
            gap = validate_prior_endpoint(engine, prior, target, event=target["tx_event"]["event"])
            if gap is not None and target.get("tx_event_source_gap") != gap:
                raise ValueError("historical source gap differs from its exact archive proof")
            validate_event_chain(target)
            identity = target["tx_event"]
            if (intent.get("schema") != INTENT_SCHEMA or intent.get("transaction_id") != txid
                    or target.get("transaction_id") != txid
                    or (prior and prior.get("transaction_id") != txid)
                    or intent.get("prior_state_sha256") != digest(prior)
                    or intent.get("target_state_sha256") != digest(target)
                    or path.stem != identity["event_id"]
                    or identity.get("required_projection") is not True
                    or identity.get("previous_event_id") != str(prior.get("tx_event_id") or "")
                    or identity.get("state_before") != str(prior.get("state") or "")
                    or identity["sequence"] <= int(prior.get("tx_event_sequence", 0))
                    or target.get("tx_event_history") != [*prior.get("tx_event_history", []), *([prior["tx_event"]] if prior.get("tx_event") else [])]):
                raise ValueError("event-intent identity or endpoints differ")
            result.append((path, intent, raw))
        return sorted(result, key=lambda row: row[1]["target_state"]["tx_event_sequence"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise intent_error(txid, "native event intent cannot be verified") from exc


def persist_event_intent(engine: Any, root: Path, prior: dict[str, Any], target: dict[str, Any]) -> None:
    validate_event_chain(target)
    txid = str(target["transaction_id"])
    directory = intent_directory(engine) / txid
    reject_link_components(directory, label="event intent directory")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (target["tx_event_id"] + ".json")
    reject_link_components(path, label="event intent")
    intent = {"schema": INTENT_SCHEMA, "transaction_id": txid,
              "prior_state": json.loads(json.dumps(prior)), "target_state": json.loads(json.dumps(target)),
              "prior_state_sha256": digest(prior), "target_state_sha256": digest(target),
              "lease_sha256": _control_sha(engine.lease_path), "seal_sha256": _control_sha(engine.seal_path)}
    if len(canonical(intent)) > MAX_INTENT_BYTES:
        raise intent_error(txid, "native event intent exceeds its bounded size", event=target["tx_event"])
    if path.exists():
        raise intent_error(txid, "native event intent already exists", event=target["tx_event"])
    if len(event_intents(engine, txid)) >= MAX_TX_INTENTS:
        raise intent_error(txid, "resolve existing event intents before creating another")
    atomic_write_json(path, intent)
    # Read through the same bounded verifier before making the marker visible.
    if not any(row[1] == intent for row in event_intents(engine, txid)):
        raise intent_error(txid, "native event intent did not persist exactly", event=target["tx_event"])


def assert_no_event_intents(engine: Any, transaction_id: str | None = None) -> None:
    if transaction_id is not None:
        from .tx_retention import _checked_transaction_path
        _checked_transaction_path(engine, transaction_id)
        entries = event_intents(engine, transaction_id)
        if entries:
            raise intent_error(transaction_id, "resolve the exact native intent with tx-document-reconcile before another owner starts",
                               event=entries[0][1]["target_state"]["tx_event"])
        return
    directory = intent_directory(engine)
    reject_link_components(directory, label="event intent root")
    if not directory.exists():
        return
    import re
    try:
        for child in directory.iterdir():
            reject_link_components(child, label="event intent transaction")
            if not child.is_dir() or re.fullmatch(r"TXN_[0-9a-f]{24}", child.name) is None:
                raise intent_error(child.name, "untrusted event-intent transaction entry")
            entries = event_intents(engine, child.name)
            if entries:
                raise intent_error(child.name, "recover the native event intent before checkout",
                                   event=entries[0][1]["target_state"]["tx_event"])
    except OSError as exc:
        raise intent_error("UNVERIFIED", "native event-intent root cannot be inspected") from exc


def recover_event_intents(engine: Any, txid: str) -> set[str]:
    """Complete only the exact previously-authorized native state commit.

    Callers hold the common writer lock. Technical operations are never called.
    The owner-computed target is controlled WAL state, not a pending-marker view.
    """
    from .tx_retention import _checked_transaction_path
    _checked_transaction_path(engine, txid)
    root = engine._transaction_path(txid)
    intents = event_intents(engine, txid)
    retired: set[str] = set()
    for _, intent, _ in reversed(intents):
        try:
            actual = json.loads((root / "state.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            actual = {}
        target = intent["target_state"]
        lease_sha = _control_sha(engine.lease_path)
        # A regular checkout exception can release its lease before the first
        # state commit. Its native WAL must remain recoverable as an orphan,
        # without resurrecting source-write authority or a Guardian session.
        from .tx_recycling import PRE_APPLY, retire_orphan
        lost_checkout = (not intent["prior_state"] and intent.get("lease_sha256") is not None
                         and lease_sha is None and target.get("state") in PRE_APPLY
                         and endpoint(actual) in (endpoint(intent["prior_state"]), endpoint(target)))
        if lost_checkout:
            if (engine._guardian_enabled() or not root.is_dir()
                    or _control_sha(engine.seal_path) != intent.get("seal_sha256")
                    or engine._verified_package(require_seal=True) != target.get("baseline_package_sha256")):
                raise intent_error(txid, "failed checkout requires its native Guardian or baseline recovery", event=target["tx_event"])
        elif endpoint(actual) != endpoint(intent["prior_state"]):
            if event_is_represented(actual, target["tx_event"]):
                continue
            raise intent_error(txid, "saved state differs from both native intent endpoints", event=target["tx_event"])
        if (not root.is_dir() or (not lost_checkout and lease_sha != intent.get("lease_sha256"))
                or _control_sha(engine.seal_path) != intent.get("seal_sha256")):
            raise intent_error(txid, "native intent lease, seal or transaction-root boundary changed", event=target["tx_event"])
        if target.get("state") == "POSTCHECK_VERIFIED":
            package = engine._verified_package(require_seal=True)
            seal = json.loads(engine.seal_path.read_text(encoding="utf-8"))
            if package != target.get("postcheck_package_sha256") or seal.get("transaction_id") != txid:
                raise intent_error(txid, "native postcheck seal does not prove this exact transaction", event=target["tx_event"])
            heartbeat = target.get("heartbeat")
            if heartbeat is not None:
                if not isinstance(heartbeat, dict) or heartbeat.get("verified") is not True:
                    raise intent_error(txid, "native postcheck heartbeat is not verified", event=target["tx_event"])
            else:
                # The native auxiliary owner postchecks exact document bytes and
                # an unchanged runtime rather than running a source heartbeat.
                from .auxiliary import _runtime_tree_manifest
                baseline = target.get("baseline_runtime")
                if (not isinstance(baseline, dict) or target.get("runtime_postcheck") != baseline
                        or _runtime_tree_manifest(engine.config) != baseline
                        or seal.get("runtime_package_sha256") != baseline.get("package_sha256")):
                    raise intent_error(txid, "native auxiliary runtime evidence is not verified", event=target["tx_event"])
        if target.get("historical_scope"):
            from .tx_documents import load_scope_transition
            load_scope_transition(engine, target)
        reject_link_components(root / "state.json", label="native event replay state")
        atomic_write_json(root / "state.json", target)
        if lost_checkout:
            # The existing owner validates pre-Apply evidence and preserves a
            # complete native archive. Retrying after an interruption observes
            # the same original intent until retirement is projected.
            retire_orphan(engine, txid, reason="Native precommit checkout recovery: original lease was released before its first state commit; preserve the exact event and retire the unleased pre-Apply candidate.")
            retired.add(target["tx_event_id"])
        elif engine._guardian_enabled() and engine.lease_path.is_file():
            engine.guardian().observe_transaction_state(txid, event=target["tx_event"]["event"], state=target)
    return retired


def acknowledge_event_intents(engine: Any, state: dict[str, Any]) -> None:
    from .tx_documents import assert_projected
    txid = str(state["transaction_id"])
    intents = event_intents(engine, txid)
    if not intents:
        return
    assert_projected(engine, txid, ignore_pending=True)
    root, actual = engine._load_transaction(txid)
    if endpoint(actual) != endpoint(state):
        raise intent_error(txid, "state changed before event-intent acknowledgement")
    for path, intent, raw in intents:
        if not event_is_represented(actual, intent["target_state"]["tx_event"]):
            continue
        reject_link_components(path, label="event intent acknowledgement")
        if path.read_bytes() != raw:
            raise intent_error(txid, "event intent changed before acknowledgement")
        path.unlink()
    directory = intent_directory(engine) / txid
    reject_link_components(directory, label="acknowledged event intent directory")
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
