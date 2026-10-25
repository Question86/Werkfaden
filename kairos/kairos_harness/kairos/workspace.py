from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import DEFAULT_CANONICAL_FILES, DEFAULT_DOCUMENT_ROOTS, GOAL_SCHEMA_VERSION, RUNTIME_SCHEMA_VERSION
from .database import KnowledgeDatabase
from .goals import reconcile_goal_files, sync_goal_files, update_goal_manifest, validate_goal
from .promoter import promote_many
from .templates import (
    bug_document,
    code_document,
    pointer,
    render_document,
    report_document,
    router_document,
    task_document,
)
from .util import atomic_write_json, atomic_write_text, utc_now


class WorkspaceError(RuntimeError):
    pass


def database_path(workspace: Path) -> Path:
    return workspace / ".kairos" / "kairos.db"


def load_config(workspace: Path) -> dict[str, Any]:
    path = workspace / ".kairos" / "config.json"
    if not path.exists():
        raise WorkspaceError(f"not a KAIROS workspace: {workspace}")
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _seed_goal() -> dict[str, Any]:
    return {
        "schema": GOAL_SCHEMA_VERSION,
        "id": "GOAL_KAIROS_001",
        "title": "Operational KAIROS context harness",
        "objective": "Run long autonomous loops with same-heartbeat documentation, section-addressable knowledge promotion, adaptive context acquisition, and fail-closed finalization.",
        "state": "active",
        "created_at": utc_now(),
        "milestones": [
            {
                "id": "MILESTONE_KAIROS_01",
                "title": "Context-native knowledge substrate",
                "objective": "Establish deterministic context headers, typed references, section indexing, and question-oriented retrieval.",
                "state": "active",
                "depends_on": [],
                "criteria": [
                    {
                        "id": "CRIT_KAIROS_001",
                        "description": "All canonical document types have valid first-window context headers and stable section anchors.",
                        "state": "open",
                        "evidence_required": "Schema and template tests plus successful promotion receipts.",
                        "required_artifact_types": ["report", "code"],
                    },
                    {
                        "id": "CRIT_KAIROS_002",
                        "description": "Changed documents become searchable in the same heartbeat without a full workspace scan.",
                        "state": "open",
                        "evidence_required": "Incremental heartbeat receipt and idempotent replay test.",
                        "required_artifact_types": ["report"],
                    },
                    {
                        "id": "CRIT_KAIROS_003",
                        "description": "English causal questions retrieve the correct document section and its typed context chase.",
                        "state": "open",
                        "evidence_required": "Gold-query retrieval results and search-contract receipts.",
                        "required_artifact_types": ["report"],
                    },
                    {
                        "id": "CRIT_KAIROS_004",
                        "description": "Finalization remains blocked while promotions fail or required criteria lack evidence.",
                        "state": "open",
                        "evidence_required": "Fail-closed finalization integration test.",
                        "required_artifact_types": ["report"],
                    },
                ],
            }
        ],
    }


