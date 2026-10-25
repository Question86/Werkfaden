from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .errors import KickstartError
from .portable import portable_path, reject_link_components
from .survey import survey_compile_commands
from .universal import (
    UniversalSource,
    UniversalSurvey,
    _iter_project_files,
    _sha256,
    _SUFFIX_TO_SPEC,
    render_markdown_corpus,
)
from .universal_workshop import prepare_universal_workshop_binding, verify_universal_workshop_binding


C_FAMILY_TRANSLATION_UNIT_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".cu"})
C_FAMILY_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".cuh"})


def _canonical_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _harness_imports():
    harness = _canonical_root() / "kairos" / "kairos_harness"
    if str(harness) not in sys.path:
        sys.path.insert(0, str(harness))
    from kairos.governance import issue_external_edit_permit
    from kairos.heartbeat import run_heartbeat
    from kairos.loops import validate_project_kickoff_contract
    from kairos.util import atomic_write_json, atomic_write_text, read_json, utc_now
    from kairos.workspace import initialize_project_workspace, load_config
    return (
        issue_external_edit_permit,
        run_heartbeat,
        validate_project_kickoff_contract,
        atomic_write_json,
        atomic_write_text,
        read_json,
        utc_now,
        initialize_project_workspace,
        load_config,
    )


def _safe_workspace_id(value: str) -> str:
    import re
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.:-]{2,127}", value):
        raise KickstartError("WORKSPACE_ID_INVALID", f"workspace_id is not a stable upper-case identifier: {value!r}")
    return value


def _workspace_scope(workspace: Path, load_config, read_json) -> tuple[str, str, str, str]:
    config = load_config(workspace)
    workspace_id = _safe_workspace_id(str(config.get("workspace_id", "")))
    current = read_json(workspace / "current.json", default={})
    if not isinstance(current, dict):
        raise KickstartError("CURRENT_STATE_INVALID", "current.json must be an object")
    goal_id = str(current.get("active_goal", ""))
    milestone_id = str(current.get("active_milestone", ""))
    task_id = str(current.get("active_task", ""))
    if not all((goal_id, milestone_id, task_id)):
        raise KickstartError("ACTIVE_SCOPE_MISSING", "current.json must declare active goal, milestone and task")
    return workspace_id, goal_id, milestone_id, task_id


def _compiler_membership(project_root: Path, compile_commands: Path | None) -> set[str]:
    c_family_present = [
        path.relative_to(project_root).as_posix()
        for path in _iter_project_files(project_root)
        if path.suffix.casefold() in C_FAMILY_TRANSLATION_UNIT_SUFFIXES
    ]
    if not c_family_present:
        return set()
    if compile_commands is None:
        raise KickstartError(
            "C_FAMILY_COMPILER_AUTHORITY_REQUIRED",
            "C/C++/CUDA translation units were detected; --auto requires compiler-produced compile_commands.json for those files",
            details={"detected": sorted(c_family_present)[:100], "count": len(c_family_present)},
        )
    survey = survey_compile_commands(compile_commands, project_root, source_kind="compile_commands")
    membership = {unit.relative_path for unit in survey.units}
    unproven = sorted(set(c_family_present) - membership)
    if unproven:
        raise KickstartError(
            "C_FAMILY_COMPILER_AUTHORITY_INCOMPLETE",
            "project-local C/C++/CUDA translation units exist outside compiler-produced membership",
            details={"unproven": unproven[:100], "count": len(unproven)},
        )
    return membership


