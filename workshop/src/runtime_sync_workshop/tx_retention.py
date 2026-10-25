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

from .util import WorkshopError, atomic_write_json, sha256_bytes, utc_now, read_json

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
        tmp = None
        try:
            # A unique temporary name makes retries safe even when a previous
            # process crashed before it could remove a staged object.
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=dest.parent, prefix=".pending-", delete=False
            ) as output:
                tmp = Path(output.name)
                with path.open("rb") as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(tmp, dest)
            except FileExistsError:
                pass
            except OSError:
                # Some Windows/network filesystems refuse hard links. Keep
                # exclusive creation and verify exact bytes before use.
                try:
                    with tmp.open("rb") as source, dest.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                except FileExistsError:
                    pass
        finally:
            if tmp is not None:
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


def _compaction_committed(root: Path) -> bool:
    """Only a durable post-projection commit frees a TX capacity slot."""
    marker = root / "compaction-committed.json"
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        receipt = json.loads((root / "compaction.json").read_text(encoding="utf-8"))
        plan = json.loads((root / "compaction-plan.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return (record.get("schema") == "werkfaden-tx-compaction-commit/v1"
            and record.get("transaction_id") == root.name
            and record.get("manifest_sha256") == receipt.get("manifest_sha256") == plan.get("manifest_sha256")
            and record.get("status") == "COMMITTED")


def materialized_worktrees(engine: Any) -> list[Path]:
    directory = engine.config.transaction_directory
    if not directory.is_dir():
        return []
    return sorted(
        root for root in directory.iterdir()
        if root.is_dir() and not root.is_symlink()
        and ((root / "work").is_dir()
             or ((root / "compaction-plan.json").is_file()
                 and not _compaction_committed(root)))
    )


@contextmanager
def _retention_lock(engine: Any):
    """OS-released process lock; crash cannot leave a permanent lockfile."""
    path = engine.config.state_directory / "tx-retention.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                if not lock.read(1):
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise WorkshopError("TX_RETENTION_BUSY", "another retention process holds the pool lock") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)



def _assert_document_projected(engine: Any, transaction_id: str) -> None:
    from .tx_documents import assert_projected
    assert_projected(engine, transaction_id)


def _verified_plan(engine: Any, root: Path, transaction_id: str) -> tuple[dict[str, Any], Path]:
    plan_path = root / "compaction-plan.json"
    if not plan_path.is_file() or plan_path.is_symlink():
        raise WorkshopError("TX_COMPACTION_PLAN_MISSING", "no durable compaction plan exists")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("transaction_id") != transaction_id:
        raise WorkshopError("TX_COMPACTION_PLAN_INVALID", "compaction plan refers to a different TX")
    manifest_path = Path(str(plan.get("manifest", ""))).resolve()
    trusted = (vault_dir(engine) / "manifests").resolve()
    if manifest_path.parent != trusted or not manifest_path.is_file():
        raise WorkshopError("TX_COMPACTION_MANIFEST_INVALID", "compaction archive manifest is missing or outside the vault")
    manifest_bytes = manifest_path.read_bytes()
    if sha256_bytes(manifest_bytes) != plan.get("manifest_sha256"):
        raise WorkshopError("TX_COMPACTION_MANIFEST_CORRUPT", "archived manifest digest differs from compaction plan")
    manifest = json.loads(manifest_bytes)
    if manifest.get("transaction_id") != transaction_id or manifest.get("complete") is not True:
        raise WorkshopError("TX_COMPACTION_MANIFEST_INVALID", "archive is not a complete TX snapshot")
    for relative, record in manifest.get("files", {}).items():
        digest = str(record.get("sha256", ""))
        blob = _object_path(vault_dir(engine), digest)
        if blob.is_symlink() or not blob.is_file() or _sha256_file(blob) != digest:
            raise WorkshopError("TX_COMPACTION_BLOB_CORRUPT", f"archive missing exact source bytes for {relative}")
    return plan, manifest_path


