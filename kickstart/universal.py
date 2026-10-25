from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import KickstartError


@dataclass(frozen=True)
class EcosystemSpec:
    name: str
    suffixes: frozenset[str]
    markers: tuple[str, ...]
    authority: str = "static_source_membership"


@dataclass(frozen=True)
class UniversalSource:
    relative_path: str
    absolute_path: Path
    ecosystem: str
    sha256: str
    byte_count: int


@dataclass(frozen=True)
class UniversalSurvey:
    project_root: Path
    sources: tuple[UniversalSource, ...]
    ecosystems: tuple[str, ...]
    markers: dict[str, tuple[str, ...]]
    excluded_directories: tuple[str, ...]


ECOSYSTEMS: tuple[EcosystemSpec, ...] = (
    EcosystemSpec("python", frozenset({".py", ".pyi"}), ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")),
    EcosystemSpec("javascript_typescript", frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}), ("package.json", "tsconfig.json", "jsconfig.json")),
    EcosystemSpec("rust", frozenset({".rs"}), ("Cargo.toml", "Cargo.lock")),
    EcosystemSpec("go", frozenset({".go"}), ("go.mod", "go.work", "go.sum")),
    EcosystemSpec("jvm", frozenset({".java", ".kt", ".kts", ".groovy"}), ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")),
    EcosystemSpec("dotnet", frozenset({".cs", ".fs", ".vb"}), ("global.json", "Directory.Build.props", "Directory.Build.targets")),
    EcosystemSpec("ruby", frozenset({".rb", ".rake"}), ("Gemfile", "Rakefile", "gemspec")),
    EcosystemSpec("php", frozenset({".php", ".phtml"}), ("composer.json", "composer.lock")),
    EcosystemSpec("c_family", frozenset({".c", ".cc", ".cpp", ".cxx", ".cu", ".h", ".hh", ".hpp", ".hxx", ".cuh"}), ("CMakeLists.txt", "compile_commands.json", "meson.build", "BUILD", "BUILD.bazel"), "compiler_backed"),
)

EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", ".kairos",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".pnpm-store", ".yarn", "bower_components",
    "target", "build", "dist", "out", "bin", "obj", ".gradle", ".mvn",
    "vendor", "coverage", ".coverage", ".next", ".nuxt", ".cache",
})

