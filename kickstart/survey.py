from __future__ import annotations

import json
import os
import shlex
import stat
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import KickstartError
from .portable import reject_link_components


SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".cu"})
HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".cuh", ".inl", ".ipp", ".tpp", ".inc", ".def"})


@dataclass(frozen=True)
class CompilationVariant:
    argv: tuple[str, ...]
    # Effective explicit search roots after applying compiler category order.
    # quote_include_roots excludes the including file directory, which is
    # handled by the resolver itself; angle_include_roots excludes -iquote.
    quote_include_roots: tuple[Path, ...]
    angle_include_roots: tuple[Path, ...]
    idirafter_roots: tuple[Path, ...]
    # Stable union retained for backwards-compatible manifests/diagnostics.
    include_roots: tuple[Path, ...]
    directory: Path


@dataclass(frozen=True)
class CompilationUnit:
    relative_path: str
    absolute_path: Path
    directory: Path
    variants: tuple[CompilationVariant, ...]
    commands: tuple[str, ...]
    # Ordered include-root sequences are retained per distinct compiler command.
    # Resolution order is compiler semantics; sorting these paths can select a
    # different same-named header than the build actually sees.
    include_root_sequences: tuple[tuple[Path, ...], ...]
    # Stable union retained for diagnostics/manifests only. Do not use this
    # field to resolve includes.
    include_roots: tuple[Path, ...]


@dataclass(frozen=True)
class Survey:
    compile_commands: Path
    compile_commands_sha256: str
    units: tuple[CompilationUnit, ...]
    include_roots: tuple[Path, ...]
    ignored_external_include_roots: tuple[str, ...]
    source_kind: str
    cmake_file: Path | None = None
    cmake_sha256: str | None = None


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def reject_links(path: Path, root: Path, *, code: str = "LINKED_SOURCE_UNSUPPORTED") -> None:
    """Reject symlink/reparse components so a ledger path has one byte identity."""
    root_abs = Path(os.path.abspath(str(root)))
    candidate = Path(os.path.abspath(str(path)))
    try:
        relative = candidate.relative_to(root_abs)
    except ValueError as exc:
        raise KickstartError(code, f"path is outside the project root: {path}") from exc
    current = root_abs
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise KickstartError(code, f"cannot inspect path component: {current}") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise KickstartError(code, f"symlink or reparse component is not accepted: {current}")


def _validate_relative_for_authority(relative: str) -> None:
    # These characters would be ambiguous in the Workshop CMake list or the
    # DATAFLOW markdown table. Refusing them preserves an exact, parseable
    # source identity instead of silently escaping or rewriting a path.
    if any(char in relative for char in ("\n", "\r", "`", "|", "#")):
        raise KickstartError(
            "UNREPRESENTABLE_SOURCE_PATH",
            f"source path contains a character not representable by the authority ledgers: {relative!r}",
        )


def _normalized_relative(root: Path, path: Path) -> tuple[str, Path]:
    try:
        Path(os.path.abspath(str(path))).relative_to(Path(os.path.abspath(str(root))))
    except ValueError as exc:
        raise KickstartError(
            "SOURCE_OUTSIDE_PROJECT",
            f"compile_commands source is outside project_root: {path}",
        ) from exc
    reject_links(path, root)
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise KickstartError(
            "SOURCE_OUTSIDE_PROJECT",
            f"compile_commands source is missing or outside project_root: {path}",
        ) from exc
    if not resolved.is_file():
        raise KickstartError("SOURCE_NOT_FILE", f"compile_commands source is not a file: {resolved}")
    if resolved.suffix.casefold() not in SOURCE_SUFFIXES:
        raise KickstartError(
            "UNSUPPORTED_TRANSLATION_UNIT",
            f"unsupported translation-unit suffix for {relative}; supported: {sorted(SOURCE_SUFFIXES)}",
        )
    _validate_relative_for_authority(relative)
    return relative, resolved


def _regular_directory(path: Path, *, label: str) -> Path:
    reject_link_components(path, label=label)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise KickstartError("COMPILE_DIRECTORY_MISSING", f"{label} does not exist: {path}") from exc
    reject_link_components(resolved, label=label)
    if not resolved.is_dir():
        raise KickstartError("COMPILE_DIRECTORY_INVALID", f"{label} is not a directory: {resolved}")
    return resolved


