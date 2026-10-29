from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .backup import create_backup, recover_artifact_revisions, restore_drill, verify_backup
from .database import KnowledgeDatabase
from .freshness import ensure_workspace_fresh
from .framework import search_framework, verify_framework_bundle
from .frontmatter import render_frontmatter, split_frontmatter
from .golden import (
    build_golden_template,
    build_framework_distribution,
    materialize_golden_seed,
    render_goal_prompt,
    verify_golden_template,
)
from .goals import coverage_report, validate_goal
from .graph import DEFAULT_GRAPH_ROWS, run_graph_query
from .governance import (
    COMMAND_PHASES,
    assert_command_classification,
    attribute_quarantined_changes,
    begin_cli_action,
    enable_governance,
    finish_cli_action,
    governance_status,
    issue_external_edit_permit,
    recover_orphaned_action,
    revoke_action_permit,
)
from .heartbeat import finalize_workspace, run_heartbeat, status_report
from .health import health_audit
from .locking import workspace_write_lock
from .loops import kickoff_project, start_new_loop
from .promoter import prepare_document, prepare_rendered_document, promote_many
from .reconcile import reconcile_documents
from .recovery import rebuild_derived_state
from .search import search_database
from .search_policy import (
    execute_emergency_source_search,
    execute_source_search,
    issue_emergency_metadata_repair_permit,
    issue_source_permit,
    record_routing_receipt,
)
from .templates import (
    bug_document,
    code_document,
    decision_document,
    pointer,
    report_document,
    research_document,
    task_document,
)
from .util import atomic_write_json, atomic_write_text, json_dumps, read_json, resolve_workspace_path, utc_now
from .workspace import database_path, initialize_workspace, load_config


def _workspace(value: str) -> Path:
    return Path(value).resolve()


def _current_loop(workspace: Path) -> int:
    current = read_json(workspace / "current.json")
    if not isinstance(current, dict):
        raise ValueError("current.json must be an object before creating a task")
    loop = current.get("loop")
    if isinstance(loop, bool) or not isinstance(loop, int) or loop < 1:
        raise ValueError("current.json must declare a positive integer loop before creating a task")
    return loop


def _database(workspace: Path) -> KnowledgeDatabase:
    load_config(workspace)
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    return database


def _require_id(value: str, prefix: str) -> str:
    if not re.fullmatch(rf"{re.escape(prefix)}_[A-Z0-9_.:-]+", value):
        raise ValueError(f"identifier must match {prefix}_[A-Z0-9_.:-]+: {value!r}")
    return value


def _print(payload: Any) -> None:
    print(json_dumps(payload), end="")


def _all_documents(workspace: Path) -> list[Path]:
    config = load_config(workspace)
    result: set[Path] = set()
    for relative in config.get("canonical_files", []):
        path = workspace / relative
        if path.exists():
            result.add(path.resolve())
    for relative in config.get("document_roots", []):
        root = workspace / relative
        if root.exists():
            result.update(path.resolve() for path in root.rglob("*.md") if path.is_file())
    return sorted(result)


def _goal_milestone(workspace: Path, goal_id: str, milestone_id: str) -> tuple[dict, dict]:
    goal_path = workspace / "goals" / f"{goal_id}.json"
    if not goal_path.exists():
        raise ValueError(f"goal file does not exist: {goal_path}")
    goal = json.loads(goal_path.read_text(encoding="utf-8"))
    validate_goal(goal)
    if goal["id"] != goal_id:
        raise ValueError(f"goal identity mismatch in {goal_path}: {goal['id']!r}")
    milestone = next((value for value in goal["milestones"] if value["id"] == milestone_id), None)
    if not milestone:
        raise ValueError(f"milestone {milestone_id!r} is not part of goal {goal_id!r}")
    return goal, milestone


def _validate_new_artifact_scope(
    workspace: Path,
    *,
    task_id: str,
    goal_id: str,
    milestone_id: str,
    criteria: list[str],
) -> None:
    goal, milestone = _goal_milestone(workspace, goal_id, milestone_id)
    if goal["state"] in {"completed", "superseded"} or milestone["state"] in {
        "completed",
        "superseded",
    }:
        raise ValueError(f"cannot add evidence to closed scope {goal_id}/{milestone_id}")
    task_path = workspace / "tasks" / f"task_{task_id}.md"
    if not task_path.exists():
        raise ValueError(f"parent task does not exist: {task_path}")
    task_metadata = split_frontmatter(task_path.read_text(encoding="utf-8")).metadata
    if (
        task_metadata.get("id") != task_id
        or task_metadata.get("type") != "task"
        or task_metadata.get("goal") != goal_id
        or task_metadata.get("milestone") != milestone_id
    ):
        raise ValueError(f"parent task scope does not match {goal_id}/{milestone_id}: {task_id}")
    known_criteria = {value["id"] for value in milestone["criteria"]}
    unknown = sorted(set(criteria) - known_criteria)
    if unknown:
        raise ValueError(
            f"artifact criteria are not part of {milestone_id}: {', '.join(unknown)}"
        )