_SUFFIX_TO_SPEC = {
    suffix: spec
    for spec in ECOSYSTEMS
    for suffix in spec.suffixes
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {root}")
    return root


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _iter_project_files(
    project_root: Path,
    *,
    excluded_roots: Iterable[Path] = (),
) -> Iterable[Path]:
    """Yield regular project files while pruning generated/tool-owned roots.

    ``excluded_roots`` is path-based rather than name-based. This matters when a KAIROS
    workspace lives below the governed project: Workshop transaction state may be named
    anything, and a source-set checkout must never walk into its own candidate tree.
    """
    root = _safe_root(project_root)
    dynamic_exclusions: list[Path] = []
    for value in excluded_roots:
        candidate = Path(value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate == root:
            raise KickstartError(
                "PROJECT_EXCLUSION_INVALID",
                "an excluded tool-owned root may not equal project_root",
            )
        if candidate not in dynamic_exclusions:
            dynamic_exclusions.append(candidate)

    def is_excluded(path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == boundary or boundary in resolved.parents for boundary in dynamic_exclusions)

    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        retained: list[str] = []
        for name in directory_names:
            candidate = current_path / name
            if name in EXCLUDED_DIRECTORIES or _is_link(candidate) or is_excluded(candidate):
                continue
            retained.append(name)
        directory_names[:] = retained
        for name in sorted(file_names):
            candidate = current_path / name
            if is_excluded(candidate) or _is_link(candidate):
                continue
            try:
                info = candidate.stat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            try:
                candidate.resolve().relative_to(root)
            except ValueError:
                continue
            yield candidate


def detect_project(project_root: Path) -> dict[str, Any]:
    root = _safe_root(project_root)
    marker_hits: dict[str, set[str]] = {spec.name: set() for spec in ECOSYSTEMS}
    suffix_counts: dict[str, int] = {spec.name: 0 for spec in ECOSYSTEMS}

    marker_index = {
        marker.casefold(): spec.name
        for spec in ECOSYSTEMS
        for marker in spec.markers
        if marker != "gemspec"
    }
    for path in _iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        spec = _SUFFIX_TO_SPEC.get(suffix)
        if spec is not None:
            suffix_counts[spec.name] += 1
        name = path.name.casefold()
        marker_ecosystem = marker_index.get(name)
        if marker_ecosystem is not None:
            marker_hits[marker_ecosystem].add(relative)
        if name.endswith(".gemspec"):
            marker_hits["ruby"].add(relative)
        if suffix in {".csproj", ".fsproj", ".vbproj", ".sln"}:
            marker_hits["dotnet"].add(relative)

    detected = []
    for spec in ECOSYSTEMS:
        count = suffix_counts[spec.name]
        hits = sorted(marker_hits[spec.name])
        if count or hits:
            detected.append({
                "ecosystem": spec.name,
                "source_files": count,
                "markers": hits,
                "authority": spec.authority,
            })
    return {
        "schema": "kairos-project-detection/v1",
        "project_root": root.as_posix(),
        "ecosystems": detected,
        "mixed": len(detected) > 1,
        "requires_compiler_authority": any(row["ecosystem"] == "c_family" for row in detected),
        "excluded_directories": sorted(EXCLUDED_DIRECTORIES),
    }


def survey_static_sources(
    project_root: Path,
    *,
    compiler_membership: Iterable[str] = (),
) -> UniversalSurvey:
    """Build exact initial source membership without inferring dynamic/build semantics.

    Non-C-family ecosystems are admitted by bounded file membership plus exact hashes.
    C/C++/CUDA paths are admitted only when explicitly present in compiler_membership.
    This function never writes to project_root.
    """
    root = _safe_root(project_root)
    compiler_set = {str(value).replace("\\", "/") for value in compiler_membership}
    sources: list[UniversalSource] = []
    marker_hits: dict[str, set[str]] = {spec.name: set() for spec in ECOSYSTEMS}
    c_family_seen: list[str] = []

    marker_index = {
        marker.casefold(): spec.name
        for spec in ECOSYSTEMS
        for marker in spec.markers
        if marker != "gemspec"
    }
    for path in _iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        name = path.name.casefold()
        marker_ecosystem = marker_index.get(name)
        if marker_ecosystem is not None:
            marker_hits[marker_ecosystem].add(relative)
        if name.endswith(".gemspec"):
            marker_hits["ruby"].add(relative)
        if suffix in {".csproj", ".fsproj", ".vbproj", ".sln"}:
            marker_hits["dotnet"].add(relative)

        spec = _SUFFIX_TO_SPEC.get(suffix)
        if spec is None:
            continue
        if spec.name == "c_family":
            c_family_seen.append(relative)
            if relative not in compiler_set:
                continue
        raw = path.read_bytes()
        sources.append(UniversalSource(
            relative_path=relative,
            absolute_path=path,
            ecosystem=spec.name,
            sha256=_sha256(raw),
            byte_count=len(raw),
        ))

    unproven_c_family = sorted(set(c_family_seen) - compiler_set)
    if unproven_c_family:
        raise KickstartError(
            "C_FAMILY_COMPILER_AUTHORITY_REQUIRED",
            "C/C++/CUDA source membership is present but not proven by compiler authority",
            details={"unproven_paths": unproven_c_family[:100], "count": len(unproven_c_family)},
        )
    if not sources:
        raise KickstartError("SOURCE_MEMBERSHIP_EMPTY", "no supported governed source files were detected")

    ecosystem_names = tuple(sorted({source.ecosystem for source in sources}))
    return UniversalSurvey(
        project_root=root,
        sources=tuple(sorted(sources, key=lambda item: item.relative_path.casefold())),
        ecosystems=ecosystem_names,
        markers={name: tuple(sorted(values)) for name, values in marker_hits.items() if values},
        excluded_directories=tuple(sorted(EXCLUDED_DIRECTORIES)),
    )


def _ledger_rows(lines: list[str]) -> str:
    if not lines:
        return ""
    width = max(4, len(str(len(lines))))
    return "\n".join(f"C:{index:0{width}d} {line}" for index, line in enumerate(lines, 1))


def _load_renderer():
    root = Path(__file__).resolve().parents[1]
    harness = root / "kairos" / "kairos_harness"
    if str(harness) not in sys.path:
        sys.path.insert(0, str(harness))
    from kairos.templates import render_document
    return render_document


def artifact_id_for(relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12].upper()
    return f"PROJECT_CODE_{digest}"


def filename_for(relative_path: str) -> str:
    return artifact_id_for(relative_path) + ".md"


def render_source_blueprint(
    source: UniversalSource,
    *,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    updated_at: str,
) -> str:
    render_document = _load_renderer()
    raw = source.absolute_path.read_bytes()
    if _sha256(raw) != source.sha256:
        raise KickstartError("SOURCE_CHANGED_DURING_INTAKE", f"source changed while intake was rendering: {source.relative_path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KickstartError("SOURCE_NON_UTF8", f"source is not UTF-8 and cannot be mirrored safely: {source.relative_path}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    eof_newline = bool(normalized.endswith("\n"))
    if eof_newline:
        lines = lines[:-1]
    artifact_id = artifact_id_for(source.relative_path)
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
        "capsule": f"Exact initial source mirror for {source.relative_path} ({source.ecosystem}).",
        "claim_boundary": (
            "This deterministic mirror proves source membership, exact bytes, hashes and the source ledger only. "
            "It does not infer imports, runtime reachability, build participation or semantic behavior."
        ),
        "entities": [artifact_id, source.relative_path, source.ecosystem, "KAIROS"],
        "facets": ["project-initiation", "universal-intake", source.ecosystem, "exact-ledger"],
        "criteria": [],
        "does_not_answer": ["runtime execution result", "dynamic import resolution", "semantic intent not present in source"],
        "answers": [
            {"intent": "implementation_location", "question": f"Which exact source bytes are bound to {source.relative_path}?", "target": "s-mapping", "language": "en"},
            {"intent": "evidence", "question": f"Where is the verbatim source ledger for {source.relative_path}?", "target": "s-source-ledger", "language": "en"},
        ],
        "refs": {},
        "search_contract": [
            {"query": f"Which exact source bytes are bound to {source.relative_path}?", "expected": f"{artifact_id}#s-mapping", "required_top_k": 5},
        ],
    }
    mapping = "\n".join((
        f'target_source_file = {json.dumps(source.relative_path, ensure_ascii=False)}',
        'target_header_file = "none"',
        f'source_line_count = {len(lines)}',
        f'source_byte_count = {len(raw)}',
        f'source_sha256 = "{source.sha256.upper()}"',
        f'source_eof_newline = {str(eof_newline).lower()}',
        f'ecosystem = "{source.ecosystem}"',
        f'generated_at = {json.dumps(updated_at)}',
    ))
    source_authority = "compiler_backed" if source.ecosystem == "c_family" else "static_source_membership"
    sections = [
        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact initial source mirror.", "content": f"The governed source file is `{source.relative_path}`."},
        {"id": "s-mapping", "title": "SOURCE MAPPING", "capsule": "Path, ecosystem and exact byte facts are bound without semantic inference.", "content": mapping},
        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Initial membership is explicit and ecosystem-bounded; build/runtime semantics are not invented.", "content": f"- Ecosystem: `{source.ecosystem}`\n- Authority: `{source_authority}`\n- SHA-256: `{source.sha256.upper()}`"},
        {"id": "s-source-ledger", "title": "SOURCE LEDGER", "capsule": "Verbatim numbered source evidence.", "content": "### HEADER 0 LINES\n~~~text\n~~~\n\n### SOURCE " + str(len(lines)) + " LINES\n~~~text\n" + _ledger_rows(lines) + "\n~~~\n\nEND OF BLUEPRINT"},
    ]
    return render_document(metadata, f"{artifact_id}: {source.relative_path}", sections)


def render_source_index(
    survey: UniversalSurvey,
    *,
    workspace_id: str,
    updated_at: str,
) -> str:
    render_document = _load_renderer()
    rows = "\n".join(
        f"| `{source.relative_path}` | `{source.ecosystem}` | `{source.sha256.upper()}` |"
        for source in survey.sources
    )
    ecosystems = ", ".join(f"`{value}`" for value in survey.ecosystems)
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
        "capsule": "Exact universal-intake source membership, ecosystem classification and source hashes.",
        "claim_boundary": "This index proves observed initial file membership and hashes only; imports, build graph and runtime reachability require stronger ecosystem-specific evidence.",
        "entities": ["PROJECT_SOURCE_INDEX", *survey.ecosystems, "KAIROS"],
        "facets": ["ground-truth", "universal-intake", "source-membership", "provenance"],
        "criteria": [],
        "does_not_answer": ["dynamic imports", "runtime reachability", "successful build"],
        "answers": [{"intent": "membership", "question": "Which source files were admitted by universal intake?", "target": "s-project-membership", "language": "en"}],
        "refs": {},
        "search_contract": [{"query": "Which source files were admitted by universal intake?", "expected": "PROJECT_SOURCE_INDEX#s-project-membership", "required_top_k": 1}],
    }
    return render_document(metadata, "PROJECT SOURCE INDEX", [
        {"id": "s-authority", "title": "SOURCE AUTHORITY", "capsule": "Universal intake admits exact files under explicit ecosystem-specific claim boundaries.", "content": f"Detected ecosystems: {ecosystems}.\n\nExcluded generated/vendor directories: " + ", ".join(f"`{value}`" for value in survey.excluded_directories)},
        {"id": "s-project-membership", "title": "PROJECT MEMBERSHIP", "capsule": "Every admitted file is listed with ecosystem and exact SHA-256.", "content": "| Source | Ecosystem | SHA-256 |\n|---|---|---|\n" + rows},
        {"id": "s-boundary", "title": "CLAIM BOUNDARY", "capsule": "Static ecosystems are not promoted into invented dependency or build claims.", "content": "Python/JS/TS/Rust/Go/JVM/.NET/Ruby/PHP membership is derived from bounded project-local source files and ecosystem markers. C/C++/CUDA is excluded unless compiler-backed membership explicitly admits it."},
    ])


def render_markdown_corpus(
    survey: UniversalSurvey,
    *,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    updated_at: str,
) -> dict[str, str]:
    documents = {
        f"code/{filename_for(source.relative_path)}": render_source_blueprint(
            source,
            workspace_id=workspace_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
            updated_at=updated_at,
        )
        for source in survey.sources
    }
    documents["docs/PROJECT_SOURCE_INDEX.md"] = render_source_index(
        survey,
        workspace_id=workspace_id,
        updated_at=updated_at,
    )
    return dict(sorted(documents.items()))
