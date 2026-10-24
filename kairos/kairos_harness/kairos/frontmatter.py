from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import (
    AUTHORITIES,
    DOCUMENT_TYPES,
    DOCUMENT_TYPE_AUTHORITIES,
    GRAPH_HEADER_KEYS,
    LIFECYCLE_STATES,
    MAX_ANSWER_HANDLES,
    MAX_CAPSULE_CHARS,
    MAX_CLAIM_BOUNDARY_CHARS,
    MAX_GRAPH_ENTITIES,
    MAX_GRAPH_HEADER_BYTES,
    MAX_HEADER_BYTES,
    MAX_PRIMARY_REFS,
    SCHEMA_VERSION,
)


class HeaderError(ValueError):
    pass


def is_graph_bearing(metadata: dict[str, Any]) -> bool:
    """A header that declares any normalized graph structure is machine calibration."""
    return any(key in metadata for key in GRAPH_HEADER_KEYS)


@dataclass(frozen=True)
class ParsedFrontmatter:
    metadata: dict[str, Any]
    body: str
    raw_header: str
    header_bytes: int
    graph_bearing: bool = False


REQUIRED_KEYS = (
    "schema",
    "id",
    "type",
    "revision",
    "state",
    "authority",
    "workspace",
    "route",
    "updated_at",
    "capsule",
    "claim_boundary",
)

PREFERRED_KEY_ORDER = (
    "schema",
    "id",
    "type",
    "revision",
    "state",
    "authority",
    "workspace",
    "route",
    "loop",
    "task",
    "goal",
    "milestone",
    "updated_at",
    "capsule",
    "claim_boundary",
    "entities",
    "facets",
    "criteria",
    "does_not_answer",
)


def split_frontmatter(text: str) -> ParsedFrontmatter:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("+++\n"):
        raise HeaderError("document must begin at byte 0 with TOML frontmatter delimiter '+++'")
    closing = normalized.find("\n+++\n", 4)
    if closing < 0:
        raise HeaderError("frontmatter closing delimiter '+++' is missing")
    raw_header = normalized[4:closing]
    header_bytes = len(("+++\n" + raw_header + "\n+++\n").encode("utf-8"))
    # The absolute ceiling is checked before parsing so an oversized header cannot be used
    # to exhaust the TOML parser. The tighter routing budget needs the document class and
    # is therefore applied once the header is parsed.
    if header_bytes > MAX_GRAPH_HEADER_BYTES:
        raise HeaderError(f"frontmatter uses {header_bytes} bytes; maximum is {MAX_GRAPH_HEADER_BYTES}")
    try:
        metadata = tomllib.loads(raw_header)
    except tomllib.TOMLDecodeError as exc:
        raise HeaderError(f"invalid TOML frontmatter: {exc}") from exc
    graph_bearing = is_graph_bearing(metadata)
    body = normalized[closing + len("\n+++\n") :]
    validate_metadata(metadata)
    return ParsedFrontmatter(
        metadata=metadata,
        body=body,
        raw_header=raw_header,
        header_bytes=header_bytes,
        graph_bearing=graph_bearing,
    )


