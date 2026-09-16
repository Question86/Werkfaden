+++
schema = "kairos-context/v1"
id = "KAIROS_AGENT_ENTRYPOINT"
type = "documentation"
revision = 3
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/KAIROS_AGENT_ENTRYPOINT"
updated_at = "2026-09-16T20:00:00Z"
capsule = "Fresh-agent entrypoint: bind canonical Memory, start Guardian, perform state Memory preflight before every action, and remain inside the evidence-to-patch-session loop."
claim_boundary = "This entrypoint starts the operating method; it does not prove live project state, architecture, implementation, Runtime behavior, or completion."
entities = ["KAIROS_AGENT_ENTRYPOINT", "KAIROS_HYPOTHESIS_TO_PATCH_LOOP", "KAIROS_MEMORY_VERIFICATION_PROTOCOL", "Werkfaden Investigation Guardian"]
facets = ["entrypoint", "bootstrap", "memory-verification", "guardian", "patch-session"]
criteria = []
does_not_answer = ["current project state", "current source bytes", "whether a hypothesis is true"]

[[answers]]
intent = "orientation"
question = "How must a fresh agent start work in this workspace?"
target = "s-start"

[[answers]]
intent = "operating_rules"
question = "What mechanically gates every investigation state and coding transaction?"
target = "s-guardian"
+++
# START HERE — FRESH AGENT ENTRYPOINT

## CONTEXT INDEX

- [`s-start`](#s-start) — Bind exact workspace and canonical Memory authority, then start one Guardian session for the Human problem.
- [`s-zero-guess`](#s-zero-guess) — Would-be project guesses become Memory/live-authority questions.
- [`s-guardian`](#s-guardian) — Every state starts with `guard-enter`; every patch transaction belongs to the same proven patch session.
- [`s-loop`](#s-loop) — Human problem through fresh run remains one closed sequence.

<a id="s-start"></a>
## START CONTRACT

> Capsule: Bind exact workspace and canonical Memory authority, then start one Guardian session for the Human problem.

A fresh agent receives the workspace root, the exact Human-authored canonical Memory path, and the Human problem. Do not discover or guess the Memory path.

Before material work:

1. read this entrypoint;
2. read `docs/MEMORY_VERIFICATION_PROTOCOL.md` and `docs/HYPOTHESIS_TO_PATCH_LOOP.md`;
3. use `workshop/GUARDIAN.md` for the machine-enforced command contract;
4. confirm the canonical Workshop config and Guardian Memory/state-selector authority;
5. start exactly one Guardian session for the Human problem;
6. call `guard-enter HYPOTHESIS` **before** framing the hypothesis;
7. continue state by state; never read source or open a Workshop work tree early.

Routine full reads of the canonical Memory are forbidden. Guardian preflight resolves only the configured passages for the next state and returns them as bounded context.

<a id="s-zero-guess"></a>
## ZERO-GUESS ENTRY RULE

> Capsule: Would-be project guesses become Memory/live-authority questions.

Before acting on a project-specific proposition, ask what fact the action would otherwise assume. Convert every remembered/likely/inferred project fact into a verification question. Durable rules come from the relevant Memory passage; live/current facts come from the owner named by Memory.

```text
would-be guess
-> guard-enter state
-> relevant canonical Memory passage(s)
-> live authority when required
-> VERIFIED / HYPOTHESIS / UNRESOLVED
-> state action
```

`HYPOTHESIS` and `UNRESOLVED` may drive the next query. They may not authorize architecture claims, causal proof, mutation scope or completion.

<a id="s-guardian"></a>
## DETERMINISTIC GUARDIAN

> Capsule: Every state starts with `guard-enter`; every patch transaction belongs to the same proven patch session.

The Guardian is not an LLM judge. It enforces evidence order and mutation authority.

For reasoning states:

```text
guard-enter STATE
-> Memory context + GST_<ticket>
-> perform that state's query/reasoning
-> guard-step STATE --state-ticket GST_<ticket>
```

Search/source receipts used by the state must be generated after and bound to that ticket. Under Guardian enforcement, supported KAIROS source-permit/source-search calls refuse early source escalation.

After PROVE, one patch session may own many Workshop transactions. Before each TX:

```text
guard-enter PATCH
-> PATCH + WORKSHOP Memory context
-> GST_<patch-ticket>
-> guarded checkout using same session/ticket
-> prepare / verify / apply
-> real terminal postcheck
-> next PATCH ticket or HEARTBEAT when scope is exhausted
```

The patch session maintains rolling package identity plus available/consumed proven scope. A consumed file cannot be reopened under the same proof. New required scope means reframe to MAP/HYPOTHESIS.

<a id="s-loop"></a>
## MANDATORY LOOP

> Capsule: Human problem through fresh run remains one closed sequence.

```text
HUMAN PROBLEM
-> HYPOTHESIS
-> FALSIFIER 1
-> FALSIFIER 2
-> MAP
-> COUNTERPROBE
-> EXACT SOURCE
-> PROVE
-> PATCH SESSION (TX1..TXn)
-> HEARTBEAT / PATCH CLOSE
-> FRESH RUN
-> RETURN TO TOP FROM FRESH EVIDENCE
```

FALSIFIER 1 is semantic/authority evidence. FALSIFIER 2 is independent structural/graph evidence. Source is late. Mutation is later.

`guard-enter FRESH_RUN` happens before executing the real Runtime. Closure requires a content-bound fresh-run receipt tied to the final patch package and heartbeat. A `STILL_PRESENT` or `INCONCLUSIVE` result closes the old investigation just as a `FIXED` result does; the next hypothesis is formed from the fresh artifacts, not inherited narrative.

The Guardian closes supported application mutation paths. It is not an OS sandbox: direct native filesystem/database writes remain owned by the Workshop security boundary, filesystem ACL/service identity, and a later model-facing hook.
