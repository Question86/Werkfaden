"""Content-addressed, fail-closed Workshop transaction retention.

Only terminal and fully projected TX worktrees can be compacted. Every original
byte is copied and re-read before any original directory is removed. Shared
objects are NOT garbage-collected by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import WorkshopError, atomic_write_json, sha256_bytes

TERMINAL = {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK", "ABORTED_ORPHANED_CHECKOUT"}
LARGE_DIRS = ("baseline", "work", "rollback", "shadow")

def vault_dir(engine: Any) -> Path:
    return engine.config.state_directory / "tx-vault"

def _safe_files(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise WorkshopError("TX_ARCHIVE_PATH_UNSAFE", "transaction root is not an ordinary directory")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise WorkshopError("TX_ARCHIVE_LINK_UNSAFE", "transaction contains symlinks")
        if path.is_file():
            yield path

def _object_path(vault: Path, digest: str) -> Path:
    return vault / "objects" / digest[:2] / digest

def _store(vault: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    # Stream input: large source files must not occupy RAM in their entirety.
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    hexdigest = digest.hexdigest()
    dest = _object_path(vault, hexdigest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.parent / (".pending-" + str(os.getpid()) + "-" + hexdigest)
        try:
            with path.open("rb") as source, tmp.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            # Do not replace a pre-existing shared object.
            try:
                os.link(tmp, dest)
            except FileExistsError:
                pass
        finally:
            tmp.unlink(missing_ok=True)
    if dest.is_symlink() or not dest.is_file() or _sha256_file(dest) != hexdigest:
        raise WorkshopError("TX_ARCHIVE_OBJECT_CORRUPT", f"stored object is not byte-identical: {hexdigest}")
    if _sha256_file(path) != hexdigest:
        raise WorkshopError("TX_ARCHIVE_SOURCE_CHANGED", "TX source changed while being archived")
    return {"sha256": hexdigest, "size": size}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(engine: Any, transaction_id: str, *, include_entire_tx: bool = False) -> dict[str, Any]:
    root, state = engine._load_transaction(transaction_id)
    if not include_entire_tx and state.get("state") != "SHADOW_VERIFIED":
        raise WorkshopError("TX_ARCHIVE_STATE_INVALID", "only a shadow-verified candidate can be snapshotted as verified")
    vault = vault_dir(engine)
    entries: dict[str, Any] = {}
    roots = [root] if include_entire_tx else [root / "work", root / "review"]
    for base in roots:
        if not base.is_dir():
            continue
        for path in _safe_files(base):
            relative = path.relative_to(root).as_posix()
            entries[relative] = _store(vault, path)
    if not entries:
        raise WorkshopError("TX_ARCHIVE_EMPTY", "no transaction bytes were available to archive")
    payload = {
        "schema": "werkfaden-transaction-vault/v1",
        "transaction_id": transaction_id,
        "state": state.get("state"),
        "verified_work_package_sha256": state.get("verified_work_package_sha256"),
        "baseline_package_sha256": state.get("baseline_package_sha256"),
        "complete": include_entire_tx,
        "files": entries,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fingerprint = sha256_bytes(canonical)
    manifest = vault / "manifests" / f"{transaction_id}-{fingerprint}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.exists():
        with manifest.open("xb") as out:
            out.write(canonical)
    if sha256_bytes(manifest.read_bytes()) != fingerprint:
        raise WorkshopError("TX_ARCHIVE_MANIFEST_CORRUPT", "archive manifest changed unexpectedly")
    for item in entries.values():
        blob = _object_path(vault, item["sha256"])
        if not blob.is_file() or _sha256_file(blob) != item["sha256"]:
            raise WorkshopError("TX_ARCHIVE_VERIFY_FAILED", "archive object could not be re-read")
    return {"manifest": str(manifest), "sha256": fingerprint, "file_count": len(entries), "complete": include_entire_tx}


def materialized_worktrees(engine: Any) -> list[Path]:
    directory = engine.config.transaction_directory
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink() and (p / "work").is_dir())


@contextmanager
def _retention_lock(engine: Any):
    path = engine.config.state_directory / "tx-retention.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise WorkshopError("TX_RETENTION_BUSY", "another retention process holds the TX pool lock") from exc
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def _assert_document_projected(engine: Any, transaction_id: str) -> None:
    from .tx_documents import assert_projected
    assert_projected(engine, transaction_id)


def _compact_locked(engine: Any, transaction_id: str) -> dict[str, Any]:
    root, state = engine._load_transaction(transaction_id)
    if state.get("state") not in TERMINAL:
        raise WorkshopError("TX_ARCHIVE_ACTIVE", "nonterminal transaction cannot be compacted")
    if engine.lease_path.is_file():
        raise WorkshopError("TX_ARCHIVE_LEASE_ACTIVE", "cannot compact while any Workshop transaction holds the lease")
    _assert_document_projected(engine, transaction_id)
    saved = snapshot(engine, transaction_id, include_entire_tx=True)
    manifest_path = Path(saved["manifest"])
    evidence = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, record in evidence["files"].items():
        if _sha256_file(_object_path(vault_dir(engine), record["sha256"])) != record["sha256"]:
            raise WorkshopError("TX_ARCHIVE_VERIFY_FAILED", "archive verification failed before cleanup")
    # Do not delete state, review receipts or the archive manifest. Only
    # materialized working copies are disposable after complete byte capture.
    reclaimed = 0
    for name in LARGE_DIRS:
        directory = root / name
        if directory.is_symlink():
            raise WorkshopError("TX_ARCHIVE_LINK_UNSAFE", f"refusing linked deletion target: {name}")
        if directory.is_dir():
            for path in _safe_files(directory):
                reclaimed += path.stat().st_size
            shutil.rmtree(directory)
    marker = root / "compaction.json"
    atomic_write_json(marker, {
        "schema": "werkfaden-tx-compaction/v1",
        "transaction_id": transaction_id, "source_state": state["state"],
        "complete_manifest": saved["manifest"], "manifest_sha256": saved["sha256"],
        "reclaimed_worktree_bytes": reclaimed,
    })
    return {"transaction_id": transaction_id, "reclaimed_worktree_bytes": reclaimed, "archive": saved}


def compact(engine: Any, transaction_id: str) -> dict[str, Any]:
    with _retention_lock(engine):
        return _compact_locked(engine, transaction_id)


def enforce_pool_limit(engine: Any) -> dict[str, Any]:
    """Compact only proved-terminal records; never remove an unproven candidate."""
    if engine.config.raw.get("tx_retention_required", False) is not True:
        return {"enabled": False}
    if engine.config.raw.get("transaction_documents_required", False) is not True:
        raise WorkshopError("TX_RETENTION_NEEDS_DOCUMENTS", "enable transaction_documents_required before automatic retention")
    cap = engine.config.raw.get("tx_max_materialized", 10)
    if type(cap) is not int or cap < 1 or cap > 1000:
        raise WorkshopError("TX_POOL_LIMIT_INVALID", "tx_max_materialized must be an integer from 1 to 1000")
    with _retention_lock(engine):
        open_worktrees = materialized_worktrees(engine)
        if len(open_worktrees) < cap:
            return {"cap": cap, "materialized": len(open_worktrees), "compacted": []}
        reclaimed = []
        for root in open_worktrees:
            if len(materialized_worktrees(engine)) < cap:
                break
            try:
                state = json.loads((root / "state.json").read_text(encoding="utf-8"))
                if state.get("state") not in TERMINAL:
                    continue
                reclaimed.append(_compact_locked(engine, str(state["transaction_id"])))
            except (OSError, ValueError, WorkshopError):
                continue
        remaining = len(materialized_worktrees(engine))
        if remaining >= cap:
            raise WorkshopError("TX_POOL_CAPACITY", "pool is full of protected or undocumented transactions; no automatic eviction is safe",
                                details={"cap": cap, "materialized": remaining})
        return {"cap": cap, "materialized": remaining, "compacted": reclaimed}

def assert_verified_snapshot(engine: Any, transaction_id: str) -> None:
    """Require exact recoverable bytes for the current verified Work candidate."""
    root, state = engine._load_transaction(transaction_id)
    package = state.get("verified_work_package_sha256")
    if not isinstance(package, str) or not package:
        raise WorkshopError("TX_ARCHIVE_VERIFICATION_MISSING", "verified package hash is missing")
    base = vault_dir(engine) / "manifests"
    found = False
    if base.is_dir():
        for path in sorted(base.glob(transaction_id + "-*.json")):
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if info.get("verified_work_package_sha256") != package:
                continue
            rows = info.get("files") or {}
            if not isinstance(rows, dict) or not rows:
                continue
            if not any(relative.startswith("work/") for relative in rows):
                continue
            for relative, entry in rows.items():
                blob = _object_path(vault_dir(engine), str(entry.get("sha256", "")))
                if not blob.is_file() or blob.is_symlink() or _sha256_file(blob) != entry.get("sha256"):
                    raise WorkshopError("TX_ARCHIVE_VERIFICATION_FAILED", "verified candidate's archival object is missing or corrupted")
            found = True
            break
    if not found:
        raise WorkshopError("TX_ARCHIVE_VERIFICATION_MISSING", "verified candidate has no retained immutable snapshot")


def rehydrate(engine: Any, transaction_id: str, manifest_path: Path, destination: Path) -> dict[str, Any]:
    """Restore a complete archived terminal TX into a NEW, empty directory."""
    engine._transaction_path(transaction_id)
    manifest_path = manifest_path.resolve(strict=True)
    expected_dir = (vault_dir(engine) / "manifests").resolve()
    if manifest_path.parent != expected_dir:
        raise WorkshopError("TX_ARCHIVE_MANIFEST_INVALID", "archive manifest is outside the trusted vault")
    info = json.loads(manifest_path.read_text(encoding="utf-8"))
    if info.get("transaction_id") != transaction_id or info.get("complete") is not True:
        raise WorkshopError("TX_ARCHIVE_MANIFEST_INVALID", "manifest is not the complete requested transaction")
    if destination.exists():
        raise WorkshopError("TX_ARCHIVE_DEST_EXISTS", "restore requires a new directory")
    destination.mkdir(parents=True, exist_ok=False)
    written = 0
    try:
        for relative, entry in (info.get("files") or {}).items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise WorkshopError("TX_ARCHIVE_PATH_ESCAPE", "invalid path inside manifest")
            object_sha = str(entry["sha256"])
            blob = _object_path(vault_dir(engine), object_sha)
            if not blob.is_file() or _sha256_file(blob) != object_sha:
                raise WorkshopError("TX_ARCHIVE_OBJECT_CORRUPT", "restore source blob failed verification")
            output = destination / relative_path
            output.parent.mkdir(parents=True, exist_ok=True)
            with blob.open("rb") as src, output.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if _sha256_file(output) != object_sha:
                raise WorkshopError("TX_ARCHIVE_RESTORE_MISMATCH", "rehydrated bytes differ from archived source")
            written += 1
        return {"transaction_id": transaction_id, "restored_files": written, "destination": str(destination)}
    except Exception:
        # Do not leave a partial recovered candidate masquerading as complete.
        shutil.rmtree(destination)
        raise
