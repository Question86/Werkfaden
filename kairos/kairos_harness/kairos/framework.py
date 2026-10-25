from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import KnowledgeDatabase
from .search import search_database

FRAMEWORK_WORKSPACE_ID = "KAIROS_FRAMEWORK"
FRAMEWORK_INDEX_WORKSPACE_ID = "KAIROS_FRAMEWORK_INDEX"
FRAMEWORK_VERSION = "0.1.0-alpha.6"
FRAMEWORK_DATABASE_NAME = "framework.db"
FRAMEWORK_MANIFEST_NAME = "framework_manifest.json"

# These are the authored framework sources admitted to the bootstrap corpus. Templates, project
# self-model history and generated release/runtime state are deliberately outside this surface.
FRAMEWORK_DOCUMENT_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    "workspace/docs/EVIDENCE_BASED_AI_REVIEW.md",
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/CANONICAL_DOCUMENTS.md",
    "docs/DATABASE_AND_DYNAMICS.md",
    "docs/EXTERNAL_AUDIT_SCOPE.md",
    "docs/FRAMEWORK_BOOTSTRAP.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/LOOP_PROCEDURE.md",
    "docs/OPERATING_MODEL.md",
    "docs/PORTABILITY_SCOPE.md",
    "docs/PROJECT_KICKSTART.md",
    "docs/UNIVERSAL_ONBOARDING.md",
    "docs/WORKSHOP.md",
    "kairos/kairos_harness/docs/CONTEXT_HEADER_SPEC.md",
    "kairos/kairos_harness/docs/INGESTED_DOCUMENT_SPEC.md",
    "kairos/kairos_harness/docs/LLM_OPERATING_CONTRACT.md",
    "kairos/kairos_harness/docs/OPERATIONS.md",
    "kairos/kairos_harness/docs/RETRIEVAL_METHOD.md",
    "kairos/kairos_harness/docs/SEARCH_INDEX.md",
    "kairos/kairos_harness/docs/SYSTEM_SYNTHESIS.md",
    "workshop/README.md",
    "workshop/OPERATIONS.md",
    "workshop/RECYCLING.md",
    "workshop/SECURITY_BOUNDARY.md",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sqlite_value(value: object) -> list[object]:
    """Encode SQLite values without depending on the host SQLite file format."""
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, str):
        return ["text", value]
    return [type(value).__name__, repr(value)]


def framework_database_content_sha256(path: Path | str | None = None) -> str:
    """Hash framework database content independently of SQLite build metadata.

    SQLite may change the file header, schema cookie, statistics tables, and page layout
    across operating systems or SQLite versions.  Those bytes remain useful for bundle
    integrity, but they are not a portable rebuild identity.  This digest covers the
    executable schema and row content while excluding SQLite's derived ``sqlite_stat*``
    tables.
    """
    database_path = Path(path) if path is not None else framework_database_path()
    resolved = database_path.resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_stat%' ORDER BY type, name"
        ).fetchall()
        canonical: list[object] = []
        for object_type, name, table_name, sql in objects:
            canonical.append(["schema", object_type, name, table_name, sql])
            if object_type != "table":
                continue
            identifier = '"' + str(name).replace('"', '""') + '"'
            try:
                rows = connection.execute(f"SELECT rowid, * FROM {identifier}").fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute(f"SELECT * FROM {identifier}").fetchall()
            encoded_rows = [
                [_canonical_sqlite_value(value) for value in row]
                for row in rows
            ]
            encoded_rows.sort(
                key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            )
            canonical.append(["rows", name, encoded_rows])
        payload = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return _sha256_bytes(payload)
    finally:
        connection.close()


def package_directory() -> Path:
    return Path(__file__).resolve().parent


def source_distribution_root() -> Path | None:
    """Return the source-release root when the package is running from the KAIROS tree."""
    candidate = package_directory().parents[2]
    if (candidate / "AGENTS.md").is_file() and (candidate / "docs").is_dir():
        return candidate
    return None


