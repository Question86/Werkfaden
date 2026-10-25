from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import KickstartError
from .survey import CompilationUnit, HEADER_SUFFIXES, Survey


def _canonical_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_harness() -> tuple[object, object, object, object]:
    harness = _canonical_root() / "kairos" / "kairos_harness"
    if str(harness) not in sys.path:
        sys.path.insert(0, str(harness))
    workshop_src = _canonical_root() / "workshop" / "src"
    if str(workshop_src) not in sys.path:
        sys.path.insert(0, str(workshop_src))
    from kairos.frontmatter import render_frontmatter
    from kairos.templates import pointer, render_document
    from runtime_sync_workshop.util import file_facts, logical_lines

    return render_frontmatter, pointer, render_document, (file_facts, logical_lines)


@dataclass(frozen=True)
class BlueprintRecord:
    relative_path: str
    filename: str
    artifact_id: str
    kind: str
    source: Path | None
    header: Path | None
    text: str


@dataclass(frozen=True)
class BlueprintSet:
    records: tuple[BlueprintRecord, ...]
    headers: tuple[str, ...]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def artifact_id_for(relative_path: str, *, header_only: bool = False) -> str:
    prefix = "PROJECT_HEADER" if header_only else "PROJECT_CODE"
    return f"{prefix}_{_digest(relative_path)}"


def filename_for(relative_path: str, *, header_only: bool = False) -> str:
    prefix = "PROJECT_HEADER" if header_only else "PROJECT_CODE"
    return f"{prefix}_{_digest(relative_path)}.md"


def _ledger_rows(prefix: str, lines: list[str]) -> str:
    if not lines:
        return ""
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{prefix}:{index:0{width}d} {line}" for index, line in enumerate(lines, 1))


def _metadata(
    *,
    artifact_id: str,
    workspace_id: str,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    updated_at: str,
    relative_path: str,
    kind: str,
    pointer,
) -> dict[str, object]:
    if kind == "source":
        capsule = f"Exact compiler-recorded translation unit mirror for {relative_path}."
        questions = [
            ("implementation_location", f"Which exact source bytes are bound to {relative_path}?", "s-mapping"),
            ("dependency", f"Which project headers does {relative_path} reach?", "s-includes"),
            ("evidence", f"Where is the verbatim source ledger for {relative_path}?", "s-source-ledger"),
        ]
        facets = ["project-initiation", "translation-unit", "compile-commands", "exact-ledger"]
        does_not_answer = ["runtime execution result", "binary provenance", "semantic intent not present in source"]
    else:
        capsule = f"Exact compiled-include header mirror for {relative_path}; no source owner is inferred."
        questions = [
            ("implementation_location", f"Which exact header bytes are bound to {relative_path}?", "s-mapping"),
            ("dependency", f"Which translation units include {relative_path}?", "s-ownership"),
            ("evidence", f"Where is the verbatim header ledger for {relative_path}?", "s-source-ledger"),
        ]
        facets = ["project-initiation", "header-only", "compiled-include", "exact-ledger"]
        does_not_answer = ["runtime execution result", "semantic ownership", "binary provenance"]
    metadata: dict[str, object] = {
        "schema": "kairos-context/v1",
        "id": artifact_id,
        "type": "code",
        "revision": 1,
        "state": "ready",
        "authority": "implementation_documentation",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{artifact_id}",
        "loop": 1,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": updated_at,
        "capsule": capsule,
        "claim_boundary": (
            "This mechanical mirror proves exact bytes, line ledgers, hashes, mapping and "
            "compiler/include evidence only; it does not infer behavior or execution results."
        ),
        "entities": [artifact_id, relative_path, "compile_commands", "KAIROS"],
        "facets": facets,
        "criteria": [],
        "does_not_answer": does_not_answer,
        "answers": [
            {"intent": intent, "question": question, "target": target, "language": "en"}
            for intent, question, target in questions
        ],
        "refs": {
            "method": pointer(
                "docs/PROJECT_KICKSTART.md",
                section="s-ground-truth",
                artifact_id="PROJECT_KICKSTART",
                version="1",
                relation="constrained_by",
                tags=("method", "ground-truth"),
                source="generated",
            ),
            "source_index": pointer(
                "docs/PROJECT_SOURCE_INDEX.md",
                section="s-project-membership",
                artifact_id="PROJECT_SOURCE_INDEX",
                version="1",
                relation="indexes",
                tags=("build", "membership"),
                source="generated",
            ),
            "task": pointer(
                f"tasks/task_{task_id}.md",
                section="s-objective",
                artifact_id=task_id,
                version="dynamic",
                relation="implements",
                tags=("scope", "task"),
                source="system",
            ),
        },
        "search_contract": [
            {"query": question, "expected": f"{artifact_id}#{target}", "required_top_k": 5}
            for _, question, target in questions
        ],
    }
    return metadata


