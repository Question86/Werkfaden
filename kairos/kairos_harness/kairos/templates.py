from __future__ import annotations

from typing import Any

from .constants import MAX_INDEX_CAPSULE_CHARS
from .frontmatter import render_frontmatter
from .util import utc_now


def pointer(
    path: str,
    *,
    section: str | None = None,
    artifact_id: str | None = None,
    version: str = "1",
    relation: str = "references",
    tags: tuple[str, ...] = ("context",),
    source: str = "declared",
) -> str:
    locator = path.replace("\\", "/") + (f"#{section}" if section else "")
    fields = [f"v:{version}", f"rel:{relation}", f"tags:{','.join(sorted(tags))}", f"src:{source}"]
    if artifact_id:
        fields.insert(0, f"id:{artifact_id}")
    return f"[ref:{locator}|{'|'.join(fields)}]"


def render_document(metadata: dict[str, Any], title: str, sections: list[dict[str, str]]) -> str:
    index_lines = ["## CONTEXT INDEX", ""]
    for section in sections:
        summary = " ".join(section["capsule"].split())
        if len(summary) > MAX_INDEX_CAPSULE_CHARS:
            summary = summary[:MAX_INDEX_CAPSULE_CHARS].rstrip()
        index_lines.append(f"- [`{section['id']}`](#{section['id']}) — {summary}")
    body = [f"# {title}", "", *index_lines, ""]
    for section in sections:
        body.extend(
            [
                f'<a id="{section["id"]}"></a>',
                f"## {section['title']}",
                "",
                f"> Capsule: {section['capsule']}",
                "",
                section["content"].strip(),
                "",
            ]
        )
    return render_frontmatter(metadata) + "\n".join(body).rstrip() + "\n"


