from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import WorkshopError, file_facts, logical_lines, normalized_text, sha256_text, source_prefix


ANCHOR_RE = re.compile(r'(?m)^<a id="(?P<id>[^"]+)"></a>[ \t]*$')
ASSIGNMENT_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)[ \t]*$")
LEDGER_LINE_RE = re.compile(r"^(?P<prefix>[HC]):(?P<number>\d+) ?(?P<body>.*)$")
HEADER_HEADING_RE = re.compile(
    r"(?im)^###\s+(?:HEADER\s+(?P<count_a>\d+)\s+LINES|Header\s+[^\n]*?\((?P<count_b>\d+)\s+lines\))[ \t]*$"
)
SOURCE_HEADING_RE = re.compile(
    r"(?im)^###\s+(?:SOURCE\s+(?P<count_a>\d+)\s+LINES|Translation\s+unit\s+[^\n]*?\((?P<count_b>\d+)\s+lines\))[ \t]*$"
)
SOURCE_FIELDS = (
    "target_source_file", "source_file", "source_path", "implementation_source",
    "translation_unit", "source",
)
HEADER_FIELDS = (
    "target_header_file", "source_header_file", "header_file", "header_path",
    "interface", "header",
)


@dataclass(frozen=True)
class SectionSpan:
    section_id: str
    start: int
    end: int


@dataclass(frozen=True)
class BlueprintDocument:
    path: Path
    text: str
    newline: str
    metadata: dict[str, Any]
    frontmatter_start: int
    frontmatter_end: int
    body_start: int
    sections: dict[str, SectionSpan]
    ledger_span: SectionSpan
    mapping: dict[str, str]
    source_path: str | None
    header_path: str | None

    @property
    def artifact_id(self) -> str:
        return str(self.metadata.get("id", ""))

    @property
    def revision(self) -> int:
        value = self.metadata.get("revision")
        if not isinstance(value, int) or value < 1:
            raise WorkshopError("BLUEPRINT_REVISION_INVALID", f"invalid revision in {self.path}: {value!r}")
        return value


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], int, int, int]:
    if not text.startswith("+++\n"):
        raise WorkshopError("BLUEPRINT_FRONTMATTER_MISSING", f"frontmatter must begin on line 1: {path}")
    closing = text.find("\n+++\n", 4)
    if closing < 0:
        raise WorkshopError("BLUEPRINT_FRONTMATTER_UNCLOSED", f"frontmatter delimiter is missing: {path}")
    raw = text[4:closing]
    try:
        metadata = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise WorkshopError("BLUEPRINT_FRONTMATTER_INVALID", f"invalid TOML in {path}: {exc}") from exc
    return metadata, 4, closing, closing + 5


