+++
schema = "kairos-context/v1"
id = "KAIROS_OPERATING_CONTRACT"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_OPERATING_CONTRACT"
updated_at = "2026-09-15T22:38:00Z"
capsule = "Mandatory metadata-first operating contract: falsify the problem before mapping code, prove cause before patching, and keep context bounded."
claim_boundary = "This canonical owns operating procedure only; live state, implementation facts, Runtime behavior, validation, and completion remain owned by their scoped authorities."
entities = ["KAIROS_OPERATING_CONTRACT", "KAIROS_FRAMEWORK_CANONICAL_20260907", "KAIROS", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP"]
facets = ["canonical", "navigation", "context-routing", "falsification", "context-budget"]
criteria = []
does_not_answer = ["implementation evidence", "live Runtime state", "unreferenced historical detail"]

[[answers]]
intent = "orientation"
question = "How must an agent operate inside this KAIROS workspace?"
target = "s-operating-loop"

[[answers]]
intent = "research"
question = "What investigation loop must an agent follow before patching code?"
target = "s-investigation-loop"

[[answers]]
intent = "authority"
question = "Which KAIROS source is authoritative?"
target = "s-authority"

[[answers]]
intent = "retrieval"
question = "How must raw reads and grep output be bounded?"
target = "s-context-budget"

[[answers]]
intent = "validation"
question = "When may an agent finalize a KAIROS loop?"
target = "s-finalization"

[refs]
state = "[ref:current.json|v:dynamic|rel:references|tags:authority,state|src:system]"
gate = "[ref:_LOOP_GATE.md#s-verdict|id:KAIROS_LOOP_GATE|v:dynamic|rel:requires|tags:gate,state|src:system]"
router = "[ref:NEURAL_CORTEX.md#s-orientation|id:KAIROS_NEURAL_CORTEX|v:dynamic|rel:references|tags:orientation,router|src:system]"
active = "[ref:ACTIVE.md#s-active-frontier|id:KAIROS_ACTIVE|v:dynamic|rel:references|tags:active,queue|src:system]"
protocol = "[ref:docs/HYPOTHESIS_TO_PATCH_LOOP.md#s-state-machine|id:KAIROS_HYPOTHESIS_TO_PATCH_LOOP|v:1|rel:requires|tags:investigation,falsification,patch|src:declared]"

[[search_contract]]
query = "How must an agent operate inside this KAIROS workspace?"
expected = "KAIROS_OPERATING_CONTRACT#s-operating-loop"
required_top_k = 5
+++
# KAIROS AGENT OPERATING CONTRACT

## CONTEXT INDEX

- [`s-language`](#s-language) — English-only governed project material.
- [`s-operating-loop`](#s-operating-loop) — Orient from live authority, then enter the mandatory falsification-first investigation loop.
- [`s-investigation-loop`](#s-investigation-loop) — Human input becomes one falsifiable hypothesis; two independent probes must support it before code mapping.
- [`s-authority`](#s-authority) — Authority is question-scoped; routing metadata never overrides the source that owns the claim.
- [`s-context`](#s-context) — Acquire only the smallest authoritative context needed for the current reasoning state.
- [`s-retrieval`](#s-retrieval) — Graph resolves structure, search resolves prose questions, governed source search resolves exact bytes.
- [`s-context-budget`](#s-context-budget) — Whole-file dumps are not discovery; grep is bounded and context widens by a sharper query, not a bigger dump.
- [`s-finalization`](#s-finalization) — Finalization is fail-closed while required evidence, source freshness, promotion, or closure gates remain open.

<a id="s-language"></a>
## LANGUAGE CONTRACT

> Capsule: All KAIROS source code, metadata, documents, queries, receipts, tests, logs, and generated artifacts must be written in English.

Do not introduce non-English identifiers, prose, filenames, query handles, or generated text into the KAIROS workspace.

<a id="s-operating-loop"></a>
## MANDATORY OPERATING LOOP

> Capsule: Orient from live authority, then enter the mandatory falsification-first investigation loop.

1. Read `current.json`, `_LOOP_GATE.md`, `NEURAL_CORTEX.md`, and `ACTIVE.md`.
2. Resolve the active goal/task/criterion and required evidence before material work.
3. For a Human problem or observed Runtime defect, follow `KAIROS_HYPOTHESIS_TO_PATCH_LOOP`; do not jump directly to source inspection or mutation.
4. Document material work in the artifact type that owns its claim and promote it in the same heartbeat.
5. Inspect receipts, coverage, current state, and all applicable postchecks before any closure claim.
6. After a fresh Runtime run, restart from orientation and form the next hypothesis from the new artifacts rather than carrying the previous investigative frontier forward as truth.

<a id="s-investigation-loop"></a>
## FALSIFICATION-FIRST INVESTIGATION LOOP

> Capsule: Human input becomes one falsifiable hypothesis; two independent probes must support it before code mapping.

The non-skippable sequence is:

```text
HUMAN INPUT
-> FRAME ONE FALSIFIABLE HYPOTHESIS
-> FALSIFY A: semantic / authority probe
-> FALSIFY B: structural / graph probe
-> MAP semantic architecture -> run evidence -> graph -> implementation sections
-> COUNTERPROBE captured context for a missing producer / consumer / owner / bypass
-> PREDICT exact file / symbol / section and defect signature
-> INSPECT exact permitted source lines
-> PROVE observation + violated invariant + causal owner + exact affected scope
-> PATCH the smallest general mechanism only
-> GOVERNED WORKSHOP / mutation path
-> HEARTBEAT / promotion / postcheck
-> FRESH RUN
-> RETURN TO ORIENTATION
```

If either falsifier fails, reformulate the hypothesis. If the counterprobe finds another branch, return to mapping. If causal proof is incomplete, no patch exists yet. New evidence that invalidates patch scope aborts the patch and returns to mapping; it does not authorize scope growth in place.

The detailed gate definitions are owned by `docs/HYPOTHESIS_TO_PATCH_LOOP.md`.

<a id="s-authority"></a>
## AUTHORITY AND MUTATION BOUNDARY

> Capsule: Authority is question-scoped; routing metadata never overrides the source that owns the claim.

- Operating procedure: `AGENTS.md` and the referenced investigation-loop contract.
- Live identifiers and lifecycle: `current.json`.
- Allowed transitions and blockers: `_LOOP_GATE.md`.
- Required work and acceptance: active goal and task contracts.
- Factual outcome claims: promoted evidence sections.
- Stable project architecture: the project's promoted architecture authority or semantic atlas when one exists.
- Observed execution order: current run/runtime evidence, not a remembered architecture diagram.
- Retrieval: routers, graph, search, and SQLite metadata locate authority but never replace it.

A later timestamp, higher search score, remembered filename, or plausible architecture cannot override the source that owns the question.

<a id="s-context"></a>
## CONTEXT ACQUISITION

> Capsule: Acquire only the smallest authoritative context needed for the current reasoning state.

During investigation, context narrows in this order when those surfaces exist: semantic responsibility atlas -> current run-derived evidence -> typed graph neighbourhood -> exact implementation-document sections -> permitted source lines. Use `depth` for one causal/evidence path, `breadth` only for genuine competing branches or contradictions, `verify` to reopen evidence before a claim transition, and `breathe` to checkpoint the exact restart frontier.

Full workspace scans are recovery-only. A search miss is not proof of absence.

<a id="s-retrieval"></a>
## CHOOSING A RETRIEVAL SURFACE

> Capsule: Graph resolves structure, search resolves prose questions, governed source search resolves exact bytes.

Classify the question before choosing a surface.

1. Identity, counts, distributions, typed neighbourhoods, producer/consumer relations, provenance, or multi-hop structure: use the appropriate `graph` surface.
2. A precise prose question asking which governed section owns an explanation, contract, invariant, rationale, or evidence claim: use `search`.
3. An exact string when only containing files matter: use bounded `rg -l`; the hit is location evidence, not authority.
4. Exact governed source bytes: use `search -> routing receipt -> source-permit -> source-search` after the expected file/symbol/section is known.
5. Empty graph results require reading `no_match`; qualified-name ambiguity is not corpus absence.

The measured cost rules remain in `RETRIEVAL_METHOD.md` in the harness documentation.

<a id="s-context-budget"></a>
## CONTEXT-BUDGET DISCIPLINE

> Capsule: Whole-file dumps are not discovery; grep is bounded and context widens by a sharper query, not a bigger dump.

- Unbounded `Get-Content`, `cat`, `type`, `ReadAllText`, or equivalent whole-file dumps of governed Markdown or source are forbidden for discovery. Small explicit state/authority files required by this contract are the narrow exception.
- `rg` is a scalpel: one exact-location/completeness call per routing decision, on an already-bounded path scope, returning at most 30 source lines, 50 documentation lines, or 20 file paths. Prefer `rg -l` when only location matters.
- Do not repeat materially identical searches without new evidence or a materially changed question.
- When bounded evidence is insufficient, ask a sharper KAIROS question or follow a typed relation. Do not solve uncertainty by raising output limits until the whole file is in context.
- A designated Human-authored cross-system memory may be read and used by the model, but the model may not replace it, fork it, synthesize a competing memory, or overwrite it unless the Human explicitly requests that exact memory mutation.

<a id="s-finalization"></a>
## FAIL-CLOSED FINALIZATION

> Capsule: Finalization is fail-closed while required evidence, source freshness, promotion, or closure gates remain open.

A completion claim requires the live goal/criterion contract to be satisfied, a verified final heartbeat, clean mandatory source/promotion state, the required immutable archive/backup surfaces, and every applicable project-specific release gate. A Workshop/test/search PASS proves only its own boundary.
