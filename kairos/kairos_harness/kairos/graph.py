"""Bounded reads over the normalized graph layer.

The four graph tables hold typed facts about entities that are not documents. This module
is the only read surface for them, so a caller never reaches past KAIROS into SQLite.

Every result is bounded and every response states its own denominator, so a truncated
answer is visible as truncated instead of looking complete.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .constants import (
    GRAPH_ARTIFACT_OPERATIONS,
    GRAPH_ARTIFACT_ROLES,
    GRAPH_CONTRACT_KINDS,
    GRAPH_DRIFT_CLASSIFICATIONS,
    GRAPH_DRIFT_STATES,
    GRAPH_OBJECT_KINDS,
    RELATION_TYPES,
)
from .database import KnowledgeDatabase
from .query import QueryFrame, tokenize


class GraphError(RuntimeError):
    pass


MAX_GRAPH_ROWS = 500
DEFAULT_GRAPH_ROWS = 50

_TABLES = ("graph_relations", "graph_contracts", "graph_artifacts", "graph_drift", "compiler_include_edges")

# Column -> declared vocabulary. A stored value outside its set is a finding, never a
# reason to reject a document, so the deviation is reported here instead of at promotion.
DECLARED_VOCABULARY: dict[tuple[str, str], set[str]] = {
    ("graph_relations", "predicate"): set(RELATION_TYPES),
    ("graph_relations", "object_kind"): set(GRAPH_OBJECT_KINDS),
    ("graph_contracts", "kind"): set(GRAPH_CONTRACT_KINDS),
    ("graph_artifacts", "role"): set(GRAPH_ARTIFACT_ROLES),
    ("graph_artifacts", "operation"): set(GRAPH_ARTIFACT_OPERATIONS),
    ("graph_drift", "classification"): set(GRAPH_DRIFT_CLASSIFICATIONS),
    ("graph_drift", "status"): set(GRAPH_DRIFT_STATES),
}


def _bounded(limit: int) -> int:
    if not 1 <= limit <= MAX_GRAPH_ROWS:
        raise GraphError(f"limit must be between 1 and {MAX_GRAPH_ROWS}")
    return limit


def _rows(connection, sql: str, parameters: tuple[Any, ...], limit: int) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, (*parameters, limit)).fetchall()]


def _total(connection, sql: str, parameters: tuple[Any, ...]) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def _available_tables(connection) -> set[str]:
    """Return normalized graph tables present in this database.

    ``compiler_include_edges`` is additive in alpha.5. Read-only alpha.4 framework
    bundles and existing workspaces remain valid inputs; absence means the stronger
    edge authority is unavailable, never that the known graph is corrupt.
    """
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }


_SEARCH_FIELDS = {
    "graph_relations": ("subject", "predicate", "object", "scope"),
    "graph_contracts": ("contract_id", "kind", "subject", "failure_or_effect"),
    "graph_artifacts": (
        "asset_id", "role", "operation", "producer_or_consumer", "schema_or_type",
    ),
    "graph_drift": ("drift_id", "historical", "subject", "classification", "status"),
    "compiler_include_edges": (
        "compiled_root", "including_file", "include_token", "resolved_target",
        "resolution_class", "resolution_path",
    ),
}
_RESULT_FIELDS = {
    "tx_recycling_chunk_atoms": (
        "transaction_id", "inventory_sha256", "chunk_index", "status", "source_path",
        "unit_id", "path", "disposition", "reason", "evidence_json", "protected", "parent_reviewed",
    ),
    "tx_recycling_chunks": (
        "transaction_id", "inventory_sha256", "chunk_index", "range_start", "range_stop",
        "chunk_sha256", "receipt_sha256", "source_sha256", "source_path", "decision_count", "status",
    ),
    "graph_relations": (
        "subject", "predicate", "object", "object_kind", "scope", "evidence",
    ),
    "graph_contracts": (
        "contract_id", "kind", "subject", "statement", "failure_or_effect",
    ),
    "graph_artifacts": (
        "asset_id", "role", "operation", "producer_or_consumer", "schema_or_type",
        "hash_bound", "commit_bound",
    ),
    "graph_drift": (
        "drift_id", "historical", "subject", "classification", "summary", "status",
    ),
    "compiler_include_edges": (
        "compiled_root", "including_file", "include_line", "include_token", "delimiter",
        "resolved_target", "resolution_class", "compiler_variant", "resolution_path",
    ),
    "tx_recycling_atoms": (
        "unit_id", "path", "baseline_sha256", "candidate_sha256",
        "disposition", "reason", "evidence_json",
        "source_transaction", "successor_transaction", "protected",
    ),
}
_PRODUCER_PREDICATES = {"produces", "writes", "commits"}
_CONSUMER_PREDICATES = {"consumes", "reads", "validates"}
_PRODUCER_OPERATIONS = {"write", "commit", "produce", "emit"}
_CONSUMER_OPERATIONS = {"read", "verify", "consume", "validate"}
_FAILURE_LITERAL = re.compile(r"^(?:FAIL|ERROR|ERR|E)_[A-Za-z0-9_.:-]+$", re.I)


def _failure_boundary_clauses(identifier: str) -> tuple[list[str], list[str]]:
    if not _FAILURE_LITERAL.fullmatch(identifier):
        return [], []
    return (
        [
            "g.failure_or_effect=? COLLATE BINARY",
            "g.failure_or_effect GLOB ?",
            "g.failure_or_effect GLOB ?",
            "g.failure_or_effect GLOB ?",
        ],
        [
            identifier,
            f"{identifier}[^A-Za-z0-9_.:-]*",
            f"*[^A-Za-z0-9_.:-]{identifier}",
            f"*[^A-Za-z0-9_.:-]{identifier}[^A-Za-z0-9_.:-]*",
        ],
    )


def _literal_where(table: str, identifiers: tuple[str, ...]) -> tuple[str, tuple[Any, ...]]:
    placeholders = ",".join("?" for _ in identifiers)
    clauses: list[str] = []
    parameters: list[Any] = []
    for field in _SEARCH_FIELDS[table]:
        clauses.append(f"g.{field} COLLATE BINARY IN ({placeholders})")
        parameters.extend(identifiers)
    if table == "graph_contracts":
        for identifier in identifiers:
            boundary_clauses, boundary_parameters = _failure_boundary_clauses(identifier)
            clauses.extend(boundary_clauses)
            parameters.extend(boundary_parameters)
    return "(" + " OR ".join(clauses) + ")", tuple(parameters)


def _specificity_expression(
    table: str,
    identifiers: tuple[str, ...],
) -> tuple[str, tuple[Any, ...]]:
    """Count distinct exact query identities matched by one graph row in SQL."""
    terms: list[str] = []
    parameters: list[Any] = []
    for identifier in identifiers:
        clauses = [
            f"g.{field}=? COLLATE BINARY"
            for field in _SEARCH_FIELDS[table]
        ]
        values: list[Any] = [identifier] * len(_SEARCH_FIELDS[table])
        if table == "graph_contracts":
            boundary_clauses, boundary_parameters = _failure_boundary_clauses(identifier)
            clauses.extend(boundary_clauses)
            values.extend(boundary_parameters)
        terms.append("CASE WHEN (" + " OR ".join(clauses) + ") THEN 1 ELSE 0 END")
        parameters.extend(values)
    return " + ".join(terms), tuple(parameters)


def _identity_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\\", "/").strip()


MAX_RESOLVED_CANDIDATES = 24


def resolve_identity(connection, partial: str, *, limit: int = MAX_RESOLVED_CANDIDATES) -> dict[str, Any]:
    """Turn a partial name into the canonical identities the graph actually stores.

    A caller works from a stack trace, a log line or an artifact, where a symbol shows
    up bare. The graph stores it qualified. Matching those two silently would make the
    reader trust a guess, so this reports every candidate with its occurrence count and
    lets the caller choose. An empty result is an answer too: the name is not in here.
    """
    needle = _identity_key(partial)
    if not 2 <= len(needle) <= 512:
        raise GraphError("resolve needs between 2 and 512 characters")
    bound = max(1, min(limit, MAX_RESOLVED_CANDIDATES))
    # a bare symbol ends a qualified one; a bare file name ends a path
    escaped = needle.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
    patterns = (needle, "%::" + escaped, "%/" + escaped)
    counts: dict[str, dict[str, Any]] = {}
    available = _available_tables(connection)
    for table, fields in _SEARCH_FIELDS.items():
        if table not in available:
            continue
        for field in fields:
            clauses = " OR ".join(f"g.{field} LIKE ? ESCAPE '\\'" for _ in patterns)
            rows = connection.execute(
                f"SELECT g.{field} AS value, count(*) AS hits FROM {table} g "
                f"WHERE ({clauses}) GROUP BY g.{field} ORDER BY hits DESC LIMIT ?",
                (*patterns, bound * 4),
            ).fetchall()
            for row in rows:
                value = str(row["value"] or "")
                if not value:
                    continue
                entry = counts.setdefault(value, {"identity": value, "hits": 0, "where": []})
                entry["hits"] += int(row["hits"])
                place = f"{table}.{field}"
                if place not in entry["where"]:
                    entry["where"].append(place)
    ordered = sorted(counts.values(), key=lambda e: (-e["hits"], e["identity"]))
    exact = [e for e in ordered if e["identity"] == partial]
    return {
        "schema": "kairos-graph-resolve/v1",
        "partial": partial,
        "exact_identity": exact[0]["identity"] if exact else None,
        "total": len(ordered),
        "returned": min(len(ordered), bound),
        "truncated": len(ordered) > bound,
        "candidates": ordered[:bound],
    }


def _matched_fields(table: str, row: dict[str, Any], identifiers: tuple[str, ...]) -> tuple[list[str], list[str]]:
    matched_fields: list[str] = []
    matched_identifiers: list[str] = []
    normalized = {_identity_key(value): value for value in identifiers}
    for field in _SEARCH_FIELDS[table]:
        field_value = str(row.get(field, ""))
        field_normalized = _identity_key(field_value)
        direct = normalized.get(field_normalized)
        matches: list[str] = [direct] if direct else []
        if table == "graph_contracts" and field == "failure_or_effect":
            for identifier in identifiers:
                if not _FAILURE_LITERAL.fullmatch(identifier):
                    continue
                if re.search(
                    rf"(?<![A-Za-z0-9_.:-]){re.escape(identifier)}(?![A-Za-z0-9_.:-])",
                    field_value,
                ):
                    matches.append(identifier)
        if matches:
            matched_fields.append(field)
            for identifier in matches:
                if identifier not in matched_identifiers:
                    matched_identifiers.append(identifier)
    return matched_fields, matched_identifiers


def _semantic_score(table: str, row: dict[str, Any], frame: QueryFrame) -> tuple[float, list[str]]:
    score = 180.0
    reasons = ["exact_graph_identity +180"]
    intents = set(frame.intents)
    producer_match = (
        table == "graph_relations" and str(row.get("predicate")) in _PRODUCER_PREDICATES
    ) or (
        table == "graph_artifacts"
        and (
            str(row.get("operation")) in _PRODUCER_OPERATIONS
            or str(row.get("role")) == "output"
        )
    )
    consumer_match = (
        table == "graph_relations" and str(row.get("predicate")) in _CONSUMER_PREDICATES
    ) or (
        table == "graph_artifacts"
        and (
            str(row.get("operation")) in _CONSUMER_OPERATIONS
            or str(row.get("role")) == "input"
        )
    )
    if "producer" in intents and producer_match:
        score += 40.0
        reasons.append("producer_role +40")
    if "consumer" in intents and consumer_match:
        score += 40.0
        reasons.append("consumer_role +40")
    if "graph_contract" in intents and table == "graph_contracts":
        score += 40.0
        reasons.append("contract_surface +40")
    if "graph_drift" in intents and table == "graph_drift":
        score += 40.0
        reasons.append("drift_surface +40")
    return min(score, 220.0), reasons


def _table_order(table: str, frame: QueryFrame) -> str:
    intents = set(frame.intents)
    if "producer" in intents and table == "graph_relations":
        return (
            "CASE WHEN g.predicate IN ('produces','writes','commits') THEN 0 ELSE 1 END,"
            "g.artifact_id,g.ordinal"
        )
    if "consumer" in intents and table == "graph_relations":
        return (
            "CASE WHEN g.predicate IN ('consumes','reads','validates') THEN 0 ELSE 1 END,"
            "g.artifact_id,g.ordinal"
        )
    if "producer" in intents and table == "graph_artifacts":
        return (
            "CASE WHEN g.operation IN ('write','commit','produce','emit') OR g.role='output' "
            "THEN 0 ELSE 1 END,g.artifact_id,g.ordinal"
        )
    if "consumer" in intents and table == "graph_artifacts":
        return (
            "CASE WHEN g.operation IN ('read','verify','consume','validate') OR g.role='input' "
            "THEN 0 ELSE 1 END,g.artifact_id,g.ordinal"
        )
    return "g.artifact_id,g.ordinal"


def query_graph_context(
    connection,
    frame: QueryFrame,
    *,
    limit: int,
) -> dict[str, Any]:
    """Resolve exact query literals through normalized graph facts to evidence sections."""
    bound = _bounded(limit)
    identifiers = tuple(frame.graph_identifiers)
    route_basis = {
        "match": "exact_identity",
        "identity_case": "case_sensitive",
        "combination_order": "matched_identifier_count_desc",
        "identifiers": list(identifiers),
        "intents": [
            intent
            for intent in frame.intents
            if intent in {"producer", "consumer", "graph_contract", "graph_drift", "graph_context"}
        ],
        "tables": list(_SEARCH_FIELDS),
    }
    if not identifiers:
        return {
            "schema": "kairos-search-graph-context/v1",
            "route_basis": route_basis,
            "table_totals": {table: 0 for table in _SEARCH_FIELDS},
            "seed_total": 0,
            "seed_returned": 0,
            "returned": 0,
            "truncated": False,
            "resolved_anchor_total": 0,
            "unresolved_anchor_total": 0,
            "max_returned_identifier_specificity": 0,
            "resolved_at_max_specificity_total": 0,
            "near_misses": [],
            "facts": [],
        }

    facts: list[dict[str, Any]] = []
    table_totals: dict[str, int] = {}
    unresolved_anchor_total = 0
    available = _available_tables(connection)
    for table in _SEARCH_FIELDS:
        if table not in available:
            table_totals[table] = 0
            continue
        where, parameters = _literal_where(table, identifiers)
        specificity_sql, specificity_parameters = _specificity_expression(
            table,
            identifiers,
        )
        table_total = _total(
            connection,
            f"SELECT count(*) FROM {table} g WHERE {where}",
            parameters,
        )
        table_totals[table] = table_total
        unresolved_anchor_total += _total(
            connection,
            f"""
            SELECT count(*)
            FROM {table} g
            LEFT JOIN sections s
              ON s.artifact_id=g.artifact_id AND s.section_id=g.evidence_target
            WHERE {where} AND (g.evidence_target='' OR s.section_id IS NULL)
            """,
            parameters,
        )
        rows = connection.execute(
            f"""
            SELECT g.*,a.path AS document_path,
                   ({specificity_sql}) AS matched_identifier_count,
                   CASE WHEN g.evidence_target<>'' AND s.section_id IS NOT NULL THEN 1 ELSE 0 END
                       AS evidence_resolved
            FROM {table} g
            JOIN artifacts a ON a.artifact_id=g.artifact_id
            LEFT JOIN sections s
              ON s.artifact_id=g.artifact_id AND s.section_id=g.evidence_target
            WHERE {where}
            ORDER BY matched_identifier_count DESC,evidence_resolved DESC,
                     {_table_order(table, frame)}
            LIMIT ?
            """,
            (*specificity_parameters, *parameters, bound),
        ).fetchall()
        for source_row in rows:
            row = dict(source_row)
            matched_fields, matched_identifiers = _matched_fields(table, row, identifiers)
            if not matched_identifiers:
                continue
            semantic_score, semantic_reasons = _semantic_score(table, row, frame)
            matched_identifier_count = int(row.pop("matched_identifier_count"))
            evidence_resolved = bool(row.pop("evidence_resolved"))
            document_path = str(row.pop("document_path"))
            evidence_target = str(row["evidence_target"])
            direction = "declared"
            if table == "graph_relations":
                if "subject" in matched_fields and "object" not in matched_fields:
                    direction = "outgoing"
                elif "object" in matched_fields and "subject" not in matched_fields:
                    direction = "incoming"
            facts.append(
                {
                    "depth": 0,
                    "direction": direction,
                    "table": table,
                    "artifact_id": str(row["artifact_id"]),
                    "ordinal": int(row["ordinal"]),
                    "evidence_target": evidence_target,
                    "evidence_resolved": evidence_resolved,
                    "evidence_path": (
                        f"{document_path}#{evidence_target}" if evidence_resolved else None
                    ),
                    "matched_fields": matched_fields,
                    "matched_identifiers": matched_identifiers,
                    "matched_identifier_count": matched_identifier_count,
                    "matched_tokens": tokenize(" ".join(matched_identifiers))[:32],
                    "semantic_score": semantic_score,
                    "semantic_reasons": semantic_reasons,
                    "row": {field: row[field] for field in _RESULT_FIELDS[table]},
                }
            )

    facts.sort(
        key=lambda item: (
            -int(item["matched_identifier_count"]),
            0 if item["evidence_resolved"] else 1,
            -float(item["semantic_score"]),
            item["evidence_path"] or "~",
            item["table"],
            item["artifact_id"],
            int(item["ordinal"]),
        )
    )
    seed_total = sum(table_totals.values())
    returned_facts = facts[:bound]
    max_returned_specificity = max(
        (int(fact["matched_identifier_count"]) for fact in returned_facts),
        default=0,
    )
    resolved_at_max_specificity_total = sum(
        1
        for fact in returned_facts
        if int(fact["matched_identifier_count"]) == max_returned_specificity
        and fact["evidence_resolved"]
    )
    # A caller reads a symbol off a stack trace, where it is bare, while the graph
    # stores it qualified. Exact identity then returns nothing, and nothing looks the
    # same as absent. Naming the canonical forms turns that dead end into a next step
    # without ever substituting one identity for another behind the caller's back.
    near_misses: list[dict[str, Any]] = []
    if seed_total == 0:
        for identifier in identifiers[:4]:
            try:
                resolution = resolve_identity(connection, identifier, limit=6)
            except GraphError:
                continue
            if resolution["candidates"]:
                near_misses.append({
                    "identifier": identifier,
                    "total": resolution["total"],
                    "candidates": [c["identity"] for c in resolution["candidates"]],
                })

    return {
        "schema": "kairos-search-graph-context/v1",
        "route_basis": route_basis,
        "table_totals": table_totals,
        "seed_total": seed_total,
        "seed_returned": len(returned_facts),
        "returned": len(returned_facts),
        "truncated": seed_total > len(returned_facts),
        "resolved_anchor_total": seed_total - unresolved_anchor_total,
        "unresolved_anchor_total": unresolved_anchor_total,
        "max_returned_identifier_specificity": max_returned_specificity,
        "resolved_at_max_specificity_total": resolved_at_max_specificity_total,
        "near_misses": near_misses,
        "facts": returned_facts,
    }


INVENTORY_TABLES = {
    "relations": "graph_relations",
    "contracts": "graph_contracts",
    "artifacts": "graph_artifacts",
    "drift": "graph_drift",
    "include_edges": "compiler_include_edges",
    "tx_atoms": "tx_recycling_atoms",
    "tx_chunks": "tx_recycling_chunks",
    "tx_chunk_atoms": "tx_recycling_chunk_atoms",
}
# the column each inventory is grouped by when no filter narrows it
INVENTORY_GROUPING = {
    "graph_relations": "predicate",
    "graph_contracts": "kind",
    "graph_artifacts": "operation",
    "graph_drift": "status",
    "compiler_include_edges": "resolution_class",
    "tx_recycling_atoms": "disposition",
    "tx_recycling_chunks": "status",
    "tx_recycling_chunk_atoms": "disposition",
}


def inventory(
    connection,
    table_name: str,
    *,
    field: str = "",
    value: str = "",
    limit: int = DEFAULT_GRAPH_ROWS,
) -> dict[str, Any]:
    """One whole table across the corpus, grouped, bounded, with its true total.

    Per-document and per-entity reads already exist. What was missing is the question a
    census cannot answer and a document query answers 148 times over: give me every row
    of this kind at once, optionally narrowed to one column value. Walking the corpus
    document by document to build an inventory costs two orders of magnitude more than
    the answer is worth.
    """
    if table_name not in INVENTORY_TABLES:
        raise GraphError(
            "inventory must name one of: " + ", ".join(sorted(INVENTORY_TABLES))
        )
    table = INVENTORY_TABLES[table_name]
    bound = _bounded(limit)
    if table not in _available_tables(connection):
        return {
            "schema": "kairos-graph-inventory/v1",
            "table": table_name,
            "filter": {"field": field, "value": value} if field else None,
            "available": False,
            "total": 0,
            "returned": 0,
            "truncated": False,
            "documents": 0,
            "grouped_by": INVENTORY_GROUPING[table],
            "groups": [],
            "rows": [],
        }
    where, parameters = "1=1", ()
    if field:
        if field not in _RESULT_FIELDS[table] and field != "artifact_id":
            raise GraphError(
                f"{field!r} is not a column of {table_name}; available: "
                + ", ".join(("artifact_id", *_RESULT_FIELDS[table]))
            )
        where, parameters = f"g.{field}=?", (value,)

    total = _total(connection, f"SELECT count(*) FROM {table} g WHERE {where}", parameters)
    grouping = INVENTORY_GROUPING[table]
    groups = [
        {"value": row["value"], "rows": int(row["rows"]),
         "documents": int(row["documents"])}
        for row in connection.execute(
            f"SELECT g.{grouping} AS value, count(*) AS rows, "
            f"count(DISTINCT g.artifact_id) AS documents "
            f"FROM {table} g WHERE {where} GROUP BY g.{grouping} "
            f"ORDER BY rows DESC LIMIT 64",
            parameters,
        ).fetchall()
    ]
    columns = ",".join(f"g.{name}" for name in ("artifact_id", *_RESULT_FIELDS[table]))
    order_field = "unit_id" if table in {"tx_recycling_atoms", "tx_recycling_chunk_atoms"} else "chunk_index" if table == "tx_recycling_chunks" else "ordinal"
    rows = _rows(
        connection,
        f"SELECT {columns} FROM {table} g WHERE {where} "
        f"ORDER BY g.artifact_id,g.{order_field} LIMIT ?",
        parameters,
        bound,
    )
    return {
        "schema": "kairos-graph-inventory/v1",
        "table": table_name,
        "filter": {"field": field, "value": value} if field else None,
        "total": total,
        "returned": len(rows),
        "truncated": total > len(rows),
        "documents": _total(
            connection,
            f"SELECT count(DISTINCT g.artifact_id) FROM {table} g WHERE {where}",
            parameters,
        ),
        "grouped_by": grouping,
        "groups": groups,
        "rows": rows,
    }


MAX_CHASE_HOPS = 4


def graph_chase(
    connection,
    seeds: tuple[str, ...],
    *,
    max_hops: int = 2,
    limit: int = MAX_GRAPH_ROWS,
    predicates: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Walk the declared code graph outward from named entities, hop by hop.

    The document chase follows header references and lands on routers. This one follows
    `graph_relations`, so a failure literal leads back to whatever gates it and onward to
    what that calls. Every edge keeps its predicate, its owning document and its evidence
    anchor, and every hop states how many edges existed against how many were returned —
    a truncated chain has to be visible as truncated.
    """
    if not 0 <= max_hops <= MAX_CHASE_HOPS:
        raise GraphError(f"max_hops must be between 0 and {MAX_CHASE_HOPS}")
    bound = _bounded(limit)
    frontier = [s for s in dict.fromkeys(_identity_key(s) for s in seeds) if s]
    if not frontier:
        return {
            "schema": "kairos-graph-chase/v1", "seeds": [], "max_hops": max_hops,
            "hops": [], "nodes_reached": 0, "edges_returned": 0, "truncated": False,
        }

    predicate_clause, predicate_parameters = "", ()
    if predicates:
        predicate_clause = " AND g.predicate IN (%s)" % ",".join("?" for _ in predicates)
        predicate_parameters = tuple(predicates)

    visited = set(frontier)
    seen_edges: set[tuple[str, int]] = set()
    hops: list[dict[str, Any]] = []
    returned = 0
    truncated = False

    for hop in range(1, max_hops + 1):
        if not frontier or returned >= bound:
            break
        frontier_set = set(frontier)
        placeholders = ",".join("?" for _ in frontier)
        where = (f"(g.subject IN ({placeholders}) OR g.object IN ({placeholders}))"
                 + predicate_clause)
        parameters = (*frontier, *frontier, *predicate_parameters)
        total = _total(connection, f"SELECT count(*) FROM graph_relations g WHERE {where}",
                       parameters)
        room = bound - returned
        rows = _rows(
            connection,
            f"""
            SELECT g.subject,g.predicate,g.object,g.object_kind,g.scope,g.artifact_id,
                   g.evidence_target,g.ordinal,
                   CASE WHEN s.section_id IS NULL THEN 0 ELSE 1 END AS evidence_resolved
            FROM graph_relations g
            LEFT JOIN sections s
              ON s.artifact_id=g.artifact_id AND s.section_id=g.evidence_target
            WHERE {where}
            ORDER BY g.artifact_id,g.ordinal LIMIT ?
            """,
            parameters,
            room,
        )
        edges: list[dict[str, Any]] = []
        next_frontier: list[str] = []
        for row in rows:
            subject, target = str(row["subject"]), str(row["object"])
            # one edge belongs to one hop: without this an edge reappears on the next
            # hop through whichever endpoint the frontier moved to, and the chain reads
            # as deeper than it is
            edge_key = (str(row["artifact_id"]), int(row["ordinal"]))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            # direction is relative to where this hop started, not to everything seen
            from_subject = _identity_key(subject) in frontier_set
            reached = target if from_subject else subject
            direction = "outgoing" if from_subject else "incoming"
            edges.append({
                "hop": hop,
                "direction": direction,
                "subject": subject,
                "predicate": str(row["predicate"]),
                "object": target,
                "object_kind": str(row["object_kind"] or ""),
                "scope": str(row["scope"] or ""),
                "artifact_id": str(row["artifact_id"]),
                "evidence_target": str(row["evidence_target"] or ""),
                "evidence_resolved": bool(row["evidence_resolved"]),
                "reached": reached,
            })
            key = _identity_key(reached)
            if key and key not in visited:
                visited.add(key)
                next_frontier.append(key)
        returned += len(edges)
        hop_truncated = len(rows) < total
        truncated = truncated or hop_truncated
        hops.append({
            "hop": hop,
            "from_nodes": len(frontier),
            "edge_total": total,
            "edge_read": len(rows),
            "edge_new": len(edges),
            "edge_already_seen": len(rows) - len(edges),
            "truncated": hop_truncated,
            "edges": edges,
        })
        frontier = next_frontier

    return {
        "schema": "kairos-graph-chase/v1",
        "seeds": list(seeds),
        "max_hops": max_hops,
        "predicates": list(predicates),
        "hops": hops,
        "nodes_reached": len(visited) - len(seeds),
        "edges_returned": returned,
        "truncated": truncated,
    }


