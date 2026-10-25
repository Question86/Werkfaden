from __future__ import annotations

import json
import ntpath
import re
import stat
import sqlite3
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from .blueprint import BlueprintDocument, ledger_lines, parse_blueprint, verify_ledger
from .util import (
    WorkshopError,
    file_facts,
    logical_lines,
    normalized_sha256,
    normalized_text,
    package_hash,
    read_json,
    reject_link_components,
    relative_posix,
    sha256_bytes,
    sha256_text,
    source_prefix,
    utc_now,
)


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".cu"}
# Both quoted and angle-bracket includes are inspected.  Resolution is still
# constrained to configured project roots, so a system header is ignored unless
# the project actually provides a file with that include spelling.
INCLUDE_RE = re.compile(r'(?m)^\s*#\s*include\s*[<"]([^">]+)[">]')
SECTION_RE = re.compile(
    r'(?m)^<a id="(?P<id>s-[a-z0-9][a-z0-9-]{1,62})"></a>\s*\n##\s+(?P<title>[^\n]+)\s*$'
)


GRAPH_PROJECTIONS: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("relations", "graph_relations", (
        ("subject", ("subject",)), ("predicate", ("predicate",)), ("object", ("object",)),
        ("object_kind", ("object_kind",)), ("scope", ("scope",)),
        ("evidence_target", ("evidence_target",)), ("evidence", ("evidence",)),
    )),
    ("contracts", "graph_contracts", (
        ("contract_id", ("id",)), ("kind", ("kind",)), ("subject", ("subject",)),
        ("statement", ("statement",)), ("failure_or_effect", ("failure_or_effect",)),
        ("evidence_target", ("evidence_target",)),
    )),
    ("artifacts", "graph_artifacts", (
        ("asset_id", ("id",)), ("role", ("role",)), ("operation", ("operation",)),
        ("producer_or_consumer", ("producer_or_consumer",)),
        ("schema_or_type", ("schema_or_type",)), ("hash_bound", ("hash_bound",)),
        ("commit_bound", ("commit_bound",)), ("evidence_target", ("evidence_target",)),
    )),
    ("drift_records", "graph_drift", (
        ("drift_id", ("id",)), ("historical", ("historical",)),
        ("subject", ("subject",)), ("classification", ("classification",)),
        ("summary", ("summary",)), ("status", ("status",)),
        ("evidence_target", ("current_evidence_target", "evidence_target")),
    )),
)


@dataclass(frozen=True)
class WorkshopConfig:
    config_path: Path
    machine_root: Path
    codebase_root: Path
    runtime_root: Path
    cmake_file: Path
    cmake_source_sets: tuple[str, ...]
    cmake_extra_sources: tuple[str, ...]
    dataflow_index: Path
    blueprint_root: Path
    managed_blueprint_root: Path
    kairos_workspace: Path
    kairos_harness: Path
    kairos_database: Path
    include_roots: tuple[Path, ...]
    expected_translation_units: int
    blueprint_ignore: frozenset[str]
    auxiliary_documents: tuple[str, ...]
    machine_authority_files: tuple[str, ...]
    header_extensions: frozenset[str]
    additional_header_owners: dict[str, str]
    header_only_blueprints: dict[str, str]
    state_directory: Path
    transaction_directory: Path
    raw: dict[str, Any]


@dataclass(frozen=True)
class CorpusEntry:
    source_relative: str | None
    source_path: Path | None
    header_relative: str | None
    header_path: Path | None
    blueprint_path: Path
    managed_path: Path
    document: BlueprintDocument

    @property
    def record_key(self) -> str:
        value = self.source_relative or self.header_relative
        if not value:
            raise WorkshopError(
                "BLUEPRINT_MAPPING_EMPTY",
                f"entry has neither source nor header mapping: {self.blueprint_path}",
            )
        return value

    @property
    def kind(self) -> str:
        return "translation_unit" if self.source_relative else "header_only"


def dependent_test_inventory(config: WorkshopConfig) -> dict[str, Any]:
    """Validate explicitly configured test companions without project-specific exceptions."""
    mapping = config.raw.get("dependent_tests", {})
    if not isinstance(mapping, dict):
        raise WorkshopError("TEST_AUTHORITY_INVALID", "dependent_tests must map exact test paths to source owners")
    test_root = str(config.raw.get("dependent_test_root", "tests")).replace("\\", "/").strip("/")
    if not test_root or Path(test_root).is_absolute() or ".." in Path(test_root).parts:
        raise WorkshopError("TEST_AUTHORITY_INVALID", "dependent_test_root must be a safe relative directory")
    configured_suffixes = config.raw.get("dependent_test_suffixes", sorted(SOURCE_SUFFIXES))
    if not isinstance(configured_suffixes, list) or any(
        not isinstance(value, str) or not value.startswith(".") for value in configured_suffixes
    ):
        raise WorkshopError("TEST_AUTHORITY_INVALID", "dependent_test_suffixes must contain dotted suffixes")
    test_suffixes = {str(value).lower() for value in configured_suffixes}

    def validate_file(relative: str, *, label: str) -> tuple[Path, stat.stat_result]:
        normalized = relative.replace("\\", "/")
        if (
            not normalized
            or Path(normalized).is_absolute()
            or ".." in Path(normalized).parts
        ):
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"{label} must be a safe relative path: {relative}")
        current = config.codebase_root
        info: stat.stat_result | None = None
        for part in Path(normalized).parts:
            current = current / part
            try:
                info = current.lstat()
            except OSError as exc:
                raise WorkshopError("TEST_AUTHORITY_INVALID", f"missing {label} component: {current}") from exc
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise WorkshopError("TEST_AUTHORITY_INVALID", f"linked {label} component: {current}")
            if current != config.codebase_root / normalized and not stat.S_ISDIR(info.st_mode):
                raise WorkshopError("TEST_AUTHORITY_INVALID", f"non-directory {label} component: {current}")
        if info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"{label} is not a single-link regular file: {current}")
        try:
            current.resolve().relative_to(config.codebase_root.resolve())
        except ValueError as exc:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"{label} escapes the configured codebase: {relative}") from exc
        return current, info

    result: dict[str, Any] = {}
    for relative, owner in sorted(mapping.items()):
        if not isinstance(relative, str) or not isinstance(owner, str):
            raise WorkshopError("TEST_AUTHORITY_INVALID", "invalid dependent test path or source owner")
        relative = relative.replace("\\", "/")
        owner = owner.replace("\\", "/")
        if not relative.startswith(test_root + "/") or Path(relative).suffix.lower() not in test_suffixes:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"dependent test is outside the configured test authority: {relative}")
        test_path, _ = validate_file(relative, label="dependent test")
        owner_path, _ = validate_file(owner, label="source owner")
        try:
            owner_path.resolve().relative_to(config.runtime_root.resolve())
        except ValueError as exc:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"source owner is outside the governed source root: {owner}") from exc
        if source_prefix() and not owner.startswith(source_prefix()):
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"source owner does not use the governed source prefix: {owner}")
        if Path(owner).suffix.lower() not in SOURCE_SUFFIXES:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"source owner is not a translation unit: {owner}")
        result[relative] = {"owner": owner, **file_facts(test_path)}
    return result


def _path_is_absolute(value: str) -> bool:
    """Recognize both the host platform's and Windows' absolute syntax."""

    return Path(value).is_absolute() or ntpath.isabs(value)


def _path_is_drive_relative(value: str) -> bool:
    """Reject ``C:relative`` paths, whose meaning depends on process state."""

    drive, _ = ntpath.splitdrive(value)
    return bool(drive) and not _path_is_absolute(value)


