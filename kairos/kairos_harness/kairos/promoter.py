from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import KnowledgeDatabase
from .constants import (
    DYNAMIC_CANONICAL_FILES,
    MAX_DOCUMENT_BYTES,
    MAX_GENERATED_RECEIPT_FILES,
    MAX_HEADER_BYTES,
)
from .frontmatter import ParsedFrontmatter, split_frontmatter
from .query import fts_match, tokenize
from .references import Reference, ReferenceError, collect_references, validate_reference_targets
from .sections import Section, parse_sections, validate_answer_targets
from .util import (
    atomic_write_json,
    json_dumps,
    prune_oldest_files,
    read_json,
    sha256_text,
    utc_now,
    workspace_relative,
    resolve_workspace_path,
)


class PromotionError(RuntimeError):
    pass


# Header array -> table, and the columns each row contributes. A column names the header
# keys it accepts, first match wins, so a document may spell one field either way without
# losing the value. Adding a fifth structure is a change to this table, not to the code
# below it. Table and column names come from here only and are never taken from a document.
GRAPH_PROJECTIONS: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "relations",
        "graph_relations",
        (
            ("subject", ("subject",)),
            ("predicate", ("predicate",)),
            ("object", ("object",)),
            ("object_kind", ("object_kind",)),
            ("scope", ("scope",)),
            ("evidence_target", ("evidence_target",)),
            ("evidence", ("evidence",)),
        ),
    ),
    (
        "contracts",
        "graph_contracts",
        (
            ("contract_id", ("id",)),
            ("kind", ("kind",)),
            ("subject", ("subject",)),
            ("statement", ("statement",)),
            ("failure_or_effect", ("failure_or_effect",)),
            ("evidence_target", ("evidence_target",)),
        ),
    ),
    (
        "artifacts",
        "graph_artifacts",
        (
            ("asset_id", ("id",)),
            ("role", ("role",)),
            ("operation", ("operation",)),
            ("producer_or_consumer", ("producer_or_consumer",)),
            ("schema_or_type", ("schema_or_type",)),
            ("hash_bound", ("hash_bound",)),
            ("commit_bound", ("commit_bound",)),
            ("evidence_target", ("evidence_target",)),
        ),
    ),
    (
        "drift_records",
        "graph_drift",
        (
            ("drift_id", ("id",)),
            ("historical", ("historical",)),
            ("subject", ("subject",)),
            ("classification", ("classification",)),
            ("summary", ("summary",)),
            ("status", ("status",)),
            ("evidence_target", ("current_evidence_target", "evidence_target")),
        ),
    ),
)

_GRAPH_BOOLEAN_COLUMNS = {"hash_bound", "commit_bound"}


def _graph_cell(column: str, raw: Any) -> Any:
    if column in _GRAPH_BOOLEAN_COLUMNS:
        return 1 if raw is True else 0
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else json_dumps(raw, pretty=False)


def project_graph_layer(connection: Any, artifact_id: str, metadata: dict[str, Any]) -> dict[str, int]:
    """Write the declared graph structures verbatim and return one count per table.

    Values are stored as written. Controlled vocabularies are not enforced here: a value
    outside the declared set is a finding to be queried, not a reason to lose a document.
    """
    counts: dict[str, int] = {}
    for header_key, table, columns in GRAPH_PROJECTIONS:
        connection.execute(f"DELETE FROM {table} WHERE artifact_id=?", (artifact_id,))
        rows = metadata.get(header_key, [])
        if not isinstance(rows, list):
            raise PromotionError(f"{header_key} must be an array of tables")
        names = ",".join(("artifact_id", "ordinal", *(column for column, _ in columns)))
        placeholders = ",".join("?" for _ in range(len(columns) + 2))
        for ordinal, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise PromotionError(f"{header_key}[{ordinal}] must be a table")
            values: list[Any] = [artifact_id, ordinal]
            for column, keys in columns:
                raw = next((row[key] for key in keys if key in row), None)
                values.append(_graph_cell(column, raw))
            connection.execute(f"INSERT INTO {table}({names}) VALUES({placeholders})", values)
        # Count from the table, not from the loop: the receipt must state what the database
        # holds, not what the projection intended to write.
        stored = connection.execute(
            f"SELECT count(*) FROM {table} WHERE artifact_id=?", (artifact_id,)
        ).fetchone()[0]
        if int(stored) != len(rows):
            raise PromotionError(
                f"{table} stored {stored} of {len(rows)} declared rows for {artifact_id}"
            )
        counts[table] = int(stored)
    return counts


