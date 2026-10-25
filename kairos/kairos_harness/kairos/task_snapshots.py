from __future__ import annotations

import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .constants import MAX_MANAGED_DOCUMENTS, MAX_MANAGED_SOURCE_BYTES
from .promoter import prepare_document
from .reconcile import candidate_paths
from .util import json_dumps, resolve_workspace_path, sha256_bytes, sha256_text, workspace_relative
from .workspace import load_config


TASK_SNAPSHOT_START_LOOP = 5
TASK_SNAPSHOT_SCHEMA = "kairos-task-snapshot/v1"
TASK_SNAPSHOT_ROOT = "archive/task_snapshots"
TASK_SNAPSHOT_MANIFEST = "manifest.json"
TASK_SNAPSHOT_MAX_PACKAGE_BYTES = MAX_MANAGED_SOURCE_BYTES
TASK_SNAPSHOT_MAX_ENTRIES = MAX_MANAGED_DOCUMENTS
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_BINDING_RE = re.compile(
    r"(?m)^Task snapshot package:\s+`([^`]+)`\s+\|\s+"
    r"SHA-256:\s+`([0-9a-f]{64})`\s+\|\s+"
    r"previous SHA-256:\s+`(none|[0-9a-f]{64})`\s*$"
)
_UNSET = object()


class TaskSnapshotError(RuntimeError):
    """A task snapshot is absent, malformed, or does not match its binding."""


def task_snapshot_relative_path(loop: int) -> str:
    if isinstance(loop, bool) or not isinstance(loop, int) or loop < TASK_SNAPSHOT_START_LOOP:
        raise TaskSnapshotError(
            f"task snapshot packages begin at loop {TASK_SNAPSHOT_START_LOOP}: {loop!r}"
        )
    return f"{TASK_SNAPSHOT_ROOT}/TASK_SNAPSHOT_L{loop:04d}.zip"


