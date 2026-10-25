from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def json_dumps(value: Any, *, pretty: bool = True) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json_dumps(value))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_relative(path: Path, workspace: Path) -> str:
    resolved_workspace = workspace.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_workspace).as_posix()
    except ValueError as exc:
        raise ValueError(f"path is outside workspace: {resolved_path}") from exc


def resolve_workspace_path(workspace: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError(f"reference path must be workspace-relative: {relative!r}")
    parts = Path(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe workspace-relative path: {relative!r}")
    candidate = (workspace / Path(*parts)).resolve()
    workspace_relative(candidate, workspace)
    return candidate


def prune_oldest_files(directory: Path, *, suffix: str, keep: int) -> list[str]:
    if keep < 1:
        raise ValueError("retention must keep at least one file")
    if not directory.exists():
        return []
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.name.endswith(suffix)),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    removed: list[str] = []
    for path in files[keep:]:
        path.unlink()
        removed.append(path.name)
    return removed