def _mapping(
    *,
    source: Path | None,
    header: Path | None,
    project_root: Path,
    updated_at: str,
    file_facts,
    logical_lines,
) -> list[str]:
    source_relative = source.resolve().relative_to(project_root.resolve()).as_posix() if source else "none"
    header_relative = header.resolve().relative_to(project_root.resolve()).as_posix() if header else "none"
    source_raw = source.read_bytes() if source else b""
    header_raw = header.read_bytes() if header else b""
    source_lines, source_eof = logical_lines(source_raw) if source else ([], False)
    header_lines, header_eof = logical_lines(header_raw) if header else ([], False)
    source_info = file_facts(source) if source else None
    header_info = file_facts(header) if header else None
    values: list[tuple[str, object]] = [
        ("target_source_file", source_relative),
        ("target_header_file", header_relative),
        ("source_line_count", len(source_lines)),
        ("source_byte_count", len(source_raw)),
        ("source_sha256", source_info["sha256"].upper() if source_info else "none"),
        ("source_ledger_sha256", source_info["logical_sha256"].upper() if source_info else "none"),
        ("source_eof_newline", source_eof),
        ("source_newline_style", source_info["newline_style"] if source_info else "none"),
        ("header_line_count", len(header_lines)),
        ("header_byte_count", len(header_raw)),
        ("header_sha256", header_info["sha256"].upper() if header_info else "none"),
        ("header_ledger_sha256", header_info["logical_sha256"].upper() if header_info else "none"),
        ("header_eof_newline", header_eof),
        ("header_newline_style", header_info["newline_style"] if header_info else "none"),
        ("generated_at", updated_at),
    ]
    lines: list[str] = []
    for key, value in values:
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, int):
            encoded = str(value)
        else:
            encoded = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key} = {encoded}")
    return lines


def _document(
    *,
    metadata: dict[str, object],
    title: str,
    sections: list[dict[str, str]],
    render_document,
) -> str:
    return render_document(metadata, title, sections)


