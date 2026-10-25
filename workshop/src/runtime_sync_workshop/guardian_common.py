from __future__ import annotations

import difflib
import json
import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .util import WorkshopError, atomic_write_json, canonical_json, reject_link_components, sha256_bytes, sha256_text, utc_now

SCHEMA = "werkfaden-guardian/v2"
PATCH_SCOPE_SCHEMA = "werkfaden-patch-scope/v1"
FRESH_RUN_SCHEMA = "werkfaden-fresh-run-receipt/v1"
MAX_MEMORY_BYTES = 8 * 1024 * 1024
MAX_MEMORY_PASSAGE_BYTES = 32 * 1024
EDIT_CONTEXT_LINES = 40
PRE_PATCH_STEPS = (
    "HYPOTHESIS",
    "FALSIFIER_1",
    "FALSIFIER_2",
    "MAP",
    "COUNTERPROBE",
    "EXACT_SOURCE",
    "PROVE",
)


def guardian_required(raw_config: dict[str, Any]) -> bool:
    configured = bool(raw_config.get("guardian_required", False))
    forced = os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold()
    return configured or forced in {"1", "true", "yes", "on"}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_rel(value: str, *, label: str) -> str:
    normalized = str(value).replace("\\", "/").strip()
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise WorkshopError("GUARDIAN_SCOPE_INVALID", f"{label} must be a safe project-relative path: {value!r}")
    return normalized


def _normalize_paths(values: Iterable[str], *, label: str, allow_empty: bool = False) -> list[str]:
    result = sorted({_safe_rel(value, label=label) for value in values if str(value).strip()})
    if not result and not allow_empty:
        raise WorkshopError("GUARDIAN_SCOPE_EMPTY", f"{label} requires at least one path")
    return result


def _memory_facts(memory_file: Path) -> dict[str, Any]:
    path = Path(memory_file)
    reject_link_components(path, label="canonical Memory")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise WorkshopError("GUARDIAN_MEMORY_MISSING", f"canonical Memory file is missing: {path}") from exc
    reject_link_components(resolved, label="canonical Memory")
    if not resolved.is_file():
        raise WorkshopError("GUARDIAN_MEMORY_MISSING", f"canonical Memory file is missing: {resolved}")
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkshopError("GUARDIAN_MEMORY_INVALID", "canonical Memory must be a single-link regular file")
    if info.st_size <= 0 or info.st_size > MAX_MEMORY_BYTES:
        raise WorkshopError("GUARDIAN_MEMORY_INVALID", f"canonical Memory must be between 1 and {MAX_MEMORY_BYTES} bytes")
    raw = resolved.read_bytes()
    return {"path": str(resolved), "size": int(info.st_size), "sha256": sha256_bytes(raw)}


def _passage(text: str, selector: str) -> dict[str, Any]:
    selector = selector.strip()
    if not selector or len(selector) > 512:
        raise WorkshopError("GUARDIAN_MEMORY_SELECTOR_INVALID", "Memory selector must contain 1 to 512 characters")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Build stable Markdown sections. A selector is admissible only when it resolves to
    # exactly one section; multiple literal hits inside that same section are fine.
    headings: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level and len(stripped) > level and stripped[level:level + 1].isspace():
            headings.append((index, level))
    sections: list[tuple[int, int]] = []
    if headings:
        for position, (start, level) in enumerate(headings):
            end = len(lines)
            for candidate, candidate_level in headings[position + 1:]:
                if candidate_level <= level:
                    end = candidate
                    break
            sections.append((start, end))
    else:
        sections.append((0, len(lines)))

    selector_cf = selector.casefold()
    matching: list[tuple[int, int]] = []
    for start, end in sections:
        if any(selector_cf in line.casefold() for line in lines[start:end]):
            matching.append((start, end))
    # De-duplicate nested matches by preferring the deepest/smallest section that contains
    # every hit. This makes a code repeated inside a parent heading resolve to its local block.
    if len(matching) > 1:
        smallest = sorted(matching, key=lambda item: (item[1] - item[0], -item[0]))[0]
        hits = [index for index, line in enumerate(lines) if selector_cf in line.casefold()]
        if hits and all(smallest[0] <= index < smallest[1] for index in hits):
            matching = [smallest]
    if not matching:
        raise WorkshopError("GUARDIAN_MEMORY_SELECTOR_MISSING", f"Memory selector not found: {selector}")
    if len(matching) != 1:
        raise WorkshopError(
            "GUARDIAN_MEMORY_SELECTOR_AMBIGUOUS",
            f"Memory selector resolves to multiple sections: {selector}",
            details={"sections": [{"start_line": a + 1, "end_line": b} for a, b in matching[:16]]},
        )
    start, end = matching[0]
    raw = ("\n".join(lines[start:end]).strip() + "\n").encode("utf-8")
    if len(raw) > MAX_MEMORY_PASSAGE_BYTES:
        raise WorkshopError(
            "GUARDIAN_MEMORY_PASSAGE_TOO_LARGE",
            f"selected Memory passage exceeds {MAX_MEMORY_PASSAGE_BYTES} bytes; choose a narrower selector",
        )
    return {
        "selector": selector,
        "start_line": start + 1,
        "end_line": end,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "text": raw.decode("utf-8"),
    }


