from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    MAX_GOVERNANCE_VIOLATIONS,
    MAX_GOVERNED_ACTION_RECEIPTS,
    MAX_QUARANTINE_HISTORY,
    MAX_TERMINAL_ACTION_PERMITS,
)
from .database import KnowledgeDatabase
from .frontmatter import split_frontmatter
from .util import atomic_write_json, json_dumps, read_json, resolve_workspace_path, sha256_bytes, utc_now, workspace_relative
from .workspace import database_path, load_config


class GovernanceError(RuntimeError):
    pass


PHASES = {
    "COMMAND_INTAKE",
    "TASK_FORMULATION",
    "METADATA_ROUTING",
    "SOURCE_INSPECTION",
    "MUTATION",
    "VERIFICATION",
    "CLOSURE",
    "ROLLOVER",
    "RECOVERY",
}

COMMAND_PHASES = {
    "rebuild-derived": "RECOVERY",
    "status": "VERIFICATION",
    "validate": "VERIFICATION",
    "promote": "MUTATION",
    "reconcile": "VERIFICATION",
    "heartbeat": "VERIFICATION",
    "search": "METADATA_ROUTING",
    "graph": "METADATA_ROUTING",
    "source-permit": "SOURCE_INSPECTION",
    "source-search": "SOURCE_INSPECTION",
    "goal-sync": "TASK_FORMULATION",
    "coverage": "VERIFICATION",
    "health": "VERIFICATION",
    "backup": "VERIFICATION",
    "backup-verify": "VERIFICATION",
    "restore-drill": "RECOVERY",
    "finalize": "CLOSURE",
    "new-loop": "ROLLOVER",
    "project-kickoff": "ROLLOVER",
    "export-golden": "MUTATION",
    "starter-check": "VERIFICATION",
    "goal-prompt": "COMMAND_INTAKE",
    "new-task": "TASK_FORMULATION",
    "new-report": "MUTATION",
    "new-bug": "MUTATION",
    "new-code": "MUTATION",
    "new-decision": "MUTATION",
    "new-research": "MUTATION",
    "close-task": "CLOSURE",
    "action-permit": "COMMAND_INTAKE",
    "revoke-permit": "COMMAND_INTAKE",
    "recover-action": "RECOVERY",
    "recover-revisions": "RECOVERY",
    "attribute-change": "MUTATION",
    "governance-enable": "VERIFICATION",
    "governance-status": "VERIFICATION",
}

ALLOWED_TRANSITIONS = {
    "COMMAND_INTAKE": PHASES,
    "TASK_FORMULATION": {
        "TASK_FORMULATION", "METADATA_ROUTING", "SOURCE_INSPECTION", "MUTATION",
        "VERIFICATION", "CLOSURE", "RECOVERY",
    },
    "METADATA_ROUTING": {
        "METADATA_ROUTING", "SOURCE_INSPECTION", "TASK_FORMULATION", "MUTATION",
        "VERIFICATION", "CLOSURE", "RECOVERY",
    },
    "SOURCE_INSPECTION": {
        "SOURCE_INSPECTION", "METADATA_ROUTING", "TASK_FORMULATION", "MUTATION",
        "VERIFICATION", "RECOVERY",
    },
    "MUTATION": {
        "MUTATION", "METADATA_ROUTING", "SOURCE_INSPECTION", "VERIFICATION",
        "CLOSURE", "RECOVERY",
    },
    "VERIFICATION": {
        "VERIFICATION", "METADATA_ROUTING", "SOURCE_INSPECTION", "TASK_FORMULATION",
        "MUTATION", "CLOSURE", "ROLLOVER", "RECOVERY",
    },
    "CLOSURE": {
        "CLOSURE", "ROLLOVER", "TASK_FORMULATION", "METADATA_ROUTING",
        "VERIFICATION", "RECOVERY",
    },
    "ROLLOVER": {
        "ROLLOVER", "TASK_FORMULATION", "METADATA_ROUTING", "VERIFICATION", "RECOVERY",
    },
    "RECOVERY": {
        "RECOVERY", "VERIFICATION", "METADATA_ROUTING", "TASK_FORMULATION", "ROLLOVER",
    },
}
for _allowed_targets in ALLOWED_TRANSITIONS.values():
    _allowed_targets.add("COMMAND_INTAKE")

SCOPE_EXEMPT_COMMANDS = {
    "governance-status",
    "rebuild-derived",
    "backup-verify",
    "restore-drill",
    "recover-revisions",
}
POST_CLOSURE_COMMANDS = {"status", "coverage", "health", "backup", "finalize"}
DERIVED_DOCUMENTS = {
    "ACTIVE.md", "CLOSED.md", "NEURAL_CORTEX.md", "_LOOP_GATE.md", "_SESSION.md"
}


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _database(workspace: Path) -> KnowledgeDatabase:
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    return database


def governance_mode(workspace: Path) -> str:
    mode = str(load_config(workspace).get("governance_enforcement", "audit")).casefold()
    if mode not in {"audit", "required"}:
        raise GovernanceError(f"invalid governance_enforcement mode: {mode!r}")
    return mode


def _task_criterion(workspace: Path, arguments: dict[str, Any]) -> str | None:
    requested = [value.strip() for value in str(arguments.get("criteria", "")).split(",") if value.strip()]
    if requested:
        return requested[0]
    goal_id = arguments.get("goal")
    milestone_id = arguments.get("milestone")
    if not goal_id or not milestone_id:
        return None
    goal = read_json(workspace / "goals" / f"{goal_id}.json", default={}) or {}
    milestone = next((item for item in goal.get("milestones", []) if item.get("id") == milestone_id), {})
    criteria = milestone.get("criteria", [])
    return criteria[0].get("id") if criteria else None


def _project_kickoff_scope(arguments: dict[str, Any]) -> dict[str, str | None]:
    raw = arguments.get("spec_json")
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > 65536:
        return {}
    try:
        spec = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(spec, dict):
        return {}
    goal = spec.get("goal")
    task = spec.get("task")
    if not isinstance(goal, dict) or not isinstance(task, dict):
        return {}
    criteria = task.get("criteria")
    criterion_id = (
        str(criteria[0])
        if isinstance(criteria, list) and criteria and isinstance(criteria[0], str)
        else None
    )
    return {
        "goal_id": goal.get("id") if isinstance(goal.get("id"), str) else None,
        "milestone_id": (
            task.get("milestone") if isinstance(task.get("milestone"), str) else None
        ),
        "task_id": task.get("id") if isinstance(task.get("id"), str) else None,
        "criterion_id": criterion_id,
    }


