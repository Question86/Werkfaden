from __future__ import annotations

import os
import stat
from pathlib import Path

from .errors import KickstartError


_PATH_PAIRED = {
    "-I", "-isystem", "-iquote", "-idirafter", "-isysroot",
    "--sysroot", "-F", "-iframework", "-B", "--gcc-toolchain",
}
_PATH_ATTACHED = (
    "-I", "-isystem", "-iquote", "-idirafter", "--sysroot=",
    "-isysroot", "-F", "-iframework", "-B", "--gcc-toolchain=",
)


def is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def reject_link_components(path: Path, *, label: str = "path") -> None:
    """Reject an existing symlink/junction before a path is resolved."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor) if candidate.anchor else Path.cwd()
    start = 1 if candidate.anchor else 0
    for part in candidate.parts[start:]:
        if part in {"", "."}:
            continue
        if part == "..":
            if is_link_or_reparse(current):
                raise KickstartError(
                    "LINKED_PATH_UNSUPPORTED",
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
            raise KickstartError("PATH_UNVERIFIABLE", f"cannot inspect {label}: {current}") from exc
        if is_link_or_reparse(current):
            raise KickstartError(
                "LINKED_PATH_UNSUPPORTED",
                f"{label} contains a symlink or reparse component: {current}",
            )


def portable_path(
    value: Path,
    *,
    base: Path,
    relative_roots: tuple[Path, ...] | None = None,
) -> str:
    """Serialize a path relative to the config directory when possible.

    The Workshop loader resolves relative values against the directory that
    contains the config file.  This keeps a project and its workspace movable
    between users and checkouts while retaining absolute paths for a
    cross-volume relationship that cannot be represented relatively.
    """

    target = Path(value).resolve()
    anchor = Path(base).resolve()
    if relative_roots is not None:
        movable = False
        for root in relative_roots:
            try:
                target.relative_to(Path(root).resolve())
                movable = True
                break
            except ValueError:
                continue
        if not movable:
            return target.as_posix()
    try:
        relative = os.path.relpath(str(target), str(anchor))
    except ValueError:
        # Windows raises for different drive letters.  The loader will retain
        # this as an explicit absolute dependency instead of guessing.
        return target.as_posix()
    return relative.replace("\\", "/") or "."


def portable_probe_argv(
    argv: tuple[str, ...], *, base: Path, relative_roots: tuple[Path, ...] | None = None
) -> tuple[str, ...]:
    """Make path-valued compiler-probe arguments config-directory-relative."""

    if not argv:
        return tuple()
    result: list[str] = [argv[0]]
    values = list(argv[1:])

    def serialize(value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            return value.replace("\\", "/")
        return portable_path(path, base=base, relative_roots=relative_roots)

    index = 0
    while index < len(values):
        token = values[index]
        if token in _PATH_PAIRED and index + 1 < len(values):
            result.extend((token, serialize(values[index + 1])))
            index += 2
            continue
        matched = False
        for prefix in _PATH_ATTACHED:
            if token.startswith(prefix) and len(token) > len(prefix):
                separator = "=" if prefix.endswith("=") else ""
                base_prefix = prefix[:-1] if separator else prefix
                result.append(base_prefix + separator + serialize(token[len(prefix):]))
                matched = True
                break
        if not matched:
            result.append(token)
        index += 1
    return tuple(result)
