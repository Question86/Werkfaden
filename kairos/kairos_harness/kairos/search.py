from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .constants import (
    MAX_CONTEXT_CHASE_RESULTS,
    MAX_CONTEXT_HOPS,
    MAX_RETRIEVAL_TRACES,
    MAX_SEARCH_CANDIDATES,
    MAX_SEARCH_RESULTS,
)
from .database import KnowledgeDatabase
from .graph import graph_chase, query_graph_context
from .query import QueryFrame, compile_query, fts_match, tokenize
from .util import json_dumps, sha256_text, utc_now


AUTHORITY_BOOSTS = {
    "state_authority": 40.0,
    "architecture_authority": 34.0,
    "goal_authority": 32.0,
    "task_contract": 26.0,
    "execution_evidence": 24.0,
    "validation_evidence": 24.0,
    "implementation_documentation": 16.0,
    "research_evidence": 18.0,
    "operating_contract": 30.0,
    "diagnostic_record": 12.0,
    "loop_archive": 10.0,
    "routing": 5.0,
}

INTENT_TYPE_PREFERENCES = {
    "architecture": {"documentation", "router"},
    "content_placement": {"documentation", "router"},
    "contextual_knowledge": {"documentation", "research", "decision"},
    "promotion": {"documentation", "report", "code"},
    "root_cause": {"bug", "report", "research"},
    "implementation_location": {"code", "report"},
    "validation": {"report", "code"},
    "evidence": {"report", "research", "archive"},
    "goal_gap": {"task", "router", "report"},
    "authority": {"gate", "router", "archive", "documentation"},
    "current_state": {"gate", "session", "router"},
    "decision_rationale": {"decision", "report"},
}


def _candidate_rows(connection, frame: QueryFrame, candidate_limit: int):
    if not frame.tokens:
        return []
    match = fts_match(frame.tokens)
    return connection.execute(
        """
        SELECT
            f.artifact_id,
            f.section_id,
            f.title,
            f.capsule AS search_capsule,
            s.capsule AS section_capsule,
            a.path,
            a.document_type,
            a.state,
            a.authority,
            a.route,
            a.goal_id,
            a.milestone_id,
            a.task_id,
            a.updated_at,
            a.claim_boundary,
            bm25(section_fts,0.0,0.0,10.0,8.0,12.0,6.0,4.0,1.0) AS fts_rank
        FROM section_fts f
        JOIN sections s ON s.artifact_id=f.artifact_id AND s.section_id=f.section_id
        JOIN artifacts a ON a.artifact_id=f.artifact_id
        WHERE section_fts MATCH ?
        ORDER BY fts_rank
        LIMIT ?
        """,
        (match, candidate_limit),
    ).fetchall()


def _exact_rows(connection, identifiers: tuple[str, ...], candidate_limit: int):
    rows = []
    for identifier in identifiers:
        remaining = candidate_limit - len(rows)
        if remaining <= 0:
            break
        rows.extend(
            connection.execute(
                """
                SELECT
                    a.artifact_id,
                    s.section_id,
                    s.title,
                    s.capsule AS search_capsule,
                    s.capsule AS section_capsule,
                    a.path,
                    a.document_type,
                    a.state,
                    a.authority,
                    a.route,
                    a.goal_id,
                    a.milestone_id,
                    a.task_id,
                    a.updated_at,
                    a.claim_boundary,
                    -100.0 AS fts_rank
                FROM artifacts a
                JOIN sections s ON s.artifact_id=a.artifact_id
                WHERE a.artifact_id=? OR a.task_id=? OR a.goal_id=? OR a.milestone_id=?
                ORDER BY s.position
                LIMIT ?
                """,
                (identifier, identifier, identifier, identifier, remaining),
            ).fetchall()
        )
    return rows


MAX_PATH_DOCUMENTS = 12
MAX_PATH_SECTIONS = 4


