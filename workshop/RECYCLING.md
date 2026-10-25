+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_RECYCLING"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_RECYCLING"
updated_at = "2026-10-25T20:00:00Z"
capsule = "Binding Workshop transaction recycling rules: nine-TX admission, historical catch-up, evidence-bound disposition, SQL projection and crash-safe compaction."
claim_boundary = "Defines the operating protocol, not the correctness of any historical patch, live project state or production deployment."
entities = ["KAIROS", "Workshop", "Transaction recycling"]
facets = ["workshop", "recycling", "retention", "transaction", "recovery"]
criteria = []
does_not_answer = ["live Workshop transaction inventory", "unverified historical outcome"]

[[answers]]
intent = "operating_rules"
question = "When must Workshop recycle transactions before another checkout?"
target = "s-trigger"

[[answers]]
intent = "recycling_procedure"
question = "How does the agent safely process more than ten old Workshop transactions?"
target = "s-catch-up"

[[answers]]
intent = "failure_semantics"
question = "How do recycled transactions survive a crash?"
target = "s-recovery"

[[answers]]
intent = "transaction_outcome"
question = "Where must rejected transaction code and the rationale be documented?"
target = "s-documentation"

[[search_contract]]
query = "How does Workshop recycle historical transactions safely?"
expected = "KAIROS_WORKSHOP_RECYCLING#s-catch-up"
required_top_k = 5
+++
# Workshop TX recycling and legacy backlog recovery

## CONTEXT INDEX

