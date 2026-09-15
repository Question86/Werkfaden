+++
schema = "kairos-context/v1"
id = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
updated_at = "2026-09-15T22:38:00Z"
capsule = "Binding falsification-first loop from Human problem input to bounded context, causal proof, minimal general patch, governed apply, fresh run, and restart from the top."
claim_boundary = "This contract owns the investigation and patch sequence; it does not prove any project hypothesis, implementation fact, Runtime state, test result, or completion claim."
entities = ["KAIROS", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP", "hypothesis", "falsification", "context-map", "source-prediction", "causal-proof"]
facets = ["investigation", "falsification", "retrieval", "source-inspection", "patch-scope", "context-budget"]
criteria = []
does_not_answer = ["current project state", "whether a hypothesis is true", "current source bytes", "whether a patch passed"]

[[answers]]
intent = "operating_rules"
question = "What sequence must an agent follow from a Human problem to a code patch?"
target = "s-state-machine"

[[answers]]
intent = "research"
question = "What must be falsified before an agent maps implementation context?"
target = "s-falsify"

[[answers]]
intent = "retrieval"
question = "In which order should an agent acquire implementation context?"
target = "s-map"

[[answers]]
intent = "validation"
question = "What must be proven before an agent may patch source?"
target = "s-prove"

[[answers]]
intent = "retrieval"
question = "How must an agent limit raw file reads and grep output?"
target = "s-context-budget"

[refs]
contract = "[ref:AGENTS.md#s-investigation-loop|id:KAIROS_OPERATING_CONTRACT|v:dynamic|rel:requires|tags:operating-loop,hypothesis|src:declared]"
router = "[ref:NEURAL_CORTEX.md#s-orientation|id:KAIROS_NEURAL_CORTEX|v:dynamic|rel:references|tags:orientation,router|src:system]"

[[search_contract]]
query = "What sequence must an agent follow from a Human problem to a code patch?"
expected = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP#s-state-machine"
required_top_k = 3
+++
# Hypothesis-to-Patch Loop

## CONTEXT INDEX

- [`s-state-machine`](#s-state-machine) — One closed investigation loop governs every material problem from Human input through fresh-run restart.
- [`s-frame`](#s-frame) — Convert Human input into one explicit falsifiable problem hypothesis before implementation discovery.
- [`s-falsify`](#s-falsify) — Two independent probes must support the hypothesis before implementation mapping begins.
- [`s-map`](#s-map) — Acquire context from semantic architecture to run evidence to graph to implementation documents before source bytes.
- [`s-counterprobe`](#s-counterprobe) — Try to prove the captured causal surface incomplete before exact source inspection.
- [`s-source-prediction`](#s-source-prediction) — Predict file, symbol or section, defect signature, and legitimate alternative before reading exact code lines.
- [`s-prove`](#s-prove) — No patch exists until observation, violated invariant, causal owner, and exact affected scope are evidenced.
- [`s-patch-run`](#s-patch-run) — Apply only the smallest general repair through the governed mutation path, then run fresh and restart from the top.
- [`s-context-budget`](#s-context-budget) — Route before reading; raw shell output is bounded evidence and never the default discovery mechanism.

<a id="s-state-machine"></a>
## CLOSED STATE MACHINE

> Capsule: One closed investigation loop governs every material problem from Human input through fresh-run restart.

The only permitted material problem-solving sequence is:

```text
HUMAN INPUT
-> FRAME
-> FALSIFY x2
-> MAP
-> CONTEXT COUNTERPROBE
-> SOURCE PREDICTION
-> EXACT SOURCE INSPECTION
-> CAUSAL PROOF
-> SMALLEST GENERAL PATCH
-> GOVERNED WORKSHOP / MUTATION PATH
-> HEARTBEAT / PROMOTION / POSTCHECK
-> FRESH RUNTIME RUN
-> RETURN TO ORIENTATION AND FRAME AGAIN
```

A failed gate returns to the nearest earlier reasoning state. It never licenses a broader read, a speculative patch, or a bypass around the missing evidence.

<a id="s-frame"></a>
## FRAME ONE FALSIFIABLE PROBLEM

> Capsule: Convert Human input into one explicit falsifiable problem hypothesis before implementation discovery.

Record exactly five things before implementation discovery:

1. the Human observation or requested behavior;
2. the expected invariant;
3. the suspected violation;
4. the expected owning architecture surface;
5. evidence that would refute the hypothesis.

The hypothesis must be narrow enough to be wrong. Do not turn an observation into a root-cause claim by wording alone.

<a id="s-falsify"></a>
## TWO INDEPENDENT FALSIFIERS

> Capsule: Two independent probes must support the hypothesis before implementation mapping begins.

Run two orthogonal probes. Rephrasing the same search twice does not count.

**Falsifier A — semantic / authority:** ask which current contract or architecture authority would have to define, permit, forbid, or constrain the suspected behavior if the hypothesis were correct.

**Falsifier B — structural / graph:** ask which declared ownership, producer/consumer, dependency, call, evidence, or Runtime route would have to exist if the suspected mechanism were structurally possible.

Continue only when both probes support the same problem frame. If either probe refutes, contradicts, or fails to establish the expected condition, return to `FRAME` and reformulate the hypothesis.

<a id="s-map"></a>
## MAP CONTEXT IN INFORMATION-EFFICIENT ORDER

> Capsule: Acquire context from semantic architecture to run evidence to graph to implementation documents before source bytes.

Once the hypothesis survives both falsifiers, narrow context in this order:

1. semantic architecture or stable responsibility atlas, when the project provides one;
2. current run-derived execution evidence when execution order or observed participation matters;
3. the smallest typed graph neighbourhood needed for ownership, direction, producer/consumer, dependency, or causal reach;
4. exact implementation-document sections already attached to that bounded surface;
5. source bytes only after the expected file/symbol/section is known.

Choose the surface that eliminates the most remaining explanations with the least returned context. Source code is high-volume evidence and therefore comes late, not first.

<a id="s-counterprobe"></a>
## COUNTERPROBE THE CAPTURED CONTEXT

> Capsule: Try to prove the captured causal surface incomplete before exact source inspection.

Before reading exact code lines, state the current context claim: which nodes, implementation documents, files, and interfaces are believed to contain the complete causal surface.

Then make one bounded negative/completeness probe. Ask whether there is another producer, consumer, implementation, bypass, alternate owner, or relevant exact-literal location outside the captured surface. Prefer graph traversal; use `rg -l` only when an exact literal and already-bounded filesystem scope make it the cheaper falsifier.

If an unexpected branch appears, mark the context incomplete and return to `MAP`. Do not keep the old scope and append the surprise opportunistically.

<a id="s-source-prediction"></a>
## PREDICT SOURCE EVIDENCE BEFORE READING IT

> Capsule: Predict file, symbol or section, defect signature, and legitimate alternative before reading exact code lines.

Before source inspection, write the expected evidence shape:

- expected file;
- expected symbol or indexed section;
- expected relation to the already-mapped mechanism;
- concrete code signature that would support the hypothesis;
- concrete legitimate implementation shape that would refute it.

Only then use the governed `search -> routing receipt -> source-permit -> source-search` path for the exact lines needed to test that prediction.

<a id="s-prove"></a>
## CAUSAL PROOF GATES PATCHING

> Capsule: No patch exists until observation, violated invariant, causal owner, and exact affected scope are evidenced.

A patch may begin only when all four are established:

1. observed defect or mismatch;
2. violated invariant or contract;
3. causal owning mechanism;
4. exact affected implementation scope.

The evidence must explain why the identified code causes the observed behavior, why the proposed mechanism is the general correction, and why no required owner remains outside the bounded scope. If any element is unresolved, there is no patch yet.

<a id="s-patch-run"></a>
## PATCH, GOVERN, RUN, RESET

> Capsule: Apply only the smallest general repair through the governed mutation path, then run fresh and restart from the top.

Freeze scope after causal proof. Repair the smallest general mechanism that closes the demonstrated defect. A general fix may be small; general does not mean broad refactor. New evidence that invalidates scope aborts the patch and returns to `MAP` rather than widening the transaction in place.

Use the project's governed mutation sequence, including isolated/shadow verification, required tests, apply, heartbeat, promotion, postcheck, and current-state verification. A green subcheck proves only its boundary.

After a fresh Runtime run, discard the previous investigative frontier as the starting assumption. Re-enter orientation, inspect the new artifacts, identify the next observed problem, frame a new falsifiable hypothesis, and repeat the loop from the top.

<a id="s-context-budget"></a>
## CONTEXT-BUDGET DISCIPLINE

> Capsule: Route before reading; raw shell output is bounded evidence and never the default discovery mechanism.

1. Use the smallest applicable KAIROS surface before raw reads: graph for identity/count/relation questions, section search for prose questions, and governed source search for exact bytes.
2. Unbounded full-file dumps of governed Markdown or source are forbidden for discovery. Do not use `Get-Content`, `cat`, `type`, `ReadAllText`, or equivalents to pour a whole source/document into model context. Small explicit state/authority files required by the operating contract are the narrow exception.
3. `rg` is a scalpel: one exact-location/completeness call per routing decision, already-bounded path scope, at most 30 returned source lines, 50 documentation lines, or 20 file paths. Prefer `rg -l` when only location matters.
4. If bounded evidence is insufficient, ask a sharper KAIROS question or follow a typed relation. Never solve uncertainty by raising the output limit until the whole file appears.
5. Do not repeat materially identical searches without new evidence or a changed question.
