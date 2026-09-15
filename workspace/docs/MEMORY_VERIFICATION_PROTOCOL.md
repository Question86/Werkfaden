+++
schema = "kairos-context/v1"
id = "KAIROS_MEMORY_VERIFICATION_PROTOCOL"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_MEMORY_VERIFICATION_PROTOCOL"
updated_at = "2026-09-15T23:10:00Z"
capsule = "Memory-first verification protocol: intercept every project-specific guess or inference, convert it into an explicit question, verify the relevant canonical Memory passage, and escalate to live authority when Memory cannot prove the claim."
claim_boundary = "This protocol governs how the agent converts uncertainty into verified context. It does not make Memory live-state authority and does not prove implementation, runtime, test, or completion claims."
entities = ["KAIROS_MEMORY_VERIFICATION_PROTOCOL", "canonical-memory", "zero-guess", "verification", "AX.RT"]
facets = ["memory", "verification", "no-guessing", "context-budget", "routing"]
criteria = []
does_not_answer = ["current source bytes", "current Runtime state", "whether a hypothesis is true", "whether a patch passed"]

[[answers]]
intent = "operating_rules"
question = "What must the agent do before making a project-specific guess or inference?"
target = "s-zero-guess"

[[answers]]
intent = "retrieval"
question = "How should the agent use the large canonical Memory without reading it in full?"
target = "s-memory-query"

[[answers]]
intent = "authority"
question = "When does Memory stop being enough and live authority become required?"
target = "s-escalation"

[[answers]]
intent = "retrieval"
question = "Which Memory areas should be consulted during the investigation loop?"
target = "s-state-routing"

[[search_contract]]
query = "What must the agent do before making a project-specific guess or inference?"
expected = "KAIROS_MEMORY_VERIFICATION_PROTOCOL#s-zero-guess"
required_top_k = 3
+++
# Memory Verification Protocol

## CONTEXT INDEX