def _source_blueprint(
    unit: CompilationUnit,
    *,
    project_root: Path,
    workspace_id: str,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    updated_at: str,
    pointer,
    render_document,
    file_facts,
    logical_lines,
) -> BlueprintRecord:
    relative = unit.relative_path
    artifact_id = artifact_id_for(relative)
    source_lines, _ = logical_lines(unit.absolute_path.read_bytes())
    facts = file_facts(unit.absolute_path)
    include_roots = [root.resolve().relative_to(project_root.resolve()).as_posix() for root in unit.include_roots if _inside(root, project_root)]
    metadata = _metadata(
        artifact_id=artifact_id,
        workspace_id=workspace_id,
        task_id=task_id,
        goal_id=goal_id,
        milestone_id=milestone_id,
        updated_at=updated_at,
        relative_path=relative,
        kind="source",
        pointer=pointer,
    )
    mapping = _mapping(
        source=unit.absolute_path,
        header=None,
        project_root=project_root,
        updated_at=updated_at,
        file_facts=file_facts,
        logical_lines=logical_lines,
    )
    includes_body = [*(f"- `{value}`" for value in include_roots)] or ["- none declared by the compiler command"]
    command_fingerprint = hashlib.sha256("\n".join(unit.commands).encode("utf-8")).hexdigest().upper()
    sections = [
        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact compiler-recorded source mirror.", "content": f"The authoritative translation unit is `{relative}`."},
        {"id": "s-mapping", "title": "SOURCE MAPPING", "capsule": "The mapping binds the live source path and exact byte/line facts.", "content": "\n".join(mapping)},
        {"id": "s-includes", "title": "COMPILE INCLUDE ROOTS", "capsule": "Include roots are extracted from the compiler command and constrained to the project root.", "content": "\n".join(includes_body)},
        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Compiler-recorded membership is the source-set authority; no binary or stem inference is used.", "content": f"- Source authority: `compile_commands.json`\n- Command-set SHA-256: `{command_fingerprint}`\n- Commands recorded for this unit: `{len(unit.commands)}`"},
        {"id": "s-source-ledger", "title": "SOURCE LEDGER", "capsule": "The source ledger is verbatim numbered evidence for the current translation unit.", "content": "### HEADER 0 LINES\n~~~text\n~~~\n\n### SOURCE " + str(len(source_lines)) + " LINES\n~~~text\n" + _ledger_rows("C", source_lines) + "\n~~~\n\nEND OF BLUEPRINT"},
    ]
    return BlueprintRecord(relative, filename_for(relative), artifact_id, "translation_unit", unit.absolute_path, None, _document(metadata=metadata, title=f"{artifact_id}: {relative}", sections=sections, render_document=render_document))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _header_blueprint(
    relative: str,
    header: Path,
    *,
    project_root: Path,
    workspace_id: str,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    updated_at: str,
    owners: list[str],
    pointer,
    render_document,
    file_facts,
    logical_lines,
) -> BlueprintRecord:
    artifact_id = artifact_id_for(relative, header_only=True)
    header_lines, _ = logical_lines(header.read_bytes())
    facts = file_facts(header)
    metadata = _metadata(
        artifact_id=artifact_id,
        workspace_id=workspace_id,
        task_id=task_id,
        goal_id=goal_id,
        milestone_id=milestone_id,
        updated_at=updated_at,
        relative_path=relative,
        kind="header",
        pointer=pointer,
    )
    mapping = _mapping(source=None, header=header, project_root=project_root, updated_at=updated_at, file_facts=file_facts, logical_lines=logical_lines)
    owner_text = [f"- `{owner}`" for owner in owners] or ["- none found in the static include closure"]
    sections = [
        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact compiled-include header mirror.", "content": f"The authoritative header is `{relative}`."},
        {"id": "s-mapping", "title": "SOURCE MAPPING", "capsule": "The header is explicit and has no inferred source owner.", "content": "\n".join(mapping)},
        {"id": "s-ownership", "title": "COMPILED OWNERS", "capsule": "Only translation units reaching this header in the static include closure are listed.", "content": "\n".join(owner_text)},
        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Header ownership is include evidence, not semantic authorship.", "content": "The header-only artifact does not claim a paired translation unit, binary identity, or runtime behavior."},
        {"id": "s-source-ledger", "title": "SOURCE LEDGER", "capsule": "The header ledger is verbatim numbered evidence and the source ledger is explicitly empty.", "content": "### HEADER " + str(len(header_lines)) + " LINES\n~~~text\n" + _ledger_rows("H", header_lines) + "\n~~~\n\n### SOURCE 0 LINES\n~~~text\n~~~\n\nEND OF BLUEPRINT"},
    ]
    return BlueprintRecord(relative, filename_for(relative, header_only=True), artifact_id, "header_only", None, header, _document(metadata=metadata, title=f"{artifact_id}: {relative}", sections=sections, render_document=render_document))


def render_blueprints(
    survey: Survey,
    project_root: Path,
    *,
    headers: dict[str, tuple[Path, list[str]]],
    workspace_id: str,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    updated_at: str,
) -> BlueprintSet:
    render_frontmatter, pointer, render_document, helpers = _load_harness()
    _ = render_frontmatter  # The shared document renderer validates the header.
    file_facts, logical_lines = helpers
    records = [
        _source_blueprint(
            unit,
            project_root=project_root,
            workspace_id=workspace_id,
            task_id=task_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            updated_at=updated_at,
            pointer=pointer,
            render_document=render_document,
            file_facts=file_facts,
            logical_lines=logical_lines,
        )
        for unit in survey.units
    ]
    for relative, (header, owners) in sorted(headers.items()):
        records.append(
            _header_blueprint(
                relative,
                header,
                project_root=project_root,
                workspace_id=workspace_id,
                task_id=task_id,
                goal_id=goal_id,
                milestone_id=milestone_id,
                updated_at=updated_at,
                owners=owners,
                pointer=pointer,
                render_document=render_document,
                file_facts=file_facts,
                logical_lines=logical_lines,
            )
        )
    return BlueprintSet(tuple(sorted(records, key=lambda value: value.filename.casefold())), tuple(sorted(headers)))


