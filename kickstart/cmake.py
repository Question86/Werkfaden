from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import KickstartError
from .portable import reject_link_components


@dataclass(frozen=True)
class CMakeConfigure:
    cmake_file: Path
    build_directory: Path
    compile_commands: Path
    stdout: str
    stderr: str
    temporary_build: bool


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _reject_links_before_tool(project_root: Path) -> None:
    """Fail before exposing a cloned tree to CMake if path identity is ambiguous.

    The compiler-backed intake already rejects symlink/reparse source identities.  CMake
    must be held to the same boundary *before* it executes so a source-tree link cannot
    give configure-time logic an unintended path outside the isolated clone.
    """
    for current, directory_names, file_names in os.walk(project_root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in [*directory_names, *file_names]:
            candidate = current_path / name
            if _is_link_or_reparse(candidate):
                raise KickstartError(
                    "CMAKE_SOURCE_LINK_UNSUPPORTED",
                    f"CMake isolation refuses symlink/reparse paths in the observed project: {candidate}",
                )


def _reject_existing_link_components(path: Path, *, label: str) -> None:
    """Refuse an external output/tool path that traverses a link or junction."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor) if candidate.anchor else Path.cwd()
    start = 1 if candidate.anchor else 0
    for part in candidate.parts[start:]:
        if part in {"", "."}:
            continue
        if part == "..":
            if _is_link_or_reparse(current):
                raise KickstartError(
                    "CMAKE_PATH_LINK_UNSAFE",
                    f"{label} contains a symlink or reparse component: {current}",
                )
            current = current.parent
            continue
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        except (OSError, ValueError) as exc:
            raise KickstartError("CMAKE_PATH_UNVERIFIABLE", f"cannot inspect {label}: {current}") from exc
        if _is_link_or_reparse(current):
            raise KickstartError(
                "CMAKE_PATH_LINK_UNSAFE",
                f"{label} contains a symlink or reparse component: {current}",
            )


def _resolve_external_cmake(value: str, project_root: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KickstartError("CMAKE_EXECUTABLE_INVALID", "cmake executable must be a non-empty command or path")
    explicit = Path(value)
    if explicit.is_absolute() or any(separator in value for separator in ("/", "\\")):
        candidate = explicit if explicit.is_absolute() else Path.cwd() / explicit
    else:
        located = shutil.which(value)
        if not located:
            raise KickstartError("CMAKE_EXECUTABLE_MISSING", f"CMake executable was not found on PATH: {value}")
        candidate = Path(located)
    _reject_existing_link_components(candidate, label="CMake executable")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise KickstartError("CMAKE_EXECUTABLE_MISSING", f"CMake executable is not readable: {candidate}") from exc
    if _inside(resolved, project_root):
        raise KickstartError(
            "CMAKE_EXECUTABLE_PROJECT_LOCAL",
            "CMake executable must resolve outside the inspected project; project-local wrappers are not executed",
        )
    if not resolved.is_file():
        raise KickstartError("CMAKE_EXECUTABLE_INVALID", f"CMake executable is not a file: {resolved}")
    return str(resolved)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _remap_clone_text(value: str, clone_root: Path, project_root: Path) -> str:
    replacements = (
        (str(clone_root), str(project_root)),
        (clone_root.as_posix(), project_root.as_posix()),
        (str(clone_root).replace("/", "\\"), str(project_root).replace("/", "\\")),
    )
    result = value
    for old, new in sorted(set(replacements), key=lambda item: len(item[0]), reverse=True):
        if old:
            result = result.replace(old, new)
    return result


def _remap_compile_value(value: Any, clone_root: Path, project_root: Path) -> Any:
    if isinstance(value, str):
        return _remap_clone_text(value, clone_root, project_root)
    if isinstance(value, list):
        return [_remap_compile_value(item, clone_root, project_root) for item in value]
    if isinstance(value, dict):
        return {key: _remap_compile_value(item, clone_root, project_root) for key, item in value.items()}
    return value


def _normalize_compile_commands(path: Path, clone_root: Path, project_root: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KickstartError("COMPILE_COMMANDS_INVALID", f"CMake emitted invalid compile_commands.json: {path}") from exc
    if not isinstance(payload, list) or not payload:
        raise KickstartError("COMPILE_COMMANDS_EMPTY", "CMake emitted an empty compiler command database")
    normalized = _remap_compile_value(payload, clone_root, project_root)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configure_compile_commands(
    project_root: Path,
    cmake_file: Path,
    *,
    cmake_executable: str = "cmake",
    build_directory: Path | None = None,
    timeout_seconds: int = 300,
) -> CMakeConfigure:
    """Produce compiler authority without exposing the governed source tree to CMake.

    CMake configure is potentially mutating project logic.  The governed source tree is
    therefore observed only by KAIROS itself; CMake receives a disposable byte clone as
    its source directory.  Its compile database is then path-remapped back to the exact
    original snapshot identities before the normal compiler-authority survey consumes it.
    """
    project_root = Path(project_root)
    reject_link_components(project_root, label="project root")
    project_root = project_root.resolve()
    cmake_file = Path(cmake_file)
    reject_link_components(cmake_file, label="CMake file")
    cmake_file = cmake_file.resolve()
    if not project_root.is_dir():
        raise KickstartError("PROJECT_ROOT_INVALID", f"project_root is not a directory: {project_root}")
    try:
        cmake_relative = cmake_file.relative_to(project_root)
    except ValueError as exc:
        raise KickstartError("CMAKE_OUTSIDE_PROJECT", f"CMake file is outside project_root: {cmake_file}") from exc
    if not cmake_file.is_file():
        raise KickstartError("CMAKE_FILE_MISSING", f"CMake file is missing: {cmake_file}")

    _reject_links_before_tool(project_root)
    cmake_command = _resolve_external_cmake(cmake_executable, project_root)

    temporary = build_directory is None
    if temporary:
        build_directory = Path(tempfile.mkdtemp(prefix="kairos-cmake-build-"))
    else:
        _reject_existing_link_components(build_directory, label="CMake build directory")
        build_directory = build_directory.resolve()
        if _inside(build_directory, project_root):
            raise KickstartError(
                "CMAKE_BUILD_DIRECTORY_LIVE",
                "CMake build_directory must not be inside the governed project; use an isolated external build directory",
            )
        build_directory.mkdir(parents=True, exist_ok=True)
        _reject_existing_link_components(build_directory, label="CMake build directory")

    clone_parent = Path(tempfile.mkdtemp(prefix="kairos-cmake-source-"))
    clone_root = clone_parent / "project"
    try:
        shutil.copytree(project_root, clone_root, symlinks=False)
        clone_cmake_file = clone_root / cmake_relative
        if not clone_cmake_file.is_file():
            raise KickstartError("CMAKE_CLONE_INVALID", f"isolated CMake source is missing after clone: {cmake_relative}")
        source_directory = clone_cmake_file.parent
        command = [
            cmake_command,
            "-S",
            str(source_directory),
            "-B",
            str(build_directory),
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
        # Visual Studio generators do not emit compile_commands.json. Prefer a
        # generator with a compiler-command database when one is available, while
        # retaining the platform default as the final fallback for toolchains that
        # provide no Make/Ninja driver.
        if shutil.which("ninja"):
            command[1:1] = ["-G", "Ninja"]
        elif shutil.which("mingw32-make"):
            command[1:1] = ["-G", "MinGW Makefiles"]
        try:
            completed = subprocess.run(
                command,
                cwd=clone_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KickstartError(
                "CMAKE_CONFIGURE_FAILED",
                f"could not execute isolated CMake configure: {exc}",
                details={"command": command, "source_isolated": True},
            ) from exc
        if completed.returncode != 0:
            raise KickstartError(
                "CMAKE_CONFIGURE_FAILED",
                f"isolated CMake configure exited with status {completed.returncode}",
                details={
                    "command": command,
                    "source_isolated": True,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
            )
        compile_commands = build_directory / "compile_commands.json"
        if not compile_commands.is_file():
            raise KickstartError(
                "COMPILE_COMMANDS_MISSING",
                "isolated CMake configure succeeded but did not emit compile_commands.json; "
                "the project must enable a compiler-backed source authority",
                details={"build_directory": str(build_directory), "source_isolated": True},
            )
        _normalize_compile_commands(compile_commands, clone_root, project_root)
        return CMakeConfigure(
            cmake_file=cmake_file,
            build_directory=build_directory,
            compile_commands=compile_commands,
            stdout=completed.stdout,
            stderr=completed.stderr,
            temporary_build=temporary,
        )
    finally:
        shutil.rmtree(clone_parent, ignore_errors=True)