def _command_tokens(entry: dict[str, Any], *, path: Path) -> list[str]:
    arguments = entry.get("arguments")
    command = entry.get("command")
    if isinstance(arguments, list):
        if not arguments or any(not isinstance(value, str) for value in arguments):
            raise KickstartError("COMPILE_COMMAND_INVALID", f"arguments must be a non-empty string array: {path}")
        return list(arguments)
    if isinstance(command, str) and command.strip():
        try:
            # CMake commonly stores a Windows command as one string. Use the
            # platform command-line parser there so quotes around paths with
            # spaces are removed without treating drive backslashes as escapes.
            if os.name == "nt":
                import ctypes

                argc = ctypes.c_int()
                parser = ctypes.windll.shell32.CommandLineToArgvW
                parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
                parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
                argv = parser(command, ctypes.byref(argc))
                if not argv:
                    raise ValueError("CommandLineToArgvW returned no arguments")
                try:
                    return [argv[index] for index in range(argc.value)]
                finally:
                    ctypes.windll.kernel32.LocalFree(argv)
            return shlex.split(command, posix=True)
        except ValueError as exc:
            raise KickstartError("COMPILE_COMMAND_INVALID", f"cannot parse command in {path}: {exc}") from exc
    raise KickstartError("COMPILE_COMMAND_MISSING", f"entry has neither arguments nor command: {path}")




def compiler_probe_argv(
    argv: tuple[str, ...], *, directory: Path | None = None, project_root: Path | None = None
) -> tuple[str, ...]:
    """Reduce one compiler command to a path-stable implicit-header probe argv.

    Path-valued search/toolchain flags are absolutized against the compiler command's
    working directory. Project source/output operands are omitted. This lets the caller
    run the probe from a stable workspace directory without depending on a temporary
    CMake build directory that may no longer exist.
    """
    if not argv:
        return tuple()
    compiler = argv[0]
    name = Path(compiler).name.lower()
    if not (
        name in {"c++", "cc", "g++", "gcc", "clang", "clang++"}
        or "clang" in name
        or "g++" in name
        or name.startswith("gcc")
    ):
        return tuple()
    compiler_path = Path(compiler)
    if compiler_path.is_absolute():
        resolved_compiler = compiler_path.resolve()
    elif any(sep in compiler for sep in ("/", "\\")):
        if directory is None:
            return tuple()
        resolved_compiler = (directory / compiler_path).resolve()
    else:
        located = shutil.which(compiler)
        if not located:
            return tuple()
        resolved_compiler = Path(located).resolve()
    if project_root is not None:
        try:
            resolved_compiler.relative_to(project_root.resolve())
        except ValueError:
            pass
        else:
            # Never execute a compiler/wrapper supplied from the project being
            # inspected. Untrusted compiler commands remain retained evidence,
            # but probe-dependent questions fail closed instead of executing it.
            return tuple()
    compiler = str(resolved_compiler)

    def absolute_path(value: str) -> str:
        path = Path(value)
        if directory is not None and not path.is_absolute():
            path = directory / path
        return str(path.resolve()) if directory is not None else str(path)

    search_args: list[str] = []
    values = list(argv[1:])
    index = 0
    path_paired = {
        "-I", "-isystem", "-iquote", "-idirafter", "-isysroot",
        "--sysroot", "-F", "-iframework", "-B", "--gcc-toolchain",
    }
    scalar_paired = {"-target"}
    path_attached = (
        "-I", "-isystem", "-iquote", "-idirafter", "--sysroot=",
        "-isysroot", "-F", "-iframework", "-B", "--gcc-toolchain=",
    )
    scalar_attached = ("-target=",)
    standalone = {"-nostdinc", "-nostdinc++", "-m32", "-m64"}
    while index < len(values):
        token = values[index]
        if token in path_paired and index + 1 < len(values):
            search_args.extend((token, absolute_path(values[index + 1])))
            index += 2
            continue
        if token in scalar_paired and index + 1 < len(values):
            search_args.extend((token, values[index + 1]))
            index += 2
            continue
        matched = False
        for prefix in path_attached:
            if token.startswith(prefix) and len(token) > len(prefix):
                separator = "=" if prefix.endswith("=") else ""
                base = prefix[:-1] if separator else prefix
                search_args.append(base + separator + absolute_path(token[len(prefix):]))
                matched = True
                break
        if matched:
            index += 1
            continue
        if token.startswith(scalar_attached) or token in standalone:
            search_args.append(token)
        index += 1
    return (compiler, *search_args)