def render_binding_documents(
    *,
    survey: Survey,
    project_root: Path,
    workspace_id: str,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    updated_at: str,
    header_closure: dict[str, list[str]],
    include_edges: list[dict[str, object]] | None = None,
    include_topology_sha256: str = "",
) -> dict[str, str]:
    _, pointer, render_document, _ = _load_harness()
    source_rows = "\n".join(f"| `{unit.relative_path}` | yes |" for unit in survey.units)
    if not source_rows:
        source_rows = "| (none) | no |"
    # dataflow_membership() is intentionally strict and configurable. The empty
    # prefix heading lets it resolve root-level source names without inventing a
    # directory prefix; paths containing a slash remain fully qualified.
    membership = "### 2.3 Project translation-unit membership\n\n**``\n\n| Source | Compiled |\n|---|---|\n" + source_rows + "\n\n**End of translation-unit membership\n\n### 2.7 End of source membership"
    include_rows = "\n".join(
        f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in owners)
        for header, owners in sorted(header_closure.items())
    ) or "- none"
    canonical_edges = include_edges or []
    edge_body = (
        f"Edge count: `{len(canonical_edges)}`\n"
        f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\n\n"
        "Exact compiler-observed rows are retained in machine authority and the SQLite projection; "
        "this human-readable index binds them by count and digest without duplicating the graph."
    )
    index_metadata = {
        "schema": "kairos-context/v1",
        "id": "PROJECT_SOURCE_INDEX",
        "type": "documentation",
        "revision": 1,
        "state": "ready",
        "authority": "architecture_authority",
        "workspace": workspace_id,
        "route": f"{workspace_id}/PROJECT_SOURCE_INDEX",
        "updated_at": updated_at,
        "capsule": "Compiler-recorded project membership and static local-header closure for the initial KAIROS ground truth.",
        "claim_boundary": "This index records compiler/source membership and bounded include evidence; it does not prove link identity, runtime execution, or semantic intent.",
        "entities": ["PROJECT_SOURCE_INDEX", "compile_commands", "translation units", "include closure"],
        "facets": ["ground-truth", "build-membership", "include-closure", "provenance"],
        "criteria": [],
        "does_not_answer": ["binary identity", "runtime result", "semantic ownership"],
        "answers": [
            {"intent": "membership", "question": "Which translation units are compiler-recorded for this project?", "target": "s-project-membership", "language": "en"},
            {"intent": "dependency", "question": "Which local headers are in the static compiled include closure?", "target": "s-include-closure", "language": "en"},
            {"intent": "dependency", "question": "Which direct C-family include edges are compiler-observed?", "target": "s-include-edges", "language": "en"},
            {"intent": "provenance", "question": "Which hashes bind the initial project source authority?", "target": "s-provenance", "language": "en"},
        ],
        "refs": {
            "method": pointer("docs/PROJECT_KICKSTART.md", section="s-ground-truth", artifact_id="PROJECT_KICKSTART", version="1", relation="constrained_by", tags=("method", "ground-truth"), source="generated"),
        },
        "search_contract": [
            {"query": "Which translation units are compiler-recorded for this project?", "expected": "PROJECT_SOURCE_INDEX#s-project-membership", "required_top_k": 1},
            {"query": "Which local headers are in the static compiled include closure?", "expected": "PROJECT_SOURCE_INDEX#s-include-closure", "required_top_k": 1},
        ],
    }
    source_hash = survey.compile_commands_sha256.upper()
    index = render_document(index_metadata, "PROJECT SOURCE INDEX", [
        {"id": "s-authority", "title": "SOURCE AUTHORITY", "capsule": "compile_commands.json is the compiler-produced membership authority.", "content": f"- Kind: `{survey.source_kind}`\n- compile_commands SHA-256: `{source_hash}`\n- Translation units: `{len(survey.units)}`\n- External include roots not projected: `{len(survey.ignored_external_include_roots)}`"},
        {"id": "s-project-membership", "title": "PROJECT MEMBERSHIP", "capsule": "The exact translation-unit set is repeated in the Workshop DATAFLOW authority format.", "content": membership},
        {"id": "s-include-closure", "title": "STATIC INCLUDE CLOSURE", "capsule": "Only local headers resolved from configured include roots and source directories are projected.", "content": include_rows},
        {"id": "s-include-edges", "title": "DIRECT COMPILER INCLUDE EDGES", "capsule": "Direct include consumers are kept separate from transitive compiled roots and byte owners.", "content": edge_body},
        {"id": "s-provenance", "title": "PROVENANCE", "capsule": "Source membership is bound to the input hash and exact source ledgers.", "content": f"The compiler authority hash is `{source_hash}`. Every generated blueprint repeats raw and logical hashes for its live file."},
    ])
    method_metadata = {
        "schema": "kairos-context/v1",
        "id": "PROJECT_KICKSTART",
        "type": "documentation",
        "revision": 1,
        "state": "ready",
        "authority": "architecture_authority",
        "workspace": workspace_id,
        "route": f"{workspace_id}/PROJECT_KICKSTART",
        "updated_at": updated_at,
        "capsule": "Procedure for turning a compiler-backed project into exact KAIROS Markdown blueprints, Workshop authority and a same-heartbeat database projection.",
        "claim_boundary": "This procedure defines project initiation and evidence boundaries; it does not authorize live Runtime edits or claim a successful build.",
        "entities": ["PROJECT_KICKSTART", "CMake", "compile_commands", "KAIROS", "Workshop"],
        "facets": ["initiation", "ground-truth", "provenance", "fail-closed"],
        "criteria": [],
        "does_not_answer": ["runtime execution result", "binary/source identity", "release approval"],
        "answers": [
            {"intent": "ground_truth", "question": "How is an arbitrary project turned into KAIROS ground truth?", "target": "s-ground-truth", "language": "en"},
            {"intent": "boundary", "question": "Which weak evidence does project initiation refuse?", "target": "s-boundary", "language": "en"},
            {"intent": "workflow", "question": "Which steps create and verify the initial project corpus?", "target": "s-workflow", "language": "en"},
        ],
        "refs": {"source_index": pointer("docs/PROJECT_SOURCE_INDEX.md", section="s-project-membership", artifact_id="PROJECT_SOURCE_INDEX", version="1", relation="indexes", tags=("build", "membership"), source="generated")},
        "search_contract": [
            {"query": "How is an arbitrary project turned into KAIROS ground truth?", "expected": "PROJECT_KICKSTART#s-ground-truth", "required_top_k": 1},
            {"query": "Which weak evidence does project initiation refuse?", "expected": "PROJECT_KICKSTART#s-boundary", "required_top_k": 1},
        ],
    }
    method = render_document(method_metadata, "PROJECT KICKSTART PROCEDURE", [
        {"id": "s-ground-truth", "title": "GROUND TRUTH", "capsule": "CMake is executed only to obtain the compiler-produced compile_commands authority; raw binary or stem guesses are never promoted.", "content": "The user supplies a project root and either an existing `compile_commands.json` or a CMake file. For CMake input, the framework runs an isolated configure with `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, then validates every recorded translation unit as an existing project-local C/C++ file. The resulting source set, compiler command fingerprints, include roots, exact source/header facts and input hashes are materialized as Markdown and a machine manifest."},
        {"id": "s-workflow", "title": "INITIATION WORKFLOW", "capsule": "Discover, render, promote, and verify are one bounded initiation transaction.", "content": "1. Survey compiler records. 2. Resolve local quoted and angle includes using command include roots. 3. Render one exact source blueprint per translation unit and one header-only blueprint per uncovered closure header. 4. Render the source index and procedure document. 5. Generate a derived Workshop config and CMake membership authority. 6. Promote all Markdown through one KAIROS heartbeat. 7. Verify Workshop corpus parity, database projections, hashes and KAIROS health."},
        {"id": "s-boundary", "title": "FAIL-CLOSED BOUNDARY", "capsule": "Binary-only inputs, regex-only CMake guesses, missing files, path escapes, ambiguous ledger paths and unsupported suffixes are refused.", "content": "A binary does not disclose a trustworthy source membership without a project-specific symbol/debug adapter, so it is not accepted by this generic initiator. CMake source text is not regex-mined as ground truth; CMake is an execution engine that must emit compiler records. Missing or external source files and unrepresentable paths fail before any corpus is marked verified."},
        {"id": "s-verification", "title": "VERIFICATION CONTRACT", "capsule": "A successful initiation requires every source and include header to have exact ledger and database evidence.", "content": "The Workshop must report `verified: true`; CMake membership, DATAFLOW membership and blueprint source membership must be identical; every ledger must match raw and logical file facts; external and managed blueprints must be byte-identical; and the KAIROS database projection and health audit must pass. A failure leaves evidence for diagnosis but never silently downgrades the authority."},
    ])
    return {"docs/PROJECT_SOURCE_INDEX.md": index, "docs/PROJECT_KICKSTART.md": method}