def _starter_architecture_document(workspace_id: str) -> str:
    metadata = {
        "schema": "kairos-context/v1",
        "id": "KAIROS_STARTER_ARCHITECTURE",
        "type": "documentation",
        "revision": 1,
        "state": "active",
        "authority": "architecture_authority",
        "workspace": workspace_id,
        "route": f"{workspace_id}/KAIROS_STARTER_ARCHITECTURE",
        "updated_at": utc_now(),
        "capsule": "Clean starter self-model for KAIROS authority, content placement, metadata retrieval, loop finalization, and atomic project kickoff.",
        "claim_boundary": "This document owns starter architecture and placement; live state, task contracts, and promoted evidence own their scoped claims.",
        "entities": ["KAIROS_STARTER_ARCHITECTURE", "KAIROS", "SQLite FTS5"],
        "facets": ["architecture", "content-placement", "database-model", "lifecycle", "project-kickoff"],
        "criteria": [],
        "does_not_answer": ["live project outcome", "task completion"],
        "answers": [
            {
                "intent": "architecture",
                "question": "How is KAIROS organized and what should I read first?",
                "target": "s-entry",
            },
            {
                "intent": "content_placement",
                "question": "Where should I put new KAIROS content?",
                "target": "s-placement",
            },
            {
                "intent": "lifecycle",
                "question": "How do I start a new project from the golden KAIROS template?",
                "target": "s-kickoff",
            },
            {
                "intent": "architecture",
                "question": "How is KAIROS architecture represented in the database?",
                "target": "s-database",
            },
        ],
        "refs": {
            "contract": pointer(
                "AGENTS.md",
                section="s-operating-loop",
                artifact_id="KAIROS_OPERATING_CONTRACT",
                version="1",
                relation="requires",
                tags=("contract", "workflow"),
                source="system",
            ),
            "state": pointer(
                "current.json",
                version="dynamic",
                relation="references",
                tags=("state", "runtime"),
                source="system",
            ),
        },
        "search_contract": [
            {
                "query": "How is KAIROS organized and what should I read first?",
                "expected": "KAIROS_STARTER_ARCHITECTURE#s-entry",
                "required_top_k": 1,
            },
            {
                "query": "Where should I put new KAIROS content?",
                "expected": "KAIROS_STARTER_ARCHITECTURE#s-placement",
                "required_top_k": 1,
            },
            {
                "query": "How do I start a new project from the golden KAIROS template?",
                "expected": "KAIROS_STARTER_ARCHITECTURE#s-kickoff",
                "required_top_k": 1,
            },
        ],
    }
    return render_document(
        metadata,
        "KAIROS STARTER ARCHITECTURE",
        [
            {
                "id": "s-entry",
                "title": "LLM ENTRY",
                "capsule": "Use the operating contract for procedure, live state for current scope, and this self-model for stable architecture.",
                "content": "Read AGENTS.md, current.json, and _LOOP_GATE.md. A new Human material command becomes a task. Ask KAIROS metadata for meaning and authority before requesting bounded source inspection.",
            },
            {
                "id": "s-layers",
                "title": "SYSTEM LAYERS",
                "capsule": "Authoritative documents feed a verified SQLite projection, bounded routers, receipts, archives, and recovery packages.",
                "content": "Goal JSON and governed Markdown own claims. Heartbeats validate and promote section-addressable metadata into SQLite. Generated routers expose the current frontier. Archives and verified backups seal each numbered loop.",
            },
            {
                "id": "s-placement",
                "title": "CONTENT PLACEMENT",
                "capsule": "Place each durable claim by epistemic role and connect it with typed workspace-relative references.",
                "content": "| Content | Location |\n|---|---|\n| Goal, milestone, criterion | goals/ |\n| Required work | tasks/ |\n| Outcome and evidence | reports/ |\n| Failure and root cause | bugs/ |\n| Implementation map | code/ |\n| Source fact and interpretation | research/ |\n| Tradeoff decision | decisions/ |\n| Stable synthesis | docs/ |\n| Closed-loop synthesis | archive/ |",
            },
            {
                "id": "s-database",
                "title": "DATABASE REPRESENTATION",
                "capsule": "SQLite stores artifacts, stable sections, natural-language handles, typed relations, goal coverage, receipts, and governance evidence.",
                "content": "Search returns artifact and section routes plus context-chase edges. Scores route inspection but never override the owning source. SQLite and dynamic routers are derived and must not be edited directly.",
            },
            {
                "id": "s-lifecycle",
                "title": "LOOP LIFECYCLE",
                "capsule": "Task and goal closure make a loop ready; finalize creates its mandatory archive, receipt, and verified backup.",
                "content": "BREATHE manages prompt pressure only. finalize seals semantic completion. FINALIZED history remains immutable. Every completed numbered loop retains ARCHIVE_LNNNN and a verified backup.",
            },
            {
                "id": "s-kickoff",
                "title": "ATOMIC PROJECT KICKOFF",
                "capsule": "One Human-triggered project-kickoff contract creates fresh goal and task sources and activates the next loop without reopening the predecessor.",
                "content": "Run kairos project-kickoff with a kairos-project-kickoff/v1 JSON contract. The command verifies the predecessor seal, derives new governance scope, stages deterministic sources, records an exact-once transition, and activates Loop N+1.",
            },
        ],
    )