def artifact_graph(connection, artifact_id: str, *, limit: int) -> dict[str, Any]:
    """Every declared structure of one document, table by table, with true totals."""
    result: dict[str, Any] = {"artifact_id": artifact_id, "tables": {}}
    available = _available_tables(connection)
    for table in _TABLES:
        if table not in available:
            result["tables"][table] = {
                "available": False, "total": 0, "returned": 0,
                "truncated": False, "rows": [],
            }
            continue
        total = _total(
            connection, f"SELECT count(*) FROM {table} WHERE artifact_id=?", (artifact_id,)
        )
        rows = _rows(
            connection,
            f"SELECT * FROM {table} WHERE artifact_id=? ORDER BY ordinal LIMIT ?",
            (artifact_id,),
            limit,
        )
        result["tables"][table] = {
            "total": total,
            "returned": len(rows),
            "truncated": total > len(rows),
            "rows": rows,
        }
    return result


def _empty_lookup_hint(connection, value: str) -> dict[str, Any]:
    """Explain an empty exact-identity lookup instead of letting it read as a finding.

    Every lookup here matches the stored identity exactly. A bare symbol therefore
    returns nothing even when the graph knows it under its qualified name, and an empty
    result looks indistinguishable from "the corpus does not mention this". That
    ambiguity is the dangerous part, so a miss says which of the two it is and, where a
    qualified form exists, names it.
    """
    try:
        resolved = resolve_identity(connection, value)
    except GraphError:
        return {
            "reason": "no row names this identity",
            "hint": "lookups match the stored identity exactly",
            "candidates": [],
        }
    candidates = resolved["candidates"]
    if candidates:
        return {
            "reason": "no row names this identity exactly",
            "hint": "the graph stores this name in a qualified form; retry with one of "
                    "the candidates below, or use --resolve to list them",
            "candidates": candidates,
        }
    return {
        "reason": "no row names this identity, and no qualified form ends in it",
        "hint": "the corpus does not mention this name at all",
        "candidates": [],
    }