def _complete_goal_contract_for_task(
    workspace: Path,
    *,
    goal_id: str,
    milestone_id: str,
    criteria: list[str],
) -> Path:
    goal_path = workspace / "goals" / f"{goal_id}.json"
    goal, milestone = _goal_milestone(workspace, goal_id, milestone_id)
    criterion_by_id = {value["id"]: value for value in milestone["criteria"]}
    unknown = sorted(set(criteria) - set(criterion_by_id))
    if unknown:
        raise ValueError(f"task closure references unknown criteria: {', '.join(unknown)}")
    for criterion_id in criteria:
        if criterion_by_id[criterion_id]["state"] != "waived":
            criterion_by_id[criterion_id]["state"] = "completed"
    if all(value["state"] in {"completed", "waived"} for value in milestone["criteria"]):
        milestone["state"] = "completed"
    if all(value["state"] in {"completed", "superseded"} for value in goal["milestones"]):
        goal["state"] = "completed"
    validate_goal(goal)
    atomic_write_json(goal_path, goal)
    return goal_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kairos", description="KAIROS autonomous context and goal harness")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create and promote a prepared KAIROS workspace")
    init.add_argument("--workspace", required=True)
    init.add_argument("--workspace-id", default="KAIROS_WORKSPACE")

    rebuild = sub.add_parser(
        "rebuild-derived",
        help="Reconstruct an absent database and dynamic state from authoritative tracked sources",
    )
    rebuild.add_argument("--workspace", required=True)
    rebuild.add_argument("--history-backup", default="")
    rebuild.add_argument("--history-ref", action="append", default=[])

    status = sub.add_parser("status", help="Show runtime, database, and goal coverage state")
    status.add_argument("--workspace", required=True)
    status.add_argument("--no-refresh", action="store_true", help="Inspect a potentially stale projection")

    validate = sub.add_parser("validate", help="Validate headers, anchors, answer handles, and references")
    validate.add_argument("--workspace", required=True)
    validate.add_argument("paths", nargs="*")
    validate.add_argument("--all", action="store_true")

    promote = sub.add_parser("promote", help="Diagnostic direct promotion; normal work should use heartbeat")
    promote.add_argument("--workspace", required=True)
    promote.add_argument("paths", nargs="+")

    reconcile = sub.add_parser("reconcile", help="Read-only bounded document reconciliation preview")
    reconcile.add_argument("--workspace", required=True)
    reconcile.add_argument("--verify-content-hashes", action="store_true", help="Check actual source bytes against promoted SQL hashes")

    heartbeat = sub.add_parser("heartbeat", help="Run work-document-promote-verify-checkpoint cycle")
    heartbeat.add_argument("--workspace", required=True)
    heartbeat.add_argument("--mode", choices=("auto", "work", "depth", "breadth", "breathe", "verify"), default="auto")
    heartbeat.add_argument("--changed", action="append", default=[])
    heartbeat.add_argument("--no-reconcile", action="store_true")
    heartbeat.add_argument("--metrics-json", default="")

    search = sub.add_parser("search", help="Question-first section search with typed context chase")
    search.add_argument("--workspace", default="", help="Project workspace. Omit before project intake to query the bundled framework corpus.")
    search.add_argument("query")
    search.add_argument("--mode", choices=("depth", "breadth", "breathe", "work", "verify"), default="breadth")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--candidate-limit", type=int, default=100)
    search.add_argument("--max-hops", type=int, default=2)
    search.add_argument("--no-refresh", action="store_true", help="Query a potentially stale projection")
    search.add_argument("--verify-content-hashes", action="store_true", help="Strict source SHA-256 reconciliation (O(managed corpus bytes))")
    trace = search.add_mutually_exclusive_group()
    trace.add_argument("--trace", action="store_true", help="Persist this retrieval trace")
    trace.add_argument("--no-trace", action="store_true", help="Do not persist a BREATHE trace")

    source_permit = sub.add_parser("source-permit", help="Exchange a fresh routing receipt or narrow exception for one bounded source search")
    source_permit.add_argument("--workspace", required=True)
    source_permit.add_argument("--routing-receipt", default="")
    source_permit.add_argument(
        "--purpose",
        required=True,
        choices=(
            "implementation_verification",
            "exact_location",
            "contradiction",
            "negative_proof",
            "exact_file_request",
            "metadata_repair",
        ),
    )
    source_permit.add_argument("--path", action="append", required=True)
    source_permit.add_argument("--pattern", action="append", required=True)
    source_permit.add_argument("--reason", required=True)
    source_permit.add_argument("--ttl-seconds", type=int, default=300)
    source_permit.add_argument(
        "--emergency",
        action="store_true",
        help="Use only for metadata_repair when SQLite is unavailable or known unhealthy",
    )
    source_permit.add_argument("--metadata-error", default="")

    source_search = sub.add_parser("source-search", help="Consume one source permit and return bounded line matches")
    source_search.add_argument("--workspace", required=True)
    source_search.add_argument("--permit", required=True)

    governance_status_parser = sub.add_parser(
        "governance-status",
        help="Show current phase, scope, permits, violations, and quarantine state",
    )
    governance_status_parser.add_argument("--workspace", required=True)

    action_permit = sub.add_parser(
        "action-permit",
        help="Issue one expiring exact-path permit for an external document edit",
    )
    action_permit.add_argument("--workspace", required=True)
    action_permit.add_argument("--path", action="append", required=True)
    action_permit.add_argument("--reason", required=True)
    action_permit.add_argument("--ttl-seconds", type=int, default=900)

    revoke_permit = sub.add_parser(
        "revoke-permit",
        help="Withdraw an outstanding action permit whose edit will not be made",
    )
    revoke_permit.add_argument("--workspace", required=True)
    revoke_permit.add_argument("--permit", required=True)
    revoke_permit.add_argument("--reason", required=True)

    recover_action = sub.add_parser(
        "recover-action",
        help="Terminalize one exact foreign orphaned RUNNING action as FAILED",
    )
    recover_action.add_argument("--workspace", required=True)
    recover_action.add_argument("--action", required=True)
    recover_action.add_argument("--reason", required=True)

    recover_revisions = sub.add_parser(
        "recover-revisions",
        help="Recover exact immutable revision rows from one verified backup",
    )
    recover_revisions.add_argument("--workspace", required=True)
    recover_revisions.add_argument("--backup-id", required=True)
    recover_revisions.add_argument("--ref", action="append", required=True)

    attribute_change = sub.add_parser(
        "attribute-change",
        help="Review, attribute, and promote the current bytes of one or more quarantined paths",
    )
    attribute_change.add_argument("--workspace", required=True)
    attribute_change.add_argument("--path", action="append", required=True)
    attribute_change.add_argument("--reason", required=True)
    attribute_change.add_argument(
        "--mode",
        choices=("work", "depth", "breadth", "breathe", "verify"),
        default="verify",
    )

    governance_enable = sub.add_parser(
        "governance-enable",
        help="Switch a clean active workspace from audit migration to required enforcement",
    )
    governance_enable.add_argument("--workspace", required=True)

    graph = sub.add_parser("graph", help="Bounded read over the normalized graph layer")
    graph.add_argument("--workspace", required=True)
    graph.add_argument("--artifact", default="", help="Every declared structure of one document")
    graph.add_argument("--node", default="", help="Edges naming one entity in either position")
    graph.add_argument("--asset", default="", help="Who produces or consumes one asset")
    graph.add_argument("--predicate", default="", help="Edges carrying one predicate")
    graph.add_argument(
        "--resolve",
        default="",
        help="Canonical identities ending in a partial name, so a bare symbol from a "
             "stack trace can be turned into the identity the graph stores",
    )
    graph.add_argument(
        "--chase",
        default="",
        help="Walk the declared code graph outward from one or more comma-separated "
             "entities; --predicate then narrows which edges are followed",
    )
    graph.add_argument("--max-hops", type=int, default=2, help="Hops for --chase")
    graph.add_argument(
        "--inventory",
        default="",
        choices=("", "relations", "contracts", "artifacts", "drift", "include_edges", "tx_atoms", "tx_chunks", "tx_chunk_atoms"),
        help="One whole table across the corpus, grouped and bounded, instead of "
             "walking every document to build the same list",
    )
    graph.add_argument("--field", default="", help="Column to narrow --inventory by")
    graph.add_argument("--value", default="", help="Value for --field")
    graph.add_argument("--vocabulary", action="store_true", help="Values outside their declared set")
    graph.add_argument("--integrity", action="store_true", help="Rows whose evidence anchor does not resolve")
    graph.add_argument("--census", action="store_true", help="Deterministic graph-layer census")
    graph.add_argument("--limit", type=int, default=DEFAULT_GRAPH_ROWS)

    goal_sync = sub.add_parser("goal-sync", help="Validate and synchronize goal graph files")
    goal_sync.add_argument("--workspace", required=True)

    coverage = sub.add_parser("coverage", help="Show criterion-to-evidence coverage")
    coverage.add_argument("--workspace", required=True)
    coverage.add_argument("--no-refresh", action="store_true", help="Inspect a potentially stale projection")

    health = sub.add_parser("health", help="Audit source/database parity, integrity, bounds, backup, and search latency")
    health.add_argument("--workspace", required=True)
    health.add_argument("--full", action="store_true", help="Use full SQLite integrity_check instead of quick_check")
    health.add_argument("--no-refresh", action="store_true", help="Audit a potentially stale projection")

    backup = sub.add_parser("backup", help="Create and verify a consistent database and source package")
    backup.add_argument("--workspace", required=True)
    backup.add_argument("--keep", type=int, default=3)

    backup_verify = sub.add_parser("backup-verify", help="Reopen and verify a KAIROS backup package")
    backup_verify.add_argument("--workspace", required=True)
    backup_verify.add_argument("--id", required=True)

    restore = sub.add_parser("restore-drill", help="Restore a backup into an isolated temporary target and verify it")
    restore.add_argument("--workspace", required=True)
    restore.add_argument("--id", required=True)

    finalize = sub.add_parser("finalize", help="Fail-closed finalization with archive promotion")
    finalize.add_argument("--workspace", required=True)

    new_loop = sub.add_parser("new-loop", help="Open the next numbered loop from a verified predecessor seal")
    new_loop.add_argument("--workspace", required=True)
    new_loop.add_argument("--goal", required=True)
    new_loop.add_argument("--milestone", required=True)
    new_loop.add_argument("--task", required=True)

    kickoff = sub.add_parser(
        "project-kickoff",
        help="Atomically create a successor goal and first task from a verified FINALIZED loop",
    )
    kickoff.add_argument("--workspace", required=True)
    kickoff.add_argument(
        "--spec-json",
        required=True,
        help="Inline kairos-project-kickoff/v1 JSON contract",
    )

    export_golden = sub.add_parser(
        "export-golden",
        help="Build a sanitized source-only golden template in an empty target",
    )
    export_golden.add_argument("--workspace", required=True)
    export_golden.add_argument("--source-root", required=True)
    export_golden.add_argument("--target", required=True)
    export_golden.add_argument("--source-commit", required=True)
    export_golden.add_argument("--format", choices=("starter", "framework"), default="starter",
                               help="Select sealed starter or full project-neutral framework sources")

    starter_check = sub.add_parser(
        "starter-check",
        help="Verify the immutable source-only golden starter before project kickoff",
    )
    starter_check.add_argument("--workspace", required=True)

    goal_prompt = sub.add_parser(
        "goal-prompt",
        help="Render a safe portable /goal prompt from one plain project idea",
    )
    goal_prompt.add_argument("--workspace", required=True)
    goal_prompt.add_argument("--idea", required=True)

    new_task = sub.add_parser("new-task", help="Create a task contract and promote it in a heartbeat")
    new_task.add_argument("--workspace", required=True)
    new_task.add_argument("--id", required=True)
    new_task.add_argument("--title", required=True)
    new_task.add_argument("--objective", required=True)
    new_task.add_argument("--goal", required=True)
    new_task.add_argument("--milestone", required=True)
    new_task.add_argument(
        "--criteria",
        default="",
        help="Comma-separated milestone criterion subset; defaults to every milestone criterion",
    )
    new_task.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="work")

    new_report = sub.add_parser("new-report", help="Create a report and promote it in a heartbeat")
    new_report.add_argument("--workspace", required=True)
    new_report.add_argument("--id", required=True)
    new_report.add_argument("--task", required=True)
    new_report.add_argument("--title", required=True)
    new_report.add_argument("--outcome", required=True)
    new_report.add_argument("--work", default="")
    new_report.add_argument("--evidence", default="")
    new_report.add_argument("--limitations", default="")
    new_report.add_argument("--next-action", default="")
    new_report.add_argument("--goal", required=True)
    new_report.add_argument("--milestone", required=True)
    new_report.add_argument("--criteria", default="")
    new_report.add_argument("--state", choices=("partial", "success", "failed", "blocked"), default="partial")
    new_report.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="verify")

    new_bug = sub.add_parser("new-bug", help="Create a causal bug record and promote it in a heartbeat")
    new_bug.add_argument("--workspace", required=True)
    new_bug.add_argument("--id", required=True)
    new_bug.add_argument("--task", required=True)
    new_bug.add_argument("--goal", required=True)
    new_bug.add_argument("--milestone", required=True)
    new_bug.add_argument("--title", required=True)
    new_bug.add_argument("--observation", required=True)
    new_bug.add_argument("--impact", required=True)
    new_bug.add_argument("--root-cause", required=True)
    new_bug.add_argument("--resolution", required=True)
    new_bug.add_argument("--regression", required=True)
    new_bug.add_argument("--criteria", default="")
    new_bug.add_argument("--state", choices=("active", "resolved", "blocked"), default="active")
    new_bug.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="work")

    new_code = sub.add_parser("new-code", help="Create a code navigation document and promote it in a heartbeat")
    new_code.add_argument("--workspace", required=True)
    new_code.add_argument("--id", required=True)
    new_code.add_argument("--task", required=True)
    new_code.add_argument("--goal", required=True)
    new_code.add_argument("--milestone", required=True)
    new_code.add_argument("--title", required=True)
    new_code.add_argument("--purpose", required=True)
    new_code.add_argument("--components", required=True)
    new_code.add_argument("--interfaces", required=True)
    new_code.add_argument("--invariants", required=True)
    new_code.add_argument("--dependencies", required=True)
    new_code.add_argument("--testing", required=True)
    new_code.add_argument("--criteria", default="")
    new_code.add_argument("--state", choices=("ready", "active", "completed", "superseded"), default="ready")
    new_code.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="work")

    new_decision = sub.add_parser("new-decision", help="Create a scoped tradeoff decision and promote it in a heartbeat")
    new_decision.add_argument("--workspace", required=True)
    new_decision.add_argument("--id", required=True)
    new_decision.add_argument("--task", required=True)
    new_decision.add_argument("--goal", required=True)
    new_decision.add_argument("--milestone", required=True)
    new_decision.add_argument("--title", required=True)
    new_decision.add_argument("--question", required=True)
    new_decision.add_argument("--context", required=True)
    new_decision.add_argument("--options", required=True)
    new_decision.add_argument("--selected", required=True)
    new_decision.add_argument("--rationale", required=True)
    new_decision.add_argument("--risks", required=True)
    new_decision.add_argument("--validation", required=True)
    new_decision.add_argument("--criteria", default="")
    new_decision.add_argument("--state", choices=("active", "completed", "superseded"), default="completed")
    new_decision.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="work")

    new_research = sub.add_parser("new-research", help="Capture workspace, search, dataset, user, or internet evidence and promote it in a heartbeat")
    new_research.add_argument("--workspace", required=True)
    new_research.add_argument("--id", required=True)
    new_research.add_argument("--task", required=True)
    new_research.add_argument("--title", required=True)
    new_research.add_argument("--question", required=True)
    new_research.add_argument("--source", required=True)
    new_research.add_argument("--source-kind", choices=("internet", "workspace", "search", "dataset", "user"), required=True)
    new_research.add_argument("--source-sha256", default="")
    new_research.add_argument("--fact", required=True)
    new_research.add_argument("--interpretation", required=True)
    new_research.add_argument("--contradictions", default="No contradiction has been established; competing evidence remains open until explicitly checked.")
    new_research.add_argument("--next-query", default="Reopen the source, inspect at least one competing branch, and convert validated evidence into a success report.")
    new_research.add_argument("--goal", required=True)
    new_research.add_argument("--milestone", required=True)
    new_research.add_argument("--criteria", default="")
    new_research.add_argument("--state", choices=("partial", "success", "blocked"), default="partial")
    new_research.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="breadth")

    close_task = sub.add_parser("close-task", help="Close an evidenced task and promote its closure in the same heartbeat")
    close_task.add_argument("--workspace", required=True)
    close_task.add_argument("--id", required=True)
    close_task.add_argument(
        "--next-task",
        default="",
        help="Explicitly activate an already-promoted open successor after verified closure",
    )
    close_task.add_argument("--mode", choices=("work", "depth", "breadth", "breathe", "verify"), default="verify")
    return parser


