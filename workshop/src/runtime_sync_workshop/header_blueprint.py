from __future__ import annotations

import json
from pathlib import Path

from .util import WorkshopError, file_facts, logical_lines, source_prefix


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _ledger_rows(prefix: str, lines: list[str]) -> list[str]:
    width = max(4, len(str(len(lines))))
    return [f"{prefix}:{index:0{width}d} {line}" for index, line in enumerate(lines, 1)]


def _section(lines: list[str], anchor: str, title: str, capsule: str, body: list[str]) -> None:
    lines.extend((
        f'<a id="{anchor}"></a>',
        f"## {title}",
        "",
        f"> Capsule: {capsule}",
        "",
        *body,
        "",
    ))


def generate_header_only_blueprint(
    *,
    artifact_id: str,
    header_relative: str,
    header: Path,
    direct_runtime_includes: list[str],
    compiled_include_roots: list[str],
    updated_at: str,
) -> str:
    """Render a conservative source-less blueprint from exact live header bytes."""

    header_relative = header_relative.replace("\\", "/")
    if not header_relative.startswith(source_prefix()):
        raise WorkshopError(
            "HEADER_OUTSIDE_RUNTIME",
            f"header-only mapping is outside the governed source root: {header_relative}",
        )
    if not header.is_file():
        raise WorkshopError("HEADER_MISSING", f"header-only input is missing: {header}")
    raw = header.read_bytes()
    header_lines, header_eof = logical_lines(raw)
    facts = file_facts(header)
    dependencies = sorted(set(value.replace("\\", "/") for value in direct_runtime_includes))
    include_roots = sorted(set(value.replace("\\", "/") for value in compiled_include_roots))
    entities = [artifact_id, header_relative, *dependencies]
    basename = Path(header_relative).name
    capsule = (
        f"Preserves the exact compiled header-only Runtime file {header_relative}, including "
        "every declaration, constant, type and inline definition present in the live bytes; "
        "no paired C/C++ translation unit exists in the configured build and source-index authority."
    )
    claim_boundary = (
        "This mechanical mirror proves exact header text, mapping, hashes, line count, EOF state "
        "and direct include evidence only; it adds no inferred behavior, semantic owner, runtime "
        "result, build identity or scientific claim."
    )
    lines = [
        "+++",
        'schema = "kairos-context/v1"',
        f"id = {_toml_string(artifact_id)}",
        'type = "code"',
        "revision = 1",
        'state = "ready"',
        'authority = "implementation_documentation"',
        'workspace = "PROJECT_WORKSPACE"',
        f'route = "GOAL_PROJECT_CODEBASE_TRANSPARENCY/MILESTONE_PROJECT_CODEBASE_TRANSPARENCY/TASK_PROJECT_CODEBASE_BLUEPRINTS/{artifact_id}"',
        "loop = 1",
        'task = "TASK_PROJECT_CODEBASE_BLUEPRINTS"',
        'goal = "GOAL_PROJECT_CODEBASE_TRANSPARENCY"',
        'milestone = "MILESTONE_PROJECT_CODEBASE_TRANSPARENCY"',
        f"updated_at = {_toml_string(updated_at)}",
        f"capsule = {_toml_string(capsule)}",
        f"claim_boundary = {_toml_string(claim_boundary)}",
        f"entities = {_toml_array(entities)}",
        'facets = ["runtime-header", "header-only", "compiled-include", "exact-source-ledger"]',
        "criteria = []",
        (
            'does_not_answer = ["runtime execution result", "source-to-binary identity", '
            '"semantic behavior not explicit in the header", "historical intent alignment"]'
        ),
        "contracts = []",
        "artifacts = []",
        "drift_records = []",
        "",
        "[[answers]]",
        'intent = "implementation_location"',
        f"question = {_toml_string(f'Where is the exact header-only implementation text for {basename}?')}",
        'target = "s-source-ledger"',
        'language = "en"',
        "",
        "[[answers]]",
        'intent = "boundary"',
        f"question = {_toml_string(f'Which live Runtime header and compiled include edges bind {basename}?')}",
        'target = "s-boundary"',
        'language = "en"',
        "",
        "[refs]",
        (
            'method = "[ref:docs/BLUEPRINT_METHOD.md#s-context-header|'
            'id:PROJECT_BLUEPRINT_METHOD|v:1|rel:constrained_by|'
            'tags:method,kairos,graph|src:method]"'
        ),
        (
            'dataflow_index = "[ref:PROJECT_SOURCE_INDEX.md|id:PROJECT_SOURCE_INDEX|'
            'v:1|rel:indexes|tags:governance,build|src:verified]"'
        ),
        "",
        "[[search_contract]]",
        f"query = {_toml_string(f'Where is the exact header-only implementation text for {basename}?')}",
        f"expected = {_toml_string(f'{artifact_id}#s-source-ledger')}",
        "required_top_k = 5",
        "",
        "[[search_contract]]",
        f"query = {_toml_string(f'Which live Runtime header and compiled include edges bind {basename}?')}",
        f"expected = {_toml_string(f'{artifact_id}#s-boundary')}",
        "required_top_k = 5",
        "",
        "[[relations]]",
        f"subject = {_toml_string(artifact_id)}",
        'predicate = "defines"',
        f"object = {_toml_string(header_relative)}",
        'object_kind = "header"',
        'scope = "header_only_compiled_include"',
        'evidence_target = "s-mapping"',
        'evidence = "direct"',
    ]
    for dependency in dependencies:
        lines.extend((
            "",
            "[[relations]]",
            f"subject = {_toml_string(header_relative)}",
            'predicate = "depends_on"',
            f"object = {_toml_string(dependency)}",
            'object_kind = "header"',
            'scope = "compile_include"',
            'evidence_target = "s-graph-metadata"',
            'evidence = "direct"',
        ))
    lines.extend((
        "+++",
        f"# {artifact_id}: {header_relative}",
        "",
        "## CONTEXT INDEX",
        "",
    ))
    index_rows = (
        ("s-purpose", "Exact scope of the header-only mirror."),
        ("s-mapping", "Live header identity and explicit absence of a source translation unit."),
        ("s-drift", "Historical material is excluded from current-code authority."),
        ("s-ownership", "Byte authority without an inferred external semantic owner."),
        ("s-graph-metadata", "Direct quoted runtime includes only."),
        ("s-symbols", "Complete symbol evidence remains unabridged in the ledger."),
        ("s-inputs", "Header-declared inputs, if any, remain in the ledger."),
        ("s-preconditions", "Header-declared checks, if any, remain in the ledger."),
        ("s-structures", "Every declared type remains in the ledger."),
        ("s-constants", "Every declared constant remains in the ledger."),
        ("s-primitives", "Every inline or template primitive remains in the ledger."),
        ("s-execution", "Every header-defined execution body remains in the ledger."),
        ("s-output", "Header-declared outputs, if any, remain in the ledger."),
        ("s-failures", "Header-declared failure surfaces remain in the ledger."),
        ("s-determinism", "No behavior is inferred beyond exact header text."),
        ("s-boundary", "Compiled CMake roots reaching the header and no paired translation unit."),
        ("s-tests", "Mechanical equality checks only."),
        ("s-acceptance", "Exact-line, hash and zero-source requirements."),
        ("s-source-ledger", "Exact numbered header lines and an empty source ledger."),
    )
    lines.extend(f"- [{anchor}](#{anchor}) — {description}" for anchor, description in index_rows)
    lines.append("")

    _section(
        lines,
        "s-purpose",
        "PURPOSE",
        "Preserve this compiled header exactly without inventing a missing translation unit.",
        [
            "This document is a mechanical header-only code mirror.",
            "The complete H ledger is the evidence; no reduced prose summary replaces it.",
        ],
    )
    _section(
        lines,
        "s-mapping",
        "SOURCE MAPPING",
        "Bind exact live bytes and record that no paired C/C++ source exists in the authoritative build set.",
        [
            'target_source_file = "none"',
            f"target_header_file = {_toml_string(header_relative)}",
            "source_line_count = 0",
            "source_byte_count = 0",
            'source_sha256 = "none"',
            'source_ledger_sha256 = "none"',
            "source_eof_newline = false",
            'source_newline_style = "none"',
            f"header_line_count = {len(header_lines)}",
            f"header_byte_count = {len(raw)}",
            f'header_sha256 = "{facts["sha256"].upper()}"',
            f'header_ledger_sha256 = "{facts["logical_sha256"].upper()}"',
            f"header_eof_newline = {str(header_eof).lower()}",
            f'header_newline_style = "{facts["newline_style"]}"',
            f"generated_at = {_toml_string(updated_at)}",
        ],
    )
    _section(
        lines,
        "s-drift",
        "HISTORICAL DRIFT",
        "No historical document is used as current-code authority in this mechanical repair.",
        ["none — this document makes no historical alignment claim."],
    )
    _section(
        lines,
        "s-ownership",
        "OWNERSHIP",
        "The live header owns its bytes; no external semantic owner is inferred.",
        [
            f"The byte authority is {header_relative}.",
            "Consumer modules establish compile dependencies; they are not relabeled as owners here.",
        ],
    )
    _section(
        lines,
        "s-graph-metadata",
        "GRAPH METADATA",
        "Only directly parsed quoted runtime include edges are projected.",
        [*(f"- {value}" for value in dependencies)] if dependencies else ["none"],
    )
    generic_sections = (
        ("s-symbols", "PUBLIC SYMBOLS", "Every declaration and definition remains in the exact header ledger."),
        ("s-inputs", "INPUTS", "No input semantics are inferred beyond the exact header."),
        ("s-preconditions", "PRECONDITIONS", "No precondition semantics are inferred beyond the exact header."),
        ("s-structures", "STRUCTURES", "Every type and field remains in the exact header ledger."),
        ("s-constants", "CONSTANTS", "Every constant and literal remains in the exact header ledger."),
        ("s-primitives", "PRIMITIVES", "Every inline, template or header primitive remains verbatim."),
        ("s-execution", "EXECUTION", "Header-defined bodies are exact; no paired source ledger exists."),
        ("s-output", "OUTPUT", "No output semantics are inferred beyond the exact header."),
        ("s-failures", "FAILURES", "No failure semantics are inferred beyond exact header code."),
    )
    for anchor, title, capsule_text in generic_sections:
        _section(lines, anchor, title, capsule_text, ["See s-source-ledger."])
    _section(
        lines,
        "s-determinism",
        "DETERMINISM AND HASH RULES",
        "The mirror is deterministic over live bytes; runtime behavior is not inferred.",
        [
            "The mapping records raw SHA-256, logical-line SHA-256, bytes, lines,",
            "newline style and EOF-newline state from the exact live file.",
        ],
    )
    _section(
        lines,
        "s-boundary",
        "BOUNDARY",
        "The header has no paired authoritative translation unit and enters the build through measured include paths.",
        ["Compiled CMake translation-unit roots with a recursive include path:", "", *(f"- {value}" for value in include_roots)],
    )
    _section(
        lines,
        "s-tests",
        "TEST HOOKS",
        "Verification is mechanical and byte-grounded.",
        [
            "- compare every H row with the live header in order;",
            "- require SOURCE 0 LINES and no C rows;",
            "- recompute raw and logical hashes, bytes, lines, newline style and EOF state;",
            "- require the header in the recursive include closure of every configured authoritative translation unit.",
        ],
    )
    _section(
        lines,
        "s-acceptance",
        "ACCEPTANCE",
        "PASS requires an exact header ledger, an empty source ledger and a verified compiled-include path.",
        ["No semantic approximation can substitute for these mechanical checks."],
    )
    lines.extend((
        '<a id="s-source-ledger"></a>',
        "## SOURCE LEDGER",
        "",
        "> Capsule: The ledger is verbatim numbered evidence for the current header; source absence is represented by zero lines.",
        "",
        f"### HEADER {len(header_lines)} LINES",
        "~~~text",
        *_ledger_rows("H", header_lines),
        "~~~",
        "",
        "### SOURCE 0 LINES",
        "~~~text",
        "~~~",
        "",
        "END OF BLUEPRINT",
        "",
    ))
    return "\n".join(lines)