def node_graph(connection, node: str, *, limit: int) -> dict[str, Any]:
    """Edges that name one entity, in either position, plus what it produces or consumes."""
    outgoing_total = _total(
        connection, "SELECT count(*) FROM graph_relations WHERE subject=?", (node,)
    )
    incoming_total = _total(
        connection, "SELECT count(*) FROM graph_relations WHERE object=?", (node,)
    )
    asset_total = _total(
        connection, "SELECT count(*) FROM graph_artifacts WHERE producer_or_consumer=?", (node,)
    )
    contract_total = _total(
        connection, "SELECT count(*) FROM graph_contracts WHERE subject=?", (node,)
    )
    result: dict[str, Any] = {
        "node": node,
        "outgoing": {
            "total": outgoing_total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,predicate,object,object_kind,scope,evidence_target "
                "FROM graph_relations WHERE subject=? ORDER BY predicate,object LIMIT ?",
                (node,),
                limit,
            ),
        },
        "incoming": {
            "total": incoming_total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,subject,predicate,object_kind,scope,evidence_target "
                "FROM graph_relations WHERE object=? ORDER BY predicate,subject LIMIT ?",
                (node,),
                limit,
            ),
        },
        "assets": {
            "total": asset_total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,asset_id,role,operation,schema_or_type,hash_bound,"
                "commit_bound,evidence_target FROM graph_artifacts "
                "WHERE producer_or_consumer=? ORDER BY asset_id LIMIT ?",
                (node,),
                limit,
            ),
        },
        "contracts": {
            "total": contract_total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,contract_id,kind,statement,failure_or_effect,evidence_target "
                "FROM graph_contracts WHERE subject=? ORDER BY ordinal LIMIT ?",
                (node,),
                limit,
            ),
        },
    }
    if not (outgoing_total or incoming_total or asset_total or contract_total):
        result["no_match"] = _empty_lookup_hint(connection, node)
    return result