def framework_database_path() -> Path:
    return package_directory() / FRAMEWORK_DATABASE_NAME


def framework_manifest_path() -> Path:
    return package_directory() / FRAMEWORK_MANIFEST_NAME


def load_framework_manifest() -> dict[str, Any]:
    path = framework_manifest_path()
    if not path.is_file():
        raise RuntimeError(
            f"KAIROS framework manifest is missing: {path}. Reinstall the release or rebuild the framework index."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "kairos-framework-index/v1":
        raise RuntimeError(f"unsupported framework manifest schema: {payload.get('schema')!r}")
    if payload.get("workspace_id") != FRAMEWORK_WORKSPACE_ID:
        raise RuntimeError("framework manifest workspace identity mismatch")
    return payload


def verify_framework_bundle(*, verify_sources: bool = True) -> dict[str, Any]:
    manifest = load_framework_manifest()
    database_path = framework_database_path()
    if not database_path.is_file():
        raise RuntimeError(
            f"KAIROS framework database is missing: {database_path}. Reinstall the release or rebuild the framework index."
        )
    database_sha = _sha256_bytes(database_path.read_bytes())
    expected_database_sha = str(manifest.get("database_sha256", ""))
    if database_sha != expected_database_sha:
        raise RuntimeError(
            f"framework database SHA-256 mismatch: {database_sha} != {expected_database_sha}"
        )
    database_content_sha = framework_database_content_sha256(database_path)
    expected_database_content_sha = manifest.get("database_content_sha256")
    if expected_database_content_sha and database_content_sha != expected_database_content_sha:
        raise RuntimeError(
            "framework database content SHA-256 mismatch: "
            f"{database_content_sha} != {expected_database_content_sha}"
        )

    verified_sources = 0
    root = source_distribution_root() if verify_sources else None
    if root is not None:
        entries = manifest.get("documents", [])
        expected_paths = [entry.get("path") for entry in entries if isinstance(entry, dict)]
        if tuple(expected_paths) != FRAMEWORK_DOCUMENT_PATHS:
            raise RuntimeError("framework manifest document inventory does not match the executable inventory")
        for entry in entries:
            relative = str(entry["path"])
            source = root / relative
            if not source.is_file():
                raise RuntimeError(f"framework source is missing: {relative}")
            actual = _sha256_bytes(source.read_bytes())
            if actual != entry.get("sha256"):
                raise RuntimeError(
                    f"framework source SHA-256 mismatch for {relative}: {actual} != {entry.get('sha256')}"
                )
            verified_sources += 1

    return {
        "verified": True,
        "schema": manifest["schema"],
        "version": manifest.get("version", FRAMEWORK_VERSION),
        "database": str(database_path),
        "database_sha256": database_sha,
        "database_content_sha256": database_content_sha,
        "document_count": int(manifest.get("document_count", 0)),
        "source_documents_verified": verified_sources,
    }


def search_framework(
    query: str,
    *,
    limit: int = 10,
    candidate_limit: int = 100,
    mode: str = "breadth",
    max_hops: int = 2,
) -> dict[str, Any]:
    verification = verify_framework_bundle()
    database = KnowledgeDatabase(framework_database_path())
    result = search_database(
        database,
        query,
        limit=limit,
        candidate_limit=candidate_limit,
        mode=mode,
        max_hops=max_hops,
        record_trace=False,
    )
    result["corpus"] = "framework"
    result["framework"] = verification
    result["freshness"] = {
        "checked": True,
        "refreshed": False,
        "reason": "immutable hash-verified framework projection",
    }
    for item in result.get("results", []):
        item["corpus"] = "framework"
    if isinstance(result.get("primary"), dict):
        result["primary"]["corpus"] = "framework"
    for item in result.get("context_chase", []):
        item["corpus"] = "framework"
    return result