def task_snapshot_path(workspace: Path, loop: int) -> Path:
    return resolve_workspace_path(workspace, task_snapshot_relative_path(loop))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _zip_member(name: str, data: bytes) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0o600 << 16
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def _deterministic_zip(manifest: dict[str, Any], members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        manifest_bytes = json_dumps(manifest, pretty=True).encode("utf-8")
        archive.writestr(_zip_member(TASK_SNAPSHOT_MANIFEST, manifest_bytes), manifest_bytes)
        for name in sorted(members):
            archive.writestr(_zip_member(name, members[name]), members[name])
    return output.getvalue()


def _safe_member_name(name: str) -> str:
    normalized = str(name).replace("\\", "/")
    parts = Path(normalized).parts
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized.split("/", 1)[0]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise TaskSnapshotError(f"unsafe task snapshot member: {name!r}")
    return normalized


def _previous_package_hash(workspace: Path, loop: int) -> str | None:
    if loop == TASK_SNAPSHOT_START_LOOP:
        return None
    previous_path = task_snapshot_path(workspace, loop - 1)
    if not previous_path.is_file():
        raise TaskSnapshotError(
            f"task snapshot chain predecessor is missing: {workspace_relative(previous_path, workspace)}"
        )
    return sha256_bytes(previous_path.read_bytes())


def _task_entries(workspace: Path, database: Any, loop: int) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    config = load_config(workspace)
    entries: list[dict[str, Any]] = []
    members: dict[str, bytes] = {}
    connection = database.connect(read_only=True)
    try:
        for path in candidate_paths(workspace, config):
            prepared = prepare_document(path, workspace)
            metadata = prepared.frontmatter.metadata
            if metadata.get("type") != "task" or metadata.get("loop") != loop:
                continue
            if metadata.get("state") == "superseded":
                continue
            if prepared.external_origin:
                raise TaskSnapshotError(
                    f"task snapshot cannot capture imported task: {prepared.relative_path}"
                )
            row = connection.execute(
                """
                SELECT artifact_id,path,revision,state,loop_id,content_sha256
                FROM artifacts WHERE artifact_id=?
                """,
                (metadata["id"],),
            ).fetchone()
            if not row:
                raise TaskSnapshotError(
                    f"task snapshot source is not promoted: {prepared.relative_path}"
                )
            if (
                str(row["path"]) != prepared.relative_path
                or int(row["revision"]) != int(metadata["revision"])
                or str(row["state"]) != str(metadata["state"])
                or str(row["loop_id"] or "") != str(loop)
                or str(row["content_sha256"]) != prepared.content_sha256
            ):
                raise TaskSnapshotError(
                    f"task snapshot source/database mismatch: {prepared.relative_path}"
                )
            raw = path.read_bytes()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TaskSnapshotError(
                    f"task snapshot source is not UTF-8: {prepared.relative_path}"
                ) from exc
            member = _safe_member_name(prepared.relative_path)
            if member in members:
                raise TaskSnapshotError(f"duplicate task snapshot member: {member}")
            members[member] = raw
            entries.append(
                {
                    "artifact_id": str(metadata["id"]),
                    "path": prepared.relative_path,
                    "member": member,
                    "loop": loop,
                    "revision": int(metadata["revision"]),
                    "state": str(metadata["state"]),
                    "bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                    "content_sha256": prepared.content_sha256,
                }
            )
    finally:
        connection.close()
    entries.sort(key=lambda entry: (entry["path"], entry["artifact_id"]))
    return entries, members


def _verified_package(
    workspace: Path,
    path: Path,
    *,
    expected_loop: int | None = None,
    expected_previous: str | None | object = _UNSET,
) -> dict[str, Any]:
    path = path.resolve()
    workspace_relative(path, workspace)
    if not path.is_file():
        raise TaskSnapshotError(f"task snapshot package is missing: {workspace_relative(path, workspace)}")
    package_bytes = path.read_bytes()
    if len(package_bytes) > TASK_SNAPSHOT_MAX_PACKAGE_BYTES:
        raise TaskSnapshotError("task snapshot package exceeds its safety bound")
    package_sha256 = sha256_bytes(package_bytes)
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise TaskSnapshotError(f"task snapshot package is not a valid ZIP: {path}") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) > TASK_SNAPSHOT_MAX_ENTRIES:
            raise TaskSnapshotError("task snapshot member count exceeds its safety bound")
        if len(names) != len(set(names)):
            raise TaskSnapshotError("task snapshot package contains duplicate members")
        if TASK_SNAPSHOT_MANIFEST not in names:
            raise TaskSnapshotError("task snapshot manifest is missing")
        normalized_names = {_safe_member_name(name) for name in names}
        if len(normalized_names) != len(names):
            raise TaskSnapshotError("task snapshot package contains duplicate normalized members")
        try:
            manifest = json.loads(archive.read(TASK_SNAPSHOT_MANIFEST).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise TaskSnapshotError("task snapshot manifest is not valid UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise TaskSnapshotError("task snapshot manifest must be an object")
        if manifest.get("schema") != TASK_SNAPSHOT_SCHEMA:
            raise TaskSnapshotError("task snapshot manifest schema mismatch")
        if manifest.get("selection") != "current_loop_task_contracts_non_superseded":
            raise TaskSnapshotError("task snapshot selection contract is invalid")
        loop = manifest.get("loop")
        if isinstance(loop, bool) or not isinstance(loop, int) or loop < TASK_SNAPSHOT_START_LOOP:
            raise TaskSnapshotError("task snapshot manifest loop is invalid")
        if expected_loop is not None and loop != expected_loop:
            raise TaskSnapshotError(
                f"task snapshot loop mismatch: expected {expected_loop}, got {loop}"
            )
        archive_id = manifest.get("archive_id")
        if archive_id != f"ARCHIVE_L{loop:04d}":
            raise TaskSnapshotError("task snapshot archive identity mismatch")
        heartbeat_id = manifest.get("heartbeat_id")
        if not isinstance(heartbeat_id, str) or not heartbeat_id.strip():
            raise TaskSnapshotError("task snapshot heartbeat identity is missing")
        previous = manifest.get("previous_package_sha256")
        if previous is not None and (not isinstance(previous, str) or not _HASH_RE.fullmatch(previous)):
            raise TaskSnapshotError("task snapshot predecessor hash is invalid")
        if expected_previous is not _UNSET and previous != expected_previous:
            raise TaskSnapshotError("task snapshot predecessor hash does not match the chain")
        entries = manifest.get("entries")
        if not isinstance(entries, list) or len(entries) > TASK_SNAPSHOT_MAX_ENTRIES:
            raise TaskSnapshotError("task snapshot entries are invalid or exceed their bound")
        expected_members = {TASK_SNAPSHOT_MANIFEST}
        seen_paths: set[str] = set()
        total_bytes = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise TaskSnapshotError("task snapshot entry must be an object")
            required = {"artifact_id", "path", "member", "loop", "revision", "state", "bytes", "sha256", "content_sha256"}
            if set(entry) != required:
                raise TaskSnapshotError("task snapshot entry fields are invalid")
            member = _safe_member_name(str(entry["member"]))
            relative = _safe_member_name(str(entry["path"]))
            if member != relative or member == TASK_SNAPSHOT_MANIFEST or not member.startswith("tasks/"):
                raise TaskSnapshotError("task snapshot member does not bind a task source path")
            if relative in seen_paths:
                raise TaskSnapshotError("task snapshot contains duplicate task paths")
            seen_paths.add(relative)
            expected_members.add(member)
            if entry["loop"] != loop or isinstance(entry["revision"], bool) or not isinstance(entry["revision"], int) or entry["revision"] < 1:
                raise TaskSnapshotError("task snapshot entry identity is invalid")
            if not isinstance(entry["artifact_id"], str) or not entry["artifact_id"].strip():
                raise TaskSnapshotError("task snapshot artifact identity is invalid")
            if not isinstance(entry["state"], str) or entry["state"] == "superseded":
                raise TaskSnapshotError("task snapshot entry state is invalid")
            if isinstance(entry["bytes"], bool) or not isinstance(entry["bytes"], int) or entry["bytes"] < 0:
                raise TaskSnapshotError("task snapshot entry byte count is invalid")
            for field in ("sha256", "content_sha256"):
                if not isinstance(entry[field], str) or not _HASH_RE.fullmatch(entry[field]):
                    raise TaskSnapshotError(f"task snapshot entry {field} is invalid")
            if member not in normalized_names:
                raise TaskSnapshotError(f"task snapshot source member is missing: {member}")
            data = archive.read(member)
            total_bytes += len(data)
            if len(data) != entry["bytes"] or sha256_bytes(data) != entry["sha256"]:
                raise TaskSnapshotError(f"task snapshot source hash mismatch: {member}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TaskSnapshotError(f"task snapshot source is not UTF-8: {member}") from exc
            if sha256_text(text.replace("\r\n", "\n").replace("\r", "\n")) != entry["content_sha256"]:
                raise TaskSnapshotError(f"task snapshot normalized hash mismatch: {member}")
        if normalized_names != expected_members:
            raise TaskSnapshotError("task snapshot member set does not match its manifest")
        if total_bytes > TASK_SNAPSHOT_MAX_PACKAGE_BYTES:
            raise TaskSnapshotError("task snapshot expands beyond its safety bound")
        return {
            "required": True,
            "verified": True,
            "path": workspace_relative(path, workspace),
            "package_sha256": package_sha256,
            "previous_package_sha256": previous,
            "loop": loop,
            "archive_id": archive_id,
            "heartbeat_id": heartbeat_id,
            "entries": entries,
        }


def verify_task_snapshot_package(
    workspace: Path,
    path: Path,
    *,
    expected_loop: int | None = None,
    expected_previous: str | None | object = _UNSET,
) -> dict[str, Any]:
    """Verify one immutable task snapshot package and return its evidence."""
    return _verified_package(
        workspace.resolve(),
        path,
        expected_loop=expected_loop,
        expected_previous=expected_previous,
    )


def ensure_task_snapshot_package(
    workspace: Path,
    database: Any,
    loop: int,
    heartbeat_id: str,
) -> dict[str, Any]:
    """Create or verify the immutable package required for finalized loops >= 5."""
    if loop < TASK_SNAPSHOT_START_LOOP:
        return {
            "required": False,
            "verified": True,
            "loop": loop,
            "path": None,
            "package_sha256": None,
            "previous_package_sha256": None,
            "entries": [],
        }
    workspace = workspace.resolve()
    path = task_snapshot_path(workspace, loop)
    previous = _previous_package_hash(workspace, loop)
    if path.exists():
        verified = _verified_package(
            workspace,
            path,
            expected_loop=loop,
            expected_previous=previous,
        )
        current_entries, _ = _task_entries(workspace, database, loop)
        current_identity = [
            (entry["artifact_id"], entry["path"], entry["revision"], entry["sha256"])
            for entry in current_entries
        ]
        package_identity = [
            (entry["artifact_id"], entry["path"], entry["revision"], entry["sha256"])
            for entry in verified["entries"]
        ]
        if current_identity != package_identity:
            raise TaskSnapshotError(
                "existing task snapshot package does not match the current loop task sources"
            )
        return verified
    entries, members = _task_entries(workspace, database, loop)
    manifest = {
        "schema": TASK_SNAPSHOT_SCHEMA,
        "loop": loop,
        "archive_id": f"ARCHIVE_L{loop:04d}",
        "heartbeat_id": str(heartbeat_id),
        "selection": "current_loop_task_contracts_non_superseded",
        "previous_package_sha256": previous,
        "entries": entries,
    }
    package_bytes = _deterministic_zip(manifest, members)
    if len(package_bytes) > TASK_SNAPSHOT_MAX_PACKAGE_BYTES:
        raise TaskSnapshotError("task snapshot package exceeds its safety bound")
    _atomic_write_bytes(path, package_bytes)
    return _verified_package(
        workspace,
        path,
        expected_loop=loop,
        expected_previous=previous,
    )


def parse_task_snapshot_binding(text: str) -> dict[str, Any]:
    matches = list(_BINDING_RE.finditer(text))
    if len(matches) != 1:
        raise TaskSnapshotError(
            f"expected exactly one task snapshot binding line, found {len(matches)}"
        )
    path, package_sha256, previous = matches[0].groups()
    return {
        "path": path.replace("\\", "/"),
        "package_sha256": package_sha256,
        "previous_package_sha256": None if previous == "none" else previous,
    }


def verify_task_snapshot_history(
    workspace: Path,
    archive_documents: Iterable[Any],
) -> dict[str, Any]:
    """Verify the archive-bound task snapshot chain from Loop 5 onward."""
    workspace = workspace.resolve()
    archives: dict[int, Any] = {}
    failures: list[str] = []
    for document in archive_documents:
        metadata = document.frontmatter.metadata
        if metadata.get("type") != "archive" or metadata.get("state") != "finalized":
            continue
        loop = metadata.get("loop")
        if isinstance(loop, bool) or not isinstance(loop, int) or loop < TASK_SNAPSHOT_START_LOOP:
            continue
        if loop in archives:
            failures.append(f"duplicate finalized archive for loop {loop}")
        archives[loop] = document
    if not archives:
        return {
            "schema": "kairos-task-snapshot-history/v1",
            "verdict": "NOT_APPLICABLE",
            "required_from_loop": TASK_SNAPSHOT_START_LOOP,
            "finalized_loops": [],
            "checked_packages": [],
            "failures": [],
        }
    max_loop = max(archives)
    package_hashes: dict[int, str | None] = {}
    checked: list[dict[str, Any]] = []
    for loop in range(TASK_SNAPSHOT_START_LOOP, max_loop + 1):
        document = archives.get(loop)
        if document is None:
            failures.append(f"finalized archive loop {loop} is missing from task snapshot chain")
            package_hashes[loop] = None
            continue
        try:
            binding = parse_task_snapshot_binding(document.text)
            expected_path = task_snapshot_relative_path(loop)
            if binding["path"] != expected_path:
                raise TaskSnapshotError(
                    f"archive binds {binding['path']!r}, expected {expected_path!r}"
                )
            package_path = resolve_workspace_path(workspace, binding["path"])
            expected_previous = None if loop == TASK_SNAPSHOT_START_LOOP else package_hashes.get(loop - 1)
            verified = _verified_package(
                workspace,
                package_path,
                expected_loop=loop,
                expected_previous=expected_previous,
            )
            if verified["package_sha256"] != binding["package_sha256"]:
                raise TaskSnapshotError("archive task snapshot hash does not match package bytes")
            if verified["previous_package_sha256"] != binding["previous_package_sha256"]:
                raise TaskSnapshotError("archive task snapshot predecessor does not match package manifest")
            package_hashes[loop] = verified["package_sha256"]
            checked.append(
                {
                    "loop": loop,
                    "archive_id": verified["archive_id"],
                    "path": verified["path"],
                    "package_sha256": verified["package_sha256"],
                    "previous_package_sha256": verified["previous_package_sha256"],
                    "entries": len(verified["entries"]),
                    "verified": True,
                }
            )
        except (OSError, TaskSnapshotError, ValueError) as exc:
            failures.append(f"loop {loop}: {exc}")
            package_path = workspace / task_snapshot_relative_path(loop)
            package_hashes[loop] = (
                sha256_bytes(package_path.read_bytes()) if package_path.is_file() else None
            )
    return {
        "schema": "kairos-task-snapshot-history/v1",
        "verdict": "PASS" if not failures else "FAIL",
        "required_from_loop": TASK_SNAPSHOT_START_LOOP,
        "finalized_loops": sorted(archives),
        "checked_packages": checked,
        "failures": failures,
    }
