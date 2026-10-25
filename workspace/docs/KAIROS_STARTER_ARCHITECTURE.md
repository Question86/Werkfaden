+++
schema = "kairos-context/v1"
id = "KAIROS_STARTER_ARCHITECTURE"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_STARTER_ARCHITECTURE"
updated_at = "2026-09-07T17:52:05Z"
capsule = "Clean starter self-model for KAIROS authority, content placement, metadata retrieval, loop finalization, and atomic project kickoff."
claim_boundary = "This document owns starter architecture and placement; live state, task contracts, and promoted evidence own their scoped claims."
entities = ["KAIROS_STARTER_ARCHITECTURE", "KAIROS", "SQLite FTS5"]
facets = ["architecture", "content-placement", "database-model", "lifecycle", "project-kickoff"]
criteria = []
does_not_answer = ["live project outcome", "task completion"]

[[answers]]
intent = "architecture"
question = "How is KAIROS organized and what should I read first?"
target = "s-entry"

[[answers]]
intent = "content_placement"
question = "Where should I put new KAIROS content?"
target = "s-placement"

[[answers]]
intent = "lifecycle"
question = "How do I start a new project from the golden KAIROS template?"
target = "s-kickoff"

[[answers]]
intent = "architecture"
question = "How is KAIROS architecture represented in the database?"
target = "s-database"

[refs]
contract = "[ref:AGENTS.md#s-operating-loop|id:KAIROS_OPERATING_CONTRACT|v:1|rel:requires|tags:contract,workflow|src:system]"
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
query = "How do I start a new project from the golden KAIROS template?"
expected = "KAIROS_STARTER_ARCHITECTURE#s-kickoff"
required_top_k = 1
+++
# KAIROS STARTER ARCHITECTURE

## CONTEXT INDEX

- [`s-entry`](#s-entry) — Use the operating contract for procedure, live state for current scope, and this self-model for stable architecture.
- [`s-layers`](#s-layers) — Authoritative documents feed a verified SQLite projection, bounded routers, receipts, archives, and recovery packages.
- [`s-placement`](#s-placement) — Place each durable claim by epistemic role and connect it with typed workspace-relative references.
- [`s-database`](#s-database) — SQLite stores artifacts, stable sections, natural-language handles, typed relations, goal coverage, receipts, and governance evidence.
- [`s-lifecycle`](#s-lifecycle) — Task and goal closure make a loop ready; finalize creates its mandatory archive, receipt, and verified backup.
- [`s-kickoff`](#s-kickoff) — One Human-triggered project-kickoff contract creates fresh goal and task sources and activates the next loop without reopening the predecess

<a id="s-entry"></a>
## LLM ENTRY

> Capsule: Use the operating contract for procedure, live state for current scope, and this self-model for stable architecture.

Read AGENTS.md, current.json, and _LOOP_GATE.md. A new Human material command becomes a task. Ask KAIROS metadata for meaning and authority before requesting bounded source inspection.

<a id="s-layers"></a>
## SYSTEM LAYERS

> Capsule: Authoritative documents feed a verified SQLite projection, bounded routers, receipts, archives, and recovery packages.

Goal JSON and governed Markdown own claims. Heartbeats validate and promote section-addressable metadata into SQLite. Generated routers expose the current frontier. Archives and verified backups seal each numbered loop.

<a id="s-placement"></a>
## CONTENT PLACEMENT

> Capsule: Place each durable claim by epistemic role and connect it with typed workspace-relative references.

| Content | Location |
|---|---|
| Goal, milestone, criterion | goals/ |
| Required work | tasks/ |
| Outcome and evidence | reports/ |
| Failure and root cause | bugs/ |
| Implementation map | code/ |
| Source fact and interpretation | research/ |
| Tradeoff decision | decisions/ |
| Stable synthesis | docs/ |
| Closed-loop synthesis | archive/ |

<a id="s-database"></a>
## DATABASE REPRESENTATION

> Capsule: SQLite stores artifacts, stable sections, natural-language handles, typed relations, goal coverage, receipts, and governance evidence.

Search returns artifact and section routes plus context-chase edges. Scores route inspection but never override the owning source. SQLite and dynamic routers are derived and must not be edited directly.

<a id="s-lifecycle"></a>
## LOOP LIFECYCLE

> Capsule: Task and goal closure make a loop ready; finalize creates its mandatory archive, receipt, and verified backup.

BREATHE manages prompt pressure only. finalize seals semantic completion. FINALIZED history remains immutable. Every completed numbered loop retains ARCHIVE_LNNNN and a verified backup.

<a id="s-kickoff"></a>
## ATOMIC PROJECT KICKOFF

> Capsule: One Human-triggered project-kickoff contract creates fresh goal and task sources and activates the next loop without reopening the predecessor.

Run kairos project-kickoff with a kairos-project-kickoff/v1 JSON contract. The command verifies the predecessor seal, derives new governance scope, stages deterministic sources, records an exact-once transition, and activates Loop N+1.
