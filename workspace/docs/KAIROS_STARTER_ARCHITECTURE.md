+++
schema = "kairos-context/v1"
id = "KAIROS_STARTER_ARCHITECTURE"
type = "documentation"
revision = 2
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_STARTER_ARCHITECTURE"
updated_at = "2026-09-15T22:38:00Z"
capsule = "Starter self-model for authority, semantic project atlases, falsification-first investigation, bounded retrieval, evidence promotion, and controlled mutation."
claim_boundary = "This document owns starter architecture and placement; live state, project-specific atlases, run evidence, task contracts, implementation documents, and promoted evidence own their scoped claims."
entities = ["KAIROS_STARTER_ARCHITECTURE", "KAIROS", "SQLite FTS5", "semantic-atlas", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP"]
facets = ["architecture", "content-placement", "database-model", "lifecycle", "project-kickoff", "investigation"]
criteria = []
does_not_answer = ["live project outcome", "task completion", "project-specific architecture facts"]

[[answers]]
intent = "architecture"
question = "How is KAIROS organized and what should I read first?"
target = "s-entry"

[[answers]]
intent = "content_placement"
question = "Where should I put new KAIROS content?"
target = "s-placement"

[[answers]]
intent = "architecture"
question = "How should a project expose a stable top-level architecture map above implementation documents?"
target = "s-semantic-atlas"

[[answers]]
intent = "lifecycle"
question = "How do I start a new project from the golden KAIROS template?"
target = "s-kickoff"

[[answers]]
intent = "architecture"
question = "How is KAIROS architecture represented in the database?"
target = "s-database"

[refs]
contract = "[ref:AGENTS.md#s-operating-loop|id:KAIROS_OPERATING_CONTRACT|v:2|rel:requires|tags:contract,workflow|src:system]"
method = "[ref:docs/HYPOTHESIS_TO_PATCH_LOOP.md#s-state-machine|id:KAIROS_HYPOTHESIS_TO_PATCH_LOOP|v:1|rel:requires|tags:investigation,falsification|src:declared]"
state = "[ref:current.json|v:dynamic|rel:references|tags:runtime,state|src:system]"

[[search_contract]]
query = "How is KAIROS organized and what should I read first?"
expected = "KAIROS_STARTER_ARCHITECTURE#s-entry"
required_top_k = 1

[[search_contract]]
query = "Where should I put new KAIROS content?"
expected = "KAIROS_STARTER_ARCHITECTURE#s-placement"
required_top_k = 1

[[search_contract]]
query = "How should a project expose a stable top-level architecture map above implementation documents?"
expected = "KAIROS_STARTER_ARCHITECTURE#s-semantic-atlas"
required_top_k = 1
+++
# KAIROS STARTER ARCHITECTURE

## CONTEXT INDEX

- [`s-entry`](#s-entry) — Use the operating contract for procedure, live state for current scope, and project architecture authorities for stable system meaning.
- [`s-layers`](#s-layers) — Human problem framing, semantic architecture, run evidence, graph, implementation documents, source bytes, and controlled mutation form distinct layers.
- [`s-placement`](#s-placement) — Place each durable claim by epistemic role and connect it with typed workspace-relative references.
- [`s-semantic-atlas`](#s-semantic-atlas) — A project may expose a thin stable semantic atlas above run evidence and implementation documents without duplicating source detail.
- [`s-database`](#s-database) — SQLite stores artifacts, stable sections, answer handles, typed relations, graph projections, goal coverage, receipts, and governance evidence.
- [`s-lifecycle`](#s-lifecycle) — Investigation returns to orientation after each fresh run; loop finalization remains a separate fail-closed lifecycle operation.
- [`s-kickoff`](#s-kickoff) — One Human-triggered project-kickoff contract creates fresh goal and task sources and activates the next loop.

<a id="s-entry"></a>
## LLM ENTRY

> Capsule: Use the operating contract for procedure, live state for current scope, and project architecture authorities for stable system meaning.

Read `AGENTS.md`, `current.json`, `_LOOP_GATE.md`, `NEURAL_CORTEX.md`, and `ACTIVE.md`. A new Human material command becomes bounded governed work. For a problem or defect, enter the falsification-first investigation loop before implementation discovery. Ask KAIROS metadata for meaning and authority before requesting bounded source inspection.

<a id="s-layers"></a>
## SYSTEM LAYERS

> Capsule: Human problem framing, semantic architecture, run evidence, graph, implementation documents, source bytes, and controlled mutation form distinct layers.

The preferred project reasoning stack is:

```text
Human problem / observation
-> one falsifiable model hypothesis
-> two independent falsifiers
-> stable semantic responsibility atlas, when present
-> current run/runtime evidence
-> typed graph neighbourhood
-> exact implementation-document sections
-> exact permitted source lines
-> causal proof
-> smallest general governed patch
-> fresh run
-> return to orientation
```

Goal JSON and governed Markdown own claims. Heartbeats validate and promote section-addressable metadata into SQLite. Generated routers expose the current frontier. Graph/search locate authoritative material; they do not replace it.

<a id="s-placement"></a>
## CONTENT PLACEMENT

> Capsule: Place each durable claim by epistemic role and connect it with typed workspace-relative references.

| Content | Location |
|---|---|
| Goal, milestone, criterion | `goals/` |
| Required work | `tasks/` |
| Outcome and evidence | `reports/` |
| Failure and root cause | `bugs/` |
| Implementation map | `code/` |
| Source fact and interpretation | `research/` |
| Tradeoff decision | `decisions/` |
| Stable synthesis / semantic atlas | `docs/` |
| Closed-loop synthesis | `archive/` |

<a id="s-semantic-atlas"></a>
## PROJECT SEMANTIC ATLAS

> Capsule: A project may expose a thin stable semantic atlas above run evidence and implementation documents without duplicating source detail.

For a large system whose implementation documents are already complete but difficult to enter from the top, create one promoted architecture document that names stable semantic responsibility codes and routes each code downward.

The atlas should contain only the minimum needed for orientation:

- stable semantic code / identity;
- role and boundary;
- broad input/output class where explicitly known;
- invariants owned at that level;
- references to current run/runtime evidence;
- references to implementation documents;
- related semantic codes;
- explicit `does_not_answer` / claim boundary.

The atlas must not copy full implementation prose, pretend semantic labels are execution order, or become a second source of live state. Run evidence answers what executed; implementation documents answer what implements it; governed source inspection answers exact current bytes.

`AXIOM_RUNTIME_ATLAS` in this workspace is an example of that separation: its A-L codes name semantic responsibilities while current execution must be proven separately.

<a id="s-database"></a>
## DATABASE REPRESENTATION

> Capsule: SQLite stores artifacts, stable sections, answer handles, typed relations, graph projections, goal coverage, receipts, and governance evidence.

Search returns artifact/section routes and context-chase edges. Graph surfaces answer normalized identity, count, provenance, producer/consumer, contract, and multi-hop questions. Scores and graph matches route inspection but never override the owning source. SQLite and dynamic routers are derived and must not be edited directly.

<a id="s-lifecycle"></a>
## LOOP LIFECYCLE

> Capsule: Investigation returns to orientation after each fresh run; loop finalization remains a separate fail-closed lifecycle operation.

A successful patch/test does not license continuation from the previous local mental model. After a fresh Runtime run, use the new artifacts to identify the next observed problem and restart at orientation/problem framing. Separately, task and goal closure can make a numbered KAIROS loop ready for `finalize`, which creates its required archive, receipt, and verified backup.

<a id="s-kickoff"></a>
## ATOMIC PROJECT KICKOFF

> Capsule: One Human-triggered project-kickoff contract creates fresh goal and task sources and activates the next loop.

Run `kairos project-kickoff` with a `kairos-project-kickoff/v1` JSON contract. The command verifies the predecessor seal, derives new governance scope, stages deterministic sources, records an exact-once transition, and activates Loop N+1.