def task_document(
    *,
    task_id: str,
    title: str,
    objective: str,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    criteria: list[dict[str, str]],
    loop: int,
    revision: int = 1,
    state: str = "active",
    updated_at: str | None = None,
) -> str:
    criteria_ids = [criterion["id"] for criterion in criteria]
    acceptance = "\n".join(
        f'<a id="{criterion["id"].casefold().replace("_", "-")}"></a>\n'
        f'- [ ] **{criterion["id"]}:** {criterion["description"]}\n'
        f'  - Required evidence: {criterion["evidence_required"]}'
        for criterion in criteria
    )
    metadata = {
        "schema": "kairos-context/v1",
        "id": task_id,
        "type": "task",
        "revision": revision,
        "state": state,
        "authority": "task_contract",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": updated_at or utc_now(),
        "capsule": objective[:470],
        "claim_boundary": "This task defines required work and evidence; it does not prove implementation or acceptance.",
        "entities": [task_id, goal_id, milestone_id, "KAIROS"],
        "facets": ["goal-decomposition", "task-contract", "context-harness"],
        "criteria": criteria_ids,
        "does_not_answer": ["validated outcome", "finalization approval"],
        "answers": [
            {"intent": "goal_gap", "question": f"What must {task_id} achieve?", "target": "s-objective"},
            {"intent": "dependency", "question": f"Which prerequisites and dependencies does {task_id} have?", "target": "s-dependencies"},
            {"intent": "validation", "question": f"Which acceptance criteria and evidence does {task_id} require?", "target": "s-acceptance"},
        ],
        "refs": {
            "orientation": pointer(
                "NEURAL_CORTEX.md",
                section="s-active-frontier",
                artifact_id="KAIROS_NEURAL_CORTEX",
                version="dynamic",
                relation="belongs_to",
                tags=("orientation", "router"),
            )
        },
        "search_contract": [
            {
                "query": f"Which acceptance criteria must {task_id} satisfy?",
                "expected": f"{task_id}#s-acceptance",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {
            "id": "s-objective",
            "title": "OBJECTIVE",
            "capsule": objective,
            "content": objective,
        },
        {
            "id": "s-context",
            "title": "CONTEXT",
            "capsule": "The task is positioned inside the active goal and milestone route.",
            "content": f"- Goal: `{goal_id}`\n- Milestone: `{milestone_id}`\n- Loop: `{loop}`",
        },
        {
            "id": "s-dependencies",
            "title": "DEPENDENCIES",
            "capsule": "Work requires a valid goal graph, writable KAIROS runtime state, and resolvable canonical pointers.",
            "content": "- Goal and criteria are present in `goals/`.\n- The KAIROS database schema is initialized.\n- All declared document references resolve inside the workspace.",
        },
        {
            "id": "s-acceptance",
            "title": "ACCEPTANCE CRITERIA",
            "capsule": "Completion requires explicit evidence for every listed criterion.",
            "content": acceptance,
        },
        {
            "id": "s-next",
            "title": "NEXT ACTION",
            "capsule": "Execute one bounded heartbeat action, document it, and promote the resulting artifact in the same heartbeat.",
            "content": "Select `work`, `depth`, `breadth`, `breathe`, or `verify`; record the rationale and required evidence before acting.",
        },
    ]
    return render_document(metadata, f"{task_id}: {title}", sections)


def report_document(
    *,
    report_id: str,
    task_id: str,
    title: str,
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    criteria: list[str],
    task_path: str,
    loop: int,
    revision: int = 1,
    state: str = "partial",
    outcome: str = "KAIROS workspace initialized; end-to-end validation remains pending.",
    work_performed: str = "- Context-header document grammar\n- Section-level FTS and typed relation graph\n- Goal and criterion coverage tables\n- Promotion events and receipts\n- Heartbeat and search command surfaces",
    evidence: str | None = None,
    limitations: str = "- Run the complete test suite.\n- Execute the prepared workspace heartbeat.\n- Verify query contracts and goal coverage.\n- Keep all non-workspace sources outside the active authority boundary.",
    next_action: str = "Use `python -m kairos heartbeat --workspace <path>` and inspect its receipt before any finalization attempt.",
) -> str:
    if state == "success" and (not evidence or not evidence.strip()):
        raise ValueError("a success report requires explicit validation evidence")
    evidence_text = evidence or "Current state: initialization evidence only. Unit, integration, retrieval, and crash-boundary checks must be recorded before this report can become `success`."
    metadata = {
        "schema": "kairos-context/v1",
        "id": report_id,
        "type": "report",
        "revision": revision,
        "state": state,
        "authority": "execution_evidence",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{report_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": utc_now(),
        "capsule": outcome,
        "claim_boundary": "This report describes the initialized harness; only explicitly listed checks count as validation evidence.",
        "entities": [report_id, task_id, "KAIROS", "SQLite FTS5", "promotion receipt"],
        "facets": ["heartbeat", "incremental-promotion", "section-search", "validation"],
        "criteria": criteria,
        "does_not_answer": ["formal production release", "authority outside this workspace"],
        "answers": [
            {"intent": "evidence", "question": f"What outcome does {report_id} record?", "target": "s-outcome"},
            {"intent": "validation", "question": f"Which validation checks does {report_id} record?", "target": "s-evidence"},
            {"intent": "goal_gap", "question": f"Which work and evidence remain open for {report_id}?", "target": "s-limitations"},
        ],
        "refs": {
            "parent": pointer(
                task_path,
                section="s-objective",
                artifact_id=task_id,
                version="dynamic",
                relation="documents",
                tags=("task", "objective"),
            )
        },
        "search_contract": [
            {
                "query": f"Which work and evidence remain open for {report_id}?",
                "expected": f"{report_id}#s-limitations",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {"id": "s-outcome", "title": "OUTCOME", "capsule": outcome, "content": outcome},
        {
            "id": "s-work",
            "title": "WORK PERFORMED",
            "capsule": work_performed.replace("\n", " ")[:470],
            "content": work_performed,
        },
        {
            "id": "s-evidence",
            "title": "VALIDATION EVIDENCE",
            "capsule": evidence_text.replace("\n", " ")[:470],
            "content": evidence_text,
        },
        {
            "id": "s-limitations",
            "title": "LIMITATIONS AND OPEN WORK",
            "capsule": limitations.replace("\n", " ")[:470],
            "content": limitations,
        },
        {
            "id": "s-next",
            "title": "NEXT ACTION",
            "capsule": next_action.replace("\n", " ")[:470],
            "content": next_action,
        },
    ]
    return render_document(metadata, f"REPORT: {task_id} — {title}", sections)


def bug_document(
    *,
    bug_id: str,
    task_id: str,
    workspace_id: str,
    task_path: str,
    goal_id: str,
    milestone_id: str,
    title: str = "Out-of-band metadata freshness gap",
    observation: str = "A valid source document can change outside a material heartbeat and temporarily diverge from its derived database projection.",
    impact: str = "Context acquisition may use stale metadata, duplicate research, or miss a causal link that was documented only moments earlier.",
    root_cause: str = "Out-of-band writes bypass the mandatory document-promote-verify heartbeat transaction.",
    resolution: str = "Reconcile bounded source signatures before every supported metadata access and promote valid drift through a serialized verification heartbeat.",
    regression: str = "Prove implicit-access promotion, idempotent replay, malformed-edit fail-closed behavior, and a no-change access that creates no heartbeat.",
    criteria: list[str] | None = None,
    loop: int,
    revision: int = 1,
    state: str = "active",
) -> str:
    bug_path = f"bugs/{bug_id}.md"
    criteria = criteria or []
    metadata = {
        "schema": "kairos-context/v1",
        "id": bug_id,
        "type": "bug",
        "revision": revision,
        "state": state,
        "authority": "diagnostic_record",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{bug_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": utc_now(),
        "capsule": observation.replace("\n", " ")[:470],
        "claim_boundary": "This diagnostic explains the freshness risk and required invariant; it does not prove that any particular workspace is stale.",
        "entities": [bug_id, task_id, title],
        "facets": ["root-cause", "impact", "resolution", "regression"],
        "criteria": criteria,
        "does_not_answer": ["current workspace freshness without a live check", "production release acceptance"],
        "answers": [
            {"intent": "root_cause", "question": f"What is the root cause recorded by {bug_id}?", "target": "s-root-cause"},
            {"intent": "resolution", "question": f"What resolution does {bug_id} specify?", "target": "s-resolution"},
            {"intent": "validation", "question": f"Which regression evidence validates {bug_id}?", "target": "s-regression"},
        ],
        "refs": {
            "task": pointer(
                task_path,
                section="s-acceptance",
                artifact_id=task_id,
                version="dynamic",
                relation="informs",
                tags=("task", "criteria"),
            )
        },
        "search_contract": [
            {
                "query": f"What is the root cause recorded by {bug_id}?",
                "expected": f"{bug_id}#s-root-cause",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {
            "id": "s-observation",
            "title": "OBSERVATION",
            "capsule": observation.replace("\n", " ")[:470],
            "content": observation,
        },
        {
            "id": "s-impact",
            "title": "IMPACT",
            "capsule": impact.replace("\n", " ")[:470],
            "content": impact,
        },
        {
            "id": "s-root-cause",
            "title": "ROOT CAUSE",
            "capsule": root_cause.replace("\n", " ")[:470],
            "content": root_cause + "\n\nResolution route: "
            + pointer(
                bug_path,
                section="s-resolution",
                artifact_id=bug_id,
                relation="next",
                tags=("resolution", "causal-chain"),
                source="declared",
            ),
        },
        {
            "id": "s-resolution",
            "title": "RESOLUTION DESIGN",
            "capsule": resolution.replace("\n", " ")[:470],
            "content": resolution + "\n\nRegression evidence route: "
            + pointer(
                bug_path,
                section="s-regression",
                artifact_id=bug_id,
                relation="validated_by",
                tags=("regression", "evidence"),
                source="declared",
            ),
        },
        {
            "id": "s-regression",
            "title": "REGRESSION CONTRACT",
            "capsule": regression.replace("\n", " ")[:470],
            "content": regression,
        },
    ]
    return render_document(metadata, f"{bug_id}: {title}", sections)


def code_document(
    *,
    code_id: str,
    task_id: str,
    workspace_id: str,
    task_path: str,
    goal_id: str,
    milestone_id: str,
    title: str = "KAIROS runtime core",
    purpose: str = "Convert structured context artifacts into a section-addressable, goal-aware SQLite knowledge graph and control their promotion lifecycle.",
    components: str = "- `kairos.frontmatter.split_frontmatter`\n- `kairos.sections.parse_sections`\n- `kairos.promoter.promote_document`\n- `kairos.search.search_database`\n- `kairos.heartbeat.run_heartbeat`\n- `kairos.workspace.initialize_workspace`",
    interfaces: str = "The supported interface is `python -m kairos` with explicit workspace-scoped subcommands.",
    invariants: str = "Authoritative sources remain workspace-local; promotions are revisioned and transactional; generated routers never become source authority.",
    dependencies: str = "Python 3.11 or newer and SQLite with FTS5; no third-party runtime package is required.",
    testing: str = "Run `python -m unittest discover -s tests -v` and bind each claimed behavior to positive, negative, and failure-boundary evidence.",
    criteria: list[str] | None = None,
    loop: int,
    revision: int = 1,
    state: str = "ready",
) -> str:
    criteria = criteria or []
    metadata = {
        "schema": "kairos-context/v1",
        "id": code_id,
        "type": "code",
        "revision": revision,
        "state": state,
        "authority": "implementation_documentation",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{code_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": utc_now(),
        "capsule": purpose.replace("\n", " ")[:470],
        "claim_boundary": "This document locates and explains implementation surfaces; test results remain separate execution evidence.",
        "entities": [code_id, task_id, title],
        "facets": ["implementation", "interfaces", "invariants", "dependencies", "testing"],
        "criteria": criteria,
        "does_not_answer": ["runtime test pass", "formal acceptance"],
        "answers": [
            {"intent": "implementation_location", "question": f"Which implementation surfaces does {code_id} map?", "target": "s-components"},
            {"intent": "dependency", "question": f"Which dependencies does {code_id} declare?", "target": "s-dependencies"},
            {"intent": "validation", "question": f"Which tests protect the behavior documented by {code_id}?", "target": "s-testing"},
        ],
        "refs": {
            "task": pointer(
                task_path,
                section="s-objective",
                artifact_id=task_id,
                version="dynamic",
                relation="implements",
                tags=("task", "implementation"),
            )
        },
        "search_contract": [
            {
                "query": f"Which implementation surfaces does {code_id} map?",
                "expected": f"{code_id}#s-components",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {
            "id": "s-purpose",
            "title": "PURPOSE",
            "capsule": purpose.replace("\n", " ")[:470],
            "content": purpose,
        },
        {
            "id": "s-components",
            "title": "COMPONENTS AND SYMBOLS",
            "capsule": components.replace("\n", " ")[:470],
            "content": components,
        },
        {
            "id": "s-interfaces",
            "title": "INTERFACES",
            "capsule": interfaces.replace("\n", " ")[:470],
            "content": interfaces,
        },
        {
            "id": "s-invariants",
            "title": "INVARIANTS",
            "capsule": invariants.replace("\n", " ")[:470],
            "content": invariants,
        },
        {
            "id": "s-dependencies",
            "title": "DEPENDENCIES",
            "capsule": dependencies.replace("\n", " ")[:470],
            "content": dependencies,
        },
        {
            "id": "s-testing",
            "title": "TESTING",
            "capsule": testing.replace("\n", " ")[:470],
            "content": testing,
        },
    ]
    return render_document(metadata, f"{code_id}: {title}", sections)


def decision_document(
    *,
    decision_id: str,
    task_id: str,
    title: str,
    question: str,
    context: str,
    options: str,
    selected: str,
    rationale: str,
    risks: str,
    validation: str,
    workspace_id: str,
    task_path: str,
    goal_id: str,
    milestone_id: str,
    criteria: list[str] | None = None,
    loop: int,
    revision: int = 1,
    state: str = "completed",
) -> str:
    criteria = criteria or []
    metadata = {
        "schema": "kairos-context/v1",
        "id": decision_id,
        "type": "decision",
        "revision": revision,
        "state": state,
        "authority": "architecture_authority",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{decision_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": utc_now(),
        "capsule": selected.replace("\n", " ")[:470],
        "claim_boundary": "This record owns the selected project decision and rationale; it does not prove implementation or runtime validation.",
        "entities": [decision_id, task_id, title],
        "facets": ["decision", "tradeoff", "risk", "rollback", "validation"],
        "criteria": criteria,
        "does_not_answer": ["implementation completion", "runtime test pass", "external release authorization"],
        "answers": [
            {"intent": "decision", "question": f"What did {decision_id} decide?", "target": "s-decision"},
            {"intent": "rationale", "question": f"Why was the option in {decision_id} selected?", "target": "s-rationale"},
            {"intent": "risk", "question": f"Which risks and rollback conditions govern {decision_id}?", "target": "s-risks"},
        ],
        "refs": {
            "task": pointer(
                task_path,
                section="s-acceptance",
                artifact_id=task_id,
                version="dynamic",
                relation="informs",
                tags=("decision", "task"),
            )
        },
        "search_contract": [
            {
                "query": f"What did {decision_id} decide?",
                "expected": f"{decision_id}#s-decision",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {
            "id": "s-question",
            "title": "DECISION QUESTION",
            "capsule": question.replace("\n", " ")[:470],
            "content": question,
        },
        {
            "id": "s-context",
            "title": "CONTEXT AND CONSTRAINTS",
            "capsule": context.replace("\n", " ")[:470],
            "content": context,
        },
        {
            "id": "s-options",
            "title": "OPTIONS CONSIDERED",
            "capsule": options.replace("\n", " ")[:470],
            "content": options,
        },
        {
            "id": "s-decision",
            "title": "SELECTED DECISION",
            "capsule": selected.replace("\n", " ")[:470],
            "content": selected,
        },
        {
            "id": "s-rationale",
            "title": "RATIONALE",
            "capsule": rationale.replace("\n", " ")[:470],
            "content": rationale,
        },
        {
            "id": "s-risks",
            "title": "RISKS, REVERSIBILITY, AND ROLLBACK",
            "capsule": risks.replace("\n", " ")[:470],
            "content": risks,
        },
        {
            "id": "s-validation",
            "title": "VALIDATION CONTRACT",
            "capsule": validation.replace("\n", " ")[:470],
            "content": validation,
        },
    ]
    return render_document(metadata, f"{decision_id}: {title}", sections)


def research_document(
    *,
    research_id: str,
    task_id: str,
    title: str,
    question: str,
    source_uri: str,
    source_kind: str,
    source_fact: str,
    interpretation: str,
    workspace_id: str,
    task_path: str,
    goal_id: str,
    milestone_id: str,
    criteria: list[str] | None = None,
    source_sha256: str = "",
    contradictions: str = "No contradiction has been established; competing evidence remains open until explicitly checked.",
    next_query: str = "Reopen the source, inspect at least one competing branch, and convert validated evidence into a success report.",
    loop: int,
    revision: int = 1,
    state: str = "partial",
) -> str:
    normalized_kind = source_kind.strip().casefold().replace("_", "-")
    if normalized_kind not in {"internet", "workspace", "search", "dataset", "user"}:
        raise ValueError(f"unsupported research source kind: {source_kind!r}")
    metadata = {
        "schema": "kairos-context/v1",
        "id": research_id,
        "type": "research",
        "revision": revision,
        "state": state,
        "authority": "research_evidence",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{research_id}",
        "loop": loop,
        "task": task_id,
        "goal": goal_id,
        "milestone": milestone_id,
        "updated_at": utc_now(),
        "capsule": source_fact[:470],
        "claim_boundary": "The source binding and extracted fact are distinct from model interpretation; independent verification is required before this research can support final completion.",
        "entities": [research_id, task_id, source_kind, source_uri[:160]],
        "facets": ["research", "context-acquisition", normalized_kind],
        "criteria": criteria or [],
        "does_not_answer": ["independent source truth", "formal criterion acceptance", "external release approval"],
        "source_uri": source_uri,
        "source_kind": normalized_kind,
        "source_sha256": source_sha256,
        "answers": [
            {"intent": "evidence", "question": question, "target": "s-fact", "language": "en"},
            {"intent": "authority", "question": f"Which source is bound to {research_id}?", "target": "s-source", "language": "en"},
            {"intent": "contradiction", "question": f"Which contradiction remains open in {research_id}?", "target": "s-contradictions", "language": "en"},
            {"intent": "goal_gap", "question": f"Which question follows {research_id}?", "target": "s-next-query", "language": "en"},
        ],
        "refs": {
            "task": pointer(
                task_path,
                section="s-acceptance",
                artifact_id=task_id,
                version="dynamic",
                relation="informs",
                tags=("research", "task"),
            )
        },
        "search_contract": [
            {
                "query": question,
                "expected": f"{research_id}#s-fact",
                "required_top_k": 5,
            }
        ],
    }
    sections = [
        {
            "id": "s-question",
            "title": "RESEARCH QUESTION",
            "capsule": question,
            "content": question,
        },
        {
            "id": "s-source",
            "title": "SOURCE BINDING",
            "capsule": f"Source kind `{normalized_kind}` is bound to `{source_uri}` with an optional byte identity.",
            "content": f"- Source: `{source_uri}`\n- Kind: `{normalized_kind}`\n- SHA-256: `{source_sha256 or 'not supplied'}`\n- Retrieved: `{metadata['updated_at']}`",
        },
        {
            "id": "s-fact",
            "title": "EXTRACTED SOURCE FACT",
            "capsule": source_fact[:470],
            "content": source_fact,
        },
        {
            "id": "s-interpretation",
            "title": "MODEL INTERPRETATION",
            "capsule": interpretation[:470],
            "content": interpretation,
        },
        {
            "id": "s-contradictions",
            "title": "CONTRADICTIONS AND EXCLUDED BRANCHES",
            "capsule": contradictions[:470],
            "content": contradictions,
        },
        {
            "id": "s-next-query",
            "title": "NEXT QUERY",
            "capsule": next_query[:470],
            "content": next_query,
        },
    ]
    return render_document(metadata, f"{research_id}: {title}", sections)


def router_document(
    *,
    artifact_id: str,
    title: str,
    workspace_id: str,
    state: str,
    capsule: str,
    sections: list[dict[str, str]],
    answers: list[dict[str, str]],
    refs: dict[str, str] | None = None,
    document_type: str = "router",
    authority: str = "routing",
    revision: int = 1,
) -> str:
    metadata = {
        "schema": "kairos-context/v1",
        "id": artifact_id,
        "type": document_type,
        "revision": revision,
        "state": state,
        "authority": authority,
        "workspace": workspace_id,
        "route": f"{workspace_id}/{artifact_id}",
        "updated_at": utc_now(),
        "capsule": capsule,
        "claim_boundary": "This canonical routes context and state; substantive evidence remains in the referenced task, report, bug, code, or archive sections.",
        "entities": [artifact_id, workspace_id, "KAIROS"],
        "facets": ["canonical", "navigation", "context-routing"],
        "criteria": [],
        "does_not_answer": ["implementation evidence", "unreferenced historical detail"],
        "answers": answers,
        "refs": refs or {},
        "search_contract": [
            {
                "query": answers[0]["question"],
                "expected": f"{artifact_id}#{answers[0]['target']}",
                "required_top_k": 5,
            }
        ],
    }
    return render_document(metadata, title, sections)