def survey_universal_sources(project_root: Path, *, compile_commands: Path | None = None) -> UniversalSurvey:
    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    if compile_commands is not None:
        compile_commands = Path(compile_commands)
        reject_link_components(compile_commands, label="compile_commands")
    root = project_root.resolve()
    if not root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {root}")
    compiler_membership = _compiler_membership(root, compile_commands)
    sources: list[UniversalSource] = []
    markers: dict[str, set[str]] = {}
    for path in _iter_project_files(root):
        suffix = path.suffix.casefold()
        spec = _SUFFIX_TO_SPEC.get(suffix)
        if spec is None:
            continue
        relative = path.relative_to(root).as_posix()
        if spec.name == "c_family":
            if suffix in C_FAMILY_HEADER_SUFFIXES:
                continue
            if relative not in compiler_membership:
                continue
        raw = path.read_bytes()
        sources.append(UniversalSource(
            relative_path=relative,
            absolute_path=path,
            ecosystem=spec.name,
            sha256=_sha256(raw),
            byte_count=len(raw),
        ))
    if not sources:
        raise KickstartError("SOURCE_MEMBERSHIP_EMPTY", "no supported governed source files were detected")

    for path in _iter_project_files(root):
        name = path.name.casefold()
        for spec in {item.name: item for item in _SUFFIX_TO_SPEC.values()}.values():
            if any(name == marker.casefold() for marker in spec.markers if marker != "gemspec"):
                markers.setdefault(spec.name, set()).add(path.relative_to(root).as_posix())
        if name.endswith(".gemspec"):
            markers.setdefault("ruby", set()).add(path.relative_to(root).as_posix())
        if path.suffix.casefold() in {".csproj", ".fsproj", ".vbproj", ".sln"}:
            markers.setdefault("dotnet", set()).add(path.relative_to(root).as_posix())

    from .universal import EXCLUDED_DIRECTORIES
    return UniversalSurvey(
        project_root=root,
        sources=tuple(sorted(sources, key=lambda item: item.relative_path.casefold())),
        ecosystems=tuple(sorted({item.ecosystem for item in sources})),
        markers={key: tuple(sorted(value)) for key, value in markers.items()},
        excluded_directories=tuple(sorted(EXCLUDED_DIRECTORIES)),
    )