GOVERNED_COMMANDS = set(COMMAND_PHASES)
MUTATING_COMMANDS = set(GOVERNED_COMMANDS)
assert_command_classification(GOVERNED_COMMANDS)
SOURCE_ONLY_STARTER_COMMANDS = {"starter-check", "goal-prompt"}


def _write_new_artifact(workspace: Path, path: Path, text: str) -> None:
    prepare_rendered_document(path, workspace, text)
    atomic_write_text(path, text)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    command = args.command
    # The immutable framework corpus is intentionally searchable before a project workspace
    # exists. This is the bootstrap edge: an agent can ask KAIROS how KAIROS must be used
    # without inventing a temporary task, database, receipt or governance state.
    if command == "search" and not args.workspace:
        if args.trace:
            raise ValueError("framework bootstrap search is immutable and does not record retrieval traces")
        result = search_framework(
            args.query,
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            mode=args.mode,
            max_hops=args.max_hops,
        )
        return result, 0 if result["result_count"] else 1
    workspace = _workspace(args.workspace)
    # These are deliberately narrow pre-governance commands. A fresh golden starter
    # has no database or active task yet, so it cannot issue a normal action permit.
    # Both commands are read-only and verify the sealed source set before returning.
    if command in SOURCE_ONLY_STARTER_COMMANDS:
        return _execute_unlocked(args, workspace)
    if command in GOVERNED_COMMANDS:
        with workspace_write_lock(workspace):
            if (
                command == "project-kickoff"
                and not database_path(workspace).is_file()
                and (workspace / ".kairos" / "golden_seal.json").is_file()
            ):
                setattr(args, "_golden_materialization", materialize_golden_seed(workspace))
            if command == "rebuild-derived" and not database_path(workspace).is_file():
                return _execute_unlocked(args, workspace)
            if (
                command == "source-permit"
                and args.purpose == "metadata_repair"
                and not database_path(workspace).is_file()
            ):
                return _execute_unlocked(args, workspace)
            if (
                command == "source-search"
                and args.permit.startswith("EMR_")
                and not database_path(workspace).is_file()
            ):
                return _execute_unlocked(args, workspace)
            action = begin_cli_action(
                workspace,
                command_name=command,
                arguments=vars(args),
            )
            setattr(args, "_governance_action_id", action["action_id"])
            try:
                payload, exit_code = _execute_unlocked(args, workspace)
            except Exception as exc:
                finish_cli_action(
                    workspace,
                    action,
                    success=False,
                    result={"error": type(exc).__name__, "message": str(exc)},
                )
                raise
            action_receipt = finish_cli_action(
                workspace,
                action,
                success=exit_code == 0,
                result=payload,
            )
            if isinstance(payload, dict):
                payload["governance_action"] = action_receipt
            else:
                payload = {"result": payload, "governance_action": action_receipt}
            return payload, exit_code
    return _execute_unlocked(args, workspace)


