from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .binding import _resolve_include_closure
from .errors import KickstartError
from .portable import portable_path, portable_probe_argv, reject_link_components
from .survey import compiler_context_sha256, compiler_probe_argv, survey_compile_commands
from .universal import UniversalSurvey


C_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".cuh"})


@dataclass(frozen=True)
class UniversalWorkshopBinding:
    documents: dict[str, str]
    config_path: Path
    header_count: int
    workshop_config: dict[str, Any]


def _canonical_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, text: str) -> None:
    _atomic_bytes(path, text.encode("utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_renderer():
    harness = _canonical_root() / "kairos" / "kairos_harness"
    if str(harness) not in sys.path:
        sys.path.insert(0, str(harness))
    from kairos.templates import render_document
    return render_document


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def header_artifact_id(relative: str) -> str:
    return f"PROJECT_HEADER_{_digest(relative)}"


def header_filename(relative: str) -> str:
    return header_artifact_id(relative) + ".md"


def _logical_lines(raw: bytes) -> tuple[list[str], bool]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KickstartError("SOURCE_NON_UTF8", f"governed source is not UTF-8: {exc}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    eof = normalized.endswith("\n")
    rows = normalized.split("\n")
    if eof:
        rows = rows[:-1]
    return rows, eof


def _ledger_rows(prefix: str, lines: list[str]) -> str:
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{prefix}:{index:0{width}d} {line}" for index, line in enumerate(lines, 1))


def _render_header_blueprint(
    *,
    relative: str,
    path: Path,
    owners: list[str],
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    updated_at: str,
) -> str:
    render_document = _load_renderer()
    raw = path.read_bytes()
    lines, eof = _logical_lines(raw)
    artifact_id = header_artifact_id(relative)
    sha = hashlib.sha256(raw).hexdigest().upper()
    metadata = {
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
        "capsule": f"Exact compiler-reached header mirror for {relative}.",
        "claim_boundary": "This mirror proves exact header bytes and compiler-scoped ownership only; it does not infer semantic ownership or runtime behavior.",
        "entities": [artifact_id, relative, "c_family", "KAIROS"],
        "facets": ["project-initiation", "universal-intake", "c_family", "header-only", "exact-ledger"],
        "criteria": [],
        "does_not_answer": ["runtime execution result", "semantic ownership", "binary provenance"],
        "answers": [
            {"intent": "implementation_location", "question": f"Which exact header bytes are bound to {relative}?", "target": "s-mapping", "language": "en"},
            {"intent": "dependency", "question": f"Which compiler-backed source files reach {relative}?", "target": "s-ownership", "language": "en"},
        ],
        "refs": {},
        "search_contract": [
            {"query": f"Which exact header bytes are bound to {relative}?", "expected": f"{artifact_id}#s-mapping", "required_top_k": 5}
        ],
    }
    mapping = "\n".join((
        'target_source_file = "none"',
        f"target_header_file = {json.dumps(relative, ensure_ascii=False)}",
        "source_line_count = 0",
        "source_byte_count = 0",
        'source_sha256 = "none"',
        f"header_line_count = {len(lines)}",
        f"header_byte_count = {len(raw)}",
        f'header_sha256 = "{sha}"',
        f"header_eof_newline = {str(eof).lower()}",
        'ecosystem = "c_family"',
        f"generated_at = {json.dumps(updated_at)}",
    ))
    owner_rows = "\n".join(f"- `{owner}`" for owner in sorted(owners)) or "- none"
    return render_document(metadata, f"{artifact_id}: {relative}", [
        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact compiler-reached header mirror.", "content": f"The governed header file is `{relative}`."},
        {"id": "s-mapping", "title": "SOURCE MAPPING", "capsule": "The header path and exact byte facts are explicit.", "content": mapping},
        {"id": "s-ownership", "title": "COMPILED OWNERS", "capsule": "Only compiler-backed translation units reaching this header are listed.", "content": owner_rows},
        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Header membership comes from the C-family include closure, not filename inference.", "content": "- Authority: `compiler_backed_include_closure`\n- Current byte hashes are owned by [SOURCE MAPPING](#s-mapping)."},
        {"id": "s-source-ledger", "title": "SOURCE LEDGER", "capsule": "Verbatim numbered header evidence.", "content": f"### HEADER {len(lines)} LINES\n~~~text\n{_ledger_rows('H', lines)}\n~~~\n\n### SOURCE 0 LINES\n~~~text\n~~~\n\nEND OF BLUEPRINT"},
    ])


def _render_source_index(
    survey: UniversalSurvey,
    *,
    header_owners: dict[str, list[str]],
    include_edges: list[dict[str, Any]] | None = None,
    include_topology_sha256: str = "",
    workspace_id: str,
    updated_at: str,
) -> str:
    render_document = _load_renderer()
    source_rows = "\n".join(
        f"| `{source.relative_path}` | `{source.ecosystem}` |"
        for source in survey.sources
    )
    workshop_rows = "\n".join(f"| `{source.relative_path}` | yes |" for source in survey.sources)
    include_rows = "\n".join(
        f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in owners)
        for header, owners in sorted(header_owners.items())
    ) or "- none"
    canonical_edges = include_edges or []
    edge_body = (
        f"Edge count: `{len(canonical_edges)}`\n"
        f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\n\n"
        "Exact compiler-observed rows are retained in machine authority and the SQLite projection; "
        "this human-readable index binds them by count and digest without duplicating the graph."
    )
    metadata = {
        "schema": "kairos-context/v1",
        "id": "PROJECT_SOURCE_INDEX",
        "type": "documentation",
        "revision": 1,
        "state": "ready",
        "authority": "architecture_authority",
        "workspace": workspace_id,
        "route": f"{workspace_id}/PROJECT_SOURCE_INDEX",
        "updated_at": updated_at,
        "capsule": "Exact universal source membership with ecosystem classification and compiler-backed C-family header closure.",
        "claim_boundary": "Static ecosystems prove observed file membership only. C-family source/header reachability is additionally compiler-backed. No dynamic import or runtime reachability is inferred.",
        "entities": ["PROJECT_SOURCE_INDEX", *survey.ecosystems, "KAIROS"],
        "facets": ["ground-truth", "universal-intake", "source-membership", "provenance"],
        "criteria": [],
        "does_not_answer": ["dynamic imports", "runtime reachability", "successful build"],
        "answers": [
            {"intent": "membership", "question": "Which source files are governed by this project?", "target": "s-project-membership", "language": "en"},
            {"intent": "dependency", "question": "Which C-family headers are compiler-reached?", "target": "s-include-closure", "language": "en"},
            {"intent": "dependency", "question": "Which direct C-family include edges are compiler-observed?", "target": "s-include-edges", "language": "en"},
        ],
        "refs": {},
        "search_contract": [
            {"query": "Which source files are governed by this project?", "expected": "PROJECT_SOURCE_INDEX#s-project-membership", "required_top_k": 1}
        ],
    }
    membership = (
        "### 2.3 Governed source membership\n\n"
        "**``\n\n"
        "| Source | Governed |\n|---|---|\n"
        + workshop_rows
        + "\n\n**End of governed source membership\n\n"
        "### 2.7 End of governed source membership\n\n"
        "### Ecosystem classification\n\n"
        "| Source | Ecosystem |\n|---|---|\n"
        + source_rows
    )
    return render_document(metadata, "PROJECT SOURCE INDEX", [
        {"id": "s-authority", "title": "SOURCE AUTHORITY", "capsule": "Membership is explicit and ecosystem-bounded.", "content": "Detected ecosystems: " + ", ".join(f"`{value}`" for value in survey.ecosystems)},
        {"id": "s-project-membership", "title": "PROJECT MEMBERSHIP", "capsule": "The Workshop and human-readable source inventories describe the same exact set.", "content": membership},
        {"id": "s-include-closure", "title": "C-FAMILY INCLUDE CLOSURE", "capsule": "Only compiler-resolved project-local headers and their owning translation units are listed.", "content": include_rows},
        {"id": "s-include-edges", "title": "DIRECT COMPILER INCLUDE EDGES", "capsule": "Direct include consumers are distinct from transitive compiled roots and byte owners.", "content": edge_body},
        {"id": "s-boundary", "title": "CLAIM BOUNDARY", "capsule": "Static source membership is not promoted into invented dependency or build claims.", "content": "Python/JS/TS/Rust/Go/JVM/.NET/Ruby/PHP are governed as exact project-local source files. C/C++/CUDA translation-unit and header membership remains compiler-backed and fail-closed."},
    ])


def _membership_authority(paths: list[str]) -> str:
    return "\n".join([
        "# Derived KAIROS Workshop source-membership authority.",
        "# This file is NOT a claim that non-C-family files are CMake translation units.",
        "# It is a machine-readable parity surface for the sealed Workshop corpus.",
        "set(KAIROS_TRANSLATION_UNITS",
        *(f'  "{path}"' for path in paths),
        ")",
        "",
    ])


def _c_family_evidence(project_root: Path, compile_commands: Path | None):
    c_sources = [source for source in project_root.rglob("*") if source.is_file() and source.suffix.casefold() in {".c", ".cc", ".cpp", ".cxx", ".cu"}]
    if not c_sources:
        return None, {}, None
    if compile_commands is None:
        raise KickstartError("C_FAMILY_COMPILER_AUTHORITY_REQUIRED", "C-family source exists but compiler authority was not supplied")
    survey = survey_compile_commands(compile_commands, project_root, source_kind="compile_commands")
    headers, unresolved = _resolve_include_closure(project_root, survey)
    if unresolved:
        raise KickstartError(
            "C_FAMILY_INCLUDE_AUTHORITY_UNVERIFIED",
            "C-family compiler membership was found but its project-local include closure is not fully provable",
            details=unresolved,
        )
    from .include_edges import authority_payload, collect_include_edges, owners_from_edges
    edges, edge_issues = collect_include_edges(project_root, survey)
    if edge_issues:
        raise KickstartError("INCLUDE_EDGE_AUTHORITY_UNVERIFIED", "direct compiler include-edge authority is not fully provable", details=edge_issues)
    closure_owners = {relative: sorted(owners) for relative, (_, owners) in headers.items()}
    if owners_from_edges(edges) != closure_owners:
        raise KickstartError("INCLUDE_EDGE_CLOSURE_MISMATCH", "direct include edges disagree with transitive header closure")
    return survey, headers, authority_payload(edges)


def prepare_universal_workshop_binding(
    *,
    project_root: Path,
    workspace: Path,
    survey: UniversalSurvey,
    documents: dict[str, str],
    compile_commands: Path | None,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    updated_at: str,
) -> UniversalWorkshopBinding:
    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    project_root = project_root.resolve()
    workspace = Path(workspace)
    reject_link_components(workspace, label="KAIROS workspace")
    workspace = workspace.resolve()
    c_survey, headers, edge_payload = _c_family_evidence(project_root, compile_commands)
    header_owners = {relative: sorted(owners) for relative, (_, owners) in headers.items()}

    rendered = dict(documents)
    rendered["docs/PROJECT_SOURCE_INDEX.md"] = _render_source_index(
        survey,
        header_owners=header_owners,
        include_edges=(edge_payload or {}).get("edges", []),
        include_topology_sha256=str((edge_payload or {}).get("topology_sha256", "")),
        workspace_id=workspace_id,
        updated_at=updated_at,
    )
    for relative, (path, owners) in sorted(headers.items()):
        rendered[f"code/{header_filename(relative)}"] = _render_header_blueprint(
            relative=relative,
            path=path,
            owners=owners,
            workspace_id=workspace_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
            updated_at=updated_at,
        )

    intake_dir = workspace / ".kairos" / "project-intake"
    blueprint_root = intake_dir / "blueprints"
    blueprint_root.mkdir(parents=True, exist_ok=True)
    for relative, text in rendered.items():
        if relative.startswith("code/"):
            _atomic_text(blueprint_root / Path(relative).name, text)

    source_paths = [source.relative_path for source in survey.sources]
    membership_path = intake_dir / "PROJECT_SOURCE_AUTHORITY.cmake"
    _atomic_text(membership_path, _membership_authority(source_paths))

    machine_authorities = [
        "workshop.config.json",
        "universal-intake.json",
        "project-intake/PROJECT_SOURCE_AUTHORITY.cmake",
    ]
    retained_compile: Path | None = None
    if c_survey is not None and compile_commands is not None:
        retained_compile = intake_dir / "compile_commands.json"
        _atomic_bytes(retained_compile, Path(compile_commands).read_bytes())
        machine_authorities.append("project-intake/compile_commands.json")
        edge_authority_path = intake_dir / "include-edges.json"
        _atomic_json(edge_authority_path, edge_payload)
        machine_authorities.append("project-intake/include-edges.json")

    config_path = workspace / ".kairos" / "workshop.config.json"
    machine_root = config_path.parent.resolve()
    movable_roots = (project_root.resolve(), workspace.resolve())
    serialize_path = lambda value: portable_path(
        value, base=machine_root, relative_roots=movable_roots
    )

    def serialize_probe(value: tuple[str, ...]) -> list[str]:
        return list(
            portable_probe_argv(value, base=machine_root, relative_roots=movable_roots)
        )

    include_roots = [serialize_path(root) for root in c_survey.include_roots] if c_survey is not None else []
    include_sequences: dict[str, list[list[str]]] = {}
    include_variants: dict[str, list[dict[str, Any]]] = {}
    compiler_probes: dict[str, list[dict[str, Any]]] = {}
    if c_survey is not None:
        for unit in c_survey.units:
            include_sequences[unit.relative_path] = [
                [serialize_path(root) for root in sequence] for sequence in unit.include_root_sequences
            ]
            variant_rows: list[dict[str, Any]] = []
            probe_rows: list[dict[str, Any]] = []
            for variant in unit.variants:
                probe = compiler_probe_argv(variant.argv, directory=variant.directory, project_root=project_root)
                row = {
                    "quote_roots": [serialize_path(root) for root in variant.quote_include_roots],
                    "angle_roots": [serialize_path(root) for root in variant.angle_include_roots],
                    "idirafter_roots": [serialize_path(root) for root in variant.idirafter_roots],
                    "compiler_probe": (
                        {"argv": serialize_probe(probe), "directory": serialize_path(variant.directory)}
                        if probe else None
                    ),
                }
                if row not in variant_rows:
                    variant_rows.append(row)
                if probe:
                    probe_row = {
                        "argv": serialize_probe(probe),
                        "directory": serialize_path(variant.directory),
                    }
                    if probe_row not in probe_rows:
                        probe_rows.append(probe_row)
            include_variants[unit.relative_path] = variant_rows
            compiler_probes[unit.relative_path] = probe_rows

    source_extensions = sorted({Path(source.relative_path).suffix.casefold().lstrip(".") for source in survey.sources})
    config = {
        "schema": "runtime-sync-workshop-config/v1",
        "membership_authority_kind": "universal-static-plus-compiler",
        "path_resolution": "config-directory-relative-v1",
        "codebase_root": serialize_path(project_root),
        "runtime_root": serialize_path(project_root),
        "cmake_file": serialize_path(membership_path),
        "cmake_source_sets": ["KAIROS_TRANSLATION_UNITS"],
        "cmake_extra_sources": [],
        "dataflow_index": serialize_path(workspace / "docs" / "PROJECT_SOURCE_INDEX.md"),
        "blueprint_root": serialize_path(blueprint_root),
        "managed_blueprint_root": serialize_path(workspace / "code"),
        "kairos_workspace": serialize_path(workspace),
        "kairos_harness": "@bundled",
        "kairos_database": serialize_path(workspace / ".kairos" / "kairos.db"),
        "include_roots": include_roots,
        "translation_unit_include_roots": include_sequences,
        "translation_unit_include_variants": include_variants,
        "translation_unit_compiler_probes": compiler_probes,
        "expected_translation_units": len(source_paths),
        "blueprint_ignore": [],
        "auxiliary_documents": [],
        "machine_authority_files": sorted(machine_authorities),
        "header_extensions": sorted(C_HEADER_SUFFIXES),
        "additional_header_owners": {},
        "header_only_blueprints": {relative: header_filename(relative) for relative in sorted(headers)},
        "dependent_tests": {},
        "dependent_test_root": "tests",
        "dependent_test_suffixes": ["." + value for value in source_extensions],
        "source_ecosystems": {source.relative_path: source.ecosystem for source in survey.sources},
        "source_authority": {
            "index_section_start": "### 2.3 ",
            "index_section_end": "### 2.7 ",
            "index_membership_token": "yes",
            "index_prefix_reset_marker": "**End of governed source membership",
            "translation_unit_extensions": source_extensions,
            "include_ownership_section_id": "s-include-closure",
            **({
                "include_edges_section_id": "s-include-edges",
                "include_edges_file": "project-intake/include-edges.json",
            } if edge_payload is not None else {}),
        },
        "state_directory": ".state",
        "transaction_directory": "transactions",
    }
    if c_survey is not None:
        config["compiler_context_sha256"] = compiler_context_sha256(c_survey, project_root)
    _atomic_json(config_path, config)
    return UniversalWorkshopBinding(
        documents=dict(sorted(rendered.items())),
        config_path=config_path,
        header_count=len(headers),
        workshop_config=config,
    )


def verify_universal_workshop_binding(config_path: Path) -> dict[str, Any]:
    workshop_src = _canonical_root() / "workshop" / "src"
    if str(workshop_src) not in sys.path:
        sys.path.insert(0, str(workshop_src))
    from runtime_sync_workshop.corpus import build_corpus_manifest, load_config

    return build_corpus_manifest(load_config(config_path), verify_database=True)