def asset_graph(connection, asset_id: str, *, limit: int) -> dict[str, Any]:
    """Who produces or consumes one asset, and which documents describe it.

    This is the entry point from an observed artifact back into the corpus.
    """
    total = _total(connection, "SELECT count(*) FROM graph_artifacts WHERE asset_id=?", (asset_id,))
    edge_total = _total(
        connection, "SELECT count(*) FROM graph_relations WHERE object=?", (asset_id,)
    )
    result: dict[str, Any] = {
        "asset_id": asset_id,
        "declarations": {
            "total": total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,role,operation,producer_or_consumer,schema_or_type,"
                "hash_bound,commit_bound,evidence_target FROM graph_artifacts "
                "WHERE asset_id=? ORDER BY artifact_id,ordinal LIMIT ?",
                (asset_id,),
                limit,
            ),
        },
        "edges": {
            "total": edge_total,
            "rows": _rows(
                connection,
                "SELECT artifact_id,subject,predicate,scope,evidence_target "
                "FROM graph_relations WHERE object=? ORDER BY predicate,subject LIMIT ?",
                (asset_id,),
                limit,
            ),
        },
    }
    if not (total or edge_total):
        result["no_match"] = _empty_lookup_hint(connection, asset_id)
    return result


def predicate_graph(connection, predicate: str, *, limit: int) -> dict[str, Any]:
    total = _total(
        connection, "SELECT count(*) FROM graph_relations WHERE predicate=?", (predicate,)
    )
    result: dict[str, Any] = {
        "predicate": predicate,
        "total": total,
        "rows": _rows(
            connection,
            "SELECT artifact_id,subject,object,object_kind,scope,evidence_target "
            "FROM graph_relations WHERE predicate=? ORDER BY subject,object LIMIT ?",
            (predicate,),
            limit,
        ),
    }
    if not total:
        # a predicate comes from a closed set, so the useful hint is the set itself
        result["no_match"] = {
            "reason": "no relation carries this predicate",
            "hint": "predicates are a closed vocabulary; one of the declared values below",
            "declared": sorted(DECLARED_VOCABULARY[("graph_relations", "predicate")]),
        }
    return result


