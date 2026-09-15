+++
schema = "kairos-context/v1"
id = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
updated_at = "2026-09-15T23:12:00Z"
capsule = "Binding loop: Human problem -> hypothesis -> two falsifiers -> map -> counterprobe -> exact source -> prove -> patch -> Workshop -> heartbeat -> fresh run, with a mandatory Memory verification gate before every state."
claim_boundary = "This contract owns the investigation and patch sequence; it does not prove any project hypothesis, implementation fact, Runtime state, test result, or completion claim."
entities = ["KAIROS", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP", "KAIROS_MEMORY_VERIFICATION_PROTOCOL", "hypothesis", "falsification", "context-map", "source-prediction", "causal-proof"]
facets = ["investigation", "falsification", "memory-verification", "retrieval", "source-inspection", "patch-scope", "context-budget"]
criteria = []
does_not_answer = ["current project state", "whether a hypothesis is true", "current source bytes", "whether a patch passed"]

[[answers]]
intent = "operating_rules"
question = "What exact loop must an agent follow from a Human problem through a fresh run?"
target = "s-state-machine"

[[answers]]
intent = "operating_rules"
question = "What must the agent verify in Memory before executing any investigation state?"
target = "s-memory-gate"

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

[refs]
memory_protocol = "[ref:docs/MEMORY_VERIFICATION_PROTOCOL.md#s-zero-guess|id:KAIROS_MEMORY_VERIFICATION_PROTOCOL|v:1|rel:requires|tags:memory,verification,no-guess|src:declared]"
axiom_atlas = "[ref:docs/AXIOM_RUNTIME_ATLAS.md#s-overview|id:AXIOM_RUNTIME_ATLAS|v:1|rel:references|tags:axiom,runtime,architecture|src:declared]"
router = "[ref:NEURAL_CORTEX.md#s-orientation|id:KAIROS_NEURAL_CORTEX|v:dynamic|rel:references|tags:orientation,router|src:system]"

[[search_contract]]
query = "What exact loop must an agent follow from a Human problem through a fresh run?"
expected = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP#s-state-machine"
required_top_k = 3

[[search_contract]]
query = "What must the agent verify in Memory before executing any investigation state?"
expected = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP#s-memory-gate"
required_top_k = 3
+++
# Hypothesis-to-Patch Loop

## CONTEXT INDEX

- [`s-state-machine`](#s-state-machine) — The loop is fixed: Human problem -> hypothesis -> two falsifiers -> map -> counterprobe -> exact source -> prove -> patch -> Workshop -> heartbeat -> fresh run.
- [`s-memory-gate`](#s-memory-gate) — Before every state, intercept every would-be guess/inference and verify the relevant canonical Memory passage; remembered context does not count.
- [`s-frame`](#s-frame) — Convert Human input into one explicit falsifiable problem hypothesis before implementation discovery.
- [`s-falsify`](#s-falsify) — Two independent probes must support the hypothesis before implementation mapping begins.
- [`s-map`](#s-map) — Acquire context from semantic architecture to run evidence to graph to implementation documents before source bytes.
- [`s-counterprobe`](#s-counterprobe) — Try to prove the captured causal surface incomplete before exact source inspection.
- [`s-exact-source`](#s-exact-source) — Predict and inspect only the exact source lines needed to test the bounded hypothesis.
- [`s-prove`](#s-prove) — No patch exists until observation, violated invariant, causal owner, and exact affected scope are evidenced.
- [`s-patch-run`](#s-patch-run) — Apply only the smallest general repair through Workshop, heartbeat it, run fresh, and restart from the top.
- [`s-context-budget`](#s-context-budget) — Route before reading; raw shell output is bounded evidence and never the default discovery mechanism.

<a id="s-state-machine"></a>
## CLOSED STATE MACHINE

> Capsule: The loop is fixed: Human problem -> hypothesis -> two falsifiers -> map -> counterprobe -> exact source -> prove -> patch -> Workshop -> heartbeat -> fresh run.

The only permitted material problem-solving sequence is:

```text
HUMAN PROBLEM
-> HYPOTHESIS
-> FALSIFIER 1
-> FALSIFIER 2
-> MAP
-> COUNTERPROBE
-> EXACT SOURCE
-> PROVE
-> PATCH
-> WORKSHOP
-> HEARTBEAT
-> FRESH RUN
-> RETURN TO HUMAN PROBLEM / HYPOTHESIS FROM FRESH EVIDENCE
```

Before every arrow is crossed, execute the Memory gate below.

No state may be skipped. A failed gate returns to the nearest earlier reasoning state. Failure never licenses a broader raw read, a speculative conclusion, a larger patch, or a shortcut around missing evidence.

<a id="s-memory-gate"></a>
## MANDATORY MEMORY GATE BEFORE EVERY STATE

> Capsule: Before every state, intercept every would-be guess/inference and verify the relevant canonical Memory passage; remembered context does not count.

The agent's default response to uncertainty is verification, not inference.

Before executing any state:

```text
1. State what project-specific fact(s) the next action depends on.
2. Identify any fact that is currently only remembered, assumed, inferred, likely, or guessed.
3. Convert each such fact into one explicit verification question.
4. Query the smallest relevant passage of the Human-authored canonical Memory.
5. Classify the result:
     VERIFIED_MEMORY
     VERIFIED_LIVE
     HYPOTHESIS
     UNRESOLVED
6. If Memory says the fact is live/changeable, verify the named live authority.
7. Execute the state only when every prerequisite fact is verified or explicitly retained as a hypothesis that the state is designed to falsify.
```

Hard rule:

```text
WOULD-BE GUESS OR INFERENCE
-> MEMORY QUESTION
-> RELEVANT PASSAGE
-> VERIFICATION STATUS
-> ACTION
```

Never:

```text
WOULD-BE GUESS
-> plausible conclusion
-> code search / patch
```

For project-specific facts, guessing must trend to zero and silent inference must trend to zero. The model may reason about evidence, but it may not manufacture missing project facts by connecting unverified gaps. If a conclusion requires an unstated edge, owner, stage, file responsibility, Runtime relation, or rule, verify that missing fact first.

The detailed Memory access and escalation rules are owned by `KAIROS_MEMORY_VERIFICATION_PROTOCOL`.

<a id="s-frame"></a>
## HUMAN PROBLEM -> HYPOTHESIS

> Capsule: Convert Human input into one explicit falsifiable problem hypothesis before implementation discovery.

After the Memory gate, record exactly:

1. Human observation/request;
2. expected invariant;
3. suspected violation;
4. expected architecture owner;
5. evidence that would refute the hypothesis.

The hypothesis must be narrow enough to be wrong. It is `HYPOTHESIS`, not fact. Do not inspect source yet.

<a id="s-falsify"></a>
## TWO INDEPENDENT FALSIFIERS

> Capsule: Two independent probes must support the hypothesis before implementation mapping begins.

Perform the Memory gate before each falsifier.

**Falsifier 1 — semantic / authority**

Ask which current contract, invariant, or architecture authority would have to define, permit, forbid, or constrain the suspected behavior if the hypothesis were correct.

**Falsifier 2 — structural / graph**

Ask which declared owner, producer/consumer relation, dependency, call, evidence route, or Runtime path would have to exist if the suspected mechanism were structurally possible.

The two probes must be orthogonal. Rephrasing the same search twice does not count.

Continue only if both support the same hypothesis. If either contradicts or fails to establish the expected condition, return to `HYPOTHESIS` and reformulate from verified evidence.

<a id="s-map"></a>
## MAP

> Capsule: Acquire context from semantic architecture to run evidence to graph to implementation documents before source bytes.

After the Memory gate, map only the implementation surface implicated by the hypothesis that survived both falsifiers.

Use this information-efficient order:

```text
semantic responsibility / AX.RT.*
-> current run-derived evidence when execution participation matters
-> smallest typed graph neighbourhood
-> exact implementation-document sections
-> expected source owners
```

Choose the query that removes the most remaining explanations with the least returned context. Source bytes are high-volume evidence and therefore come late.

Output a bounded map: semantic stage(s), relevant observed run stage/artifact(s), graph nodes/edges, implementation document sections, expected source owners.

<a id="s-counterprobe"></a>
## COUNTERPROBE

> Capsule: Try to prove the captured causal surface incomplete before exact source inspection.

Perform the Memory gate, then explicitly challenge the map:

```text
If this captured context is complete, there must not be another relevant
producer, consumer, implementation owner, bypass, alternate path, or
matching exact-literal location outside it.
```

Use one bounded negative probe. Prefer graph traversal. Use `rg -l` only for an exact literal in an already-bounded filesystem scope.

Unexpected relevant branch -> `MAP` again.
No competing branch established -> `EXACT SOURCE`.

Do not append surprise scope opportunistically.

<a id="s-exact-source"></a>
## EXACT SOURCE

> Capsule: Predict and inspect only the exact source lines needed to test the bounded hypothesis.

Perform the Memory gate, including the source-escalation and bounded-read rules.

Before reading source, predict:

- exact file;
- exact symbol / indexed section;
- expected relation to the mapped mechanism;
- concrete defect signature if the hypothesis is true;
- concrete legitimate implementation shape if it is false.

Then inspect only the governed source lines needed to test that prediction through the existing source-permit/source-search path.

Do not use source as discovery material after the map is already bounded.

<a id="s-prove"></a>
## PROVE

> Capsule: No patch exists until observation, violated invariant, causal owner, and exact affected scope are evidenced.

Perform the Memory gate, especially the relevant invariant, no-guess rule, and general-solution contract.

A patch may begin only when all four are verified:

1. observed defect/mismatch;
2. violated invariant/contract;
3. causal implementation owner/mechanism;
4. exact affected implementation scope.

The evidence must establish why the identified mechanism causes the observation, why the intended correction repairs the general mechanism rather than one dataset/scenario, and why no required owner remains outside the bounded scope.

Any missing link is `UNRESOLVED`, not an invitation to infer it. Return to `MAP`, `COUNTERPROBE`, or `HYPOTHESIS` as appropriate.

<a id="s-patch-run"></a>
## PATCH -> WORKSHOP -> HEARTBEAT -> FRESH RUN

> Capsule: Apply only the smallest general repair through Workshop, heartbeat it, run fresh, and restart from the top.

Perform the Memory gate separately before `PATCH`, `WORKSHOP`, `HEARTBEAT`, and `FRESH RUN`.

**PATCH** — freeze the proven scope. Repair only the smallest general mechanism. No dataset/lane special case, opportunistic refactor, or silent scope widening. New evidence that invalidates scope aborts the patch and returns to `MAP`.

**WORKSHOP** — use the existing governed isolated/shadow/verify/test/apply procedure exactly as owned by the local Workshop rules. This supplemental document does not replace them.

**HEARTBEAT** — promote/reconcile the bounded work and verify receipts, coverage, state, and required postchecks. A passing subcheck proves only its own boundary.

**FRESH RUN** — execute the real Runtime again. The fresh artifacts become the next observation authority. Do not continue from the previous debugging narrative as though it were still current. Re-enter the relevant Memory rules/topology, inspect fresh evidence, and form the next hypothesis from the top.

<a id="s-context-budget"></a>
## CONTEXT-BUDGET DISCIPLINE

> Capsule: Route before reading; raw shell output is bounded evidence and never the default discovery mechanism.

1. Ask the smallest precise question first. Graph resolves structure; search resolves governed prose questions; governed source-search resolves exact bytes.
2. Routine whole-file dumps of governed Memory, Markdown, or source are forbidden for discovery. Do not use `Get-Content`, `cat`, `type`, `ReadAllText`, or equivalents to compensate for an unresolved question.
3. `rg` is a scalpel, not a context loader: use it only for exact-location/completeness work in an already-bounded scope. Prefer `rg -l` when only containing files matter.
4. If evidence is insufficient, sharpen the Memory/Werkfaden question or follow a typed relation. Do not increase output until the whole file is in context.
5. Do not repeat materially identical searches without new evidence or a changed question.
6. Once `PROVE` closes the causal scope, stop searching. Patch the proven mechanism or return to an earlier state if new evidence invalidates the proof.
