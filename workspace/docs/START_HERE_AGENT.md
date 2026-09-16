+++
schema = "kairos-context/v1"
id = "KAIROS_AGENT_ENTRYPOINT"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_AGENT_ENTRYPOINT"
updated_at = "2026-09-16T12:35:00Z"
capsule = "Single entry point for a fresh coding agent: bind the canonical Memory, enter the deterministic Guardian, verify relevant Memory passages, then remain inside the hypothesis-to-fresh-run loop."
claim_boundary = "This entrypoint starts the operating method. It does not prove project state, architecture, implementation, Runtime behavior, or completion."
entities = ["KAIROS_AGENT_ENTRYPOINT", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP", "KAIROS_MEMORY_VERIFICATION_PROTOCOL", "AXIOM_RUNTIME_ATLAS", "Werkfaden Investigation Guardian"]
facets = ["entrypoint", "bootstrap", "memory-verification", "investigation-loop", "guardian"]
criteria = []
does_not_answer = ["current project state", "current source bytes", "whether a hypothesis is true"]

[[answers]]
intent = "orientation"
question = "How must a fresh agent start work in this workspace?"
target = "s-start"

[[answers]]
intent = "operating_rules"
question = "What is the mandatory loop after startup?"
target = "s-loop"

[[answers]]
intent = "operating_rules"
question = "What mechanically gates coding after the investigation loop?"
target = "s-guardian"

[refs]
memory_protocol = "[ref:docs/MEMORY_VERIFICATION_PROTOCOL.md#s-zero-guess|id:KAIROS_MEMORY_VERIFICATION_PROTOCOL|v:1|rel:requires|tags:memory,verification,no-guessing|src:declared]"
loop = "[ref:docs/HYPOTHESIS_TO_PATCH_LOOP.md#s-state-machine|id:KAIROS_HYPOTHESIS_TO_PATCH_LOOP|v:1|rel:requires|tags:investigation,falsification,patch|src:declared]"
atlas = "[ref:docs/AXIOM_RUNTIME_ATLAS.md#s-overview|id:AXIOM_RUNTIME_ATLAS|v:1|rel:references|tags:architecture,axiom,runtime|src:declared]"

[[search_contract]]
query = "How must a fresh agent start work in this workspace?"
expected = "KAIROS_AGENT_ENTRYPOINT#s-start"
required_top_k = 1
+++
# START HERE — FRESH AGENT ENTRYPOINT

## CONTEXT INDEX

- [`s-start`](#s-start) — Bind the exact canonical Memory file supplied by the Human, then load only the relevant passages before doing anything material.
- [`s-zero-guess`](#s-zero-guess) — Any fact the agent would otherwise guess, infer, assume, or remember must first be checked against the canonical Memory or the live authority named by it.
- [`s-loop`](#s-loop) — Stay inside one closed Human-problem-to-fresh-run loop.
- [`s-guardian`](#s-guardian) — When enabled, the Workshop Guardian records the loop in order and refuses coding checkout before PROVE.
- [`s-first-action`](#s-first-action) — The first action is orientation and Memory verification, never source inspection or mutation.

<a id="s-start"></a>
## START CONTRACT

> Capsule: Bind the exact canonical Memory file supplied by the Human, then load only the relevant passages before doing anything material.

A fresh agent starts with exactly two externally supplied facts:

1. the workspace root;
2. the exact path of the Human-authored canonical KAIROS/AXIOM Memory file.

Do not discover or guess the Memory path. The Human must provide it explicitly in the kickstart instruction.

Before any material action:

1. read this entrypoint;
2. read `docs/MEMORY_VERIFICATION_PROTOCOL.md`;
3. read `docs/HYPOTHESIS_TO_PATCH_LOOP.md`;
4. for AXIOM Runtime work, read only the relevant section(s) of `docs/AXIOM_RUNTIME_ATLAS.md`;
5. resolve the relevant section(s) of the canonical Memory;
6. if the local Workshop Guardian is enabled, start one Guardian session for the Human problem before entering `HYPOTHESIS`;
7. only then continue with the current loop state.

Never read the whole canonical Memory by default. Use it as an addressable semantic kernel.

<a id="s-zero-guess"></a>
## ZERO-GUESS ENTRY RULE

> Capsule: Any fact the agent would otherwise guess, infer, assume, or remember must first be checked against the canonical Memory or the live authority named by it.

Before acting on a project-specific proposition, ask:

```text
What am I about to assume?
```

If the answer contains any project-specific fact not already verified in the current step, convert that would-be assumption into an explicit verification question.

Then:

```text
would-be guess / inference
-> canonical Memory lookup
-> if Memory owns the durable fact: verify the relevant passage
-> if Memory points to live authority: query that authority
-> VERIFIED or UNRESOLVED
```

`UNRESOLVED` may trigger the next query. It may not be silently promoted into architecture, cause, scope, or patch rationale.

Remembered context, conversation history, filename intuition, common software practice, and model knowledge are not substitutes for verification.

<a id="s-loop"></a>
## MANDATORY LOOP

> Capsule: Stay inside one closed Human-problem-to-fresh-run loop.

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
-> RETURN TO TOP
```

Before every state, re-check the Memory passages relevant to that state. Do not rely on having read them earlier in the session.

The detailed gates and failure returns are owned by `docs/HYPOTHESIS_TO_PATCH_LOOP.md`.

<a id="s-guardian"></a>
## DETERMINISTIC CODING GUARDIAN

> Capsule: When enabled, the Workshop Guardian records the loop in order and refuses coding checkout before PROVE.

The Guardian is mechanical sequence enforcement, not an LLM judge. For each state it records the state summary, the Memory passage references re-checked for that state, and the evidence handles supplied by the investigation.

Its hard pre-patch properties are:

1. states must be entered in the declared order;
2. every state must name at least one relevant canonical-Memory passage/reference;
3. `EXACT_SOURCE` must carry a real `SIR_...` receipt already verified by KAIROS;
4. canonical Memory identity and the sealed governed package may not drift while the pre-patch investigation is open;
5. `PROVE` freezes the exact governed source scope;
6. normal Workshop `checkout` is refused before `PROVE` when Guardian enforcement is enabled;
7. checkout source scope must exactly equal the scope frozen by `PROVE`;
8. `PATCH` cannot be entered with `guard-step`; successful guarded Workshop checkout is the only transition into `PATCH`.

Repository-level usage and commands are documented in `workshop/GUARDIAN.md`. The Guardian cannot prove that an LLM's semantic reasoning is true and cannot stop a process with unrestricted OS write access from bypassing Workshop entirely. Those are separate hook/ACL boundaries.

<a id="s-first-action"></a>
## FIRST ACTION

> Capsule: The first action is orientation and Memory verification, never source inspection or mutation.

When given a Human problem:

1. identify which durable project facts you would need to assume in order to frame the problem;
2. verify those facts against the relevant canonical Memory passages;
3. resolve any Memory pointer that requires live authority;
4. when Guardian enforcement is active, bind the Human problem and exact canonical Memory path with `guard-start`;
5. frame one falsifiable hypothesis;
6. record `HYPOTHESIS` in the Guardian and begin the two-falsifier sequence.

Forbidden as a first action:

- broad `Get-Content`, `cat`, `type`, or equivalent whole-file reads;
- broad `rg` or repository grep;
- source browsing without a mapped reason;
- patch planning;
- code mutation.