def _path_rows(connection, frame: QueryFrame, candidate_limit: int):
    """Documents whose own name carries the query, whether or not FTS found them.

    Full-text tokenisation is weak on underscore-heavy names, so a document called
    `runtime_intake_parser_extraction_semantics_v1` can lose to any document that merely
    mentions the words. Matching the path directly is the standing repair for that, and
    it is a second candidate source rather than a reranking: a document FTS never
    returned can never be reranked into view.

    Ordered token pairs go first because a compound is far more selective than a word.
    """
    tokens = [token for token in frame.tokens if len(token) >= 4][:12]
    if not tokens:
        return []
    compounds: list[str] = []
    for index, left in enumerate(tokens):
        for right in tokens[index + 1:]:
            compounds.append(f"%{left}_{right}%")
    patterns = compounds[:24] or [f"%{token}%" for token in tokens[:8]]
    clause = " OR ".join("a.path LIKE ?" for _ in patterns)
    documents = connection.execute(
        f"""
        SELECT a.artifact_id, count(*) AS hits
        FROM artifacts a
        WHERE {clause}
        GROUP BY a.artifact_id
        ORDER BY hits DESC, a.path
        LIMIT ?
        """,
        (*patterns, MAX_PATH_DOCUMENTS),
    ).fetchall()
    if not documents:
        return []
    identifiers = [str(row["artifact_id"]) for row in documents]
    placeholders = ",".join("?" for _ in identifiers)
    return connection.execute(
        f"""
        SELECT * FROM (
            SELECT
                a.artifact_id,
                s.section_id,
                s.title,
                s.capsule AS search_capsule,
                s.capsule AS section_capsule,
                a.path,
                a.document_type,
                a.state,
                a.authority,
                a.route,
                a.goal_id,
                a.milestone_id,
                a.task_id,
                a.updated_at,
                a.claim_boundary,
                row_number() OVER (PARTITION BY a.artifact_id ORDER BY s.position) AS rank_in_document
            FROM artifacts a
            JOIN sections s ON s.artifact_id=a.artifact_id
            WHERE a.artifact_id IN ({placeholders})
        )
        WHERE rank_in_document <= ?
        LIMIT ?
        """,
        (*identifiers, MAX_PATH_SECTIONS, candidate_limit),
    ).fetchall()


def _section_row_for_graph_evidence(connection, artifact_id: str, section_id: str):
    return connection.execute(
        """
        SELECT
            a.artifact_id,
            s.section_id,
            s.title,
            s.capsule AS search_capsule,
            s.capsule AS section_capsule,
            a.path,
            a.document_type,
            a.state,
            a.authority,
            a.route,
            a.goal_id,
            a.milestone_id,
            a.task_id,
            a.updated_at,
            a.claim_boundary,
            0.0 AS fts_rank
        FROM artifacts a
        JOIN sections s ON s.artifact_id=a.artifact_id
        WHERE a.artifact_id=? AND s.section_id=?
        LIMIT 1
        """,
        (artifact_id, section_id),
    ).fetchone()


def _graph_bonus(facts: list[dict[str, Any]]) -> tuple[float, list[str]]:
    if not facts:
        return 0.0, []
    strongest = max(
        facts,
        key=lambda fact: (
            float(fact["semantic_score"]),
            fact["table"],
            -int(fact["ordinal"]),
        ),
    )
    score = float(strongest["semantic_score"])
    fields = ",".join(str(value) for value in strongest["matched_fields"])
    reasons = [
        f"normalized_graph={strongest['table']}:{fields}",
        *[f"graph_{reason}" for reason in strongest["semantic_reasons"]],
    ]
    return score, reasons


def _graph_evidence_anchors(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for fact in facts:
        key = (str(fact["table"]), str(fact["artifact_id"]), int(fact["ordinal"]))
        if key in seen:
            continue
        seen.add(key)
        anchors.append(
            {
                "table": fact["table"],
                "artifact_id": fact["artifact_id"],
                "ordinal": int(fact["ordinal"]),
                "evidence_path": fact["evidence_path"],
                "matched_fields": list(fact["matched_fields"]),
            }
        )
        if len(anchors) >= 5:
            break
    return anchors


def _candidate_features(
    connection,
    rows,
    query: str,
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str | None], set[str]],
    set[tuple[str, str]],
]:
    artifact_ids = sorted({str(row["artifact_id"]) for row in rows})
    if not artifact_ids:
        return {}, {}, set()
    placeholders = ",".join("?" for _ in artifact_ids)
    handles: dict[tuple[str, str], set[str]] = {}
    for row in connection.execute(
        f"SELECT artifact_id,section_id,intent FROM query_handles WHERE artifact_id IN ({placeholders})",
        artifact_ids,
    ).fetchall():
        handles.setdefault((str(row["artifact_id"]), str(row["section_id"])), set()).add(str(row["intent"]))
    exact_questions = {
        (str(row["artifact_id"]), str(row["section_id"]))
        for row in connection.execute(
            f"SELECT artifact_id,section_id FROM query_handles "
            f"WHERE artifact_id IN ({placeholders}) AND question=? COLLATE NOCASE",
            [*artifact_ids, query.strip()],
        ).fetchall()
    }
    predicates: dict[tuple[str, str | None], set[str]] = {}
    for row in connection.execute(
        f"SELECT source_artifact_id,source_section_id,predicate FROM relations "
        f"WHERE source_artifact_id IN ({placeholders})",
        artifact_ids,
    ).fetchall():
        key = (
            str(row["source_artifact_id"]),
            str(row["source_section_id"]) if row["source_section_id"] is not None else None,
        )
        predicates.setdefault(key, set()).add(str(row["predicate"]))
    return handles, predicates, exact_questions


