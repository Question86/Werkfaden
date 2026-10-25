from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .backup import BACKUP_ID_RE, verify_backup
from .constants import (
    AUTHORITIES,
    DYNAMIC_CANONICAL_FILES,
    MAX_DYNAMIC_CRITERION_POINTERS,
    MAX_DYNAMIC_TASK_POINTERS,
    MAX_GENERATED_RECEIPT_FILES,
    MAX_GOVERNANCE_VIOLATIONS,
    MAX_GOVERNED_ACTION_RECEIPTS,
    MAX_QUARANTINE_HISTORY,
    MAX_RETRIEVAL_TRACES,
    MAX_TERMINAL_ACTION_PERMITS,
)
from .database import KnowledgeDatabase
from .goals import coverage_report, goal_source_paths, reconcile_goal_files, validate_goal
from .governance import governance_status
from .markdown import mask_markdown_code
from .promoter import prepare_document
from .reconcile import candidate_paths, reconcile_documents
from .search import search_database
from .task_snapshots import verify_task_snapshot_history
from .util import json_dumps, sha256_text, workspace_relative
from .workspace import database_path, load_config


ABSOLUTE_FILE_REFERENCE = re.compile(r"(?i)(?:file://|(?<![A-Za-z0-9_])[A-Z]:[\\/])")
LEGACY_ACTIVE_MARKERS: tuple[str, ...] = ()


def _normalized_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n"))


