from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import (
    DYNAMIC_CANONICAL_FILES,
    MAX_DYNAMIC_CRITERION_POINTERS,
    MAX_DYNAMIC_TASK_POINTERS,
)
from .database import KnowledgeDatabase
from .frontmatter import split_frontmatter
from .goals import unmet_required_criteria
from .readiness import evaluate_finalization_readiness
from .templates import pointer, router_document
from .util import atomic_write_json, atomic_write_text, json_dumps, read_json, sha256_text, utc_now
from .workspace import load_config


def _next_revision(database: KnowledgeDatabase, artifact_id: str) -> int:
    connection = database.connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT revision FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        return int(row["revision"]) + 1 if row else 1
    finally:
        connection.close()


def _normalized_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n"))


def _semantic_signature(text: str) -> str:
    parsed = split_frontmatter(text)
    metadata = dict(parsed.metadata)
    metadata.pop("revision", None)
    metadata.pop("updated_at", None)
    return sha256_text(json_dumps(metadata, pretty=False) + "\n" + parsed.body)


def _task_pointer(row, relation: str = "references") -> str:
    return pointer(
        row["path"],
        section="s-objective",
        artifact_id=row["artifact_id"],
        version="dynamic",
        relation=relation,
        tags=("task", str(row["state"])),
        source="canonical-projection",
    )