def _outgoing_relations(connection, artifact_id: str, section_id: str | None = None) -> list[dict[str, Any]]:
    if section_id:
        rows = connection.execute(
            """
            SELECT * FROM relations
            WHERE source_artifact_id=? AND (source_section_id=? OR source_section_id IS NULL)
            ORDER BY CASE predicate
                WHEN 'evidenced_by' THEN 1 WHEN 'validated_by' THEN 2
                WHEN 'implemented_by' THEN 3 WHEN 'caused_by' THEN 4
                WHEN 'depends_on' THEN 5 WHEN 'next' THEN 6 ELSE 20 END,
                relation_id
            LIMIT 64
            """,
            (artifact_id, section_id),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM relations WHERE source_artifact_id=? ORDER BY relation_id LIMIT 64",
            (artifact_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json"))
        result.append(item)
    return result


def _path_tokens(path: str) -> tuple[set[str], set[str], str]:
    """Tokens of the whole path, tokens of the file name, and the normalized name."""
    normalized = str(path or "").replace("\\", "/").casefold()
    name = normalized.rsplit("/", 1)[-1]
    for suffix in (".md", ".markdown"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return set(tokenize(normalized)), set(tokenize(name)), name


def _name_affinity(row, frame: QueryFrame) -> tuple[float, list[str]]:
    """How much the document's own name answers the query.

    Sections are ranked by their text, but in a corpus of one document per translation
    unit the file name carries the subject: a query naming `parser_extraction_semantics`
    is asking about `runtime_intake_parser_extraction_semantics_v1`, and body text alone
    ranks that no higher than any document that merely mentions the words. An ordered
    pair of query tokens appearing as one compound in the name is the strongest signal
    available without reading the file.
    """
    path = str(row["path"] if "path" in row.keys() else "")
    if not path or not frame.tokens:
        return 0.0, []
    path_set, name_set, name = _path_tokens(path)
    query_set = set(frame.tokens)
    score = 0.0
    reasons: list[str] = []

    name_hits = query_set & name_set
    if name_hits:
        value = min(48.0, 12.0 * len(name_hits))
        score += value
        reasons.append(f"name_tokens={','.join(sorted(name_hits))} +{value:.0f}")

    path_hits = (query_set & path_set) - name_hits
    if path_hits:
        value = min(16.0, 4.0 * len(path_hits))
        score += value
        reasons.append(f"path_tokens={len(path_hits)} +{value:.0f}")

    flattened = name.replace("-", "_")
    compounds = 0
    ordered = list(frame.tokens)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if f"{left}_{right}" in flattened:
                compounds += 1
    if compounds:
        value = min(40.0, 14.0 * compounds)
        score += value
        reasons.append(f"name_compound={compounds} +{value:.0f}")
    return score, reasons


def _score_row(
    row,
    frame: QueryFrame,
    ordinal: int | None,
    handles: dict[tuple[str, str], set[str]],
    predicates: dict[tuple[str, str | None], set[str]],
    exact_questions: set[tuple[str, str]],
    graph_specificity: int = 0,
) -> tuple[float, list[str]]:
    score = max(0.0, 100.0 - ordinal * 1.5) if ordinal is not None else 0.0
    reasons = [f"fts_order={ordinal}"] if ordinal is not None else ["candidate=normalized_graph"]
    artifact_id = str(row["artifact_id"])
    if artifact_id in frame.exact_identifiers or row["task_id"] in frame.exact_identifiers:
        score += 120.0
        reasons.append("exact_identifier +120")
    authority_boost = AUTHORITY_BOOSTS.get(str(row["authority"]), 0.0)
    if authority_boost:
        score += authority_boost
        reasons.append(f"authority={row['authority']} +{authority_boost:.0f}")
    section_id = str(row["section_id"])
    if (artifact_id, section_id) in exact_questions:
        score += 180.0
        reasons.append("exact_answer_question +180")
    handle_intents = handles.get((artifact_id, section_id), set())
    intent_hits = handle_intents & set(frame.intents)
    if intent_hits:
        value = 28.0 * len(intent_hits)
        score += value
        reasons.append(f"answer_intent={','.join(sorted(intent_hits))} +{value:.0f}")
    preferred_types: set[str] = set()
    for intent in frame.intents:
        preferred_types.update(INTENT_TYPE_PREFERENCES.get(intent, set()))
    if row["document_type"] in preferred_types:
        score += 14.0
        reasons.append(f"type={row['document_type']} +14")
    relation_predicates = predicates.get((artifact_id, section_id), set()) | predicates.get((artifact_id, None), set())
    relation_hits = relation_predicates & set(frame.preferred_relations)
    if relation_hits:
        value = 8.0 * len(relation_hits)
        score += value
        reasons.append(f"causal_edges={','.join(sorted(relation_hits))} +{value:.0f}")
    # An exact document identifier is worth +120. A graph row that matched the query
    # literally is the same kind of evidence one level down — the entity was named and
    # this section is where it is declared — and it scored nothing at all until now.
    if graph_specificity:
        value = 60.0 + 40.0 * (graph_specificity - 1)
        score += value
        reasons.append(f"graph_identity x{graph_specificity} +{value:.0f}")
    name_score, name_reasons = _name_affinity(row, frame)
    if name_score:
        score += name_score
        reasons.extend(name_reasons)
    if row["state"] in {"superseded", "failed"} and "chronology" not in frame.intents:
        score -= 18.0
        reasons.append(f"state={row['state']} -18")
    return score, reasons


def _resolve_relation_target(connection, relation: dict[str, Any]) -> dict[str, Any] | None:
    if relation.get("target_artifact_id"):
        row = connection.execute(
            """
            SELECT a.artifact_id,a.path,a.document_type,a.authority,a.state,s.section_id,s.title,s.capsule
            FROM artifacts a
            JOIN sections s ON s.artifact_id=a.artifact_id
            WHERE a.artifact_id=? AND (? IS NULL OR s.section_id=?)
            ORDER BY s.position LIMIT 1
            """,
            (
                relation["target_artifact_id"],
                relation.get("target_section_id"),
                relation.get("target_section_id"),
            ),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT a.artifact_id,a.path,a.document_type,a.authority,a.state,s.section_id,s.title,s.capsule
            FROM artifacts a
            JOIN sections s ON s.artifact_id=a.artifact_id
            WHERE a.path=? AND (? IS NULL OR s.section_id=?)
            ORDER BY s.position LIMIT 1
            """,
            (relation["target_path"], relation.get("target_section_id"), relation.get("target_section_id")),
        ).fetchone()
    return dict(row) if row else None


def _context_chase(connection, primary: dict[str, Any], frame: QueryFrame, mode: str, max_hops: int) -> list[dict[str, Any]]:
    if mode in {"breathe", "work", "verify"} or max_hops <= 0:
        return []
    frontier = [(primary["artifact_id"], primary["section_id"], 0)]
    visited = {(primary["artifact_id"], primary["section_id"])}
    chase: list[dict[str, Any]] = []
    while frontier and len(chase) < MAX_CONTEXT_CHASE_RESULTS:
        artifact_id, section_id, depth = frontier.pop(0)
        if depth >= max_hops:
            continue
        relations = _outgoing_relations(connection, artifact_id, section_id)
        relations.sort(
            key=lambda value: (
                0 if value["predicate"] in frame.preferred_relations else 1,
                value["predicate"],
                value["target_path"],
            )
        )
        if mode == "depth":
            relations = relations[:1]
        else:
            relations = relations[:5]
        for relation in relations:
            if len(chase) >= MAX_CONTEXT_CHASE_RESULTS:
                break
            target = _resolve_relation_target(connection, relation)
            if not target:
                continue
            key = (target["artifact_id"], target["section_id"])
            if key in visited:
                continue
            visited.add(key)
            chase.append(
                {
                    "depth": depth + 1,
                    "via": relation["predicate"],
                    "artifact_id": target["artifact_id"],
                    "section_id": target["section_id"],
                    "path": f"{target['path']}#{target['section_id']}",
                    "title": target["title"],
                    "capsule": target["capsule"],
                    "authority": target["authority"],
                    "state": target["state"],
                }
            )
            frontier.append((target["artifact_id"], target["section_id"], depth + 1))
    return chase


def search_database(
    database: KnowledgeDatabase,
    query: str,
    *,
    limit: int = 10,
    candidate_limit: int = 100,
    mode: str = "breadth",
    max_hops: int = 2,
    record_trace: bool = False,
) -> dict[str, Any]:
    if mode not in {"depth", "breadth", "breathe", "work", "verify"}:
        raise ValueError(f"unsupported search mode: {mode}")
    if not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
    if not limit <= candidate_limit <= MAX_SEARCH_CANDIDATES:
        raise ValueError(
            f"candidate_limit must be between limit ({limit}) and {MAX_SEARCH_CANDIDATES}"
        )
    if not 0 <= max_hops <= MAX_CONTEXT_HOPS:
        raise ValueError(f"max_hops must be between 0 and {MAX_CONTEXT_HOPS}")
    started = time.perf_counter()
    frame = compile_query(query)
    if not frame.tokens and not frame.graph_identifiers:
        raise ValueError("query contains no searchable tokens")
    connection = database.connect(read_only=True)
    try:
        fts_rows = list(_candidate_rows(connection, frame, candidate_limit))
        exact_rows = list(
            _exact_rows(connection, frame.exact_identifiers, candidate_limit)
        )
        path_rows = list(_path_rows(connection, frame, candidate_limit))
        graph_context = query_graph_context(
            connection,
            frame,
            limit=candidate_limit,
        )
        # The document chase follows header references and lands on routers. When the
        # query named entities the code graph knows, walk that graph too — it is the
        # chain from a failure literal back to whatever gates it.
        chase_seeds = tuple(
            dict.fromkeys(
                identifier
                for fact in graph_context["facts"]
                for identifier in fact["matched_identifiers"]
            )
        )[:8]
        graph_chain = (
            graph_chase(
                connection,
                chase_seeds,
                max_hops=min(max_hops, 3),
                limit=min(candidate_limit, 120),
                predicates=tuple(frame.preferred_relations),
            )
            if chase_seeds and max_hops > 0
            else {"schema": "kairos-graph-chase/v1", "seeds": [], "max_hops": max_hops,
                  "hops": [], "nodes_reached": 0, "edges_returned": 0, "truncated": False}
        )
        legacy_ordinals: dict[tuple[str, str], int] = {}
        fts_by_key: dict[tuple[str, str], Any] = {}
        for ordinal, row in enumerate(fts_rows):
            key = (str(row["artifact_id"]), str(row["section_id"]))
            fts_by_key.setdefault(key, row)
            legacy_ordinals.setdefault(key, ordinal)
        exact_by_key: dict[tuple[str, str], Any] = {}
        for row in exact_rows:
            key = (str(row["artifact_id"]), str(row["section_id"]))
            exact_by_key.setdefault(key, row)
        graph_matches: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for fact in graph_context["facts"]:
            if not fact["evidence_resolved"]:
                continue
            key = (str(fact["artifact_id"]), str(fact["evidence_target"]))
            graph_matches.setdefault(key, []).append(fact)
        graph_fail_closed = (
            int(graph_context["seed_total"]) > 0
            and int(graph_context["resolved_at_max_specificity_total"]) == 0
        )
        unique: dict[tuple[str, str], Any] = {}

        def add_candidate(key: tuple[str, str], row) -> None:
            if row is not None and key not in unique and len(unique) < candidate_limit:
                unique[key] = row

        if not graph_fail_closed:
            for key in graph_matches:
                row = fts_by_key.get(key) or exact_by_key.get(key)
                if row is None:
                    row = _section_row_for_graph_evidence(connection, *key)
                add_candidate(key, row)
            for key, row in exact_by_key.items():
                add_candidate(key, row)
            for key, row in fts_by_key.items():
                add_candidate(key, row)
            # last, so it never displaces a full-text or graph candidate, but a document
            # whose name answers the query is at least in the running
            for row in path_rows:
                add_candidate((str(row["artifact_id"]), str(row["section_id"])), row)
        handles, predicates, exact_questions = _candidate_features(
            connection,
            list(unique.values()),
            query,
        )
        role_intent = bool(
            frame.graph_identifiers
            and {"producer", "consumer"} & set(frame.intents)
        )
        scored: list[dict[str, Any]] = []
        for key, row in unique.items():
            score, reasons = _score_row(
                row,
                frame,
                legacy_ordinals.get(key),
                handles,
                predicates,
                exact_questions,
                max(
                    (int(fact["matched_identifier_count"])
                     for fact in graph_matches.get(key, ())),
                    default=0,
                ),
            )
            matched_facts = graph_matches.get(key, [])
            graph_bonus, graph_reasons = _graph_bonus(matched_facts)
            score += graph_bonus
            reasons.extend(graph_reasons)
            item = {
                "artifact_id": row["artifact_id"],
                "section_id": row["section_id"],
                "path": f"{row['path']}#{row['section_id']}",
                "document_type": row["document_type"],
                "state": row["state"],
                "authority": row["authority"],
                "route": row["route"],
                "goal_id": row["goal_id"],
                "milestone_id": row["milestone_id"],
                "task_id": row["task_id"],
                "title": row["title"],
                "capsule": row["section_capsule"],
                "claim_boundary": row["claim_boundary"],
                "updated_at": row["updated_at"],
                "score": round(score, 3),
                "reasons": reasons,
            }
            if matched_facts:
                item["graph_match_count"] = len(matched_facts)
                item["graph_evidence_anchors"] = _graph_evidence_anchors(matched_facts)
                item["graph_intent_role_match"] = any(
                    reason.startswith(("producer_role", "consumer_role"))
                    for fact in matched_facts
                    for reason in fact["semantic_reasons"]
                )
                item["graph_identifier_specificity"] = max(
                    int(fact["matched_identifier_count"])
                    for fact in matched_facts
                )
            role_match = bool(item.get("graph_intent_role_match"))
            item["rank_basis"] = {
                "graph_intent_role_tier": 0 if not role_intent or role_match else 1,
                "graph_identifier_specificity": int(
                    item.get("graph_identifier_specificity", 0)
                ),
                "score": item["score"],
                "path": item["path"],
            }
            scored.append(item)
        scored.sort(
            key=lambda item: (
                item["rank_basis"]["graph_intent_role_tier"],
                -item["rank_basis"]["graph_identifier_specificity"],
                -item["rank_basis"]["score"],
                item["rank_basis"]["path"],
            )
        )
        results = scored[:limit]
        primary = results[0] if results else None
        chase = _context_chase(connection, primary, frame, mode, max_hops) if primary else []
    finally:
        connection.close()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    payload = {
        "query": query,
        "query_frame": frame.as_dict(),
        "mode": mode,
        "retrieval_surfaces": [
            "section_fts",
            *(["document_path"] if path_rows else []),
            *(["normalized_graph"] if frame.graph_identifiers else []),
        ],
        "ranking_basis": [
            "graph_intent_role_tier ASC",
            "graph_identifier_specificity DESC",
            "score DESC",
            "path ASC",
        ],
        "graph_context": graph_context,
        "graph_route_status": (
            "UNRESOLVED_EVIDENCE"
            if graph_fail_closed
            else "RESOLVED"
            if graph_matches
            else "NO_GRAPH_MATCH"
        ),
        "candidate_count": len(scored),
        "result_count": len(results),
        "elapsed_ms": round(elapsed_ms, 3),
        "primary": primary,
        "context_chase": chase,
        "graph_chase": graph_chain,
        "results": results,
    }
    if mode == "breathe":
        payload["checkpoint"] = {
            "focus": primary["capsule"] if primary else "No matching context",
            "retain": [item["path"] for item in results[:3]],
            "release": "Discard non-selected candidate bodies; reload only the retained section anchors.",
        }
    if record_trace:
        trace_id = "RT_" + sha256_text(f"{utc_now()}|{query}|{mode}|{elapsed_ms}")[:32]
        with database.transaction() as write_connection:
            write_connection.execute(
                """
                INSERT INTO retrieval_traces(
                    trace_id,query_text,query_frame_json,result_json,mode,elapsed_ms,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    trace_id,
                    query,
                    json_dumps(frame.as_dict(), pretty=False),
                    json_dumps(
                        {
                            "primary": primary,
                            "context_chase": chase,
                            "results": results,
                            "retrieval_surfaces": payload["retrieval_surfaces"],
                            "graph_context": graph_context,
                        },
                        pretty=False,
                    ),
                    mode,
                    elapsed_ms,
                    utc_now(),
                ),
            )
            write_connection.execute(
                """
                DELETE FROM retrieval_traces
                WHERE trace_id NOT IN (
                    SELECT trace_id FROM retrieval_traces
                    ORDER BY created_at DESC,trace_id DESC
                    LIMIT ?
                )
                """,
                (MAX_RETRIEVAL_TRACES,),
            )
        payload["trace_id"] = trace_id
    return payload