def _section_spans(text: str, body_start: int) -> dict[str, SectionSpan]:
    matches = list(ANCHOR_RE.finditer(text, body_start))
    sections: dict[str, SectionSpan] = {}
    for index, match in enumerate(matches):
        section_id = match.group("id")
        if section_id in sections:
            raise WorkshopError("BLUEPRINT_DUPLICATE_ANCHOR", f"duplicate anchor {section_id}")
        sections[section_id] = SectionSpan(
            section_id=section_id,
            start=match.start(),
            end=matches[index + 1].start() if index + 1 < len(matches) else len(text),
        )
    return sections


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _mapping_values(section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in section.splitlines():
        match = ASSIGNMENT_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key")
        if key in values:
            raise WorkshopError("BLUEPRINT_MAPPING_DUPLICATE", f"duplicate mapping key: {key}")
        values[key] = _unquote(match.group("value"))
    return values


def _runtime_relative(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    prefix = source_prefix()
    if not prefix:
        return normalized
    match = re.search(rf"(?:^|/){re.escape(prefix)}", normalized, flags=re.I)
    if match:
        return normalized[match.end() - len(prefix):]
    return normalized


def _is_none_mapping(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized == "none" or normalized.startswith("none ") or normalized.startswith("none(")


def _first_mapping(
    mapping: dict[str, str],
    names: tuple[str, ...],
    *,
    path: Path,
    allow_none: bool = False,
) -> str | None:
    values = [(name, mapping[name]) for name in names if mapping.get(name)]
    if not values:
        raise WorkshopError("BLUEPRINT_MAPPING_MISSING", f"no {names[0]} mapping in {path}")
    normalized = [
        None if allow_none and _is_none_mapping(value) else _runtime_relative(value)
        for _, value in values
    ]
    distinct = set(normalized)
    if len(distinct) != 1:
        raise WorkshopError("BLUEPRINT_MAPPING_CONFLICT", f"conflicting mapping fields in {path}: {values}")
    return normalized[0]


def _ledger_span(text: str, sections: dict[str, SectionSpan], body_start: int, path: Path) -> SectionSpan:
    if "s-source-ledger" in sections:
        return sections["s-source-ledger"]
    heading = HEADER_HEADING_RE.search(text, body_start)
    if not heading:
        raise WorkshopError("BLUEPRINT_LEDGER_BLOCK_MISSING", f"HEADER ledger heading is missing in {path}")
    source_heading = SOURCE_HEADING_RE.search(text, heading.end())
    if not source_heading:
        raise WorkshopError("BLUEPRINT_SOURCE_LEDGER_MISSING", f"SOURCE ledger heading is missing in {path}")
    opener = re.compile(r"(?m)^(?P<fence>~~~|```)[^\n]*$").search(text, source_heading.end())
    if not opener:
        raise WorkshopError("BLUEPRINT_SOURCE_LEDGER_FENCE_MISSING", f"SOURCE ledger fence is missing in {path}")
    fence = opener.group("fence")
    closer = re.compile(rf"(?m)^{re.escape(fence)}[ \t]*$").search(text, opener.end())
    if not closer:
        raise WorkshopError("BLUEPRINT_SOURCE_LEDGER_FENCE_UNCLOSED", f"SOURCE ledger fence is unclosed in {path}")
    next_anchor = ANCHOR_RE.search(text, closer.end())
    ending = re.compile(r"(?m)^END OF BLUEPRINT[ \t]*$").search(text, closer.end())
    if ending and (next_anchor is None or ending.start() < next_anchor.start()):
        end = ending.end()
    else:
        end = closer.end()
    return SectionSpan("s-source-ledger-legacy", heading.start(), end)


def _prose_mapping(section: str, label: str) -> str:
    match = re.search(
        rf"(?im)^\s*(?:[-*]\s*)?(?:Current\s+)?{re.escape(label)}\s*:\s*`?(?P<path>[^\s`—]+)",
        section,
    )
    return _runtime_relative(match.group("path").rstrip(".,;")) if match else ""


def inspect_blueprint_mapping(
    path: Path,
) -> tuple[dict[str, Any], str | None, str | None, str]:
    """Read frontmatter and source/header mapping without requiring a ledger."""

    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkshopError("BLUEPRINT_NON_UTF8", f"blueprint is not strict UTF-8: {path}: {exc}") from exc
    newline = "\r\n" if "\r\n" in decoded and decoded.count("\r\n") >= decoded.count("\n") / 2 else "\n"
    text = normalized_text(decoded)
    metadata, _, _, body_start = _split_frontmatter(text, path)
    sections = _section_spans(text, body_start)
    if "s-mapping" not in sections:
        raise WorkshopError("BLUEPRINT_SECTION_MISSING", f"s-mapping is missing in {path}")
    mapping_span = sections["s-mapping"]
    mapping_section = text[mapping_span.start:mapping_span.end]
    mapping = _mapping_values(mapping_section)
    if not any(mapping.get(field) for field in SOURCE_FIELDS):
        prose_source = _prose_mapping(mapping_section, "Source")
        if prose_source:
            mapping["source"] = prose_source
    if not any(mapping.get(field) for field in HEADER_FIELDS):
        prose_header = _prose_mapping(mapping_section, "Header")
        if prose_header:
            mapping["header"] = prose_header
    source = _first_mapping(mapping, SOURCE_FIELDS, path=path, allow_none=True)
    raw_header = next((mapping[name] for name in HEADER_FIELDS if mapping.get(name)), "")
    header = None if not raw_header or _is_none_mapping(raw_header) else _runtime_relative(raw_header)
    if source is None and header is None:
        raise WorkshopError("BLUEPRINT_MAPPING_EMPTY", f"source and header mappings are both none in {path}")
    return metadata, source, header, newline


def parse_blueprint(path: Path) -> BlueprintDocument:
    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkshopError("BLUEPRINT_NON_UTF8", f"blueprint is not strict UTF-8: {path}: {exc}") from exc
    newline = "\r\n" if "\r\n" in decoded and decoded.count("\r\n") >= decoded.count("\n") / 2 else "\n"
    text = normalized_text(decoded)
    metadata, frontmatter_start, frontmatter_end, body_start = _split_frontmatter(text, path)
    sections = _section_spans(text, body_start)
    if "s-mapping" not in sections:
        raise WorkshopError("BLUEPRINT_SECTION_MISSING", f"s-mapping is missing in {path}")
    ledger_span = _ledger_span(text, sections, body_start, path)
    mapping_span = sections["s-mapping"]
    mapping_section = text[mapping_span.start:mapping_span.end]
    mapping = _mapping_values(mapping_section)
    if not any(mapping.get(field) for field in SOURCE_FIELDS):
        prose_source = _prose_mapping(mapping_section, "Source")
        if prose_source:
            mapping["source"] = prose_source
    if not any(mapping.get(field) for field in HEADER_FIELDS):
        prose_header = _prose_mapping(mapping_section, "Header")
        if prose_header:
            mapping["header"] = prose_header
    source = _first_mapping(mapping, SOURCE_FIELDS, path=path, allow_none=True)
    raw_header = ""
    for field in HEADER_FIELDS:
        if mapping.get(field):
            raw_header = mapping[field]
            break
    header = None if not raw_header or _is_none_mapping(raw_header) else _runtime_relative(raw_header)
    if source is None and header is None:
        raise WorkshopError("BLUEPRINT_MAPPING_EMPTY", f"source and header mappings are both none in {path}")
    return BlueprintDocument(
        path=path,
        text=text,
        newline=newline,
        metadata=metadata,
        frontmatter_start=frontmatter_start,
        frontmatter_end=frontmatter_end,
        body_start=body_start,
        sections=sections,
        ledger_span=ledger_span,
        mapping=mapping,
        source_path=source,
        header_path=header,
    )


def _extract_ledger_block(section: str, kind: str, declared_hint: int | None = None) -> list[str]:
    prefix = "H" if kind == "HEADER" else "C"
    rows = section.splitlines()
    matching = [index for index, row in enumerate(rows) if re.match(rf"^{prefix}:\d+", row)]
    if not matching:
        heading = (HEADER_HEADING_RE if kind == "HEADER" else SOURCE_HEADING_RE).search(section)
        heading_count = int(heading.group("count_a") or heading.group("count_b")) if heading else None
        if declared_hint == 0 or heading_count == 0:
            return []
        raise WorkshopError("BLUEPRINT_LEDGER_BLOCK_MISSING", f"{kind} ledger block is missing")
    rows = rows[matching[0]:matching[-1] + 1]
    declared = declared_hint if declared_hint is not None else int(LEDGER_LINE_RE.match(rows[-1]).group("number"))
    values: list[str] = []
    for expected, row in enumerate(rows, 1):
        parsed = LEDGER_LINE_RE.match(row)
        if not parsed or parsed.group("prefix") != prefix:
            raise WorkshopError("BLUEPRINT_LEDGER_LINE_INVALID", f"invalid {kind} ledger row {expected}: {row[:160]}")
        if int(parsed.group("number")) != expected:
            raise WorkshopError(
                "BLUEPRINT_LEDGER_NUMBER_INVALID",
                f"{kind} ledger row is {parsed.group('number')}, expected {expected}",
            )
        values.append(parsed.group("body"))
    if len(values) != declared:
        raise WorkshopError(
            "BLUEPRINT_LEDGER_COUNT_INVALID",
            f"{kind} ledger declares {declared} lines but contains {len(values)}",
        )
    return values


def ledger_lines(document: BlueprintDocument) -> tuple[list[str], list[str]]:
    span = document.ledger_span
    section = document.text[span.start:span.end]
    def hint(key: str) -> int | None:
        value = document.mapping.get(key, "").strip().strip('"')
        return int(value) if value.isdigit() else None
    return (
        _extract_ledger_block(section, "HEADER", hint("header_line_count")),
        _extract_ledger_block(section, "SOURCE", hint("source_line_count")),
    )


def verify_ledger(
    document: BlueprintDocument,
    source: Path | None,
    header: Path | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        header_ledger, source_ledger = ledger_lines(document)
    except WorkshopError as exc:
        return [exc.as_dict()]
    if source is None:
        if source_ledger:
            issues.append({
                "code": "UNEXPECTED_SOURCE_LEDGER",
                "message": "header-only mapping contains source ledger rows",
                "details": {"blueprint": document.path.name},
            })
    else:
        source_lines, _ = logical_lines(source.read_bytes())
        if source_ledger != source_lines:
            first = next(
                (index for index, pair in enumerate(zip(source_ledger, source_lines), 1) if pair[0] != pair[1]),
                min(len(source_ledger), len(source_lines)) + 1,
            )
            issues.append({
                "code": "SOURCE_LEDGER_MISMATCH",
                "message": f"source ledger differs at line {first}",
                "details": {"blueprint": document.path.name, "source": document.source_path, "ledger_lines": len(source_ledger), "source_lines": len(source_lines)},
            })
    if header is None:
        if header_ledger:
            issues.append({"code": "UNEXPECTED_HEADER_LEDGER", "message": "headerless mapping contains header ledger rows", "details": {"blueprint": document.path.name}})
    else:
        header_lines, _ = logical_lines(header.read_bytes())
        if header_ledger != header_lines:
            first = next(
                (index for index, pair in enumerate(zip(header_ledger, header_lines), 1) if pair[0] != pair[1]),
                min(len(header_ledger), len(header_lines)) + 1,
            )
            issues.append({
                "code": "HEADER_LEDGER_MISMATCH",
                "message": f"header ledger differs at line {first}",
                "details": {"blueprint": document.path.name, "header": document.header_path, "ledger_lines": len(header_ledger), "header_lines": len(header_lines)},
            })
    return issues


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _replace_frontmatter_field(text: str, document: BlueprintDocument, key: str, value: Any) -> str:
    prefix = text[:document.frontmatter_start]
    frontmatter = text[document.frontmatter_start:document.frontmatter_end]
    suffix = text[document.frontmatter_end:]
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
    replacement = f"{key} = {_format_value(value)}"
    if not pattern.search(frontmatter):
        raise WorkshopError("BLUEPRINT_FRONTMATTER_FIELD_MISSING", f"frontmatter field is missing: {key}")
    frontmatter = pattern.sub(replacement, frontmatter, count=1)
    return prefix + frontmatter + suffix


def _update_mapping_section(section: str, values: dict[str, Any]) -> str:
    lines = section.split("\n")
    found: set[str] = set()
    last_assignment = -1
    for index, line in enumerate(lines):
        match = ASSIGNMENT_RE.match(line.strip())
        if not match:
            continue
        last_assignment = index
        key = match.group("key")
        if key in values:
            lines[index] = f"{key} = {_format_value(values[key])}"
            found.add(key)
    missing = [key for key in values if key not in found]
    if missing:
        additions = [f"{key} = {_format_value(values[key])}" for key in missing]
        if last_assignment < 0:
            # Prose-only mappings have no assignment insertion point. Append
            # facts inside this section, never before its anchor in prior prose.
            return section.rstrip("\n") + "\n\n" + "\n".join(additions) + "\n\n"
        insertion = last_assignment + 1
        lines[insertion:insertion] = additions
    return "\n".join(lines)


def _ledger_rows(prefix: str, lines: list[str]) -> str:
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{prefix}:{index:0{width}d} {line}" for index, line in enumerate(lines, 1))


def _fenced_ledger_span(ledger: str, heading_re: re.Pattern[str], kind: str) -> tuple[int, int]:
    """Locate one complete mechanical block without consuming section metadata."""
    heading = heading_re.search(ledger)
    if not heading:
        raise WorkshopError("BLUEPRINT_LEDGER_BLOCK_MISSING", f"{kind} ledger heading is missing")
    opener = re.match(r"\n(?:[ \t]*\n)*[ \t]*(?P<fence>~~~|```)[^\n]*\n", ledger[heading.end():])
    if not opener:
        raise WorkshopError("BLUEPRINT_LEDGER_FENCE_MISSING", f"{kind} ledger opening fence is missing")
    content_start = heading.end() + opener.end()
    closer = re.compile(rf"(?m)^{re.escape(opener.group('fence'))}[ \t]*$").search(ledger, content_start)
    if not closer:
        raise WorkshopError("BLUEPRINT_LEDGER_FENCE_UNCLOSED", f"{kind} ledger closing fence is missing")
    return heading.start(), closer.end()


def regenerate_mechanical_layers(
    document: BlueprintDocument,
    source: Path | None,
    header: Path | None,
    *,
    revision: int,
    updated_at: str,
) -> str:
    if source is not None:
        source_raw = source.read_bytes()
        source_lines, source_eof = logical_lines(source_raw)
        source_facts = file_facts(source)
    else:
        source_raw = b""
        source_lines = []
        source_eof = False
        source_facts = None
    if header is not None:
        header_raw = header.read_bytes()
        header_lines, header_eof = logical_lines(header_raw)
        header_facts = file_facts(header)
    else:
        header_lines = []
        header_eof = False
        header_facts = None

    mapping_values: dict[str, Any] = {
        "source_line_count": len(source_lines),
        "source_byte_count": len(source_raw),
        "source_sha256": source_facts["sha256"].upper() if source_facts else "none",
        "source_ledger_sha256": source_facts["logical_sha256"].upper() if source_facts else "none",
        "source_eof_newline": source_eof,
        "source_newline_style": source_facts["newline_style"] if source_facts else "none",
        "header_line_count": len(header_lines),
        "header_byte_count": len(header_raw) if header is not None else 0,
        "header_sha256": header_facts["sha256"].upper() if header_facts else "none",
        "header_ledger_sha256": header_facts["logical_sha256"].upper() if header_facts else "none",
        "header_eof_newline": header_eof,
        "header_newline_style": header_facts["newline_style"] if header_facts else "none",
        "generated_at": updated_at,
    }
    # Preserve the declared mapping shape. A sealed implicit sibling-header
    # owner is resolved by corpus inventory, not made explicit by regeneration;
    # adding a path here would violate the next prepare's mapping-change guard.

    text = document.text
    mapping_span = document.sections["s-mapping"]
    mapped = _update_mapping_section(text[mapping_span.start:mapping_span.end], mapping_values)
    text = text[:mapping_span.start] + mapped + text[mapping_span.end:]
    reparsed = parse_blueprint_text(document.path, text, document.newline)
    ledger_span = reparsed.ledger_span
    old_ledger = text[ledger_span.start:ledger_span.end]
    heading = HEADER_HEADING_RE.search(old_ledger)
    if heading:
        payload_start = heading.start()
    else:
        payload = re.search(r"(?im)^(?:###\s+(?:Included\s+header|Header)|(?:~~~|```)(?:text|cpp)?|HEADER\s*:|H:\d+)", old_ledger)
        if not payload:
            raise WorkshopError("BLUEPRINT_LEDGER_BLOCK_MISSING", f"HEADER ledger payload missing in {document.path}")
        payload_start = payload.start()
    prefix = old_ledger[:payload_start].rstrip("\n")
    header_rows = _ledger_rows("H", header_lines)
    source_rows = _ledger_rows("C", source_lines)
    terminal = bool(re.search(r"(?m)^END OF BLUEPRINT[ \t]*$", old_ledger))
    replacement = (
        f"{prefix}\n\n### HEADER {len(header_lines)} LINES\n~~~text\n"
        f"{header_rows}\n~~~\n\n### SOURCE {len(source_lines)} LINES\n~~~text\n"
        f"{source_rows}\n~~~\n"
    )
    if terminal:
        replacement += "\nEND OF BLUEPRINT\n"
    if ledger_span.section_id == "s-source-ledger-legacy":
        # A legacy span starts at the header heading, not at a section boundary.
        # Replace only fenced blocks: preserve intermediate indexed sections and
        # all surrounding whitespace instead of merging or growing the span.
        header_start, header_end = _fenced_ledger_span(old_ledger, HEADER_HEADING_RE, "HEADER")
        source_start, source_end = _fenced_ledger_span(old_ledger, SOURCE_HEADING_RE, "SOURCE")
        if header_end > source_start or ANCHOR_RE.search(old_ledger[header_start:header_end]) or ANCHOR_RE.search(old_ledger[source_start:source_end]):
            raise WorkshopError("BLUEPRINT_LEDGER_BOUNDARY_INVALID", "split ledger payloads overlap or consume an indexed boundary")
        replacement = (
            old_ledger[:header_start]
            + f"### HEADER {len(header_lines)} LINES\n~~~text\n{header_rows}\n~~~"
            + old_ledger[header_end:source_start]
            + f"### SOURCE {len(source_lines)} LINES\n~~~text\n{source_rows}\n~~~"
            + old_ledger[source_end:]
        )
    text = text[:ledger_span.start] + replacement + text[ledger_span.end:]
    reparsed = parse_blueprint_text(document.path, text, document.newline)
    text = _replace_frontmatter_field(text, reparsed, "revision", revision)
    reparsed = parse_blueprint_text(document.path, text, document.newline)
    text = _replace_frontmatter_field(text, reparsed, "updated_at", updated_at)
    return text.replace("\n", document.newline) if document.newline != "\n" else text


def advance_document_revision(
    document: BlueprintDocument,
    *,
    revision: int,
    updated_at: str,
) -> str:
    """Advance only frontmatter identity while preserving every body byte logically."""

    if revision != document.revision + 1:
        raise WorkshopError(
            "BLUEPRINT_REVISION_INVALID",
            f"document-only revision must be baseline+1 for {document.path.name}",
        )
    text = _replace_frontmatter_field(document.text, document, "revision", revision)
    reparsed = parse_blueprint_text(document.path, text, document.newline)
    text = _replace_frontmatter_field(text, reparsed, "updated_at", updated_at)
    return text.replace("\n", document.newline) if document.newline != "\n" else text


def parse_blueprint_text(path: Path, text: str, newline: str = "\n") -> BlueprintDocument:
    normalized = normalized_text(text)
    metadata, frontmatter_start, frontmatter_end, body_start = _split_frontmatter(normalized, path)
    sections = _section_spans(normalized, body_start)
    if "s-mapping" not in sections:
        raise WorkshopError("BLUEPRINT_SECTION_MISSING", f"s-mapping is missing in {path}")
    ledger_span = _ledger_span(normalized, sections, body_start, path)
    mapping_span = sections["s-mapping"]
    mapping_section = normalized[mapping_span.start:mapping_span.end]
    mapping = _mapping_values(mapping_section)
    if not any(mapping.get(field) for field in SOURCE_FIELDS):
        prose_source = _prose_mapping(mapping_section, "Source")
        if prose_source:
            mapping["source"] = prose_source
    if not any(mapping.get(field) for field in HEADER_FIELDS):
        prose_header = _prose_mapping(mapping_section, "Header")
        if prose_header:
            mapping["header"] = prose_header
    source = _first_mapping(mapping, SOURCE_FIELDS, path=path, allow_none=True)
    raw_header = next((mapping[name] for name in HEADER_FIELDS if mapping.get(name)), "")
    header = None if not raw_header or _is_none_mapping(raw_header) else _runtime_relative(raw_header)
    if source is None and header is None:
        raise WorkshopError("BLUEPRINT_MAPPING_EMPTY", f"source and header mappings are both none in {path}")
    return BlueprintDocument(path, normalized, newline, metadata, frontmatter_start, frontmatter_end, body_start, sections, ledger_span, mapping, source, header)


def complete_missing_mechanical_layers(
    path: Path,
    source: Path | None,
    header: Path | None,
    *,
    revision: int,
    updated_at: str,
) -> str:
    """Complete only a blueprint whose exact H/C ledger section is absent."""

    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkshopError("BLUEPRINT_NON_UTF8", f"blueprint is not strict UTF-8: {path}: {exc}") from exc
    newline = "\r\n" if "\r\n" in decoded and decoded.count("\r\n") >= decoded.count("\n") / 2 else "\n"
    text = normalized_text(decoded)
    metadata, _, _, body_start = _split_frontmatter(text, path)
    sections = _section_spans(text, body_start)
    if "s-mapping" not in sections:
        raise WorkshopError("BLUEPRINT_SECTION_MISSING", f"s-mapping is missing in {path}")
    if "s-source-ledger" in sections or re.search(r"(?m)^[HC]:\d+", text):
        raise WorkshopError(
            "BLUEPRINT_LEDGER_REPAIR_SCOPE_INVALID",
            f"missing-ledger completion refuses an existing ledger payload: {path}",
        )
    end_matches = list(re.finditer(r"(?m)^END OF BLUEPRINT[ \t]*$", text))
    if len(end_matches) != 1:
        raise WorkshopError(
            "BLUEPRINT_LEDGER_END_INVALID",
            f"missing-ledger completion requires exactly one terminal marker: {path}",
        )
    context_match = re.search(
        r"(?ms)^## CONTEXT INDEX[ \t]*\n(?P<body>.*?)(?=^<a id=)",
        text,
    )
    if not context_match:
        raise WorkshopError("BLUEPRINT_CONTEXT_INDEX_MISSING", f"CONTEXT INDEX is missing in {path}")
    context = context_match.group(0)
    if "s-source-ledger" not in context:
        context = (
            context.rstrip("\n")
            + "\n- [\x60s-source-ledger\x60](#s-source-ledger) — exact numbered header and source lines.\n\n"
        )
        text = text[:context_match.start()] + context + text[context_match.end():]
        end_matches = list(re.finditer(r"(?m)^END OF BLUEPRINT[ \t]*$", text))
    end = end_matches[0]
    prefix = text[:end.start()].rstrip()
    ledger = (
        '<a id="s-source-ledger"></a>\n'
        "## SOURCE LEDGER\n\n"
        "> Capsule: The following ledgers are verbatim numbered evidence for the current header and source files.\n\n"
        "### HEADER 0 LINES\n~~~text\n~~~\n\n"
        "### SOURCE 0 LINES\n~~~text\n~~~\n\n"
        "END OF BLUEPRINT\n"
    )
    staged = prefix + "\n\n" + ledger
    document = parse_blueprint_text(path, staged, newline)
    if int(metadata.get("revision", 0)) + 1 != revision:
        raise WorkshopError(
            "BLUEPRINT_REVISION_INVALID",
            f"repair revision must be baseline+1 for {path.name}",
        )
    return regenerate_mechanical_layers(
        document,
        source,
        header,
        revision=revision,
        updated_at=updated_at,
    )


def semantic_document_hash(document: BlueprintDocument) -> str:
    metadata = copy.deepcopy(document.metadata)
    metadata.pop("revision", None)
    metadata.pop("updated_at", None)
    spans = sorted(
        (document.sections["s-mapping"], document.ledger_span),
        key=lambda item: item.start,
    )
    # Some legacy documents embed the ledger inside s-mapping. Exclude the
    # union once: deleting overlapping spans twice uses stale offsets and can
    # erase following semantic text from the hash input.
    exclusions: list[tuple[int, int]] = []
    for span in spans:
        if exclusions and span.start <= exclusions[-1][1]:
            start, end = exclusions[-1]
            exclusions[-1] = (start, max(end, span.end))
        else:
            exclusions.append((span.start, span.end))
    body = document.text[document.body_start:]
    for start, end in reversed(exclusions):
        relative_start = start - document.body_start
        relative_end = end - document.body_start
        body = body[:relative_start] + body[relative_end:]
    return sha256_text(repr(sorted(metadata.items())) + "\n" + body.strip())


def cpp_token_signature(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkshopError("NON_UTF8_SOURCE", f"C/C++ input is not UTF-8: {exc}") from exc
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if char.isspace():
            index += 1
            continue
        if char == "/" and following == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and following == "*":
            closing = text.find("*/", index + 2)
            if closing < 0:
                raise WorkshopError("CPP_COMMENT_UNCLOSED", "unterminated C/C++ block comment")
            index = closing + 2
            continue
        if char in {'"', "'"}:
            quote = char
            start = index
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            tokens.append(text[start:index])
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[EePp][+-]?\d+)?[A-Za-z0-9_]*", text[index:])
        if match:
            tokens.append(match.group(0))
            index += len(match.group(0))
            continue
        three = text[index:index + 3]
        two = text[index:index + 2]
        if three in {"<<=", ">>=", "...", "->*"}:
            tokens.append(three)
            index += 3
        elif two in {"::", "->", "++", "--", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", ".*", "##"}:
            tokens.append(two)
            index += 2
        else:
            tokens.append(char)
            index += 1
    return sha256_text("\n".join(tokens))
