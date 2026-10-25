from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from .constants import MAX_QUERY_CHARS


STOPWORDS = {
    "a",
    "about",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "which",
    "where",
    "why",
    "with",
}

INTENT_PHRASES = {
    "orientation": ("where am i", "what should i read first", "orientation", "navigation", "entry point"),
    "architecture": ("architecture", "system structure", "core structure", "database model", "database entities", "cross-document pointers", "how is kairos organized"),
    "content_placement": ("where should i put", "content placement", "which folder", "where to write", "future content", "should receive", "be documented", "be stored"),
    "contextual_knowledge": ("domain knowledge", "cultural knowledge", "contextual knowledge", "external knowledge", "research finding", "cultural convention", "domain research", "stable synthesis"),
    "promotion": ("become searchable", "without a full scan", "document promotion", "same-heartbeat promotion", "same heartbeat promotion"),
    "current_state": ("current state", "current status", "what is active", "active task", "live state", "status"),
    "authority": ("canonical", "authority", "approved", "release", "may finalize", "decides whether"),
    "goal_gap": ("what is missing", "which blocker", "reach the goal", "open criterion", "missing"),
    "root_cause": ("why", "root cause", "caused", "reason", "failure mechanism"),
    "dependency": ("depends", "requires", "prerequisite", "blocked by", "dependency"),
    "implementation_location": ("where implemented", "which function", "which file", "code path", "implemented"),
    "producer": ("who produces", "what produces", "which module produces", "producer", "produce", "produces", "write", "writes", "commit", "commits", "emit", "emits"),
    "consumer": ("who consumes", "what consumes", "which module consumes", "consumer", "consume", "consumes", "read", "reads", "verify", "verifies"),
    "graph_contract": ("which contract", "contract invariant", "precondition", "postcondition", "failure literal"),
    "graph_drift": ("graph drift", "historical difference", "implementation drift", "route drift", "drift"),
    "graph_context": ("graph", "call chain", "data flow", "upstream", "downstream"),
    "evidence": ("what proves", "proof", "evidence", "supported by"),
    "validation": ("which test", "validated", "verification", "validation", "test"),
    "resolution": ("how fixed", "solution", "fix", "fixed", "repaired", "resolved", "resolution", "remedy"),
    "decision_rationale": ("why decided", "rationale", "alternative", "decision", "tradeoff"),
    "chronology": ("before", "after", "chronology", "history", "superseded"),
    "contradiction": ("conflict", "contradiction", "contradicts", "inconsistent"),
    "experience": ("experience", "what worked", "lessons learned", "under which conditions"),
}

INTENT_RELATIONS = {
    "architecture": ("references", "belongs_to", "depends_on", "produces"),
    "content_placement": ("belongs_to", "documents", "produces", "references"),
    "contextual_knowledge": ("derived_from", "informs", "documents", "references"),
    "promotion": ("produces", "implemented_by", "validates", "references"),
    "root_cause": ("caused_by", "fixes", "resolves", "next", "validated_by", "documents"),
    "dependency": ("depends_on", "requires", "unblocks"),
    "implementation_location": ("implemented_by", "implements"),
    "producer": ("produces", "writes", "commits"),
    "consumer": ("consumes", "reads", "validates"),
    "graph_contract": ("constrained_by", "gates", "binds"),
    "graph_drift": ("drifts_from", "supersedes"),
    "graph_context": (
        "depends_on", "calls", "delegates_to", "precedes", "produces", "consumes",
        "reads", "writes", "commits", "validates", "gates", "binds",
    ),
    "evidence": ("evidenced_by", "supports"),
    "validation": ("validated_by", "validates"),
    "resolution": ("fixes", "resolves", "next", "validated_by"),
    "contradiction": ("contradicts",),
    "chronology": ("supersedes", "derived_from", "next"),
    "goal_gap": ("satisfies", "requires", "unblocks"),
}

COMPOUND_LEXEMES = {
    "architecture",
    "artifact",
    "context",
    "cultural",
    "database",
    "document",
    "evidence",
    "finalization",
    "knowledge",
    "metadata",
    "promotion",
    "research",
    "runtime",
    "search",
    "validation",
}


