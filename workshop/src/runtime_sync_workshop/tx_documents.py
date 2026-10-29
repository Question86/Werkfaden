"""Governed TX lifecycle document publication through KAIROS heartbeat.

A TX markdown source is kept in the existing reports/ document root, outside
disposable transaction worktrees. Any publishing failure is recorded in a
durable pending marker. It must never transform successful Apply into a
second attempt or prevent a safe Abort/Recovery.
"""
from __future__ import annotations

import json
import fnmatch
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json, reject_link_components, sha256_bytes

PENDING_OUTCOME = "TX_DOCUMENT_PROJECTION_PENDING"
MAX_PENDING_REFERENCES = 20
MAX_PENDING_MARKERS = 10000
MAX_PENDING_MARKER_BYTES = 1024 * 1024


def _publication_identity(transaction_id: str, event_id: str,
                          content_sha256: str, scope: dict[str, Any]) -> str:
    """A replay grant identifies exact intended bytes and its current owner."""
    intent = {
        "event_id": event_id,
        "content_sha256": content_sha256,
        "scope": {key: scope.get(key) for key in
                  ("goal_id", "milestone_id", "task_id", "criterion_id")},
    }
    digest = sha256_bytes(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{transaction_id}:{digest}"


# Publication is selected from authoritative state transitions by tx_lifecycle.
# Technical event spellings are not a second allowlist here.


def pending_path(engine: Any, transaction_id: str) -> Path:
    return engine.config.state_directory / "transaction-document-pending" / (transaction_id + ".json")



def pending_record(state: dict[str, Any], *, event: str, reason: str,
                   error: Exception | None = None) -> dict[str, Any]:
    """One outbox identity contract for pre-commit staging and publication failure."""
    identity = state.get("tx_event")
    identity = identity if isinstance(identity, dict) else {}
    record = {
        "schema": "werkfaden-tx-projection-pending/v1",
        "transaction_id": str(state.get("transaction_id") or ""),
        "event": event,
        "event_id": str(state.get("tx_event_id") or identity.get("event_id") or ""),
        "event_sequence": state.get("tx_event_sequence"),
        "state": state.get("state"),
        "candidate_sha256": str(identity.get("candidate_sha256") or ""),
        "baseline_sha256": str(identity.get("baseline_sha256") or state.get("baseline_package_sha256") or ""),
        "scope_receipt_sha256": str(identity.get("scope_receipt_sha256") or ""),
        "pending": True,
        "reason": reason,
    }
    if error is not None:
        record.update(code=getattr(error, "code", type(error).__name__), message=str(error))
        details = getattr(error, "details", None)
        if details is not None:
            # Diagnostics do not introduce a second evidence authority. Keep
            # arbitrary exception payloads serializable so the outbox survives.
            record["details"] = json.loads(json.dumps(details, default=str))
    return record


def _pending_marker_observation(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Describe bounded marker bytes without treating them as state authority."""
    result: dict[str, Any] = {"marker": path.name, "transaction_id": path.stem,
                              "event_id": None, "event_sequence": None, "status": "UNVERIFIED"}
    try:
        reject_link_components(path, label="pending marker")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            result["reason"] = "NOT_REGULAR_FILE"
            return result, None
        if before.st_size > MAX_PENDING_MARKER_BYTES:
            result["reason"] = "MARKER_TOO_LARGE"
            return result, None
        with path.open("rb") as handle:
            raw = handle.read(MAX_PENDING_MARKER_BYTES + 1)
        reject_link_components(path, label="pending marker")
        after = path.stat()
        if (len(raw) > MAX_PENDING_MARKER_BYTES or len(raw) != before.st_size
                or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns):
            result["reason"] = "MARKER_CHANGED_DURING_READ"
            return result, None
        result["marker_sha256"] = sha256_bytes(raw)
        marker = json.loads(raw.decode("utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("marker is not a JSON object")
        event_id = marker.get("event_id")
        sequence = marker.get("event_sequence")
        if (marker.get("schema") != "werkfaden-tx-projection-pending/v1"
                or marker.get("pending") is not True
                or marker.get("transaction_id") != path.stem or not isinstance(event_id, str)
                or re.fullmatch(r"TXE_[0-9A-F]{32}", event_id) is None
                or type(sequence) is not int or sequence < 1):
            result["reason"] = "marker identity is missing or invalid"
            return result, marker
        result.update(event_id=event_id, event_sequence=sequence, status="OBSERVED")
        return result, marker
    except Exception as exc:
        result["reason"] = str(getattr(exc, "code", type(exc).__name__))
        return result, None


def assert_no_pending(engine: Any) -> None:
    pending = engine.config.state_directory / "transaction-document-pending"
    try:
        pending.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkshopError("TX_DOCUMENT_SYNC_PENDING", "pending transaction directory cannot be inspected",
                            details=[], outcome=PENDING_OUTCOME,
                            references={"status": "UNVERIFIED_DIRECTORY", "reason": type(exc).__name__}) from exc
    try:
        reject_link_components(pending, label="pending directory")
        if not pending.is_dir():
            raise ValueError("pending path is not a directory")
        names: list[str] = []
        overflow = False
        with os.scandir(pending) as entries:
            for entry in entries:
                if fnmatch.fnmatch(entry.name, "TXN_*.json"):
                    names.append(entry.name)
                    if len(names) > MAX_PENDING_MARKERS:
                        overflow = True
                        break
        names.sort()
    except Exception as exc:
        raise WorkshopError("TX_DOCUMENT_SYNC_PENDING", "pending transaction directory cannot be inspected",
                            details=[], outcome=PENDING_OUTCOME,
                            references={"status": "UNVERIFIED_DIRECTORY", "reason": type(exc).__name__}) from exc
    if names:
        selected = names[:MAX_PENDING_REFERENCES]
        observations = [_pending_marker_observation(pending / name)[0] for name in selected]
        raise WorkshopError(
            "TX_DOCUMENT_SYNC_PENDING",
            "transaction documentation requires repair before another checkout",
            details=selected,
            outcome=PENDING_OUTCOME,
            references={"total": len(names), "total_exact": not overflow,
                        "omitted": len(names) - len(selected), "omitted_exact": not overflow,
                        "items": observations},
        )


def _sync_scope(workspace: Path, old_metadata: dict[str, Any] | None,
                state: dict[str, Any], *, expected_workspace_id: str | None = None,
                history_control: dict[str, Any] | None = None,
                ) -> tuple[str | None, str | None, str | None, int | None]:
    """Keep historical evidence distinct; never infer ownership from current.json."""
    empty = (None, None, None, None)
    keys = ("goal_id", "milestone_id", "task_id", "loop")
    if history_control and history_control.get("operation") == "INTAKE":
        return empty  # Explicit native quarantine; prior claims survive in its receipt.
    original = state.get("original_scope")
    if original is not None and not isinstance(original, dict):
        raise WorkshopError("TX_DOCUMENT_SCOPE_INVALID", "original scope is not an object")
    original = original or {}
    claims = {key: original[key] for key in keys if original.get(key) not in (None, "")}
    for key, value in claims.items():
        valid = (type(value) is int and value > 0) if key == "loop" else (isinstance(value, str) and bool(value.strip()))
        if not valid:
            raise WorkshopError("TX_DOCUMENT_SCOPE_INVALID", "original scope contains an invalid field",
                                details={"field": key, "value": value})
    recorded_workspace = original.get("workspace_id")
    if (recorded_workspace and expected_workspace_id
            and recorded_workspace != expected_workspace_id):
        raise WorkshopError("TX_DOCUMENT_SCOPE_WORKSPACE_MISMATCH", "original scope belongs to another workspace",
                            details={"recorded_workspace_id": recorded_workspace,
                                     "expected_workspace_id": expected_workspace_id})
    historical = None
    if old_metadata and old_metadata.get("scope_status") != "SCOPE_UNRESOLVED":
        values = tuple(old_metadata.get(key) for key in ("goal", "milestone", "task", "loop"))
        if (all(isinstance(value, str) and value for value in values[:3])
                and type(values[3]) is int and values[3] > 0):
            historical = values
    verified_binding = (history_control and history_control.get("operation") == "BIND"
                        and all(history_control.get("scope", {}).get(key) == original.get(key) for key in _SCOPE_KEYS))
    if old_metadata and old_metadata.get("scope_status") == "SCOPE_UNRESOLVED" and claims and not verified_binding:
        raise WorkshopError(
            "TX_DOCUMENT_SCOPE_REBIND_REQUIRED",
            "unresolved historical ownership requires an explicit verified binding event",
            details={"scope_status": "SCOPE_UNRESOLVED", "proposed_scope": claims},
        )
    if historical:
        recorded = dict(zip(keys, historical))
        conflicts = {key: {"document": recorded[key], "transaction": value}
                     for key, value in claims.items() if value != recorded[key]}
        if conflicts:
            raise WorkshopError(
                "TX_DOCUMENT_SCOPE_CONFLICT",
                "stored transaction scope conflicts with the existing historical document",
                details={"scope_status": "CONFLICT", "conflicts": conflicts,
                         "existing_scope": recorded, "proposed_scope": claims},
            )
    if len(claims) == len(keys):
        return tuple(claims[key] for key in keys)
    return historical or empty




def load_scope_transition(engine: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    """Validate the immutable owner receipt before using a scope transition."""
    from .util import reject_link_components, sha256_bytes
    history = state.get("historical_scope")
    if history is None:
        return None
    if not isinstance(history, dict):
        raise WorkshopError("TX_SCOPE_RECEIPT_INVALID", "historical scope control is not an object")
    txid = state.get("transaction_id", "")
    if not isinstance(txid, str) or len(txid) != 28 or not txid.startswith("TXN_") or any(c not in "0123456789abcdefABCDEF" for c in txid[4:]):
        raise WorkshopError("TX_SCOPE_RECEIPT_INVALID", "scope receipt has an invalid transaction identity")
    digest = history.get("receipt_sha256", "")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise WorkshopError("TX_SCOPE_RECEIPT_INVALID", "historical scope receipt hash is invalid")
    path = engine.config.state_directory / "transaction-scope-history" / state["transaction_id"] / (digest + ".json")
    reject_link_components(path, label="scope transition receipt")
    try:
        if path.stat().st_size > 8 * 1024 * 1024 or path.stat().st_nlink != 1:
            raise ValueError("scope transition receipt exceeds its safe file contract")
        raw = path.read_bytes()
        receipt = json.loads(raw)
        if sha256_bytes(raw) != digest or receipt.get("schema") != "werkfaden-tx-scope-transition/v1":
            raise ValueError("scope transition receipt hash/schema mismatch")
        if (receipt.get("transaction_id") != state["transaction_id"]
                or receipt.get("operation") != history.get("operation")
                or receipt.get("inspection", {}).get("report_sha256") != history.get("report_sha256")):
            raise ValueError("scope transition receipt has different operation or transaction evidence")
        inspection = receipt["inspection"]
        report_body = {key: value for key, value in inspection.items() if key != "report_sha256"}
        if sha256_bytes(json.dumps(report_body, sort_keys=True, separators=(",", ":")).encode("utf-8")) != inspection.get("report_sha256"):
            raise ValueError("historical inspection content does not match its report identity")
        operation = receipt.get("operation")
        if operation not in {"INTAKE", "BIND"} or history.get("scope_status") != ("BOUND" if operation == "BIND" else "SCOPE_UNRESOLVED"):
            raise ValueError("scope transition status/operation mismatch")
        if operation == "BIND" and (receipt["inspection"].get("status") != "VERIFIED" or receipt.get("scope") != receipt["inspection"].get("scope")):
            raise ValueError("binding receipt has no complete verified inspection")
        if operation == "BIND":
            original = state.get("original_scope") or {}
            if (original.get("binding_kind") != "HISTORICAL_VERIFIED" or original.get("source_receipt_sha256") != digest
                    or any(original.get(key) != receipt["scope"].get(key) for key in _SCOPE_KEYS)):
                raise ValueError("bound state differs from its immutable historical scope receipt")
        for source in receipt.get("owner_sources", []):
            if sha256_bytes(source["raw_utf8"].encode("utf-8")) != source["sha256"]:
                raise ValueError("owner source snapshot does not match its receipt")
        previous = receipt["previous_state_utf8"].encode("utf-8")
        if sha256_bytes(previous) != receipt["previous_state_sha256"]:
            raise ValueError("prior state snapshot does not match the receipt")
        admission = receipt.get("legacy_event_format")
        declared = history.get("legacy_event_format_schema")
        if admission is not None:
            from .tx_lifecycle import legacy_event_anchor_admission
            expected = legacy_event_anchor_admission(
                json.loads(previous), inspection, receipt["previous_state_sha256"])
            if (operation != "INTAKE" or admission != expected
                    or declared != expected["schema"]):
                raise ValueError("legacy format admission differs from its exact native receipt")
        elif declared is not None:
            raise ValueError("legacy format admission declaration has no immutable receipt")
        document = receipt.get("previous_document_utf8")
        if document is not None and sha256_bytes(document.encode("utf-8")) != receipt.get("previous_document_sha256"):
            raise ValueError("prior document snapshot does not match the receipt")
        return receipt
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise WorkshopError("TX_SCOPE_RECEIPT_INVALID", str(exc)) from exc


def prepare_scope_transition(engine: Any, transaction_id: str, *, operation: str,
                             expected_report_sha256: str, reason: str) -> tuple[Path, dict[str, Any], bool]:
    """Prepare a terminal metadata transition under the retention writer lock.

    No source from current task state is inferred. Scope assignment is possible
    only from a complete, consistent inspected historical observation.
    """
    from .util import atomic_write_bytes, canonical_json, reject_link_components, sha256_bytes, utc_now
    from .tx_retention import TERMINAL
    if operation not in {"INTAKE", "BIND"}:
        raise WorkshopError("TX_SCOPE_OPERATION_INVALID", "unsupported historical scope operation")
    if not isinstance(reason, str) or not 40 <= len(reason.strip()) <= 2000:
        raise WorkshopError("TX_SCOPE_REASON_INVALID", "scope transition reason requires 40-2000 characters")
    if engine.lease_path.exists() or engine.lease_path.is_symlink():
        raise WorkshopError("TX_SCOPE_LEASE_ACTIVE", "history mutation cannot overlap an active Workshop lease")
    root, state = engine._load_transaction(transaction_id)
    if state.get("state") not in TERMINAL:
        raise WorkshopError("TX_SCOPE_TX_NOT_TERMINAL", "only technically terminal transactions support historical intake/binding")
    previous = (root / "state.json").read_bytes()
    old = load_scope_transition(engine, state)
    if (old and old["operation"] == operation and old["inspection"]["report_sha256"] == expected_report_sha256):
        if old["reason"] != reason.strip():
            raise WorkshopError("TX_SCOPE_REPLAY_CONFLICT", "same transition request has a different reason")
        return root, state, True
    report = inspect_historical_scope(engine, transaction_id)
    if report["report_sha256"] != expected_report_sha256:
        raise WorkshopError("TX_SCOPE_INSPECTION_STALE", "review the exact current historical evidence before mutation")
    legacy_admission = None
    anchor = state.get("tx_event")
    if (operation == "INTAKE" and isinstance(anchor, dict)
            and ("source_epoch_kind" not in anchor or "source_epoch" not in anchor)):
        from .tx_lifecycle import legacy_event_anchor_admission
        try:
            legacy_admission = legacy_event_anchor_admission(state, report, sha256_bytes(previous))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise WorkshopError("TX_LEGACY_EVENT_ADMISSION_INVALID", str(exc)) from exc
    owner_sources = []
    if operation == "BIND":
        if not old or old["operation"] != "INTAKE":
            raise WorkshopError("TX_SCOPE_INTAKE_REQUIRED", "explicit legacy intake must precede binding")
        if report["status"] != "VERIFIED" or not report.get("scope"):
            raise WorkshopError("TX_SCOPE_EVIDENCE_UNRESOLVED", "missing or contradictory historical evidence cannot bind ownership", details=report)
        owner_sources = _validate_historical_owner(engine, report["scope"])
    engine._import_kairos()
    from kairos.transaction_document import document_path
    path = engine.config.kairos_workspace / document_path(transaction_id)
    prior_document = None
    if path.exists():
        reject_link_components(path, label="prior transaction document")
        if path.stat().st_size > 1024 * 1024:
            raise WorkshopError("TX_SCOPE_DOCUMENT_TOO_LARGE", "prior transaction document exceeds snapshot budget")
        prior_document = path.read_bytes()
    receipt = {"schema": "werkfaden-tx-scope-transition/v1", "transaction_id": transaction_id,
               "operation": operation, "inspection": report, "reason": reason.strip(), "created_at": utc_now(),
               "owner_sources": owner_sources,
               "previous_state_utf8": previous.decode("utf-8"), "previous_state_sha256": sha256_bytes(previous),
               "previous_document_utf8": prior_document.decode("utf-8") if prior_document is not None else None,
               "previous_document_sha256": sha256_bytes(prior_document) if prior_document is not None else None,
               "scope": report["scope"] if operation == "BIND" else None,
               "claim_boundary": "Verified local source identities and explicit historical metadata decision; not independent historical execution attestation"}
    if legacy_admission is not None:
        receipt["legacy_event_format"] = legacy_admission
    raw = canonical_json(receipt).encode("utf-8")
    if len(raw) > 8 * 1024 * 1024:
        raise WorkshopError("TX_SCOPE_RECEIPT_TOO_LARGE", "scope transition receipt exceeds its byte budget")
    digest = sha256_bytes(raw)
    target = engine.config.state_directory / "transaction-scope-history" / transaction_id / (digest + ".json")
    reject_link_components(target, label="scope transition receipt")
    if target.exists():
        if target.read_bytes() != raw:
            raise WorkshopError("TX_SCOPE_RECEIPT_CONFLICT", "immutable scope transition receipt changed")
    else:
        atomic_write_bytes(target, raw)
    # Recheck after snapshot creation, before the authoritative state commit.
    if (root / "state.json").read_bytes() != previous or (path.read_bytes() if path.exists() else None) != prior_document:
        raise WorkshopError("TX_SCOPE_SOURCE_CHANGED", "historical source changed during transition preparation")
    for source in owner_sources:
        if sha256_bytes((engine.config.kairos_workspace / source["path"]).read_bytes()) != source["sha256"]:
            raise WorkshopError("TX_SCOPE_OWNER_CHANGED", "Task/Goal source changed during scope transition preparation")
    state["historical_scope"] = {"operation": operation, "receipt_sha256": digest,
        "report_sha256": report["report_sha256"], "report_status": report["status"],
        "scope_status": "BOUND" if operation == "BIND" else "SCOPE_UNRESOLVED",
        "conflict_count": len(report["conflicts"]),
        "evidence_conflict_count": sum(row["status"] == "CONFLICT" for row in report["evidence"]), "reason": reason.strip()}
    if legacy_admission is not None:
        state["historical_scope"]["legacy_event_format_schema"] = legacy_admission["schema"]
    if operation == "BIND":
        state["original_scope"] = {**report["scope"], "binding_kind": "HISTORICAL_VERIFIED",
                                   "bound_at": receipt["created_at"], "source_receipt_sha256": digest}
    return root, state, False


def _validate_historical_owner(engine: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Use SQL as a locator, then verify the actual Task and Goal source bytes."""
    from .util import reject_link_components, sha256_bytes
    from kairos.database import KnowledgeDatabase
    from kairos.workspace import database_path
    from kairos.frontmatter import split_frontmatter
    from kairos.goals import goal_source_paths, validate_goal
    from kairos.util import resolve_workspace_path
    workspace = engine.config.kairos_workspace
    database = KnowledgeDatabase(database_path(workspace))
    connection = database.connect(read_only=True)
    try:
        row = connection.execute("SELECT path FROM artifacts WHERE artifact_id=? AND document_type='task'", (scope["task_id"],)).fetchone()
    finally:
        connection.close()
    if not row:
        raise WorkshopError("TX_SCOPE_TASK_SOURCE_MISSING", "historical Task source has no lookup route")
    path = resolve_workspace_path(workspace, row["path"])
    if Path(row["path"]).parts[0] not in {"tasks", "archive"}:
        raise WorkshopError("TX_SCOPE_TASK_SOURCE_INVALID", "historical Task route is outside Task/archive roots")
    reject_link_components(path, label="historical Task source")
    if path.stat().st_nlink != 1 or path.stat().st_size > 1024 * 1024:
        raise WorkshopError("TX_SCOPE_TASK_SOURCE_INVALID", "historical Task source exceeds its safe snapshot contract")
    raw = path.read_bytes()
    sources = [{"path": path.relative_to(workspace).as_posix(), "sha256": sha256_bytes(raw), "raw_utf8": raw.decode("utf-8")}]

    metadata = split_frontmatter(raw.decode("utf-8")).metadata
    if (metadata.get("type") != "task" or metadata.get("id") != scope["task_id"] or metadata.get("goal") != scope["goal_id"]
            or metadata.get("milestone") != scope["milestone_id"]):
        raise WorkshopError("TX_SCOPE_TASK_OWNER_CONFLICT", "Task source does not support the proposed Goal/Milestone identity")
    goals = []
    for candidate in goal_source_paths(workspace):
        reject_link_components(candidate, label="historical Goal source")
        try:
            candidate.relative_to(workspace / "goals")
        except ValueError as exc:
            raise WorkshopError("TX_SCOPE_GOAL_SOURCE_INVALID", "Goal source escapes the canonical Goal root") from exc
        raw = candidate.read_bytes()
        payload = json.loads(raw)
        if payload.get("id") == scope["goal_id"]:
            validate_goal(payload)
            goals.append(payload)
            sources.append({"path": candidate.relative_to(workspace).as_posix(), "sha256": sha256_bytes(raw), "raw_utf8": raw.decode("utf-8")})
    if len(goals) != 1 or not any(row["id"] == scope["milestone_id"] for row in goals[0]["milestones"]):
        raise WorkshopError("TX_SCOPE_GOAL_SOURCE_CONFLICT", "Goal source does not uniquely support the proposed Milestone")
    return sources


HISTORICAL_SCOPE_TIERS = ("TX_STATE", "GUARDIAN", "TASK_GOAL", "LOOP_ARCHIVE", "TRANSACTION_DOCUMENT")
_SCOPE_KEYS = ("workspace_id", "goal_id", "milestone_id", "task_id", "loop")


def inspect_historical_scope(engine: Any, transaction_id: str) -> dict[str, Any]:
    """Read bounded historical evidence without mutating state or assigning ownership.

    VERIFIED means byte-identified, complete recorded scope with no observed
    contradiction. It is not independent attestation of historical execution.
    Referenced receipts must already be hash-bound in the authoritative TX;
    current.json and current task state are never candidate evidence.

    Optional state.historical_scope_receipts entries contain tier, path and
    raw-file sha256. Paths use forward slashes. TX_STATE receipts live below
    the transaction baseline; TASK_GOAL, LOOP_ARCHIVE and TRANSACTION_DOCUMENT
    receipts live below workspace tasks/goals, archive and reports respectively.
    Their schema is werkfaden-historical-scope-receipt/v1 with transaction_id,
    workspace_id and scope containing all five _SCOPE_KEYS. Guardian evidence
    uses bounded native ledger discovery, never an unvalidated caller path.
    Limits: 64 pinned references, 256 Guardian candidates, 1 MiB per source,
    8 MiB total reads. A truncated search cannot establish complete provenance.
    These receipts are inspected only; this API creates no receipt, modifies no
    scope, and does not implement a verified reassignment or legacy intake.
    """
    import hashlib
    from itertools import islice
    from pathlib import PurePosixPath
    from .util import reject_link_components
    engine._import_kairos()
    from kairos.workspace import load_config
    from kairos.frontmatter import split_frontmatter
    from kairos.transaction_document import document_path
    from .guardian_ledger import GuardianLedgerMixin

    root = engine._transaction_path(transaction_id)
    workspace = engine.config.kairos_workspace
    workspace_id = str(load_config(workspace)["workspace_id"])
    rows: list[dict[str, Any]] = []
    complete = True
    remaining_bytes = 8 * 1024 * 1024

    def read(path: Path) -> tuple[bytes, dict[str, Any]]:
        nonlocal remaining_bytes
        reject_link_components(path, label="historical scope evidence")
        info = path.stat()
        if not path.is_file() or info.st_nlink != 1 or info.st_size > 1024 * 1024 or info.st_size > remaining_bytes:
            raise ValueError("historical evidence must be a single-link regular file within byte budgets")
        raw = path.read_bytes()
        if len(raw) != info.st_size or path.stat().st_mtime_ns != info.st_mtime_ns:
            raise ValueError("historical evidence changed during inspection")
        remaining_bytes -= len(raw)
        return raw, {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    def record(tier: str, scope: Any, source: dict[str, Any], *, eligible: bool, note: str) -> None:
        if not isinstance(scope, dict):
            rows.append({"tier": tier, "status": "CONFLICT", "source": source, "reason": "scope is not an object"})
            return
        claims = {key: scope[key] for key in _SCOPE_KEYS if scope.get(key) not in (None, "")}
        invalid = [key for key, value in claims.items() if not (
            (type(value) is int and value > 0) if key == "loop" else
            (isinstance(value, str) and bool(value.strip())))]
        if invalid or (claims.get("workspace_id") and claims["workspace_id"] != workspace_id):
            rows.append({"tier": tier, "status": "CONFLICT", "scope": claims, "source": source,
                         "reason": "invalid fields or foreign workspace", "invalid_fields": invalid})
            return
        rows.append({"tier": tier, "status": "VERIFIED" if len(claims) == len(_SCOPE_KEYS) and eligible else "MISSING",
                     "scope": claims, "source": source, "binding_eligible": eligible and len(claims) == len(_SCOPE_KEYS),
                     "verification_kind": "SOURCE_IDENTITY_AND_RECORDED_SCOPE_ONLY", "reason": note})

    raw, state_source = read(root / "state.json")
    state = json.loads(raw)
    if not isinstance(state, dict) or state.get("transaction_id") != transaction_id:
        raise WorkshopError("TX_SCOPE_STATE_INVALID", "transaction state has a different identity")
    if state.get("original_scope") is not None:
        record("TX_STATE", state["original_scope"], state_source, eligible=True,
               note="Authoritative transaction records this original scope; historical execution is not independently attested")
    else:
        rows.append({"tier": "TX_STATE", "status": "MISSING", "source": state_source,
                     "reason": "transaction has no recorded original scope"})

    prior_conflicts = []
    try:
        control = load_scope_transition(engine, state)
        if control and control["inspection"].get("status") == "CONFLICT":
            prior_conflicts = control["inspection"].get("conflicts", [])
            rows.append({"tier": "TX_STATE", "status": "CONFLICT", "source": {"receipt_sha256": state["historical_scope"]["receipt_sha256"]},
                         "reason": "previously quarantined conflicting evidence remains unresolved", "historical_conflicts": prior_conflicts})
    except WorkshopError as exc:
        rows.append({"tier": "TX_STATE", "status": "CONFLICT", "reason": str(exc)})
    from .tx_retention import vault_dir, _object_path
    vault = vault_dir(engine)
    try:
        reject_link_components(vault / "manifests", label="historical snapshot discovery")
        manifests = sorted(islice((vault / "manifests").glob(transaction_id + "-*.json"), 65))
        if len(manifests) > 64:
            complete = False
            rows.append({"tier": "TX_STATE", "status": "MISSING", "reason": "native TX snapshot discovery exceeds 64-manifest budget"})
        for candidate in manifests[:64]:
            raw, manifest_source = read(candidate)
            digest = hashlib.sha256(raw).hexdigest()
            manifest = json.loads(raw)
            if (candidate.name != transaction_id + "-" + digest + ".json"
                    or manifest.get("schema") != "werkfaden-transaction-vault/v1"
                    or manifest.get("transaction_id") != transaction_id):
                raise ValueError("native historical snapshot manifest identity mismatch")
            if manifest.get("complete") is not True or "state.json" not in manifest.get("files", {}):
                continue
            fact = manifest["files"]["state.json"]
            hexdigest = fact.get("sha256", "")
            if len(hexdigest) != 64 or any(c not in "0123456789abcdef" for c in hexdigest):
                raise ValueError("native historical snapshot state object hash is invalid")
            raw, source = read(_object_path(vault, hexdigest))
            if source["sha256"] != hexdigest or source["bytes"] != fact.get("size"):
                raise ValueError("native historical state object changed")
            snapshot = json.loads(raw)
            if (snapshot.get("transaction_id") != transaction_id or
                    snapshot.get("baseline_package_sha256") != state.get("baseline_package_sha256")):
                raise ValueError("native historical snapshot has a different TX/baseline")
            source["manifest"] = manifest_source
            if snapshot.get("original_scope"):
                record("TX_STATE", snapshot["original_scope"], source, eligible=True,
                       note="Native complete-TX snapshot and exact state object verified; historical execution is not independently attested")
    except (OSError, ValueError, TypeError, KeyError, WorkshopError) as exc:
        rows.append({"tier": "TX_STATE", "status": "CONFLICT", "reason": str(exc)})
    references = state.get("historical_scope_receipts", [])
    if not isinstance(references, list) or len(references) > 64:
        rows.append({"tier": "TX_STATE", "status": "CONFLICT", "reason": "invalid or over-budget pinned receipt list"})
        references = []
    if any(not isinstance(ref, dict) or ref.get("tier") not in HISTORICAL_SCOPE_TIERS for ref in references):
        rows.append({"tier": "TX_STATE", "status": "CONFLICT", "reason": "receipt list contains an unsupported tier or malformed reference"})
    for tier in HISTORICAL_SCOPE_TIERS:
        for ref in references:
            if not isinstance(ref, dict) or ref.get("tier") != tier:
                continue
            source = {"declared_reference": ref}
            try:
                reference_path = str(ref.get("path") or "")
                relative = PurePosixPath(reference_path)
                if (not reference_path or not relative.parts or relative.is_absolute() or ".." in relative.parts
                        or ":" in reference_path or "\\" in reference_path):
                    raise ValueError("receipt path must be bounded and relative")
                owner = root if tier == "TX_STATE" else workspace
                allowed = {"TX_STATE": {"baseline"}, "GUARDIAN": set(), "TASK_GOAL": {"tasks", "goals"},
                           "LOOP_ARCHIVE": {"archive"}, "TRANSACTION_DOCUMENT": {"reports"}}[tier]
                if relative.parts[0] not in allowed:
                    raise ValueError("receipt is outside its tier's historical source root")
                raw, source = read(owner / Path(*relative.parts))
                if source["sha256"] != ref.get("sha256"):
                    raise ValueError("pinned historical receipt hash mismatch")
                payload = json.loads(raw)
                if (not isinstance(payload, dict) or payload.get("schema") != "werkfaden-historical-scope-receipt/v1"
                        or payload.get("transaction_id") != transaction_id
                        or payload.get("workspace_id") != workspace_id):
                    raise ValueError("historical receipt has unsupported schema or different transaction/workspace")
                scope = payload.get("scope")
                record(tier, scope, source, eligible=True,
                       note="Exact pinned receipt records this transaction scope; hash proves identity, not historical truth")
            except FileNotFoundError as exc:
                complete = False
                rows.append({"tier": tier, "status": "MISSING", "source": source, "reason": str(exc)})
            except (OSError, ValueError, TypeError, KeyError, WorkshopError) as exc:
                rows.append({"tier": tier, "status": "CONFLICT", "source": source, "reason": str(exc)})

        if tier == "GUARDIAN":
            directory = engine.config.state_directory / "guardian-v2"
            try:
                reject_link_components(directory, label="historical Guardian discovery")
                paths = sorted(islice(directory.glob("GRD2_*.json"), 257)) if directory.is_dir() else []
            except WorkshopError as exc:
                complete = False
                paths = []
                rows.append({"tier": tier, "status": "CONFLICT", "reason": str(exc)})
            if len(paths) > 256:
                complete = False
                rows.append({"tier": tier, "status": "MISSING", "reason": "Guardian discovery exceeds 256-file budget; no exhaustive absence claim"})
            for path in paths[:256]:
                related = False
                try:
                    raw, source = read(path)
                    payload = json.loads(raw)
                    records = (payload.get("patch_session") or {}).get("transactions") or []
                    if not any(row.get("transaction_id") == transaction_id for row in records if isinstance(row, dict)):
                        continue
                    related = True
                    if payload.get("schema") != "werkfaden-guardian/v2" or path.stem != payload.get("guardian_id"):
                        raise ValueError("Guardian identity mismatch")
                    GuardianLedgerMixin._verify_chain(None, payload)
                    bound = [event for event in payload.get("events", []) if event.get("kind") == "TX_BIND"
                             and event.get("details", {}).get("transaction_id") == transaction_id]
                    if len(bound) != 1:
                        raise ValueError("Guardian has no unique hash-chained transaction binding")
                    if (state.get("baseline_package_sha256") and
                            bound[0].get("details", {}).get("baseline_package_sha256") != state["baseline_package_sha256"]):
                        rows.append({"tier": tier, "status": "CONFLICT", "source": source,
                                     "reason": "Guardian TX binding names a different baseline package"})
                        continue
                    record(tier, payload.get("project_scope", {}), source, eligible=False,
                           note="Guardian ledger and TX binding checked; legacy project_scope lacks a bound workspace/loop observation and cannot authorize reassignment")
                except (OSError, ValueError, TypeError, KeyError, AttributeError, WorkshopError) as exc:
                    complete = False
                    rows.append({"tier": tier, "status": "CONFLICT" if related else "MISSING", "source": {"path": str(path)}, "reason": str(exc)})
        if tier == "TRANSACTION_DOCUMENT":
            path = workspace / document_path(transaction_id)
            if path.is_file():
                try:
                    raw, source = read(path)
                    metadata = split_frontmatter(raw.decode("utf-8")).metadata
                    if metadata.get("transaction_id") != transaction_id or metadata.get("type") != "transaction":
                        raise ValueError("associated document has a different transaction identity")
                    scope = {"workspace_id": metadata.get("workspace"), "goal_id": metadata.get("goal"),
                             "milestone_id": metadata.get("milestone"), "task_id": metadata.get("task"), "loop": metadata.get("loop")}
                    record(tier, scope, source, eligible=False,
                           note="Existing document declaration is retained as evidence, not promoted to a checkout observation")
                except (OSError, ValueError, TypeError, KeyError, WorkshopError) as exc:
                    rows.append({"tier": tier, "status": "CONFLICT", "source": {"path": str(path)}, "reason": str(exc)})
        if not any(row["tier"] == tier for row in rows):
            rows.append({"tier": tier, "status": "MISSING", "reason": "no pinned historical evidence for this tier"})

    try:
        _, latest_state_source = read(root / "state.json")
        if latest_state_source["sha256"] != state_source["sha256"]:
            rows.append({"tier": "TX_STATE", "status": "CONFLICT", "source": latest_state_source,
                         "reason": "transaction changed during historical discovery"})
    except (OSError, ValueError, WorkshopError) as exc:
        complete = False
        rows.append({"tier": "TX_STATE", "status": "MISSING", "reason": str(exc)})
    ordered = sorted(rows, key=lambda row: HISTORICAL_SCOPE_TIERS.index(row["tier"]))
    observed: dict[str, Any] = {}
    conflicts = list(prior_conflicts)
    for row in ordered:
        for key, value in row.get("scope", {}).items():
            if key in observed and observed[key] != value:
                conflicts.append({"field": key, "first": observed[key], "other": value, "source": row.get("source")})
            else:
                observed[key] = value
    eligible = [row for row in ordered if row.get("binding_eligible")]
    status = "CONFLICT" if conflicts or any(row["status"] == "CONFLICT" for row in rows) else (
        "VERIFIED" if complete and eligible else "MISSING")
    report = {"schema": "werkfaden-historical-scope-inspection/v1", "transaction_id": transaction_id,
              "status": status, "scope_status": "SCOPE_UNRESOLVED" if status != "VERIFIED" else "RECORDED_SCOPE_VERIFIED",
              "scope": dict(eligible[0]["scope"]) if status == "VERIFIED" else None,
              "observed_claims": observed, "conflicts": conflicts, "evidence": ordered,
              "discovery_complete": complete, "automatic_binding": False,
              "claim_boundary": "Read-only identity and recorded-scope consistency inspection; not historical execution attestation, Apply or verified reassignment"}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return report



def ensure_history_contract(engine: Any) -> dict[str, Any]:
    """Publish the single canonical method through its own resumable heartbeat."""
    engine._import_kairos()
    from kairos.database import KnowledgeDatabase
    from kairos.promoter import prepare_document
    from kairos.transaction_document import HISTORY_METHOD_ID, HISTORY_METHOD_PATH, render_history_contract
    from kairos.reconcile import reconcile_documents
    from kairos.workspace import load_config
    from kairos.util import atomic_write_text, sha256_text
    from .util import reject_link_components
    workspace = engine.config.kairos_workspace
    cfg = load_config(workspace)
    if "docs" not in cfg.get("document_roots", []):
        raise WorkshopError("TX_HISTORY_METHOD_UNMANAGED", "canonical history method requires the governed docs source root")
    path = workspace / HISTORY_METHOD_PATH
    reject_link_components(path, label="canonical history method")
    expected = render_history_contract(str(cfg["workspace_id"]))
    if path.exists() and not path.is_file():
        raise WorkshopError("TX_HISTORY_METHOD_DRIFT", "canonical history method must be a regular file")
    if path.is_file():
        observed = path.read_bytes()
        connection = KnowledgeDatabase(engine.config.kairos_database).connect(read_only=True)
        try:
            row = connection.execute("SELECT path,content_sha256,metadata_json FROM artifacts WHERE artifact_id=?", (HISTORY_METHOD_ID,)).fetchone()
        finally:
            connection.close()
        instance_timestamp = observed != expected.encode("utf-8")
        if instance_timestamp:
            try:
                if not row or row["path"] != HISTORY_METHOD_PATH:
                    raise ValueError("canonical method has no native artifact identity")
                stamp = json.loads(row["metadata_json"])["updated_at"]
                if not isinstance(stamp, str) or not 1 <= len(stamp) <= 128:
                    raise ValueError("native method timestamp is invalid")
                expected = render_history_contract(str(cfg["workspace_id"]), updated_at=stamp)
                if observed != expected.encode("utf-8"):
                    raise ValueError("canonical policy bytes differ from the current renderer")
            except (ValueError, TypeError, KeyError) as exc:
                raise WorkshopError("TX_HISTORY_METHOD_DRIFT", "canonical history method differs; a verified document migration is required") from exc
        reconciliation = reconcile_documents(workspace)
        source_pending = any(candidate.resolve() == path.resolve() for candidate in reconciliation.changed)
        if (row and row["path"] == HISTORY_METHOD_PATH
                and row["content_sha256"] == prepare_document(path, workspace).content_sha256 and not source_pending):
            return {"path": HISTORY_METHOD_PATH, "artifact_id": HISTORY_METHOD_ID, "replayed": True}
        if instance_timestamp:
            raise WorkshopError("TX_HISTORY_METHOD_DRIFT", "instance timestamp requires an existing exact reconciled native projection")
    governance, heartbeat = engine._import_kairos()
    permit = governance.issue_external_edit_permit(
        workspace, paths=[HISTORY_METHOD_PATH], reason="Publish the canonical historical transaction intake method",
        ttl_seconds=1800, publication_id="HISTORY_METHOD:" + sha256_bytes(expected.encode("utf-8")),
        expected_sha256={HISTORY_METHOD_PATH: sha256_text(expected)})
    if not path.exists():
        atomic_write_text(path, expected)
    result = heartbeat.run_heartbeat(workspace, requested_mode="verify", changed_paths=[HISTORY_METHOD_PATH],
        use_reconciliation=True, trigger="workshop-history-method", resume_publication_permit_id=permit["permit_id"])
    if not result.get("verified"):
        raise WorkshopError("TX_HISTORY_METHOD_PENDING", "canonical history method projection did not verify", details=result.get("failures"))
    connection = KnowledgeDatabase(engine.config.kairos_database).connect(read_only=True)
    try:
        row = connection.execute("SELECT path,content_sha256 FROM artifacts WHERE artifact_id=?", (HISTORY_METHOD_ID,)).fetchone()
    finally:
        connection.close()
    if not row or row["path"] != HISTORY_METHOD_PATH or row["content_sha256"] != prepare_document(path, workspace).content_sha256:
        raise WorkshopError("TX_HISTORY_METHOD_SQL_MISMATCH", "canonical method has no exact native projection")
    return {"path": HISTORY_METHOD_PATH, "artifact_id": HISTORY_METHOD_ID, "heartbeat_id": result.get("heartbeat_id")}



def assert_projection_lineage(engine: Any, state: dict[str, Any]) -> None:
    """Do not replace an acknowledged event with an unproved restored prefix/fork.

    Receipts and SQL remain derived evidence: they may block an inconsistent
    restore but NEVER authorize reconstructing or overwriting canonical state.
    """
    from .tx_lifecycle import (MAX_INTENT_BYTES, SOURCE_EPOCH_KIND, canonical,
                               event_for_marker, source_epoch, validate_event_chain)
    txid = str(state.get("transaction_id") or "")
    if not txid:
        return
    checkpoint = engine.config.state_directory / "transaction-document-projections" / (txid + ".json")
    proofs: list[dict[str, Any]] = []

    def bounded(path: Path, limit: int) -> bytes | None:
        reject_link_components(path, label="acknowledged projection evidence")
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise WorkshopError("TX_DOCUMENT_ACK_EVIDENCE_UNSAFE",
                "acknowledged projection evidence is not a bounded regular file",
                outcome=PENDING_OUTCOME)
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise WorkshopError("TX_DOCUMENT_ACK_EVIDENCE_UNSAFE",
                "acknowledged projection evidence exceeds its byte bound",
                outcome=PENDING_OUTCOME)
        return raw

    raw = bounded(checkpoint, MAX_INTENT_BYTES)
    if raw is None:
        workspace = engine.config.kairos_workspace
        reject_link_components(workspace, label="acknowledged projection workspace")
        try:
            workspace.lstat()
        except FileNotFoundError:
            # No checkpoint and no workspace means there can be no earlier
            # document/SQL acknowledgement. Do not require a harness binding
            # for a storage-only operation with no publication evidence.
            return
    if raw is not None:
        try:
            ack = json.loads(raw)
            snapshot_raw = ack["source_state_utf8"].encode("utf-8")
            snapshot = json.loads(snapshot_raw)
            validate_event_chain(snapshot)
            event = snapshot["tx_event"]
            valid = (ack["schema"] == "werkfaden-tx-projection/v1"
                and ack["transaction_id"] == snapshot["transaction_id"] == txid
                and sha256_bytes(snapshot_raw) == ack["source_state_sha256"]
                and ack["source_epoch_kind"] == SOURCE_EPOCH_KIND
                and source_epoch(snapshot) == ack["source_epoch"]
                and ack["event_id"] == snapshot["tx_event_id"] == event["event_id"]
                and type(ack["event_sequence"]) is int
                and ack["event_sequence"] == snapshot["tx_event_sequence"] == event["sequence"]
                and isinstance(ack["heartbeat_id"], str) and bool(ack["heartbeat_id"]))
            if valid:
                proofs.append({"kind": "projection_checkpoint", "path": str(checkpoint),
                    "sha256": sha256_bytes(raw), "event_id": event["event_id"],
                    "event": event, "content_sha256": ack["content_sha256"]})
        except (ValueError, TypeError, KeyError, AttributeError):
            # A corrupt derived checkpoint is repairable from actual source and
            # native publication; it cannot become an alternative authority.
            pass

    engine._import_kairos()
    from kairos.constants import MAX_DOCUMENT_BYTES
    from kairos.database import KnowledgeDatabase
    from kairos.frontmatter import split_frontmatter
    from kairos.promoter import prepare_document
    from kairos.transaction_document import document_id, document_path
    document = engine.config.kairos_workspace / document_path(txid)
    document_raw = bounded(document, MAX_DOCUMENT_BYTES)
    if document_raw is not None:
        try:
            metadata = split_frontmatter(document_raw.decode("utf-8")).metadata
            with closing(KnowledgeDatabase(engine.config.kairos_database).connect(read_only=True)) as connection:
                row = connection.execute(
                    "SELECT revision,content_sha256,path,metadata_json FROM artifacts "
                    "WHERE artifact_id=? AND document_type='transaction'", (document_id(txid),)).fetchone()
            projected = json.loads(row["metadata_json"]) if row else {}
            event_id = metadata.get("tx_event_id")
            valid = (row is not None and metadata.get("id") == document_id(txid)
                and metadata.get("type") == "transaction" and metadata.get("transaction_id") == txid
                and isinstance(event_id, str) and bool(re.fullmatch(r"TXE_[A-F0-9]{32}", event_id))
                and projected.get("tx_event_id") == event_id
                and int(row["revision"]) == metadata["revision"]
                and row["path"] == document_path(txid)
                and row["content_sha256"] == prepare_document(document, engine.config.kairos_workspace).content_sha256)
            if valid:
                proofs.append({"kind": "matching_markdown_sql", "path": str(document),
                    "sha256": sha256_bytes(document_raw), "event_id": event_id,
                    "content_sha256": row["content_sha256"]})
        except (ValueError, TypeError, KeyError, AttributeError):
            pass

    for proof in proofs:
        try:
            represented = event_for_marker(state, proof["event_id"])
        except ValueError:
            represented = None
        if represented is not None and ("event" not in proof or canonical(represented) == canonical(proof["event"])):
            continue
        # Only the existing reviewed, immutable legacy INTAKE declaration may
        # explain replacement of an incompatible pre-material event family.
        # Check the exact previous document, not a caller-supplied exemption.
        admitted = False
        if (state.get("tx_event") or {}).get("event") == "TX_HISTORY_INTAKE":
            control = load_scope_transition(engine, state)
            if control and control.get("operation") == "INTAKE" and control.get("legacy_event_format"):
                from kairos.util import sha256_text
                previous_document = control.get("previous_document_utf8")
                admitted = (isinstance(previous_document, str)
                    and sha256_text(previous_document) == proof["content_sha256"])
        if admitted:
            continue
        source_path = engine._transaction_path(txid) / "state.json"
        reference = {key: value for key, value in proof.items() if key != "event"}
        source_raw = bounded(source_path, MAX_INTENT_BYTES)
        actual = json.loads(source_raw) if source_raw is not None else {}
        reference.update(status="ACKNOWLEDGED_HISTORY_NOT_IN_SOURCE",
                         source_path=str(source_path),
                         source_sha256=sha256_bytes(source_raw) if source_raw is not None else None,
                         source_event_id=actual.get("tx_event_id"),
                         source_event_sequence=actual.get("tx_event_sequence"),
                         candidate_event_id=state.get("tx_event_id"),
                         candidate_event_sequence=state.get("tx_event_sequence"))
        raise WorkshopError("TX_DOCUMENT_SOURCE_HISTORY_CONFLICT",
            "authoritative source does not retain the last acknowledged event; "
            "preserve restore evidence and recover verified source authority before retrying native reconcile; "
            "do not reconstruct source from derived SQL/checkpoint",
            details=reference, outcome=PENDING_OUTCOME,
            references={"total": 1, "total_exact": True, "omitted": 0,
                        "omitted_exact": True, "items": [reference]})


def capture_projection_source(engine: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Capture the actual authoritative bytes; never acknowledge a caller-only view."""
    from .tx_lifecycle import SOURCE_EPOCH_KIND, source_epoch
    from .util import canonical_json, reject_link_components, sha256_bytes
    path = engine._transaction_path(str(state["transaction_id"])) / "state.json"
    reject_link_components(path, label="projection source state")
    raw = path.read_bytes()
    persisted = json.loads(raw)
    if canonical_json(persisted) != canonical_json(state):
        raise WorkshopError("TX_DOCUMENT_SOURCE_STATE_MISMATCH", "provided transaction differs from its persisted authoritative state")
    from .tx_lifecycle import validate_event_chain
    validate_event_chain(persisted)
    epoch = source_epoch(persisted)
    identity = persisted.get("tx_event") or {}
    if identity.get("source_epoch") and identity["source_epoch"] != epoch:
        raise WorkshopError("TX_DOCUMENT_SOURCE_EVENT_STALE", "material source changed without a matching lifecycle event")
    return {"source_epoch": epoch, "source_epoch_kind": SOURCE_EPOCH_KIND,
            "source_state_sha256": sha256_bytes(raw), "source_state_utf8": raw.decode("utf-8")}


def assert_projection_source_unchanged(engine: Any, transaction_id: str, captured: dict[str, Any]) -> None:
    from .util import sha256_bytes
    path = engine._transaction_path(transaction_id) / "state.json"
    if sha256_bytes(path.read_bytes()) != captured["source_state_sha256"]:
        raise WorkshopError("TX_DOCUMENT_SOURCE_CHANGED", "authoritative state changed before the projection acknowledgement")


def _chunk_sources(engine: Any, state: dict[str, Any], workspace_id: str) -> dict[str, str]:
    """Resolve only native receipt-bound subordinate sources; never adopt drift."""
    from kairos.transaction_document import recycling_chunk_identity, render_recycling_chunk
    from kairos.constants import MAX_DOCUMENT_BYTES
    from .tx_recycling import _canonical, _chunk_path
    owner = state.get("recycling") if (state.get("recycling") or {}).get("chunks") else state.get("recycling_progress")
    if not owner:
        return {}
    result = {}
    for item in owner["chunks"]:
        receipt = _chunk_path(engine._transaction_path(state["transaction_id"]), owner["inventory_sha256"], item["chunk_index"])
        raw = receipt.read_bytes()
        packet = json.loads(raw)
        if (raw != _canonical(packet) or sha256_bytes(raw) != item["receipt_sha256"]
                or packet["transaction_id"] != state["transaction_id"]
                or packet["inventory_sha256"] != owner["inventory_sha256"]
                or packet["decisions"] != item["decisions"] or packet["range"] != item["range"]
                or packet["chunk_sha256"] != item["chunk_sha256"]):
            raise WorkshopError("TX_RECYCLING_CHUNK_CORRUPT", "subordinate source differs from its immutable packet")
        _, relative = recycling_chunk_identity(state["transaction_id"], owner["inventory_sha256"], item["chunk_index"])
        text = render_recycling_chunk(transaction_id=state["transaction_id"], workspace_id=workspace_id,
                                      packet=packet, receipt_sha256=item["receipt_sha256"])
        if sha256_bytes(text.encode("utf-8")) != item["document_sha256"] or len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise WorkshopError("TX_RECYCLING_CHUNK_SOURCE_DRIFT", "chunk source recipe or unchanged byte budget differs")
        result[relative] = text
    return result


def _assert_chunk_projection(engine: Any, state: dict[str, Any]) -> None:
    from kairos.workspace import load_config
    owner = state.get("recycling") if (state.get("recycling") or {}).get("chunks") else state.get("recycling_progress")
    if not owner:
        return
    sources = _chunk_sources(engine, state, str(load_config(engine.config.kairos_workspace)["workspace_id"]))
    with closing(sqlite3.connect(engine.config.kairos_database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM tx_recycling_chunks WHERE transaction_id=? AND inventory_sha256=? ORDER BY chunk_index",
            (state["transaction_id"], owner["inventory_sha256"])).fetchall()
    if len(rows) != len(owner["chunks"]):
        raise WorkshopError("TX_RECYCLING_CHUNK_SQL_PENDING", "partial chunk coverage has no exact source/SQL projection")
    for item, row in zip(owner["chunks"], rows):
        relative = row["source_path"]
        path = engine.config.kairos_workspace / relative
        reject_link_components(path, label="recycling chunk source")
        if (relative not in sources or not path.is_file() or path.read_bytes() != sources[relative].encode("utf-8")
                or row["source_sha256"] != item["document_sha256"]
                or row["chunk_index"] != item["chunk_index"]
                or [row["range_start"], row["range_stop"]] != item["range"]
                or row["chunk_sha256"] != item["chunk_sha256"]
                or row["receipt_sha256"] != item["receipt_sha256"]
                or row["decision_count"] != len(item["decisions"])
                or json.loads(row["decisions_json"]) != item["decisions"]):
            raise WorkshopError("TX_RECYCLING_CHUNK_SQL_PENDING", "chunk source/SQL bytes differ from the accepted immutable record")


def _ensure_chunk_projection_schema(database: Any) -> None:
    """Native additive initialization, only on the explicit publication path."""
    with closing(database.connect(read_only=True)) as connection:
        available = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('tx_recycling_chunks','tx_recycling_chunk_atoms')")}
    if available != {"tx_recycling_chunks", "tx_recycling_chunk_atoms"}:
        database.initialize()


def _publish_chunk_sources(engine: Any, state: dict[str, Any], workspace_id: str,
                            captured: dict[str, Any]) -> None:
    """Publish bounded child batches before their parent; preserve the TX outbox."""
    from kairos.database import KnowledgeDatabase
    from kairos.heartbeat import run_heartbeat
    from kairos.util import atomic_write_text, sha256_text
    sources = _chunk_sources(engine, state, workspace_id)
    if not sources:
        return
    workspace = engine.config.kairos_workspace
    pending = []
    database = KnowledgeDatabase(engine.config.kairos_database)
    # Keep additive migration out of read-only assertions and do it before
    # the first lookup, including when resuming a legacy-schema outbox.
    _ensure_chunk_projection_schema(database)
    with closing(database.connect(read_only=True)) as connection:
        for relative, text in sources.items():
            path = workspace / relative
            reject_link_components(path, label="recycling chunk source")
            if path.exists() and (not path.is_file() or path.stat().st_nlink != 1 or path.read_bytes() != text.encode("utf-8")):
                raise WorkshopError("TX_RECYCLING_CHUNK_SOURCE_DRIFT", "immutable subordinate source differs; do not overwrite it")
            row = connection.execute("SELECT source_sha256 FROM tx_recycling_chunks WHERE source_path=?", (relative,)).fetchone()
            if not path.exists() or row is None or row["source_sha256"] != sha256_text(text):
                pending.append(relative)
    governance, _ = engine._import_kairos()
    for start in range(0, len(pending), 32):
        paths = pending[start:start + 32]
        hashes = {relative: sha256_text(sources[relative]) for relative in paths}
        identity = sha256_bytes(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        permit = governance.issue_external_edit_permit(
            workspace, paths=paths, reason="Publish immutable subordinate recycling evidence without review clearance",
            ttl_seconds=1800, publication_id=_publication_identity(state["transaction_id"],
                str(state.get("tx_event_id") or ""), identity, governance.runtime_scope(workspace)),
            expected_sha256=hashes)
        for relative in paths:
            path = workspace / relative
            if not path.exists():
                atomic_write_text(path, sources[relative])
        result = run_heartbeat(workspace, requested_mode="verify", changed_paths=paths,
                               use_reconciliation=False, trigger="workshop-recycling-chunk-sources",
                               resume_publication_permit_id=permit["permit_id"])
        if not result.get("verified"):
            raise WorkshopError("TX_RECYCLING_CHUNK_SQL_PENDING", "bounded subordinate source heartbeat failed",
                                details=result.get("failures"))
        assert_projection_source_unchanged(engine, state["transaction_id"], captured)
    _assert_chunk_projection(engine, state)


def publish(engine: Any, state: dict[str, Any]) -> dict[str, Any]:
    # Import through the project's configured harness binding. No second DB writer.
    engine._import_kairos()
    from kairos.database import KnowledgeDatabase
    from kairos.frontmatter import split_frontmatter
    from kairos.heartbeat import run_heartbeat
    from kairos.promoter import prepare_document
    from kairos.transaction_document import document_id, document_path, render_transaction_document
    from kairos.util import atomic_write_text, sha256_text, utc_now
    from kairos.workspace import load_config

    txid = str(state["transaction_id"])
    workspace = engine.config.kairos_workspace
    relative = document_path(txid)
    path = workspace / relative
    cfg = load_config(workspace)
    if "reports" not in cfg.get("document_roots", []):
        raise WorkshopError("TX_DOCUMENT_ROOT_UNMANAGED", "reports/ must be a managed KAIROS source root")
    old = None
    existing_text = None
    revision = 1
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise WorkshopError("TX_DOCUMENT_PATH_UNSAFE", "transaction document source is not a regular file")
        existing_text = path.read_text(encoding="utf-8")
        old = split_frontmatter(existing_text).metadata
        if old["id"] != document_id(txid) or old["type"] != "transaction" or old.get("transaction_id") != txid or old.get("workspace") != cfg["workspace_id"]:
            raise WorkshopError("TX_DOCUMENT_IDENTITY_MISMATCH", "existing transaction document does not match this transaction/workspace identity")
        revision = int(old["revision"]) + 1
    control = load_scope_transition(engine, state)
    goal, milestone, task, loop = _sync_scope(workspace, old, state, expected_workspace_id=str(cfg["workspace_id"]), history_control=control)
    captured = capture_projection_source(engine, state)
    assert_projection_lineage(engine, state)
    if control:
        ensure_history_contract(engine)
    _publish_chunk_sources(engine, state, str(cfg["workspace_id"]), captured)
    # A diagnostic-only save may change journal/time bytes without changing
    # the material projection. Keep the previously acknowledged full snapshot.
    if old and (old.get("tx_event_id") or "") == (state.get("tx_event_id") or ""):
        try:
            assert_projected(engine, txid, ignore_pending=True)
        except WorkshopError:
            pass
        else:
            assert_projection_source_unchanged(engine, txid, captured)
            checkpoint = engine.config.state_directory / "transaction-document-projections" / (txid + ".json")
            proven = json.loads(checkpoint.read_text(encoding="utf-8"))
            return {"artifact_id": document_id(txid), "path": relative, "revision": old["revision"],
                    "sha256": prepare_document(path, workspace).content_sha256,
                    "heartbeat_id": proven["heartbeat_id"], "source_epoch": proven["source_epoch"],
                    "source_state_sha256": proven["source_state_sha256"], "replayed": True}
    render_timestamp = str(state.get("updated_at") or state.get("created_at") or (old or {}).get("updated_at") or utc_now())
    text = render_transaction_document(
        transaction=state, workspace_id=str(cfg["workspace_id"]),
        goal_id=goal, milestone_id=milestone, task_id=task, loop=loop,
        revision=revision, updated_at=render_timestamp,
        task_reference_exists=(bool(task) and (workspace / "tasks" / f"task_{task}.md").is_file()),
    )
    # Reconciliation of an already persisted event must not invent a new
    # source revision. Render the same revision and require exact source
    # equality; changed evidence still takes the normal revision path.
    replay = False
    if old and (old.get("tx_event_id") or "") == (state.get("tx_event_id") or ""):
        same_revision = render_transaction_document(
            transaction=state, workspace_id=str(cfg["workspace_id"]),
            goal_id=goal, milestone_id=milestone, task_id=task, loop=loop,
            revision=int(old["revision"]), updated_at=render_timestamp,
            task_reference_exists=(bool(task) and (workspace / "tasks" / f"task_{task}.md").is_file()),
        )
        if same_revision == existing_text:
            revision = int(old["revision"])
            text = same_revision
            replay = True
            # A durable pending marker or a missing checkpoint still needs
            # recovery below. A fully projected replay is a read-only no-op.
            try:
                assert_projected(engine, txid)
            except WorkshopError:
                pass
            else:
                checkpoint = engine.config.state_directory / "transaction-document-projections" / (txid + ".json")
                proven = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.is_file() else {}
                return {"artifact_id": document_id(txid), "path": relative,
                        "revision": revision, "sha256": prepare_document(path, workspace).content_sha256,
                        "heartbeat_id": proven.get("heartbeat_id"), "replayed": True}

    # Verify document grammar, answers and references BEFORE requesting write authority.
    # The document is generated in the KAIROS template module and has fixed stable IDs.
    from kairos.frontmatter import split_frontmatter as parse_header
    from kairos.sections import parse_sections, validate_answer_targets
    parsed = parse_header(text)
    sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
    validate_answer_targets(parsed.metadata, sections)

    governance, _ = engine._import_kairos()
    intended_sha256 = sha256_text(text)
    publication_permit = governance.issue_external_edit_permit(
        workspace, paths=[relative], reason=f"Workshop lifecycle {txid}: {state.get('state', 'unknown')}",
        ttl_seconds=1800,
        publication_id=_publication_identity(
            txid, str(state.get("tx_event_id") or ""), intended_sha256,
            governance.runtime_scope(workspace),
        ),
        expected_sha256={relative: intended_sha256},
    )
    if not replay:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
    receipt = run_heartbeat(
        workspace, requested_mode="verify", changed_paths=[relative],
        use_reconciliation=True, trigger=f"workshop-transaction-document:{txid}",
        resume_publication_permit_id=publication_permit["permit_id"],
    )
    if not receipt.get("verified"):
        raise WorkshopError("TX_DOCUMENT_PROJECTION_FAILED", "heartbeat did not verify the transaction document: " + str(receipt.get("failures", []))[:1000] + "; missing=" + str(receipt.get("missing_sources", []))[:300], details=receipt.get("failures"))
    expected = prepare_document(path, workspace).content_sha256
    db = KnowledgeDatabase(engine.config.kairos_database)
    with closing(db.connect(read_only=True)) as connection:
        row = connection.execute(
            "SELECT revision,content_sha256,path FROM artifacts WHERE artifact_id=? AND document_type='transaction'",
            (document_id(txid),),
        ).fetchone()
    if not row or int(row["revision"]) != revision or row["content_sha256"] != expected or row["path"] != relative:
        raise WorkshopError("TX_DOCUMENT_SQL_MISMATCH", "transaction document was not projected as the exact expected revision")
    # One replaceable projection checkpoint per TX, not a receipt file per
    # heartbeat. The authoritative event itself remains in state.json.
    assert_projection_source_unchanged(engine, txid, captured)
    checkpoint = engine.config.state_directory / "transaction-document-projections" / (txid + ".json")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(checkpoint, {
            "schema": "werkfaden-tx-projection/v1",
            "transaction_id": txid,
            "event_id": state.get("tx_event_id") or "",
            "event_sequence": state.get("tx_event_sequence"),
            "document_revision": revision,
            "content_sha256": expected,
            "heartbeat_id": receipt.get("heartbeat_id"),
            **captured,
        })
    return {"artifact_id": document_id(txid), "path": relative, "revision": revision, "sha256": expected,
            "heartbeat_id": receipt.get("heartbeat_id"), "source_epoch": captured["source_epoch"],
            "source_state_sha256": captured["source_state_sha256"], "replayed": replay}


def pending_identity_matches(record: dict[str, Any], identity: dict[str, Any]) -> bool:
    view = {"transaction_id": identity["transaction_id"], "state": identity["state_after"],
            "tx_event": identity, "tx_event_id": identity["event_id"], "tx_event_sequence": identity["sequence"]}
    expected = pending_record(view, event=str(identity.get("event") or ""), reason="")
    keys = ("schema", "transaction_id", "event_id", "event_sequence", "state", "candidate_sha256",
            "baseline_sha256", "scope_receipt_sha256", "pending")
    if any(type(record.get(k)) is not type(expected[k]) or record.get(k) != expected[k] for k in keys):
        return False
    return not (identity.get("event") == "TX_RECYCLE_REVIEWED" or record.get("event") == "TX_RECYCLE_REVIEWED") or record.get("event") == expected["event"]


def acknowledge_pending_marker(engine: Any, state: dict[str, Any],
                               observed: dict[str, Any] | None) -> None:
    """Only the exact observed marker and a proved represented event may retire."""
    from .tx_lifecycle import event_for_marker
    txid = str(state["transaction_id"])
    marker = pending_path(engine, txid)
    try:
        marker.lstat()
    except FileNotFoundError:
        if observed is None:
            return
        after = {"transaction_id": txid, "status": "MISSING"}
        record = None
    else:
        after, record = _pending_marker_observation(marker)
    identity = event_for_marker(state, str((record or {}).get("event_id") or ""))
    if (observed is None or after.get("status") != "OBSERVED"
            or observed.get("marker_sha256") != after.get("marker_sha256")
            or identity is None or not pending_identity_matches(record, identity)):
        raise WorkshopError("TX_DOCUMENT_SYNC_PENDING", "pending marker changed or has no exact native event proof",
                            outcome=PENDING_OUTCOME, details=[marker.name],
                            references={"total": 1, "total_exact": True, "omitted": 0,
                                        "omitted_exact": True, "items": [after]})
    marker.unlink()


def update_document(engine: Any, state: dict[str, Any], *, event: str) -> None:
    from .tx_retention import _retention_lock
    from .tx_lifecycle import acknowledge_event_intents
    txid = str(state.get("transaction_id", ""))
    if not txid:
        return
    with _retention_lock(engine):
        marker = pending_path(engine, txid)
        try:
            marker.lstat()
        except FileNotFoundError:
            observed = None
        else:
            observed, _ = _pending_marker_observation(marker)
        try:
            publish(engine, state)
            assert_projected(engine, txid, ignore_pending=True)
            acknowledge_pending_marker(engine, state, observed)
            acknowledge_event_intents(engine, state)
        except Exception as exc:
            # Never replace a newer, malformed or unrelated obligation merely
            # because publication of this event failed. Its native intent stays.
            try:
                marker.lstat()
            except FileNotFoundError:
                marker.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(marker, pending_record(
                    state, event=event, reason="lifecycle document publication failed", error=exc))
            else:
                current, _ = _pending_marker_observation(marker)
                if (observed is not None and current.get("status") == "OBSERVED"
                        and current.get("marker_sha256") == observed.get("marker_sha256")
                        and current.get("event_id") == state.get("tx_event_id")):
                    atomic_write_json(marker, pending_record(
                        state, event=event, reason="lifecycle document publication failed", error=exc))
            return


def assert_projected(engine: Any, transaction_id: str, *, ignore_pending: bool = False) -> None:
    marker = pending_path(engine, transaction_id)
    try:
        marker.lstat()
        marker_exists = True
    except FileNotFoundError:
        marker_exists = False
    except OSError:
        marker_exists = True
    if marker_exists and not ignore_pending:
        observed, pending = _pending_marker_observation(marker)
        details = pending if pending is not None else {"marker": marker.name, "status": "UNVERIFIED"}
        raise WorkshopError(
            "TX_DOCUMENT_SYNC_PENDING",
            "Workshop transaction document projection is pending; do not repeat Apply. "
            + str(details.get("code")) + ": " + str(details.get("message")),
            details=details, outcome=PENDING_OUTCOME,
            references={"total": 1, "total_exact": True, "omitted": 0,
                        "omitted_exact": True, "items": [observed]},
        )
    # The persisted source and SQL projection must match the actual terminal
    # state, not just share an artifact identity from an earlier heartbeat.
    # Resolve KAIROS through the configured Workshop binding on this entry path.
    engine._import_kairos()
    from kairos.transaction_document import document_path, document_id
    from kairos.promoter import prepare_document
    from kairos.frontmatter import split_frontmatter

    path = engine.config.kairos_workspace / document_path(transaction_id)
    if not path.is_file() or path.is_symlink():
        raise WorkshopError("TX_DOCUMENT_MISSING", "transaction lifecycle document not found")
    _, state = engine._load_transaction(transaction_id)
    metadata = split_frontmatter(path.read_text(encoding="utf-8")).metadata
    if state.get("tx_event_id") and metadata.get("tx_event_id") != state["tx_event_id"]:
        raise WorkshopError("TX_DOCUMENT_EVENT_STALE",
                            "document does not bind the latest authoritative TX event")
    if (metadata.get("id") != document_id(transaction_id)
            or metadata.get("transaction_id") != transaction_id
            or metadata.get("workshop_state") != state.get("state")
            or metadata.get("type") != "transaction"):
        raise WorkshopError("TX_DOCUMENT_STALE", "transaction Markdown does not describe current Workshop state")
    compaction_sha = str((state.get("compaction") or {}).get("manifest_sha256") or "")
    if compaction_sha:
        from kairos.sections import parse_sections
        from kairos.frontmatter import split_frontmatter
        parsed = split_frontmatter(path.read_text(encoding="utf-8"))
        sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
        output = next((part.body for part in sections if part.section_id == "s-output"), "")
        if compaction_sha not in output or "Compaction:" not in output:
            raise WorkshopError("TX_DOCUMENT_COMPACTION_STALE",
                                "transaction Markdown omits the current persisted archive receipt")
    prepared = prepare_document(path, engine.config.kairos_workspace)
    with closing(sqlite3.connect(engine.config.kairos_database)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT revision,content_sha256,path,metadata_json FROM artifacts WHERE artifact_id=? AND document_type='transaction'",
            (document_id(transaction_id),),
        ).fetchone()
    if (row is None or int(row["revision"]) != metadata["revision"]
            or row["content_sha256"] != prepared.content_sha256
            or row["path"] != document_path(transaction_id)
            or json.loads(row["metadata_json"]).get("workshop_state") != state.get("state")
            or json.loads(row["metadata_json"]).get("tx_event_id", "") != (state.get("tx_event_id") or "")):
        raise WorkshopError("TX_DOCUMENT_NOT_PROJECTED", "exact transaction revision and lifecycle state are not in KAIROS SQL")

    from .tx_lifecycle import SOURCE_EPOCH_KIND, source_epoch
    from .util import sha256_bytes
    checkpoint = engine.config.state_directory / "transaction-document-projections" / (transaction_id + ".json")
    try:
        proven = json.loads(checkpoint.read_text(encoding="utf-8"))
        snapshot_raw = proven["source_state_utf8"].encode("utf-8")
        snapshot = json.loads(snapshot_raw)
        valid_source = (sha256_bytes(snapshot_raw) == proven["source_state_sha256"]
                        and proven["source_epoch_kind"] == SOURCE_EPOCH_KIND
                        and source_epoch(snapshot) == proven["source_epoch"] == source_epoch(state)
                        and (snapshot.get("tx_event_id") or "") == (state.get("tx_event_id") or "")
                        and snapshot.get("tx_event_sequence") == state.get("tx_event_sequence"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        proven, valid_source = {}, False
    if (not valid_source or proven.get("event_id") != (state.get("tx_event_id") or "")
            or proven.get("document_revision") != metadata["revision"]
            or proven.get("content_sha256") != prepared.content_sha256
            or not proven.get("heartbeat_id")):
        raise WorkshopError("TX_DOCUMENT_OUTBOX_UNACKNOWLEDGED",
                            "current material source has no matching full-state Markdown/SQL projection checkpoint")
    review = state.get("recycling") if isinstance(state.get("recycling"), dict) else {}
    _assert_chunk_projection(engine, state)
    if review.get("status") == "REVIEWED":
        with closing(sqlite3.connect(engine.config.kairos_database)) as con:
            con.row_factory = sqlite3.Row
            evidence = con.execute(
                "SELECT inventory_sha256,analysis_sha256,decision_count,document_revision "
                "FROM tx_recycling_reviews WHERE artifact_id=?",
                (document_id(transaction_id),),
            ).fetchone()
        if (evidence is None
                or evidence["inventory_sha256"] != review.get("inventory_sha256")
                or evidence["analysis_sha256"] != review.get("analysis_sha256")
                or evidence["decision_count"] != len(review.get("decisions", []))
                or evidence["document_revision"] != metadata["revision"]):
            raise WorkshopError("TX_RECYCLING_SQL_NOT_PROJECTED",
                                "KAIROS SQL recycling decisions are missing or not bound to the current TX document")