def _canonical_documents(
    workspace_id: str,
    task_id: str,
    *,
    goal_id: str = "GOAL_KAIROS_001",
    milestone_id: str = "MILESTONE_KAIROS_01",
    criterion_ids: list[str] | None = None,
    task_title: str | None = None,
    task_objective: str | None = None,
) -> dict[str, str]:
    task_ref = pointer(
        f"tasks/task_{task_id}.md",
        section="s-objective",
        artifact_id=task_id,
        version="dynamic",
        relation="references",
        tags=("active", "task"),
        source="system",
    )
    agents = router_document(
        artifact_id="KAIROS_OPERATING_CONTRACT",
        title="KAIROS AGENT OPERATING CONTRACT",
        workspace_id=workspace_id,
        state="active",
        document_type="documentation",
        authority="operating_contract",
        capsule="Mandatory English-only, metadata-first operating contract for every agent working inside this KAIROS workspace.",
        answers=[
            {"intent": "orientation", "question": "How must an agent operate inside this KAIROS workspace?", "target": "s-operating-loop"},
            {"intent": "authority", "question": "Which KAIROS source is authoritative?", "target": "s-authority"},
            {"intent": "validation", "question": "When may an agent finalize a KAIROS loop?", "target": "s-finalization"},
        ],
        refs={
            "state": pointer("current.json", version="dynamic", relation="references", tags=("state", "authority"), source="system"),
            "gate": pointer("_LOOP_GATE.md", section="s-verdict", artifact_id="KAIROS_LOOP_GATE", version="dynamic", relation="requires", tags=("gate", "state"), source="system"),
            "router": pointer("NEURAL_CORTEX.md", section="s-orientation", artifact_id="KAIROS_NEURAL_CORTEX", version="dynamic", relation="references", tags=("orientation", "router"), source="system"),
            "active": pointer("ACTIVE.md", section="s-active-frontier", artifact_id="KAIROS_ACTIVE", version="dynamic", relation="references", tags=("active", "queue"), source="system"),
        },
        sections=[
            {
                "id": "s-language",
                "title": "LANGUAGE CONTRACT",
                "capsule": "All KAIROS source code, metadata, documents, queries, receipts, tests, logs, and generated artifacts must be written in English.",
                "content": "Do not introduce non-English identifiers, prose, filenames, query handles, or generated text into the KAIROS workspace.",
            },
            {
                "id": "s-operating-loop",
                "title": "MANDATORY OPERATING LOOP",
                "capsule": "Orient, select the active criterion, acquire bounded context, act, document, promote, verify, update coverage, and checkpoint in every material heartbeat.",
                "content": "1. Read `current.json`, `_LOOP_GATE.md`, `NEURAL_CORTEX.md`, and `ACTIVE.md`.\n2. Search by an explicit question before broad inspection.\n3. Choose `work`, `depth`, `breadth`, `breathe`, or `verify` from task structure.\n4. Perform one bounded action.\n5. Write the appropriate task, report, bug, code, decision, research, or archive document.\n6. Promote it in the same heartbeat and inspect the receipt.\n7. Update goal coverage and checkpoint state.",
            },
            {
                "id": "s-authority",
                "title": "AUTHORITY AND MUTATION BOUNDARY",
                "capsule": "Authority is question-scoped: operating rules, live state, permitted transitions, work scope, factual evidence, architecture, and retrieval each have a distinct owner.",
                "content": "- Operating procedure: `AGENTS.md`.\n- Live identifiers and lifecycle: `current.json`.\n- Allowed transitions and blockers: `_LOOP_GATE.md`.\n- Required work and acceptance: active goal and task contracts.\n- Factual outcome claims: promoted evidence sections.\n- Placement and topology: `KAIROS_ARCHITECTURE_ATLAS`.\n- Retrieval: routers and SQLite metadata, which locate authority but never replace it.\n\nA later or higher-scored result cannot override the source that owns the question.",
            },
            {
                "id": "s-context",
                "title": "CONTEXT ACQUISITION",
                "capsule": "Open the returned artifact section first, then chase only typed prerequisite, cause, implementation, evidence, validation, or supersession relations needed by the query frame.",
                "content": "Use `depth` for one causal or evidence chain, `breadth` for competing branches or contradictions, and `breathe` to persist the frontier before releasing prompt context. Full workspace scans are recovery-only.",
            },
            {
                "id": "s-retrieval",
                "title": "CHOOSING A RETRIEVAL SURFACE",
                "capsule": "Classify the question before choosing a surface; the same answer costs up to three orders of magnitude more through the wrong one.",
                "content": (
                    "`search` is not the default entry point. It ranks sections, so it "
                    "cannot aggregate and cannot reach the file system.\n\n"
                    "1. Counting or distribution over the corpus: `graph --census` "
                    "first, then `graph --inventory <table> --field F --value V` when "
                    "the rows themselves are needed. Never build a corpus total by "
                    "looping per-document reads; that path costs about two thousand "
                    "times the census.\n"
                    "2. Before naming an identifier in a query, run `graph --resolve "
                    "<partial>`. It returns the canonical identity and how many "
                    "documents carry it. At one or two documents, query the identifier "
                    "directly or go to `graph --node`. From three, describe the subject "
                    "in prose instead. At twenty-six or more, keep the identifier out "
                    "of the query: the bonus for exact graph identity is flat, so a "
                    "widely shared name pulls in every document that touches it.\n"
                    "3. An exact string you already hold, when only the containing "
                    "files matter, is a file-search question and not a graph question.\n"
                    "4. One relation out: `graph --node`. Two or more: `graph --chase` "
                    "with an explicit `--max-hops`.\n"
                    "5. An empty exact lookup is not a finding until its `no_match` "
                    "block says no qualified form of the name exists. Stored under a "
                    "qualified name and absent from the corpus are different answers, "
                    "and only the second may be cited.\n\n"
                    "The measured procedure behind these rules, with the cost of each "
                    "surface, is `RETRIEVAL_METHOD.md` in the harness documentation."
                ),
            },
            {
                "id": "s-finalization",
                "title": "FAIL-CLOSED FINALIZATION",
                "capsule": "Finalization is forbidden while a required criterion lacks validated evidence or any promotion event is pending or failed.",
                "content": "A completion claim requires a verified final heartbeat, complete mandatory criterion coverage, a promoted immutable archive, and a finalization receipt.",
            },
        ],
    )
    neural = router_document(
        artifact_id="KAIROS_NEURAL_CORTEX",
        title="KAIROS NEURAL CORTEX",
        workspace_id=workspace_id,
        state="active",
        capsule="Primary workspace router for current state, active goal, context frontier, and bounded KAIROS operating protocol.",
        answers=[
            {"intent": "orientation", "question": "Where does orientation begin in the KAIROS workspace?", "target": "s-orientation"},
            {"intent": "current_state", "question": "Which task and goal are currently active?", "target": "s-active-frontier"},
            {"intent": "dependency", "question": "In which order is context loaded?", "target": "s-read-order"},
        ],
        refs={
            "state": pointer("current.json", version="dynamic", relation="references", tags=("state", "authority"), source="system"),
            "contract": pointer("AGENTS.md", section="s-operating-loop", artifact_id="KAIROS_OPERATING_CONTRACT", relation="requires", tags=("contract", "protocol"), source="system"),
            "active": task_ref,
            "queue": pointer("ACTIVE.md", section="s-active-frontier", artifact_id="KAIROS_ACTIVE", version="dynamic", relation="references", tags=("active", "queue"), source="system"),
        },
        sections=[
            {
                "id": "s-orientation",
                "title": "WORKSPACE ORIENTATION",
                "capsule": "Read current state, the loop gate, and the active frontier before opening substantive documents.",
                "content": "Authority order: `current.json` → `_LOOP_GATE.md` → `ACTIVE.md` → active task criteria → evidence sections. Search results route context but never override explicit state authority.",
            },
            {
                "id": "s-active-frontier",
                "title": "ACTIVE GOAL AND FRONTIER",
                "capsule": f"{goal_id}, {milestone_id}, and {task_id} form the prepared active route.",
                "content": f"- Goal: `{goal_id}`\n- Milestone: `{milestone_id}`\n- Active task: {task_ref}",
            },
            {
                "id": "s-read-order",
                "title": "MINIMAL READ ORDER",
                "capsule": "Load only the state, primary target section, required prerequisites, and evidence chain selected by the query frame.",
                "content": "1. Read state and gate.\n2. Search by question.\n3. Open the returned `artifact#section`.\n4. Chase typed prerequisite or evidence relations.\n5. Use BREATHE when context pressure requires a checkpoint.",
            },
            {
                "id": "s-protocol",
                "title": "HEARTBEAT PROTOCOL",
                "capsule": "Every material action ends with documentation, promotion, verification, goal-coverage update, and a heartbeat receipt.",
                "content": "No lifecycle transition may claim completion while pending or failed promotion events remain. Finalization additionally requires all mandatory criteria to have evidence.",
            },
        ],
    )
    active = router_document(
        artifact_id="KAIROS_ACTIVE",
        title="KAIROS ACTIVE CONTEXT FRONTIER",
        workspace_id=workspace_id,
        state="active",
        capsule="Pointer-only active frontier with enough routing metadata to select the next task and criterion.",
        answers=[
            {"intent": "goal_gap", "question": "Which KAIROS task is active next?", "target": "s-active-frontier"},
            {"intent": "dependency", "question": "What is the immediate documented next step?", "target": "s-next-action"},
        ],
        refs={"task": task_ref},
        sections=[
            {
                "id": "s-active-frontier",
                "title": "ACTIVE FRONTIER",
                "capsule": f"{task_id} is the prepared active project task.",
                "content": f"- HIGH — `{task_id}` — Goal `{goal_id}` — {task_title or 'Project initiation'} — {task_ref}",
            },
            {
                "id": "s-next-action",
                "title": "NEXT ACTION",
                "capsule": "Run a reconciled heartbeat, inspect receipts, then execute the retrieval and fail-closed finalization tests.",
                "content": "The queue contains routing metadata only. Detailed evidence belongs in reports and validation sections.",
            },
        ],
    )
    closed = router_document(
        artifact_id="KAIROS_CLOSED",
        title="KAIROS CLOSED CONTEXT ROUTES",
        workspace_id=workspace_id,
        state="ready",
        capsule="Pointer-only closure router; no KAIROS tasks are closed in the freshly prepared workspace.",
        answers=[
            {"intent": "chronology", "question": "Which KAIROS tasks are already closed?", "target": "s-recent"},
            {"intent": "orientation", "question": "Where are older completed loops located?", "target": "s-history"},
        ],
        sections=[
            {
                "id": "s-recent",
                "title": "RECENT CLOSURES",
                "capsule": "No task has been closed in the prepared workspace.",
                "content": "No completed task pointers yet.",
            },
            {
                "id": "s-history",
                "title": "HISTORICAL ROUTING",
                "capsule": "Completed work is grouped by loop and milestone, while detailed history is retrieved from immutable archives and the KAIROS database.",
                "content": "This document must remain a bounded router rather than an ever-growing evidence dump.",
            },
        ],
    )
    gate = router_document(
        artifact_id="KAIROS_LOOP_GATE",
        title="KAIROS LOOP GATE",
        workspace_id=workspace_id,
        state="ready",
        document_type="gate",
        authority="state_authority",
        capsule="Workspace is prepared for ACTIVE work; finalization is blocked until required criteria have validated evidence.",
        answers=[
            {"intent": "current_state", "question": "Is the KAIROS loop ready for work or blocked?", "target": "s-verdict"},
            {"intent": "goal_gap", "question": "Why is KAIROS finalization still blocked?", "target": "s-blockers"},
            {"intent": "orientation", "question": "Which actions are allowed in the current state?", "target": "s-allowed-actions"},
        ],
        refs={"task": task_ref},
        sections=[
            {
                "id": "s-verdict",
                "title": "VERDICT",
                "capsule": "READY_FOR_WORK; NOT_READY_FOR_FINALIZATION.",
                "content": "Work heartbeats are allowed. A finalization attempt must remain fail-closed while evidence and promotion criteria are open.",
            },
            {
                "id": "s-blockers",
                "title": "FINALIZATION BLOCKERS",
                "capsule": f"{len(criterion_ids or [])} mandatory project criterion/criteria are open and lack validated completion evidence.",
                "content": "Open criteria: " + (", ".join(f"`{value}`" for value in (criterion_ids or [])) or "none declared"),
            },
            {
                "id": "s-allowed-actions",
                "title": "ALLOWED ACTIONS",
                "capsule": "Validate, search, document, promote, reconcile, checkpoint, and repair failed promotion events.",
                "content": "Starting unrelated work or asserting final completion is not allowed until the active task contract is satisfied.",
            },
        ],
    )
    session = router_document(
        artifact_id="KAIROS_SESSION",
        title="KAIROS SESSION CONTEXT PACK",
        workspace_id=workspace_id,
        state="ready",
        document_type="session",
        capsule="Minimal reload packet for the prepared goal, active task, current evidence boundary, and next heartbeat.",
        answers=[
            {"intent": "orientation", "question": "Which minimum context should a new KAIROS session load?", "target": "s-focus"},
            {"intent": "goal_gap", "question": "Which evidence is missing in the current session?", "target": "s-frontier"},
            {"intent": "experience", "question": "How is context compressed and reloaded under pressure?", "target": "s-breathe"},
        ],
        refs={"task": task_ref},
        sections=[
            {
                "id": "s-focus",
                "title": "SESSION FOCUS",
                "capsule": f"Focus on {task_id} and its declared project criteria.",
                "content": f"Open the task objective and acceptance criteria through {task_ref}." + (f" Objective: {task_objective}" if task_objective else ""),
            },
            {
                "id": "s-frontier",
                "title": "EVIDENCE FRONTIER",
                "capsule": "Project evidence is still required for the active task criteria; framework verification does not satisfy project outcomes.",
                "content": "Do not convert planned behavior into success claims until receipts and test outputs exist.",
            },
            {
                "id": "s-breathe",
                "title": "BREATHE CHECKPOINT",
                "capsule": "Retain goal, active criterion, primary evidence section, unresolved contradiction, and next query; release other prompt context.",
                "content": "The runtime state and retrieval trace preserve the frontier so a new model context can continue without rereading entire documents.",
            },
        ],
    )
    return {
        "AGENTS.md": agents,
        "NEURAL_CORTEX.md": neural,
        "ACTIVE.md": active,
        "CLOSED.md": closed,
        "_LOOP_GATE.md": gate,
        "_SESSION.md": session,
    }