def validate_metadata(metadata: dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in metadata]
    if missing:
        raise HeaderError(f"frontmatter missing required keys: {', '.join(missing)}")
    if metadata["schema"] != SCHEMA_VERSION:
        raise HeaderError(f"unsupported schema {metadata['schema']!r}; expected {SCHEMA_VERSION!r}")
    artifact_id = metadata["id"]
    if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_.:-]{2,127}", artifact_id):
        raise HeaderError("id must be a stable upper-case artifact identifier")
    document_type = metadata["type"]
    if document_type not in DOCUMENT_TYPES:
        raise HeaderError(f"unsupported document type: {document_type!r}")
    revision = metadata["revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise HeaderError("revision must be an integer >= 1")
    state = metadata["state"]
    if state not in LIFECYCLE_STATES:
        raise HeaderError(f"unsupported lifecycle state: {state!r}")
    for key in ("authority", "workspace", "route", "updated_at"):
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise HeaderError(f"{key} must be a non-empty string")
    authority = metadata["authority"]
    if authority not in AUTHORITIES:
        raise HeaderError(f"unsupported authority: {authority!r}")
    allowed_authorities = DOCUMENT_TYPE_AUTHORITIES[document_type]
    if authority not in allowed_authorities:
        raise HeaderError(
            f"authority {authority!r} is invalid for document type {document_type!r}; "
            f"expected one of {sorted(allowed_authorities)}"
        )
    if len(metadata["workspace"]) > 128 or len(metadata["route"]) > 512 or len(metadata["updated_at"]) > 64:
        raise HeaderError("workspace, route, or updated_at exceeds its bounded header length")
    if (
        metadata["route"].startswith("/")
        or "\\" in metadata["route"]
        or any(part in {"", ".", ".."} for part in metadata["route"].split("/"))
    ):
        raise HeaderError("route must be a normalized relative identifier path")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", metadata["updated_at"]):
        raise HeaderError("updated_at must be a second-precision UTC timestamp ending in Z")
    source_time = datetime.strptime(metadata["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if source_time > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HeaderError("updated_at cannot be more than five minutes in the future")
    scoped_types = {"task", "report", "bug", "code", "decision", "research", "transaction"}
    has_scope_fields = any(key in metadata for key in ("task", "goal", "milestone"))
    if document_type in scoped_types or has_scope_fields:
        scoped = {}
        for key in ("task", "goal", "milestone"):
            value = metadata.get(key)
            if not isinstance(value, str) or not value.strip():
                raise HeaderError(f"{document_type} documents require a non-empty {key}")
            scoped[key] = value
        expected_prefix = f"{scoped['goal']}/{scoped['milestone']}/{scoped['task']}"
        expected_route = expected_prefix if document_type == "task" else f"{expected_prefix}/{artifact_id}"
        if metadata["route"] != expected_route:
            raise HeaderError(
                f"{document_type} route must be {expected_route!r}, got {metadata['route']!r}"
            )
        if document_type == "task" and scoped["task"] != artifact_id:
            raise HeaderError("task metadata.task must equal the task artifact id")
    capsule = metadata["capsule"]
    if not isinstance(capsule, str) or not capsule.strip() or len(capsule) > MAX_CAPSULE_CHARS:
        raise HeaderError(f"capsule must contain 1-{MAX_CAPSULE_CHARS} characters")
    boundary = metadata["claim_boundary"]
    if not isinstance(boundary, str) or not boundary.strip() or len(boundary) > MAX_CLAIM_BOUNDARY_CHARS:
        raise HeaderError(f"claim_boundary must contain 1-{MAX_CLAIM_BOUNDARY_CHARS} characters")
    # A graph-bearing header lists its direct graph neighbours in entities, so the routing
    # budget of 32 is replaced by the graph budget for that class only.
    entity_limit = MAX_GRAPH_ENTITIES if is_graph_bearing(metadata) else 32
    for key in ("entities", "facets", "criteria", "does_not_answer"):
        value = metadata.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise HeaderError(f"{key} must be an array of non-empty strings")
        limits = {"entities": entity_limit, "facets": 32, "criteria": 64, "does_not_answer": 16}
        if len(value) > limits[key] or len(value) != len(set(value)):
            raise HeaderError(f"{key} must contain at most {limits[key]} unique values")
        if any(len(item) > 160 for item in value):
            raise HeaderError(f"{key} contains a value longer than 160 characters")
    answers = metadata.get("answers", [])
    if not isinstance(answers, list) or not answers:
        raise HeaderError("answers must contain at least one answer handle")
    if len(answers) > MAX_ANSWER_HANDLES:
        raise HeaderError(f"answers exceeds maximum of {MAX_ANSWER_HANDLES}")
    for index, answer in enumerate(answers, 1):
        if not isinstance(answer, dict):
            raise HeaderError(f"answers[{index}] must be a table")
        for key in ("intent", "question", "target"):
            if not isinstance(answer.get(key), str) or not answer[key].strip():
                raise HeaderError(f"answers[{index}].{key} must be a non-empty string")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", answer["intent"]):
            raise HeaderError(f"answers[{index}].intent must be a bounded lower-case identifier")
        if len(answer["question"]) > 512:
            raise HeaderError(f"answers[{index}].question exceeds 512 characters")
        if answer.get("language", "en") != "en":
            raise HeaderError(f"answers[{index}].language must be 'en' in an English-only KAIROS workspace")
        if not re.fullmatch(r"s-[a-z0-9][a-z0-9-]{1,62}", answer["target"]):
            raise HeaderError(f"answers[{index}].target must be a stable s-* section id")
    refs = metadata.get("refs", {})
    if not isinstance(refs, dict):
        raise HeaderError("refs must be a TOML table")
    if len(refs) > MAX_PRIMARY_REFS:
        raise HeaderError(f"refs exceeds maximum of {MAX_PRIMARY_REFS}")
    if any(not isinstance(value, str) or not value.strip() for value in refs.values()):
        raise HeaderError("every refs value must be a non-empty string")
    contracts = metadata.get("search_contract", [])
    if not isinstance(contracts, list):
        raise HeaderError("search_contract must be an array of tables")
    for index, contract in enumerate(contracts, 1):
        if not isinstance(contract, dict):
            raise HeaderError(f"search_contract[{index}] must be a table")
        for key in ("query", "expected"):
            if not isinstance(contract.get(key), str) or not contract[key].strip():
                raise HeaderError(f"search_contract[{index}].{key} must be a non-empty string")
        if len(contract["query"]) > 512 or len(contract["expected"]) > 256:
            raise HeaderError(f"search_contract[{index}] exceeds its bounded field length")
        if not contract["expected"].startswith(f"{artifact_id}#s-"):
            raise HeaderError(
                f"search_contract[{index}].expected must target a section in {artifact_id}"
            )
        top_k = contract.get("required_top_k", 10)
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 50:
            raise HeaderError(f"search_contract[{index}].required_top_k must be between 1 and 50")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def render_frontmatter(metadata: dict[str, Any]) -> str:
    validate_metadata(metadata)
    # KAIROS renders only the documents it generates, and it never generates a graph
    # layer. Rendering one would silently drop it, so refuse instead of losing evidence.
    if is_graph_bearing(metadata):
        present = sorted(key for key in GRAPH_HEADER_KEYS if key in metadata)
        raise HeaderError(
            "cannot render a graph-bearing header; the normalized graph layer would be "
            f"dropped: {', '.join(present)}"
        )
    lines = ["+++"]
    emitted: set[str] = set()
    for key in PREFERRED_KEY_ORDER:
        if key in metadata:
            lines.append(f"{key} = {_toml_value(metadata[key])}")
            emitted.add(key)
    for key in sorted(metadata):
        if key in emitted or key in {"answers", "refs", "search_contract"}:
            continue
        value = metadata[key]
        if isinstance(value, (str, int, float, bool, list)):
            lines.append(f"{key} = {_toml_value(value)}")
            emitted.add(key)
    for answer in metadata.get("answers", []):
        lines.extend(["", "[[answers]]"])
        for key in ("intent", "question", "target", "language", "weight"):
            if key in answer:
                lines.append(f"{key} = {_toml_value(answer[key])}")
    if metadata.get("refs"):
        lines.extend(["", "[refs]"])
        for key, value in metadata["refs"].items():
            lines.append(f"{key} = {_toml_value(value)}")
    for contract in metadata.get("search_contract", []):
        lines.extend(["", "[[search_contract]]"])
        for key in ("query", "expected", "required_top_k"):
            if key in contract:
                lines.append(f"{key} = {_toml_value(contract[key])}")
    lines.extend(["+++", ""])
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > MAX_HEADER_BYTES:
        raise HeaderError("rendered frontmatter exceeds the first-window header budget")
    return rendered
