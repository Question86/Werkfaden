from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .blueprints import BlueprintSet, filename_for, render_binding_documents, render_blueprints
from .cmake import CMakeConfigure, configure_compile_commands
from .errors import KickstartError
from .portable import portable_path, portable_probe_argv, reject_link_components
from .survey import HEADER_SUFFIXES, Survey, compiler_context_sha256, compiler_probe_argv, reject_links, survey_compile_commands


def _canonical_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _harness_imports():
    harness = _canonical_root() / "kairos" / "kairos_harness"
    import sys

    if str(harness) not in sys.path:
        sys.path.insert(0, str(harness))
    from kairos.health import health_audit
    from kairos.heartbeat import run_heartbeat
    from kairos.governance import issue_external_edit_permit
    from kairos.util import atomic_write_json, atomic_write_text, read_json, utc_now
    from kairos.workspace import database_path, initialize_workspace, initialize_project_workspace, load_config
    from kairos.loops import validate_project_kickoff_contract

    return (
        health_audit, run_heartbeat, issue_external_edit_permit, atomic_write_json,
        atomic_write_text, read_json, utc_now, database_path, initialize_workspace,
        initialize_project_workspace, validate_project_kickoff_contract, load_config,
    )


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_workspace_id(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.:-]{2,127}", value):
        raise KickstartError("WORKSPACE_ID_INVALID", f"workspace_id is not a stable upper-case identifier: {value!r}")
    return value


def _workspace_scope(workspace: Path, load_config, read_json) -> tuple[str, str, str, str]:
    config = load_config(workspace)
    workspace_id = _safe_workspace_id(str(config.get("workspace_id", "")))
    current = read_json(workspace / "current.json", default={})
    if not isinstance(current, dict):
        raise KickstartError("CURRENT_STATE_INVALID", "current.json must be an object")
    if str(current.get("lifecycle", "")).upper() == "FINALIZED":
        raise KickstartError("WORKSPACE_FINALIZED", "cannot bind a project into a FINALIZED workspace; create a fresh workspace")
    goal_id = str(current.get("active_goal", ""))
    milestone_id = str(current.get("active_milestone", ""))
    task_id = str(current.get("active_task", ""))
    if not all((goal_id, milestone_id, task_id)):
        raise KickstartError("ACTIVE_SCOPE_MISSING", "current.json must declare active goal, milestone and task")
    task_path = workspace / "tasks" / f"task_{task_id}.md"
    if not task_path.is_file():
        raise KickstartError("ACTIVE_TASK_MISSING", f"active task source is missing: {task_path}")
    return workspace_id, goal_id, milestone_id, task_id


def _compiler_selected_literal(
    *,
    argv: tuple[str, ...],
    directory: Path,
    including: Path,
    opener: str,
    include: str,
    project_root: Path,
) -> tuple[str, Path | None]:
    """Ask a retained GNU/Clang compiler which literal include it selects.

    The probe preprocesses synthetic stdin only. ``-H`` exposes the direct selected
    dependency without compiling or executing project source.  The including-file
    directory is represented by a front ``-iquote`` only for quoted includes.

    Returns ``(local|external|missing|unsupported, path)``.
    """
    probe = compiler_probe_argv(argv, directory=directory, project_root=project_root)
    if not probe:
        return "unsupported", None
    language = "c" if including.suffix.casefold() == ".c" else "c++"
    command = [*probe]
    if opener == '"':
        command.extend(("-iquote", str(including.parent)))
    command.extend(("-H", "-E", "-x", language, "-"))
    source = f"#include {opener}{include}{'>' if opener == '<' else chr(34)}\n"
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            input=source,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unsupported", None
    selected: Path | None = None
    for line in completed.stderr.splitlines():
        match = re.match(r"^\.\s+(.+?)\s*$", line)
        if not match:
            continue
        candidate = Path(match.group(1).strip())
        try:
            selected = candidate.resolve(strict=True)
        except OSError:
            continue
        if selected.is_file():
            break
    if selected is None:
        return ("missing", None) if completed.returncode != 0 else ("unsupported", None)
    try:
        selected.relative_to(project_root.resolve())
    except ValueError:
        return "external", selected
    return "local", selected