def _validated_recycling_decisions(rows):
    seen = set()
    for entry in rows:
        if not isinstance(entry, dict):
            raise PromotionError("transaction recycling decision must be an object")
        uid, rel = entry.get("unit_id"), entry.get("path")
        if not isinstance(uid, str) or not uid.startswith(("FILE_CHANGE_", "ATOMIC_")) or uid in seen:
            raise PromotionError("transaction recycling has repeated or invalid atomic identities")
        if (not isinstance(rel, str) or len(rel) > 1024
                or rel.startswith("/") or "\\" in rel or
                any(part in {"", ".", ".."} for part in rel.split("/"))):
            raise PromotionError("transaction recycling has an unsafe file path")
        seen.add(uid)
        disposition = entry.get("disposition")
        if disposition not in {"RETAIN_FOR_REUSE", "RETAIN_EVIDENCE", "REJECT_FOR_REUSE"}:
            raise PromotionError("transaction recycling has an invalid disposition")
        reason = entry.get("reason")
        refs = entry.get("evidence_refs")
        if not isinstance(reason, str) or len(reason.strip()) < 28 or not isinstance(refs, list) or not refs:
            raise PromotionError("transaction recycling decisions require an explicit rationale and evidence")
        if len(refs) > 8 or any(not isinstance(v, str) or len(v) > 500 for v in refs):
            raise PromotionError("transaction recycling evidence references are invalid")
        if entry.get("protected") != (disposition != "REJECT_FOR_REUSE"):
            raise PromotionError("transaction recycling protection flags do not match dispositions")
        proofs = entry.get("evidence_proofs", [])
        if proofs:
            if (not isinstance(proofs, list) or len(proofs) != len(refs)
                    or any(not isinstance(fact, dict)
                           or fact.get("locator") not in refs
                           or fact.get("verification_kind") != "EXISTENCE_AND_HASH_ONLY"
                           or not isinstance(fact.get("content_sha256"), str)
                           or re.fullmatch(r"[0-9a-f]{64}", fact["content_sha256"]) is None
                           for fact in proofs)):
                raise PromotionError("transaction recycling evidence hashes are not structurally bound")
        yield entry