- [`s-trigger`](#s-trigger) — Checkout at nine materialized TX is blocked; maintenance remains available for larger historical pools.
- [`s-catch-up`](#s-catch-up) — Inspect, justify, preserve, project and safely compact terminal transactions in resumable batches.
- [`s-orphans`](#s-orphans) — Unleased orphan retirement requires proof; active or Guardian-controlled work uses proper recovery.
- [`s-recovery`](#s-recovery) — Compaction plans and verified vault bytes allow replay without a second Apply or unproved deletion.
- [`s-documentation`](#s-documentation) — A transaction record must preserve the planned code/approach, actual attempt, disposition, rationale and evidence.
- [`s-boundary`](#s-boundary) — File-level dispositions are not semantic patch adoption, provider attestation or binary deployment proof.

Status: opt-in feature. No production workspace is upgraded automatically.

<a id="s-trigger"></a>
## Authority and trigger

> Capsule: Checkout at nine materialized TX is blocked; maintenance remains available for larger historical pools.

Set `transaction_documents_required=true` and
`tx_retention_required=true` in the canonical Workshop configuration.
`tx_max_materialized=10` is the absolute configured limit; the default
`tx_recycle_trigger=9` deliberately blocks a **new** checkout when
nine materialized TX already exist. An existing messy workspace with 11,
50 or 200 TX is not rejected by the **maintenance** operations.

Workshop's public Engine lease admission is the enforcement point, shared
by ordinary, auxiliary and migration checkout callers. KAIROS's
`current.json`, `NEURAL_CORTEX.md`, `ACTIVE.md` and `_LOOP_GATE.md`
are **derived routing**, not a replacement for the live Engine gate.

KAIROS's numbered goal/task Loop is NOT incremented when a transaction is
recycled. The recycling operation sits between patch-session transactions
under the existing task, and uses its own transaction history.

<a id="s-catch-up"></a>
## Supported catch-up sequence

> Capsule: Inspect, justify, preserve, project and safely compact terminal transactions in resumable batches.

From the Workshop entry point, with its canonical `--config`:

1. `tx-recycle-status --refresh-loop` — enumerate all existing materialized
   TX. The KAIROS Loop Gate then points to `s-tx-recycling`.
2. `tx-recycle-drain --max-items 100` — first resume interrupted
   compactions, then process eligible, already reviewed terminal TX.
   For old terminal TX, the command backfills a KAIROS transaction report
   if absent. For unreviewed candidates, it builds an immutable
   baseline-versus-work inventory and an editable proposal template.
   Every TX that cannot safely progress is returned with a named blocker. The fair drain cursor rotates past a bounded batch of blocked predecessors instead of rescanning the same first hundred indefinitely.
   The operation does not manufacture an agent verdict.
3. For each terminal TX that still needs a decision, open the
   `proposal_path` returned by `tx-recycle-inspect <TX-ID>` or drain.
   Fill **planned_approach**, **attempted_approach**, **outcome_reason**,
   and one decision per changed file unit (`FILE_CHANGE_<hash>`, not a semantically verified atomic). Each decision must name an
   exact unit ID, select `RETAIN_FOR_REUSE`, `RETAIN_EVIDENCE`, or
   `REJECT_FOR_REUSE`, provide a concrete rationale and refer to an
   existing `TX:<TX-relative-file>` or `KAIROS:<workspace-file>`.
   An empty candidate still requires `no_changes_reason`.
4. `tx-recycle-submit <TX-ID> --analysis <edited-proposal.json>`.
   The Engine verifies exact candidate inventory coverage, retains the
   entire TX as SHA-addressed objects, and publishes the recycling
   outcome inside the canonical `transaction` Markdown document.
   The KAIROS Heartbeat promotes the document and normalizes the exact
   decision rows into `tx_recycling_reviews` and
   `tx_recycling_atoms`. The SQL projection must match the record.
5. `tx-recycle-drain --max-items 100` again. Only a terminal TX with a
   reviewed, still-current inventory and verified Markdown/SQL evidence
   can relinquish its materialized working directories. Repeat until
   there are **at most eight** materialized TX.
6. `tx-recycle-status --refresh-loop` confirms admission. Retry new
   Checkout only after the live Engine count is under nine.

Any failed individual TX remains intact and is reported; other eligible TX
continue within the batch limit. A backlog larger than `max-items` is
processed by running the command again. No destructive "force" shortcut
is offered. When all remaining TX are protected or unreadable, Checkout
correctly remains blocked: safe reduction below nine is not always possible.

<a id="s-orphans"></a>
## Pre-apply orphans and recoveries

> Capsule: Unleased orphan retirement requires proof; active or Guardian-controlled work uses proper recovery.

A missing Workshop lease does **not** prove that a patch was never applied.
An unleased `CHECKED_OUT`, `PREPARED` or `SHADOW_VERIFIED` TX may be
explicitly retired with `tx-recycle-retire-orphan <TX-ID> --reason ...`
only when there is no Apply/permit/rollback evidence, no active lease and
no Guardian-required control plane. Its full bytes are vaulted first.
`APPLYING`, `RECOVERY_REQUIRED` and Guardian-enforced states use their
existing proper recovery commands, not this shortcut.

Verified, never-applied candidates must remain `RETAIN_FOR_REUSE` when
recycled. A `REJECT_FOR_REUSE` decision means "do not carry this
approach into new work"; it does NOT erase the historical bytes from the
vault. The code can be recovered with `tx-rehydrate`, but any reused
part needs its own fresh validation. There is deliberately no shared
vault garbage collector in this change.

<a id="s-recovery"></a>
## Crash safety

> Capsule: Compaction plans and verified vault bytes allow replay without a second Apply or unproved deletion.

Before any working directory is renamed, the Engine preserves and
rereads every TX file in a SHA-addressed complete manifest, verifies the
reviewed inventory, and writes a durable `compaction-plan.json`.
Original folders are renamed under `.reclaim/`; a receipt and the
updated KAIROS transaction document are verified before removing the
staged copies. The next `tx-recycle-drain` resumes any interrupted
plan. The retention process lock is released by the OS on process exit,
including crashes. Missing bytes or inconsistent hashes stop only that
TX and surface a diagnostic.

<a id="s-documentation"></a>
## Documentation contract

> Capsule: A transaction record must preserve the planned code/approach, actual attempt, disposition, rationale and evidence.

The existing 18 fixed KAIROS section anchors remain in place. The TX
Markdown describes **what code/approach was planned, what was actually
attempted, why it failed or was retained/rejected**, plus the original
and candidate hashes, per-file decision IDs, evidence locators and the
vault receipt. The full machine decision ledger is stored once under
`s-graph-metadata` and projected into SQL. Any item the agent cannot
justify must be retained as unresolved evidence; a file's existence
does not prove the truth of its content.

The existing TypeSafe JEV review is a candidate attempt assessment,
not a deployment authority. A semantic decision in the recycling
report is evidence-attributed agent judgment, not an independently
verified correctness theorem. Apply and deployment remain distinct
events.

<a id="s-boundary"></a>
## Still out of scope

> Capsule: File-level dispositions are not semantic patch adoption, provider attestation or binary deployment proof.

Automatic semantic hunk extraction, cross-TX matching, merging code
into a successor TX, proof-carrying adoption by that successor, and
actual deployed-binary verification are separate future capabilities.
The initial file-level units support reliable audit coverage, not
semantic patch correctness. Old TX may lack retrievable historical
test logs. In that case the record must say "unknown" and preserve
the evidence; do not infer successful tests from age or state alone.
