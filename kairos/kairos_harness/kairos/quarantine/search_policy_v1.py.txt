from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .database import KnowledgeDatabase
from .util import atomic_write_json, json_dumps, prune_oldest_files, read_json, sha256_text, utc_now
from .workspace import load_config


class SearchPolicyError(RuntimeError):
    pass


NORMAL_PURPOSES = {
    "implementation_verification",
    "exact_location",
    "contradiction",
    "negative_proof",
}
EXCEPTION_PURPOSES = {"exact_file_request", "metadata_repair"}
ALL_PURPOSES = NORMAL_PURPOSES | EXCEPTION_PURPOSES
MAX_ROUTING_AGE_SECONDS = 900
MAX_PERMIT_TTL_SECONDS = 900
MAX_SCOPE_PATHS = 8
MAX_SCOPE_PATTERNS = 8
MAX_SCOPE_FILES = 128
MAX_SCOPE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_INSPECTION_MATCHES = 200
MAX_EMERGENCY_RECEIPTS = 200
TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _runtime_scope(workspace: Path) -> tuple[str | None, str | None]:
    # Search policy must use the same effective scope as the central command
    # guard.  In terminal verification the runtime frontier intentionally has
    # no next criterion, while the last task criterion remains the bounded
    # authority only after DB coverage proves every task criterion complete.
    from .governance import runtime_scope

    scope = runtime_scope(workspace)
    task_id = scope.get("task_id")
    criterion_id = scope.get("criterion_id")
    return (
        str(task_id) if isinstance(task_id, str) and task_id else None,
        str(criterion_id) if isinstance(criterion_id, str) and criterion_id else None,
    )


def _record_violation(
    database: KnowledgeDatabase,
    workspace: Path,
    *,
    action_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    task_id, criterion_id = _runtime_scope(workspace)
    created_at = utc_now()
    payload = {"message": message, **(details or {})}
    violation_id = "SPV_" + sha256_text(
        f"{created_at}|{uuid.uuid4().hex}|{action_type}|{message}"
    )[:32]
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO search_policy_violations(
                violation_id,action_type,task_id,criterion_id,detail_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                violation_id,
                action_type,
                task_id,
                criterion_id,
                json_dumps(payload, pretty=False),
                created_at,
            ),
        )


def record_routing_receipt(
    workspace: Path,
    database: KnowledgeDatabase,
    search_result: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    task_id, criterion_id = _runtime_scope(workspace)
    primary = search_result.get("primary") or {}
    if not freshness.get("checked"):
        status = "STALE_DIAGNOSTIC"
    elif not task_id or not criterion_id:
        status = "UNSCOPED"
    elif not primary:
        status = "NO_MATCH"
    else:
        status = "ROUTED"
    created_at = utc_now()
    receipt_id = "SRR_" + sha256_text(
        f"{created_at}|{uuid.uuid4().hex}|{task_id}|{criterion_id}|{search_result['query']}"
    )[:32]
    freshness_record = {
        key: freshness.get(key)
        for key in (
            "checked",
            "refreshed",
            "reason",
            "heartbeat_id",
            "verified",
            "changed",
            "missing",
            "changed_goals",
            "missing_goals",
        )
        if key in freshness
    }
    receipt = {
        "schema": "kairos-search-routing-receipt/v1",
        "routing_receipt_id": receipt_id,
        "status": status,
        "task_id": task_id,
        "criterion_id": criterion_id,
        "query": search_result["query"],
        "query_frame": search_result["query_frame"],
        "mode": search_result["mode"],
        "freshness": freshness_record,
        "primary": (
            {
                key: primary.get(key)
                for key in (
                    "artifact_id",
                    "section_id",
                    "path",
                    "authority",
                    "document_type",
                    "goal_id",
                    "milestone_id",
                    "task_id",
                )
            }
            if primary
            else None
        ),
        "result_count": int(search_result["result_count"]),
        "created_at": created_at,
    }
    graph_context = search_result.get("graph_context")
    graph_identifiers = (
        graph_context.get("route_basis", {}).get("identifiers", [])
        if isinstance(graph_context, dict)
        else []
    )
    if graph_identifiers and int(graph_context.get("seed_total", 0)) > 0:
        primary_anchors = primary.get("graph_evidence_anchors") or [] if primary else []
        receipt["graph_route"] = {
            "schema": graph_context.get("schema"),
            "route_basis": graph_context.get("route_basis", {}),
            "table_totals": graph_context.get("table_totals", {}),
            "seed_total": int(graph_context.get("seed_total", 0)),
            "returned": int(graph_context.get("returned", 0)),
            "truncated": bool(graph_context.get("truncated", False)),
            "resolved_anchor_total": int(
                graph_context.get("resolved_anchor_total", 0)
            ),
            "unresolved_anchor_total": int(
                graph_context.get("unresolved_anchor_total", 0)
            ),
            "max_returned_identifier_specificity": int(
                graph_context.get("max_returned_identifier_specificity", 0)
            ),
            "resolved_at_max_specificity_total": int(
                graph_context.get("resolved_at_max_specificity_total", 0)
            ),
            "route_status": search_result.get("graph_route_status"),
            "primary_anchors": [
                {
                    key: anchor.get(key)
                    for key in (
                        "table", "artifact_id", "ordinal", "evidence_path", "matched_fields"
                    )
                }
                for anchor in primary_anchors[:8]
                if isinstance(anchor, dict)
            ],
        }
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO search_routing_receipts(
                routing_receipt_id,task_id,criterion_id,query_text,mode,status,
                primary_artifact_id,primary_section_id,primary_path,receipt_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                receipt_id,
                task_id,
                criterion_id,
                search_result["query"],
                search_result["mode"],
                status,
                primary.get("artifact_id"),
                primary.get("section_id"),
                primary.get("path"),
                json_dumps(receipt, pretty=False),
                created_at,
            ),
        )
    return receipt