def _resume_compaction_locked(engine: Any, transaction_id: str) -> dict[str, Any]:
    """Crash-safe redo after a durable plan, including interrupted directory moves."""
    root, state = engine._load_transaction(transaction_id)
    if state.get("state") not in TERMINAL:
        raise WorkshopError("TX_COMPACTION_STATE_INVALID", "a nonterminal TX cannot resume retirement")
    if engine.lease_path.exists():
        raise WorkshopError("TX_ARCHIVE_LEASE_ACTIVE", "recycle cannot run during any active Workshop lease")
    plan, manifest = _verified_plan(engine, root, transaction_id)
    pending = root / ".reclaim"
    pending.mkdir(exist_ok=True)
    for name in LARGE_DIRS:
        original, staged = root / name, pending / name
        if original.is_symlink() or staged.is_symlink():
            raise WorkshopError("TX_ARCHIVE_LINK_UNSAFE", f"linked compaction target: {name}")
        if original.is_dir():
            if staged.exists():
                raise WorkshopError("TX_COMPACTION_CONFLICT", f"both live and staged compaction targets exist: {name}")
            os.replace(original, staged)
    if any((root / name).exists() for name in LARGE_DIRS):
        raise WorkshopError("TX_COMPACTION_INCOMPLETE", "one or more large TX work roots remain materialized")
    record = {
        "schema": "werkfaden-tx-compaction/v1",
        "transaction_id": transaction_id, "source_state": state["state"],
        "complete_manifest": str(manifest),
        "manifest_sha256": plan["manifest_sha256"],
        "reclaimed_worktree_bytes": int(plan.get("reclaimed_worktree_bytes", 0)),
    }
    atomic_write_json(root / "compaction.json", record)
    if state.get("compaction") != record:
        state["compaction"] = record
        atomic_write_json(root / "state.json", state)
    if engine.config.raw.get("transaction_documents_required", False) is True:
        from .tx_documents import update_document, assert_projected
        # A previous partial failure may already have published the same state.
        from .tx_documents import pending_path
        if pending_path(engine, transaction_id).is_file():
            from .tx_documents import publish
            publish(engine, state)
            pending_path(engine, transaction_id).unlink(missing_ok=True)
        else:
            try:
                assert_projected(engine, transaction_id)
            except WorkshopError:
                update_document(engine, state, event="TX_COMPACTED")
        assert_projected(engine, transaction_id)
    # Release capacity only AFTER the exact document/SQL projection is verified.
    # The commit marker survives a crash before staging GC and is independent
    # of the mere disappearance of work/.
    atomic_write_json(root / "compaction-committed.json", {
        "schema": "werkfaden-tx-compaction-commit/v1",
        "transaction_id": transaction_id,
        "manifest_sha256": plan["manifest_sha256"],
        "status": "COMMITTED",
        "committed_at": utc_now(),
    })
    # The archive and SQL are verified; staging may now be garbage-collected.
    if pending.exists():
        shutil.rmtree(pending)
    return {"transaction_id": transaction_id,
            "reclaimed_worktree_bytes": record["reclaimed_worktree_bytes"],
            "archive": {"manifest": str(manifest), "sha256": plan["manifest_sha256"], "complete": True}}


def _compact_locked(engine: Any, transaction_id: str) -> dict[str, Any]:
    root, state = engine._load_transaction(transaction_id)
    if state.get("state") not in TERMINAL:
        raise WorkshopError("TX_ARCHIVE_ACTIVE", "nonterminal transaction cannot be compacted")
    if engine.lease_path.is_file():
        raise WorkshopError("TX_ARCHIVE_LEASE_ACTIVE", "cannot compact while any Workshop lease is held")
    if (root / "compaction-plan.json").is_file():
        if _compaction_committed(root):
            staged = root / ".reclaim"
            if staged.is_dir() and not staged.is_symlink():
                shutil.rmtree(staged)
            receipt = read_json(root / "compaction.json")
            return {"transaction_id": transaction_id,
                    "reclaimed_worktree_bytes": receipt["reclaimed_worktree_bytes"],
                    "archive": {"manifest": receipt["complete_manifest"],
                                "sha256": receipt["manifest_sha256"], "complete": True}}
        return _resume_compaction_locked(engine, transaction_id)
    from .tx_recycling import assert_reviewed
    assert_reviewed(engine, transaction_id)
    _assert_document_projected(engine, transaction_id)
    archived = snapshot(engine, transaction_id, include_entire_tx=True)
    # Archive may take a long time. Refuse retirement when the actual changed
    # candidate differs from the one the reviewer classified.
    assert_reviewed(engine, transaction_id)
    reclaimed = 0
    for name in LARGE_DIRS:
        sub = root / name
        if sub.is_symlink():
            raise WorkshopError("TX_ARCHIVE_LINK_UNSAFE", f"linked compaction target: {name}")
        if sub.is_dir():
            reclaimed += sum(path.stat().st_size for path in _safe_files(sub))
    plan = {
        "schema": "werkfaden-tx-compaction-plan/v1",
        "transaction_id": transaction_id, "manifest": archived["manifest"],
        "manifest_sha256": archived["sha256"], "reclaimed_worktree_bytes": reclaimed,
    }
    # Plan is durable BEFORE the first destructive rename; retries never resnapshot
    # a partially moved TX and therefore never lose its original bytes.
    atomic_write_json(root / "compaction-plan.json", plan)
    return _resume_compaction_locked(engine, transaction_id)


def compact(engine: Any, transaction_id: str) -> dict[str, Any]:
    with _retention_lock(engine):
        return _compact_locked(engine, transaction_id)