def _chunk_payload(document: "PreparedDocument") -> dict[str, Any]:
    section = next((row for row in document.sections if row.section_id == "s-graph-metadata"), None)
    if section is None:
        raise PromotionError("chunk source requires s-graph-metadata")
    body = section.body.strip()
    if body.startswith("> Capsule:"):
        body = body.split("\n\n", 1)[1].strip()
    match = re.fullmatch(r"\x60\x60\x60json\n([^\x00]+?)\n\x60\x60\x60", body, re.DOTALL)
    if not match:
        raise PromotionError("chunk source requires one exact JSON payload")
    try:
        payload = json.loads(match[1], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError) as exc:
        raise PromotionError("invalid strict chunk JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "werkfaden-tx-recycling-chunk-source/v1":
        raise PromotionError("invalid chunk source schema")
    return payload


def _project_chunk_source(connection: Any, document: "PreparedDocument") -> None:
    from .transaction_document import recycling_chunk_identity
    import hashlib
    meta = document.frontmatter.metadata
    payload = _chunk_payload(document)
    packet = payload.get("packet")
    if not isinstance(packet, dict) or packet.get("schema") != "werkfaden-tx-recycling-chunk-receipt/v1":
        raise PromotionError("invalid immutable chunk packet")
    tx, inventory, index = packet.get("transaction_id"), packet.get("inventory_sha256"), packet.get("chunk_index")
    try:
        identity, path = recycling_chunk_identity(tx, inventory, index)
    except (TypeError, ValueError) as exc:
        raise PromotionError("invalid chunk source identity") from exc
    if (meta["id"] != identity or meta["type"] != "report" or meta["state"] != "partial"
            or meta.get("transaction_id") != tx or payload.get("transaction_id") != tx
            or document.relative_path != path or meta["revision"] != 1
            or meta["updated_at"] != packet.get("created_at")):
        raise PromotionError("chunk source scope or immutable revision mismatch")
    if not isinstance(packet.get("scope"), dict) or any(meta.get(key) != packet["scope"].get(key) for key in ("goal", "milestone", "task", "loop")):
        raise PromotionError("chunk source must preserve its exact native parent scope")
    bounds = packet.get("range")
    rows = packet.get("decisions")
    if (not isinstance(bounds, list) or len(bounds) != 2 or any(type(value) is not int for value in bounds)
            or bounds[0] < 0 or not 1 <= bounds[1] - bounds[0] <= 512
            or not isinstance(rows, list) or len(rows) != bounds[1] - bounds[0]):
        raise PromotionError("chunk source must contain one bounded explicit range")
    list(_validated_recycling_decisions(rows))
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    descriptor = {"inventory_sha256": inventory, "chunk_index": index, "range": bounds,
                  "unit_ids": [row["unit_id"] for row in rows]}
    if packet.get("chunk_sha256") != hashlib.sha256(canonical(descriptor)).hexdigest():
        raise PromotionError("chunk membership hash mismatch")
    receipt = hashlib.sha256(canonical(packet)).hexdigest()
    if payload.get("receipt_sha256") != receipt:
        raise PromotionError("chunk receipt hash mismatch")
    connection.execute("""INSERT INTO tx_recycling_chunks(
        artifact_id,transaction_id,inventory_sha256,chunk_index,range_start,range_stop,
        chunk_sha256,receipt_sha256,source_sha256,source_path,decision_count,decisions_json,status
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (identity, tx, inventory, index, bounds[0], bounds[1], packet["chunk_sha256"],
         receipt, sha256_text(document.text), path, len(rows), json_dumps(rows, pretty=False), "PARTIAL"))


def _paged_recycling_rows(connection: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    from .transaction_document import recycling_chunk_identity
    count = payload.get("global_unit_count")
    refs = payload.get("chunks")
    if (payload.get("status") not in {"PARTIAL", "REVIEWED"}
            or type(count) is not int or count < 1 or not isinstance(refs, list) or not refs):
        raise PromotionError("paged recycling index requires bounded-source references and explicit status")
    rows = []
    stop = 0
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or type(ref.get("chunk_index")) is not int or ref["chunk_index"] != index:
            raise PromotionError("paged chunk indices must be unique and consecutive")
        identity, path = recycling_chunk_identity(payload["transaction_id"], payload["inventory_sha256"], index)
        item = connection.execute("SELECT * FROM tx_recycling_chunks WHERE artifact_id=?", (identity,)).fetchone()
        bounds = ref.get("range")
        if (item is None or ref.get("artifact_id") != identity or ref.get("path") != path
                or not isinstance(bounds, list) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)
                or bounds != [item["range_start"], item["range_stop"]] or bounds[0] != stop
                or bounds[1] > count or item["inventory_sha256"] != payload["inventory_sha256"]
                or item["transaction_id"] != payload["transaction_id"]
                or ref.get("chunk_sha256") != item["chunk_sha256"]
                or ref.get("receipt_sha256") != item["receipt_sha256"]
                or ref.get("document_sha256") != item["source_sha256"]
                or type(ref.get("decision_count")) is not int or ref["decision_count"] != item["decision_count"]):
            raise PromotionError("paged index differs from exact promoted subordinate chunk sources")
        stop = bounds[1]
        rows.extend(json.loads(item["decisions_json"]))
    if payload["status"] == "REVIEWED" and stop != count:
        raise PromotionError("completed paged review has missing global coverage")
    return rows

def project_transaction_recycling(connection: Any, document: "PreparedDocument") -> int:
    """Normalize the single canonical recycling ledger inside transaction Markdown.

    The body is the source; SQLite is derived and can be rebuilt from Markdown.
    An invalid/incomplete ledger fails the entire document promotion.
    """
    metadata = document.frontmatter.metadata
    artifact_id = str(metadata["id"])
    connection.execute("DELETE FROM tx_recycling_atoms WHERE artifact_id=?", (artifact_id,))
    connection.execute("DELETE FROM tx_recycling_reviews WHERE artifact_id=?", (artifact_id,))
    connection.execute("DELETE FROM tx_recycling_chunks WHERE artifact_id=?", (artifact_id,))
    if "recycling_chunk_schema" in metadata:
        if metadata["recycling_chunk_schema"] != "werkfaden-tx-recycling-chunk-source/v1":
            raise PromotionError("unsupported recycling chunk source schema")
        _project_chunk_source(connection, document)
        return 0
    if metadata["type"] != "transaction":
        return 0
    section = next((row for row in document.sections if row.section_id == "s-graph-metadata"), None)
    if section is None:
        raise PromotionError("transaction document must contain s-graph-metadata")
    # parse_sections keeps the leading blockquote capsule in section.body.
    # It is a routing summary, not part of the indexed machine ledger.
    full_body = section.body.strip()
    if full_body.startswith("> Capsule:"):
        parts = full_body.split("\n\n", 1)
        if len(parts) != 2:
            raise PromotionError("recycling graph section is missing its content after the capsule")
        full_body = parts[1].strip()
    if full_body == "none":
        if metadata.get("recycling_analysis_sha256"):
            raise PromotionError("transaction declares a recycling receipt but the machine ledger is missing")
        return 0
    match = re.fullmatch(r"\`\`\`json\n([^\x00]+?)\n\`\`\`", full_body, re.DOTALL)
    if match is None:
        raise PromotionError("transaction recycling graph section must contain one exact JSON block")
    try:
        payload = json.loads(match.group(1), parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    except (ValueError, TypeError) as exc:
        raise PromotionError("transaction recycling ledger is invalid strict JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") not in {"werkfaden-tx-recycling-index/v1", "werkfaden-tx-recycling-index/v2"}:
        raise PromotionError("transaction recycling ledger has an invalid schema")
    txid = metadata.get("transaction_id")
    if (not isinstance(txid, str)
            or payload.get("transaction_id") != txid
            or metadata.get("id") != "TRANSACTION_" + txid.upper()):
        raise PromotionError("transaction recycling ledger has mismatched identity")
    sha = payload.get("analysis_sha256")
    inventory = payload.get("inventory_sha256")
    if any(not isinstance(val, str) or re.fullmatch(r"[0-9a-f]{64}", val) is None
           for val in (sha, inventory)):
        raise PromotionError("transaction recycling hashes must be SHA-256")
    paged = payload["schema"] == "werkfaden-tx-recycling-index/v2"
    completed = not paged or payload.get("status") == "REVIEWED"
    if sha != metadata.get("recycling_analysis_sha256" if completed else "recycling_progress_sha256"):
        raise PromotionError("transaction recycling report hash does not match its header")
    rows = _paged_recycling_rows(connection, payload) if paged else payload.get("decisions")
    if not isinstance(rows, list) or (not paged and len(rows) > 4000):
        raise PromotionError("transaction recycling decision rows are unbounded")
    if not completed:
        # Explicit unfinished work is searchable through its subordinate sources,
        # never through completed-review or completed-atom rows.
        list(_validated_recycling_decisions(rows))
        return 0
    for entry in _validated_recycling_decisions(rows):
        uid, rel = entry["unit_id"], entry["path"]
        disposition, reason, refs = entry["disposition"], entry["reason"], entry["evidence_refs"]
        proofs = entry.get("evidence_proofs", [])
        source = str(entry.get("source_transaction") or "")
        successor = str(entry.get("successor_transaction") or "")
        connection.execute(
            """INSERT INTO tx_recycling_atoms(
                artifact_id,unit_id,path,baseline_sha256,candidate_sha256,
                disposition,reason,evidence_json,source_transaction,successor_transaction,protected
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (artifact_id, uid, rel, entry.get("baseline_sha256"),
             entry.get("candidate_sha256"), disposition, reason,
             json_dumps(proofs or refs, pretty=False), source, successor,
             1 if entry["protected"] else 0),
        )
    connection.execute(
        """INSERT INTO tx_recycling_reviews(
            artifact_id,transaction_id,inventory_sha256,analysis_sha256,
            decision_count,document_revision
        ) VALUES(?,?,?,?,?,?)""",
        (artifact_id, txid, inventory, sha, len(rows), metadata["revision"]),
    )
    return len(rows)


@dataclass(frozen=True)
class PreparedDocument:
    path: Path
    relative_path: str
    text: str
    content_sha256: str
    frontmatter: ParsedFrontmatter
    sections: tuple[Section, ...]
    references: tuple[tuple[str | None, Reference], ...]
    external_origin: str = ""
    search_contract_policy: str = "host_enforced"


def _external_origin(workspace: Path, metadata: dict[str, Any]) -> str:
    """Return the foreign workspace id when the document was imported, else empty.

    Derived from the document itself: a header that declares another workspace owns its
    own pointer namespace, which this workspace cannot resolve.
    """
    from .workspace import load_config

    declared = str(metadata.get("workspace", "")).strip()
    host = str(load_config(workspace).get("workspace_id", "")).strip()
    return declared if declared and host and declared != host else ""


def _search_contract_policy(workspace: Path, external_origin: str) -> str:
    if not external_origin:
        return "host_enforced"
    config = read_json(workspace / ".kairos" / "config.json")
    policies = config.get("imported_search_contract_policies", {})
    if not isinstance(policies, dict):
        raise PromotionError("imported_search_contract_policies must be a workspace-id mapping")
    policy = policies.get(external_origin, "host_enforced")
    if policy not in {"host_enforced", "origin_recorded"}:
        raise PromotionError(
            f"unsupported imported search-contract policy for {external_origin!r}: {policy!r}"
        )
    return str(policy)


def _validate_contract_targets(
    metadata: dict[str, Any],
    sections: list[Section],
) -> None:
    available = {
        f"{metadata['id']}#{section.section_id}"
        for section in sections
    }
    for index, contract in enumerate(metadata.get("search_contract", []), 1):
        if contract["expected"] not in available:
            raise PromotionError(
                f"search_contract[{index}] expected target does not exist: "
                f"{contract['expected']!r}"
            )


def prepare_document(path: Path, workspace: Path) -> PreparedDocument:
    if path.suffix.lower() != ".md":
        raise PromotionError(f"KAIROS document promotion currently requires Markdown: {path}")
    # Retain the physical-byte bound before universal-newline decoding.
    size = path.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise PromotionError(f"document uses {size} bytes; maximum is {MAX_DOCUMENT_BYTES}: {path}")
    return _prepare_document_text(path, workspace, path.read_text(encoding="utf-8"), size=size)


def prepare_rendered_document(path: Path, workspace: Path, text: str) -> PreparedDocument:
    """Validate an unwritten native artifact; no file, SQL or runtime mutation."""
    if path.exists() or path.is_symlink():
        raise PromotionError(f"new document already exists: {path}")
    return _prepare_document_text(path, workspace, text, size=len(text.encode("utf-8")),
                                  pending_self=True)


def _prepare_document_text(path: Path, workspace: Path, text: str, *, size: int,
                           pending_self: bool = False) -> PreparedDocument:
    if path.suffix.lower() != ".md":
        raise PromotionError(f"KAIROS document promotion currently requires Markdown: {path}")
    if size > MAX_DOCUMENT_BYTES:
        raise PromotionError(f"document uses {size} bytes; maximum is {MAX_DOCUMENT_BYTES}: {path}")
    frontmatter = split_frontmatter(text)
    external_origin = _external_origin(workspace, frontmatter.metadata)
    # The first-window budget protects the cost of opening a document KAIROS itself wrote.
    # An ingested document is never opened that way: it is reached through the database and
    # read at a named anchor. Carrying a graph layer implies the same thing, so either
    # property lifts the budget; a locally authored routing document keeps it.
    ingested = bool(external_origin) or frontmatter.graph_bearing
    if not ingested and frontmatter.header_bytes > MAX_HEADER_BYTES:
        raise PromotionError(
            f"frontmatter uses {frontmatter.header_bytes} bytes; "
            f"maximum is {MAX_HEADER_BYTES} for a locally authored document: {path}"
        )
    sections = parse_sections(
        frontmatter.body,
        header_bytes=frontmatter.header_bytes,
        ingested=ingested,
    )
    _validate_contract_targets(frontmatter.metadata, sections)
    validate_answer_targets(frontmatter.metadata, sections)
    references = collect_references(frontmatter.metadata, sections)
    if pending_self and not external_origin:
        # Defer only the exact new document path. Validate its self-anchors
        # against the rendered candidate; all other targets resolve normally.
        current = path.resolve()
        known_sections = {section.section_id for section in sections}
        existing = []
        for owner, reference in references:
            target = resolve_workspace_path(workspace, reference.target_path)
            if target == current:
                if reference.target_section and reference.target_section not in known_sections:
                    raise ReferenceError(
                        f"reference section does not exist in new document: {reference.target_path}#{reference.target_section}"
                    )
            else:
                existing.append((owner, reference))
        validate_reference_targets(workspace, existing)
    else:
        validate_reference_targets(workspace, references, resolve=not external_origin)
    return PreparedDocument(
        path=path,
        relative_path=workspace_relative(path, workspace),
        text=text.replace("\r\n", "\n").replace("\r", "\n"),
        content_sha256=sha256_text(text.replace("\r\n", "\n").replace("\r", "\n")),
        frontmatter=frontmatter,
        sections=tuple(sections),
        references=tuple(references),
        external_origin=external_origin,
        search_contract_policy=_search_contract_policy(workspace, external_origin),
    )


def _event_payload(document: PreparedDocument) -> dict[str, Any]:
    metadata = document.frontmatter.metadata
    policy_discriminator = (
        f"|search-contract-policy:{document.search_contract_policy}"
        if document.search_contract_policy != "host_enforced"
        else ""
    )
    event_id = "PE_" + sha256_text(
        f"{document.relative_path}|{metadata['id']}|{metadata['revision']}|"
        f"{document.content_sha256}{policy_discriminator}"
    )[:32]
    return {
        "schema": "kairos-promotion-event/v1",
        "event_id": event_id,
        "artifact_id": metadata["id"],
        "path": document.relative_path,
        "revision": metadata["revision"],
        "content_sha256": document.content_sha256,
        "search_contract_policy": document.search_contract_policy,
        "created_at": utc_now(),
    }


def _write_event_file(workspace: Path, event: dict[str, Any]) -> None:
    directory = workspace / ".kairos" / "events"
    atomic_write_json(directory / f"{event['event_id']}.json", event)
    prune_oldest_files(directory, suffix=".json", keep=MAX_GENERATED_RECEIPT_FILES)


def _questions_for(metadata: dict[str, Any], section_id: str) -> str:
    return " | ".join(answer["question"] for answer in metadata.get("answers", []) if answer["target"] == section_id)


def _verify_contracts(connection, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for contract in metadata.get("search_contract", []):
        terms = tokenize(contract["query"])
        match = fts_match(terms)
        rows = connection.execute(
            """
            SELECT
                artifact_id,
                section_id,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM query_handles AS handle
                    WHERE handle.artifact_id=section_fts.artifact_id
                      AND handle.section_id=section_fts.section_id
                      AND handle.question=? COLLATE NOCASE
                ) THEN 1 ELSE 0 END AS exact_question,
                bm25(section_fts,0.0,0.0,10.0,8.0,12.0,6.0,4.0,1.0) AS rank
            FROM section_fts
            WHERE section_fts MATCH ?
            ORDER BY exact_question DESC,rank,artifact_id,section_id
            LIMIT ?
            """,
            (
                contract["query"].strip(),
                match,
                int(contract.get("required_top_k", 10)),
            ),
        ).fetchall()
        actual = [f"{row['artifact_id']}#{row['section_id']}" for row in rows]
        if contract["expected"] not in actual:
            raise PromotionError(
                f"search contract failed for {contract['query']!r}: expected {contract['expected']!r} in {actual}"
            )
        verified.append(
            {
                "query": contract["query"],
                "expected": contract["expected"],
                "required_top_k": int(contract.get("required_top_k", 10)),
                "verification_scope": "host_workspace",
                "host_enforced": True,
                "status": "matched",
                "actual": actual,
            }
        )
    return verified


def _record_origin_contracts(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "query": contract["query"],
            "expected": contract["expected"],
            "required_top_k": int(contract.get("required_top_k", 10)),
            "verification_scope": str(metadata["workspace"]),
            "host_enforced": False,
            "status": "recorded_not_resolved",
        }
        for contract in metadata.get("search_contract", [])
    ]


def _load_existing_receipt(database: KnowledgeDatabase, event_id: str) -> dict[str, Any] | None:
    connection = database.connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT receipt_json FROM promotion_receipts WHERE event_id=? AND verified=1",
            (event_id,),
        ).fetchone()
        return json.loads(row["receipt_json"]) if row else None
    finally:
        connection.close()


def _verify_scope_ownership(connection, workspace: Path, metadata: dict[str, Any]) -> None:
    config = read_json(workspace / ".kairos" / "config.json")
    expected_workspace = config.get("workspace_id") if isinstance(config, dict) else None
    if not expected_workspace:
        raise PromotionError("workspace configuration declares no workspace_id")
    declared_workspace = metadata.get("workspace")
    if declared_workspace != expected_workspace:
        imported = {
            value
            for value in (config.get("imported_workspace_ids") or [])
            if isinstance(value, str)
        }
        if declared_workspace not in imported:
            raise PromotionError(
                f"artifact workspace {declared_workspace!r} is neither the configured workspace "
                f"{expected_workspace!r} nor a declared imported workspace"
            )
        # An imported document carries the goal, milestone and task of its origin workspace.
        # Those identifiers are recorded as declared and are not resolved here, for the same
        # reason an external reference is a leaf: this workspace does not own that namespace.
        return

    goal_id = metadata.get("goal")
    milestone_id = metadata.get("milestone")
    if not goal_id and not milestone_id:
        return
    owner = connection.execute(
        """
        SELECT g.state AS goal_state,m.state AS milestone_state
        FROM milestones m
        JOIN goals g ON g.goal_id=m.goal_id
        WHERE g.goal_id=? AND m.milestone_id=?
        """,
        (goal_id, milestone_id),
    ).fetchone()
    if not owner:
        raise PromotionError(
            f"artifact scope references unknown goal/milestone: {goal_id}/{milestone_id}"
        )

    document_type = metadata["type"]
    document_state = metadata["state"]
    if (
        document_type == "task"
        and document_state in {"new", "ready", "active", "in_progress", "partial", "blocked"}
        and (
            owner["goal_state"] in {"completed", "superseded"}
            or owner["milestone_state"] in {"completed", "superseded"}
        )
    ):
        raise PromotionError(
            f"non-terminal task cannot belong to closed goal/milestone: {goal_id}/{milestone_id}"
        )

    task_id = metadata.get("task")
    if document_type == "task" or not task_id:
        return
    task = connection.execute(
        """
        SELECT document_type,goal_id,milestone_id
        FROM artifacts
        WHERE artifact_id=?
        """,
        (task_id,),
    ).fetchone()
    if not task or task["document_type"] != "task":
        raise PromotionError(f"artifact references unknown parent task: {task_id}")
    if task["goal_id"] != goal_id or task["milestone_id"] != milestone_id:
        raise PromotionError(
            f"artifact scope {goal_id}/{milestone_id} does not match parent task "
            f"{task_id} scope {task['goal_id']}/{task['milestone_id']}"
        )


def promote_document(
    path: Path,
    workspace: Path,
    database: KnowledgeDatabase,
    *,
    allow_dynamic: bool = False,
) -> dict[str, Any]:
    database.initialize()
    document = prepare_document(path, workspace)
    if document.relative_path in DYNAMIC_CANONICAL_FILES and not allow_dynamic:
        raise PromotionError(
            f"{document.relative_path} is a generated projection and cannot be promoted directly; run a heartbeat"
        )
    metadata = document.frontmatter.metadata
    event = _event_payload(document)
    _write_event_file(workspace, event)
    existing_receipt = _load_existing_receipt(database, event["event_id"])
    if existing_receipt:
        return existing_receipt

    with database.transaction() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO promotion_events(
                event_id,artifact_id,path,revision,content_sha256,status,created_at
            ) VALUES(?,?,?,?,?,'pending',?)
            """,
            (
                event["event_id"],
                metadata["id"],
                document.relative_path,
                metadata["revision"],
                document.content_sha256,
                event["created_at"],
            ),
        )

    try:
        now = utc_now()
        with database.transaction() as connection:
            _verify_scope_ownership(connection, workspace, metadata)
            existing_revision = connection.execute(
                "SELECT content_sha256 FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (metadata["id"], metadata["revision"]),
            ).fetchone()
            if existing_revision and existing_revision["content_sha256"] != document.content_sha256:
                raise PromotionError(
                    f"artifact {metadata['id']} revision {metadata['revision']} changed bytes; increment revision before promotion"
                )
            newer = connection.execute(
                "SELECT max(revision) FROM artifact_revisions WHERE artifact_id=?",
                (metadata["id"],),
            ).fetchone()[0]
            if newer is not None and int(newer) > int(metadata["revision"]):
                raise PromotionError(f"artifact {metadata['id']} cannot regress from revision {newer}")
            metadata_json = json_dumps(metadata, pretty=False)
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id,path,document_type,state,authority,workspace_id,route,
                    loop_id,task_id,goal_id,milestone_id,revision,updated_at,capsule,
                    claim_boundary,content_sha256,header_bytes,metadata_json,promoted_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    path=excluded.path,
                    document_type=excluded.document_type,
                    state=excluded.state,
                    authority=excluded.authority,
                    workspace_id=excluded.workspace_id,
                    route=excluded.route,
                    loop_id=excluded.loop_id,
                    task_id=excluded.task_id,
                    goal_id=excluded.goal_id,
                    milestone_id=excluded.milestone_id,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    capsule=excluded.capsule,
                    claim_boundary=excluded.claim_boundary,
                    content_sha256=excluded.content_sha256,
                    header_bytes=excluded.header_bytes,
                    metadata_json=excluded.metadata_json,
                    promoted_at=excluded.promoted_at
                """,
                (
                    metadata["id"],
                    document.relative_path,
                    metadata["type"],
                    metadata["state"],
                    metadata["authority"],
                    metadata["workspace"],
                    metadata["route"],
                    str(metadata.get("loop", "")) or None,
                    metadata.get("task") or None,
                    metadata.get("goal") or None,
                    metadata.get("milestone") or None,
                    metadata["revision"],
                    metadata["updated_at"],
                    metadata["capsule"],
                    metadata["claim_boundary"],
                    document.content_sha256,
                    document.frontmatter.header_bytes,
                    metadata_json,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO artifact_revisions(
                    artifact_id,revision,content_sha256,path,metadata_json,promoted_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    metadata["id"],
                    metadata["revision"],
                    document.content_sha256,
                    document.relative_path,
                    metadata_json,
                    now,
                ),
            )
            connection.execute("DELETE FROM section_fts WHERE artifact_id=?", (metadata["id"],))
            connection.execute("DELETE FROM query_handles WHERE artifact_id=?", (metadata["id"],))
            connection.execute("DELETE FROM relations WHERE source_artifact_id=?", (metadata["id"],))
            connection.execute("DELETE FROM sections WHERE artifact_id=?", (metadata["id"],))
            entities = " | ".join(metadata.get("entities", []))
            facets = " | ".join(metadata.get("facets", []))
            for section in document.sections:
                connection.execute(
                    """
                    INSERT INTO sections(artifact_id,section_id,position,title,capsule,body,body_sha256)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        metadata["id"],
                        section.section_id,
                        section.position,
                        section.title,
                        section.capsule,
                        section.body,
                        sha256_text(section.body),
                    ),
                )
                questions = _questions_for(metadata, section.section_id)
                connection.execute(
                    """
                    INSERT INTO section_fts(
                        artifact_id,section_id,title,capsule,questions,entities,facets,body
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        metadata["id"],
                        section.section_id,
                        section.title,
                        f"{metadata['capsule']} | {section.capsule} | {metadata['claim_boundary']}",
                        questions,
                        entities,
                        facets,
                        section.body,
                    ),
                )
            for answer in metadata.get("answers", []):
                connection.execute(
                    """
                    INSERT INTO query_handles(artifact_id,section_id,intent,question,language,weight)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        metadata["id"],
                        answer["target"],
                        answer["intent"],
                        answer["question"],
                        answer.get("language", "en"),
                        float(answer.get("weight", 1.0)),
                    ),
                )
            for source_section, ref in document.references:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO relations(
                        source_artifact_id,source_section_id,predicate,target_artifact_id,
                        target_path,target_section_id,version,tags_json,provenance,confidence
                    ) VALUES(?,?,?,?,?,?,?,?,?,1.0)
                    """,
                    (
                        metadata["id"],
                        source_section,
                        ref.relation,
                        ref.target_id,
                        ref.target_path,
                        ref.target_section,
                        ref.version,
                        json_dumps(list(ref.tags), pretty=False),
                        ref.source,
                    ),
                )
            graph_counts = project_graph_layer(connection, metadata["id"], metadata)
            project_transaction_recycling(connection, document)
            coverage_state = "evidenced" if metadata["type"] == "report" and metadata["state"] == "success" else "referenced"
            coverage_section = metadata["answers"][0]["target"]
            connection.execute(
                "DELETE FROM goal_coverage WHERE artifact_id=?",
                (metadata["id"],),
            )
            for criterion_id in metadata.get("criteria", []):
                criterion_owner = connection.execute(
                    """
                    SELECT m.goal_id,c.milestone_id
                    FROM criteria c
                    JOIN milestones m ON m.milestone_id=c.milestone_id
                    WHERE c.criterion_id=?
                    """,
                    (criterion_id,),
                ).fetchone()
                if not criterion_owner:
                    raise PromotionError(f"artifact references unknown goal criterion: {criterion_id}")
                if (
                    metadata.get("goal") != criterion_owner["goal_id"]
                    or metadata.get("milestone") != criterion_owner["milestone_id"]
                ):
                    raise PromotionError(
                        f"criterion {criterion_id} belongs to "
                        f"{criterion_owner['goal_id']}/{criterion_owner['milestone_id']}, "
                        f"not {metadata.get('goal')}/{metadata.get('milestone')}"
                    )
                connection.execute(
                    """
                    INSERT INTO goal_coverage(
                        criterion_id,artifact_id,section_id,coverage_state,confidence,evidence_note,updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(criterion_id,artifact_id,section_id) DO UPDATE SET
                        coverage_state=excluded.coverage_state,
                        confidence=excluded.confidence,
                        evidence_note=excluded.evidence_note,
                        updated_at=excluded.updated_at
                    """,
                    (
                        criterion_id,
                        metadata["id"],
                        coverage_section,
                        coverage_state,
                        1.0 if coverage_state == "evidenced" else 0.5,
                        metadata["capsule"],
                        now,
                    ),
                )
            if document.search_contract_policy == "origin_recorded":
                contract_results = _record_origin_contracts(metadata)
            else:
                contract_results = _verify_contracts(connection, metadata)
            relation_count = connection.execute(
                "SELECT count(*) FROM relations WHERE source_artifact_id=?", (metadata["id"],)
            ).fetchone()[0]
            query_count = connection.execute(
                "SELECT count(*) FROM query_handles WHERE artifact_id=?", (metadata["id"],)
            ).fetchone()[0]
            receipt_id = "PR_" + sha256_text(f"{event['event_id']}|verified")[:32]
            receipt = {
                "schema": "kairos-promotion-receipt/v1",
                "receipt_id": receipt_id,
                "event_id": event["event_id"],
                "artifact_id": metadata["id"],
                "path": document.relative_path,
                "revision": metadata["revision"],
                "content_sha256": document.content_sha256,
                "section_count": len(document.sections),
                "relation_count": int(relation_count),
                "query_handle_count": int(query_count),
                "search_contracts": contract_results,
                "search_contract_policy": document.search_contract_policy,
                "search_contract_unverified_count": sum(
                    1 for result in contract_results if not result["host_enforced"]
                ),
                "graph_counts": graph_counts,
                "external_origin": document.external_origin,
                "verified": True,
                "created_at": now,
            }
            connection.execute(
                """
                INSERT INTO promotion_receipts(
                    receipt_id,event_id,artifact_id,content_sha256,section_count,relation_count,
                    query_handle_count,search_contract_count,verified,receipt_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,1,?,?)
                """,
                (
                    receipt_id,
                    event["event_id"],
                    metadata["id"],
                    document.content_sha256,
                    len(document.sections),
                    int(relation_count),
                    int(query_count),
                    len(contract_results),
                    json_dumps(receipt, pretty=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE promotion_events SET status='applied',applied_at=?,error=NULL WHERE event_id=?",
                (now, event["event_id"]),
            )
            connection.execute(
                """
                UPDATE promotion_events
                SET status='superseded',applied_at=?,
                    error=coalesce(error || ' | ', '') || ?
                WHERE artifact_id=? AND status='failed' AND event_id<>? AND revision<=?
                """,
                (
                    now,
                    f"superseded by verified event {event['event_id']}",
                    metadata["id"],
                    event["event_id"],
                    metadata["revision"],
                ),
            )
        connection = database.connect(read_only=True)
        try:
            row = connection.execute(
                """
                SELECT a.content_sha256,count(s.section_id) AS sections,count(pr.receipt_id) AS receipts
                FROM artifacts a
                JOIN sections s ON s.artifact_id=a.artifact_id
                JOIN promotion_receipts pr ON pr.artifact_id=a.artifact_id AND pr.event_id=?
                WHERE a.artifact_id=?
                GROUP BY a.content_sha256
                """,
                (event["event_id"], metadata["id"]),
            ).fetchone()
            if not row or row["content_sha256"] != document.content_sha256 or int(row["sections"]) != len(document.sections):
                raise PromotionError("post-reopen promotion verification failed")
        finally:
            connection.close()
        receipt_directory = workspace / ".kairos" / "receipts"
        atomic_write_json(receipt_directory / f"{receipt['receipt_id']}.json", receipt)
        prune_oldest_files(receipt_directory, suffix=".json", keep=MAX_GENERATED_RECEIPT_FILES)
        return receipt
    except Exception as exc:
        with database.transaction() as connection:
            connection.execute(
                "UPDATE promotion_events SET status='failed',error=? WHERE event_id=?",
                (str(exc)[:2000], event["event_id"]),
            )
        raise


def promote_many(
    paths: list[Path],
    workspace: Path,
    database: KnowledgeDatabase,
    *,
    allow_dynamic: bool = False,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in paths:
        receipts.append(promote_document(path, workspace, database, allow_dynamic=allow_dynamic))
    return receipts