def _normalize_scope(raw: dict[str, Any] | None, fallback_sources: Iterable[str] = ()) -> dict[str, Any]:
    if raw is None:
        return {
            "schema": PATCH_SCOPE_SCHEMA,
            "normal": {
                "sources": _normalize_paths(fallback_sources, label="PROVE source"),
                "headers": [],
                "dependent_tests": [],
            },
            "auxiliary": {"documents": []},
            "source_set": {"operations": []},
            "authority": {"add_headers": [], "remove_headers": []},
        }
    if raw.get("schema") != PATCH_SCOPE_SCHEMA:
        raise WorkshopError("GUARDIAN_SCOPE_SCHEMA_INVALID", f"scope schema must be {PATCH_SCOPE_SCHEMA}")
    normal = raw.get("normal") or {}
    auxiliary = raw.get("auxiliary") or {}
    source_set = raw.get("source_set") or {}
    authority = raw.get("authority") or {}
    operations: list[dict[str, str]] = []
    for item in source_set.get("operations") or []:
        if not isinstance(item, dict) or item.get("op") not in {"add", "remove", "rename"}:
            raise WorkshopError("GUARDIAN_SCOPE_INVALID", "source_set operations require op=add|remove|rename")
        row = {"op": str(item["op"]), "path": _safe_rel(str(item.get("path", "")), label="source-set path")}
        if row["op"] == "rename":
            row["to"] = _safe_rel(str(item.get("to", "")), label="source-set rename target")
        operations.append(row)
    result = {
        "schema": PATCH_SCOPE_SCHEMA,
        "normal": {
            "sources": _normalize_paths(normal.get("sources") or [], label="normal source", allow_empty=True),
            "headers": _normalize_paths(normal.get("headers") or [], label="normal header", allow_empty=True),
            "dependent_tests": _normalize_paths(normal.get("dependent_tests") or [], label="dependent test", allow_empty=True),
        },
        "auxiliary": {
            "documents": _normalize_paths(auxiliary.get("documents") or [], label="auxiliary document", allow_empty=True),
        },
        "source_set": {"operations": sorted(operations, key=lambda item: (item["op"], item["path"], item.get("to", "")))},
        "authority": {
            "add_headers": _normalize_paths(authority.get("add_headers") or [], label="authority added header", allow_empty=True),
            "remove_headers": _normalize_paths(authority.get("remove_headers") or [], label="authority removed header", allow_empty=True),
        },
    }
    if not any((
        result["normal"]["sources"], result["normal"]["headers"], result["normal"]["dependent_tests"],
        result["auxiliary"]["documents"], result["source_set"]["operations"],
        result["authority"]["add_headers"], result["authority"]["remove_headers"],
    )):
        raise WorkshopError("GUARDIAN_SCOPE_EMPTY", "PROVE patch scope is empty")
    return result


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()



__all__ = ['SCHEMA', 'PATCH_SCOPE_SCHEMA', 'FRESH_RUN_SCHEMA', 'MAX_MEMORY_BYTES', 'MAX_MEMORY_PASSAGE_BYTES', 'EDIT_CONTEXT_LINES', 'PRE_PATCH_STEPS', 'guardian_required', '_parse_time', '_safe_rel', '_normalize_paths', '_memory_facts', '_passage', '_normalize_scope', '_file_lock', 'json', 'os', 'sqlite3', 'stat', 'uuid', 'contextmanager', 'datetime', 'timezone', 'Path', 'Any', 'Iterable', 'Iterator', 'difflib', 'WorkshopError', 'atomic_write_json', 'canonical_json', 'reject_link_components', 'sha256_bytes', 'sha256_text', 'utc_now']
