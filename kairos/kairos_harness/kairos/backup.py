from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .database import KnowledgeDatabase
from .constants import (
    MAX_BACKUP_MANIFEST_BYTES,
    MAX_BACKUP_SOURCE_BYTES,
    MAX_BACKUP_SOURCE_FILES,
)
from .goals import goal_source_paths
from .locking import workspace_write_lock
from .reconcile import candidate_paths
from .search import search_database
from .util import (
    atomic_write_json,
    resolve_workspace_path,
    sha256_bytes,
    sha256_text,
    utc_now,
    workspace_relative,
)
from .workspace import database_path, load_config


class BackupError(RuntimeError):
    pass


BACKUP_ID_RE = re.compile(r"BACKUP_[A-Z0-9][A-Z0-9_-]{7,80}")
REVISION_IDENTITY_RE = re.compile(
    r"(?P<artifact>[A-Z][A-Z0-9_.:-]{2,159})@(?P<revision>[1-9][0-9]{0,8})"
)
MAX_REVISION_RECOVERY_IDENTITIES = 256


def parse_revision_identity(value: str) -> tuple[str, int]:
    match = REVISION_IDENTITY_RE.fullmatch(value.strip())
    if not match:
        raise BackupError(
            "revision identity must use ARTIFACT_ID@POSITIVE_REVISION syntax"
        )
    return match.group("artifact"), int(match.group("revision"))


def _database_checks(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("artifacts", "sections", "relations", "query_handles", "promotion_receipts")
        }
    finally:
        connection.close()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
        "counts": counts,
        "verified": integrity == ["ok"] and not foreign_keys,
    }


def _source_paths(workspace: Path) -> list[Path]:
    paths = set(candidate_paths(workspace))
    paths.update(goal_source_paths(workspace))
    for relative in (
        "current.json",
        ".kairos/config.json",
        ".kairos/runtime_state.json",
        ".kairos/manifest.json",
        ".kairos/goal_manifest.json",
        ".kairos/canonical_projection.json",
    ):
        path = workspace / relative
        if path.is_file():
            paths.add(path.resolve())
    snapshot_root = workspace / "archive" / "task_snapshots"
    if snapshot_root.is_dir():
        paths.update(path.resolve() for path in snapshot_root.rglob("*") if path.is_file())
    ordered = sorted(paths, key=lambda path: workspace_relative(path, workspace).casefold())
    if len(ordered) > MAX_BACKUP_SOURCE_FILES:
        raise BackupError(f"backup source count exceeds {MAX_BACKUP_SOURCE_FILES}")
    total_bytes = sum(path.stat().st_size for path in ordered)
    if total_bytes > MAX_BACKUP_SOURCE_BYTES:
        raise BackupError(f"backup source bytes exceed {MAX_BACKUP_SOURCE_BYTES}")
    return ordered


def _artifact_source_parity(database_file: Path, source_hashes: dict[str, str]) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{database_file.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT artifact_id,path,content_sha256 FROM artifacts ORDER BY path"
        ).fetchall()
    finally:
        connection.close()
    missing = sorted(str(row["path"]) for row in rows if row["path"] not in source_hashes)
    mismatches = sorted(
        str(row["path"])
        for row in rows
        if row["path"] in source_hashes
        and source_hashes[str(row["path"])] != str(row["content_sha256"])
    )
    return {
        "artifact_count": len(rows),
        "missing_sources": missing,
        "hash_mismatches": mismatches,
        "verified": not missing and not mismatches,
    }