def refresh_dynamic_canonicals(
    workspace: Path,
    database: KnowledgeDatabase,
    runtime_state: dict[str, Any],
    *,
    source_check: dict[str, Any] | None = None,
) -> tuple[list[Path], str]:
    connection = database.connect(read_only=True)
    try:
        active_count = int(
            connection.execute(
                """
                SELECT count(*) FROM artifacts
                WHERE document_type='task'
                  AND state NOT IN ('completed','success','superseded','finalized')
                """
            ).fetchone()[0]
        )
        closed_count = int(
            connection.execute(
                """
                SELECT count(*) FROM artifacts
                WHERE document_type='task'
                  AND state IN ('completed','success','superseded','finalized')
                """
            ).fetchone()[0]
        )
        active = list(connection.execute(
            """
            SELECT artifact_id,path,state,capsule,goal_id,milestone_id,updated_at
            FROM artifacts
            WHERE document_type='task'
              AND state NOT IN ('completed','success','superseded','finalized')
            ORDER BY updated_at DESC,artifact_id
            LIMIT ?
            """
            ,
            (MAX_DYNAMIC_TASK_POINTERS,),
        ).fetchall())
        closed = list(connection.execute(
            """
            SELECT artifact_id,path,state,capsule,goal_id,milestone_id,updated_at
            FROM artifacts
            WHERE document_type='task'
              AND state IN ('completed','success','superseded','finalized')
            ORDER BY updated_at DESC,artifact_id
            LIMIT ?
            """,
            (MAX_DYNAMIC_TASK_POINTERS,),
        ).fetchall())
        active_task_id = runtime_state.get("active_task")
        if active_task_id and all(row["artifact_id"] != active_task_id for row in active):
            active_task_row = connection.execute(
                """
                SELECT artifact_id,path,state,capsule,goal_id,milestone_id,updated_at
                FROM artifacts
                WHERE document_type='task' AND artifact_id=?
                  AND state NOT IN ('completed','success','superseded','finalized')
                """,
                (active_task_id,),
            ).fetchone()
            if active_task_row:
                active = [active_task_row, *active[: MAX_DYNAMIC_TASK_POINTERS - 1]]
        canonical_rows = {
            str(row["path"]): row
            for row in connection.execute(
                """
                SELECT path,content_sha256,revision FROM artifacts
                WHERE path IN ('NEURAL_CORTEX.md','ACTIVE.md','CLOSED.md','_LOOP_GATE.md','_SESSION.md')
                """
            ).fetchall()
        }
    finally:
        connection.close()
    readiness = evaluate_finalization_readiness(database, source_check=source_check)
    # Advisory routing only: Workshop admission independently checks the actual
    # transaction directories. This derived marker is never mutation authority.
    tx_frontier = read_json(workspace / ".kairos" / "tx_recycling_frontier.json", default={}) or {}
    if not isinstance(tx_frontier, dict) or tx_frontier.get("schema") != "werkfaden-tx-recycling-frontier/v1":
        tx_frontier = {}
    recycling_required = tx_frontier.get("required") is True
    recycling_count = tx_frontier.get("materialized_count", 0)
    recycling_target = tx_frontier.get("target_max", 8)
    recycling_candidates = tx_frontier.get("candidates", [])
    if not isinstance(recycling_candidates, list):
        recycling_candidates = []
    recycling_instruction = (
        "Workshop checkout is blocked. Complete a prior transaction recycling "
        "review with file-level retain/reject decisions and cited evidence, "
        "verify its SQL-projected TRANSACTION report, then use tx-recycle-drain "
        "until the pool is below the threshold. Technical recovery remains allowed."
    ) if recycling_required else "Workshop recycling admission is not currently required."

    unmet = readiness["unmet_criteria"]
    unmet_display = unmet[:MAX_DYNAMIC_CRITERION_POINTERS]
    pending = int(readiness["pending"])
    failed = int(readiness["failed"])
    projection = {
        "projection_version": 4,
        "active_count": active_count,
        "closed_count": closed_count,
        "active": [dict(row) for row in active],
        "closed": [dict(row) for row in closed],
        "runtime": {
            key: runtime_state.get(key)
            for key in (
                "lifecycle",
                "active_goal",
                "active_milestone",
                "active_task",
                "active_criterion",
            )
        },
        "unmet_count": len(unmet),
        "unmet": [row["criterion_id"] for row in unmet_display],
        "pending": pending,
        "failed": failed,
        "tx_recycling": {"required": recycling_required, "materialized_count": recycling_count,
                         "target_max": recycling_target, "candidates": recycling_candidates[:16]},
        "finalization_ready": readiness["ready"],
        "finalization_blocker_types": [row["type"] for row in readiness["blockers"]],
    }
    signature = sha256_text(json_dumps(projection, pretty=False))
    projection_path = workspace / ".kairos" / "canonical_projection.json"
    previous = read_json(projection_path, default={}) or {}

    workspace_id = load_config(workspace)["workspace_id"]
    active_refs = {f"task_{index + 1}": _task_pointer(row) for index, row in enumerate(active)}
    closed_refs = {f"task_{index + 1}": _task_pointer(row) for index, row in enumerate(closed)}
    active_lines = [
        f"- `{row['state'].upper()}` — `{row['artifact_id']}` — {row['capsule']} — {_task_pointer(row)}"
        for row in active
    ] or ["- No active task contract is promoted."]
    if active_count > len(active):
        active_lines.append(
            f"- {active_count - len(active)} additional active task(s) are omitted; query the metadata base by state and goal."
        )
    closed_lines = [
        f"- `{row['state'].upper()}` — `{row['artifact_id']}` — {row['capsule']} — {_task_pointer(row)}"
        for row in closed
    ] or ["- No closed task contract is promoted."]
    if closed_count > len(closed):
        closed_lines.append(
            f"- {closed_count - len(closed)} additional closure(s) are omitted; use a chronology query against the metadata base."
        )
    active_row = next(
        (row for row in active if row["artifact_id"] == runtime_state.get("active_task")),
        active[0] if active else None,
    )
    active_ref = _task_pointer(active_row) if active_row else "No active task pointer."
    active_identity = (
        f"`{active_row['artifact_id']}#s-objective`"
        if active_row
        else "No active task is promoted."
    )

    neural_path = workspace / "NEURAL_CORTEX.md"
    neural_refs = {
        "state": pointer(
            "current.json",
            version="dynamic",
            relation="references",
            tags=("state", "authority"),
            source="system",
        ),
        "contract": pointer(
            "AGENTS.md",
            section="s-operating-loop",
            artifact_id="KAIROS_OPERATING_CONTRACT",
            version="dynamic",
            relation="requires",
            tags=("contract", "protocol"),
            source="system",
        ),
        "queue": pointer(
            "ACTIVE.md",
            section="s-active-frontier",
            artifact_id="KAIROS_ACTIVE",
            version="dynamic",
            relation="references",
            tags=("active", "queue"),
            source="system",
        ),
    }
    if active_row:
        neural_refs["active"] = active_ref
    active_route = (
        f"{active_row['goal_id']} / {active_row['milestone_id']} / {active_row['artifact_id']}"
        if active_row
        else "No active task is promoted. Create the next goal-linked task before material work."
    )
    neural = router_document(
        artifact_id="KAIROS_NEURAL_CORTEX",
        title="KAIROS NEURAL CORTEX",
        workspace_id=workspace_id,
        state="active" if active_row else "ready",
        revision=_next_revision(database, "KAIROS_NEURAL_CORTEX"),
        capsule="Dynamic workspace router for current state, active goal, context frontier, and the bounded KAIROS operating protocol.",
        answers=[
            {"intent": "orientation", "question": "Where does orientation begin in the KAIROS workspace?", "target": "s-orientation"},
            {"intent": "current_state", "question": "Which task and goal are currently active?", "target": "s-active-frontier"},
            {"intent": "dependency", "question": "In which order is context loaded?", "target": "s-read-order"},
        ],
        refs=neural_refs,
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
                "capsule": active_route,
                "content": f"Active route: {active_route}\n\nPrimary task pointer: {active_ref}",
            },
            {
                "id": "s-read-order",
                "title": "MINIMAL READ ORDER",
                "capsule": "Load only state, the primary target section, required prerequisites, and the evidence chain selected by the query frame.",
                "content": "1. Read state and gate.\n2. Search by question.\n3. Open the returned `artifact#section`.\n4. Chase typed prerequisite or evidence relations.\n5. Use BREATHE when context pressure requires a checkpoint.",
            },
            {
                "id": "s-protocol",
                "title": "HEARTBEAT PROTOCOL",
                "capsule": "Every material action ends with documentation, promotion, verification, goal-coverage update, and a heartbeat receipt.",
                "content": "No lifecycle transition may claim completion while pending or failed promotion events remain. Finalization additionally requires all mandatory criteria to have evidence. " + recycling_instruction,
            },
        ],
    )

    active_path = workspace / "ACTIVE.md"
    active_document = router_document(
        artifact_id="KAIROS_ACTIVE",
        title="KAIROS ACTIVE CONTEXT FRONTIER",
        workspace_id=workspace_id,
        state="active" if active_count else "ready",
        revision=_next_revision(database, "KAIROS_ACTIVE"),
        capsule=f"Bounded pointer frontier for {active_count} active task contract(s), generated from promoted task state.",
        answers=[
            {"intent": "goal_gap", "question": "Which KAIROS task is active next?", "target": "s-active-frontier"},
            {"intent": "dependency", "question": "What is the immediate documented next step?", "target": "s-next-action"},
        ],
        refs=active_refs,
        sections=[
            {
                "id": "s-active-frontier",
                "title": "ACTIVE FRONTIER",
                "capsule": f"{active_count} active task contract(s) exist; at most {MAX_DYNAMIC_TASK_POINTERS} are materialized here.",
                "content": "\n".join(active_lines),
            },
            {
                "id": "s-next-action",
                "title": "NEXT ACTION",
                "capsule": "Open the active task objective and its first uncovered criterion before selecting an acquisition mode.",
                "content": (
                    (recycling_instruction + " Selected historical candidates: " +
                     ", ".join(str(value) for value in recycling_candidates[:8]))
                    if recycling_required else (
                        f"Active task route: {active_identity}. Resolve it through the "
                        "typed task reference already declared in this router's context header."
                    )
                ),
            },
        ],
    )

    closed_path = workspace / "CLOSED.md"
    closed_document = router_document(
        artifact_id="KAIROS_CLOSED",
        title="KAIROS CLOSED CONTEXT ROUTES",
        workspace_id=workspace_id,
        state="ready",
        revision=_next_revision(database, "KAIROS_CLOSED"),
        capsule=f"Bounded pointer closure router for {closed_count} closed task contract(s).",
        answers=[
            {"intent": "chronology", "question": "Which KAIROS tasks are already closed?", "target": "s-recent"},
            {"intent": "orientation", "question": "Where are older completed loops located?", "target": "s-history"},
        ],
        refs=closed_refs,
        sections=[
            {
                "id": "s-recent",
                "title": "RECENT CLOSURES",
                "capsule": f"{closed_count} closed task contract(s) exist; at most {MAX_DYNAMIC_TASK_POINTERS} are materialized here.",
                "content": "\n".join(closed_lines),
            },
            {
                "id": "s-history",
                "title": "HISTORICAL ROUTING",
                "capsule": "Older detail remains in immutable archives and section records rather than this router.",
                "content": "Use chronology queries and typed supersession relations instead of reading a growing linear history file.",
            },
        ],
    )

    finalization_ready = bool(readiness["ready"])
    blocker_lines: list[str] = []
    for blocker in readiness["blockers"]:
        blocker_type = blocker["type"]
        if blocker_type == "source_freshness":
            blocker_lines.append(
                f"- `source_freshness` — checked={blocker['checked']}; "
                f"changed={len(blocker['changed'])}; missing={len(blocker['missing'])}; "
                f"failures={len(blocker['failures'])}."
            )
        elif blocker_type == "promotion_state":
            blocker_lines.append(
                f"- `promotion_state` — pending={blocker['pending']}; failed={blocker['failed']}."
            )
        elif blocker_type == "goal_coverage":
            blocker_lines.append(
                f"- `goal_coverage` — {len(unmet)} required criterion or criteria lack complete evidence-class coverage."
            )
            blocker_lines.extend(
                f"  - `goal_coverage:{row['criterion_id']}` — {row['description']}"
                for row in unmet_display
            )
            if len(unmet) > len(unmet_display):
                blocker_lines.append(
                    f"- {len(unmet) - len(unmet_display)} additional uncovered criterion or criteria are omitted; query goal coverage."
                )
        elif blocker_type == "task_closure":
            blocker_lines.append(
                f"- `task_closure` — {len(blocker['tasks'])} task contract(s) remain open."
            )
        elif blocker_type == "goal_closure":
            blocker_lines.append(
                f"- `goal_closure` — {len(blocker['goals'])} goal contract(s) remain open."
            )
        elif blocker_type == "governance":
            blocker_lines.append(
                f"- `governance` — enforcement={blocker['enforcement_mode']}; "
                f"quarantine={blocker['open_quarantine']}; "
                f"unfinished_actions={len(blocker['unfinished_actions'])}."
            )
    gate_path = workspace / "_LOOP_GATE.md"
    gate = router_document(
        artifact_id="KAIROS_LOOP_GATE",
        title="KAIROS LOOP GATE",
        workspace_id=workspace_id,
        state="blocked" if runtime_state.get("lifecycle") == "FINALIZING_METADATA_BLOCKED" else "ready",
        document_type="gate",
        authority="state_authority",
        revision=_next_revision(database, "KAIROS_LOOP_GATE"),
        capsule=(
            "Loop finalization is ready, but new Workshop checkout is blocked by TX recycling."
            if finalization_ready and recycling_required
            else "Task/loop finalization gates are clear; Workshop checkout must still pass live admission."
            if finalization_ready
            else f"Task/loop finalization is blocked by {len(readiness['blockers'])} policy gate(s); TX recycling is separate."
        ),
        answers=[
            {"intent": "current_state", "question": "Is the KAIROS loop ready or blocked?", "target": "s-verdict"},
            {"intent": "goal_gap", "question": "Why is KAIROS finalization still blocked?", "target": "s-blockers"},
            {"intent": "orientation", "question": "Which actions are allowed in the current state?", "target": "s-allowed-actions"},
            {"intent": "current_state", "question": "Is governed-action enforcement active and is quarantine clear?", "target": "s-governance"},
            {"intent": "tx_recycling", "question": "Is Workshop transaction recycling required before another checkout?", "target": "s-tx-recycling"},
        ],
        refs={"task": active_ref} if active_row else {},
        sections=[
            {
                "id": "s-verdict",
                "title": "VERDICT",
                "capsule": "READY_FOR_FINALIZATION." if finalization_ready else "READY_FOR_WORK; NOT_READY_FOR_FINALIZATION.",
                "content": (
                    f"Lifecycle: `{runtime_state.get('lifecycle')}`. Shared readiness: "
                    f"`{'READY' if finalization_ready else 'BLOCKED'}`. Pending promotions: `{pending}`. "
                    f"Failed promotions: `{failed}`. "
                    f"Workshop checkout: `{'RECYCLING_REQUIRED' if recycling_required else 'NOT_BLOCKED_BY_TX_COUNT_CACHE'}` "
                    "(actual admission always rechecks the live Workshop pool)."
                ),
            },
            {
                "id": "s-blockers",
                "title": "FINALIZATION BLOCKERS",
                "capsule": "No finalization blockers remain." if finalization_ready else f"Shared readiness reports {len(readiness['blockers'])} blocker class(es).",
                "content": "\n".join(blocker_lines) or "No blockers.",
            },
            {
                "id": "s-allowed-actions",
                "title": "ALLOWED ACTIONS",
                "capsule": "Validate, search, document, promote, repair, checkpoint, or finalize when the blockers section is empty.",
                "content": ("A final completion claim is forbidden while any blocker remains. "
                            + ("New Workshop checkout is blocked; only TX recycling and technical recovery may progress."
                               if recycling_required else
                               "No TX recycling blocker is cached; real checkout still requires an independent Workshop admission check.")),
            },
            {
                "id": "s-tx-recycling",
                "title": "TRANSACTION RECYCLING ADMISSION",
                "capsule": (
                    f"Workshop has {recycling_count} materialized transaction(s); "
                    f"recycling is {'required' if recycling_required else 'not required'}."
                ),
                "content": (
                    f"Materialized TX: {recycling_count}. Must reach at most "
                    f"{recycling_target} before a new checkout. " + recycling_instruction
                    + "\nSuggested TX: " + (", ".join(str(v) for v in recycling_candidates[:12]) or "none")
                    + "\nThis router reports a cached snapshot. The Workshop Engine"
                      " independently rechecks its actual directories and receipts."
                ),
            },
            {
                "id": "s-governance",
                "title": "GOVERNED ACTION STATUS",
                "capsule": (
                    f"Enforcement is {readiness['governance']['enforcement_mode']}; "
                    f"{readiness['governance']['open_quarantine']} quarantined change(s) and "
                    f"{len(readiness['governance']['unfinished_actions'])} unfinished action(s)."
                ),
                "content": (
                    "Every material command requires active goal, milestone, task, and criterion "
                    "scope plus a one-use phase permit. Unattributed source drift cannot be promoted."
                ),
            },
        ],
    )

    session_path = workspace / "_SESSION.md"
    session = router_document(
        artifact_id="KAIROS_SESSION",
        title="KAIROS SESSION CONTEXT PACK",
        workspace_id=workspace_id,
        state="ready",
        document_type="session",
        revision=_next_revision(database, "KAIROS_SESSION"),
        capsule=f"Minimal reload packet for {runtime_state.get('active_goal')}, {runtime_state.get('active_milestone')}, and {runtime_state.get('active_task')}.",
        answers=[
            {"intent": "orientation", "question": "Which minimum context should a new KAIROS session load?", "target": "s-focus"},
            {"intent": "goal_gap", "question": "Which evidence is missing in the current session?", "target": "s-frontier"},
            {"intent": "experience", "question": "How is context compressed and reloaded under pressure?", "target": "s-breathe"},
        ],
        refs={"task": active_ref} if active_row else {},
        sections=[
            {
                "id": "s-focus",
                "title": "SESSION FOCUS",
                "capsule": f"Active route: {runtime_state.get('active_goal')} / {runtime_state.get('active_milestone')} / {runtime_state.get('active_task')} / {runtime_state.get('active_criterion')}.",
                "content": f"Primary task: {active_ref}",
            },
            {
                "id": "s-frontier",
                "title": "EVIDENCE FRONTIER",
                "capsule": f"{len(unmet)} mandatory criterion or criteria still lack validated success evidence.",
                "content": (
                    "\n".join(f"- `{row['criterion_id']}` — {row['description']}" for row in unmet_display)
                    + (
                        f"\n- {len(unmet) - len(unmet_display)} additional criterion or criteria are omitted; query goal coverage."
                        if len(unmet) > len(unmet_display)
                        else ""
                    )
                ) or "All mandatory criteria are evidenced.",
            },
            {
                "id": "s-breathe",
                "title": "BREATHE CHECKPOINT",
                "capsule": "Retain the goal, active criterion, primary evidence section, unresolved contradiction, and next query; release other prompt context.",
                "content": "Reload only this session packet and the retained section anchors after context compaction.",
            },
        ],
    )

    generated = [
        (neural_path, neural),
        (active_path, active_document),
        (closed_path, closed_document),
        (gate_path, gate),
        (session_path, session),
    ]
    # The projection signature is only a cache key.  A failed heartbeat can
    # publish a transient canonical document and leave that document plus its
    # database row byte-identical to one another while the cache still names a
    # different readiness snapshot.  Compare freshly rendered semantic bytes
    # before skipping the refresh; volatile revision and timestamp fields are
    # intentionally excluded by _semantic_signature.
    canonical_drift = any(
        not path.is_file()
        or path.name not in canonical_rows
        or _normalized_hash(path) != canonical_rows[path.name]["content_sha256"]
        for path, _ in generated
    )
    semantic_drift = any(
        not path.is_file()
        or _semantic_signature(path.read_text(encoding="utf-8"))
        != _semantic_signature(content)
        for path, content in generated
    )
    if previous.get("signature") == signature and not canonical_drift and not semantic_drift:
        return [], signature

    changed: list[Path] = []
    for path, content in generated:
        relative = path.name
        current_row = canonical_rows.get(relative)
        current_is_promoted = (
            path.is_file()
            and current_row is not None
            and _normalized_hash(path) == current_row["content_sha256"]
        )
        semantically_equal = False
        if path.is_file():
            try:
                semantically_equal = _semantic_signature(path.read_text(encoding="utf-8")) == _semantic_signature(content)
            except Exception:
                semantically_equal = False
        if current_is_promoted and semantically_equal:
            continue
        atomic_write_text(path, content)
        changed.append(path)
    return changed, signature


def commit_canonical_projection(workspace: Path, signature: str) -> None:
    path = workspace / ".kairos" / "canonical_projection.json"
    previous = read_json(path, default={}) or {}
    if previous.get("signature") == signature:
        return
    atomic_write_json(
        path,
        {"schema": "kairos-canonical-projection/v1", "signature": signature, "updated_at": utc_now()},
    )