def initialize_project_workspace(
    workspace: Path,
    *,
    workspace_id: str,
    goal: dict[str, Any],
    milestone: dict[str, Any],
    task: dict[str, Any],
    selected_criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a fresh KAIROS workspace directly under a validated project intent.

    This is the project-intake edge: project goals are authoritative before compiler
    discovery. Compiler evidence is attached to this scope later by ``kickstart init``;
    the framework's own bootstrap goal is never inserted into a customer project.
    """
    workspace = workspace.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise WorkspaceError(
            f"target workspace is not empty: {workspace}; project initialization requires a fresh target"
        )
    goal = json.loads(json.dumps(goal))
    task = json.loads(json.dumps(task))
    goal_id = str(goal["id"])
    milestone_id = str(milestone["id"])
    task_id = str(task["id"])
    criterion_ids = [str(value["id"]) for value in selected_criteria]
    if not criterion_ids:
        raise WorkspaceError("project initialization requires at least one active criterion")
    if not isinstance(goal.get("created_at"), str) or not goal["created_at"].strip():
        goal["created_at"] = utc_now()
    validate_goal(goal)

    workspace.mkdir(parents=True, exist_ok=True)
    for directory in [*DEFAULT_DOCUMENT_ROOTS, "goals", ".kairos/events", ".kairos/receipts", ".kairos/heartbeats"]:
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    config = {
        "schema": "kairos-workspace-config/v1",
        "workspace_id": workspace_id,
        "database": ".kairos/kairos.db",
        "document_roots": DEFAULT_DOCUMENT_ROOTS,
        "canonical_files": DEFAULT_CANONICAL_FILES,
        "reconcile_extensions": [".md"],
        "inspection_root": ".",
        "imported_workspace_ids": [],
        "imported_search_contract_policies": {},
        "fullscan_policy": "recovery_only",
        "governance_enforcement": "required",
        "created_at": utc_now(),
    }
    atomic_write_json(workspace / ".kairos" / "config.json", config)
    runtime_state = {
        "schema": RUNTIME_SCHEMA_VERSION,
        "sequence": 0,
        "lifecycle": "READY",
        "active_goal": goal_id,
        "active_milestone": milestone_id,
        "active_task": task_id,
        "active_criterion": criterion_ids[0],
        "mode": "work",
        "context_pressure": 0.0,
        "unresolved_branches": 1,
        "contradiction_count": 0,
        "evidence_gap_count": len(criterion_ids),
        "pending_promotions": 0,
        "failed_promotions": 0,
        "last_promotion_receipt": None,
        "last_heartbeat_receipt": None,
        "updated_at": utc_now(),
    }
    atomic_write_json(workspace / ".kairos" / "runtime_state.json", runtime_state)
    current = {
        "schema": "kairos-current/v1",
        "state_revision": 1,
        "lifecycle": "READY",
        "loop": 1,
        "active_goal": goal_id,
        "active_milestone": milestone_id,
        "active_task": task_id,
        "active_criterion": criterion_ids[0],
        "context_frontier": [f"{task_id}#s-objective", f"{task_id}#s-acceptance"],
        "pending_promotions": 0,
        "last_promotion_receipt": None,
        "next_required_read": "NEURAL_CORTEX.md#s-orientation",
        "updated_at": utc_now(),
    }
    atomic_write_json(workspace / "current.json", current)
    goal_path = workspace / "goals" / f"{goal_id}.json"
    atomic_write_json(goal_path, goal)
    task_path = workspace / "tasks" / f"task_{task_id}.md"
    atomic_write_text(
        task_path,
        task_document(
            task_id=task_id,
            title=str(task["title"]),
            objective=str(task["objective"]),
            workspace_id=workspace_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            criteria=selected_criteria,
            loop=1,
        ),
    )
    starter_architecture_path = workspace / "docs" / "KAIROS_STARTER_ARCHITECTURE.md"
    atomic_write_text(starter_architecture_path, _starter_architecture_document(workspace_id))
    canonical = _canonical_documents(
        workspace_id,
        task_id,
        goal_id=goal_id,
        milestone_id=milestone_id,
        criterion_ids=criterion_ids,
        task_title=str(task["title"]),
        task_objective=str(task["objective"]),
    )
    for relative, content in canonical.items():
        atomic_write_text(workspace / relative, content)

    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    goal_results = sync_goal_files(workspace, database)
    document_paths = [task_path, starter_architecture_path, *[workspace / name for name in DEFAULT_CANONICAL_FILES]]
    receipts = promote_many(document_paths, workspace, database, allow_dynamic=True)
    from .reconcile import reconcile_documents, update_manifest

    update_manifest(workspace, reconcile_documents(workspace), document_paths, updated_at=utc_now())
    update_goal_manifest(workspace, reconcile_goal_files(workspace), updated_at=utc_now())
    return {
        "workspace": str(workspace),
        "workspace_id": workspace_id,
        "database": str(database.path),
        "goals": goal_results,
        "active_goal": goal_id,
        "active_milestone": milestone_id,
        "active_task": task_id,
        "active_criterion": criterion_ids[0],
        "promoted": len(receipts),
        "receipts": [receipt["receipt_id"] for receipt in receipts],
    }

def initialize_workspace(workspace: Path, *, workspace_id: str = "KAIROS_WORKSPACE") -> dict[str, Any]:
    workspace = workspace.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise WorkspaceError(
            f"target workspace is not empty: {workspace}; KAIROS initialization requires a fresh target"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    for directory in [*DEFAULT_DOCUMENT_ROOTS, "goals", ".kairos/events", ".kairos/receipts", ".kairos/heartbeats"]:
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    config = {
        "schema": "kairos-workspace-config/v1",
        "workspace_id": workspace_id,
        "database": ".kairos/kairos.db",
        "document_roots": DEFAULT_DOCUMENT_ROOTS,
        "canonical_files": DEFAULT_CANONICAL_FILES,
        "reconcile_extensions": [".md"],
        "inspection_root": ".",
        # Origin workspaces this workspace accepts documents from. Empty by default: an
        # undeclared origin fails closed. A document from a declared origin keeps its own
        # goal, milestone and task identifiers, which are recorded rather than resolved.
        "imported_workspace_ids": [],
        "imported_search_contract_policies": {},
        "fullscan_policy": "recovery_only",
        "governance_enforcement": "required",
        "created_at": utc_now(),
    }
    atomic_write_json(workspace / ".kairos" / "config.json", config)
    runtime_state = {
        "schema": RUNTIME_SCHEMA_VERSION,
        "sequence": 0,
        "lifecycle": "READY",
        "active_goal": "GOAL_KAIROS_001",
        "active_milestone": "MILESTONE_KAIROS_01",
        "active_task": "TASK_0001",
        "active_criterion": "CRIT_KAIROS_001",
        "mode": "work",
        "context_pressure": 0.0,
        "unresolved_branches": 1,
        "contradiction_count": 0,
        "evidence_gap_count": 4,
        "pending_promotions": 0,
        "failed_promotions": 0,
        "last_promotion_receipt": None,
        "last_heartbeat_receipt": None,
        "updated_at": utc_now(),
    }
    atomic_write_json(workspace / ".kairos" / "runtime_state.json", runtime_state)
    current = {
        "schema": "kairos-current/v1",
        "state_revision": 1,
        "lifecycle": "READY",
        "loop": 1,
        "active_goal": "GOAL_KAIROS_001",
        "active_milestone": "MILESTONE_KAIROS_01",
        "active_task": "TASK_0001",
        "active_criterion": "CRIT_KAIROS_001",
        "context_frontier": ["TASK_0001#s-objective", "TASK_0001#s-acceptance"],
        "pending_promotions": 0,
        "last_promotion_receipt": None,
        "next_required_read": "NEURAL_CORTEX.md#s-orientation",
        "updated_at": utc_now(),
    }
    atomic_write_json(workspace / "current.json", current)
    goal = _seed_goal()
    atomic_write_json(workspace / "goals" / "GOAL_KAIROS_001.json", goal)
    criteria = goal["milestones"][0]["criteria"]
    task_path = workspace / "tasks" / "task_TASK_0001.md"
    atomic_write_text(
        task_path,
        task_document(
            task_id="TASK_0001",
            title="Validate and activate the KAIROS context harness",
            objective="Validate the prepared heartbeat, same-loop promotion, section-level causal retrieval, and fail-closed finalization flow before connecting a live project archive.",
            workspace_id=workspace_id,
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            criteria=criteria,
            loop=1,
        ),
    )
    report_path = workspace / "reports" / "report_TASK_0001_L001_V01.md"
    atomic_write_text(
        report_path,
        report_document(
            report_id="REPORT_TASK_0001_L001_V01",
            task_id="TASK_0001",
            title="Prepared workspace initialization",
            workspace_id=workspace_id,
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            criteria=[],
            task_path="tasks/task_TASK_0001.md",
            loop=1,
        ),
    )
    bug_path = workspace / "bugs" / "BUG_0001_L001_V01.md"
    atomic_write_text(
        bug_path,
        bug_document(
            bug_id="BUG_0001_L001_V01",
            task_id="TASK_0001",
            workspace_id=workspace_id,
            task_path="tasks/task_TASK_0001.md",
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            loop=1,
        ),
    )
    code_path = workspace / "code" / "CODE_0001_L001_V01.md"
    atomic_write_text(
        code_path,
        code_document(
            code_id="CODE_0001_L001_V01",
            task_id="TASK_0001",
            workspace_id=workspace_id,
            task_path="tasks/task_TASK_0001.md",
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            criteria=["CRIT_KAIROS_001"],
            loop=1,
        ),
    )
    starter_architecture_path = workspace / "docs" / "KAIROS_STARTER_ARCHITECTURE.md"
    atomic_write_text(
        starter_architecture_path,
        _starter_architecture_document(workspace_id),
    )
    for relative, content in _canonical_documents(workspace_id, "TASK_0001").items():
        atomic_write_text(workspace / relative, content)
    database = KnowledgeDatabase(database_path(workspace))
    database.initialize()
    goal_results = sync_goal_files(workspace, database)
    document_paths = [
        task_path,
        report_path,
        bug_path,
        code_path,
        starter_architecture_path,
        *[workspace / name for name in DEFAULT_CANONICAL_FILES],
    ]
    receipts = promote_many(document_paths, workspace, database, allow_dynamic=True)
    from .reconcile import reconcile_documents, update_manifest

    update_manifest(
        workspace,
        reconcile_documents(workspace),
        document_paths,
        updated_at=utc_now(),
    )
    update_goal_manifest(workspace, reconcile_goal_files(workspace), updated_at=utc_now())
    return {
        "workspace": str(workspace),
        "workspace_id": workspace_id,
        "database": str(database.path),
        "goals": goal_results,
        "promoted": len(receipts),
        "receipts": [receipt["receipt_id"] for receipt in receipts],
    }