def verify_backup(workspace: Path, backup_id: str) -> dict[str, Any]:
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise BackupError("invalid backup identifier")
    root = resolve_workspace_path(workspace, f"backups/{backup_id}")
    manifest_path = root / "manifest.json"
    database_file = root / "kairos.db"
    sources_file = root / "sources.zip"
    if not manifest_path.is_file() or not database_file.is_file() or not sources_file.is_file():
        raise BackupError(f"incomplete backup package: {backup_id}")
    if manifest_path.stat().st_size > MAX_BACKUP_MANIFEST_BYTES:
        raise BackupError("backup manifest exceeds its safety bound")
    if sources_file.stat().st_size > MAX_BACKUP_SOURCE_BYTES:
        raise BackupError("compressed source archive exceeds its safety bound")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("schema") != "kairos-backup/v1":
        failures.append("backup manifest schema mismatch")
    if manifest.get("backup_id") != backup_id:
        failures.append("backup manifest identifier mismatch")
    if manifest.get("workspace_id") != load_config(workspace)["workspace_id"]:
        failures.append("backup workspace identifier mismatch")
    if sha256_bytes(database_file.read_bytes()) != manifest.get("database_sha256"):
        failures.append("database hash mismatch")
    if sha256_bytes(sources_file.read_bytes()) != manifest.get("sources_zip_sha256"):
        failures.append("source archive hash mismatch")
    source_entries = manifest.get("sources", [])
    if not isinstance(source_entries, list) or len(source_entries) > MAX_BACKUP_SOURCE_FILES:
        raise BackupError("backup manifest source list is invalid or exceeds its bound")
    source_names = [entry.get("path") for entry in source_entries if isinstance(entry, dict)]
    if len(source_names) != len(source_entries) or len(source_names) != len(set(source_names)):
        failures.append("backup manifest contains invalid or duplicate source members")
    manifest_source_bytes = sum(
        int(entry.get("bytes", 0)) for entry in source_entries if isinstance(entry, dict)
    )
    if manifest_source_bytes > MAX_BACKUP_SOURCE_BYTES:
        failures.append("backup manifest source bytes exceed safety bound")
    expected = {
        str(entry.get("path")): str(entry.get("sha256"))
        for entry in source_entries
        if isinstance(entry, dict)
    }
    expected_bytes = {
        str(entry.get("path")): int(entry.get("bytes", 0))
        for entry in source_entries
        if isinstance(entry, dict)
    }
    normalized_source_hashes: dict[str, str] = {}
    with zipfile.ZipFile(sources_file, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            failures.append("source archive contains duplicate members")
        if set(names) != set(expected):
            failures.append("source archive member set mismatch")
        archive_bytes = sum(info.file_size for info in archive.infolist())
        if len(names) > MAX_BACKUP_SOURCE_FILES or archive_bytes > MAX_BACKUP_SOURCE_BYTES:
            raise BackupError("source archive expands beyond its safety bound")
        for name in names:
            normalized = name.replace("\\", "/")
            parts = Path(normalized).parts
            if (
                not normalized
                or normalized.startswith("/")
                or ":" in normalized.split("/", 1)[0]
                or any(part in {"", ".", ".."} for part in parts)
            ):
                failures.append(f"unsafe source member: {name}")
                continue
            data = archive.read(name)
            if len(data) != expected_bytes.get(name):
                failures.append(f"source byte count mismatch: {name}")
            if sha256_bytes(data) != expected.get(name):
                failures.append(f"source hash mismatch: {name}")
            if normalized.endswith(".md"):
                try:
                    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                    normalized_source_hashes[normalized] = sha256_text(text)
                except UnicodeDecodeError:
                    failures.append(f"non-UTF-8 Markdown source: {name}")
    checks = _database_checks(database_file)
    if not checks["verified"]:
        failures.append("database integrity or foreign-key check failed")
    parity = _artifact_source_parity(database_file, normalized_source_hashes)
    if not parity["verified"]:
        failures.append(
            "database/source parity failed: "
            f"missing={parity['missing_sources']} mismatches={parity['hash_mismatches']}"
        )
    return {
        "schema": "kairos-backup-verification/v1",
        "backup_id": backup_id,
        "database_sha256": str(manifest.get("database_sha256", "")),
        "sources_zip_sha256": str(manifest.get("sources_zip_sha256", "")),
        "database": checks,
        "artifact_source_parity": parity,
        "source_count": len(expected),
        "failures": failures,
        "verified": not failures,
        "verified_at": utc_now(),
    }


def _verified_revision_rows(
    workspace: Path,
    backup_id: str,
    revision_identities: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not revision_identities:
        raise BackupError("at least one exact revision identity is required")
    identities = sorted({parse_revision_identity(value) for value in revision_identities})
    if len(identities) > MAX_REVISION_RECOVERY_IDENTITIES:
        raise BackupError(
            "revision recovery identity count exceeds "
            f"{MAX_REVISION_RECOVERY_IDENTITIES}"
        )
    verification = verify_backup(workspace, backup_id)
    if not verification["verified"]:
        raise BackupError(
            "backup is not eligible for revision recovery: "
            + "; ".join(str(value) for value in verification["failures"])
        )
    package = resolve_workspace_path(workspace, f"backups/{backup_id}")
    connection = sqlite3.connect(
        f"file:{(package / 'kairos.db').resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    try:
        for artifact_id, revision in identities:
            row = connection.execute(
                """
                SELECT artifact_id,revision,content_sha256,path,metadata_json,promoted_at
                FROM artifact_revisions
                WHERE artifact_id=? AND revision=?
                """,
                (artifact_id, revision),
            ).fetchone()
            if row is None:
                missing.append(f"{artifact_id}@{revision}")
                continue
            recovered = {key: row[key] for key in row.keys()}
            try:
                metadata = json.loads(str(recovered["metadata_json"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise BackupError(
                    f"backup revision metadata is invalid for {artifact_id}@{revision}"
                ) from exc
            if not isinstance(metadata, dict):
                raise BackupError(
                    f"backup revision metadata is not an object for {artifact_id}@{revision}"
                )
            if metadata.get("id") != artifact_id or metadata.get("revision") != revision:
                raise BackupError(
                    f"backup revision metadata identity mismatch for {artifact_id}@{revision}"
                )
            content_sha256 = str(recovered["content_sha256"])
            if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
                raise BackupError(
                    f"backup revision hash is invalid for {artifact_id}@{revision}"
                )
            recovered["path"] = workspace_relative(
                resolve_workspace_path(workspace, str(recovered["path"])), workspace
            )
            if not str(recovered["promoted_at"]).strip():
                raise BackupError(
                    f"backup revision promotion time is missing for {artifact_id}@{revision}"
                )
            rows.append(recovered)
    finally:
        connection.close()
    if missing:
        raise BackupError(
            "verified backup does not contain requested revisions: " + ", ".join(missing)
        )
    return verification, rows


def recover_artifact_revisions(
    workspace: Path,
    *,
    backup_id: str,
    revision_identities: list[str],
) -> dict[str, Any]:
    """Recover exact immutable revision rows from one verified same-workspace backup."""
    verification, recovered_rows = _verified_revision_rows(
        workspace,
        backup_id,
        revision_identities,
    )
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    inserted: list[dict[str, Any]] = []
    already_present: list[dict[str, Any]] = []
    with database.transaction() as connection:
        pending: list[dict[str, Any]] = []
        for row in recovered_rows:
            identity = f"{row['artifact_id']}@{row['revision']}"
            current = connection.execute(
                "SELECT revision FROM artifacts WHERE artifact_id=?",
                (row["artifact_id"],),
            ).fetchone()
            if current is None:
                raise BackupError(
                    f"live corpus does not contain recovered artifact identity: {row['artifact_id']}"
                )
            if int(current["revision"]) < int(row["revision"]):
                raise BackupError(
                    f"recovered revision is newer than live artifact: {identity}"
                )
            existing = connection.execute(
                """
                SELECT content_sha256,path,metadata_json
                FROM artifact_revisions
                WHERE artifact_id=? AND revision=?
                """,
                (row["artifact_id"], row["revision"]),
            ).fetchone()
            summary = {
                "identity": identity,
                "content_sha256": row["content_sha256"],
                "path": row["path"],
            }
            if existing is not None:
                existing_identity = (
                    str(existing["content_sha256"]),
                    str(existing["path"]),
                    str(existing["metadata_json"]),
                )
                recovered_identity = (
                    str(row["content_sha256"]),
                    str(row["path"]),
                    str(row["metadata_json"]),
                )
                if existing_identity != recovered_identity:
                    raise BackupError(
                        f"live revision conflicts with verified backup: {identity}"
                    )
                already_present.append(summary)
            else:
                pending.append(row)
                inserted.append(summary)
        for row in pending:
            connection.execute(
                """
                INSERT INTO artifact_revisions(
                    artifact_id,revision,content_sha256,path,metadata_json,promoted_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    row["artifact_id"],
                    row["revision"],
                    row["content_sha256"],
                    row["path"],
                    row["metadata_json"],
                    row["promoted_at"],
                ),
            )
    return {
        "schema": "kairos-revision-recovery/v1",
        "backup_id": backup_id,
        "backup_database_sha256": verification["database_sha256"],
        "requested": len(recovered_rows),
        "inserted": inserted,
        "already_present": already_present,
        "verified": True,
        "recovered_at": utc_now(),
    }


def create_backup(workspace: Path, *, keep: int = 3) -> dict[str, Any]:
    workspace = workspace.resolve()
    with workspace_write_lock(workspace):
        return _create_backup_locked(workspace, keep=keep)


def _create_backup_locked(workspace: Path, *, keep: int) -> dict[str, Any]:
    if not 1 <= keep <= 20:
        raise BackupError("backup retention must be between 1 and 20")
    load_config(workspace)
    source_database = KnowledgeDatabase(database_path(workspace))
    source_database.initialize()
    created_at = utc_now()
    backup_id = (
        "BACKUP_"
        + created_at.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
        + "_"
        + uuid.uuid4().hex[:8].upper()
    )
    backups_root = workspace / "backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    partial = backups_root / f".{backup_id}.partial"
    final = backups_root / backup_id
    if partial.exists() or final.exists():
        raise BackupError(f"backup target already exists: {backup_id}")
    try:
        partial.mkdir()
        db_target = partial / "kairos.db"
        src = source_database.connect(read_only=True)
        dst = sqlite3.connect(db_target)
        try:
            src.backup(dst, pages=128, sleep=0.05)
        finally:
            dst.close()
            src.close()
        source_entries: list[dict[str, Any]] = []
        sources_zip = partial / "sources.zip"
        with zipfile.ZipFile(sources_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            captured_bytes = 0
            for path in _source_paths(workspace):
                relative = workspace_relative(path, workspace)
                before = path.stat()
                captured_bytes += int(before.st_size)
                if captured_bytes > MAX_BACKUP_SOURCE_BYTES:
                    raise BackupError(f"backup source bytes exceed {MAX_BACKUP_SOURCE_BYTES}")
                data = path.read_bytes()
                after = path.stat()
                before_signature = (before.st_size, before.st_mtime_ns)
                after_signature = (after.st_size, after.st_mtime_ns)
                if before_signature != after_signature or len(data) != after.st_size:
                    raise BackupError(f"source changed during backup capture: {relative}")
                archive.writestr(relative, data)
                source_entries.append({"path": relative, "sha256": sha256_bytes(data), "bytes": len(data)})
        checks = _database_checks(db_target)
        if not checks["verified"]:
            raise BackupError(f"new backup failed database checks: {checks}")
        manifest = {
            "schema": "kairos-backup/v1",
            "backup_id": backup_id,
            "workspace_id": load_config(workspace)["workspace_id"],
            "created_at": created_at,
            "database_sha256": sha256_bytes(db_target.read_bytes()),
            "sources_zip_sha256": sha256_bytes(sources_zip.read_bytes()),
            "sources": source_entries,
            "database_checks": checks,
        }
        atomic_write_json(partial / "manifest.json", manifest)
        os.replace(partial, final)
        verification = verify_backup(workspace, backup_id)
        if not verification["verified"]:
            raise BackupError(f"completed backup failed reopen verification: {verification['failures']}")
    except Exception:
        if partial.exists():
            partial.resolve().relative_to(backups_root.resolve())
            shutil.rmtree(partial)
        raise
    packages = sorted(
        (
            path
            for path in backups_root.iterdir()
            if path.is_dir() and BACKUP_ID_RE.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    pruned: list[str] = []
    for path in packages[keep:]:
        path.resolve().relative_to(backups_root.resolve())
        shutil.rmtree(path)
        pruned.append(path.name)
    return {
        "schema": "kairos-backup-result/v1",
        "backup_id": backup_id,
        "path": workspace_relative(final, workspace),
        "database_sha256": manifest["database_sha256"],
        "source_count": len(source_entries),
        "verification": verification,
        "retention": {"keep": keep, "pruned": pruned},
    }


def restore_drill(workspace: Path, backup_id: str) -> dict[str, Any]:
    verification = verify_backup(workspace, backup_id)
    if not verification["verified"]:
        raise BackupError(f"backup is not eligible for a restore drill: {verification['failures']}")
    package = resolve_workspace_path(workspace, f"backups/{backup_id}")
    drill_root = workspace / ".kairos" / "restore_drills"
    drill_root.mkdir(parents=True, exist_ok=True)
    target = drill_root / f"DRILL_{uuid.uuid4().hex}"
    target.mkdir()
    try:
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        expected = {entry["path"]: entry["sha256"] for entry in manifest["sources"]}
        restored_sources = 0
        with zipfile.ZipFile(package / "sources.zip", "r") as archive:
            for name in archive.namelist():
                output = resolve_workspace_path(target, name)
                output.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(name)
                if sha256_bytes(data) != expected[name]:
                    raise BackupError(f"restore source hash mismatch: {name}")
                output.write_bytes(data)
                restored_sources += 1
        restored_database = target / ".kairos" / "kairos.db"
        restored_database.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package / "kairos.db", restored_database)
        checks = _database_checks(restored_database)
        replay_database = KnowledgeDatabase(restored_database)
        connection = replay_database.connect(read_only=True)
        try:
            handle = connection.execute(
                "SELECT question,artifact_id FROM query_handles ORDER BY artifact_id,question LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if handle:
            replay_result = search_database(
                replay_database,
                str(handle["question"]),
                limit=5,
                candidate_limit=50,
                record_trace=False,
            )
            search_replay = {
                "query": str(handle["question"]),
                "expected_artifact": str(handle["artifact_id"]),
                "primary_artifact": (
                    replay_result["primary"]["artifact_id"]
                    if replay_result.get("primary")
                    else None
                ),
                "matched": any(
                    result["artifact_id"] == handle["artifact_id"]
                    for result in replay_result["results"]
                ),
            }
        else:
            search_replay = {
                "query": None,
                "expected_artifact": None,
                "primary_artifact": None,
                "matched": False,
            }
    finally:
        shutil.rmtree(target)
    return {
        "schema": "kairos-restore-drill/v1",
        "backup_id": backup_id,
        "database": checks,
        "search_replay": search_replay,
        "restored_source_count": restored_sources,
        "expected_source_count": len(expected),
        "verified": checks["verified"] and restored_sources == len(expected) and search_replay["matched"],
        "drilled_at": utc_now(),
    }
