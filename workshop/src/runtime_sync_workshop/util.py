from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class WorkshopError(RuntimeError):
    """A deterministic, user-actionable workshop failure."""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details is not None:
            payload["details"] = self.details
        return payload


def is_link_or_reparse(path: Path) -> bool:
    """Return whether *path* is a symlink or Windows reparse point.

    ``Path.is_symlink`` alone does not cover junctions and other reparse points
    on Windows.  The latter are deliberately treated as linked paths because a
    fail-closed authority must not silently follow a path outside its declared
    byte boundary.
    """

    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def reject_link_components(path: Path, *, label: str = "path") -> None:
    """Reject existing symlink/reparse components without resolving through them."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor) if candidate.anchor else Path.cwd()
    parts = candidate.parts
    start = 1 if candidate.anchor else 0
    for part in parts[start:]:
        if part in {"", "."}:
            continue
        if part == "..":
            if is_link_or_reparse(current):
                raise WorkshopError(
                    "PATH_LINK_UNSAFE",
                    f"{label} contains a symlink or reparse component: {current}",
                )
            current = current.parent
            continue
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            # Missing descendants will be created or reported by the caller;
            # there cannot be a hidden existing link below a missing component.
            break
        except (OSError, ValueError) as exc:
            raise WorkshopError("PATH_UNVERIFIABLE", f"cannot inspect {label}: {current}") from exc
        if is_link_or_reparse(current):
            raise WorkshopError(
                "PATH_LINK_UNSAFE",
                f"{label} contains a symlink or reparse component: {current}",
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def normalized_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalized_sha256(value: bytes) -> str:
    return sha256_text(normalized_text(value.decode("utf-8")))


def logical_lines(value: bytes) -> tuple[list[str], bool]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkshopError("NON_UTF8_SOURCE", f"file is not strict UTF-8: {exc}") from exc
    text = normalized_text(text)
    eof_newline = text.endswith("\n")
    if not text:
        return [], False
    lines = text.split("\n")
    if eof_newline:
        lines.pop()
    return lines, eof_newline


def file_facts(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    lines, eof_newline = logical_lines(raw)
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n")
    bare_cr_count = raw.count(b"\r") - crlf_count
    if lf_count == 0 and bare_cr_count == 0:
        newline_style = "none"
    elif crlf_count == lf_count and bare_cr_count == 0:
        newline_style = "CRLF"
    elif crlf_count == 0 and bare_cr_count == 0:
        newline_style = "LF"
    else:
        newline_style = "mixed"
    return {
        "path": path.as_posix(),
        "sha256": sha256_bytes(raw),
        "byte_count": len(raw),
        "line_count": len(lines),
        "eof_newline": eof_newline,
        "newline_style": newline_style,
        "logical_sha256": sha256_text("\n".join(lines)),
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_hash(value: Any) -> str:
    return sha256_text(canonical_json(value))


def read_json(path: Path) -> Any:
    reject_link_components(path, label="JSON input")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkshopError("INVALID_JSON", f"cannot read JSON {path}: {exc}") from exc


def atomic_write_bytes(path: Path, value: bytes) -> None:
    reject_link_components(path.parent, label="atomic-write parent")
    if path.exists() or path.is_symlink():
        reject_link_components(path, label="atomic-write target")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def confined(root: Path, value: str | Path, *, must_exist: bool = True) -> Path:
    root = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkshopError("PATH_ESCAPE", f"path escapes configured root {root}: {candidate}") from exc
    return candidate


def relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise WorkshopError("PATH_ESCAPE", f"{path} is outside {root}") from exc


def copy_exact(source: Path, destination: Path) -> None:
    reject_link_components(source, label="copy source")
    reject_link_components(destination.parent, label="copy destination parent")
    if destination.exists() or destination.is_symlink():
        reject_link_components(destination, label="copy destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if source.read_bytes() != destination.read_bytes():
        raise WorkshopError("COPY_VERIFY_FAILED", f"copy is not byte-exact: {source} -> {destination}")


def sqlite_snapshot(source: Path, destination: Path) -> None:
    reject_link_components(source, label="SQLite source")
    reject_link_components(destination.parent, label="SQLite snapshot parent")
    if destination.exists() or destination.is_symlink():
        reject_link_components(destination, label="SQLite snapshot destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    input_connection = sqlite3.connect(source_uri, uri=True)
    output_connection = sqlite3.connect(destination)
    try:
        input_connection.backup(output_connection)
        row = output_connection.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise WorkshopError("SQLITE_SNAPSHOT_INVALID", f"snapshot integrity check failed: {row}")
    finally:
        output_connection.close()
        input_connection.close()


def deterministic_tree_hash(root: Path, relative_paths: Iterable[str]) -> str:
    rows: list[dict[str, str]] = []
    for relative in sorted(set(relative_paths)):
        path = confined(root, relative)
        rows.append({"path": relative.replace("\\", "/"), "sha256": sha256_bytes(path.read_bytes())})
    return package_hash(rows)
_SOURCE_PREFIX = "runtime/"


def set_source_prefix(value: str) -> None:
    """Bind the governed source-root prefix for this process.

    Called once by the configuration loader with runtime_root relative to codebase_root.
    An empty value means the governed root is the codebase root itself.
    """

    global _SOURCE_PREFIX
    normalized = str(value).replace("\\", "/").strip("/")
    _SOURCE_PREFIX = f"{normalized}/" if normalized and normalized != "." else ""


def source_prefix() -> str:
    """Return the governed source-root prefix, including its trailing slash."""

    return _SOURCE_PREFIX
