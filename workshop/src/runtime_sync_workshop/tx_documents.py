"""Governed TX lifecycle document publication through KAIROS heartbeat.

A TX markdown source is kept in the existing reports/ document root, outside
disposable transaction worktrees. Any publishing failure is recorded in a
durable pending marker. It must never transform successful Apply into a
second attempt or prevent a safe Abort/Recovery.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json


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


def assert_no_pending(engine: Any) -> None:
    pending = engine.config.state_directory / "transaction-document-pending"
    if pending.is_dir():
        failures = sorted(path.name for path in pending.glob("TXN_*.json") if path.is_file())
        if failures:
            raise WorkshopError(
                "TX_DOCUMENT_SYNC_PENDING",
                "transaction documentation requires repair before another checkout",
                details=failures[:20],
            )


def _sync_scope(workspace: Path, old_metadata: dict[str, Any] | None,
                state: dict[str, Any], *, expected_workspace_id: str | None = None,
                ) -> tuple[str | None, str | None, str | None, int | None]:
    """Keep historical evidence distinct; never infer ownership from current.json."""
    empty = (None, None, None, None)
    keys = ("goal_id", "milestone_id", "task_id", "loop")
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
    if old_metadata and old_metadata.get("scope_status") == "SCOPE_UNRESOLVED" and claims:
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
    goal, milestone, task, loop = _sync_scope(workspace, old, state, expected_workspace_id=str(cfg["workspace_id"]))
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
    if old and old.get("tx_event_id") == state.get("tx_event_id"):
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
    publication_permit = governance.issue_external_edit_permit(
        workspace, paths=[relative], reason=f"Workshop lifecycle {txid}: {state.get('state', 'unknown')}",
        ttl_seconds=1800,
        publication_id=f"{txid}:{state.get('tx_event_id') or sha256_text(text)}",
        expected_sha256={relative: sha256_text(text)},
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
    with db.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT revision,content_sha256,path FROM artifacts WHERE artifact_id=? AND document_type='transaction'",
            (document_id(txid),),
        ).fetchone()
    if not row or int(row["revision"]) != revision or row["content_sha256"] != expected or row["path"] != relative:
        raise WorkshopError("TX_DOCUMENT_SQL_MISMATCH", "transaction document was not projected as the exact expected revision")
    # One replaceable projection checkpoint per TX, not a receipt file per
    # heartbeat. The authoritative event itself remains in state.json.
    if state.get("tx_event_id"):
        checkpoint = engine.config.state_directory / "transaction-document-projections" / (txid + ".json")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(checkpoint, {
            "schema": "werkfaden-tx-projection/v1",
            "transaction_id": txid,
            "event_id": state["tx_event_id"],
            "event_sequence": state.get("tx_event_sequence"),
            "document_revision": revision,
            "content_sha256": expected,
            "heartbeat_id": receipt.get("heartbeat_id"),
        })
    return {"artifact_id": document_id(txid), "path": relative, "revision": revision, "sha256": expected,
            "heartbeat_id": receipt.get("heartbeat_id"), "replayed": replay}


def update_document(engine: Any, state: dict[str, Any], *, event: str) -> None:
    txid = str(state.get("transaction_id", ""))
    if not txid:
        return
    marker = pending_path(engine, txid)
    try:
        result = publish(engine, state)
    except Exception as exc:
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, pending_record(
            state, event=event, reason="lifecycle document publication failed", error=exc,
        ))
        return
    if marker.is_file():
        marker.unlink()


def assert_projected(engine: Any, transaction_id: str) -> None:
    marker = pending_path(engine, transaction_id)
    if marker.is_file():
        pending = json.loads(marker.read_text(encoding="utf-8"))
        raise WorkshopError(
            "TX_DOCUMENT_SYNC_PENDING",
            "Workshop transaction document projection is pending; do not repeat Apply. "
            + str(pending.get("code")) + ": " + str(pending.get("message")),
            details=pending,
        )
    # The persisted source and SQL projection must match the actual terminal
    # state, not just share an artifact identity from an earlier heartbeat.
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
    with sqlite3.connect(engine.config.kairos_database) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT revision,content_sha256,path,metadata_json FROM artifacts WHERE artifact_id=? AND document_type='transaction'",
            (document_id(transaction_id),),
        ).fetchone()
    if (row is None or int(row["revision"]) != metadata["revision"]
            or row["content_sha256"] != prepared.content_sha256
            or row["path"] != document_path(transaction_id)
            or json.loads(row["metadata_json"]).get("workshop_state") != state.get("state")):
        raise WorkshopError("TX_DOCUMENT_NOT_PROJECTED", "exact transaction revision and lifecycle state are not in KAIROS SQL")

    if state.get("tx_event_id"):
        checkpoint = engine.config.state_directory / "transaction-document-projections" / (transaction_id + ".json")
        try:
            proven = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proven = {}
        if (proven.get("event_id") != state.get("tx_event_id")
                or proven.get("document_revision") != metadata["revision"]
                or proven.get("content_sha256") != prepared.content_sha256):
            raise WorkshopError("TX_DOCUMENT_OUTBOX_UNACKNOWLEDGED",
                                "current event has no matching durable Markdown/SQL projection checkpoint")
    review = state.get("recycling") if isinstance(state.get("recycling"), dict) else {}
    if review.get("status") == "REVIEWED":
        with sqlite3.connect(engine.config.kairos_database) as con:
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