def runtime_scope(
    workspace: Path,
    *,
    command_name: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arguments = arguments or {}
    state = read_json(workspace / ".kairos" / "runtime_state.json", default={}) or {}
    scope = {
        "goal_id": state.get("active_goal"),
        "milestone_id": state.get("active_milestone"),
        "task_id": state.get("active_task"),
        "criterion_id": state.get("active_criterion"),
    }
    if (
        scope["task_id"]
        and not scope["criterion_id"]
    ):
        task_path = workspace / "tasks" / f"task_{scope['task_id']}.md"
        if task_path.is_file() and database_path(workspace).is_file():
            metadata = split_frontmatter(
                task_path.read_text(encoding="utf-8")
            ).metadata
            criteria = list(metadata.get("criteria") or [])
            from .goals import coverage_report

            coverage = {
                row["criterion_id"]: bool(row["requirements_met"])
                for row in coverage_report(KnowledgeDatabase(database_path(workspace)))
            }
            if criteria and all(coverage.get(str(value), False) for value in criteria):
                scope["criterion_id"] = str(criteria[-1])
    if command_name == "close-task" and not scope["task_id"]:
        requested_task = arguments.get("id")
        task_path = workspace / "tasks" / f"task_{requested_task}.md"
        if requested_task and task_path.is_file() and database_path(workspace).is_file():
            metadata = split_frontmatter(task_path.read_text(encoding="utf-8")).metadata
            criteria = [str(value) for value in (metadata.get("criteria") or [])]
            from .goals import coverage_report

            coverage = {
                row["criterion_id"]: bool(row["requirements_met"])
                for row in coverage_report(KnowledgeDatabase(database_path(workspace)))
            }
            if (
                metadata.get("id") == requested_task
                and metadata.get("type") == "task"
                and metadata.get("state") == "completed"
                and criteria
                and all(coverage.get(value, False) for value in criteria)
            ):
                scope.update(
                    goal_id=metadata.get("goal"),
                    milestone_id=metadata.get("milestone"),
                    task_id=requested_task,
                    criterion_id=criteria[-1],
                )
    if command_name in POST_CLOSURE_COMMANDS and not scope["task_id"] and database_path(workspace).is_file():
        database = KnowledgeDatabase(database_path(workspace))
        connection = database.connect(read_only=True)
        try:
            rows = connection.execute(
                """
                SELECT a.artifact_id,a.goal_id,a.milestone_id,a.metadata_json,
                       g.state AS goal_state,m.state AS milestone_state
                FROM artifacts AS a
                JOIN goals AS g ON g.goal_id=a.goal_id
                JOIN milestones AS m
                  ON m.goal_id=a.goal_id AND m.milestone_id=a.milestone_id
                WHERE a.document_type='task'
                  AND a.state IN ('completed','success','superseded','finalized')
                  AND g.state IN ('completed','superseded')
                  AND m.state IN ('completed','superseded')
                ORDER BY a.promoted_at DESC,a.artifact_id DESC
                """
            ).fetchall()
        finally:
            connection.close()
        preferred = next(
            (
                row for row in rows
                if (not scope["goal_id"] or row["goal_id"] == scope["goal_id"])
                and (not scope["milestone_id"] or row["milestone_id"] == scope["milestone_id"])
            ),
            rows[0] if rows else None,
        )
        if preferred:
            metadata = json.loads(preferred["metadata_json"])
            criteria = [str(value) for value in (metadata.get("criteria") or [])]
            from .goals import coverage_report

            coverage = {
                row["criterion_id"]: bool(row["requirements_met"])
                for row in coverage_report(database)
            }
            if criteria and all(coverage.get(value, False) for value in criteria):
                scope.update(
                    goal_id=preferred["goal_id"],
                    milestone_id=preferred["milestone_id"],
                    task_id=preferred["artifact_id"],
                    criterion_id=criteria[-1],
                )
    if command_name in {"new-task", "new-loop"}:
        scope.update(
            goal_id=arguments.get("goal") or scope["goal_id"],
            milestone_id=arguments.get("milestone") or scope["milestone_id"],
            task_id=arguments.get("id") or arguments.get("task") or scope["task_id"],
        )
        scope["criterion_id"] = _task_criterion(workspace, arguments) or scope["criterion_id"]
    if command_name == "project-kickoff":
        scope.update(_project_kickoff_scope(arguments))
    return scope


def _scope_paths(workspace: Path, command_name: str, arguments: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    if command_name in {"promote", "validate"}:
        candidates.extend(arguments.get("paths") or [])
    elif command_name == "heartbeat":
        candidates.extend(arguments.get("changed") or [])
    elif command_name in {"action-permit", "attribute-change"}:
        path_argument = arguments.get("path") or []
        if isinstance(path_argument, str):
            candidates.append(path_argument)
        else:
            candidates.extend(path_argument)
    elif command_name == "project-kickoff":
        kickoff_scope = _project_kickoff_scope(arguments)
        if kickoff_scope.get("goal_id"):
            candidates.append(f"goals/{kickoff_scope['goal_id']}.json")
        if kickoff_scope.get("task_id"):
            candidates.append(f"tasks/task_{kickoff_scope['task_id']}.md")
    elif command_name == "new-task" and arguments.get("id"):
        candidates.append(f"tasks/task_{arguments['id']}.md")
    elif command_name == "new-report" and arguments.get("id"):
        candidates.append(f"reports/report_{str(arguments['id']).removeprefix('REPORT_')}.md")
    elif command_name in {"new-bug", "new-code", "new-decision"} and arguments.get("id"):
        root = {"new-bug": "bugs", "new-code": "code", "new-decision": "decisions"}[command_name]
        candidates.append(f"{root}/{arguments['id']}.md")
    elif command_name == "new-research" and arguments.get("id"):
        candidates.append(f"research/{arguments['id']}.md")
    elif command_name == "close-task" and arguments.get("id"):
        candidates.append(f"tasks/task_{arguments['id']}.md")
    if command_name in {"goal-sync", "new-task", "close-task", "new-loop", "finalize"}:
        candidates.extend(
            workspace_relative(path, workspace)
            for path in (workspace / "goals").glob("*.json")
            if path.is_file()
        )
    if command_name == "finalize":
        current = read_json(workspace / "current.json", default={}) or {}
        loop = int(current.get("loop", 1))
        candidates.append(f"archive/ARCHIVE_L{loop:04d}.md")
    normalized: list[str] = []
    for value in candidates:
        try:
            normalized.append(workspace_relative(resolve_workspace_path(workspace, str(value)), workspace))
        except ValueError:
            continue
    return sorted(set(normalized))


def _record_violation(
    workspace: Path,
    *,
    action_type: str,
    message: str,
    command_name: str | None = None,
    phase: str | None = None,
    scope: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    violation_id = _identifier("GOV")
    scope = scope or runtime_scope(workspace)
    database = _database(workspace)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO governance_violations(
                violation_id,action_type,command_name,phase,goal_id,milestone_id,
                task_id,criterion_id,message,details_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                violation_id, action_type, command_name, phase, scope.get("goal_id"),
                scope.get("milestone_id"), scope.get("task_id"), scope.get("criterion_id"),
                message, json_dumps(details or {}, pretty=False), utc_now(),
            ),
        )
    prune_governance_history(workspace)
    return violation_id


def _current_phase(connection: Any) -> str:
    row = connection.execute(
        "SELECT current_phase FROM governance_state WHERE singleton_id=1"
    ).fetchone()
    return str(row["current_phase"]) if row and row["current_phase"] else "COMMAND_INTAKE"


def _require_transition(previous: str, target: str) -> None:
    if target not in PHASES:
        raise GovernanceError(f"unknown governance phase: {target}")
    if target not in ALLOWED_TRANSITIONS.get(previous, set()):
        raise GovernanceError(f"invalid KAIROS phase transition: {previous} -> {target}")


def issue_action_permit(
    workspace: Path,
    *,
    phase: str,
    command_name: str,
    scope: dict[str, Any],
    paths: Iterable[str] = (),
    reason: str,
    ttl_seconds: int = 120,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise GovernanceError(f"unknown governance phase: {phase}")
    if not 1 <= ttl_seconds <= 3600:
        raise GovernanceError("action permit ttl_seconds must be between 1 and 3600")
    config = load_config(workspace)
    issued = _now()
    permit_id = _identifier("GAP")
    nonce = secrets.token_hex(16)
    scope_payload = {"paths": sorted(set(paths))}
    database = _database(workspace)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO action_permits(
                permit_id,action_id,phase,command_name,workspace_id,goal_id,
                milestone_id,task_id,criterion_id,scope_json,reason,nonce,status,
                issued_at,expires_at,consumed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                permit_id, None, phase, command_name, config["workspace_id"],
                scope.get("goal_id"), scope.get("milestone_id"), scope.get("task_id"),
                scope.get("criterion_id"), json_dumps(scope_payload, pretty=False),
                reason, nonce, "ACTIVE", issued.isoformat().replace("+00:00", "Z"),
                (issued + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
                None,
            ),
        )
    return {
        "schema": "kairos-action-permit/v1",
        "permit_id": permit_id,
        "phase": phase,
        "command_name": command_name,
        "scope": {**scope, **scope_payload},
        "status": "ACTIVE",
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
    }


def consume_action_permit(
    workspace: Path,
    *,
    permit_id: str,
    command_name: str,
    phase: str,
    expected_scope: dict[str, Any],
    enforce_transition: bool = True,
) -> dict[str, Any]:
    database = _database(workspace)
    action_id = _identifier("GAR")
    started_at = utc_now()
    error: str | None = None
    transition_error: str | None = None
    permit: dict[str, Any] = {}
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM action_permits WHERE permit_id=?", (permit_id,)
        ).fetchone()
        if not row:
            error = f"unknown action permit: {permit_id}"
        else:
            permit = dict(row)
            if row["status"] != "ACTIVE":
                error = f"action permit is not active: {permit_id} ({row['status']})"
            elif _parse_time(row["expires_at"]) <= _now():
                connection.execute(
                    "UPDATE action_permits SET status='EXPIRED' WHERE permit_id=?",
                    (permit_id,),
                )
                error = f"action permit expired: {permit_id}"
            elif row["command_name"] != command_name or row["phase"] != phase:
                error = (
                    f"action permit scope mismatch: expected {row['command_name']}/"
                    f"{row['phase']}, received {command_name}/{phase}"
                )
            else:
                for key in ("goal_id", "milestone_id", "task_id", "criterion_id"):
                    if row[key] != expected_scope.get(key):
                        error = f"action permit {key} mismatch"
                        break
        if not error:
            previous = _current_phase(connection)
            try:
                _require_transition(previous, phase)
            except GovernanceError as exc:
                transition_error = str(exc)
                if enforce_transition:
                    error = transition_error
            if not error:
                connection.execute(
                    """
                    UPDATE action_permits
                    SET status='CONSUMED',action_id=?,consumed_at=?
                    WHERE permit_id=? AND status='ACTIVE'
                    """,
                    (action_id, started_at, permit_id),
                )
                connection.execute(
                    """
                    INSERT INTO governed_action_receipts(
                        action_id,permit_id,phase,command_name,goal_id,milestone_id,
                        task_id,criterion_id,status,started_at,completed_at,result_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        action_id, permit_id, phase, command_name,
                        expected_scope.get("goal_id"), expected_scope.get("milestone_id"),
                        expected_scope.get("task_id"), expected_scope.get("criterion_id"),
                        "RUNNING", started_at, None, "{}",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO governance_state(
                        singleton_id,enforcement_mode,current_phase,goal_id,milestone_id,
                        task_id,criterion_id,last_action_id,updated_at
                    ) VALUES(1,?,?,?,?,?,?,?,?)
                    ON CONFLICT(singleton_id) DO UPDATE SET
                        enforcement_mode=excluded.enforcement_mode,
                        current_phase=excluded.current_phase,
                        goal_id=excluded.goal_id,
                        milestone_id=excluded.milestone_id,
                        task_id=excluded.task_id,
                        criterion_id=excluded.criterion_id,
                        last_action_id=excluded.last_action_id,
                        updated_at=excluded.updated_at
                    """,
                    (
                        governance_mode(workspace), phase, expected_scope.get("goal_id"),
                        expected_scope.get("milestone_id"), expected_scope.get("task_id"),
                        expected_scope.get("criterion_id"), action_id, started_at,
                    ),
                )
        if error and permit and permit.get("status") == "ACTIVE":
            connection.execute(
                """
                UPDATE action_permits
                SET status='REVOKED'
                WHERE permit_id=? AND status='ACTIVE'
                """,
                (permit_id,),
            )
    if transition_error and not enforce_transition:
        _record_violation(
            workspace,
            action_type="INVALID_PHASE_TRANSITION_AUDIT",
            command_name=command_name,
            phase=phase,
            scope=expected_scope,
            message=transition_error,
            details={"permit_id": permit_id},
        )
    if error:
        _record_violation(
            workspace,
            action_type="ACTION_PERMIT_REJECTED",
            command_name=command_name,
            phase=phase,
            scope=expected_scope,
            message=error,
            details={"permit_id": permit_id},
        )
        raise GovernanceError(error)
    return {
        "schema": "kairos-governed-action/v1",
        "action_id": action_id,
        "permit_id": permit_id,
        "phase": phase,
        "command_name": command_name,
        "scope": expected_scope,
        "status": "RUNNING",
        "started_at": started_at,
    }


def begin_cli_action(
    workspace: Path,
    *,
    command_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if command_name not in COMMAND_PHASES:
        raise GovernanceError(f"command has no declared KAIROS phase: {command_name}")
    phase = COMMAND_PHASES[command_name]
    mode = governance_mode(workspace)
    scope = runtime_scope(workspace, command_name=command_name, arguments=arguments)
    missing = [
        key for key in ("goal_id", "milestone_id", "task_id", "criterion_id")
        if not scope.get(key)
    ]
    if missing and command_name not in SCOPE_EXEMPT_COMMANDS:
        message = (
            f"governed command requires active goal, milestone, task, and criterion; "
            f"missing {', '.join(missing)}"
        )
        # A source-only or otherwise unmaterialized workspace has no governance
        # database yet. Failing a misplaced governed command must not create one
        # merely to record that the command was refused; that would mutate the
        # very recovery surface the caller still needs to materialize.
        if database_path(workspace).is_file():
            _record_violation(
                workspace,
                action_type="MISSING_ACTIVE_SCOPE",
                command_name=command_name,
                phase=phase,
                scope=scope,
                message=message,
                details={"missing": missing},
            )
        if mode == "required":
            raise GovernanceError(message)
    paths = _scope_paths(workspace, command_name, arguments)
    permit = issue_action_permit(
        workspace,
        phase=phase,
        command_name=command_name,
        scope=scope,
        paths=paths,
        reason=f"automatic CLI preflight for {command_name}",
    )
    return consume_action_permit(
        workspace,
        permit_id=permit["permit_id"],
        command_name=command_name,
        phase=phase,
        expected_scope=scope,
        enforce_transition=mode == "required",
    )


def finish_cli_action(
    workspace: Path,
    action: dict[str, Any],
    *,
    success: bool,
    result: Any,
) -> dict[str, Any]:
    completed_at = utc_now()
    try:
        serialized = json_dumps(result, pretty=False)
    except (TypeError, ValueError):
        serialized = json_dumps({"representation": repr(result)}, pretty=False)
    if len(serialized.encode("utf-8")) > 8192:
        serialized = json_dumps(
            {
                "truncated": True,
                "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "preview": serialized[:2048],
            },
            pretty=False,
        )
    latest_scope = runtime_scope(workspace)
    database = _database(workspace)
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE governed_action_receipts
            SET status=?,completed_at=?,result_json=?
            WHERE action_id=? AND status='RUNNING'
            """,
            ("SUCCESS" if success else "FAILED", completed_at, serialized, action["action_id"]),
        )
        connection.execute(
            """
            UPDATE governance_state
            SET enforcement_mode=?,current_phase=?,goal_id=?,milestone_id=?,task_id=?,
                criterion_id=?,last_action_id=?,updated_at=?
            WHERE singleton_id=1
            """,
            (
                governance_mode(workspace), action["phase"], latest_scope.get("goal_id"),
                latest_scope.get("milestone_id"), latest_scope.get("task_id"),
                latest_scope.get("criterion_id"), action["action_id"], completed_at,
            ),
        )
    prune_governance_history(workspace)
    return {
        "action_id": action["action_id"],
        "permit_id": action["permit_id"],
        "phase": action["phase"],
        "status": "SUCCESS" if success else "FAILED",
        "completed_at": completed_at,
    }


def _delete_excess(
    connection: Any,
    *,
    table: str,
    identifier: str,
    where: str,
    order_by: str,
    keep: int,
) -> int:
    rows = connection.execute(
        f"""
        SELECT {identifier} FROM {table}
        WHERE {where}
        ORDER BY {order_by}
        LIMIT -1 OFFSET ?
        """,
        (keep,),
    ).fetchall()
    if not rows:
        return 0
    connection.executemany(
        f"DELETE FROM {table} WHERE {identifier}=?",
        [(row[identifier],) for row in rows],
    )
    return len(rows)


def prune_governance_history(workspace: Path) -> dict[str, int]:
    database = _database(workspace)
    with database.transaction() as connection:
        actions = _delete_excess(
            connection,
            table="governed_action_receipts",
            identifier="action_id",
            where="status IN ('SUCCESS','FAILED')",
            order_by="completed_at DESC,started_at DESC,action_id DESC",
            keep=MAX_GOVERNED_ACTION_RECEIPTS,
        )
        permits = _delete_excess(
            connection,
            table="action_permits",
            identifier="permit_id",
            where=(
                "status<>'ACTIVE' AND NOT EXISTS ("
                "SELECT 1 FROM governed_action_receipts r "
                "WHERE r.permit_id=action_permits.permit_id)"
            ),
            order_by="COALESCE(consumed_at,expires_at) DESC,permit_id DESC",
            keep=MAX_TERMINAL_ACTION_PERMITS,
        )
        violations = _delete_excess(
            connection,
            table="governance_violations",
            identifier="violation_id",
            where="1=1",
            order_by="created_at DESC,violation_id DESC",
            keep=MAX_GOVERNANCE_VIOLATIONS,
        )
        quarantine = _delete_excess(
            connection,
            table="quarantine_entries",
            identifier="quarantine_id",
            where="status<>'OPEN'",
            order_by="COALESCE(resolved_at,created_at) DESC,quarantine_id DESC",
            keep=MAX_QUARANTINE_HISTORY,
        )
    return {
        "governed_action_receipts": actions,
        "action_permits": permits,
        "governance_violations": violations,
        "quarantine_entries": quarantine,
    }


def _managed_relative(workspace: Path, value: str) -> str:
    relative = workspace_relative(resolve_workspace_path(workspace, value), workspace)
    config = load_config(workspace)
    if relative in set(config.get("canonical_files", [])):
        return relative
    if any(relative.startswith(f"{root.rstrip('/')}/") for root in config.get("document_roots", [])):
        return relative
    if relative.startswith("goals/") and relative.endswith(".json"):
        return relative
    raise GovernanceError(f"external edit path is not a managed KAIROS document: {relative}")


def _file_snapshot(workspace: Path, relative: str) -> dict[str, Any]:
    path = resolve_workspace_path(workspace, relative)
    if not path.exists():
        return {
            "path": relative,
            "exists": False,
            "sha256": None,
            "size": None,
            "mtime_ns": None,
        }
    if not path.is_file():
        raise GovernanceError(f"external edit scope must name a file: {relative}")
    stat = path.stat()
    return {
        "path": relative,
        "exists": True,
        "sha256": sha256_bytes(path.read_bytes()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def issue_external_edit_permit(
    workspace: Path,
    *,
    paths: Iterable[str],
    reason: str,
    ttl_seconds: int = 900,
) -> dict[str, Any]:
    normalized = sorted({_managed_relative(workspace, str(value)) for value in paths})
    if not normalized:
        raise GovernanceError("at least one managed document path is required")
    scope = runtime_scope(workspace)
    missing = [
        key for key in ("goal_id", "milestone_id", "task_id", "criterion_id")
        if not scope.get(key)
    ]
    if missing:
        raise GovernanceError(
            f"external edit permit requires active task scope; missing {', '.join(missing)}"
        )
    snapshots = [_file_snapshot(workspace, relative) for relative in normalized]
    database = _database(workspace)
    with database.transaction() as connection:
        active = connection.execute(
            """
            SELECT permit_id,scope_json FROM action_permits
            WHERE command_name='external-edit' AND status='ACTIVE'
            """
        ).fetchall()
        for row in active:
            existing = set(json.loads(row["scope_json"]).get("paths", []))
            if existing.intersection(normalized):
                raise GovernanceError(
                    f"an active external edit permit already covers this scope: {row['permit_id']}"
                )
    permit = issue_action_permit(
        workspace,
        phase="MUTATION",
        command_name="external-edit",
        scope=scope,
        paths=normalized,
        reason=reason,
        ttl_seconds=ttl_seconds,
    )
    payload = {
        "paths": normalized,
        "pre_edit_snapshots": snapshots,
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE action_permits SET scope_json=? WHERE permit_id=?",
            (json_dumps(payload, pretty=False), permit["permit_id"]),
        )
    permit["scope"].update(payload)
    permit["reason"] = reason
    return permit


def _running_action_paths(
    connection: Any,
    *,
    scope: dict[str, Any],
) -> tuple[str | None, set[str]]:
    rows = connection.execute(
        """
        SELECT r.action_id,r.phase,r.command_name,p.scope_json,
               r.goal_id,r.milestone_id,r.task_id,r.criterion_id
        FROM governed_action_receipts r
        JOIN action_permits p ON p.permit_id=r.permit_id
        WHERE r.status='RUNNING'
        ORDER BY r.started_at DESC
        """
    ).fetchall()
    transition_fallback: tuple[str, set[str]] | None = None
    for row in rows:
        paths = set(json.loads(row["scope_json"]).get("paths", []))
        if any(row[key] != scope.get(key) for key in ("goal_id", "milestone_id", "task_id", "criterion_id")):
            if row["phase"] in {"TASK_FORMULATION", "CLOSURE", "ROLLOVER"} and paths:
                transition_fallback = transition_fallback or (str(row["action_id"]), paths)
            continue
        return str(row["action_id"]), paths
    return transition_fallback or (None, set())


def _changed_relative_paths(
    workspace: Path,
    changed: Iterable[Path],
    missing: Iterable[str],
) -> list[str]:
    values = [workspace_relative(path, workspace) for path in changed]
    values.extend(str(value).replace("\\", "/") for value in missing)
    return sorted({value for value in values if value not in DERIVED_DOCUMENTS})


def _consume_matching_external_permit(
    workspace: Path,
    *,
    relative_paths: list[str],
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    database = _database(workspace)
    selected: dict[str, Any] | None = None
    selected_paths: list[str] = []
    expired: list[str] = []
    with database.transaction() as connection:
        rows = connection.execute(
            """
            SELECT * FROM action_permits
            WHERE command_name='external-edit' AND status='ACTIVE'
            ORDER BY issued_at
            """
        ).fetchall()
        for row in rows:
            if _parse_time(row["expires_at"]) <= _now():
                connection.execute(
                    "UPDATE action_permits SET status='EXPIRED' WHERE permit_id=?",
                    (row["permit_id"],),
                )
                expired.append(str(row["permit_id"]))
                continue
            if any(row[key] != scope.get(key) for key in ("goal_id", "milestone_id", "task_id", "criterion_id")):
                continue
            payload = json.loads(row["scope_json"])
            covered = sorted(set(relative_paths).intersection(payload.get("paths", [])))
            if covered and len(covered) > len(selected_paths):
                selected = {**dict(row), "scope": payload}
                selected_paths = covered
    for permit_id in expired:
        _record_violation(
            workspace,
            action_type="EXTERNAL_EDIT_PERMIT_EXPIRED",
            command_name="external-edit",
            phase="MUTATION",
            scope=scope,
            message=f"external edit permit expired before freshness reconciliation: {permit_id}",
            details={"permit_id": permit_id},
        )
    if not selected:
        return None
    snapshots = {item["path"]: item for item in selected["scope"].get("pre_edit_snapshots", [])}
    for relative in selected_paths:
        before = snapshots.get(relative)
        if not before:
            return None
        after = _file_snapshot(workspace, relative)
        if before.get("exists") == after.get("exists") and before.get("sha256") == after.get("sha256"):
            raise GovernanceError(f"external edit permit has no observable change for {relative}")
    action = consume_action_permit(
        workspace,
        permit_id=selected["permit_id"],
        command_name="external-edit",
        phase="MUTATION",
        expected_scope=scope,
        enforce_transition=governance_mode(workspace) == "required",
    )
    finish_cli_action(
        workspace,
        action,
        success=True,
        result={"accepted_paths": selected_paths, "reason": selected["reason"]},
    )
    return {
        "permit_id": selected["permit_id"],
        "action_id": action["action_id"],
        "paths": selected_paths,
    }


def _quarantine_change(
    workspace: Path,
    *,
    relative: str,
    reason: str,
    scope: dict[str, Any],
) -> str:
    snapshot = _file_snapshot(workspace, relative)
    observed_sha256 = snapshot["sha256"] or sha256_bytes(f"MISSING:{relative}".encode("utf-8"))
    database = _database(workspace)
    with database.transaction() as connection:
        existing = connection.execute(
            """
            SELECT quarantine_id FROM quarantine_entries
            WHERE path=? AND observed_sha256=? AND status='OPEN'
            """,
            (relative, observed_sha256),
        ).fetchone()
        if existing:
            return str(existing["quarantine_id"])
        quarantine_id = _identifier("QUAR")
        connection.execute(
            """
            INSERT INTO quarantine_entries(
                quarantine_id,path,source_kind,observed_sha256,observed_size,
                observed_mtime_ns,reason,goal_id,milestone_id,task_id,criterion_id,
                status,attributed_action_id,details_json,created_at,resolved_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                quarantine_id, relative, "UNATTRIBUTED_WORKSPACE_DRIFT", observed_sha256,
                snapshot["size"], snapshot["mtime_ns"], reason, scope.get("goal_id"),
                scope.get("milestone_id"), scope.get("task_id"), scope.get("criterion_id"),
                "OPEN", None, json_dumps({"snapshot": snapshot}, pretty=False), utc_now(), None,
            ),
        )
    return quarantine_id


def _terminalize_open_quarantine_for_path(
    connection: Any,
    *,
    relative: str,
    snapshot: dict[str, Any],
    action_id: str,
    reason: str,
    resolved_at: str,
    resolution_mode: str,
) -> list[dict[str, str]]:
    current_sha256 = snapshot["sha256"] or sha256_bytes(
        f"MISSING:{relative}".encode("utf-8")
    )
    rows = connection.execute(
        """
        SELECT quarantine_id,observed_sha256,details_json
        FROM quarantine_entries
        WHERE path=? AND status='OPEN'
        ORDER BY created_at,quarantine_id
        """,
        (relative,),
    ).fetchall()
    resolved: list[dict[str, str]] = []
    for row in rows:
        same_bytes = str(row["observed_sha256"]) == current_sha256
        status = "ATTRIBUTED" if same_bytes else "SUPERSEDED"
        details = json.loads(row["details_json"])
        details.update(
            attribution_action_id=action_id,
            attribution_reason=reason,
            current_snapshot=snapshot,
            resolution_kind=(
                "CURRENT_HASH_ATTRIBUTED"
                if same_bytes
                else "HISTORICAL_HASH_SUPERSEDED"
            ),
            resolution_mode=resolution_mode,
        )
        updated = connection.execute(
            """
            UPDATE quarantine_entries
            SET status=?,attributed_action_id=?,resolved_at=?,details_json=?
            WHERE quarantine_id=? AND status='OPEN'
            """,
            (
                status,
                action_id,
                resolved_at,
                json_dumps(details, pretty=False),
                row["quarantine_id"],
            ),
        ).rowcount
        if updated == 1:
            resolved.append(
                {"quarantine_id": str(row["quarantine_id"]), "status": status}
            )
    return resolved


def _resolve_attributed_path_quarantine(
    workspace: Path,
    *,
    attribution_actions: dict[str, str],
    reason: str,
) -> list[str]:
    resolved: list[str] = []
    database = _database(workspace)
    resolved_at = utc_now()
    with database.transaction() as connection:
        for relative, action_id in sorted(attribution_actions.items()):
            snapshot = _file_snapshot(workspace, relative)
            rows = _terminalize_open_quarantine_for_path(
                connection,
                relative=relative,
                snapshot=snapshot,
                action_id=action_id,
                reason=reason,
                resolved_at=resolved_at,
                resolution_mode="AUTOMATIC_AUTHORIZED_CHANGE",
            )
            resolved.extend(row["quarantine_id"] for row in rows)
    return resolved


def guard_freshness_changes(
    workspace: Path,
    *,
    changed: Iterable[Path],
    missing: Iterable[str],
    reason: str,
) -> dict[str, Any]:
    relative_paths = _changed_relative_paths(workspace, changed, missing)
    if not relative_paths:
        return {
            "checked": True,
            "mode": governance_mode(workspace),
            "attributed_paths": [],
            "resolved_quarantine": [],
            "quarantined": [],
        }
    scope = runtime_scope(workspace)
    database = _database(workspace)
    connection = database.connect(read_only=True)
    try:
        running_action_id, running_paths = _running_action_paths(connection, scope=scope)
    finally:
        connection.close()
    attributed: list[str] = []
    attribution_actions: dict[str, str] = {}
    external_actions: list[dict[str, Any]] = []
    remaining = sorted(set(relative_paths))
    while remaining:
        external = _consume_matching_external_permit(
            workspace,
            relative_paths=remaining,
            scope=scope,
        )
        if not external:
            break
        external_actions.append(external)
        attributed.extend(external["paths"])
        attribution_actions.update(
            {relative: external["action_id"] for relative in external["paths"]}
        )
        remaining = sorted(set(remaining) - set(external["paths"]))
    running_attributed = sorted(set(remaining).intersection(running_paths))
    attributed.extend(running_attributed)
    if running_action_id:
        attribution_actions.update(
            {relative: running_action_id for relative in running_attributed}
        )
    remaining = sorted(set(remaining) - set(running_attributed))
    resolved_quarantine = _resolve_attributed_path_quarantine(
        workspace,
        attribution_actions=attribution_actions,
        reason=reason,
    )
    mode = governance_mode(workspace)
    if remaining and mode == "audit":
        _record_violation(
            workspace,
            action_type="UNATTRIBUTED_CHANGE_AUDIT",
            command_name="freshness",
            phase="MUTATION",
            scope=scope,
            message="unattributed workspace drift observed while governance is in audit mode",
            details={"paths": remaining, "reason": reason},
        )
        attributed.extend(remaining)
        remaining = []
    quarantined: list[dict[str, str]] = []
    for relative in remaining:
        quarantine_id = _quarantine_change(
            workspace,
            relative=relative,
            reason=reason,
            scope=scope,
        )
        quarantined.append({"quarantine_id": quarantine_id, "path": relative})
    if quarantined:
        message = (
            "unattributed KAIROS source drift is quarantined and cannot be promoted: "
            + ", ".join(item["path"] for item in quarantined)
        )
        _record_violation(
            workspace,
            action_type="UNATTRIBUTED_CHANGE_QUARANTINED",
            command_name="freshness",
            phase="MUTATION",
            scope=scope,
            message=message,
            details={"entries": quarantined, "reason": reason},
        )
        raise GovernanceError(message)
    return {
        "checked": True,
        "mode": mode,
        "running_action_id": running_action_id,
        "external_action": external_actions[0] if external_actions else None,
        "external_actions": external_actions,
        "attributed_paths": sorted(set(attributed)),
        "resolved_quarantine": resolved_quarantine,
        "quarantined": [],
    }


def attribute_quarantined_changes(
    workspace: Path,
    *,
    paths: Iterable[str],
    reason: str,
    action_id: str,
) -> dict[str, Any]:
    normalized = sorted({_managed_relative(workspace, str(value)) for value in paths})
    if not normalized:
        raise GovernanceError("at least one quarantined path is required")
    database = _database(workspace)
    resolved_at = utc_now()
    resolutions: list[dict[str, str]] = []
    with database.transaction() as connection:
        for relative in normalized:
            snapshot = _file_snapshot(workspace, relative)
            path_resolutions = _terminalize_open_quarantine_for_path(
                connection,
                relative=relative,
                snapshot=snapshot,
                action_id=action_id,
                reason=reason,
                resolved_at=resolved_at,
                resolution_mode="EXPLICIT_CURRENT_STATE_REVIEW",
            )
            if not path_resolutions:
                current_sha256 = snapshot["sha256"] or sha256_bytes(
                    f"MISSING:{relative}".encode("utf-8")
                )
                already_attributed = connection.execute(
                    """
                    SELECT quarantine_id
                    FROM quarantine_entries
                    WHERE path=? AND status='ATTRIBUTED' AND observed_sha256=?
                    ORDER BY resolved_at DESC,created_at DESC,quarantine_id DESC
                    LIMIT 1
                    """,
                    (relative, current_sha256),
                ).fetchone()
                artifact = connection.execute(
                    "SELECT 1 FROM artifacts WHERE path=? LIMIT 1",
                    (relative,),
                ).fetchone()
                if already_attributed and not artifact:
                    # A previous multi-path review may have committed attribution before
                    # its heartbeat failed on another path. Reuse that exact current-hash
                    # evidence only while the source remains unpromoted; a promoted path
                    # still fails closed when no open quarantine exists.
                    resolutions.append(
                        {
                            "quarantine_id": str(already_attributed["quarantine_id"]),
                            "status": "ATTRIBUTED",
                        }
                    )
                    continue
                raise GovernanceError(f"no open quarantine entry exists for {relative}")
            resolutions.extend(path_resolutions)
    attributed = [
        row["quarantine_id"] for row in resolutions if row["status"] == "ATTRIBUTED"
    ]
    superseded = [
        row["quarantine_id"] for row in resolutions if row["status"] == "SUPERSEDED"
    ]
    status = "MIXED" if attributed and superseded else resolutions[0]["status"]
    result = {
        "paths": normalized,
        "quarantine_ids": [row["quarantine_id"] for row in resolutions],
        "attributed_quarantine_ids": attributed,
        "superseded_quarantine_ids": superseded,
        "status": status,
        "action_id": action_id,
        "resolved_at": resolved_at,
    }
    if len(normalized) == 1:
        result["path"] = normalized[0]
    return result


def attribute_quarantined_change(
    workspace: Path,
    *,
    path: str,
    reason: str,
    action_id: str,
) -> dict[str, Any]:
    """Review and attribute one exact current path through the multi-path primitive."""
    return attribute_quarantined_changes(
        workspace,
        paths=[path],
        reason=reason,
        action_id=action_id,
    )


def _supersede_reconciled_external_permits(
    workspace: Path,
    connection: Any,
) -> list[str]:
    """Retire edit authority whose changed bytes already equal SQLite authority."""
    superseded: list[str] = []
    rows = connection.execute(
        """
        SELECT permit_id,scope_json FROM action_permits
        WHERE command_name='external-edit' AND status='ACTIVE'
        ORDER BY issued_at
        """
    ).fetchall()
    for row in rows:
        payload = json.loads(row["scope_json"])
        snapshots = payload.get("pre_edit_snapshots", [])
        if not snapshots:
            continue
        changed = False
        reconciled = True
        for before in snapshots:
            relative = str(before.get("path", ""))
            if not relative:
                reconciled = False
                break
            after = _file_snapshot(workspace, relative)
            changed = changed or (
                before.get("exists") != after.get("exists")
                or before.get("sha256") != after.get("sha256")
            )
            artifact = connection.execute(
                "SELECT content_sha256 FROM artifacts WHERE path=?",
                (relative,),
            ).fetchone()
            if after["exists"]:
                if not artifact or artifact["content_sha256"] != after["sha256"]:
                    reconciled = False
                    break
            elif artifact:
                reconciled = False
                break
        # Both halves are load-bearing. Requiring an observed change is what keeps a permit
        # alive for an edit that has not happened yet: at issuance the target is typically
        # absent and unpromoted, which already satisfies reconciliation. Retiring on
        # reconciliation alone would drop a fresh permit before its edit, and the edit would
        # then arrive unattributed. A permit that can never observe a change is withdrawn
        # explicitly through revoke-permit instead.
        if not changed or not reconciled:
            continue
        updated = connection.execute(
            """
            UPDATE action_permits
            SET status='SUPERSEDED',consumed_at=?
            WHERE permit_id=? AND status='ACTIVE'
            """,
            (utc_now(), row["permit_id"]),
        ).rowcount
        if updated == 1:
            superseded.append(str(row["permit_id"]))
    return superseded


def revoke_action_permit(
    workspace: Path,
    *,
    permit_id: str,
    reason: str,
) -> dict[str, Any]:
    """Withdraw an outstanding permit whose edit will not be made.

    A permit is retired automatically only once its edit has landed and reconciled. A permit
    that will never see that edit - issued by mistake, or for bytes that were already in
    place - would otherwise hold the workspace closed until its TTL expires with no remedy.
    Withdrawal is therefore explicit, attributable and refused for a permit that is already
    backing a running action.
    """
    if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
        raise GovernanceError("revocation reason must contain 12 to 1000 characters")
    database = _database(workspace)
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT permit_id,status,command_name,action_id FROM action_permits WHERE permit_id=?",
            (permit_id,),
        ).fetchone()
        if not row:
            raise GovernanceError(f"action permit does not exist: {permit_id}")
        if row["status"] != "ACTIVE":
            raise GovernanceError(
                f"only an ACTIVE permit can be withdrawn; {permit_id} is {row['status']}"
            )
        running = connection.execute(
            """
            SELECT action_id FROM governed_action_receipts
            WHERE permit_id=? AND status='RUNNING'
            """,
            (permit_id,),
        ).fetchone()
        if running:
            raise GovernanceError(
                f"permit {permit_id} backs running action {running['action_id']} and cannot be withdrawn"
            )
        revoked_at = utc_now()
        connection.execute(
            "UPDATE action_permits SET status='REVOKED',consumed_at=? WHERE permit_id=?",
            (revoked_at, permit_id),
        )
    _record_violation(
        workspace,
        action_type="EXTERNAL_EDIT_PERMIT_REVOKED",
        command_name=str(row["command_name"]),
        phase="COMMAND_INTAKE",
        scope=runtime_scope(workspace),
        message=f"action permit withdrawn before use: {permit_id}",
        details={"permit_id": permit_id, "reason": reason.strip()},
    )
    return {
        "schema": "kairos-permit-revocation/v1",
        "permit_id": permit_id,
        "command_name": row["command_name"],
        "status": "REVOKED",
        "reason": reason.strip(),
        "revoked_at": revoked_at,
    }


def recover_orphaned_action(
    workspace: Path,
    *,
    target_action_id: str,
    reason: str,
    recovery_action_id: str,
) -> dict[str, Any]:
    """Fail an exact orphan while the recovery CLI owns the workspace lock."""
    if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
        raise GovernanceError("action recovery reason must contain 12 to 1000 characters")
    if target_action_id == recovery_action_id:
        raise GovernanceError("recover-action cannot target its own running action")
    database = _database(workspace)
    recovered_at = utc_now()
    with database.transaction() as connection:
        recovery = connection.execute(
            """
            SELECT action_id,command_name,status
            FROM governed_action_receipts
            WHERE action_id=?
            """,
            (recovery_action_id,),
        ).fetchone()
        if (
            not recovery
            or recovery["command_name"] != "recover-action"
            or recovery["status"] != "RUNNING"
        ):
            raise GovernanceError("action recovery requires its own live recover-action receipt")
        target = connection.execute(
            """
            SELECT r.action_id,r.permit_id,r.phase,r.command_name,r.goal_id,
                   r.milestone_id,r.task_id,r.criterion_id,r.status,r.started_at,
                   p.status AS permit_status,p.action_id AS permit_action_id
            FROM governed_action_receipts r
            JOIN action_permits p ON p.permit_id=r.permit_id
            WHERE r.action_id=?
            """,
            (target_action_id,),
        ).fetchone()
        if not target:
            raise GovernanceError(f"governed action does not exist: {target_action_id}")
        if target["status"] != "RUNNING":
            raise GovernanceError(
                f"only a RUNNING governed action can be recovered; "
                f"{target_action_id} is {target['status']}"
            )
        if (
            target["permit_status"] != "CONSUMED"
            or target["permit_action_id"] != target_action_id
        ):
            raise GovernanceError(
                f"running action {target_action_id} has inconsistent permit authority"
            )
        recovery_result = {
            "error": "OrphanedGovernedAction",
            "message": "The original process ended without completing its governed action.",
            "reason": reason.strip(),
            "recovery_action_id": recovery_action_id,
            "recovered_at": recovered_at,
        }
        updated = connection.execute(
            """
            UPDATE governed_action_receipts
            SET status='FAILED',completed_at=?,result_json=?
            WHERE action_id=? AND status='RUNNING'
            """,
            (
                recovered_at,
                json_dumps(recovery_result, pretty=False),
                target_action_id,
            ),
        ).rowcount
        if updated != 1:
            raise GovernanceError(
                f"running action changed during recovery: {target_action_id}"
            )
    target_scope = {
        "goal_id": target["goal_id"],
        "milestone_id": target["milestone_id"],
        "task_id": target["task_id"],
        "criterion_id": target["criterion_id"],
    }
    _record_violation(
        workspace,
        action_type="ORPHANED_ACTION_RECOVERED",
        command_name=str(target["command_name"]),
        phase="RECOVERY",
        scope=target_scope,
        message=f"orphaned governed action terminalized as FAILED: {target_action_id}",
        details={
            "target_action_id": target_action_id,
            "target_phase": target["phase"],
            "target_started_at": target["started_at"],
            "recovery_action_id": recovery_action_id,
            "reason": reason.strip(),
        },
    )
    return {
        "schema": "kairos-orphaned-action-recovery/v1",
        "target_action_id": target_action_id,
        "target_command": target["command_name"],
        "target_status": "FAILED",
        "target_permit_id": target["permit_id"],
        "recovery_action_id": recovery_action_id,
        "reason": reason.strip(),
        "recovered_at": recovered_at,
    }


def governance_status(
    workspace: Path,
    *,
    exclude_action_id: str | None = None,
) -> dict[str, Any]:
    database = _database(workspace)
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE action_permits SET status='EXPIRED'
            WHERE status='ACTIVE' AND expires_at<=?
            """,
            (now,),
        )
        superseded_external_permits = _supersede_reconciled_external_permits(
            workspace,
            connection,
        )
        state_row = connection.execute(
            "SELECT * FROM governance_state WHERE singleton_id=1"
        ).fetchone()
        open_quarantine = int(connection.execute(
            "SELECT count(*) FROM quarantine_entries WHERE status='OPEN'"
        ).fetchone()[0])
        active_permits = int(connection.execute(
            "SELECT count(*) FROM action_permits WHERE status='ACTIVE'"
        ).fetchone()[0])
        active_external_edit_permits = int(connection.execute(
            """
            SELECT count(*) FROM action_permits
            WHERE command_name='external-edit' AND status='ACTIVE'
            """
        ).fetchone()[0])
        if exclude_action_id:
            running_actions = int(connection.execute(
                """
                SELECT count(*) FROM governed_action_receipts
                WHERE status='RUNNING' AND action_id<>?
                """,
                (exclude_action_id,),
            ).fetchone()[0])
        else:
            running_actions = int(connection.execute(
                "SELECT count(*) FROM governed_action_receipts WHERE status='RUNNING'"
            ).fetchone()[0])
        violations = int(connection.execute(
            "SELECT count(*) FROM governance_violations"
        ).fetchone()[0])
        recent_rows = connection.execute(
            """
            SELECT violation_id,action_type,message,created_at
            FROM governance_violations ORDER BY created_at DESC LIMIT 5
            """
        ).fetchall()
    state = dict(state_row) if state_row else {
        "enforcement_mode": governance_mode(workspace),
        "current_phase": "COMMAND_INTAKE",
        **runtime_scope(workspace),
        "last_action_id": None,
        "updated_at": now,
    }
    verdict = (
        "PASS"
        if open_quarantine == 0 and running_actions == 0 and active_permits == 0
        else "BLOCKED"
    )
    return {
        "schema": "kairos-governance-status/v1",
        "verdict": verdict,
        "enforcement_mode": governance_mode(workspace),
        "current_phase": state.get("current_phase"),
        "scope": {
            "goal_id": state.get("goal_id"),
            "milestone_id": state.get("milestone_id"),
            "task_id": state.get("task_id"),
            "criterion_id": state.get("criterion_id"),
        },
        "last_action_id": state.get("last_action_id"),
        "open_quarantine": open_quarantine,
        "active_permits": active_permits,
        "active_external_edit_permits": active_external_edit_permits,
        "superseded_external_permits": superseded_external_permits,
        "running_actions": running_actions,
        "violation_count": violations,
        "recent_violations": [dict(row) for row in recent_rows],
        "checked_at": now,
    }


def enable_governance(workspace: Path) -> dict[str, Any]:
    scope = runtime_scope(workspace)
    missing = [key for key, value in scope.items() if not value]
    if missing:
        raise GovernanceError(
            f"cannot enable required governance without active scope: {', '.join(missing)}"
        )
    status = governance_status(workspace)
    if status["open_quarantine"] or status["running_actions"] > 1:
        raise GovernanceError("cannot enable required governance while quarantine or foreign running actions exist")
    config_path = workspace / ".kairos" / "config.json"
    config = load_config(workspace)
    previous = config.get("governance_enforcement", "audit")
    config["governance_enforcement"] = "required"
    atomic_write_json(config_path, config)
    database = _database(workspace)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO governance_state(
                singleton_id,enforcement_mode,current_phase,goal_id,milestone_id,
                task_id,criterion_id,last_action_id,updated_at
            ) VALUES(1,'required','VERIFICATION',?,?,?,?,NULL,?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                enforcement_mode='required',current_phase='VERIFICATION',
                goal_id=excluded.goal_id,milestone_id=excluded.milestone_id,
                task_id=excluded.task_id,criterion_id=excluded.criterion_id,
                updated_at=excluded.updated_at
            """,
            (
                scope["goal_id"], scope["milestone_id"], scope["task_id"],
                scope["criterion_id"], utc_now(),
            ),
        )
    return {
        "schema": "kairos-governance-enable/v1",
        "previous_mode": previous,
        "enforcement_mode": "required",
        "scope": scope,
        "enabled_at": utc_now(),
    }


