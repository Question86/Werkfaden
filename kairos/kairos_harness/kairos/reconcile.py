from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import MAX_MANAGED_DOCUMENTS, MAX_MANAGED_SOURCE_BYTES
from .util import atomic_write_json, read_json, workspace_relative, sha256_text
from .workspace import load_config


@dataclass(frozen=True)
class ReconcileResult:
    changed: tuple[Path, ...]
    missing: tuple[str, ...]
    current_stats: dict[str, dict[str, int]]
    previous_manifest: dict[str, Any]


def candidate_paths(workspace: Path, config: dict[str, Any] | None = None) -> list[Path]:
    config = config or load_config(workspace)
    extensions = {value.casefold() for value in config.get("reconcile_extensions", [".md"])}
    candidates: set[Path] = set()
    for relative in config.get("canonical_files", []):
        path = workspace / relative
        if path.exists() and path.is_file() and path.suffix.casefold() in extensions:
            candidates.add(path.resolve())
    for relative in config.get("document_roots", []):
        root = workspace / relative
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in extensions:
                candidates.add(path.resolve())
                if len(candidates) > MAX_MANAGED_DOCUMENTS:
                    raise RuntimeError(
                        f"managed document count exceeds {MAX_MANAGED_DOCUMENTS}; "
                        "split the workspace or narrow configured document roots"
                    )
    if len(candidates) > MAX_MANAGED_DOCUMENTS:
        raise RuntimeError(
            f"managed document count exceeds {MAX_MANAGED_DOCUMENTS}; "
            "split the workspace or narrow configured document roots"
        )
    managed_bytes = sum(path.stat().st_size for path in candidates)
    if managed_bytes > MAX_MANAGED_SOURCE_BYTES:
        raise RuntimeError(
            f"managed document bytes exceed {MAX_MANAGED_SOURCE_BYTES}; "
            "split the workspace or archive material outside managed roots"
        )
    return sorted(candidates, key=lambda value: workspace_relative(value, workspace).casefold())


def reconcile_documents(workspace: Path, *, verify_content_hashes: bool = False) -> ReconcileResult:
    config = load_config(workspace)
    known_hashes: dict[str, str] = {}
    if verify_content_hashes:
        from .database import KnowledgeDatabase
        from .workspace import database_path
        db = KnowledgeDatabase(database_path(workspace))
        with closing(db.connect(read_only=True)) as connection:
            known_hashes = {
                str(row["path"]): str(row["content_sha256"])
                for row in connection.execute("SELECT path,content_sha256 FROM artifacts")
            }
    manifest_path = workspace / ".kairos" / "manifest.json"
    previous = read_json(manifest_path, default={}) or {}
    previous_entries = previous.get("documents", {})
    changed: list[Path] = []
    current_stats: dict[str, dict[str, int]] = {}
    for path in candidate_paths(workspace, config):
        relative = workspace_relative(path, workspace)
        stat = path.stat()
        signature = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        current_stats[relative] = signature
        old = previous_entries.get(relative)
        stats_changed = (not old or int(old.get("size", -1)) != signature["size"]
                         or int(old.get("mtime_ns", -1)) != signature["mtime_ns"])
        if stats_changed:
            changed.append(path)
        elif verify_content_hashes:
            actual = sha256_text(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n"))
            if actual != known_hashes.get(relative):
                changed.append(path)
    missing = tuple(sorted(set(previous_entries) - set(current_stats)))
    return ReconcileResult(
        changed=tuple(changed),
        missing=missing,
        current_stats=current_stats,
        previous_manifest=previous,
    )


def update_manifest(
    workspace: Path,
    result: ReconcileResult,
    successful_paths: list[Path],
    *,
    updated_at: str,
) -> None:
    if (
        not result.changed
        and not result.missing
        and result.previous_manifest
        and not successful_paths
    ):
        return
    entries = dict(result.previous_manifest.get("documents", {}))
    successful = {workspace_relative(path, workspace) for path in successful_paths}
    for relative in successful:
        path = workspace / relative
        if path.exists() and path.is_file():
            stat = path.stat()
            entries[relative] = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    for relative, signature in result.current_stats.items():
        if relative in entries and relative not in successful:
            old = entries[relative]
            if int(old.get("size", -1)) == signature["size"] and int(old.get("mtime_ns", -1)) == signature["mtime_ns"]:
                entries[relative] = signature
    atomic_write_json(
        workspace / ".kairos" / "manifest.json",
        {
            "schema": "kairos-reconciliation-manifest/v1",
            "documents": entries,
            "missing_sources": list(result.missing),
            "updated_at": updated_at,
        },
    )