@dataclass(frozen=True)
class QueryFrame:
    query: str
    tokens: tuple[str, ...]
    exact_identifiers: tuple[str, ...]
    graph_identifiers: tuple[str, ...]
    intents: tuple[str, ...]
    preferred_relations: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("\\", "/").strip()


def _camel_split(text: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)


def tokenize(text: str) -> list[str]:
    prepared = _camel_split(unicodedata.normalize("NFKC", text)).replace("_", " ").replace("-", " ")
    normalized = normalize(prepared)
    raw = re.findall(r"[^\W_]+", normalized, re.UNICODE)
    result: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if len(token) < 2 or token in STOPWORDS:
            continue
        for value in (token, *sorted(lexeme for lexeme in COMPOUND_LEXEMES if len(token) > len(lexeme) + 2 and lexeme in token)):
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


GRAPH_QUOTED_RE = re.compile(
    r'''(?P<quote>["'`])(?P<value>[^"'`\r\n]{2,512})(?P=quote)'''
)
GRAPH_LITERAL_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_.:-])(
        (?:[A-Za-z]:)?[A-Za-z0-9_.:+-]+(?:[\\/][A-Za-z0-9_.:+-]+)+
        | [A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+
        | [A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+
        | (?:FAIL|ERROR|ERR|E)_[A-Za-z0-9_.:-]+
        | D[0-9]{1,6}
        | [A-Za-z][A-Za-z0-9-]*_[A-Za-z0-9_.:-]+
        | [A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+
        | [A-Z][A-Z0-9]{1,63}
    )(?![A-Za-z0-9_.:-])
    """,
    re.VERBOSE,
)

GRAPH_ROUTING_INTENTS = {
    "producer", "consumer", "graph_contract", "graph_drift", "graph_context",
}


def _graph_identifiers(query: str) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        candidate = unicodedata.normalize("NFKC", value).strip(" \t\r\n,;!?()[]{}")
        candidate = candidate.replace("\\", "/")
        key = candidate.casefold()
        if 2 <= len(candidate) <= 512 and key not in seen:
            seen.add(key)
            values.append(candidate)

    for match in GRAPH_QUOTED_RE.finditer(query):
        add(match.group("value"))
    for match in GRAPH_LITERAL_RE.finditer(query):
        add(match.group(1))
    return tuple(values[:16])


def _contains_intent_phrase(normalized_query: str, phrase: str) -> bool:
    normalized_phrase = normalize(phrase)
    if re.fullmatch(r"[\w ]+", normalized_phrase, re.UNICODE):
        words = normalized_phrase.split()
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"
        return re.search(pattern, normalized_query, re.UNICODE) is not None
    return normalized_phrase in normalized_query


def compile_query(query: str) -> QueryFrame:
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query uses {len(query)} characters; maximum is {MAX_QUERY_CHARS}")
    identifiers = tuple(
        dict.fromkeys(
            match.upper()
            for match in re.findall(r"\b(?:TASK|REPORT|BUG|CODE|GOAL|MILESTONE|CRIT|AC|M)_[A-Z0-9_.:-]+\b", query, re.I)
        )
    )[:16]
    normalized = normalize(query)
    token_values = tokenize(query)
    graph_identifiers = _graph_identifiers(query)
    intents: list[str] = []
    for intent, phrases in INTENT_PHRASES.items():
        if any(_contains_intent_phrase(normalized, phrase) for phrase in phrases):
            intents.append(intent)
    if not intents:
        intents.append("orientation")
    relations: list[str] = []
    for intent in intents:
        # Graph intents are consumed by the normalized graph ranker itself. They
        # must never leak into the legacy document-relation scoring surface.
        if intent in GRAPH_ROUTING_INTENTS:
            continue
        for relation in INTENT_RELATIONS.get(intent, ()):
            if relation not in relations:
                relations.append(relation)
    return QueryFrame(
        query=query.strip(),
        tokens=tuple(token_values[:32]),
        exact_identifiers=identifiers,
        graph_identifiers=graph_identifiers,
        intents=tuple(intents),
        preferred_relations=tuple(relations),
    )


def fts_match(tokens: list[str] | tuple[str, ...]) -> str:
    safe: list[str] = []
    for token in tokens:
        cleaned = token.replace('"', "").strip()
        if cleaned:
            safe.append(f'"{cleaned}"')
    if not safe:
        raise ValueError("query contains no searchable tokens")
    return " OR ".join(safe)