- [`s-zero-guess`](#s-zero-guess) — Every project-specific guess or inference is intercepted and converted into a verification question before it may influence action.
- [`s-memory-query`](#s-memory-query) — Query only the smallest relevant canonical Memory passage; never dump the whole Memory as routine context.
- [`s-evidence-status`](#s-evidence-status) — Distinguish verified fact, explicit hypothesis, unresolved question, and live-state claim.
- [`s-escalation`](#s-escalation) — Memory or current conversation may orient, but current-state and implementation claims require the live owning authority.
- [`s-state-routing`](#s-state-routing) — Each investigation state has a minimum Memory verification set and may add only problem-specific sections.
- [`s-stop-rules`](#s-stop-rules) — Missing verification blocks execution; uncertainty is resolved by another precise query, not by inference or a larger raw read.

<a id="s-zero-guess"></a>
## ZERO-GUESS / ZERO-INFERENCE RULE

> Capsule: Every project-specific guess or inference is intercepted and converted into a verification question before it may influence action.

For project-specific architecture, rules, ownership, state, behavior, file responsibility, stage participation, data flow, evidence, or mutation scope, the agent MUST NOT fill a gap from plausibility, naming, memory of an earlier turn, general software knowledge, or a likely-looking file.

Whenever the agent is about to think or act on a statement of the form:

```text
"probably ..."
"this should be ..."
"that likely means ..."
"the detector must ..."
"this file appears to own ..."
"the next stage is probably ..."
"I can infer ..."
```

it MUST instead perform this transformation:

```text
WOULD-BE GUESS
-> explicit verification question
-> smallest relevant canonical Memory lookup
-> evidence status
-> only then continue
```

The objective is not to suppress hypotheses. Hypotheses are allowed only when explicitly labeled as hypotheses and immediately routed into falsification. What is forbidden is silently promoting a hypothesis or inference into project fact.

No verified passage -> no project-fact claim.
No project-fact claim -> no action that depends on it.

<a id="s-memory-query"></a>
## QUERY THE MEMORY, DO NOT READ THE MEMORY

> Capsule: Query only the smallest relevant canonical Memory passage; never dump the whole Memory as routine context.

The Human-authored canonical Memory is the cross-system orientation kernel connecting AXIOM architecture, Werkfaden/KAIROS operating method, goals, invariants, Runtime topology, Workshop procedure, and evidence rules.

Use it as an indexed knowledge source rather than a linear text file.

For each uncertainty:

1. formulate one precise question representing the fact the agent would otherwise guess;
2. locate the smallest Memory heading / indexed passage that can answer it;
3. read only that passage plus the minimum directly linked passage needed to interpret it;
4. record whether the passage establishes, contradicts, or does not establish the claim;
5. if not established, do not widen the Memory read arbitrarily; ask the next narrower question or escalate to the live owner.

Routine full-file reads of the canonical Memory are forbidden. Full Memory reads are reserved for explicit Memory audit/reconciliation work requested by the Human.

A Memory lookup is successful only when the returned passage answers the actual question. Keyword presence alone is not verification.

<a id="s-evidence-status"></a>
## EVIDENCE STATUS

> Capsule: Distinguish verified fact, explicit hypothesis, unresolved question, and live-state claim.

Every project-specific statement used for reasoning must belong to one of four states:

- `VERIFIED_MEMORY` — directly supported by a relevant canonical Memory passage and within that passage's claim boundary;
- `VERIFIED_LIVE` — directly supported by the current owning source, run evidence, graph evidence, implementation documentation, or exact governed source bytes;
- `HYPOTHESIS` — explicit, falsifiable, and not yet treated as fact;
- `UNRESOLVED` — neither Memory nor live authority establishes the statement.

`HYPOTHESIS` and `UNRESOLVED` may guide the next verification query. They may not authorize source mutation, causal claims, architecture claims, or completion claims.

<a id="s-escalation"></a>
## MEMORY -> LIVE AUTHORITY ESCALATION

> Capsule: Memory or current conversation may orient, but current-state and implementation claims require the live owning authority.

Memory owns durable orientation, stable project rules, durable architecture synthesis, known authority locations, and retrieval method. It does not override live state.

Always escalate from Memory to the live owner before claiming any of the following as current:

- current task / criterion / closure state;
- current branch, HEAD, hashes, seals, transaction state, test result, or completion result;
- exact current Runtime execution order or newest controlled-run participation;
- exact source bytes or current implementation owner when the live code/document graph can differ;
- current database census, coverage, promotion state, blockers, or freshness state.

The safe chain is:

```text
Memory establishes what should be checked and where
-> Werkfaden verifies the current owner
-> exact current evidence establishes the claim
```

Never reverse that relationship by treating Memory as proof of a changing live fact.

<a id="s-state-routing"></a>
## MEMORY ROUTING BY INVESTIGATION STATE

> Capsule: Each investigation state has a minimum Memory verification set and may add only problem-specific sections.

Before every state in `KAIROS_HYPOTHESIS_TO_PATCH_LOOP`, perform a Memory check. Read the fixed minimum set below plus only the problem-specific architecture/rule passage required by the state.

```text
HUMAN PROBLEM
  -> no-guess rule
  -> live-authority rule
  -> top-level relevant architecture router

HYPOTHESIS
  -> no-guess rule
  -> relevant invariant / architecture boundary
  -> general-solution rule when the problem concerns a defect

FALSIFIER 1
  -> relevant contract / invariant / authority passage

FALSIFIER 2
  -> relevant architecture / topology passage
  -> retrieval-router rule for graph use

MAP
  -> relevant AX.RT.* semantic responsibility
  -> retrieval-router rules
  -> current-run freshness rule before using a run snapshot as current

COUNTERPROBE
  -> no-guess rule
  -> graph/search/file-location routing rule

EXACT SOURCE
  -> source-escalation rule
  -> bounded-read rule
  -> relevant architecture boundary

PROVE
  -> violated invariant
  -> general-solution contract
  -> no-guess rule

PATCH
  -> general-solution contract
  -> mutation/workshop boundary
  -> exact proven scope

WORKSHOP
  -> Workshop procedure and security boundary

HEARTBEAT
  -> heartbeat / promotion / completion-boundary rules

FRESH RUN
  -> live-authority rule
  -> run-derived architecture freshness rule
  -> reset-to-top rule
```

The state-specific Memory check is mandatory even when the model believes it already remembers the rule from earlier context. Remembered context is not verification.

<a id="s-stop-rules"></a>
## STOP RULES

> Capsule: Missing verification blocks execution; uncertainty is resolved by another precise query, not by inference or a larger raw read.

Stop the current state when:

- the relevant Memory passage cannot be located;
- two Memory passages appear to conflict and no authority rule resolves them;
- the Memory passage explicitly says the fact must be re-read from live authority;
- the query result is too broad to support the exact claim;
- the agent would need to infer a missing edge, owner, stage, path, or behavior.

Then do one of two things only:

1. ask a sharper Memory/Werkfaden question; or
2. move to the named live authority using the bounded retrieval path.

Do not compensate for uncertainty by reading an entire file, scanning a larger tree, widening grep output, or making a speculative patch.