def vocabulary_report(connection) -> dict[str, Any]:
    """Stored values that fall outside their declared set, with counts and example files."""
    deviations: list[dict[str, Any]] = []
    for (table, column), declared in sorted(DECLARED_VOCABULARY.items()):
        rows = connection.execute(
            f"SELECT {column} AS value,count(*) AS occurrences,"
            f"min(artifact_id) AS first_artifact FROM {table} GROUP BY {column}"
        ).fetchall()
        for row in rows:
            if str(row["value"]) not in declared:
                deviations.append(
                    {
                        "table": table,
                        "column": column,
                        "value": row["value"],
                        "occurrences": int(row["occurrences"]),
                        "first_artifact": row["first_artifact"],
                    }
                )
    totals = {
        table: _total(connection, f"SELECT count(*) FROM {table}", ())
        for table in _TABLES
    }
    deviations.sort(key=lambda item: (-item["occurrences"], item["table"], str(item["value"])))
    return {
        "schema": "kairos-graph-vocabulary/v1",
        "row_totals": totals,
        "deviation_count": len(deviations),
        "deviations": deviations,
    }


_EVIDENCE_TABLES = ("graph_relations", "graph_contracts", "graph_artifacts", "graph_drift")


def integrity_report(connection, *, limit: int) -> dict[str, Any]:
    """Graph rows whose evidence anchor names no section of their declaring document.

    A graph fact is only navigable if its anchor resolves, so a dangling anchor breaks the
    chain from fact to evidence. It is reported rather than rejected, for the same reason a
    deviating vocabulary value is: one broken row must not cost a whole document.
    """
    dangling: list[dict[str, Any]] = []
    checked = 0
    for table in _EVIDENCE_TABLES:
        checked += _total(
            connection,
            f"SELECT count(*) FROM {table} WHERE evidence_target<>''",
            (),
        )
        rows = connection.execute(
            f"""
            SELECT g.artifact_id,g.ordinal,g.evidence_target
            FROM {table} g
            LEFT JOIN sections s
              ON s.artifact_id=g.artifact_id AND s.section_id=g.evidence_target
            WHERE g.evidence_target<>'' AND s.section_id IS NULL
            ORDER BY g.artifact_id,g.ordinal
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            dangling.append({"table": table, **dict(row)})
    return {
        "schema": "kairos-graph-integrity/v1",
        "anchored_rows_checked": checked,
        "dangling_count": len(dangling),
        "dangling": dangling,
    }


def census(connection) -> dict[str, Any]:
    """Deterministic census of the graph layer, computed here and never taken on trust."""
    documents = _total(
        connection,
        "SELECT count(DISTINCT artifact_id) FROM graph_relations",
        (),
    )
    distinct_nodes = _total(
        connection,
        "SELECT count(*) FROM (SELECT subject AS node FROM graph_relations "
        "UNION SELECT object FROM graph_relations)",
        (),
    )
    return {
        "schema": "kairos-graph-census/v1",
        "graph_bearing_documents": documents,
        "distinct_nodes": distinct_nodes,
        "row_totals": {
            table: _total(connection, f"SELECT count(*) FROM {table}", ())
            for table in _TABLES
        },
        "predicates": [
            dict(row)
            for row in connection.execute(
                "SELECT predicate,count(*) AS occurrences FROM graph_relations "
                "GROUP BY predicate ORDER BY occurrences DESC,predicate"
            ).fetchall()
        ],
        "object_kinds": [
            dict(row)
            for row in connection.execute(
                "SELECT object_kind,count(*) AS occurrences FROM graph_relations "
                "GROUP BY object_kind ORDER BY occurrences DESC,object_kind"
            ).fetchall()
        ],
    }


def run_graph_query(
    database: KnowledgeDatabase,
    *,
    artifact: str = "",
    node: str = "",
    asset: str = "",
    predicate: str = "",
    resolve: str = "",
    chase: str = "",
    inventory_table: str = "",
    field: str = "",
    value: str = "",
    max_hops: int = 2,
    vocabulary: bool = False,
    integrity: bool = False,
    show_census: bool = False,
    limit: int = DEFAULT_GRAPH_ROWS,
) -> dict[str, Any]:
    # with --chase, --predicate narrows the walk instead of selecting a query
    selectors = [
        bool(artifact), bool(node), bool(asset), bool(predicate) and not chase,
        bool(resolve), bool(chase), bool(inventory_table),
        vocabulary, integrity, show_census,
    ]
    if sum(1 for value_ in selectors if value_) != 1:
        raise GraphError(
            "graph requires exactly one of --artifact, --node, --asset, --predicate, "
            "--resolve, --chase, --inventory, --vocabulary, --integrity or --census"
        )
    bound = _bounded(limit)
    connection = database.connect(read_only=True)
    try:
        if inventory_table:
            return inventory(connection, inventory_table, field=field,
                             value=value, limit=bound)
        if resolve:
            return resolve_identity(connection, resolve, limit=bound)
        if chase:
            seeds = tuple(part for part in (p.strip() for p in chase.split(",")) if part)
            filters = tuple(p for p in (predicate or "").split(",") if p)
            return graph_chase(connection, seeds, max_hops=max_hops,
                               limit=bound, predicates=filters)
        if artifact:
            return artifact_graph(connection, artifact, limit=bound)
        if node:
            return node_graph(connection, node, limit=bound)
        if asset:
            return asset_graph(connection, asset, limit=bound)
        if predicate:
            return predicate_graph(connection, predicate, limit=bound)
        if vocabulary:
            return vocabulary_report(connection)
        if integrity:
            return integrity_report(connection, limit=bound)
        return census(connection)
    finally:
        connection.close()