def _configured_path(value: Any, name: str, *, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkshopError("CONFIG_INVALID", f"{name} must be a non-empty absolute or config-relative path")
    if _path_is_drive_relative(value):
        raise WorkshopError("CONFIG_INVALID", f"{name} must not use a drive-relative path: {value}")
    raw = Path(value)
    candidate = raw if _path_is_absolute(value) else base / raw
    reject_link_components(candidate, label=name)
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise WorkshopError("PATH_UNVERIFIABLE", f"cannot resolve {name}: {value}") from exc
    reject_link_components(resolved, label=name)
    return resolved


def _bundled_harness_path() -> Path:
    return Path(__file__).resolve().parents[3] / "kairos" / "kairos_harness"


def _installed_harness_path() -> Path | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec("kairos")
    except (ImportError, ModuleNotFoundError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    package = Path(next(iter(spec.submodule_search_locations))).resolve()
    return package.parent


def _harness_path(value: Any, *, base: Path) -> Path:
    if value == "@bundled":
        candidate = _bundled_harness_path()
    elif value == "@installed":
        candidate = _installed_harness_path()
        if candidate is None:
            raise WorkshopError(
                "CONFIG_PATH_MISSING",
                "kairos_harness=@installed but the kairos harness package is not installed",
            )
    else:
        return _configured_path(value, "kairos_harness", base=base)
    reject_link_components(candidate, label="kairos_harness")
    return candidate.resolve()


def _machine_relative_path(value: Any, name: str, *, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkshopError("CONFIG_INVALID", f"{name} must be a non-empty machine-root-relative path")
    normalized = value.replace("\\", "/")
    parts = Path(normalized).parts
    if _path_is_absolute(value) or _path_is_drive_relative(value) or ".." in parts:
        raise WorkshopError("CONFIG_INVALID", f"{name} must stay below the config directory: {value}")
    candidate = _configured_path(normalized, name, base=base)
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise WorkshopError("CONFIG_INVALID", f"{name} escapes the config directory: {value}") from exc
    return candidate


def _config_value_path(config: WorkshopConfig, value: Any, name: str) -> Path:
    # A few pure include-authority callers use a lightweight namespace instead
    # of a fully loaded WorkshopConfig.  Their codebase root is the only safe
    # available base; real configs always provide machine_root.
    base_value = getattr(config, "machine_root", None)
    if base_value is None:
        base_value = config.codebase_root
    base = Path(base_value).resolve()
    return _configured_path(value, name, base=base)


def _safe_authority_relative(config: WorkshopConfig, value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkshopError("AUTHORITY_PATH_UNSAFE", f"{label} must be a non-empty project-relative path")
    normalized = value.replace("\\", "/")
    parts = Path(normalized).parts
    if (
        _path_is_absolute(normalized)
        or _path_is_drive_relative(normalized)
        or ".." in parts
    ):
        raise WorkshopError("AUTHORITY_PATH_UNSAFE", f"{label} escapes the governed project: {value}")
    codebase_value = getattr(config, "codebase_root", None)
    runtime_value = getattr(config, "runtime_root", codebase_value)
    if codebase_value is None or runtime_value is None:
        # Lightweight authority-only test/caller namespaces predate the full
        # WorkshopConfig. They can still receive syntax checks, while a real
        # loaded config always supplies both containment roots below.
        return normalized
    candidate = Path(codebase_value) / Path(normalized)
    reject_link_components(candidate, label=label)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(Path(runtime_value))
    except (OSError, ValueError) as exc:
        raise WorkshopError("AUTHORITY_PATH_UNSAFE", f"{label} is outside the governed runtime: {value}") from exc
    return normalized


def load_config(path: Path) -> WorkshopConfig:
    path = Path(path)
    reject_link_components(path, label="Workshop config")
    try:
        path = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise WorkshopError("CONFIG_PATH_MISSING", f"Workshop config is not readable: {path}") from exc
    raw = read_json(path)
    if not isinstance(raw, dict) or raw.get("schema") != "runtime-sync-workshop-config/v1":
        raise WorkshopError("CONFIG_INVALID", f"unsupported workshop config: {path}")
    machine_root = path.parent.resolve()
    path_resolution = raw.get("path_resolution")
    if path_resolution not in (None, "config-directory-relative-v1"):
        raise WorkshopError("CONFIG_INVALID", f"unsupported path resolution contract: {path_resolution}")
    codebase_root = _configured_path(raw.get("codebase_root"), "codebase_root", base=machine_root)
    runtime_root = _configured_path(raw.get("runtime_root"), "runtime_root", base=machine_root)
    from .util import set_source_prefix

    try:
        source_relative = runtime_root.relative_to(codebase_root).as_posix()
    except ValueError as exc:
        raise WorkshopError("CONFIG_INVALID", "runtime_root must be inside codebase_root") from exc
    set_source_prefix(source_relative)
    state_directory = _machine_relative_path(
        raw.get("state_directory", ".state"), "state_directory", base=machine_root
    )
    transaction_directory = _machine_relative_path(
        raw.get("transaction_directory", "transactions"), "transaction_directory", base=machine_root
    )
    additional = raw.get("additional_header_owners", {})
    if not isinstance(additional, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in additional.items()):
        raise WorkshopError("CONFIG_INVALID", "additional_header_owners must map header paths to blueprint filenames")
    header_only = raw.get("header_only_blueprints", {})
    if not isinstance(header_only, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in header_only.items()
    ):
        raise WorkshopError(
            "CONFIG_INVALID",
            "header_only_blueprints must map exact header paths to blueprint filenames",
        )
    auxiliary = raw.get("auxiliary_documents", [])
    if not isinstance(auxiliary, list) or any(
        not isinstance(value, str)
        or not value
        or Path(value).name != value
        for value in auxiliary
    ):
        raise WorkshopError(
            "CONFIG_INVALID",
            "auxiliary_documents must be an array of exact blueprint-root filenames",
        )
    machine_authorities = raw.get("machine_authority_files", [path.name])
    if not isinstance(machine_authorities, list) or any(
        not isinstance(value, str)
        or not value.strip()
        or _path_is_absolute(value)
        or _path_is_drive_relative(value)
        or ".." in Path(value.replace("\\", "/")).parts
        for value in machine_authorities
    ):
        raise WorkshopError(
            "CONFIG_INVALID",
            "machine_authority_files must be an array of non-empty machine-root-relative file paths",
        )
    normalized_machine_authorities = tuple(
        sorted({str(value).replace("\\", "/") for value in machine_authorities})
    )
    config_relative = path.relative_to(machine_root).as_posix()
    if config_relative not in normalized_machine_authorities:
        raise WorkshopError(
            "CONFIG_INVALID",
            "machine_authority_files must include the Workshop config itself",
        )
    config = WorkshopConfig(
        config_path=path,
        machine_root=machine_root,
        codebase_root=codebase_root,
        runtime_root=runtime_root,
        cmake_file=_configured_path(raw.get("cmake_file"), "cmake_file", base=machine_root),
        cmake_source_sets=tuple(str(value) for value in raw.get("cmake_source_sets", [])),
        cmake_extra_sources=tuple(str(value).replace("\\", "/") for value in raw.get("cmake_extra_sources", [])),
        dataflow_index=_configured_path(raw.get("dataflow_index"), "dataflow_index", base=machine_root),
        blueprint_root=_configured_path(raw.get("blueprint_root"), "blueprint_root", base=machine_root),
        managed_blueprint_root=_configured_path(raw.get("managed_blueprint_root"), "managed_blueprint_root", base=machine_root),
        kairos_workspace=_configured_path(raw.get("kairos_workspace"), "kairos_workspace", base=machine_root),
        kairos_harness=_harness_path(raw.get("kairos_harness"), base=machine_root),
        kairos_database=_configured_path(raw.get("kairos_database"), "kairos_database", base=machine_root),
        include_roots=(),
        expected_translation_units=int(raw.get("expected_translation_units", 0)),
        blueprint_ignore=frozenset(str(value) for value in raw.get("blueprint_ignore", [])),
        auxiliary_documents=tuple(sorted(set(auxiliary))),
        machine_authority_files=normalized_machine_authorities,
        header_extensions=frozenset(str(value).lower() for value in raw.get("header_extensions", [])),
        additional_header_owners={str(k).replace("\\", "/"): str(v) for k, v in additional.items()},
        header_only_blueprints={
            str(k).replace("\\", "/"): str(v)
            for k, v in header_only.items()
        },
        state_directory=state_directory,
        transaction_directory=transaction_directory,
        raw=raw,
    )
    configured_include_roots = raw.get("include_roots", [])
    if not isinstance(configured_include_roots, list) or any(
        not isinstance(value, str) or not value.strip()
        for value in configured_include_roots
    ):
        raise WorkshopError("CONFIG_INVALID", "include_roots must be an array of config-relative or absolute directories")
    include_roots: list[Path] = []
    for value in configured_include_roots:
        include_root = _configured_path(value, "include_roots entry", base=machine_root)
        try:
            include_root.relative_to(config.codebase_root)
        except ValueError as exc:
            raise WorkshopError(
                "CONFIG_INVALID",
                f"include root must be inside codebase_root: {include_root}",
            ) from exc
        if not include_root.is_dir():
            raise WorkshopError("CONFIG_PATH_MISSING", f"configured include root is not a dir: {include_root}")
        include_roots.append(include_root)
    config = replace(config, include_roots=tuple(sorted(set(include_roots), key=lambda value: value.as_posix().lower())))
    for name, target, expected_kind in (
        ("codebase_root", config.codebase_root, "dir"),
        ("runtime_root", config.runtime_root, "dir"),
        ("cmake_file", config.cmake_file, "file"),
        ("dataflow_index", config.dataflow_index, "file"),
        ("blueprint_root", config.blueprint_root, "dir"),
        ("managed_blueprint_root", config.managed_blueprint_root, "dir"),
        ("kairos_workspace", config.kairos_workspace, "dir"),
        ("kairos_harness", config.kairos_harness, "dir"),
        ("kairos_database", config.kairos_database, "file"),
    ):
        valid = target.is_dir() if expected_kind == "dir" else target.is_file()
        if not valid:
            raise WorkshopError("CONFIG_PATH_MISSING", f"configured {name} is not a {expected_kind}: {target}")
    if not set(config.auxiliary_documents).issubset(config.blueprint_ignore):
        raise WorkshopError(
            "CONFIG_INVALID",
            "every auxiliary document must also be excluded from Runtime blueprint inventory",
        )
    for filename in config.auxiliary_documents:
        path = config.blueprint_root / filename
        if not path.is_file():
            raise WorkshopError(
                "CONFIG_PATH_MISSING",
                f"configured auxiliary document is missing: {path}",
            )
    for relative in config.machine_authority_files:
        candidate = _machine_relative_path(relative, "machine authority", base=config.machine_root)
        if not candidate.is_file():
            raise WorkshopError(
                "CONFIG_PATH_MISSING",
                f"configured machine authority is missing: {candidate}",
            )
    return config


def cmake_membership(config: WorkshopConfig) -> list[str]:
    text = normalized_text(config.cmake_file.read_text(encoding="utf-8"))
    values: list[str] = []
    for variable in config.cmake_source_sets:
        match = re.search(rf"(?ms)\bset\s*\(\s*{re.escape(variable)}\b(?P<body>.*?)\)", text)
        if not match:
            raise WorkshopError("CMAKE_SOURCE_SET_MISSING", f"CMake source set is missing: {variable}")
        from .util import source_prefix

        extensions = {"." + value.lstrip(".").lower() for value in _translation_unit_extensions(config)}
        # Tokenize the selected CMake list rather than searching raw text.  A
        # raw regex treats commented-out paths as live members and cannot retain
        # quoted paths containing spaces.  CMake list syntax permits both quote
        # forms; unquoted tokens stop at whitespace.
        tokens: list[str] = []
        token_re = re.compile(r'"([^"]*)"|\'([^\']*)\'|([^\s]+)')
        for line in match.group("body").splitlines():
            code = line.split("#", 1)[0]
            for token_match in token_re.finditer(code):
                token = next((value for value in token_match.groups() if value is not None), "")
                token = token.replace("\\", "/")
                if not token or Path(token).suffix.lower() not in extensions:
                    continue
                prefix = source_prefix()
                if prefix and not token.casefold().startswith(prefix.casefold()):
                    continue
                tokens.append(token)
        if not tokens:
            raise WorkshopError("CMAKE_SOURCE_SET_EMPTY", f"CMake source set contains no translation units: {variable}")
        values.extend(tokens)
    values.extend(config.cmake_extra_sources)
    normalized = [
        _safe_authority_relative(config, value, label="CMake translation unit")
        for value in values
    ]
    duplicates = sorted({value for value in normalized if normalized.count(value) > 1})
    if duplicates:
        raise WorkshopError("CMAKE_SOURCE_DUPLICATE", "CMake membership contains duplicate translation units", details=duplicates)
    return normalized


def dataflow_membership(config: WorkshopConfig) -> list[str]:
    from .util import source_prefix

    authority = config.raw.get("source_authority") or {}
    governed_prefix = source_prefix()
    extensions = _translation_unit_extensions(config)
    section_start = str(authority.get("index_section_start", "### 2.3 "))
    section_end = str(authority.get("index_section_end", "### 2.7 "))
    membership_token = str(authority.get("index_membership_token", "ja"))
    prefix_reset_marker = str(authority.get("index_prefix_reset_marker", "**Einzelne Verzeichnisse"))
    text = normalized_text(config.dataflow_index.read_text(encoding="utf-8"))
    start = text.find(section_start)
    end = text.find(section_end, start)
    if start < 0 or end < 0:
        raise WorkshopError("DATAFLOW_SECTION_MISSING", "DATAFLOW_INDEX sections 2.3 through 2.6 are not present")
    section = text[start:end]
    prefix: str | None = None
    values: list[str] = []
    for line in section.splitlines():
        heading = re.match(r"^\*\*`(?P<prefix>" + re.escape(governed_prefix) + r"(?:[^`]*/)?)`", line)
        if heading:
            prefix = heading.group("prefix")
            continue
        if line.startswith(prefix_reset_marker):
            prefix = None
            continue
        row_pattern = (
            r"^\|\s*`(?P<source>[^`]+\.(?:"
            + "|".join(re.escape(value) for value in extensions)
            + r"))`(?:\s*\([^)]*\))?\s*\|\s*"
            + re.escape(membership_token)
            + r"\s*\|"
        )
        row = re.match(row_pattern, line)
        if not row:
            continue
        source = row.group("source").replace("\\", "/")
        if "/" not in source:
            if prefix is None:
                raise WorkshopError("DATAFLOW_PATH_AMBIGUOUS", f"cannot resolve DATAFLOW source row without directory context: {line}")
            source = prefix + source
        values.append(_safe_authority_relative(config, source, label="DATAFLOW translation unit"))
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise WorkshopError("DATAFLOW_SOURCE_DUPLICATE", "DATAFLOW membership contains duplicate translation units", details=duplicates)
    return values


def blueprint_inventory(config: WorkshopConfig) -> tuple[list[CorpusEntry], list[dict[str, Any]]]:
    entries: list[CorpusEntry] = []
    issues: list[dict[str, Any]] = []
    candidates = [
        path for path in sorted(config.blueprint_root.glob("*.md"), key=lambda value: value.name.lower())
        if path.name not in config.blueprint_ignore
    ]
    for blueprint_path in candidates:
        try:
            reject_link_components(blueprint_path, label="blueprint")
            document = parse_blueprint(blueprint_path)
            source_path: Path | None = None
            if document.source_path:
                source_candidate = config.codebase_root / document.source_path
                reject_link_components(source_candidate, label="mapped source")
                source_path = source_candidate.resolve()
                try:
                    source_path.relative_to(config.runtime_root)
                except ValueError:
                    raise WorkshopError("SOURCE_OUTSIDE_RUNTIME", f"mapped source is outside runtime root: {document.source_path}")
                if not source_path.is_file():
                    raise WorkshopError("SOURCE_MISSING", f"mapped source is missing: {document.source_path}")
            header_path: Path | None = None
            if document.header_path:
                header_candidate = config.codebase_root / document.header_path
                reject_link_components(header_candidate, label="mapped header")
                header_path = header_candidate.resolve()
                try:
                    header_path.relative_to(config.runtime_root)
                except ValueError:
                    raise WorkshopError("HEADER_OUTSIDE_RUNTIME", f"mapped header is outside runtime root: {document.header_path}")
                if not header_path.is_file():
                    raise WorkshopError("HEADER_MISSING", f"mapped header is missing: {document.header_path}")
            else:
                header_ledger, _ = ledger_lines(document)
                if header_ledger:
                    if not document.source_path:
                        raise WorkshopError(
                            "HEADER_MAPPING_MISSING",
                            f"header-only blueprint must declare its exact header path: {blueprint_path.name}",
                        )
                    source_base = (config.codebase_root / document.source_path).with_suffix("")
                    matches: list[Path] = []
                    for suffix in sorted(config.header_extensions):
                        candidate = source_base.with_suffix(suffix)
                        reject_link_components(candidate, label="inferred header")
                        if candidate.is_file() and logical_lines(candidate.read_bytes())[0] == header_ledger:
                            matches.append(candidate.resolve())
                    if len(matches) == 1:
                        header_path = matches[0]
                        inferred = relative_posix(header_path, config.codebase_root)
                        document = replace(document, header_path=inferred)
                    elif len(matches) > 1:
                        raise WorkshopError(
                            "HEADER_MAPPING_AMBIGUOUS",
                            f"multiple sibling headers exactly match the ledger in {blueprint_path.name}",
                            details=[str(value) for value in matches],
                        )
            managed = config.managed_blueprint_root / blueprint_path.name
            reject_link_components(managed, label="managed blueprint")
            if not managed.is_file():
                raise WorkshopError("MANAGED_BLUEPRINT_MISSING", f"managed blueprint is missing: {managed}")
            entries.append(CorpusEntry(
                source_relative=document.source_path,
                source_path=source_path,
                header_relative=document.header_path,
                header_path=header_path,
                blueprint_path=blueprint_path,
                managed_path=managed,
                document=document,
            ))
        except WorkshopError as exc:
            issue = exc.as_dict()
            issue.setdefault("details", {})
            if isinstance(issue["details"], dict):
                issue["details"]["blueprint"] = blueprint_path.name
            issues.append(issue)
    sources = [entry.source_relative for entry in entries if entry.source_relative]
    for duplicate in sorted({value for value in sources if sources.count(value) > 1}):
        issues.append({"code": "BLUEPRINT_SOURCE_DUPLICATE", "message": f"multiple blueprints map the same source: {duplicate}"})
    header_only_headers = [
        entry.header_relative
        for entry in entries
        if entry.source_relative is None and entry.header_relative
    ]
    for duplicate in sorted({
        value for value in header_only_headers if header_only_headers.count(value) > 1
    }):
        issues.append({
            "code": "BLUEPRINT_HEADER_DUPLICATE",
            "message": f"multiple header-only blueprints map the same header: {duplicate}",
        })
    return entries, issues


def _resolve_include(config: WorkshopConfig, including: Path, include: str) -> Path | None:
    """Compatibility resolver for legacy bootstrap/repair inspection.

    Normal corpus authority uses :func:`header_ownership`, which preserves per-TU
    compiler include ordering and external stop points. Bootstrap repair predates that
    owner-aware interface and only needs a bounded local target lookup; keep this helper
    project-local and deterministic instead of reviving the old platform-specific path
    rewrite.
    """
    include_path = Path(str(include).replace("\\", "/"))
    candidates = [including.parent / include_path]
    candidates.extend(root / include_path for root in config.include_roots)
    candidates.append(config.codebase_root / include_path)
    codebase = config.codebase_root.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        try:
            resolved.relative_to(codebase)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


_PROBE_PATH_PAIRED = {
    "-I", "-isystem", "-iquote", "-idirafter", "-isysroot",
    "--sysroot", "-F", "-iframework", "-B", "--gcc-toolchain",
}
_PROBE_PATH_ATTACHED = (
    "-I", "-isystem", "-iquote", "-idirafter", "--sysroot=",
    "-isysroot", "-F", "-iframework", "-B", "--gcc-toolchain=",
)


def _expand_compiler_probe_argv(config: WorkshopConfig, argv: tuple[str, ...]) -> tuple[str, ...]:
    """Expand relative path-valued probe arguments against the config directory."""

    if not argv:
        return tuple()
    result: list[str] = [argv[0]]
    values = list(argv[1:])

    def expand(value: str) -> str:
        if _path_is_absolute(value):
            return Path(value).resolve().as_posix()
        return _config_value_path(config, value, "compiler probe path").as_posix()

    index = 0
    while index < len(values):
        token = values[index]
        if token in _PROBE_PATH_PAIRED and index + 1 < len(values):
            result.extend((token, expand(values[index + 1])))
            index += 2
            continue
        matched = False
        for prefix in _PROBE_PATH_ATTACHED:
            if token.startswith(prefix) and len(token) > len(prefix):
                separator = "=" if prefix.endswith("=") else ""
                base_prefix = prefix[:-1] if separator else prefix
                result.append(base_prefix + separator + expand(token[len(prefix):]))
                matched = True
                break
        if not matched:
            result.append(token)
        index += 1
    return tuple(result)


def _translation_unit_include_sequences(config: WorkshopConfig, source_relative: str) -> tuple[tuple[Path, ...], ...]:
    """Legacy flat include-root representation retained for older workspaces."""
    mapping = config.raw.get("translation_unit_include_roots", {})
    if isinstance(mapping, dict) and source_relative in mapping:
        raw_sequences = mapping[source_relative]
        if not isinstance(raw_sequences, list) or any(not isinstance(item, list) for item in raw_sequences):
            raise WorkshopError(
                "CONFIG_INVALID",
                f"translation_unit_include_roots entry must be an array of arrays: {source_relative}",
            )
        sequences: list[tuple[Path, ...]] = []
        for item in raw_sequences:
            roots: list[Path] = []
            for value in item:
                if not isinstance(value, str) or not value.strip():
                    raise WorkshopError(
                        "CONFIG_INVALID",
                        f"translation_unit_include_roots contains a non-path value: {source_relative}",
                    )
                roots.append(_config_value_path(config, value, "translation-unit include root"))
            sequence = tuple(roots)
            if sequence not in sequences:
                sequences.append(sequence)
        return tuple(sequences or [tuple()])
    return (tuple(config.include_roots),)


def _translation_unit_compiler_probes(
    config: WorkshopConfig, source_relative: str
) -> tuple[tuple[tuple[str, ...], Path], ...]:
    mapping = config.raw.get("translation_unit_compiler_probes", {})
    if not isinstance(mapping, dict) or source_relative not in mapping:
        return tuple()
    raw_probes = mapping[source_relative]
    if not isinstance(raw_probes, list):
        raise WorkshopError(
            "CONFIG_INVALID",
            f"translation_unit_compiler_probes entry must be an array: {source_relative}",
        )
    probes: list[tuple[tuple[str, ...], Path]] = []
    for item in raw_probes:
        if not isinstance(item, dict):
            raise WorkshopError(
                "CONFIG_INVALID",
                f"translation_unit_compiler_probes contains a non-object: {source_relative}",
            )
        argv = item.get("argv")
        directory = item.get("directory")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(value, str) or not value for value in argv)
            or not isinstance(directory, str)
            or not directory.strip()
        ):
            raise WorkshopError(
                "CONFIG_INVALID",
                f"invalid compiler probe declaration: {source_relative}",
            )
        expanded_argv = _expand_compiler_probe_argv(config, tuple(argv))
        probe = (expanded_argv, _config_value_path(config, directory, "compiler probe directory"))
        if probe not in probes:
            probes.append(probe)
    return tuple(probes)


def _translation_unit_include_variants(config: WorkshopConfig, source_relative: str) -> tuple[dict[str, Any], ...]:
    """Return per-compiler-command include semantics without cross-mixing variants."""
    mapping = config.raw.get("translation_unit_include_variants", {})
    if isinstance(mapping, dict) and source_relative in mapping:
        raw_variants = mapping[source_relative]
        if not isinstance(raw_variants, list) or not raw_variants:
            raise WorkshopError(
                "CONFIG_INVALID",
                f"translation_unit_include_variants must be a non-empty array: {source_relative}",
            )
        variants: list[dict[str, Any]] = []
        for item in raw_variants:
            if not isinstance(item, dict):
                raise WorkshopError(
                    "CONFIG_INVALID",
                    f"translation_unit_include_variants contains a non-object: {source_relative}",
                )
            parsed: dict[str, Any] = {}
            for key in ("quote_roots", "angle_roots", "idirafter_roots"):
                raw_roots = item.get(key, [])
                if not isinstance(raw_roots, list) or any(not isinstance(value, str) or not value for value in raw_roots):
                    raise WorkshopError("CONFIG_INVALID", f"invalid {key} for {source_relative}")
                parsed[key] = tuple(
                    _config_value_path(config, value, f"{key} include root")
                    for value in raw_roots
                )
            raw_probe = item.get("compiler_probe")
            if raw_probe is None:
                parsed["compiler_probe"] = None
            else:
                if not isinstance(raw_probe, dict):
                    raise WorkshopError("CONFIG_INVALID", f"invalid compiler_probe for {source_relative}")
                argv = raw_probe.get("argv")
                directory = raw_probe.get("directory")
                if (
                    not isinstance(argv, list) or not argv
                    or any(not isinstance(value, str) or not value for value in argv)
                    or not isinstance(directory, str) or not directory.strip()
                ):
                    raise WorkshopError("CONFIG_INVALID", f"invalid compiler_probe for {source_relative}")
                parsed["compiler_probe"] = (
                    _expand_compiler_probe_argv(config, tuple(argv)),
                    _config_value_path(config, directory, "compiler probe directory"),
                )
            if parsed not in variants:
                variants.append(parsed)
        return tuple(variants)

    # Backwards compatibility for workspaces created before per-variant search
    # categories were persisted. Pair by ordinal where possible and otherwise
    # keep the root sequence conservative; this path must not invent -iquote or
    # -idirafter semantics that were not recorded.
    sequences = _translation_unit_include_sequences(config, source_relative)
    probes = _translation_unit_compiler_probes(config, source_relative)
    count = max(len(sequences), len(probes), 1)
    result: list[dict[str, Any]] = []
    for index in range(count):
        roots = sequences[index] if index < len(sequences) else sequences[-1]
        probe = probes[index] if index < len(probes) else None
        row = {
            "quote_roots": roots,
            "angle_roots": roots,
            "idirafter_roots": tuple(),
            "compiler_probe": probe,
        }
        if row not in result:
            result.append(row)
    return tuple(result)


def _compiler_selected_literal(
    config: WorkshopConfig,
    *,
    variant: dict[str, Any],
    owner: str,
    including: Path,
    opener: str,
    include: str,
) -> tuple[str, Path | None]:
    """Use retained GNU/Clang search evidence to identify one literal dependency."""
    probe = variant.get("compiler_probe")
    if not probe:
        return "unsupported", None
    argv, directory = probe
    cwd = directory if directory.is_dir() else config.codebase_root
    language = "c" if Path(owner).suffix.lower() == ".c" else "c++"
    command = [*argv]
    if opener == '"':
        command.extend(("-iquote", str(including.parent)))
    command.extend(("-H", "-E", "-x", language, "-"))
    source = f"#include {opener}{include}{'>' if opener == '<' else chr(34)}\n"
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
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
        selected.relative_to(config.codebase_root.resolve())
    except ValueError:
        return "external", selected
    return "local", selected


def _compiler_resolves_external_literal(
    config: WorkshopConfig,
    *,
    owner: str,
    including: Path,
    include: str,
) -> bool:
    """Compatibility wrapper for older tests/callers."""
    for variant in _translation_unit_include_variants(config, owner):
        status, _ = _compiler_selected_literal(
            config, variant=variant, owner=owner, including=including,
            opener='"', include=include,
        )
        if status == "external":
            return True
    return False


_INCLUDE_EDGE_KEYS = (
    "compiled_root", "including_file", "include_line", "include_token", "delimiter",
    "resolved_target", "resolution_class", "compiler_variant", "resolution_path",
)


def canonical_include_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in edges:
        row = {
            "compiled_root": str(raw["compiled_root"]).replace("\\", "/"),
            "including_file": str(raw["including_file"]).replace("\\", "/"),
            "include_line": int(raw["include_line"]),
            "include_token": str(raw["include_token"]),
            "delimiter": str(raw["delimiter"]),
            "resolved_target": str(raw["resolved_target"]).replace("\\", "/"),
            "resolution_class": str(raw["resolution_class"]),
            "compiler_variant": int(raw["compiler_variant"]),
            "resolution_path": str(raw["resolution_path"]),
        }
        rows[tuple(row[key] for key in _INCLUDE_EDGE_KEYS)] = row
    return [rows[key] for key in sorted(rows)]


def include_topology_sha256(edges: Iterable[dict[str, Any]]) -> str:
    return sha256_text(json.dumps(canonical_include_edges(edges), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _include_analysis(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]], list[dict[str, Any]]]:
    overrides = overrides or {}
    literal_re = re.compile(r'(?m)^\s*#\s*include\s*([<"])([^>"\n]+)[>"]')
    any_re = re.compile(r'(?m)^\s*#\s*include\s+([^\n]+)')
    codebase = config.codebase_root.resolve()
    queue: list[tuple[Path, str, int, dict[str, Any]]] = []
    owners: dict[str, set[str]] = {}
    edges: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    ecosystem_map = config.raw.get("source_ecosystems") or {}
    if not isinstance(ecosystem_map, dict):
        raise WorkshopError("CONFIG_INVALID", "source_ecosystems must map governed source paths to ecosystem names")
    for relative in translation_units:
        relative = str(relative).replace("\\", "/")
        if ecosystem_map and str(ecosystem_map.get(relative, "")) != "c_family":
            continue
        relative_path = Path(relative)
        source_candidate = codebase / relative_path
        if not relative or _path_is_absolute(relative) or _path_is_drive_relative(relative) or ".." in relative_path.parts:
            issues.append({"code": "SOURCE_AUTHORITY_PATH_UNSAFE", "message": f"translation-unit authority path is unsafe: {relative}"})
            continue
        try:
            reject_link_components(source_candidate, label="translation-unit source")
            logical = source_candidate.resolve()
            logical.relative_to(codebase)
        except (WorkshopError, OSError, ValueError) as exc:
            issues.append({"code": "SOURCE_AUTHORITY_PATH_UNSAFE", "message": f"translation-unit authority path cannot be verified: {relative}", "details": {"error": str(exc)}})
            continue
        for variant_index, variant in enumerate(_translation_unit_include_variants(config, relative), 1):
            queue.append((logical, relative, variant_index, variant))

    seen: set[tuple[Path, str, int]] = set()

    def physical_path(logical: Path) -> Path:
        try:
            relative = logical.resolve().relative_to(codebase).as_posix()
        except ValueError:
            return logical
        return overrides.get(relative, logical)

    while queue:
        logical_path, compiled_root, variant_index, variant = queue.pop(0)
        visit = (logical_path.resolve(), compiled_root, variant_index)
        if visit in seen:
            continue
        seen.add(visit)
        physical = physical_path(logical_path)
        try:
            reject_link_components(physical, label="include-graph input")
        except WorkshopError as exc:
            issues.append({"code": "INCLUDE_SCAN_LINK_UNSAFE", "message": str(exc), "details": {"including": relative_posix(logical_path, codebase), "compiled_root": compiled_root}})
            continue
        if not physical.is_file():
            issues.append({"code": "INCLUDE_SCAN_INPUT_MISSING", "message": f"include-graph input is missing: {physical}"})
            continue
        try:
            text = physical.read_text(encoding="utf-8")
        except UnicodeError as exc:
            issues.append({"code": "INCLUDE_SCAN_NON_UTF8", "message": f"cannot scan includes in {physical}: {exc}"})
            continue
        including_relative = relative_posix(logical_path, codebase)
        literal_starts = {match.start() for match in literal_re.finditer(text)}
        for match in any_re.finditer(text):
            if match.start() not in literal_starts:
                issues.append({
                    "code": "DYNAMIC_INCLUDE_UNSUPPORTED",
                    "message": "preprocessor-computed include cannot be proven by compiler include-edge authority",
                    "details": {"including": including_relative, "compiled_root": compiled_root, "include": match.group(1).strip()},
                })
        for match in literal_re.finditer(text):
            opener, include = match.group(1), match.group(2).strip()
            delimiter = "quote" if opener == '"' else "angle"
            include_line = text.count("\n", 0, match.start()) + 1
            include_path = Path(include.replace("\\", "/"))
            search_roots = variant["quote_roots"] if opener == '"' else variant["angle_roots"]
            candidates: list[tuple[Path, str]] = []
            if opener == '"':
                candidates.append((logical_path.parent / include_path, "including_directory"))
            candidates.extend((root / include_path, f"{delimiter}_search_root:{index}") for index, root in enumerate(search_roots))
            selected_local: Path | None = None
            selected_external = False
            resolution_path = ""
            unsafe = False
            for candidate, candidate_role in candidates:
                try:
                    reject_link_components(candidate, label="include candidate")
                except WorkshopError as exc:
                    issues.append({"code": "INCLUDE_LINK_UNSAFE", "message": str(exc), "details": {"including": including_relative, "compiled_root": compiled_root}})
                    unsafe = True
                    break
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not resolved.is_file():
                    continue
                try:
                    resolved.relative_to(codebase)
                except ValueError:
                    selected_external = True
                    resolution_path = candidate_role
                    break
                if resolved.suffix.lower() in config.header_extensions:
                    selected_local = resolved
                    resolution_path = candidate_role
                    break
            if unsafe:
                continue
            if selected_local is not None and any(selected_local.is_relative_to(root) for root in variant["idirafter_roots"]):
                status, compiler_selected = _compiler_selected_literal(
                    config, variant=variant, owner=compiled_root, including=logical_path, opener=opener, include=include,
                )
                if status == "external":
                    selected_local = None
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and compiler_selected is not None:
                    selected_local = compiler_selected
                    resolution_path = "compiler_probe"
                else:
                    issues.append({"code": "INCLUDE_SELECTION_AMBIGUOUS", "message": f"cannot prove compiler selection for -idirafter include: {include}", "details": {"including": including_relative, "compiled_root": compiled_root}})
                    continue
            if selected_local is None and opener == '"' and not selected_external:
                status, compiler_selected = _compiler_selected_literal(
                    config, variant=variant, owner=compiled_root, including=logical_path, opener=opener, include=include,
                )
                if status == "external":
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and compiler_selected is not None:
                    selected_local = compiler_selected
                    resolution_path = "compiler_probe"
                elif status == "missing":
                    issues.append({"code": "RUNTIME_INCLUDE_UNRESOLVED", "message": f"quoted project include cannot be resolved: {include}", "details": {"including": including_relative, "compiled_root": compiled_root}})
                    continue
            if selected_local is not None:
                if selected_local.suffix.lower() not in config.header_extensions:
                    issues.append({"code": "LOCAL_INCLUDE_SUFFIX_UNSUPPORTED", "message": f"project-local include has unsupported suffix: {include}"})
                    continue
                target = relative_posix(selected_local, codebase)
                owners.setdefault(target, set()).add(compiled_root)
                edges.append({
                    "compiled_root": compiled_root,
                    "including_file": including_relative,
                    "include_line": include_line,
                    "include_token": include,
                    "delimiter": delimiter,
                    "resolved_target": target,
                    "resolution_class": "local",
                    "compiler_variant": variant_index,
                    "resolution_path": resolution_path or "compiler_order",
                })
                queue.append((selected_local, compiled_root, variant_index, variant))
            elif selected_external:
                edges.append({
                    "compiled_root": compiled_root,
                    "including_file": including_relative,
                    "include_line": include_line,
                    "include_token": include,
                    "delimiter": delimiter,
                    "resolved_target": include,
                    "resolution_class": "external",
                    "compiler_variant": variant_index,
                    "resolution_path": resolution_path or "compiler_order",
                })
    return owners, canonical_include_edges(edges), issues


def header_ownership(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    owners, _, issues = _include_analysis(config, translation_units, overrides=overrides)
    return owners, issues


def direct_include_edges(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _, edges, issues = _include_analysis(config, translation_units, overrides=overrides)
    return edges, issues


def header_closure(config: WorkshopConfig, translation_units: Iterable[str]) -> tuple[set[str], list[dict[str, Any]]]:
    ownership, issues = header_ownership(config, translation_units)
    return set(ownership), issues


def declared_header_ownership(config: WorkshopConfig) -> dict[str, set[str]] | None:
    authority = config.raw.get("source_authority") or {}
    section_id = authority.get("include_ownership_section_id")
    if not isinstance(section_id, str) or not section_id:
        return None
    text = normalized_text(config.dataflow_index.read_text(encoding="utf-8"))
    marker = f'<a id="{section_id}"></a>'
    start = text.find(marker)
    if start < 0:
        raise WorkshopError(
            "DATAFLOW_INCLUDE_SECTION_MISSING",
            f"configured include-ownership section is missing: {section_id}",
        )
    next_anchor = text.find('<a id="', start + len(marker))
    section = text[start:] if next_anchor < 0 else text[start:next_anchor]
    result: dict[str, set[str]] = {}
    row_re = re.compile(r'^-\s+`(?P<header>[^`]+)`\s+←\s+(?P<owners>.+)$')
    owner_re = re.compile(r'`([^`]+)`')
    for line in section.splitlines():
        match = row_re.match(line.strip())
        if not match:
            continue
        result[match.group("header").replace("\\", "/")] = {
            value.replace("\\", "/") for value in owner_re.findall(match.group("owners"))
        }
    return result



def _include_edge_config(config: WorkshopConfig) -> tuple[str, str] | None:
    authority = config.raw.get("source_authority") or {}
    if not isinstance(authority, dict):
        raise WorkshopError("CONFIG_INVALID", "source_authority must be an object")
    relative = authority.get("include_edges_file")
    section_id = authority.get("include_edges_section_id")
    if relative is None and section_id is None:
        return None
    if not isinstance(relative, str) or not relative.strip() or not isinstance(section_id, str) or not section_id.strip():
        raise WorkshopError("CONFIG_INVALID", "include edge authority requires include_edges_file and include_edges_section_id")
    path = _machine_relative_path(relative, "include_edges_file", base=config.machine_root)
    return path.relative_to(config.machine_root).as_posix(), section_id


def load_include_edge_authority(config: WorkshopConfig) -> dict[str, Any] | None:
    configured = _include_edge_config(config)
    if configured is None:
        return None
    relative, _ = configured
    path = config.machine_root / relative
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != "kairos-compiler-include-edges/v1":
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", f"invalid include-edge authority: {path}")
    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list) or any(not isinstance(row, dict) for row in raw_edges):
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", "include-edge authority edges must be an object array")
    try:
        edges = canonical_include_edges(raw_edges)
    except Exception as exc:
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", f"cannot canonicalize include-edge authority: {exc}") from exc
    digest = include_topology_sha256(edges)
    if raw_edges != edges:
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_NONCANONICAL", "include-edge authority is not canonically ordered")
    if payload.get("topology_sha256") != digest or int(payload.get("edge_count", -1)) != len(edges):
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_DIGEST_MISMATCH", "include-edge authority digest/count differs from canonical rows")
    return {**payload, "edges": edges}


def declared_include_edges(config: WorkshopConfig) -> dict[str, Any] | None:
    configured = _include_edge_config(config)
    if configured is None:
        return None
    _, section_id = configured
    text = normalized_text(config.dataflow_index.read_text(encoding="utf-8"))
    marker = f'<a id="{section_id}"></a>'
    start = text.find(marker)
    if start < 0:
        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_SECTION_MISSING", f"configured include-edge section is missing: {section_id}")
    next_anchor = text.find('<a id="', start + len(marker))
    section = text[start:] if next_anchor < 0 else text[start:next_anchor]
    count_match = re.search(r"Edge count:\s*`([0-9]+)`", section)
    digest_match = re.search(r"Topology SHA-256:\s*`([0-9a-f]{64})`", section)
    if not count_match or not digest_match:
        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_DIGEST_MISSING", "include-edge section has no edge count/topology digest")
    return {
        "topology_sha256": digest_match.group(1),
        "edge_count": int(count_match.group(1)),
    }


def verify_include_edge_database(config: WorkshopConfig, edges: list[dict[str, Any]], *, database_path: Path | None = None) -> list[dict[str, Any]]:
    database_path = (database_path or config.kairos_database).resolve()
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "compiler_include_edges" not in tables:
            return [{"code": "DATABASE_INCLUDE_EDGE_TABLE_MISSING", "message": "KAIROS database has no compiler_include_edges table"}]
        actual = [
            {
                "compiled_root": row["compiled_root"],
                "including_file": row["including_file"],
                "include_line": int(row["include_line"]),
                "include_token": row["include_token"],
                "delimiter": row["delimiter"],
                "resolved_target": row["resolved_target"],
                "resolution_class": row["resolution_class"],
                "compiler_variant": int(row["compiler_variant"]),
                "resolution_path": row["resolution_path"],
            }
            for row in connection.execute(
                "SELECT compiled_root,including_file,include_line,include_token,delimiter,resolved_target,resolution_class,compiler_variant,resolution_path FROM compiler_include_edges WHERE artifact_id='PROJECT_SOURCE_INDEX' ORDER BY ordinal"
            ).fetchall()
        ]
    finally:
        connection.close()
    if actual != edges:
        return [{"code": "DATABASE_INCLUDE_EDGE_MISMATCH", "message": "compiler_include_edges projection differs from machine authority", "details": {"expected_count": len(edges), "actual_count": len(actual)}}]
    return []


def _section_capsule(content: str) -> str:
    lines = content.strip().splitlines()
    quoted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if quoted:
                break
            continue
        if stripped.startswith(">"):
            value = stripped[1:].strip()
            value = re.sub(r"^(?:capsule|context)\s*:\s*", "", value, flags=re.I)
            quoted.append(value)
            continue
        if quoted:
            break
    if quoted:
        return " ".join(quoted).strip()[:480]
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "<!--", "[ref:")):
            return stripped[:480]
    return ""


def parsed_sections(document: BlueprintDocument) -> list[dict[str, Any]]:
    body = document.text[document.body_start:]
    matches = list(SECTION_RE.finditer(body))
    result: list[dict[str, Any]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        section_body = body[match.end():end].strip()
        result.append({
            "section_id": match.group("id"),
            "position": position,
            "title": match.group("title").strip(),
            "capsule": _section_capsule(section_body),
            "body": section_body,
            "body_sha256": sha256_text(section_body),
        })
    return result


def _graph_cell(column: str, raw: Any) -> Any:
    if column in {"hash_bound", "commit_bound"}:
        return 1 if raw is True else 0
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_project_intake_binding(
    config: WorkshopConfig,
    *,
    translation_units: Iterable[str],
    include_owners: dict[str, set[str]],
    records: dict[str, Any],
    intake_path: Path | None = None,
    compile_commands_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Verify the retained project-intake claims against the current sealed corpus.

    The intake manifest and Workshop config are both machine authority files. Merely
    hashing both into one package is not enough: stale cross-references must fail closed
    rather than become two independently authentic but contradictory facts.
    """
    intake_path = Path(intake_path or (config.machine_root / "project-intake.json"))
    if not intake_path.is_absolute():
        intake_path = config.machine_root / intake_path
    reject_link_components(intake_path, label="project intake authority")
    intake_path = intake_path.resolve()
    if not intake_path.is_file():
        return []
    issues: list[dict[str, Any]] = []
    try:
        intake = read_json(intake_path)
    except Exception as exc:
        return [{
            "code": "PROJECT_INTAKE_INVALID",
            "message": f"cannot read project intake authority: {exc}",
        }]
    if not isinstance(intake, dict) or intake.get("schema") != "kairos-project-intake/v1":
        return [{
            "code": "PROJECT_INTAKE_INVALID",
            "message": "project-intake.json has an unsupported schema",
        }]

    def declared_path(value: Any) -> Path | None:
        try:
            return _configured_path(value, "project-intake path", base=config.machine_root)
        except WorkshopError:
            return None

    declared_project_root = str(intake.get("project_root", ""))
    declared_workspace = str(intake.get("workspace", ""))
    if declared_path(declared_project_root) != config.codebase_root.resolve():
        issues.append({
            "code": "PROJECT_INTAKE_PROJECT_ROOT_MISMATCH",
            "message": "project intake root differs from the configured codebase root",
            "details": {"declared": declared_project_root, "configured": config.codebase_root.as_posix()},
        })
    if declared_path(declared_workspace) != config.kairos_workspace.resolve():
        issues.append({
            "code": "PROJECT_INTAKE_WORKSPACE_MISMATCH",
            "message": "project intake workspace differs from the configured KAIROS workspace",
            "details": {"declared": declared_workspace, "configured": config.kairos_workspace.as_posix()},
        })

    declared_config_hash = str(intake.get("workshop_config_sha256", "")).lower()
    actual_config_hash = package_hash(config.raw).lower()
    if declared_config_hash != actual_config_hash:
        issues.append({
            "code": "PROJECT_INTAKE_CONFIG_HASH_MISMATCH",
            "message": "project intake does not bind the current Workshop configuration",
            "details": {"declared": declared_config_hash, "actual": actual_config_hash},
        })

    authority = intake.get("authority") or {}
    declared_compiler_context = (
        str(authority.get("compiler_context_sha256", "")).lower()
        if isinstance(authority, dict)
        else ""
    )
    configured_compiler_context = str(config.raw.get("compiler_context_sha256", "")).lower()
    if not configured_compiler_context:
        issues.append({
            "code": "PROJECT_INTAKE_COMPILER_CONTEXT_CONFIG_MISSING",
            "message": "Workshop config does not retain the compiler-context identity bound by project intake",
        })
    elif declared_compiler_context != configured_compiler_context:
        issues.append({
            "code": "PROJECT_INTAKE_COMPILER_CONTEXT_MISMATCH",
            "message": "project intake compiler-context identity differs from Workshop configuration",
            "details": {
                "declared": declared_compiler_context,
                "configured": configured_compiler_context,
            },
        })
    compile_record = authority.get("compile_commands") if isinstance(authority, dict) else None
    compile_path = Path(
        compile_commands_path
        or (config.machine_root / "project-intake" / "compile_commands.json")
    )
    if not compile_path.is_absolute():
        compile_path = config.machine_root / compile_path
    reject_link_components(compile_path, label="retained compile authority")
    compile_path = compile_path.resolve()
    if not isinstance(compile_record, dict) or not compile_path.is_file():
        issues.append({
            "code": "PROJECT_INTAKE_COMPILE_AUTHORITY_MISSING",
            "message": "project intake compile_commands authority is missing",
        })
    else:
        raw = compile_path.read_bytes()
        actual_sha = sha256_bytes(raw).lower()
        actual_bytes = len(raw)
        declared_compile_path = str(compile_record.get("path", ""))
        if declared_path(declared_compile_path) != compile_path:
            issues.append({
                "code": "PROJECT_INTAKE_COMPILE_PATH_MISMATCH",
                "message": "project intake compile_commands path differs from retained machine authority",
                "details": {"declared": declared_compile_path, "actual": compile_path.as_posix()},
            })
        if str(compile_record.get("sha256", "")).lower() != actual_sha or int(compile_record.get("bytes", -1)) != actual_bytes:
            issues.append({
                "code": "PROJECT_INTAKE_COMPILE_HASH_MISMATCH",
                "message": "retained compile_commands bytes differ from project-intake authority",
                "details": {
                    "declared_sha256": compile_record.get("sha256"),
                    "actual_sha256": actual_sha,
                    "declared_bytes": compile_record.get("bytes"),
                    "actual_bytes": actual_bytes,
                },
            })

    declared_units = intake.get("translation_units")
    unit_rows: dict[str, dict[str, Any]] = {}
    if not isinstance(declared_units, list):
        issues.append({"code": "PROJECT_INTAKE_SOURCE_SET_INVALID", "message": "project intake translation_units must be an array"})
    else:
        for item in declared_units:
            if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str):
                issues.append({"code": "PROJECT_INTAKE_SOURCE_SET_INVALID", "message": "project intake contains an invalid translation-unit row"})
                continue
            relative = item["relative_path"].replace("\\", "/")
            if relative in unit_rows:
                issues.append({"code": "PROJECT_INTAKE_SOURCE_DUPLICATE", "message": f"duplicate project-intake translation unit: {relative}"})
            unit_rows[relative] = item
    expected_units = set(str(value).replace("\\", "/") for value in translation_units)
    if set(unit_rows) != expected_units:
        issues.append({
            "code": "PROJECT_INTAKE_SOURCE_SET_MISMATCH",
            "message": "project-intake translation-unit membership differs from Workshop authority",
            "details": {
                "intake_only": sorted(set(unit_rows) - expected_units),
                "workshop_only": sorted(expected_units - set(unit_rows)),
            },
        })
    for relative in sorted(expected_units & set(unit_rows)):
        record = records.get(relative) or {}
        actual = record.get("source") if isinstance(record, dict) else None
        declared = unit_rows[relative].get("facts")
        if not isinstance(actual, dict) or not isinstance(declared, dict):
            issues.append({"code": "PROJECT_INTAKE_SOURCE_FACT_MISSING", "message": f"source facts missing for {relative}"})
            continue
        declared_source_path = str(declared.get("path", ""))
        expected_source_path = (config.codebase_root / relative).resolve()
        if declared_path(declared_source_path) != expected_source_path:
            issues.append({
                "code": "PROJECT_INTAKE_SOURCE_PATH_MISMATCH",
                "message": f"project-intake source path differs for {relative}",
            })
        if str(declared.get("sha256", "")).lower() != str(actual.get("sha256", "")).lower() or int(declared.get("bytes", -1)) != int(actual.get("byte_count", -2)):
            issues.append({
                "code": "PROJECT_INTAKE_SOURCE_FACT_MISMATCH",
                "message": f"project-intake source fact differs for {relative}",
            })

        configured_sequences = (config.raw.get("translation_unit_include_roots") or {}).get(relative, [])
        configured_roots = sorted({
            str(value)
            for sequence in configured_sequences
            if isinstance(sequence, list)
            for value in sequence
            if isinstance(value, str)
        })
        declared_roots = sorted(str(value) for value in unit_rows[relative].get("include_roots", []))
        if declared_roots != configured_roots:
            issues.append({
                "code": "PROJECT_INTAKE_TU_INCLUDE_ROOT_MISMATCH",
                "message": f"project-intake include roots differ for {relative}",
                "details": {"declared": declared_roots, "configured": configured_roots},
            })

        configured_variant_map = config.raw.get("translation_unit_include_variants") or {}
        if isinstance(configured_variant_map, dict) and relative in configured_variant_map:
            configured_variants = configured_variant_map.get(relative)
            declared_variants = unit_rows[relative].get("include_search_variants")
            if not isinstance(configured_variants, list) or not isinstance(declared_variants, list):
                issues.append({
                    "code": "PROJECT_INTAKE_INCLUDE_VARIANT_INVALID",
                    "message": f"project-intake/config include variants are not arrays for {relative}",
                })
            else:
                def roots_only(rows: list[Any]) -> list[dict[str, list[str]]]:
                    result: list[dict[str, list[str]]] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            return []
                        result.append({
                            key: [str(value) for value in row.get(key, [])]
                            for key in ("quote_roots", "angle_roots", "idirafter_roots")
                        })
                    return result
                if roots_only(configured_variants) != roots_only(declared_variants):
                    issues.append({
                        "code": "PROJECT_INTAKE_INCLUDE_VARIANT_MISMATCH",
                        "message": f"project-intake include search semantics differ for {relative}",
                    })

    declared_include_roots = sorted(str(value) for value in intake.get("include_roots", []))
    # Compare the serialized authority representation, not the loader's
    # resolved Path objects.  Relative config paths are intentionally portable
    # and must remain byte-for-byte aligned with the retained intake manifest.
    configured_include_roots = sorted(
        str(value).replace("\\", "/")
        for value in (config.raw.get("include_roots") or [])
    )
    if declared_include_roots != configured_include_roots:
        issues.append({
            "code": "PROJECT_INTAKE_INCLUDE_ROOT_MISMATCH",
            "message": "project-intake global include roots differ from Workshop configuration",
            "details": {"declared": declared_include_roots, "configured": configured_include_roots},
        })

    declared_closure = intake.get("include_closure")
    if not isinstance(declared_closure, dict):
        issues.append({"code": "PROJECT_INTAKE_INCLUDE_CLOSURE_INVALID", "message": "project intake include_closure must be an object"})
        declared_closure = {}
    actual_headers = set(include_owners)
    if set(declared_closure) != actual_headers:
        issues.append({
            "code": "PROJECT_INTAKE_INCLUDE_SET_MISMATCH",
            "message": "project-intake include closure differs from Workshop include authority",
            "details": {
                "intake_only": sorted(set(declared_closure) - actual_headers),
                "workshop_only": sorted(actual_headers - set(declared_closure)),
            },
        })
    for relative in sorted(actual_headers & set(declared_closure)):
        row = declared_closure[relative]
        record = records.get(relative) or {}
        actual = record.get("header") if isinstance(record, dict) else None
        if not isinstance(row, dict) or not isinstance(actual, dict):
            issues.append({"code": "PROJECT_INTAKE_HEADER_FACT_MISSING", "message": f"header facts missing for {relative}"})
            continue
        declared_owners = sorted(str(value) for value in row.get("owners", [])) if isinstance(row.get("owners"), list) else []
        if declared_owners != sorted(include_owners.get(relative, set())):
            issues.append({"code": "PROJECT_INTAKE_HEADER_OWNER_MISMATCH", "message": f"project-intake header owners differ for {relative}"})
        facts = row.get("facts")
        if isinstance(facts, dict):
            declared_header_path = str(facts.get("path", ""))
            expected_header_path = (config.codebase_root / relative).resolve()
            if declared_path(declared_header_path) != expected_header_path:
                issues.append({
                    "code": "PROJECT_INTAKE_HEADER_PATH_MISMATCH",
                    "message": f"project-intake header path differs for {relative}",
                })
        if not isinstance(facts, dict) or str(facts.get("sha256", "")).lower() != str(actual.get("sha256", "")).lower() or int((facts or {}).get("bytes", -1)) != int(actual.get("byte_count", -2)):
            issues.append({"code": "PROJECT_INTAKE_HEADER_FACT_MISMATCH", "message": f"project-intake header fact differs for {relative}"})

    declared_blueprints = intake.get("blueprints")
    blueprint_rows: dict[str, tuple[str, str, str]] = {}
    if isinstance(declared_blueprints, list):
        for item in declared_blueprints:
            if isinstance(item, dict) and isinstance(item.get("relative_path"), str):
                blueprint_rows[item["relative_path"].replace("\\", "/")] = (
                    str(item.get("filename", "")),
                    str(item.get("kind", "")),
                    str(item.get("artifact_id", "")),
                )
    expected_blueprints = {
        relative: (
            str(record.get("blueprint", {}).get("filename", "")),
            str(record.get("kind", "")),
            str(record.get("blueprint", {}).get("artifact_id", "")),
        )
        for relative, record in records.items()
    }
    if blueprint_rows != expected_blueprints:
        issues.append({
            "code": "PROJECT_INTAKE_BLUEPRINT_MISMATCH",
            "message": "project-intake blueprint inventory differs from active Workshop records",
        })
    return issues


def verify_database_projection(config: WorkshopConfig, entries: Iterable[CorpusEntry], *, database_path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    database_path = (database_path or config.kairos_database).resolve()
    issues: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            issues.append({"code": "DATABASE_INTEGRITY_FAILED", "message": f"SQLite integrity_check returned {integrity[0] if integrity else None}"})
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        required_tables = {"artifacts", "sections", "section_fts", "artifact_revisions", *(item[1] for item in GRAPH_PROJECTIONS)}
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            issues.append({"code": "DATABASE_SCHEMA_INCOMPLETE", "message": "KAIROS database is missing required projection tables", "details": missing_tables})
            return summaries, issues
        for entry in entries:
            raw = entry.managed_path.read_bytes()
            normalized_hash = normalized_sha256(raw)
            artifact_id = entry.document.artifact_id
            row = connection.execute(
                "SELECT artifact_id,path,revision,content_sha256,metadata_json FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if not row:
                issues.append({"code": "DATABASE_ARTIFACT_MISSING", "message": f"database artifact is missing: {artifact_id}", "details": {"blueprint": entry.blueprint_path.name}})
                continue
            expected_path = f"code/{entry.blueprint_path.name}"
            if row["path"] != expected_path:
                issues.append({"code": "DATABASE_PATH_MISMATCH", "message": f"database path differs for {artifact_id}", "details": {"expected": expected_path, "actual": row["path"]}})
            if int(row["revision"]) != entry.document.revision:
                issues.append({"code": "DATABASE_REVISION_MISMATCH", "message": f"database revision differs for {artifact_id}", "details": {"document": entry.document.revision, "database": int(row['revision'])}})
            if row["content_sha256"] != normalized_hash:
                issues.append({"code": "DATABASE_CONTENT_MISMATCH", "message": f"database content hash differs for {artifact_id}", "details": {"document": normalized_hash, "database": row['content_sha256']}})
            revision_row = connection.execute(
                "SELECT content_sha256 FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, entry.document.revision),
            ).fetchone()
            if not revision_row or revision_row["content_sha256"] != normalized_hash:
                issues.append({"code": "DATABASE_REVISION_ROW_MISMATCH", "message": f"artifact revision row differs for {artifact_id}"})
            expected_sections = parsed_sections(entry.document)
            actual_sections = [dict(value) for value in connection.execute(
                "SELECT section_id,position,title,capsule,body,body_sha256 FROM sections WHERE artifact_id=? ORDER BY position",
                (artifact_id,),
            ).fetchall()]
            if expected_sections != actual_sections:
                issues.append({
                    "code": "DATABASE_SECTION_MISMATCH",
                    "message": f"section projection differs for {artifact_id}",
                    "details": {"expected_count": len(expected_sections), "actual_count": len(actual_sections)},
                })
            fts_rows = [dict(value) for value in connection.execute(
                "SELECT section_id,title,body FROM section_fts WHERE artifact_id=? ORDER BY rowid",
                (artifact_id,),
            ).fetchall()]
            expected_fts = [{"section_id": value["section_id"], "title": value["title"], "body": value["body"]} for value in expected_sections]
            if fts_rows != expected_fts:
                issues.append({"code": "DATABASE_FTS_MISMATCH", "message": f"FTS projection differs for {artifact_id}", "details": {"expected_count": len(expected_fts), "actual_count": len(fts_rows)}})
            graph_counts: dict[str, int] = {}
            for header_key, table, columns in GRAPH_PROJECTIONS:
                declared = entry.document.metadata.get(header_key, [])
                if not isinstance(declared, list):
                    issues.append({"code": "BLUEPRINT_GRAPH_INVALID", "message": f"{header_key} is not an array in {entry.blueprint_path.name}"})
                    continue
                selected_columns = [column for column, _ in columns]
                sql_columns = ",".join(("ordinal", *selected_columns))
                actual = [dict(value) for value in connection.execute(
                    f"SELECT {sql_columns} FROM {table} WHERE artifact_id=? ORDER BY ordinal",
                    (artifact_id,),
                ).fetchall()]
                expected: list[dict[str, Any]] = []
                for ordinal, item in enumerate(declared, 1):
                    projected: dict[str, Any] = {"ordinal": ordinal}
                    if not isinstance(item, dict):
                        continue
                    for column, keys in columns:
                        value = next((item[key] for key in keys if key in item), None)
                        projected[column] = _graph_cell(column, value)
                    expected.append(projected)
                if actual != expected:
                    issues.append({"code": "DATABASE_GRAPH_MISMATCH", "message": f"{table} projection differs for {artifact_id}", "details": {"expected_count": len(expected), "actual_count": len(actual)}})
                graph_counts[table] = len(actual)
            summaries[entry.record_key] = {
                "artifact_id": artifact_id,
                "path": row["path"],
                "revision": int(row["revision"]),
                "content_sha256": row["content_sha256"],
                "section_count": len(actual_sections),
                "fts_count": len(fts_rows),
                "graph_counts": graph_counts,
            }
    finally:
        connection.close()
    return summaries, issues


def build_corpus_manifest(config: WorkshopConfig, *, verify_database: bool = True) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    blueprint_file_count = len([
        path for path in config.blueprint_root.glob("*.md")
        if path.name not in config.blueprint_ignore
    ])
    cmake = cmake_membership(config)
    dataflow = dataflow_membership(config)
    if len(cmake) != config.expected_translation_units:
        issues.append({"code": "CMAKE_COUNT_MISMATCH", "message": f"CMake defines {len(cmake)} translation units, expected {config.expected_translation_units}"})
    if len(dataflow) != config.expected_translation_units:
        issues.append({"code": "DATAFLOW_COUNT_MISMATCH", "message": f"DATAFLOW_INDEX defines {len(dataflow)} translation units, expected {config.expected_translation_units}"})
    if set(cmake) != set(dataflow):
        issues.append({
            "code": "BUILD_AUTHORITY_MISMATCH",
            "message": "CMake and DATAFLOW_INDEX translation-unit memberships differ",
            "details": {"cmake_only": sorted(set(cmake) - set(dataflow)), "dataflow_only": sorted(set(dataflow) - set(cmake))},
        })
    entries, inventory_issues = blueprint_inventory(config)
    issues.extend(inventory_issues)
    blueprint_sources = {
        entry.source_relative for entry in entries if entry.source_relative
    }
    if blueprint_sources != set(dataflow):
        issues.append({
            "code": "BLUEPRINT_MEMBERSHIP_MISMATCH",
            "message": "blueprint mappings do not exactly cover DATAFLOW_INDEX membership",
            "details": {"blueprint_only": sorted(blueprint_sources - set(dataflow)), "dataflow_without_blueprint": sorted(set(dataflow) - blueprint_sources)},
        })
    include_owners, include_issues = header_ownership(config, dataflow)
    include_headers = set(include_owners)
    issues.extend(include_issues)
    include_edges: list[dict[str, Any]] = []
    include_edge_authority = load_include_edge_authority(config)
    if include_edge_authority is not None:
        include_edges, include_edge_issues = direct_include_edges(config, dataflow)
        issues.extend(include_edge_issues)
        if include_edges != include_edge_authority["edges"]:
            issues.append({
                "code": "INCLUDE_EDGE_AUTHORITY_MISMATCH",
                "message": "live compiler-observed direct include edges differ from machine authority",
                "details": {
                    "expected_digest": include_edge_authority["topology_sha256"],
                    "actual_digest": include_topology_sha256(include_edges),
                },
            })
        declared_edges = declared_include_edges(config)
        if (
            declared_edges is None
            or declared_edges["edge_count"] != include_edge_authority["edge_count"]
            or declared_edges["topology_sha256"] != include_edge_authority["topology_sha256"]
        ):
            issues.append({
                "code": "INCLUDE_EDGE_INDEX_MISMATCH",
                "message": "PROJECT_SOURCE_INDEX direct include-edge digest/count differs from machine authority",
            })
    declared_owners = declared_header_ownership(config)
    if declared_owners is not None:
        normalized_actual = {key: set(value) for key, value in include_owners.items()}
        if normalized_actual != declared_owners:
            all_headers = sorted(set(normalized_actual) | set(declared_owners))
            details = []
            for header in all_headers:
                actual = sorted(normalized_actual.get(header, set()))
                declared = sorted(declared_owners.get(header, set()))
                if actual != declared:
                    details.append({"header": header, "declared": declared, "actual": actual})
            issues.append({
                "code": "INCLUDE_OWNERSHIP_MISMATCH",
                "message": "PROJECT_SOURCE_INDEX include ownership differs from the current compiler-scoped include graph",
                "details": details,
            })
    paired_headers = {entry.header_relative for entry in entries if entry.header_relative}
    entries_by_name = {entry.blueprint_path.name: entry for entry in entries}
    for header, owner in sorted(config.additional_header_owners.items()):
        if header not in include_headers:
            issues.append({"code": "DECLARED_HEADER_NOT_IN_CLOSURE", "message": f"declared additional header is not in the Q1 include closure: {header}", "details": {"owner": owner}})
        if not (config.blueprint_root / owner).is_file():
            issues.append({"code": "DECLARED_HEADER_OWNER_MISSING", "message": f"additional header owner blueprint is missing: {owner}", "details": {"header": header}})
        owner_entry = entries_by_name.get(owner)
        if owner_entry and owner_entry.header_relative != header:
            issues.append({
                "code": "DECLARED_HEADER_OWNER_UNVERIFIED",
                "message": f"declared owner does not carry an exact ledger for {header}",
                "details": {"owner": owner, "mapped_header": owner_entry.header_relative},
            })
    for header, filename in sorted(config.header_only_blueprints.items()):
        if header not in include_headers:
            issues.append({
                "code": "HEADER_ONLY_NOT_IN_CLOSURE",
                "message": f"configured header-only blueprint input is not in the compiled include closure: {header}",
                "details": {"blueprint": filename},
            })
        entry = entries_by_name.get(filename)
        if not entry:
            issues.append({
                "code": "HEADER_ONLY_BLUEPRINT_MISSING",
                "message": f"configured header-only blueprint is missing or invalid: {filename}",
                "details": {"header": header},
            })
        elif entry.source_relative is not None or entry.header_relative != header:
            issues.append({
                "code": "HEADER_ONLY_MAPPING_MISMATCH",
                "message": f"configured header-only blueprint mapping differs: {filename}",
                "details": {
                    "expected_header": header,
                    "actual_header": entry.header_relative,
                    "actual_source": entry.source_relative,
                },
            })
    uncovered_headers = sorted(include_headers - paired_headers)
    if uncovered_headers:
        issues.append({
            "code": "HEADER_COVERAGE_GAP",
            "message": f"{len(uncovered_headers)} local headers in the compiled include closure have no blueprint byte owner",
            "details": uncovered_headers,
        })

    records: dict[str, Any] = {}
    valid_entries: list[CorpusEntry] = []
    for entry in entries:
        ledger_issues = verify_ledger(entry.document, entry.source_path, entry.header_path)
        for issue in ledger_issues:
            details = issue.setdefault("details", {})
            if isinstance(details, dict):
                details.setdefault("record", entry.record_key)
        issues.extend(ledger_issues)
        external_raw = entry.blueprint_path.read_bytes()
        managed_raw = entry.managed_path.read_bytes()
        if external_raw != managed_raw:
            issues.append({
                "code": "BLUEPRINT_MANAGED_MISMATCH",
                "message": f"external and managed blueprints are not byte-identical: {entry.blueprint_path.name}",
                "details": {"external_sha256": sha256_bytes(external_raw), "managed_sha256": sha256_bytes(managed_raw)},
            })
        records[entry.record_key] = {
            "kind": entry.kind,
            "source": file_facts(entry.source_path) if entry.source_path else None,
            "header": file_facts(entry.header_path) if entry.header_path else None,
            "blueprint": {
                "filename": entry.blueprint_path.name,
                "artifact_id": entry.document.artifact_id,
                "revision": entry.document.revision,
                "raw_sha256": sha256_bytes(external_raw),
                "normalized_sha256": normalized_sha256(external_raw),
            },
            "managed": {
                "path": relative_posix(entry.managed_path, config.kairos_workspace),
                "raw_sha256": sha256_bytes(managed_raw),
                "normalized_sha256": normalized_sha256(managed_raw),
            },
        }
        valid_entries.append(entry)
    issues.extend(verify_project_intake_binding(
        config, translation_units=dataflow, include_owners=include_owners, records=records
    ))
    database_summary: dict[str, Any] = {}
    if verify_database:
        try:
            database_summary, database_issues = verify_database_projection(config, valid_entries)
            issues.extend(database_issues)
            for source, summary in database_summary.items():
                if source in records:
                    records[source]["database"] = summary
            if include_edge_authority is not None:
                issues.extend(verify_include_edge_database(config, include_edge_authority["edges"]))
        except Exception as exc:
            issues.append({
                "code": "DATABASE_VERIFICATION_ERROR",
                "message": f"KAIROS database verification failed closed: {exc}",
                "details": {"exception": type(exc).__name__},
            })
    authority = {
        "cmake_sha256": sha256_bytes(config.cmake_file.read_bytes()),
        "dataflow_sha256": sha256_bytes(config.dataflow_index.read_bytes()),
        "translation_units": dataflow,
        "include_headers": sorted(include_headers),
        "include_owners": {key: sorted(value) for key, value in sorted(include_owners.items())},
        **({
            "include_edges": include_edges,
            "include_topology_sha256": include_edge_authority["topology_sha256"],
        } if include_edge_authority is not None else {}),
        "paired_headers": sorted(paired_headers),
        "additional_header_owners": dict(sorted(config.additional_header_owners.items())),
        "header_only_blueprints": dict(sorted(config.header_only_blueprints.items())),
        "auxiliary_documents": {
            filename: file_facts(config.blueprint_root / filename)
            for filename in config.auxiliary_documents
        },
        "machine_authority_files": {
            relative: file_facts(config.machine_root / relative)
            for relative in config.machine_authority_files
        },
    }
    tests = dependent_test_inventory(config)
    for relative, record in tests.items():
        if relative in records:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"test companion overlaps a mapped Runtime unit: {relative}")
        if record["owner"] not in records:
            raise WorkshopError("TEST_AUTHORITY_INVALID", f"test owner is not a mapped Runtime unit: {relative}")
    if tests:
        authority["dependent_tests"] = tests
    hash_payload = {"authority": authority, "records": records}
    manifest = {
        "schema": "runtime-sync-corpus-manifest/v1",
        "created_at": utc_now(),
        "verified": not issues,
        "package_sha256": package_hash(hash_payload),
        "config_sha256": sha256_bytes(config.config_path.read_bytes()),
        "authority": authority,
        "counts": {
            "cmake_translation_units": len(cmake),
            "dataflow_translation_units": len(dataflow),
            "blueprint_files": blueprint_file_count,
            "valid_blueprints": len(entries),
            "translation_unit_blueprints": sum(entry.source_relative is not None for entry in entries),
            "header_only_blueprints": sum(entry.source_relative is None for entry in entries),
            "include_headers": len(include_headers),
            "include_edges": len(include_edges) if include_edge_authority is not None else 0,
            "paired_headers": len(paired_headers),
            "verified_database_artifacts": len(database_summary),
            "auxiliary_documents": len(config.auxiliary_documents),
            "machine_authority_files": len(config.machine_authority_files),
            "issues": len(issues),
        },
        "records": records,
        "issues": issues,
    }
    return manifest
def _translation_unit_extensions(config: WorkshopConfig) -> list[str]:
    """Return the configured translation-unit extensions, defaulting to the original set."""

    authority = config.raw.get("source_authority") or {}
    values = authority.get("translation_unit_extensions") or ["c", "cc", "cpp", "cxx", "cu"]
    return [str(value).lstrip(".") for value in values]


def _governed_include_prefix() -> str | None:
    """Prefix that marks an unresolved include as pointing into the governed source tree.

    Returns None when the governed root is the codebase root. There is then no prefix that
    separates a project include from a system or third-party one, and testing against an
    empty string would classify every unresolvable <vector> as a governed-source failure,
    which no corpus could ever pass. Losing one diagnostic is the correct trade; a corpus
    that can never verify is not a safer default, it is a broken one.
    """

    from .util import source_prefix

    return source_prefix() or None