def governance_blockers(workspace: Path) -> list[str]:
    status = governance_status(workspace)
    blockers: list[str] = []
    if status["enforcement_mode"] != "required":
        blockers.append("governance enforcement is not required")
    if status["open_quarantine"]:
        blockers.append(f"{status['open_quarantine']} quarantined source change(s) remain open")
    if status["running_actions"]:
        blockers.append(f"{status['running_actions']} governed action(s) remain unfinished")
    if status["active_permits"]:
        blockers.append(f"{status['active_permits']} unused action permit(s) remain active")
    return blockers


def record_recovery_review(workspace: Path, result: dict[str, Any]) -> dict[str, Any]:
    status = governance_status(workspace)
    review = {
        "schema": "kairos-governance-recovery-review/v1",
        "recovery_verified": bool(result.get("verified")),
        "enforcement_mode": status["enforcement_mode"],
        "open_quarantine": status["open_quarantine"],
        "verdict": "PASS" if result.get("verified") and not status["open_quarantine"] else "BLOCKED",
        "created_at": utc_now(),
    }
    atomic_write_json(workspace / ".kairos" / "governance_recovery_review.json", review)
    return review


def assert_command_classification(commands: Iterable[str]) -> None:
    missing = sorted(set(commands) - set(COMMAND_PHASES))
    if missing:
        raise GovernanceError(f"governed commands lack phase classification: {', '.join(missing)}")