def _include_values(
    tokens: Iterable[str], directory: Path
) -> tuple[list[Path], list[Path], list[Path], list[Path], list[str]]:
    """Parse explicit include roots into compiler search categories.

    GCC/Clang do not search include roots in raw command-token order.  For a
    quoted include the explicit categories are ``-iquote`` -> ``-I`` ->
    ``-isystem`` -> ``-idirafter``; angle includes skip ``-iquote``.  MSVC
    ``/I`` roots are treated as user roots and ``/external:I`` as system roots.

    Returns ``(quote_roots, angle_roots, idirafter_roots, all_roots, external)``.
    The source/header directory for a quoted include is intentionally not part
    of these arrays; the resolver owns that first search step.
    """
    iquote: list[Path] = []
    user: list[Path] = []
    system: list[Path] = []
    after: list[Path] = []
    external: list[str] = []
    values = list(tokens)

    def append_root(raw: str, category: str) -> None:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = directory / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            external.append(str(candidate))
            return
        if not resolved.is_dir():
            external.append(str(resolved))
            return
        target = {
            "iquote": iquote,
            "user": user,
            "system": system,
            "after": after,
        }[category]
        if resolved not in target:
            target.append(resolved)

    index = 0
    paired = {
        "-iquote": "iquote",
        "-I": "user",
        "/I": "user",
        "-isystem": "system",
        "/external:I": "system",
        "-idirafter": "after",
    }
    attached = (
        ("-iquote", "iquote"),
        ("-isystem", "system"),
        ("-idirafter", "after"),
        ("/external:I", "system"),
        ("-I", "user"),
        ("/I", "user"),
    )
    while index < len(values):
        token = values[index]
        if token in paired:
            if index + 1 < len(values):
                append_root(values[index + 1], paired[token])
                index += 2
                continue
            index += 1
            continue
        matched = False
        for prefix, category in attached:
            if token.startswith(prefix) and len(token) > len(prefix):
                append_root(token[len(prefix):], category)
                matched = True
                break
        index += 1
        if matched:
            continue

    quote = [*iquote, *user, *system, *after]
    angle = [*user, *system, *after]
    all_roots: list[Path] = []
    for root in [*quote, *angle]:
        if root not in all_roots:
            all_roots.append(root)
    return quote, angle, after, all_roots, external