def _resolve_include_closure(project_root: Path, survey: Survey) -> tuple[dict[str, tuple[Path, list[str]]], list[dict[str, str]]]:
    """Resolve a conservative project-local include closure using compiler order.

    Resolution is evaluated per translation unit and per distinct compiler command.
    Existing external include roots stop lookup at the point where the compiler would
    have selected them, preventing a later project-local same-named header from being
    falsely projected. Dynamic/macro includes are refused because this static scanner
    cannot prove their selected dependency.
    """
    include_re = re.compile(r'(?m)^\s*#\s*include\s*([<"])([^>"\n]+)[>"]')
    any_include_re = re.compile(r'(?m)^\s*#\s*include\s+([^\n]+)')
    root = project_root.resolve()
    queue: list[tuple[Path, str, Any]] = []
    for unit in survey.units:
        for variant in unit.variants:
            queue.append((unit.absolute_path, unit.relative_path, variant))
    seen: set[tuple[Path, str, tuple[str, ...]]] = set()
    headers: dict[str, tuple[Path, list[str]]] = {}
    unresolved: list[dict[str, str]] = []
    while queue:
        path, owner, variant = queue.pop(0)
        path = path.resolve()
        visit = (path, owner, variant.argv)
        if visit in seen:
            continue
        seen.add(visit)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise KickstartError("INCLUDE_SCAN_FAILED", f"cannot read include-graph input: {path}") from exc

        literal_spans = {(match.start(), match.group(0)) for match in include_re.finditer(text)}
        for match in any_include_re.finditer(text):
            if not any(start_pos == match.start() for start_pos, _ in literal_spans):
                token = match.group(1).strip()
                unresolved.append({
                    "including": path.relative_to(root).as_posix(),
                    "include": token,
                    "reason": "dynamic_or_macro_include",
                })

        for match in include_re.finditer(text):
            opener, include = match.group(1), match.group(2).strip()
            include_path = Path(include.replace("/", os.sep))
            # Quoted includes check the including file directory first. Explicit
            # search roots then follow compiler category order; angle includes
            # deliberately exclude -iquote roots.
            search_roots = variant.quote_include_roots if opener == '"' else variant.angle_include_roots
            candidates: list[Path] = []
            if opener == '"':
                candidates.append(path.parent / include_path)
            candidates.extend(root_dir / include_path for root_dir in search_roots)
            resolved_local: Path | None = None
            stopped_external = False
            for candidate in candidates:
                try:
                    reject_links(candidate, root, code="LINKED_HEADER_UNSUPPORTED")
                except KickstartError:
                    # reject_links also reports outside-root candidates. External
                    # roots are allowed as compiler evidence, so inspect them
                    # without weakening project-local link checks.
                    try:
                        candidate_abs = candidate.resolve(strict=True)
                    except OSError:
                        continue
                    try:
                        candidate_abs.relative_to(root)
                    except ValueError:
                        if candidate_abs.is_file():
                            stopped_external = True
                            break
                        continue
                    raise
                try:
                    candidate_resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                try:
                    candidate_resolved.relative_to(root)
                except ValueError:
                    if candidate_resolved.is_file():
                        stopped_external = True
                        break
                    continue
                if candidate_resolved.is_file() and candidate_resolved.suffix.casefold() in HEADER_SUFFIXES:
                    resolved_local = candidate_resolved
                    break
            # -idirafter is searched after compiler implicit system roots. A
            # project-local candidate there can therefore be shadowed by a
            # system header with the same spelling. Ask the retained compiler
            # to select the literal dependency instead of guessing.
            if resolved_local is not None and any(
                resolved_local.is_relative_to(after_root) for after_root in variant.idirafter_roots
            ):
                status, selected = _compiler_selected_literal(
                    argv=variant.argv, directory=variant.directory, including=path,
                    opener=opener, include=include, project_root=root,
                )
                if status == "external":
                    resolved_local = None
                    stopped_external = True
                elif status == "local" and selected is not None:
                    resolved_local = selected
                elif status in {"unsupported", "missing"}:
                    unresolved.append({
                        "including": path.relative_to(root).as_posix(),
                        "include": include,
                        "reason": "include_selection_ambiguous",
                    })
                    continue

            if resolved_local is None:
                if opener == '"' and not stopped_external:
                    status, selected = _compiler_selected_literal(
                        argv=variant.argv, directory=variant.directory, including=path,
                        opener=opener, include=include, project_root=root,
                    )
                    if status == "external":
                        stopped_external = True
                    elif status == "local" and selected is not None:
                        resolved_local = selected
                if resolved_local is None:
                    if opener == '"' and not stopped_external:
                        unresolved.append({
                            "including": path.relative_to(root).as_posix(),
                            "include": include,
                            "reason": "quoted_include_unresolved",
                        })
                    continue
            if resolved_local.suffix.casefold() not in HEADER_SUFFIXES:
                unresolved.append({
                    "including": path.relative_to(root).as_posix(),
                    "include": include,
                    "reason": "local_include_suffix_unsupported",
                    "selected": resolved_local.relative_to(root).as_posix(),
                })
                continue
            relative = resolved_local.relative_to(root).as_posix()
            if relative not in headers:
                headers[relative] = (resolved_local, [])
            if owner not in headers[relative][1]:
                headers[relative][1].append(owner)
            queue.append((resolved_local, owner, variant))
    return headers, unresolved