def initialize_universal_markdown(
    *,
    project_root: Path,
    workspace: Path,
    compile_commands: Path | None = None,
    workspace_id: str | None = None,
    project_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and Workshop-bind the initial exact Markdown layer.

    Initial derivation is the sole non-Workshop Markdown creation phase. The governed
    project is read-only throughout. After the returned corpus is sealed, all governed
    source/Markdown mutations belong to Workshop transactions.
    """
    (
        issue_external_edit_permit,
        run_heartbeat,
        validate_project_kickoff_contract,
        atomic_write_json,
        atomic_write_text,
        read_json,
        utc_now,
        initialize_project_workspace,
        load_config,
    ) = _harness_imports()

    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    project_root = project_root.resolve()
    workspace = Path(workspace)
    reject_link_components(workspace, label="KAIROS workspace")
    workspace = workspace.resolve()
    if not project_root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {project_root}")
    if (workspace / ".kairos" / "universal-intake.json").exists():
        raise KickstartError("PROJECT_ALREADY_BOUND", "workspace already contains universal intake provenance")

    if not (workspace / ".kairos" / "config.json").is_file():
        if workspace.exists() and any(workspace.iterdir()):
            raise KickstartError("WORKSPACE_NOT_FRESH", f"workspace is not a KAIROS workspace and is not empty: {workspace}")
        if project_spec is None:
            raise KickstartError("PROJECT_INTENT_REQUIRED", "fresh universal intake requires a validated kairos-project-kickoff/v1 contract")
        try:
            goal, milestone, task, selected_criteria, _ = validate_project_kickoff_contract(project_spec)
        except Exception as exc:
            raise KickstartError("PROJECT_INTENT_INVALID", str(exc)) from exc
        derived_id = workspace_id or f"KAIROS_PROJECT_{hashlib.sha256(project_root.as_posix().encode('utf-8')).hexdigest()[:12].upper()}"
        initialize_project_workspace(
            workspace,
            workspace_id=_safe_workspace_id(derived_id),
            goal=goal,
            milestone=milestone,
            task=task,
            selected_criteria=selected_criteria,
        )

    workspace_id_actual, goal_id, milestone_id, task_id = _workspace_scope(workspace, load_config, read_json)
    if workspace_id is not None and _safe_workspace_id(workspace_id) != workspace_id_actual:
        raise KickstartError("WORKSPACE_ID_MISMATCH", f"workspace already declares {workspace_id_actual!r}, not {workspace_id!r}")

    survey = survey_universal_sources(project_root, compile_commands=compile_commands)
    updated_at = utc_now()
    base_documents = render_markdown_corpus(
        survey,
        workspace_id=workspace_id_actual,
        goal_id=goal_id,
        milestone_id=milestone_id,
        task_id=task_id,
        updated_at=updated_at,
    )
    binding = prepare_universal_workshop_binding(
        project_root=project_root,
        workspace=workspace,
        survey=survey,
        documents=base_documents,
        compile_commands=compile_commands,
        workspace_id=workspace_id_actual,
        goal_id=goal_id,
        milestone_id=milestone_id,
        task_id=task_id,
        updated_at=updated_at,
    )
    documents = binding.documents
    machine_root = binding.config_path.parent.resolve()
    changed = sorted(documents)
    issue_external_edit_permit(
        workspace,
        paths=changed,
        reason="initial deterministic universal intake generated exact Markdown authority from immutable project observation",
        ttl_seconds=900,
    )
    for relative, text in documents.items():
        atomic_write_text(workspace / relative, text)

    intake = {
        "schema": "kairos-universal-intake/v1",
        "snapshot_role": "initial_intake_provenance",
        "created_at": updated_at,
        "path_resolution": "config-directory-relative-v1",
        "project_root": portable_path(project_root, base=machine_root),
        "workspace": portable_path(workspace, base=machine_root),
        "workspace_id": workspace_id_actual,
        "ecosystems": list(survey.ecosystems),
        "source_count": len(survey.sources),
        "header_count": binding.header_count,
        "sources": [
            {
                "path": source.relative_path,
                "ecosystem": source.ecosystem,
                "sha256": source.sha256,
                "bytes": source.byte_count,
                "authority": "compiler_backed" if source.ecosystem == "c_family" else "static_source_membership",
            }
            for source in survey.sources
        ],
        "markers": {key: list(value) for key, value in survey.markers.items()},
        "claim_boundary": (
            "This immutable receipt describes the initial observed source snapshot. It is provenance, not a claim that later Workshop source bytes remain equal to intake bytes. "
            "Static ecosystems do not gain invented dynamic-import/build semantics; C-family translation-unit/header membership remains compiler-backed."
        ),
        "mutation_boundary": (
            "project_root was read-only during initial derivation. After seal, governed source, build files and Markdown may advance only through verified Workshop transactions."
        ),
    }
    atomic_write_json(workspace / ".kairos" / "universal-intake.json", intake)

    heartbeat = run_heartbeat(
        workspace,
        requested_mode="work",
        changed_paths=changed,
        trigger="universal-project-kickstart",
    )
    if not heartbeat.get("verified"):
        raise KickstartError("UNIVERSAL_INTAKE_HEARTBEAT_UNVERIFIED", "initial universal Markdown promotion did not verify", details=heartbeat)

    corpus = verify_universal_workshop_binding(binding.config_path)
    if not corpus.get("verified"):
        raise KickstartError(
            "UNIVERSAL_WORKSHOP_BINDING_UNVERIFIED",
            "universal intake materialized but Workshop corpus parity failed",
            details=corpus.get("issues", []),
        )
    return {
        "schema": "kairos-universal-intake-result/v1",
        "verified": True,
        "workspace": workspace.as_posix(),
        "project_root": project_root.as_posix(),
        "ecosystems": list(survey.ecosystems),
        "sources": len(survey.sources),
        "headers": binding.header_count,
        "markdown_documents": len(documents),
        "intake_provenance": str(workspace / ".kairos" / "universal-intake.json"),
        "workshop_config": str(binding.config_path),
        "heartbeat": heartbeat,
        "corpus": corpus,
        "state": "VERIFIED_PENDING_SEAL",
    }