def survey_compile_commands(
    compile_commands: Path,
    project_root: Path,
    *,
    source_kind: str = "compile_commands",
    cmake_file: Path | None = None,
    cmake_sha256: str | None = None,
) -> Survey:
    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {project_root}")
    compile_commands = Path(compile_commands)
    reject_link_components(compile_commands, label="compile_commands")
    compile_commands = compile_commands.resolve()
    if not compile_commands.is_file():
        raise KickstartError("COMPILE_COMMANDS_MISSING", f"compile_commands.json is missing: {compile_commands}")
    if cmake_file is not None:
        cmake_file = Path(cmake_file)
        reject_link_components(cmake_file, label="CMake file")
        cmake_file = cmake_file.resolve()
    try:
        raw = compile_commands.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KickstartError("COMPILE_COMMANDS_INVALID", f"cannot read compile_commands.json: {compile_commands}") from exc
    if not isinstance(payload, list) or not payload:
        raise KickstartError("COMPILE_COMMANDS_EMPTY", f"compile_commands.json must be a non-empty array: {compile_commands}")

    by_relative: dict[str, dict[str, Any]] = {}
    ignored_external: set[str] = set()
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise KickstartError("COMPILE_ENTRY_INVALID", f"compile_commands entry {index} is not an object")
        file_value = item.get("file")
        directory_value = item.get("directory")
        if not isinstance(file_value, str) or not file_value.strip():
            raise KickstartError("COMPILE_FILE_MISSING", f"compile_commands entry {index} has no file")
        if not isinstance(directory_value, str) or not directory_value.strip():
            raise KickstartError("COMPILE_DIRECTORY_MISSING", f"compile_commands entry {index} has no directory")
        directory_path = Path(directory_value)
        if not directory_path.is_absolute():
            directory_path = compile_commands.parent / directory_path
        directory = _regular_directory(directory_path, label=f"compile directory for entry {index}")
        source = Path(file_value)
        if not source.is_absolute():
            source = directory / source
        relative, absolute = _normalized_relative(project_root, source)
        tokens = _command_tokens(item, path=compile_commands)
        quote_roots, angle_roots, idirafter_roots, roots, external = _include_values(tokens, directory)
        ignored_external.update(external)
        record = by_relative.setdefault(
            relative,
            {"absolute": absolute, "directory": directory, "commands": [], "root_sequences": [], "variants": []},
        )
        record["commands"].append(" ".join(tokens))
        root_sequence = tuple(roots)
        if root_sequence not in record["root_sequences"]:
            record["root_sequences"].append(root_sequence)
        variant = CompilationVariant(
            argv=tuple(tokens),
            quote_include_roots=tuple(quote_roots),
            angle_include_roots=tuple(angle_roots),
            idirafter_roots=tuple(idirafter_roots),
            include_roots=root_sequence,
            directory=directory,
        )
        if variant not in record["variants"]:
            record["variants"].append(variant)
        for root_path in roots:
            if not _inside(root_path, project_root):
                ignored_external.add(str(root_path))

    units_list: list[CompilationUnit] = []
    for relative, record in sorted(by_relative.items(), key=lambda item: item[0].casefold()):
        ordered_union: list[Path] = []
        for sequence in record["root_sequences"]:
            for root_path in sequence:
                if root_path not in ordered_union:
                    ordered_union.append(root_path)
        units_list.append(
            CompilationUnit(
                relative_path=relative,
                absolute_path=record["absolute"],
                directory=record["directory"],
                variants=tuple(record["variants"]),
                commands=tuple(sorted(set(record["commands"]))),
                include_root_sequences=tuple(record["root_sequences"] or [tuple()]),
                include_roots=tuple(ordered_union),
            )
        )
    units = tuple(units_list)
    local_union: list[Path] = []
    for unit in units:
        for sequence in unit.include_root_sequences:
            for root_path in sequence:
                if _inside(root_path, project_root) and root_path not in local_union:
                    local_union.append(root_path)
    include_roots = tuple(local_union)
    return Survey(
        compile_commands=compile_commands,
        compile_commands_sha256=_sha256(raw),
        units=units,
        include_roots=include_roots,
        ignored_external_include_roots=tuple(sorted(ignored_external)),
        source_kind=source_kind,
        cmake_file=cmake_file,
        cmake_sha256=cmake_sha256,
    )


def compiler_context_payload(survey: Survey, project_root: Path) -> dict[str, Any]:
    """Path-stable compiler context for migration comparison.

    Raw compile_commands bytes remain retained evidence. This payload normalizes paths
    inside the project root so an isolated candidate clone does not look like a build
    authority change merely because its absolute checkout directory differs. External
    include roots stay absolute because changing one can change header resolution.
    """
    root = project_root.resolve()
    root_strings = {str(root), root.as_posix()}

    def normalize_text(value: str) -> str:
        result = value
        for marker in sorted(root_strings, key=len, reverse=True):
            result = result.replace(marker, "${PROJECT_ROOT}")
            result = result.replace(marker.replace("/", "\\"), "${PROJECT_ROOT}")
        return result.replace("\\", "/")

    def normalize_root(path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            return "external:" + resolved.as_posix()
        return "project:" + relative

    return {
        "translation_units": [
            {
                "relative_path": unit.relative_path,
                "variants": [
                    {
                        "argv": [normalize_text(token) for token in variant.argv],
                        "include_roots": [normalize_root(path) for path in variant.include_roots],
                        "quote_include_roots": [normalize_root(path) for path in variant.quote_include_roots],
                        "angle_include_roots": [normalize_root(path) for path in variant.angle_include_roots],
                        "idirafter_roots": [normalize_root(path) for path in variant.idirafter_roots],
                        "directory": (
                            "project:" + variant.directory.resolve().relative_to(root).as_posix()
                            if _inside(variant.directory, root)
                            else "external-build-directory"
                        ),
                    }
                    for variant in unit.variants
                ],
            }
            for unit in survey.units
        ]
    }


def compiler_context_sha256(survey: Survey, project_root: Path) -> str:
    import json
    payload = compiler_context_payload(survey, project_root)
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