def _cmake_authority(units: tuple[Any, ...]) -> str:
    rows = [
        "# Generated by KAIROS project kickstart from compiler-recorded membership.",
        "# This file is a derived Workshop parser input; the retained compile_commands",
        "# artifact and its SHA-256 remain the primary source-set evidence.",
        "set(KAIROS_TRANSLATION_UNITS",
        *(f'  "{unit.relative_path}"' for unit in units),
        ")",
        "",
    ]
    return "\n".join(rows)


def _manifest(
    *,
    project_root: Path,
    workspace: Path,
    survey: Survey,
    blueprint_set: BlueprintSet,
    header_closure: dict[str, tuple[Path, list[str]]],
    unresolved_includes: list[dict[str, str]],
    retained_compile_commands: Path,
    cmake_configure: CMakeConfigure | None,
    intake_directory: Path,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    project_intent_sha256: str | None,
) -> dict[str, Any]:
    machine_root = intake_directory.parent.resolve()
    movable_roots = (project_root.resolve(), workspace.resolve())

    def facts(path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        return {
            "path": portable_path(path, base=machine_root, relative_roots=movable_roots),
            "sha256": _sha256(raw),
            "bytes": len(raw),
        }

    return {
        "schema": "kairos-project-intake/v1",
        "state": "verified_pending_workshop_audit",
        "project_root": portable_path(project_root, base=machine_root),
        "workspace": portable_path(workspace, base=machine_root),
        "project_scope": {
            "workspace_id": workspace_id,
            "goal_id": goal_id,
            "milestone_id": milestone_id,
            "task_id": task_id,
            "project_intent_sha256": project_intent_sha256,
        },
        "authority": {
            "kind": survey.source_kind,
            "compile_commands": facts(retained_compile_commands),
            "compiler_context_sha256": compiler_context_sha256(survey, project_root),
            "cmake_file": facts(cmake_configure.cmake_file) if cmake_configure else None,
            "cmake_build_directory": (
                portable_path(
                    cmake_configure.build_directory,
                    base=machine_root,
                    relative_roots=movable_roots,
                )
                if cmake_configure else None
            ),
        },
        "translation_units": [
            {
                "relative_path": unit.relative_path,
                "commands": len(unit.commands),
                "include_roots": [
                    portable_path(root, base=machine_root, relative_roots=movable_roots)
                    for root in unit.include_roots
                ],
                "include_search_variants": [
                    {
                        "quote_roots": [
                            portable_path(root, base=machine_root, relative_roots=movable_roots)
                            for root in variant.quote_include_roots
                        ],
                        "angle_roots": [
                            portable_path(root, base=machine_root, relative_roots=movable_roots)
                            for root in variant.angle_include_roots
                        ],
                        "idirafter_roots": [
                            portable_path(root, base=machine_root, relative_roots=movable_roots)
                            for root in variant.idirafter_roots
                        ],
                    }
                    for variant in unit.variants
                ],
                "facts": facts(unit.absolute_path),
            }
            for unit in survey.units
        ],
        "include_roots": [
            portable_path(root, base=machine_root, relative_roots=movable_roots)
            for root in survey.include_roots
        ],
        "external_include_roots": list(survey.ignored_external_include_roots),
        "include_closure": {
            relative: {"owners": sorted(owners), "facts": facts(path)}
            for relative, (path, owners) in sorted(header_closure.items())
        },
        "unresolved_quoted_includes": unresolved_includes,
        "blueprints": [
            {"filename": record.filename, "artifact_id": record.artifact_id, "kind": record.kind, "relative_path": record.relative_path}
            for record in blueprint_set.records
        ],
        "retained_artifacts": {
            "compile_commands": str(retained_compile_commands.relative_to(intake_directory.parent).as_posix()),
            "build_authority": "project-intake/PROJECT_BUILD_AUTHORITY.cmake",
        },
    }


def initialize_project(
    *,
    project_root: Path,
    workspace: Path,
    compile_commands: Path | None = None,
    cmake_file: Path | None = None,
    cmake_executable: str = "cmake",
    build_directory: Path | None = None,
    workspace_id: str | None = None,
    project_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    (
        health_audit, run_heartbeat, issue_external_edit_permit, atomic_write_json,
        atomic_write_text, read_json, utc_now, _, initialize_workspace,
        initialize_project_workspace, validate_project_kickoff_contract, load_config,
    ) = _harness_imports()
    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {project_root}")
    workspace = Path(workspace)
    reject_link_components(workspace, label="KAIROS workspace")
    workspace = workspace.resolve()
    if (workspace / ".kairos" / "project-intake.json").exists():
        raise KickstartError("PROJECT_ALREADY_BOUND", f"workspace already contains a project intake: {workspace / '.kairos' / 'project-intake.json'}")
    if not (workspace / ".kairos" / "config.json").is_file():
        if workspace.exists() and any(workspace.iterdir()):
            raise KickstartError("WORKSPACE_NOT_FRESH", f"workspace is not a KAIROS workspace and is not empty: {workspace}")
        derived_id = workspace_id or f"KAIROS_PROJECT_{hashlib.sha256(project_root.as_posix().encode('utf-8')).hexdigest()[:12].upper()}"
        if project_spec is None:
            raise KickstartError(
                "PROJECT_INTENT_REQUIRED",
                "fresh project intake requires a validated kairos-project-kickoff/v1 contract; "
                "generate one from the human project intent before compiler discovery",
            )
        try:
            goal, milestone, task, selected_criteria, _ = validate_project_kickoff_contract(project_spec)
        except Exception as exc:
            raise KickstartError("PROJECT_INTENT_INVALID", str(exc)) from exc
        initialize_project_workspace(
            workspace,
            workspace_id=_safe_workspace_id(derived_id),
            goal=goal,
            milestone=milestone,
            task=task,
            selected_criteria=selected_criteria,
        )
    elif project_spec is not None:
        # Existing workspaces already own their active authority. A second intent
        # contract must not silently replace it during source binding.
        try:
            goal, milestone, task, _, _ = validate_project_kickoff_contract(project_spec)
        except Exception as exc:
            raise KickstartError("PROJECT_INTENT_INVALID", str(exc)) from exc
        current_existing = read_json(workspace / "current.json", default={}) or {}
        expected = (str(goal["id"]), str(milestone["id"]), str(task["id"]))
        actual = (
            str(current_existing.get("active_goal", "")),
            str(current_existing.get("active_milestone", "")),
            str(current_existing.get("active_task", "")),
        )
        if expected != actual:
            raise KickstartError(
                "PROJECT_INTENT_SCOPE_MISMATCH",
                f"project intent targets {expected}, but existing workspace authority is {actual}",
            )
    workspace_id_actual, goal_id, milestone_id, task_id = _workspace_scope(workspace, load_config, read_json)
    project_intent_sha256 = (
        _sha256(json.dumps(project_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if project_spec is not None else None
    )
    if workspace_id is not None and _safe_workspace_id(workspace_id) != workspace_id_actual:
        raise KickstartError("WORKSPACE_ID_MISMATCH", f"workspace already declares {workspace_id_actual!r}, not {workspace_id!r}")

    cmake_configure: CMakeConfigure | None = None
    temporary_build = False
    if compile_commands is not None and cmake_file is not None:
        raise KickstartError("AUTHORITY_INPUT_AMBIGUOUS", "provide either compile_commands.json or a CMake file, not both")
    try:
        if cmake_file is not None:
            cmake_configure = configure_compile_commands(project_root, cmake_file, cmake_executable=cmake_executable, build_directory=build_directory)
            source_input = cmake_configure.compile_commands
            temporary_build = cmake_configure.temporary_build
            source_kind = "cmake_compile_commands"
            cmake_hash = _sha256(cmake_configure.cmake_file.read_bytes())
        elif compile_commands is not None:
            source_input = Path(compile_commands)
            source_kind = "compile_commands"
            cmake_hash = None
        else:
            raise KickstartError("AUTHORITY_INPUT_MISSING", "provide --compile-commands or --cmake; binary-only initiation is refused")

        intake_directory = workspace / ".kairos" / "project-intake"
        intake_directory.mkdir(parents=True, exist_ok=True)
        retained_compile_commands = intake_directory / "compile_commands.json"
        survey = survey_compile_commands(
            source_input,
            project_root,
            source_kind=source_kind,
            cmake_file=cmake_configure.cmake_file if cmake_configure else None,
            cmake_sha256=cmake_hash,
        )
        _atomic_bytes(retained_compile_commands, source_input.read_bytes())
        survey = replace(survey, compile_commands=retained_compile_commands)
        headers, unresolved = _resolve_include_closure(project_root, survey)
        if unresolved:
            dynamic = [item for item in unresolved if item.get("reason") == "dynamic_or_macro_include"]
            if dynamic:
                raise KickstartError(
                    "DYNAMIC_INCLUDE_UNSUPPORTED",
                    "one or more preprocessor-computed includes cannot be proven by the static intake scanner; "
                    "supply compiler dependency evidence or simplify the include for this intake",
                    details=dynamic,
                )
            ambiguous = [item for item in unresolved if item.get("reason") == "include_selection_ambiguous"]
            if ambiguous:
                raise KickstartError(
                    "INCLUDE_SELECTION_AMBIGUOUS",
                    "compiler search order can change the selected literal header but no safe compiler probe proved the dependency",
                    details=ambiguous,
                )
            unsupported = [item for item in unresolved if item.get("reason") == "local_include_suffix_unsupported"]
            if unsupported:
                raise KickstartError(
                    "LOCAL_INCLUDE_SUFFIX_UNSUPPORTED",
                    "a compiler-selected project-local include uses a suffix not representable by the current header authority",
                    details=unsupported,
                )
            raise KickstartError(
                "QUOTED_INCLUDE_UNRESOLVED",
                "one or more quoted project includes could not be resolved inside project_root",
                details=unresolved,
            )
        from .include_edges import authority_payload, collect_include_edges, owners_from_edges
        include_edges, edge_issues = collect_include_edges(project_root, survey)
        if edge_issues:
            raise KickstartError(
                "INCLUDE_EDGE_AUTHORITY_UNVERIFIED",
                "direct compiler include-edge authority is not fully provable",
                details=edge_issues,
            )
        edge_payload = authority_payload(include_edges)
        closure_owners = {relative: sorted(owners) for relative, (_, owners) in headers.items()}
        if owners_from_edges(include_edges) != closure_owners:
            raise KickstartError(
                "INCLUDE_EDGE_CLOSURE_MISMATCH",
                "direct include-edge traversal disagrees with the compiler-backed transitive header closure",
                details={"edges": owners_from_edges(include_edges), "closure": closure_owners},
            )
        updated_at = utc_now()
        blueprint_set = render_blueprints(
            survey,
            project_root,
            headers=headers,
            workspace_id=workspace_id_actual,
            task_id=task_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            updated_at=updated_at,
        )
        documents = render_binding_documents(
            survey=survey,
            project_root=project_root,
            workspace_id=workspace_id_actual,
            task_id=task_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            updated_at=updated_at,
            header_closure={relative: owners for relative, (_, owners) in headers.items()},
            include_edges=include_edges,
            include_topology_sha256=edge_payload["topology_sha256"],
        )
        changed = [*documents.keys(), *(f"code/{record.filename}" for record in blueprint_set.records)]
        # New generated Markdown is still an out-of-band source change from the
        # workspace's perspective. Attribute that exact, pre-edit scope before
        # the heartbeat so governance can consume it and record the mutation.
        issue_external_edit_permit(
            workspace,
            paths=sorted(changed),
            reason="compiler-backed project kickstart generated exact source/header evidence",
            ttl_seconds=900,
        )
        for relative, text in documents.items():
            atomic_write_text(workspace / relative, text)
        external_root = intake_directory / "blueprints"
        external_root.mkdir(parents=True, exist_ok=True)
        for record in blueprint_set.records:
            atomic_write_text(external_root / record.filename, record.text)
            atomic_write_text(workspace / "code" / record.filename, record.text)

        build_authority = intake_directory / "PROJECT_BUILD_AUTHORITY.cmake"
        atomic_write_text(build_authority, _cmake_authority(survey.units))
        include_edge_authority = intake_directory / "include-edges.json"
        atomic_write_json(include_edge_authority, edge_payload)
        header_only = {relative: filename_for(relative, header_only=True) for relative in sorted(headers)}
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

        workshop_config = {
            "schema": "runtime-sync-workshop-config/v1",
            "compiler_context_sha256": compiler_context_sha256(survey, project_root),
            "path_resolution": "config-directory-relative-v1",
            "codebase_root": serialize_path(project_root),
            "runtime_root": serialize_path(project_root),
            "cmake_file": serialize_path(build_authority),
            "cmake_source_sets": ["KAIROS_TRANSLATION_UNITS"],
            "cmake_extra_sources": [],
            "dataflow_index": serialize_path(workspace / "docs" / "PROJECT_SOURCE_INDEX.md"),
            "blueprint_root": serialize_path(external_root),
            "managed_blueprint_root": serialize_path(workspace / "code"),
            "kairos_workspace": serialize_path(workspace),
            "kairos_harness": "@bundled",
            "kairos_database": serialize_path(workspace / ".kairos" / "kairos.db"),
            "include_roots": [serialize_path(root) for root in survey.include_roots],
            "translation_unit_include_roots": {
                unit.relative_path: [
                    [serialize_path(root) for root in sequence]
                    for sequence in unit.include_root_sequences
                ]
                for unit in survey.units
            },
            "translation_unit_include_variants": {
                unit.relative_path: [
                    {
                        "quote_roots": [serialize_path(root) for root in variant.quote_include_roots],
                        "angle_roots": [serialize_path(root) for root in variant.angle_include_roots],
                        "idirafter_roots": [serialize_path(root) for root in variant.idirafter_roots],
                        "compiler_probe": (
                            {
                                "argv": serialize_probe(probe),
                                "directory": serialize_path(variant.directory),
                            }
                            if (probe := compiler_probe_argv(variant.argv, directory=variant.directory, project_root=project_root))
                            else None
                        ),
                    }
                    for variant in unit.variants
                ]
                for unit in survey.units
            },
            "translation_unit_compiler_probes": {
                unit.relative_path: [
                    {
                        "argv": serialize_probe(probe),
                        "directory": serialize_path(variant.directory),
                    }
                    for variant in unit.variants
                    if (probe := compiler_probe_argv(variant.argv, directory=variant.directory, project_root=project_root))
                ]
                for unit in survey.units
            },
            "expected_translation_units": len(survey.units),
            "blueprint_ignore": [],
            "auxiliary_documents": [],
            "machine_authority_files": [
                "workshop.config.json",
                "project-intake/compile_commands.json",
                "project-intake/PROJECT_BUILD_AUTHORITY.cmake",
                "project-intake/include-edges.json",
                "project-intake.json",
            ],
            "header_extensions": sorted(HEADER_SUFFIXES),
            "additional_header_owners": {},
            "header_only_blueprints": header_only,
            "dependent_tests": {},
            "dependent_test_root": "tests",
            "dependent_test_suffixes": sorted({".c", ".cc", ".cpp", ".cxx", ".cu"}),
            "source_authority": {
                "index_section_start": "### 2.3 ",
                "index_section_end": "### 2.7 ",
                "index_membership_token": "yes",
                "index_prefix_reset_marker": "**End of translation-unit membership",
                "translation_unit_extensions": ["c", "cc", "cpp", "cxx", "cu"],
                "include_ownership_section_id": "s-include-closure",
                "include_edges_section_id": "s-include-edges",
                "include_edges_file": "project-intake/include-edges.json",
            },
            "state_directory": ".state",
            "transaction_directory": "transactions",
        }
        manifest = _manifest(
            project_root=project_root,
            workspace=workspace,
            survey=survey,
            blueprint_set=blueprint_set,
            header_closure=headers,
            unresolved_includes=unresolved,
            retained_compile_commands=retained_compile_commands,
            cmake_configure=cmake_configure,
            intake_directory=intake_directory,
            workspace_id=workspace_id_actual,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
            project_intent_sha256=project_intent_sha256,
        )
        manifest["include_edge_topology"] = {
            "edge_count": edge_payload["edge_count"],
            "topology_sha256": edge_payload["topology_sha256"],
        }
        manifest["workshop_config_sha256"] = _sha256(json.dumps(workshop_config, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        atomic_write_json(config_path, workshop_config)
        atomic_write_json(intake_directory.parent / "project-intake.json", manifest)

        heartbeat = run_heartbeat(
            workspace,
            requested_mode="work",
            changed_paths=sorted(changed),
            trigger="project-kickstart",
        )

        # Importing Workshop only after its configuration exists avoids hidden
        # source-prefix state during generation.
        import sys

        workshop_src = _canonical_root() / "workshop" / "src"
        if str(workshop_src) not in sys.path:
            sys.path.insert(0, str(workshop_src))
        from runtime_sync_workshop.corpus import build_corpus_manifest, load_config as load_workshop_config

        corpus = build_corpus_manifest(load_workshop_config(config_path), verify_database=True)
        health = health_audit(workspace, full=True)
        result = {
            "schema": "kairos-project-intake-result/v1",
            "workspace": str(workspace),
            "project_root": str(project_root),
            "translation_units": len(survey.units),
            "include_headers": len(headers),
            "blueprints": len(blueprint_set.records),
            "heartbeat": heartbeat,
            "corpus": corpus,
            "health": health,
            "manifest": str(workspace / ".kairos" / "project-intake.json"),
            "verified": bool(corpus.get("verified")) and health.get("verdict") == "PASS",
        }
        if not result["verified"]:
            raise KickstartError("PROJECT_CORPUS_UNVERIFIED", "project intake completed but corpus or KAIROS health verification failed", details=result)
        return result
    finally:
        if temporary_build and cmake_configure is not None:
            shutil.rmtree(cmake_configure.build_directory, ignore_errors=True)
