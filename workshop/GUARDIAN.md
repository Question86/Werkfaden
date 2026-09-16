# Werkfaden Investigation Guardian v2

The Guardian is the deterministic authority between investigation and mutation. It does not decide whether an LLM's explanation is true. It proves that the supported mutation path is entered only from a recorded evidence chain, that every Workshop transaction belongs to one proven patch session, and that each transaction remains inside the scope frozen by `PROVE`.

## Control-plane rule

When Guardian enforcement is active, the public `WorkshopEngine` is the enforcement boundary. CLI checks are convenience only. Direct use of `WorkshopEngine`, auxiliary transactions, static source-set transactions and authority migrations inherit the same lease/state guard. The legacy v1 engine/CLI/Guardian sources are retained under `runtime_sync_workshop/quarantine/` as non-importable `.txt` evidence and are not a supported API.

A bound project has exactly one Workshop control-plane config: `<workspace>/.kairos/workshop.config.json`. A second config pointing at the same project/workspace is rejected.

## Required investigation

```text
HUMAN PROBLEM
-> HYPOTHESIS
-> FALSIFIER 1     semantic / authority SRR
-> FALSIFIER 2     independent graph-routed SRR
-> MAP              graph-routed SRR(s) + expected source owners
-> COUNTERPROBE     explicit negative SRR/SIR
-> EXACT SOURCE     current positive non-truncated SIR(s)
-> PROVE            structured causal proof + frozen patch scope
-> PATCH SESSION
```

`HYPOTHESIS` and `PROVE` are structured JSON, not free prose. `PROVE` does not accept an opaque `proof:*` string: it binds the already validated evidence chain.

Evidence must be created after the state that calls for it. A receipt from before the Guardian session, from a different task/criterion, from an exception permit, from a truncated search, or from an earlier state is rejected.

## Canonical Memory

Guardian enforcement requires one canonical Memory path and a state-to-selector contract. They may be supplied in the canonical Workshop config:

```json
{
  "guardian_required": true,
  "guardian_memory_file": "D:/project/MEMORY.md",
  "guardian_memory_requirements": {
    "HUMAN_PROBLEM": ["MEM.RULE.NO_GUESS"],
    "HYPOTHESIS": ["MEM.RULE.NO_GUESS"],
    "FALSIFIER_1": ["MEM.RULE.LIVE_AUTH"],
    "FALSIFIER_2": ["MEM.RET.GRAPH"],
    "MAP": ["MEM.RET.GRAPH"],
    "COUNTERPROBE": ["MEM.RULE.NO_GUESS"],
    "EXACT_SOURCE": ["MEM.RET.SOURCE"],
    "PROVE": ["MEM.RULE.GENERAL_FIX"],
    "PATCH": ["MEM.RULE.GENERAL_FIX"],
    "WORKSHOP": ["MEM.WORKSHOP"],
    "HEARTBEAT": ["MEM.WORKSHOP"],
    "FRESH_RUN": ["MEM.RULE.LIVE_AUTH"]
  }
}
```

For an existing sealed workspace, the environment can force the Guardian on without rewriting machine authority:

```text
WERKFADEN_GUARDIAN_REQUIRED=1
WERKFADEN_GUARDIAN_MEMORY_FILE=<exact Memory path>
WERKFADEN_GUARDIAN_MEMORY_REQUIREMENTS_FILE=<JSON requirements path>
```

The environment can force enforcement on; it cannot force a config-level requirement off.

For every state, Memory is a **preflight**, not a field on the completion command. `guard-enter --step <STATE>` resolves the mandatory configured selectors plus any explicit extras, rejects missing/ambiguous passages, hashes the exact Markdown section bytes, returns their bounded text as `memory_context`, and issues a one-state `GST_...` ticket. Only after that return may the agent perform the state action. `guard-step` must consume that exact ticket, and receipts used by the state must have been created after the ticket opened. This prevents the model from writing the hypothesis/query first and citing Memory afterwards.

## Two-phase state gate

Every executable reasoning state is two-phase:

```text
guard-enter STATE
  -> Memory passages returned to model
  -> GST_<ticket>
  -> model performs only that state's query/reasoning

guard-step STATE --state-ticket GST_<ticket>
  -> validates evidence created after the Memory gate
  -> advances state
```

`PATCH` uses the same pattern. `guard-enter PATCH` returns the PATCH + WORKSHOP rules before any transaction work tree exists. The returned ticket is required by `checkout`, `auxiliary-checkout`, `source-set-checkout`, or `authority-checkout`. After each terminal transaction the session returns either to `PATCH` for another TX or to `HEARTBEAT` when proven scope is exhausted.