def _access_freshness(workspace: Path, *, reason: str, disabled: bool,
                      verify_content_hashes: bool = False) -> dict[str, Any]:
    if disabled:
        return {"checked": False, "refreshed": False, "reason": "explicit --no-refresh override"}
    return ensure_workspace_fresh(workspace, reason=reason,
                                  verify_content_hashes=verify_content_hashes)


def _execute_unlocked(args: argparse.Namespace, workspace: Path) -> tuple[Any, int]:
    command = args.command
    if command == "init":
        return initialize_workspace(workspace, workspace_id=args.workspace_id), 0
    if command == "starter-check":
        return verify_golden_template(workspace), 0
    if command == "goal-prompt":
        return render_goal_prompt(workspace, args.idea), 0
    if command == "rebuild-derived":
        result = rebuild_derived_state(
            workspace,
            history_backup_id=args.history_backup,
            history_references=args.history_ref,
        )
        return result, 0 if result["verified"] else 2
    if command == "governance-status":
        return governance_status(
            workspace,
            exclude_action_id=getattr(args, "_governance_action_id", None),
        ), 0
    if command == "action-permit":
        return issue_external_edit_permit(
            workspace,
            paths=args.path,
            reason=args.reason,
            ttl_seconds=args.ttl_seconds,
        ), 0
    if command == "revoke-permit":
        return revoke_action_permit(
            workspace,
            permit_id=args.permit,
            reason=args.reason,
        ), 0
    if command == "recover-action":
        return recover_orphaned_action(
            workspace,
            target_action_id=args.action,
            reason=args.reason,
            recovery_action_id=args._governance_action_id,
        ), 0
    if command == "recover-revisions":
        return recover_artifact_revisions(
            workspace,
            backup_id=args.backup_id,
            revision_identities=args.ref,
        ), 0
    if command == "attribute-change":
        paths = args.path if isinstance(args.path, list) else [args.path]
        attribution = attribute_quarantined_changes(
            workspace,
            paths=paths,
            reason=args.reason,
            action_id=args._governance_action_id,
        )
        heartbeat = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=paths,
            use_reconciliation=True,
            trigger="explicit:attribute-change",
        )
        return {
            "attribution": attribution,
            "heartbeat": heartbeat,
        }, 0 if heartbeat["verified"] else 2
    if command == "governance-enable":
        return enable_governance(workspace), 0
    if command == "status":
        freshness = _access_freshness(
            workspace,
            reason="status access",
            disabled=args.no_refresh,
        )
        result = status_report(workspace)
        result["freshness"] = freshness
        tx = result.get("tx_recycling_live") or {}
        # A cached file must not be mistaken for an up-to-date admission.
        # Do not fail normal status in projects that never enabled retention.
        status_code = 2 if tx.get("reconciliation_required") is True else 0
        return result, status_code
    if command == "validate":
        paths = _all_documents(workspace) if args.all else [resolve_workspace_path(workspace, value) for value in args.paths]
        if not paths:
            raise ValueError("provide paths or use --all")
        results = []
        for path in paths:
            prepared = prepare_document(path, workspace)
            results.append(
                {
                    "path": prepared.relative_path,
                    "artifact_id": prepared.frontmatter.metadata["id"],
                    "header_bytes": prepared.frontmatter.header_bytes,
                    "sections": len(prepared.sections),
                    "references": len(prepared.references),
                    "valid": True,
                }
            )
        return {"valid": True, "documents": results}, 0
    if command == "promote":
        database = _database(workspace)
        paths = [resolve_workspace_path(workspace, value) for value in args.paths]
        return {"receipts": promote_many(paths, workspace, database)}, 0
    if command == "reconcile":
        result = reconcile_documents(workspace, verify_content_hashes=args.verify_content_hashes)
        return {
            "changed": [str(path.relative_to(workspace)).replace("\\", "/") for path in result.changed],
            "missing": list(result.missing),
            "changed_count": len(result.changed),
            "missing_count": len(result.missing),
        }, 0
    if command == "heartbeat":
        metrics = json.loads(args.metrics_json) if args.metrics_json else None
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=args.changed,
            use_reconciliation=not args.no_reconcile,
            metrics=metrics,
        )
        return result, 0 if result["verified"] else 2
    if command == "search":
        freshness = _access_freshness(
            workspace,
            reason="search access",
            disabled=args.no_refresh,
            verify_content_hashes=args.verify_content_hashes,
        )
        result = search_database(
            _database(workspace),
            args.query,
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            mode=args.mode,
            max_hops=args.max_hops,
            record_trace=args.trace or (args.mode == "breathe" and not args.no_trace),
        )
        result["freshness"] = freshness
        result["routing_receipt"] = record_routing_receipt(
            workspace,
            _database(workspace),
            result,
            freshness,
        )
        return result, 0 if result["result_count"] else 1
    if command == "source-permit":
        source_database_path = database_path(workspace)
        if args.emergency and args.purpose != "metadata_repair":
            raise ValueError("--emergency is valid only with --purpose metadata_repair")
        if args.purpose == "metadata_repair" and (args.emergency or not source_database_path.is_file()):
            observed_error = args.metadata_error or (
                f"metadata database is missing: {source_database_path}"
            )
            result = issue_emergency_metadata_repair_permit(
                workspace,
                paths=args.path,
                patterns=args.pattern,
                reason=args.reason,
                metadata_error=observed_error,
                ttl_seconds=args.ttl_seconds,
            )
            return result, 0
        try:
            permit_database = _database(workspace)
        except Exception as exc:
            if args.purpose != "metadata_repair":
                raise
            result = issue_emergency_metadata_repair_permit(
                workspace,
                paths=args.path,
                patterns=args.pattern,
                reason=args.reason,
                metadata_error=args.metadata_error or f"{type(exc).__name__}: {exc}",
                ttl_seconds=args.ttl_seconds,
            )
            return result, 0
        result = issue_source_permit(
            workspace,
            permit_database,
            routing_receipt_id=args.routing_receipt or None,
            purpose=args.purpose,
            paths=args.path,
            patterns=args.pattern,
            reason=args.reason,
            ttl_seconds=args.ttl_seconds,
        )
        return result, 0
    if command == "source-search":
        if args.permit.startswith("EMR_"):
            result = execute_emergency_source_search(
                workspace,
                permit_id=args.permit,
            )
            return result, 0 if result["status"] == "VERIFIED" else 2
        result = execute_source_search(
            workspace,
            _database(workspace),
            permit_id=args.permit,
        )
        return result, 0 if result["status"] == "VERIFIED" else 2
    if command == "graph":
        return (
            run_graph_query(
                _database(workspace),
                artifact=args.artifact,
                node=args.node,
                asset=args.asset,
                predicate=args.predicate,
                resolve=args.resolve,
                chase=args.chase,
                inventory_table=args.inventory,
                field=args.field,
                value=args.value,
                max_hops=args.max_hops,
                vocabulary=args.vocabulary,
                integrity=args.integrity,
                show_census=args.census,
                limit=args.limit,
            ),
            0,
        )
    if command == "goal-sync":
        result = run_heartbeat(
            workspace,
            requested_mode="verify",
            use_reconciliation=True,
            trigger="explicit:goal-sync",
        )
        return {"heartbeat": result}, 0 if result["verified"] else 2
    if command == "coverage":
        freshness = _access_freshness(
            workspace,
            reason="coverage access",
            disabled=args.no_refresh,
        )
        return {"coverage": coverage_report(_database(workspace)), "freshness": freshness}, 0
    if command == "health":
        freshness = _access_freshness(
            workspace,
            reason="health audit",
            disabled=args.no_refresh,
        )
        result = health_audit(
            workspace,
            full=args.full,
            governance_exclude_action_id=getattr(args, "_governance_action_id", None),
        )
        result["freshness"] = freshness
        return result, 0 if result["verdict"] == "PASS" else 2
    if command == "backup":
        freshness = ensure_workspace_fresh(workspace, reason="backup creation")
        result = create_backup(workspace, keep=args.keep)
        result["freshness"] = freshness
        return result, 0
    if command == "backup-verify":
        result = verify_backup(workspace, args.id)
        return result, 0 if result["verified"] else 2
    if command == "restore-drill":
        result = restore_drill(workspace, args.id)
        return result, 0 if result["verified"] else 2
    if command == "finalize":
        result = finalize_workspace(workspace)
        return result, 0 if result["status"] == "FINALIZED" else 2
    if command == "new-loop":
        result = start_new_loop(
            workspace,
            goal_id=args.goal,
            milestone_id=args.milestone,
            task_id=args.task,
        )
        return result, 0 if result["status"] == "VERIFIED" else 2
    if command == "project-kickoff":
        try:
            spec = json.loads(args.spec_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid project kickoff JSON: {exc.msg}") from exc
        result = kickoff_project(workspace, spec=spec)
        materialization = getattr(args, "_golden_materialization", None)
        if materialization:
            result["golden_materialization"] = materialization
        return result, 0 if result["status"] == "VERIFIED" else 2
    if command == "export-golden":
        builder = (build_framework_distribution if args.format == "framework"
                   else build_golden_template)
        result = builder(
            source_root=Path(args.source_root),
            target_root=Path(args.target),
            source_commit=args.source_commit,
        )
        return result, 0 if result["verified"] else 2
    if command == "new-task":
        _require_id(args.id, "TASK")
        _require_id(args.goal, "GOAL")
        _require_id(args.milestone, "MILESTONE")
        config = load_config(workspace)
        goal, milestone = _goal_milestone(workspace, args.goal, args.milestone)
        if goal["state"] not in {"new", "active"} or milestone["state"] not in {
            "new",
            "active",
        }:
            raise ValueError(
                f"new tasks require an open goal and milestone: {args.goal}/{args.milestone}"
            )
        requested_criteria = [
            value.strip() for value in args.criteria.split(",") if value.strip()
        ]
        milestone_criteria = {
            criterion["id"]: criterion for criterion in milestone["criteria"]
        }
        unknown = sorted(set(requested_criteria) - set(milestone_criteria))
        if unknown:
            raise ValueError(
                f"task criteria are not part of {args.milestone}: {', '.join(unknown)}"
            )
        selected_criteria = (
            [milestone_criteria[identifier] for identifier in requested_criteria]
            if requested_criteria
            else list(milestone["criteria"])
        )
        loop = _current_loop(workspace)
        path = workspace / "tasks" / f"task_{args.id}.md"
        if path.exists():
            raise ValueError(f"task already exists: {path}")
        _write_new_artifact(
            workspace, path,
            task_document(
                task_id=args.id,
                title=args.title,
                objective=args.objective,
                workspace_id=config["workspace_id"],
                goal_id=args.goal,
                milestone_id=args.milestone,
                criteria=selected_criteria,
                loop=loop,
            ),
        )
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=[str(path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
            task_activation=args.id,
        )
        return {"created": str(path), "heartbeat": result}, 0 if result["verified"] else 2
    if command == "new-report":
        _require_id(args.id, "REPORT")
        _require_id(args.task, "TASK")
        _require_id(args.goal, "GOAL")
        _require_id(args.milestone, "MILESTONE")
        config = load_config(workspace)
        report_stem = args.id.removeprefix("REPORT_")
        path = workspace / "reports" / f"report_{report_stem}.md"
        if path.exists():
            raise ValueError(f"report already exists: {path}")
        criteria = [value.strip() for value in args.criteria.split(",") if value.strip()]
        loop = _current_loop(workspace)
        _validate_new_artifact_scope(
            workspace,
            task_id=args.task,
            goal_id=args.goal,
            milestone_id=args.milestone,
            criteria=criteria,
        )
        _write_new_artifact(
            workspace, path,
            report_document(
                report_id=args.id,
                task_id=args.task,
                title=args.title,
                workspace_id=config["workspace_id"],
                goal_id=args.goal,
                milestone_id=args.milestone,
                criteria=criteria,
                task_path=f"tasks/task_{args.task}.md",
                loop=loop,
                state=args.state,
                outcome=args.outcome,
                **({"work_performed": args.work} if args.work else {}),
                evidence=args.evidence or None,
                **({"limitations": args.limitations} if args.limitations else {}),
                **({"next_action": args.next_action} if args.next_action else {}),
            ),
        )
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=[str(path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
        )
        return {"created": str(path), "heartbeat": result}, 0 if result["verified"] else 2
    if command in {"new-bug", "new-code", "new-decision"}:
        prefix = {"new-bug": "BUG", "new-code": "CODE", "new-decision": "DECISION"}[command]
        _require_id(args.id, prefix)
        _require_id(args.task, "TASK")
        _require_id(args.goal, "GOAL")
        _require_id(args.milestone, "MILESTONE")
        config = load_config(workspace)
        criteria = [value.strip() for value in args.criteria.split(",") if value.strip()]
        loop = _current_loop(workspace)
        _validate_new_artifact_scope(
            workspace,
            task_id=args.task,
            goal_id=args.goal,
            milestone_id=args.milestone,
            criteria=criteria,
        )
        if command == "new-bug":
            path = workspace / "bugs" / f"{args.id}.md"
            content = bug_document(
                bug_id=args.id,
                task_id=args.task,
                workspace_id=config["workspace_id"],
                task_path=f"tasks/task_{args.task}.md",
                goal_id=args.goal,
                milestone_id=args.milestone,
                title=args.title,
                observation=args.observation,
                impact=args.impact,
                root_cause=args.root_cause,
                resolution=args.resolution,
                regression=args.regression,
                criteria=criteria,
                loop=loop,
                state=args.state,
            )
        elif command == "new-code":
            path = workspace / "code" / f"{args.id}.md"
            content = code_document(
                code_id=args.id,
                task_id=args.task,
                workspace_id=config["workspace_id"],
                task_path=f"tasks/task_{args.task}.md",
                goal_id=args.goal,
                milestone_id=args.milestone,
                title=args.title,
                purpose=args.purpose,
                components=args.components,
                interfaces=args.interfaces,
                invariants=args.invariants,
                dependencies=args.dependencies,
                testing=args.testing,
                criteria=criteria,
                loop=loop,
                state=args.state,
            )
        else:
            path = workspace / "decisions" / f"{args.id}.md"
            content = decision_document(
                decision_id=args.id,
                task_id=args.task,
                workspace_id=config["workspace_id"],
                task_path=f"tasks/task_{args.task}.md",
                goal_id=args.goal,
                milestone_id=args.milestone,
                title=args.title,
                question=args.question,
                context=args.context,
                options=args.options,
                selected=args.selected,
                rationale=args.rationale,
                risks=args.risks,
                validation=args.validation,
                criteria=criteria,
                loop=loop,
                state=args.state,
            )
        if path.exists():
            raise ValueError(f"artifact already exists: {path}")
        _write_new_artifact(workspace, path, content)
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=[str(path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
        )
        return {"created": str(path), "heartbeat": result}, 0 if result["verified"] else 2
    if command == "new-research":
        _require_id(args.id, "RESEARCH")
        _require_id(args.task, "TASK")
        _require_id(args.goal, "GOAL")
        _require_id(args.milestone, "MILESTONE")
        config = load_config(workspace)
        path = workspace / "research" / f"{args.id}.md"
        if path.exists():
            raise ValueError(f"research artifact already exists: {path}")
        criteria = [value.strip() for value in args.criteria.split(",") if value.strip()]
        loop = _current_loop(workspace)
        _validate_new_artifact_scope(
            workspace,
            task_id=args.task,
            goal_id=args.goal,
            milestone_id=args.milestone,
            criteria=criteria,
        )
        _write_new_artifact(
            workspace, path,
            research_document(
                research_id=args.id,
                task_id=args.task,
                title=args.title,
                question=args.question,
                source_uri=args.source,
                source_kind=args.source_kind,
                source_sha256=args.source_sha256,
                source_fact=args.fact,
                interpretation=args.interpretation,
                contradictions=args.contradictions,
                next_query=args.next_query,
                workspace_id=config["workspace_id"],
                task_path=f"tasks/task_{args.task}.md",
                goal_id=args.goal,
                milestone_id=args.milestone,
                criteria=criteria,
                loop=loop,
                state=args.state,
            ),
        )
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=[str(path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
        )
        return {"created": str(path), "heartbeat": result}, 0 if result["verified"] else 2
    if command == "close-task":
        task_id = _require_id(args.id, "TASK")
        path = workspace / "tasks" / f"task_{task_id}.md"
        if not path.exists():
            raise ValueError(f"task does not exist: {path}")
        database = _database(workspace)
        pending, failed = database.pending_counts()
        if pending or failed:
            raise ValueError(f"task closure requires zero pending and failed promotions; pending={pending}, failed={failed}")
        parsed = split_frontmatter(path.read_text(encoding="utf-8"))
        metadata = dict(parsed.metadata)
        if metadata.get("id") != task_id or metadata.get("type") != "task":
            raise ValueError(f"task identity mismatch in {path}")
        next_task_metadata: dict[str, Any] | None = None
        if args.next_task:
            next_task_id = _require_id(args.next_task, "TASK")
            if next_task_id == task_id:
                raise ValueError("next task must differ from the task being closed")
            connection = database.connect(read_only=True)
            try:
                next_row = connection.execute(
                    """
                    SELECT a.metadata_json,a.state,g.state AS goal_state,m.state AS milestone_state
                    FROM artifacts AS a
                    JOIN goals AS g ON g.goal_id=a.goal_id
                    JOIN milestones AS m ON m.milestone_id=a.milestone_id AND m.goal_id=a.goal_id
                    WHERE a.artifact_id=? AND a.document_type='task'
                    """,
                    (next_task_id,),
                ).fetchone()
            finally:
                connection.close()
            if (
                not next_row
                or next_row["state"] in {"completed", "success", "superseded", "finalized"}
                or next_row["goal_state"] not in {"new", "active", "blocked"}
                or next_row["milestone_state"] not in {"new", "active", "blocked"}
            ):
                raise ValueError(f"next task is not a promoted open task in an open scope: {next_task_id}")
            next_task_metadata = json.loads(next_row["metadata_json"])
        criteria = list(metadata.get("criteria", []))
        coverage_by_id = {
            row["criterion_id"]: row for row in coverage_report(database)
        }
        connection = database.connect(read_only=True)
        try:
            uncovered = [
                criterion_id
                for criterion_id in criteria
                if not coverage_by_id.get(criterion_id, {}).get("requirements_met")
            ]
            report = connection.execute(
                """
                SELECT artifact_id,path,revision
                FROM artifacts
                WHERE document_type='report' AND task_id=? AND state='success'
                ORDER BY promoted_at DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        if uncovered:
            details = [
                {
                    "criterion": criterion_id,
                    "missing_types": coverage_by_id.get(criterion_id, {}).get(
                        "missing_artifact_types", ["<unknown criterion>"]
                    ),
                    "evidenced": bool(
                        coverage_by_id.get(criterion_id, {}).get("evidenced")
                    ),
                }
                for criterion_id in uncovered
            ]
            raise ValueError(
                "task closure requires success evidence and every declared artifact type: "
                + json_dumps(details, pretty=False)
            )
        if not report:
            raise ValueError("task closure requires a promoted success report")
        resumed_closure = metadata.get("state") == "completed"
        if not resumed_closure:
            metadata["revision"] = int(metadata["revision"]) + 1
            metadata["state"] = "completed"
            metadata["updated_at"] = utc_now()
            refs = dict(metadata.get("refs", {}))
            refs["closure_evidence"] = pointer(
                report["path"],
                section="s-evidence",
                artifact_id=report["artifact_id"],
                version=str(report["revision"]),
                relation="validated_by",
                tags=("closure", "evidence", "report"),
                source="close-task",
            )
            metadata["refs"] = refs
            body = re.sub(
                r"- \[ \] (\*\*CRIT_[^\n]+)",
                r"- [x] \1",
                parsed.body,
            )
            body = re.sub(
                r'(<a id="s-next"></a>\n## NEXT ACTION\n\n).*\Z',
                r'\1> Capsule: Task closure is verified; select the next goal-linked task before further material work.\n\nTask closed through the evidenced `close-task` gate. Open `ACTIVE.md#s-active-frontier` for the next route.\n',
                body,
                flags=re.DOTALL,
            )
            atomic_write_text(path, render_frontmatter(metadata) + body)
        _complete_goal_contract_for_task(
            workspace,
            goal_id=metadata["goal"],
            milestone_id=metadata["milestone"],
            criteria=criteria,
        )
        state_path = workspace / ".kairos" / "runtime_state.json"
        state = read_json(state_path)
        if state.get("active_task") == task_id or next_task_metadata:
            if next_task_metadata:
                next_criteria = list(next_task_metadata.get("criteria", []))
                state["active_goal"] = next_task_metadata["goal"]
                state["active_milestone"] = next_task_metadata["milestone"]
                state["active_task"] = next_task_metadata["id"]
                state["active_criterion"] = next_criteria[0] if next_criteria else None
                state["evidence_gap_count"] = len(next_criteria)
            else:
                state["active_task"] = None
                state["active_criterion"] = None
            state["updated_at"] = utc_now()
            atomic_write_json(state_path, state)
        result = run_heartbeat(
            workspace,
            requested_mode=args.mode,
            changed_paths=[str(path.relative_to(workspace)).replace("\\", "/")],
            use_reconciliation=True,
        )
        return {
            "closed": task_id,
            "activated": next_task_metadata["id"] if next_task_metadata else None,
            "resumed_closure": resumed_closure,
            "path": str(path),
            "heartbeat": result,
        }, 0 if result["verified"] else 2
    raise ValueError(f"unhandled command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload, code = execute(args)
        if args.command == "goal-prompt" and code == 0 and isinstance(payload, dict):
            print(payload["prompt"], end="")
        else:
            _print(payload)
        return code
    except Exception as exc:
        _print({"error": type(exc).__name__, "message": str(exc), "command": args.command})
        return 2


if __name__ == "__main__":
    sys.exit(main())