def recover_interrupted_compactions(engine: Any, *, max_items: int = 200) -> list[dict[str, Any]]:
    """Rotate through actual incomplete plans; committed plans are not rehashed."""
    results = []
    roots = engine.config.transaction_directory
    if not roots.is_dir():
        return results
    cursor = engine.config.state_directory / "tx-recycling" / "recovery_cursor.json"
    with _retention_lock(engine):
        outstanding = sorted(
            root for root in roots.iterdir()
            if root.is_dir() and not root.is_symlink()
            and (root / "compaction-plan.json").is_file()
            and (not _compaction_committed(root) or (root / ".reclaim").exists())
        )
        if not outstanding:
            return results
        try:
            previous = json.loads(cursor.read_text(encoding="utf-8")) if cursor.is_file() else {}
        except (OSError, ValueError):
            previous = {}
        last = str(previous.get("last_tx_id") or "") if isinstance(previous, dict) else ""
        names = [item.name for item in outstanding]
        index = (names.index(last) + 1) % len(names) if last in names else 0
        ordered = outstanding[index:] + outstanding[:index]
        selected = ordered[:max_items]
        for root in selected:
            try:
                outcome = (_compact_locked(engine, root.name)
                           if _compaction_committed(root)
                           else _resume_compaction_locked(engine, root.name))
                results.append({"transaction_id": root.name, "status": "RECOVERED", "result": outcome})
            except Exception as exc:
                results.append({"transaction_id": root.name, "status": "BLOCKED",
                                "code": getattr(exc, "code", type(exc).__name__),
                                "reason": str(exc)[:300]})
        if selected:
            cursor.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(cursor, {
                "schema": "werkfaden-tx-compaction-recovery-cursor/v1",
                "last_tx_id": selected[-1].name,
                "observed_outstanding": len(outstanding),
                "examined": len(selected),
            })
    return results


def storage_inventory(engine: Any) -> dict[str, Any]:
    """Explicit read-only physical storage census; no deletion or hidden GC.

    This scan is deliberately NOT performed on every ordinary checkout unless
    the operator configures a real byte budget. Expensive full inventories
    remain an explicit maintenance action.
    """
    workspace = engine.config.kairos_workspace
    state_dir = engine.config.state_directory
    transaction_dir = engine.config.transaction_directory

    def directory_usage(folder: Path, *, names: set[str] | None = None) -> tuple[int, int]:
        total, count = 0, 0
        if not folder.is_dir() or folder.is_symlink():
            return 0, 0
        for parent, folders, filenames in os.walk(folder, followlinks=False):
            folders[:] = [d for d in folders if not (Path(parent) / d).is_symlink()]
            for name in filenames:
                if names is not None and name not in names:
                    continue
                path = Path(parent) / name
                if path.is_symlink():
                    continue
                try:
                    total += path.stat().st_size
                    count += 1
                except (OSError, ValueError):
                    continue
        return total, count

    # TX directory includes staging and sources; avoid overlap by keeping
    # disjoint physical roots and reporting vault objects separately.
    roots = {
        "tx_directory_bytes": transaction_dir,
        "vault_unique_bytes": state_dir / "tx-vault" / "objects",
        "vault_manifest_bytes": state_dir / "tx-vault" / "manifests",
        "active_report_bytes": workspace / "reports",
        "receipt_bytes": workspace / ".kairos" / "receipts",
        "heartbeat_bytes": workspace / ".kairos" / "heartbeats",
        "pending_bytes": state_dir / "transaction-document-pending",
        "recycling_receipt_bytes": state_dir / "tx-recycling",
    }
    totals = {key: directory_usage(path)[0] for key, path in roots.items()}
    db = engine.config.kairos_database
    totals["sqlite_bytes"] = sum(
        path.stat().st_size for path in [db, Path(str(db) + "-wal"), Path(str(db) + "-shm")]
        if path.is_file() and not path.is_symlink()
    )
    # The historical archive tier does not yet exist; do not claim that
    # completed Markdown has been moved outside the active search corpus.
    totals["archived_report_bytes"] = 0
    totals["measured_total_bytes"] = sum(
        value for key, value in totals.items() if key != "archived_report_bytes"
    )
    threshold = engine.config.raw.get("tx_total_storage_budget_bytes")
    if threshold is not None and (type(threshold) is not int or threshold < 1):
        raise WorkshopError("TX_STORAGE_BUDGET_INVALID",
                            "tx_total_storage_budget_bytes must be a positive integer")
    return {
        "schema": "werkfaden-tx-storage-audit/v1",
        "totals": totals,
        "configured_budget_bytes": threshold,
        "over_budget": threshold is not None and totals["measured_total_bytes"] > threshold,
        "scope": "Measured roots only; no claim about external codebase, snapshots held outside these roots or deduplicated filesystem blocks",
    }


def enforce_pool_limit(engine: Any) -> dict[str, Any]:
    """Strict admission at nine and opt-in byte budget; maintenance remains allowed."""
    from .tx_recycling import require_budget
    result = require_budget(engine)
    if engine.config.raw.get("tx_total_storage_budget_bytes") is not None:
        space = storage_inventory(engine)
        if space["over_budget"]:
            raise WorkshopError(
                "TX_STORAGE_BUDGET_EXCEEDED",
                "total measured Workshop/KAIROS storage exceeds the configured admission budget; cleanup and recovery remain allowed",
                details=space,
            )
    return result


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
            archived_work = {relative: entry for relative, entry in rows.items() if relative.startswith("work/")}
            if not archived_work:
                continue
            current_work = {path.relative_to(root).as_posix(): path for path in _safe_files(root / "work")}
            if set(current_work) != set(archived_work):
                continue
            if any(_sha256_file(current_work[relative]) != entry.get("sha256")
                   for relative, entry in archived_work.items()):
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
