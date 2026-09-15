+++
schema = "kairos-context/v1"
id = "KAIROS_OPERATING_CONTRACT"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_OPERATING_CONTRACT"
updated_at = "2026-09-07T17:52:05Z"
capsule = "Mandatory English-only, metadata-first operating contract for every agent working inside this KAIROS workspace."
claim_boundary = "This canonical routes context and state; substantive evidence remains in the referenced task, report, bug, code, or archive sections."
entities = ["KAIROS_OPERATING_CONTRACT", "KAIROS_FRAMEWORK_CANONICAL_20260907", "KAIROS"]
facets = ["canonical", "navigation", "context-routing"]
criteria = []
does_not_answer = ["implementation evidence", "unreferenced historical detail"]

[[answers]]
intent = "orientation"
question = "How must an agent operate inside this KAIROS workspace?"
target = "s-operating-loop"

[[answers]]
intent = "authority"
question = "Which KAIROS source is authoritative?"
target = "s-authority"

[[answers]]
intent = "validation"
question = "When may an agent finalize a KAIROS loop?"
target = "s-finalization"

[refs]
state = "[ref:current.json|v:dynamic|rel:references|tags:authority,state|src:system]"
gate = "[ref:_LOOP_GATE.md#s-verdict|id:KAIROS_LOOP_GATE|v:dynamic|rel:requires|tags:gate,state|src:system]"
router = "[ref:NEURAL_CORTEX.md#s-orientation|id:KAIROS_NEURAL_CORTEX|v:dynamic|rel:references|tags:orientation,router|src:system]"
active = "[ref:ACTIVE.md#s-active-frontier|id:KAIROS_ACTIVE|v:dynamic|rel:references|tags:active,queue|src:system]"

[[search_contract]]
query = "How must an agent operate inside this KAIROS workspace?"
expected = "KAIROS_OPERATING_CONTRACT#s-operating-loop"
required_top_k = 5
+++
# KAIROS AGENT OPERATING CONTRACT

## CONTEXT INDEX

- [`s-language`](#s-language) — All KAIROS source code, metadata, documents, queries, receipts, tests, logs, and generated artifacts must be written in English.
- [`s-operating-loop`](#s-operating-loop) — Orient, select the active criterion, acquire bounded context, act, document, promote, verify, update coverage, and checkpoint in every mater
- [`s-authority`](#s-authority) — Authority is question-scoped: operating rules, live state, permitted transitions, work scope, factual evidence, architecture, and retrieval
- [`s-context`](#s-context) — Open the returned artifact section first, then chase only typed prerequisite, cause, implementation, evidence, validation, or supersession r
- [`s-retrieval`](#s-retrieval) — Classify the question before choosing a surface; the same answer costs up to three orders of magnitude more through the wrong one.
- [`s-finalization`](#s-finalization) — Finalization is forbidden while a required criterion lacks validated evidence or any promotion event is pending or failed.

<a id="s-language"></a>
## LANGUAGE CONTRACT

> Capsule: All KAIROS source code, metadata, documents, queries, receipts, tests, logs, and generated artifacts must be written in English.

Do not introduce non-English identifiers, prose, filenames, query handles, or generated text into the KAIROS workspace.

<a id="s-operating-loop"></a>
## MANDATORY OPERATING LOOP

> Capsule: Orient, select the active criterion, acquire bounded context, act, document, promote, verify, update coverage, and checkpoint in every material heartbeat.

1. Read `current.json`, `_LOOP_GATE.md`, `NEURAL_CORTEX.md`, and `ACTIVE.md`.
2. Search by an explicit question before broad inspection.
3. Choose `work`, `depth`, `breadth`, `breathe`, or `verify` from task structure.
4. Perform one bounded action.
5. Write the appropriate task, report, bug, code, decision, research, or archive document.
6. Promote it in the same heartbeat and inspect the receipt.
7. Update goal coverage and checkpoint state.

<a id="s-authority"></a>
## AUTHORITY AND MUTATION BOUNDARY

> Capsule: Authority is question-scoped: operating rules, live state, permitted transitions, work scope, factual evidence, architecture, and retrieval each have a distinct owner.

- Operating procedure: `AGENTS.md`.
- Live identifiers and lifecycle: `current.json`.
- Allowed transitions and blockers: `_LOOP_GATE.md`.
- Required work and acceptance: active goal and task contracts.
- Factual outcome claims: promoted evidence sections.
- Placement and topology: `KAIROS_ARCHITECTURE_ATLAS`.
- Retrieval: routers and SQLite metadata, which locate authority but never replace it.

A later or higher-scored result cannot override the source that owns the question.

<a id="s-context"></a>
## CONTEXT ACQUISITION

> Capsule: Open the returned artifact section first, then chase only typed prerequisite, cause, implementation, evidence, validation, or supersession relations needed by the query frame.

Use `depth` for one causal or evidence chain, `breadth` for competing branches or contradictions, and `breathe` to persist the frontier before releasing prompt context. Full workspace scans are recovery-only.

<a id="s-retrieval"></a>
## CHOOSING A RETRIEVAL SURFACE

> Capsule: Classify the question before choosing a surface; the same answer costs up to three orders of magnitude more through the wrong one.

`search` is not the default entry point. It ranks sections, so it cannot aggregate and cannot reach the file system.

1. Counting or distribution over the corpus: `graph --census` first, then `graph --inventory <table> --field F --value V` when the rows themselves are needed. Never build a corpus total by looping per-document reads; that path costs about two thousand times the census.
2. Before naming an identifier in a query, run `graph --resolve <partial>`. It returns the canonical identity and how many documents carry it. At one or two documents, query the identifier directly or go to `graph --node`. From three, describe the subject in prose instead. At twenty-six or more, keep the identifier out of the query: the bonus for exact graph identity is flat, so a widely shared name pulls in every document that touches it.
3. An exact string you already hold, when only the containing files matter, is a file-search question and not a graph question.
4. One relation out: `graph --node`. Two or more: `graph --chase` with an explicit `--max-hops`.
5. An empty exact lookup is not a finding until its `no_match` block says no qualified form of the name exists. Stored under a qualified name and absent from the corpus are different answers, and only the second may be cited.

The measured procedure behind these rules, with the cost of each surface, is `RETRIEVAL_METHOD.md` in the harness documentation.

<a id="s-finalization"></a>
## FAIL-CLOSED FINALIZATION

> Capsule: Finalization is forbidden while a required criterion lacks validated evidence or any promotion event is pending or failed.

A completion claim requires a verified final heartbeat, complete mandatory criterion coverage, a promoted immutable archive, and a finalization receipt.