def _inspection_root(workspace: Path) -> tuple[Path, str]:
    config = load_config(workspace)
    root_relative = str(config.get("inspection_root", ".")).replace("\\", "/")
    relative = Path(root_relative)
    if relative.is_absolute():
        raise SearchPolicyError("inspection_root must be relative to the KAIROS workspace")
    root = (workspace / relative).resolve()
    boundary = workspace.parent.resolve()
    try:
        root.relative_to(boundary)
    except ValueError as exc:
        raise SearchPolicyError("inspection_root escapes the configured project boundary") from exc
    if not root.is_dir():
        raise SearchPolicyError("inspection_root is not an existing directory")
    return root, root_relative


def _scoped_files(root: Path, requested_paths: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    if not 1 <= len(requested_paths) <= MAX_SCOPE_PATHS:
        raise SearchPolicyError(f"source scope requires between 1 and {MAX_SCOPE_PATHS} paths")
    normalized_paths: list[str] = []
    files: dict[str, Path] = {}
    for value in requested_paths:
        candidate_value = value.replace("\\", "/").strip()
        candidate = Path(candidate_value)
        if not candidate_value or candidate.is_absolute():
            raise SearchPolicyError("source scope paths must be non-empty and root-relative")
        resolved = (root / candidate).resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise SearchPolicyError(f"source scope path escapes inspection_root: {value}") from exc
        if resolved == root:
            raise SearchPolicyError("inspection_root itself is too broad for a source permit")
        if not resolved.exists():
            raise SearchPolicyError(f"source scope path does not exist: {value}")
        normalized_paths.append(relative)
        candidates = [resolved] if resolved.is_file() else sorted(path for path in resolved.rglob("*") if path.is_file())
        for path in candidates:
            real = path.resolve()
            try:
                file_relative = real.relative_to(root).as_posix()
            except ValueError as exc:
                raise SearchPolicyError(f"source scope contains an escaping link: {path}") from exc
            if real.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            files[file_relative] = real
            if len(files) > MAX_SCOPE_FILES:
                raise SearchPolicyError(f"source scope exceeds {MAX_SCOPE_FILES} text files")
    if not files:
        raise SearchPolicyError("source scope contains no supported text files")
    total_bytes = 0
    snapshot: list[dict[str, Any]] = []
    for relative, path in sorted(files.items()):
        stat = path.stat()
        if stat.st_size > MAX_FILE_BYTES:
            raise SearchPolicyError(f"source file exceeds {MAX_FILE_BYTES} bytes: {relative}")
        total_bytes += int(stat.st_size)
        if total_bytes > MAX_SCOPE_BYTES:
            raise SearchPolicyError(f"source scope exceeds {MAX_SCOPE_BYTES} bytes")
        snapshot.append(
            {
                "path": relative,
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    return sorted(set(normalized_paths)), snapshot


def _validate_patterns(patterns: list[str]) -> list[str]:
    if not 1 <= len(patterns) <= MAX_SCOPE_PATTERNS:
        raise SearchPolicyError(f"source scope requires between 1 and {MAX_SCOPE_PATTERNS} patterns")
    result: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 256:
            raise SearchPolicyError("source patterns must be non-empty and at most 256 characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SearchPolicyError(f"invalid source regex {pattern!r}: {exc}") from exc
        result.append(pattern)
    return result


def _scope_hash(snapshot: list[dict[str, Any]], patterns: list[str]) -> str:
    return sha256_text(json_dumps({"files": snapshot, "patterns": patterns}, pretty=False))


def _bounded_matches(
    root: Path,
    snapshot: list[dict[str, Any]],
    patterns: list[str],
) -> list[dict[str, Any]]:
    compiled = [re.compile(pattern) for pattern in patterns]
    matches: list[dict[str, Any]] = []
    for item in snapshot:
        path = root / item["path"]
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SearchPolicyError(f"source file is not UTF-8 text: {item['path']}") from exc
        for line_number, line in enumerate(text.splitlines(), 1):
            hit_patterns = [pattern.pattern for pattern in compiled if pattern.search(line)]
            if not hit_patterns:
                continue
            matches.append(
                {
                    "path": item["path"],
                    "line": line_number,
                    "patterns": hit_patterns,
                    "text": line[:500],
                }
            )
            if len(matches) >= MAX_INSPECTION_MATCHES:
                break
        if len(matches) >= MAX_INSPECTION_MATCHES:
            break
    return matches


def _record_emergency_violation(
    workspace: Path,
    *,
    action_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    task_id, criterion_id = _runtime_scope(workspace)
    created_at = utc_now()
    violation_id = "ESP_" + sha256_text(
        f"{uuid.uuid4().hex}|{created_at}|{action_type}|{message}"
    )[:32]
    directory = workspace / ".kairos" / "emergency_search_violations"
    atomic_write_json(
        directory / f"{violation_id}.json",
        {
            "schema": "kairos-emergency-search-violation/v1",
            "violation_id": violation_id,
            "action_type": action_type,
            "task_id": task_id,
            "criterion_id": criterion_id,
            "message": message,
            "details": details or {},
            "created_at": created_at,
        },
    )
    prune_oldest_files(directory, suffix=".json", keep=MAX_EMERGENCY_RECEIPTS)


def issue_emergency_metadata_repair_permit(
    workspace: Path,
    *,
    paths: list[str],
    patterns: list[str],
    reason: str,
    metadata_error: str,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    try:
        if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
            raise SearchPolicyError("metadata-repair reason must contain 12 to 1000 characters")
        if not isinstance(metadata_error, str) or not metadata_error.strip():
            raise SearchPolicyError("emergency metadata repair requires the observed metadata error")
        if not 30 <= ttl_seconds <= MAX_PERMIT_TTL_SECONDS:
            raise SearchPolicyError(f"permit TTL must be between 30 and {MAX_PERMIT_TTL_SECONDS} seconds")
        if not 1 <= len(paths) <= 4:
            raise SearchPolicyError("emergency metadata repair permits one to four exact files")
        task_id, criterion_id = _runtime_scope(workspace)
        if not task_id or not criterion_id:
            raise SearchPolicyError("emergency metadata repair requires an active task and criterion")
        root, root_relative = _inspection_root(workspace)
        normalized_paths, snapshot = _scoped_files(root, paths)
        if any((root / item).resolve().is_dir() for item in normalized_paths):
            raise SearchPolicyError("emergency metadata repair paths must be exact files")
        normalized_patterns = _validate_patterns(patterns)
        scope_sha256 = _scope_hash(snapshot, normalized_patterns)
        issued = datetime.now(timezone.utc)
        expires = issued + timedelta(seconds=ttl_seconds)
        permit_id = "EMR_" + sha256_text(
            f"{uuid.uuid4().hex}|{task_id}|{criterion_id}|{scope_sha256}|{metadata_error}"
        )[:32]
        receipt = {
            "schema": "kairos-emergency-metadata-repair-permit/v1",
            "permit_id": permit_id,
            "storage": "bounded-file-fallback",
            "exception": "metadata_repair",
            "task_id": task_id,
            "criterion_id": criterion_id,
            "reason": reason.strip(),
            "metadata_error": metadata_error.strip()[:1000],
            "root_relative": root_relative,
            "paths": normalized_paths,
            "patterns": normalized_patterns,
            "file_snapshot": snapshot,
            "scope_sha256": scope_sha256,
            "status": "ACTIVE",
            "issued_at": _iso_time(issued),
            "expires_at": _iso_time(expires),
        }
        directory = workspace / ".kairos" / "emergency_search_permits"
        atomic_write_json(directory / f"{permit_id}.json", receipt)
        prune_oldest_files(directory, suffix=".json", keep=MAX_EMERGENCY_RECEIPTS)
        return receipt
    except Exception as exc:
        error = exc if isinstance(exc, SearchPolicyError) else SearchPolicyError(str(exc))
        _record_emergency_violation(
            workspace,
            action_type="emergency_source_permit",
            message=str(error),
            details={"paths": paths, "metadata_error": metadata_error},
        )
        raise error


def execute_emergency_source_search(
    workspace: Path,
    *,
    permit_id: str,
) -> dict[str, Any]:
    try:
        if not permit_id.startswith("EMR_"):
            raise SearchPolicyError("invalid emergency metadata-repair permit identifier")
        permit_path = workspace / ".kairos" / "emergency_search_permits" / f"{permit_id}.json"
        permit = read_json(permit_path)
        if not isinstance(permit, dict) or permit.get("permit_id") != permit_id:
            raise SearchPolicyError("emergency metadata-repair permit does not exist")
        if permit.get("status") != "ACTIVE":
            raise SearchPolicyError(f"emergency metadata-repair permit is not active: {permit.get('status')}")
        if datetime.now(timezone.utc) > _parse_time(permit["expires_at"]):
            permit["status"] = "EXPIRED"
            atomic_write_json(permit_path, permit)
            raise SearchPolicyError("emergency metadata-repair permit has expired")
        task_id, criterion_id = _runtime_scope(workspace)
        if permit.get("task_id") != task_id or permit.get("criterion_id") != criterion_id:
            raise SearchPolicyError("emergency metadata-repair permit belongs to a different active task or criterion")
        root, root_relative = _inspection_root(workspace)
        if root_relative != permit.get("root_relative"):
            raise SearchPolicyError("inspection_root changed after emergency permit issuance")
        paths = list(permit["paths"])
        patterns = list(permit["patterns"])
        _, snapshot = _scoped_files(root, paths)
        if _scope_hash(snapshot, patterns) != permit.get("scope_sha256"):
            permit["status"] = "REVOKED"
            atomic_write_json(permit_path, permit)
            raise SearchPolicyError("emergency metadata-repair scope changed after issuance")
        permit["status"] = "CONSUMED"
        permit["consumed_at"] = utc_now()
        atomic_write_json(permit_path, permit)
        matches = _bounded_matches(root, snapshot, patterns)
        _, after_snapshot = _scoped_files(root, paths)
        stable = _scope_hash(after_snapshot, patterns) == permit["scope_sha256"]
        inspection_id = "ESR_" + sha256_text(
            f"{permit_id}|{permit['consumed_at']}|{len(matches)}|{stable}"
        )[:32]
        receipt = {
            "schema": "kairos-emergency-source-inspection-receipt/v1",
            "inspection_id": inspection_id,
            "permit_id": permit_id,
            "storage": "bounded-file-fallback",
            "exception": "metadata_repair",
            "task_id": task_id,
            "criterion_id": criterion_id,
            "status": "VERIFIED" if stable else "BLOCKED",
            "scope_stable": stable,
            "match_count": len(matches),
            "truncated": len(matches) >= MAX_INSPECTION_MATCHES,
            "matches": matches,
            "created_at": permit["consumed_at"],
        }
        permit["inspection"] = receipt
        atomic_write_json(permit_path, permit)
        if not stable:
            raise SearchPolicyError("emergency metadata-repair scope changed during inspection")
        return receipt
    except Exception as exc:
        error = exc if isinstance(exc, SearchPolicyError) else SearchPolicyError(str(exc))
        _record_emergency_violation(
            workspace,
            action_type="emergency_source_search",
            message=str(error),
            details={"permit_id": permit_id},
        )
        raise error


def issue_source_permit(
    workspace: Path,
    database: KnowledgeDatabase,
    *,
    routing_receipt_id: str | None,
    purpose: str,
    paths: list[str],
    patterns: list[str],
    reason: str,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    try:
        if purpose not in ALL_PURPOSES:
            raise SearchPolicyError(f"unsupported source-inspection purpose: {purpose}")
        if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
            raise SearchPolicyError("source-inspection reason must contain 12 to 1000 characters")
        if not 30 <= ttl_seconds <= MAX_PERMIT_TTL_SECONDS:
            raise SearchPolicyError(f"permit TTL must be between 30 and {MAX_PERMIT_TTL_SECONDS} seconds")
        task_id, criterion_id = _runtime_scope(workspace)
        if not task_id or not criterion_id:
            raise SearchPolicyError("source inspection requires an active task and criterion")
        route: dict[str, Any] | None = None
        if purpose in NORMAL_PURPOSES:
            if not routing_receipt_id:
                raise SearchPolicyError("normal source escalation requires a routing receipt")
            connection = database.connect(read_only=True)
            try:
                row = connection.execute(
                    "SELECT receipt_json FROM search_routing_receipts WHERE routing_receipt_id=?",
                    (routing_receipt_id,),
                ).fetchone()
                existing_permit = connection.execute(
                    "SELECT permit_id FROM source_inspection_permits WHERE routing_receipt_id=?",
                    (routing_receipt_id,),
                ).fetchone()
            finally:
                connection.close()
            if not row:
                raise SearchPolicyError("routing receipt does not exist")
            if existing_permit:
                raise SearchPolicyError("routing receipt has already been exchanged for a source permit")
            route = json.loads(row["receipt_json"])
            if route.get("status") != "ROUTED":
                raise SearchPolicyError(f"routing receipt status {route.get('status')!r} cannot authorize source inspection")
            if route.get("task_id") != task_id or route.get("criterion_id") != criterion_id:
                raise SearchPolicyError("routing receipt belongs to a different active task or criterion")
            age = (datetime.now(timezone.utc) - _parse_time(route["created_at"])).total_seconds()
            if age < 0 or age > MAX_ROUTING_AGE_SECONDS:
                raise SearchPolicyError("routing receipt is stale")
            freshness = route.get("freshness") or {}
            if not freshness.get("checked") or (
                freshness.get("refreshed") and not freshness.get("verified")
            ):
                raise SearchPolicyError("routing receipt lacks verified metadata freshness")
        else:
            if routing_receipt_id:
                raise SearchPolicyError("exception permits must not masquerade as normal routed escalation")
            if purpose == "exact_file_request" and len(paths) != 1:
                raise SearchPolicyError("exact-file exception requires exactly one file path")
            if purpose == "metadata_repair" and len(paths) > 4:
                raise SearchPolicyError("metadata-repair exception permits at most four exact file paths")

        root, root_relative = _inspection_root(workspace)
        normalized_paths, snapshot = _scoped_files(root, paths)
        if purpose in EXCEPTION_PURPOSES:
            if any((root / item).resolve().is_dir() for item in normalized_paths):
                raise SearchPolicyError("exception paths must identify exact files, not directories")
        normalized_patterns = _validate_patterns(patterns)
        scope_sha256 = _scope_hash(snapshot, normalized_patterns)
        issued = datetime.now(timezone.utc)
        expires = issued + timedelta(seconds=ttl_seconds)
        permit_id = "SIP_" + sha256_text(
            f"{uuid.uuid4().hex}|{routing_receipt_id}|{task_id}|{criterion_id}|{purpose}|{scope_sha256}"
        )[:32]
        receipt = {
            "schema": "kairos-source-inspection-permit/v1",
            "permit_id": permit_id,
            "routing_receipt_id": routing_receipt_id,
            "exception": purpose if purpose in EXCEPTION_PURPOSES else None,
            "task_id": task_id,
            "criterion_id": criterion_id,
            "purpose": purpose,
            "reason": reason.strip(),
            "root_relative": root_relative,
            "paths": normalized_paths,
            "patterns": normalized_patterns,
            "file_snapshot": snapshot,
            "scope_sha256": scope_sha256,
            "status": "ACTIVE",
            "issued_at": _iso_time(issued),
            "expires_at": _iso_time(expires),
        }
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_inspection_permits(
                    permit_id,routing_receipt_id,task_id,criterion_id,purpose,root_relative,
                    paths_json,patterns_json,scope_sha256,status,receipt_json,issued_at,
                    expires_at,consumed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,NULL)
                """,
                (
                    permit_id,
                    routing_receipt_id,
                    task_id,
                    criterion_id,
                    purpose,
                    root_relative,
                    json_dumps(normalized_paths, pretty=False),
                    json_dumps(normalized_patterns, pretty=False),
                    scope_sha256,
                    json_dumps(receipt, pretty=False),
                    receipt["issued_at"],
                    receipt["expires_at"],
                ),
            )
        return receipt
    except Exception as exc:
        error = exc if isinstance(exc, SearchPolicyError) else SearchPolicyError(str(exc))
        _record_violation(
            database,
            workspace,
            action_type="source_permit",
            message=str(error),
            details={"routing_receipt_id": routing_receipt_id, "purpose": purpose, "paths": paths},
        )
        raise error


def execute_source_search(
    workspace: Path,
    database: KnowledgeDatabase,
    *,
    permit_id: str,
) -> dict[str, Any]:
    try:
        connection = database.connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT * FROM source_inspection_permits WHERE permit_id=?",
                (permit_id,),
            ).fetchone()
        finally:
            connection.close()
        if not row:
            raise SearchPolicyError("source inspection permit does not exist")
        permit = json.loads(row["receipt_json"])
        if row["status"] != "ACTIVE":
            raise SearchPolicyError(f"source inspection permit is not active: {row['status']}")
        now = datetime.now(timezone.utc)
        if now > _parse_time(row["expires_at"]):
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE source_inspection_permits SET status='EXPIRED' WHERE permit_id=? AND status='ACTIVE'",
                    (permit_id,),
                )
            raise SearchPolicyError("source inspection permit has expired")
        task_id, criterion_id = _runtime_scope(workspace)
        if row["task_id"] != task_id or row["criterion_id"] != criterion_id:
            raise SearchPolicyError("source inspection permit belongs to a different active task or criterion")
        root, root_relative = _inspection_root(workspace)
        if root_relative != row["root_relative"]:
            raise SearchPolicyError("inspection_root changed after permit issuance")
        paths = json.loads(row["paths_json"])
        patterns = json.loads(row["patterns_json"])
        _, current_snapshot = _scoped_files(root, paths)
        if _scope_hash(current_snapshot, patterns) != row["scope_sha256"]:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE source_inspection_permits SET status='REVOKED' WHERE permit_id=? AND status='ACTIVE'",
                    (permit_id,),
                )
            raise SearchPolicyError("source scope changed after permit issuance")
        consumed_at = utc_now()
        with database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE source_inspection_permits
                SET status='CONSUMED',consumed_at=?
                WHERE permit_id=? AND status='ACTIVE'
                """,
                (consumed_at, permit_id),
            ).rowcount
            if updated != 1:
                raise SearchPolicyError("source inspection permit was consumed concurrently")

        matches = _bounded_matches(root, current_snapshot, patterns)
        _, after_snapshot = _scoped_files(root, paths)
        stable = _scope_hash(after_snapshot, patterns) == row["scope_sha256"]
        status = "VERIFIED" if stable else "BLOCKED"
        inspection_id = "SIR_" + sha256_text(f"{permit_id}|{consumed_at}|{len(matches)}|{status}")[:32]
        receipt = {
            "schema": "kairos-source-inspection-receipt/v1",
            "inspection_id": inspection_id,
            "permit_id": permit_id,
            "routing_receipt_id": permit.get("routing_receipt_id"),
            "exception": permit.get("exception"),
            "task_id": task_id,
            "criterion_id": criterion_id,
            "purpose": permit["purpose"],
            "paths": paths,
            "patterns": patterns,
            "status": status,
            "scope_stable": stable,
            "match_count": len(matches),
            "truncated": len(matches) >= MAX_INSPECTION_MATCHES,
            "matches": matches,
            "created_at": consumed_at,
        }
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_inspection_receipts(
                    inspection_id,permit_id,task_id,criterion_id,status,match_count,
                    receipt_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    inspection_id,
                    permit_id,
                    task_id,
                    criterion_id,
                    status,
                    len(matches),
                    json_dumps(receipt, pretty=False),
                    consumed_at,
                ),
            )
        if not stable:
            raise SearchPolicyError("source scope changed while inspection was running")
        return receipt
    except Exception as exc:
        error = exc if isinstance(exc, SearchPolicyError) else SearchPolicyError(str(exc))
        _record_violation(
            database,
            workspace,
            action_type="source_search",
            message=str(error),
            details={"permit_id": permit_id},
        )
        raise error