`FRESH_RUN` is also preflighted: `guard-enter FRESH_RUN` returns live-authority/reset rules and a ticket **before** the Runtime is executed. The fresh-run receipt must contain that ticket and finish after it opened.

## Patch session, not transaction

`PROVE` opens one immutable patch contract. A patch session can contain many Workshop transactions:

```text
PROVE
  -> PATCH SESSION OPEN
       -> TX1 -> POSTCHECK_VERIFIED
       -> TX2 -> POSTCHECK_VERIFIED
       -> ...
       -> TXn -> POSTCHECK_VERIFIED
  -> PATCH CLOSE
  -> FRESH RUN
```

The patch session owns the proven mutation scope, `available` and `consumed` scope, one active transaction, the original and rolling package identities, and every transaction/heartbeat receipt. A later transaction may use only still-available scope. A source already consumed by TX1 cannot be reopened by TX3 under the same proof. New evidence that requires it again means the proof is incomplete: use `guard-reframe` and return to `MAP` or `HYPOTHESIS`.

Failed, aborted or rolled-back transactions consume no proven scope. TX(n+1) cannot start while TX(n) is active.

## Scope contract

Simple source-only patches may still pass `--source` at `PROVE`. Mixed mutation classes use `werkfaden-patch-scope/v1` and explicitly name normal sources/headers/tests, auxiliary documents, static source-set operations, and C-family header-authority operations. Mechanical Workshop outputs such as regenerated blueprints, source-index updates and machine-authority projections are recorded separately as derived mutation and remain covered by the verified work-package hash.

## Engine-level transaction enforcement

With Guardian required, a patch session reserves one transaction scope; `_acquire_lease` refuses a transaction without that reservation and binds its TX ID immediately after lease acquisition; every `_require_lease` validates the exact Guardian owner; every transaction state write is observed; before `APPLYING`, actual prepared mutation scope must remain inside the proven reservation; verified work-package hashing prevents post-verify mutation; terminal release advances or preserves the patch scope from real Workshop state. The same boundary applies when the public Python API is used directly.

## KAIROS source-inspection bridge

When Guardian enforcement is active, KAIROS source escalation is state-gated before bytes are exposed through the supported source-permit/search API:

- `negative_proof` is admissible only while the active Guardian gate is `COUNTERPROBE`;
- normal implementation/exact-location/contradiction source permits are admissible only at `EXACT_SOURCE`;
- source-inspection exception permits are refused during guarded development;
- SRR/SIR receipts carry the exact `guardian_id`, `state_ticket_id` and state gate that produced them;
- later Guardian states reject receipts from another gate/session or receipts that predate the state Memory gate.

This closes the supported KAIROS early-source shortcut. Native shell/file reads remain a separate hook/ACL boundary.

## Structural transaction classes

Ordinary existing-file changes, static source add/remove/rename, governed C-family header-set topology, and configured process/authority documents all use the same engine-level Guardian lease/state boundary. Bootstrap repair is a pre-seal maintenance path and is unavailable while Guardian enforcement is active.

## Patch completion

A transaction ID is not Workshop completion. `guard-patch-close` requires no active transaction, no unconsumed proven scope, at least one `POSTCHECK_VERIFIED` transaction, the current package to equal the rolling identity, and the exact final Workshop heartbeat to exist as a verified project DB receipt. Only then does the session reach `FRESH_RUN`.

## Fresh-run receipt

`guard-fresh-run` accepts only `werkfaden-fresh-run-receipt/v1`, bound to the Guardian ID, FRESH_RUN state ticket, final package, patch-closing heartbeat, patch-close ledger head, unique run ID/time, content-bound executor/artifacts/problem evidence and any project-required build/test/parity validation receipts. `problem_status` is `FIXED`, `STILL_PRESENT`, or `INCONCLUSIVE`; mutation completion is never promoted into “problem solved”.

## Ledger, recovery and concurrency

Guardian v2 serializes each session with an OS file lock, atomic state replacement and a prev-hash/event-hash history. `guard-reconcile` handles only provable crash windows: lease/bind before state materialization, or a real terminal transaction whose Guardian acknowledgment was interrupted. It does not adopt drift or unverified bytes.

The hash chain detects accidental/manual history edits that do not recompute the chain, but it is not authentication against a hostile same-OS writer.

## Security boundary

```text
Guardian v2
= supported mutation APIs cannot enter or continue mutation outside the evidence/patch-session contract

Hook / filesystem ACL / service identity
= an agent process cannot bypass those APIs with native filesystem or database writes
```

Do not describe Guardian v2 alone as a hostile-process sandbox. Deploy the existing `acl-plan` boundary (or an equivalent service identity) before making a sole-writer security claim.
