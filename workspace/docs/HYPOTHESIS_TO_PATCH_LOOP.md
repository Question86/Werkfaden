+++
schema = "kairos-context/v1"
id = "KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
type = "documentation"
revision = 3
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_HYPOTHESIS_TO_PATCH_LOOP"
updated_at = "2026-09-16T20:00:00Z"
capsule = "Binding investigation loop with Memory preflight before every state and a multi-transaction patch session after causal proof."
claim_boundary = "This contract owns investigation and mutation ordering; it does not prove a hypothesis, current source bytes, Runtime outcome, test result, or completion claim."
entities = ["KAIROS", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP", "Werkfaden Investigation Guardian", "patch-session", "falsification"]
facets = ["investigation", "falsification", "memory-verification", "patch-session", "retrieval", "source-inspection"]
criteria = []
does_not_answer = ["current project state", "whether a hypothesis is true", "current source bytes", "whether a patch passed"]

[[answers]]
intent = "operating_rules"
question = "What exact loop must an agent follow from a Human problem through a fresh run?"
target = "s-loop"

[[answers]]
intent = "operating_rules"
question = "What must happen before each Guardian state executes?"
target = "s-preflight"

[[answers]]
intent = "validation"
question = "What must be proven before a patch session may open?"
target = "s-prove"

[[answers]]
intent = "mutation"
question = "Can one proven patch use multiple Workshop transactions?"
target = "s-patch-session"
+++
# Hypothesis-to-Patch Loop

## CONTEXT INDEX

- [`s-loop`](#s-loop) — Human problem through fresh run is one fixed evidence-first loop.
- [`s-preflight`](#s-preflight) — Every state starts with `guard-enter`, which returns the relevant canonical Memory passages before the state action occurs.
- [`s-hypothesis`](#s-hypothesis) — One explicit falsifiable hypothesis is formed from verified prerequisites.
- [`s-falsifiers`](#s-falsifiers) — Semantic authority and structural graph evidence independently attack the hypothesis.
- [`s-map`](#s-map) — Context narrows from architecture/evidence/graph to implementation docs and only then source.
- [`s-counterprobe`](#s-counterprobe) — A negative completeness probe attacks the proposed causal surface.
- [`s-source`](#s-source) — Exact source is predicted and inspected through the state-bound source policy.
- [`s-prove`](#s-prove) — Observation, violated invariant, causal owner and exact mutation scope gate patching.
- [`s-patch-session`](#s-patch-session) — One proof may authorize TX1..TXn without allowing scope growth or source reopening.
- [`s-close`](#s-close) — Real Workshop postchecks/heartbeat and a content-bound fresh run close the investigation.

<a id="s-loop"></a>
## CLOSED LOOP

> Capsule: Human problem through fresh run is one fixed evidence-first loop.

```text
HUMAN PROBLEM
-> HYPOTHESIS
-> FALSIFIER 1
-> FALSIFIER 2
-> MAP
-> COUNTERPROBE
-> EXACT SOURCE
-> PROVE
-> PATCH SESSION
     -> TX1 .. TXn
-> HEARTBEAT / PATCH CLOSE
-> FRESH RUN
-> RETURN TO TOP FROM FRESH EVIDENCE
```

No later state may be entered early. A failed falsifier or newly discovered owner returns to `HYPOTHESIS` or `MAP`; it never grants a larger read or larger patch.

<a id="s-preflight"></a>
## MEMORY PREFLIGHT BEFORE ACTION

> Capsule: Every state starts with `guard-enter`, which returns the relevant canonical Memory passages before the state action occurs.

The Memory rule is temporal: verification must occur **before** the model performs the state action.

```text
guard-enter STATE
-> Guardian resolves configured state-relevant Memory selectors
-> exact passage bytes are hashed and returned as memory_context
-> Guardian issues GST_<state-ticket>
-> model performs only that state's action/query
-> resulting receipts/evidence are bound to that ticket
-> guard-step / checkout / close consumes the same ticket
```

A project-specific fact that would otherwise be guessed, inferred, assumed or remembered becomes a verification question. Remembered conversation context is not verification. If Memory says a fact is live-owned, the named live authority must be checked.

<a id="s-hypothesis"></a>
## HYPOTHESIS

> Capsule: One explicit falsifiable hypothesis is formed from verified prerequisites.

After `guard-enter HYPOTHESIS`, record a structured hypothesis containing:

1. Human observation;
2. expected invariant;
3. suspected mechanism;
4. expected owner;
5. evidence that would refute it.

The hypothesis is not a project fact and cannot authorize source inspection or mutation.

<a id="s-falsifiers"></a>
## TWO INDEPENDENT FALSIFIERS

> Capsule: Semantic authority and structural graph evidence independently attack the hypothesis.

**FALSIFIER 1 — semantic / authority.** Ask which current contract, invariant or authority would have to permit/forbid/constrain the suspected behavior.

**FALSIFIER 2 — structural / graph.** Ask which owner, producer/consumer relation, dependency or evidence route must exist if the mechanism is structurally possible.

The receipts must be independent. Reusing the same query/receipt does not count. Stale, unscoped, truncated, unbound or unresolved evidence cannot advance the state.

<a id="s-map"></a>
## MAP

> Capsule: Context narrows from architecture/evidence/graph to implementation docs and only then source.

Use the smallest authority surface in this order:

```text
semantic responsibility / stable architecture
-> current run evidence when participation matters
-> smallest typed graph neighbourhood
-> exact implementation-document sections
-> predicted source owners
```

MAP freezes the expected source/structural surface that later PROVE may not silently exceed.

<a id="s-counterprobe"></a>
## COUNTERPROBE

> Capsule: A negative completeness probe attacks the proposed causal surface.

Challenge the map: if it is complete, there must not be another relevant producer, consumer, owner, bypass or alternate path outside it. Prefer graph/metadata evidence; exact source negative proof is admitted only during the COUNTERPROBE state gate. Any surprise returns to MAP.

<a id="s-source"></a>
## EXACT SOURCE

> Capsule: Exact source is predicted and inspected through the state-bound source policy.

Before source bytes, predict exact file, symbol/section, expected defect signature and legitimate refuting shape. Under Guardian enforcement, the supported KAIROS source-permit/source-search API refuses ordinary exact-source escalation unless the active gate is `EXACT_SOURCE`; exception permits are not admissible as proof.

Accepted SIR evidence must be current, task/criterion aligned, state-ticket bound, stable, non-truncated and created after the state Memory preflight. Exact matches create bounded edit windows used later by the Workshop guard.

<a id="s-prove"></a>
## PROVE

> Capsule: Observation, violated invariant, causal owner and exact mutation scope gate patching.

A patch session may open only when all are evidenced:

1. observed defect/mismatch;
2. violated invariant/contract;
3. causal implementation owner/mechanism;
4. exact affected mutation scope;
5. why the proposed repair is the smallest general mechanism correction.

The scope can explicitly include normal sources/headers/tests, auxiliary authority documents, static source-set operations and C-family header-authority operations. Missing scope is unresolved evidence, not permission to widen later.

<a id="s-patch-session"></a>
## PATCH SESSION — TX1..TXn

> Capsule: One proof may authorize TX1..TXn without allowing scope growth or source reopening.

`PATCH != TX`.

PROVE opens one patch contract with `available` and `consumed` scope plus a rolling package identity. Before **each** transaction, run `guard-enter PATCH`; only then may the Workshop create the transaction work tree.

Rules:

- one active TX at a time;
- TX(n+1) waits for TX(n) to terminalize;
- successful `POSTCHECK_VERIFIED` consumes only its actual proven direct scope and advances the rolling package hash;
- abort/rollback consumes nothing;
- consumed scope cannot be reopened under the same proof;
- source/header/test edits outside exact-source inspected windows fail before live apply;
- deterministic regenerated ledgers/topology/projection files are derived mutation and remain work-package hashed;
- new causal scope requires `guard-reframe` to MAP/HYPOTHESIS, not an opportunistically wider transaction.

<a id="s-close"></a>
## PATCH CLOSE, HEARTBEAT, FRESH RUN

> Capsule: Real Workshop postchecks/heartbeat and a content-bound fresh run close the investigation.

When all proven scope is consumed, `guard-enter HEARTBEAT` precedes patch close. `guard-patch-close` validates the real final `POSTCHECK_VERIFIED` transaction and its exact verified Workshop heartbeat; model-supplied completion prose is not evidence.

Then `guard-enter FRESH_RUN` occurs **before** the real Runtime execution. `guard-fresh-run` accepts only a content-bound receipt tied to the final package, closing heartbeat, Guardian state ticket/head, executor, artifacts, active-criterion problem evidence and any project-required validation receipts.

Fresh-run outcome may be `FIXED`, `STILL_PRESENT` or `INCONCLUSIVE`. In every case the old investigative frontier closes and the next investigation starts from fresh evidence at the top.

## CONTEXT DISCIPLINE

Graph resolves structure. Search resolves governed questions. Source-search resolves exact bytes. Whole-file reads and broad grep are not uncertainty-management strategies. Once PROVE closes causal scope, stop searching; either patch that proof or reframe it.