def health_audit(
    workspace: Path,
    *,
    full: bool = False,
    governance_exclude_action_id: str | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    config = load_config(workspace)
    host_workspace_id = str(config["workspace_id"])
    imported_workspace_ids = {
        str(value)
        for value in config.get("imported_workspace_ids", [])
        if isinstance(value, str)
    }
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    failures: list[str] = []
    warnings: list[str] = []
    governance = governance_status(
        workspace,
        exclude_action_id=governance_exclude_action_id,
    )
    if (
        governance["enforcement_mode"] != "required"
        and not config.get("governance_audit_allows_finalization", False)
    ):
        failures.append("governance enforcement is not required")
    if governance["open_quarantine"]:
        failures.append(
            f"governance quarantine is open: {governance['open_quarantine']} change(s)"
        )
    if governance["running_actions"]:
        failures.append(
            f"unfinished governed actions: {governance['running_actions']}"
        )
    documents = candidate_paths(workspace, config)
    validation: list[dict[str, Any]] = []
    prepared_by_path: dict[str, Any] = {}
    declared_revision_references: list[tuple[str, str, str]] = []
    imported_hygiene_findings: list[str] = []
    for path in documents:
        relative = workspace_relative(path, workspace)
        try:
            prepared = prepare_document(path, workspace)
            text = prepared.text
            accepted_import = prepared.external_origin in imported_workspace_ids
            if ABSOLUTE_FILE_REFERENCE.search(mask_markdown_code(text)):
                finding = f"outside-workspace file reference in {relative}"
                (imported_hygiene_findings if accepted_import else failures).append(finding)
            for marker in LEGACY_ACTIVE_MARKERS:
                if marker.casefold() in text.casefold():
                    finding = f"legacy active marker {marker!r} in {relative}"
                    (imported_hygiene_findings if accepted_import else failures).append(finding)
            prepared_by_path[relative] = prepared
            validation.append(
                {
                    "path": relative,
                    "id": prepared.frontmatter.metadata["id"],
                    "bytes": len(text.encode("utf-8")),
                    "sections": len(prepared.sections),
                    "references": len(prepared.references),
                }
            )
        except Exception as exc:
            failures.append(f"document validation failed for {relative}: {exc}")
    for source_path, prepared in sorted(prepared_by_path.items()):
        for _, reference in prepared.references:
            target = prepared_by_path.get(reference.target_path)
            if not target:
                continue
            target_metadata = target.frontmatter.metadata
            if reference.target_id and reference.target_id != target_metadata["id"]:
                failures.append(
                    f"reference identity drift in {source_path}: {reference.target_path} "
                    f"declares {reference.target_id}, actual {target_metadata['id']}"
                )
            if (
                prepared.external_origin not in imported_workspace_ids
                and reference.version not in {"dynamic", "live"}
                and reference.target_id
            ):
                declared_revision_references.append(
                    (source_path, reference.target_id, reference.version)
                )
    if imported_hygiene_findings:
        warnings.append(
            "accepted imported documents contain origin-local hygiene findings: "
            f"{len(imported_hygiene_findings)}"
        )
    task_snapshot_history = verify_task_snapshot_history(
        workspace,
        prepared_by_path.values(),
    )
    if task_snapshot_history["verdict"] == "FAIL":
        failures.append(
            "task snapshot history verification failed: "
            f"{task_snapshot_history['failures']}"
        )
    reconciliation = reconcile_documents(workspace)
    if reconciliation.changed or reconciliation.missing:
        failures.append(
            f"reconciliation drift changed={len(reconciliation.changed)} missing={len(reconciliation.missing)}"
        )
    goal_reconciliation = reconcile_goal_files(workspace)
    if goal_reconciliation.changed or goal_reconciliation.missing:
        failures.append(
            "goal reconciliation drift "
            f"changed={len(goal_reconciliation.changed)} missing={len(goal_reconciliation.missing)}"
        )
    goal_payloads: dict[str, dict[str, Any]] = {}
    for path in goal_source_paths(workspace):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_goal(payload)
            if payload["id"] in goal_payloads:
                failures.append(f"duplicate goal identifier: {payload['id']}")
            goal_payloads[payload["id"]] = payload
        except Exception as exc:
            failures.append(f"goal validation failed for {workspace_relative(path, workspace)}: {exc}")
    connection = database.connect(read_only=True)
    try:
        check_pragma = "PRAGMA integrity_check" if full else "PRAGMA quick_check"
        integrity = [str(row[0]) for row in connection.execute(check_pragma).fetchall()]
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        if integrity != ["ok"]:
            failures.append(f"database integrity failed: {integrity}")
        if foreign_keys:
            failures.append(f"database foreign-key violations: {len(foreign_keys)}")
        artifact_rows = connection.execute(
            "SELECT artifact_id,path,document_type,authority,workspace_id,goal_id,milestone_id,"
            "task_id,content_sha256 FROM artifacts ORDER BY path"
        ).fetchall()
        artifact_paths = {str(row["path"]): row for row in artifact_rows}
        source_paths = {workspace_relative(path, workspace): path for path in documents}
        unpromoted = sorted(set(source_paths) - set(artifact_paths))
        missing_sources = sorted(set(artifact_paths) - set(source_paths))
        hash_mismatches = sorted(
            relative
            for relative in set(source_paths) & set(artifact_paths)
            if _normalized_hash(source_paths[relative]) != artifact_paths[relative]["content_sha256"]
        )
        unknown_authorities = sorted(
            {str(row["authority"]) for row in artifact_rows if row["authority"] not in AUTHORITIES}
        )
        revision_ledger = {
            (str(row["artifact_id"]), str(row["revision"]))
            for row in connection.execute(
                "SELECT artifact_id,revision FROM artifact_revisions"
            )
        }
        missing_reference_revisions = sorted(
            {
                f"{source_path}->{target_id}@{version}"
                for source_path, target_id, version in declared_revision_references
                if (target_id, version) not in revision_ledger
            }
        )
        if missing_reference_revisions:
            warnings.append(
                "historical reference revision gaps are non-operational evidence only: "
                f"{len(missing_reference_revisions)}"
            )
        if unpromoted:
            failures.append(f"unpromoted source documents: {unpromoted}")
        if missing_sources:
            failures.append(f"database artifacts without sources: {missing_sources}")
        if hash_mismatches:
            failures.append(f"source/database hash mismatches: {hash_mismatches}")
        if unknown_authorities:
            failures.append(f"unknown authority values: {unknown_authorities}")
        accepted_imported_artifacts = sorted(
            str(row["artifact_id"])
            for row in artifact_rows
            if row["workspace_id"] in imported_workspace_ids
        )
        foreign_workspace_artifacts = sorted(
            str(row["artifact_id"])
            for row in artifact_rows
            if row["workspace_id"] != host_workspace_id
            and row["workspace_id"] not in imported_workspace_ids
        )
        scope_violations = [
            str(row["artifact_id"])
            for row in connection.execute(
                """
                SELECT a.artifact_id
                FROM artifacts a
                LEFT JOIN milestones m ON m.milestone_id=a.milestone_id
                LEFT JOIN goals g ON g.goal_id=a.goal_id
                WHERE a.workspace_id=?
                  AND (a.goal_id IS NOT NULL OR a.milestone_id IS NOT NULL)
                  AND (
                    a.goal_id IS NULL OR a.milestone_id IS NULL OR
                    g.goal_id IS NULL OR m.milestone_id IS NULL OR m.goal_id<>a.goal_id
                  )
                ORDER BY a.artifact_id
                """,
                (host_workspace_id,),
            )
        ]
        parent_task_violations = [
            str(row["artifact_id"])
            for row in connection.execute(
                """
                SELECT a.artifact_id
                FROM artifacts a
                LEFT JOIN artifacts t ON t.artifact_id=a.task_id
                WHERE a.workspace_id=?
                  AND a.document_type<>'task' AND a.task_id IS NOT NULL
                  AND (
                    t.artifact_id IS NULL OR t.document_type<>'task' OR
                    t.goal_id<>a.goal_id OR t.milestone_id<>a.milestone_id
                  )
                ORDER BY a.artifact_id
                """,
                (host_workspace_id,),
            )
        ]
        if foreign_workspace_artifacts:
            failures.append(f"foreign workspace artifacts: {foreign_workspace_artifacts}")
        if scope_violations:
            failures.append(f"goal or milestone ownership violations: {scope_violations}")
        if parent_task_violations:
            failures.append(f"parent task ownership violations: {parent_task_violations}")
        goal_rows = connection.execute(
            "SELECT goal_id,metadata_json FROM goals ORDER BY goal_id"
        ).fetchall()
        database_goal_ids = {str(row["goal_id"]) for row in goal_rows}
        source_goal_ids = set(goal_payloads)
        goal_missing_database = sorted(source_goal_ids - database_goal_ids)
        goal_missing_source = sorted(database_goal_ids - source_goal_ids)
        goal_metadata_mismatches = sorted(
            str(row["goal_id"])
            for row in goal_rows
            if row["goal_id"] in goal_payloads
            and str(row["metadata_json"]) != json_dumps(goal_payloads[str(row["goal_id"])], pretty=False)
        )
        if goal_missing_database:
            failures.append(f"goal sources absent from database: {goal_missing_database}")
        if goal_missing_source:
            failures.append(f"database goals without sources: {goal_missing_source}")
        if goal_metadata_mismatches:
            failures.append(f"goal source/database metadata mismatches: {goal_metadata_mismatches}")
        expected_milestones: dict[str, tuple[Any, ...]] = {}
        expected_criteria: dict[str, tuple[Any, ...]] = {}
        for payload in goal_payloads.values():
            for sequence, milestone in enumerate(payload["milestones"], 1):
                expected_milestones[milestone["id"]] = (
                    payload["id"],
                    milestone["title"],
                    milestone["objective"],
                    milestone["state"],
                    sequence,
                    json_dumps(milestone.get("depends_on", []), pretty=False),
                )
                for criterion in milestone["criteria"]:
                    expected_criteria[criterion["id"]] = (
                        milestone["id"],
                        criterion["description"],
                        criterion["state"],
                        criterion["evidence_required"],
                        json_dumps(criterion.get("required_artifact_types", []), pretty=False),
                    )
        actual_milestones = {
            str(row["milestone_id"]): (
                str(row["goal_id"]),
                str(row["title"]),
                str(row["objective"]),
                str(row["state"]),
                int(row["sequence"]),
                str(row["depends_on_json"]),
            )
            for row in connection.execute(
                "SELECT milestone_id,goal_id,title,objective,state,sequence,depends_on_json FROM milestones"
            )
        }
        actual_criteria = {
            str(row["criterion_id"]): (
                str(row["milestone_id"]),
                str(row["description"]),
                str(row["state"]),
                str(row["evidence_required"]),
                str(row["required_artifact_types_json"]),
            )
            for row in connection.execute(
                "SELECT criterion_id,milestone_id,description,state,evidence_required,"
                "required_artifact_types_json FROM criteria"
            )
        }
        milestone_drift = sorted(
            set(expected_milestones) ^ set(actual_milestones)
            | {
                identifier
                for identifier in set(expected_milestones) & set(actual_milestones)
                if expected_milestones[identifier] != actual_milestones[identifier]
            }
        )
        criterion_drift = sorted(
            set(expected_criteria) ^ set(actual_criteria)
            | {
                identifier
                for identifier in set(expected_criteria) & set(actual_criteria)
                if expected_criteria[identifier] != actual_criteria[identifier]
            }
        )
        if milestone_drift:
            failures.append(f"milestone source/database drift: {milestone_drift}")
        if criterion_drift:
            failures.append(f"criterion source/database drift: {criterion_drift}")
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "artifacts",
                "artifact_revisions",
                "sections",
                "relations",
                "query_handles",
                "promotion_events",
                "promotion_receipts",
                "heartbeat_receipts",
                "retrieval_traces",
                "action_permits",
                "governed_action_receipts",
                "governance_violations",
                "quarantine_entries",
            )
        }
        orphan_terminal_permits = int(connection.execute(
            """
            SELECT count(*) FROM action_permits p
            WHERE p.status<>'ACTIVE' AND NOT EXISTS (
                SELECT 1 FROM governed_action_receipts r WHERE r.permit_id=p.permit_id
            )
            """
        ).fetchone()[0])
        resolved_quarantine = int(connection.execute(
            "SELECT count(*) FROM quarantine_entries WHERE status<>'OPEN'"
        ).fetchone()[0])
        if counts["governed_action_receipts"] > MAX_GOVERNED_ACTION_RECEIPTS:
            failures.append(
                f"governed action retention exceeded: {counts['governed_action_receipts']}"
            )
        if counts["governance_violations"] > MAX_GOVERNANCE_VIOLATIONS:
            failures.append(
                f"governance violation retention exceeded: {counts['governance_violations']}"
            )
        if orphan_terminal_permits > MAX_TERMINAL_ACTION_PERMITS:
            failures.append(
                f"terminal action permit retention exceeded: {orphan_terminal_permits}"
            )
        if resolved_quarantine > MAX_QUARANTINE_HISTORY:
            failures.append(
                f"resolved quarantine retention exceeded: {resolved_quarantine}"
            )
        fts_missing = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT artifact_id,section_id FROM sections
                    EXCEPT
                    SELECT artifact_id,section_id FROM section_fts
                )
                """
            ).fetchone()[0]
        )
        fts_orphans = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT artifact_id,section_id FROM section_fts
                    EXCEPT
                    SELECT artifact_id,section_id FROM sections
                )
                """
            ).fetchone()[0]
        )
        if fts_missing or fts_orphans:
            failures.append(
                f"section FTS parity failed: missing={fts_missing} orphans={fts_orphans}"
            )
        if counts["retrieval_traces"] > MAX_RETRIEVAL_TRACES:
            failures.append(
                f"retrieval trace retention exceeded: {counts['retrieval_traces']}"
            )
        pragmas = {
            name: connection.execute(f"PRAGMA {name}").fetchone()[0]
            for name in (
                "journal_mode",
                "synchronous",
                "page_count",
                "page_size",
                "freelist_count",
                "wal_autocheckpoint",
                "journal_size_limit",
            )
        }
        indexes = int(
            connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%'"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    dynamic: dict[str, Any] = {}
    for relative in sorted(DYNAMIC_CANONICAL_FILES):
        path = workspace / relative
        text = path.read_text(encoding="utf-8")
        task_pointer_lines = sum(1 for line in text.splitlines() if "[ref:tasks/" in line)
        criterion_lines = sum(1 for line in text.splitlines() if line.lstrip().startswith("- `CRIT_"))
        bounded = (
            task_pointer_lines <= MAX_DYNAMIC_TASK_POINTERS * 2
            and criterion_lines <= MAX_DYNAMIC_CRITERION_POINTERS
            and path.stat().st_size <= 64 * 1024
        )
        if not bounded:
            failures.append(f"dynamic canonical exceeded bounds: {relative}")
        dynamic[relative] = {
            "bytes": path.stat().st_size,
            "task_pointer_lines": task_pointer_lines,
            "criterion_lines": criterion_lines,
            "bounded": bounded,
        }
    generated_counts = {}
    for relative in (".kairos/events", ".kairos/receipts", ".kairos/heartbeats"):
        directory = workspace / relative
        count = sum(1 for path in directory.glob("*.json") if path.is_file())
        generated_counts[relative] = count
        if count > MAX_GENERATED_RECEIPT_FILES:
            failures.append(f"generated receipt retention exceeded in {relative}: {count}")
    queries = (
        "Where does workspace orientation begin?",
        "Which authority decides current state?",
        "How do documents become searchable without a full scan?",
    )
    benchmarks = []
    for query in queries:
        started = time.perf_counter()
        result = search_database(database, query, limit=10, candidate_limit=100, record_trace=False)
        elapsed = (time.perf_counter() - started) * 1000
        benchmarks.append({"query": query, "elapsed_ms": round(elapsed, 3), "results": result["result_count"]})
        if elapsed > 250:
            warnings.append(f"search exceeded 250 ms: {query!r} ({elapsed:.1f} ms)")
    coverage = coverage_report(database)
    completed_coverage_violations = [
        row["criterion_id"]
        for row in coverage
        if row["declared_state"] == "completed" and not row["requirements_met"]
    ]
    if completed_coverage_violations:
        failures.append(
            "completed criteria missing declared evidence classes: "
            f"{completed_coverage_violations}"
        )
    backups_root = workspace / "backups"
    packages = (
        sorted(
            (path for path in backups_root.iterdir() if path.is_dir() and BACKUP_ID_RE.fullmatch(path.name)),
            key=lambda path: path.name,
            reverse=True,
        )
        if backups_root.exists()
        else []
    )
    try:
        backup = verify_backup(workspace, packages[0].name) if packages else None
    except Exception as exc:
        backup = None
        failures.append(f"latest backup could not be safely verified: {exc}")
    if not backup:
        warnings.append("no verified local KAIROS backup package exists")
    elif not backup["verified"]:
        failures.append(f"latest backup failed verification: {backup['failures']}")
    db_path = database_path(workspace)
    files = {
        "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "wal_bytes": Path(str(db_path) + "-wal").stat().st_size if Path(str(db_path) + "-wal").exists() else 0,
        "shm_bytes": Path(str(db_path) + "-shm").stat().st_size if Path(str(db_path) + "-shm").exists() else 0,
    }
    return {
        "schema": "kairos-health-audit/v1",
        "workspace": str(workspace),
        "verdict": "PASS" if not failures else "FAIL",
        "full_integrity_check": full,
        "failures": failures,
        "warnings": warnings,
        "governance": governance,
        "documents": {
            "validated": len(validation),
            "unpromoted": unpromoted,
            "missing_sources": missing_sources,
            "hash_mismatches": hash_mismatches,
            "imported_hygiene_findings": imported_hygiene_findings,
        },
        "goals": {
            "source_count": len(goal_payloads),
            "database_count": len(database_goal_ids),
            "missing_database": goal_missing_database,
            "missing_source": goal_missing_source,
            "metadata_mismatches": goal_metadata_mismatches,
            "milestone_drift": milestone_drift,
            "criterion_drift": criterion_drift,
            "coverage": coverage,
        },
        "database": {
            "integrity": integrity,
            "foreign_key_violations": foreign_keys,
            "counts": counts,
            "governance_retention": {
                "orphan_terminal_permits": orphan_terminal_permits,
                "resolved_quarantine": resolved_quarantine,
                "max_action_receipts": MAX_GOVERNED_ACTION_RECEIPTS,
                "max_terminal_permits": MAX_TERMINAL_ACTION_PERMITS,
                "max_violations": MAX_GOVERNANCE_VIOLATIONS,
                "max_resolved_quarantine": MAX_QUARANTINE_HISTORY,
            },
            "pragmas": pragmas,
            "indexes": indexes,
            "section_fts_parity": {"missing": fts_missing, "orphans": fts_orphans},
            "reference_revision_bindings": {
                "checked": len(declared_revision_references),
                "missing": missing_reference_revisions,
                "gating": False,
                "disposition": "historical_evidence_only",
            },
            "ownership": {
                "accepted_imported_artifacts": {
                    "count": len(accepted_imported_artifacts),
                    "workspace_ids": sorted(imported_workspace_ids),
                },
                "foreign_workspace_artifacts": foreign_workspace_artifacts,
                "goal_or_milestone_violations": scope_violations,
                "parent_task_violations": parent_task_violations,
            },
            "files": files,
        },
        "dynamic_canonicals": dynamic,
        "generated_file_counts": generated_counts,
        "search_benchmarks": benchmarks,
        "latest_backup": backup,
        "task_snapshot_history": task_snapshot_history,
    }
