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


EVENTS = {
    "CHECKOUT_COMPLETED", "TRANSACTION_ABORTED", "POSTCHECK_VERIFIED",
    "RECOVERY_POSTCHECK_VERIFIED", "APPLY_ROLLED_BACK",
    "RECOVERY_REQUIRED", "ROLLBACK_INCOMPLETE",
    "SOURCE_SET_CHECKOUT", "AUTHORITY_CHECKOUT", "AUXILIARY_CHECKOUT",
    "SOURCE_SET_POSTCHECK_VERIFIED", "AUTHORITY_POSTCHECK_VERIFIED",
    "AUXILIARY_POSTCHECK_VERIFIED",
}


def pending_path(engine: Any, transaction_id: str) -> Path:
    return engine.config.state_directory / "transaction-document-pending" / (transaction_id + ".json")


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


def _sync_scope(workspace: Path, old_metadata: dict[str, Any] | None) -> tuple[str, str, str, int]:
    if old_metadata:
        return (str(old_metadata["goal"]), str(old_metadata["milestone"]),
                str(old_metadata["task"]), int(old_metadata["loop"]))
    current_path = workspace / "current.json"
    if not current_path.is_file():
        raise WorkshopError("TX_SCOPE_MISSING", "KAIROS current.json is unavailable")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    scope = (current.get("active_goal"), current.get("active_milestone"), current.get("active_task"))
    if not all(isinstance(value, str) and value for value in scope):
        raise WorkshopError("TX_SCOPE_MISSING", "KAIROS scope is incomplete for transaction document")
    loop = current.get("loop")
    if type(loop) is not int or loop < 1:
        raise WorkshopError("TX_SCOPE_MISSING", "current loop is missing from KAIROS scope")
    return scope[0], scope[1], scope[2], loop


def publish(engine: Any, state: dict[str, Any]) -> dict[str, Any]:
    # Import through the project's configured harness binding. No second DB writer.
    engine._import_kairos()
    from kairos.database import KnowledgeDatabase
    from kairos.frontmatter import split_frontmatter
    from kairos.heartbeat import run_heartbeat
    from kairos.promoter import prepare_document
    from kairos.transaction_document import document_id, document_path, render_transaction_document
    from kairos.util import atomic_write_text, sha256_text
    from kairos.workspace import load_config

    txid = str(state["transaction_id"])
    workspace = engine.config.kairos_workspace
    relative = document_path(txid)
    path = workspace / relative
    cfg = load_config(workspace)
    if "reports" not in cfg.get("document_roots", []):
        raise WorkshopError("TX_DOCUMENT_ROOT_UNMANAGED", "reports/ must be a managed KAIROS source root")
    old = None
    revision = 1
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise WorkshopError("TX_DOCUMENT_PATH_UNSAFE", "transaction document source is not a regular file")
        old = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        if old["id"] != document_id(txid) or old["type"] != "transaction" or old.get("transaction_id") != txid:
            raise WorkshopError("TX_DOCUMENT_IDENTITY_MISMATCH", "existing transaction document belongs to another TX")
        revision = int(old["revision"]) + 1
    goal, milestone, task, loop = _sync_scope(workspace, old)
    text = render_transaction_document(
        transaction=state, workspace_id=str(cfg["workspace_id"]),
        goal_id=goal, milestone_id=milestone, task_id=task, loop=loop,
        revision=revision,
        task_reference_exists=(workspace / "tasks" / f"task_{task}.md").is_file(),
    )
    # Verify document grammar, answers and references BEFORE requesting write authority.
    # The document is generated in the KAIROS template module and has fixed stable IDs.
    from kairos.frontmatter import split_frontmatter as parse_header
    from kairos.sections import parse_sections, validate_answer_targets
    parsed = parse_header(text)
    sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
    validate_answer_targets(parsed.metadata, sections)

    governance, _ = engine._import_kairos()
    governance.issue_external_edit_permit(
        workspace, paths=[relative], reason=f"Workshop lifecycle {txid}: {state.get('state', 'unknown')}",
        ttl_seconds=1800,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    receipt = run_heartbeat(
        workspace, requested_mode="verify", changed_paths=[relative],
        use_reconciliation=True, trigger=f"workshop-transaction-document:{txid}",
    )
    if not receipt.get("verified") or document_id(txid) not in receipt.get("promoted", []):
        raise WorkshopError("TX_DOCUMENT_PROJECTION_FAILED", "heartbeat did not verify the transaction document", details=receipt.get("failures"))
    expected = prepare_document(path, workspace).content_sha256
    db = KnowledgeDatabase(engine.config.kairos_database)
    with db.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT revision,content_sha256,path FROM artifacts WHERE artifact_id=? AND document_type='transaction'",
            (document_id(txid),),
        ).fetchone()
    if not row or int(row["revision"]) != revision or row["content_sha256"] != expected or row["path"] != relative:
        raise WorkshopError("TX_DOCUMENT_SQL_MISMATCH", "transaction document was not projected as the exact expected revision")
    return {"artifact_id": document_id(txid), "path": relative, "revision": revision, "sha256": expected,
            "heartbeat_id": receipt.get("heartbeat_id")}


def update_document(engine: Any, state: dict[str, Any], *, event: str) -> None:
    txid = str(state.get("transaction_id", ""))
    if not txid or event not in EVENTS:
        return
    marker = pending_path(engine, txid)
    try:
        result = publish(engine, state)
    except Exception as exc:
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, {
            "transaction_id": txid, "event": event,
            "state": state.get("state"), "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc), "pending": True,
        })
        return
    if marker.is_file():
        marker.unlink()


def assert_projected(engine: Any, transaction_id: str) -> None:
    marker = pending_path(engine, transaction_id)
    if marker.is_file():
        raise WorkshopError(
            "TX_DOCUMENT_SYNC_PENDING",
            "Workshop state is already persisted but the transaction document projection is pending. Do not repeat Apply; reconcile the document.",
            details=json.loads(marker.read_text(encoding="utf-8")),
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
    if (metadata.get("id") != document_id(transaction_id)
            or metadata.get("transaction_id") != transaction_id
            or metadata.get("workshop_state") != state.get("state")
            or metadata.get("type") != "transaction"):
        raise WorkshopError("TX_DOCUMENT_STALE", "transaction Markdown does not describe current Workshop state")
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
