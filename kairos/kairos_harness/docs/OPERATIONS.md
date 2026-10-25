+++
schema = "kairos-context/v1"
id = "KAIROS_OPERATIONS"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_OPERATIONS"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Detailed command-level operations for session entry, search, permits, heartbeat, recovery, health, backup, closure and loop transitions."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_OPERATIONS"]
facets = ["operations", "cli", "heartbeat", "recovery"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "operations"
question = "How do I enter and operate a KAIROS session?"
target = "s-session-entry"

[[answers]]
intent = "search"
question = "How do KAIROS search and freshness work?"
target = "s-search-and-freshness"

[[answers]]
intent = "heartbeat"
question = "How do I run a material KAIROS heartbeat?"
target = "s-material-heartbeat"

[[search_contract]]
query = "How do I enter and operate a KAIROS session?"
expected = "KAIROS_OPERATIONS#s-session-entry"
required_top_k = 1
+++
# KAIROS Operations

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Run examples from kairos_harness/ with ..\kairos_workspace as the prepared workspace.
- [`s-session-entry`](#s-session-entry) — Read, in order:
- [`s-search-and-freshness`](#s-search-and-freshness) — SEARCH_INDEX.md lists every retrieval surface side by side — which command answers
- [`s-ingested-documents-and-the-graph-layer`](#s-ingested-documents-and-the-graph-layer) — A document authored in another workspace is transferred unchanged. Its origin must be
- [`s-withdrawing-an-action-permit`](#s-withdrawing-an-action-permit) — An action permit is retired automatically once its edit has landed and reconciled. A permit
- [`s-material-heartbeat`](#s-material-heartbeat) — A successful receipt includes the trigger, mode and rationale, candidates, promoted artifacts and receipts, missing sources, pending and failed counts, lifecycle transition, and verified: true.
- [`s-reconciliation-and-failure-recovery`](#s-reconciliation-and-failure-recovery) — Reconciliation is a stat comparison over configured sources with hard ceilings of 10,000 documents and 256 MiB; goal JSON is limited to 1,024 files and 64 MiB total. Content hashing occurs only for promotion candidates. If a managed source
- [`s-health-backup-and-restore`](#s-health-backup-and-restore) — Backup retention prunes only older verified KAIROS packages after the newest package passes reopen verification. Verification compares every promoted Markdown hash with the packaged source, rejects duplicate or unsafe members, and checks SQ
- [`s-task-closure-and-finalization`](#s-task-closure-and-finalization) — Task closure requires a promoted success report for every criterion and at least one linked artifact of every type declared by required_artifact_types. It then marks the task's evidenced criteria complete in the authoritative goal JSON and
- [`s-numbered-loop-transition`](#s-numbered-loop-transition) — BREATHE checkpoints prompt context and never increments the semantic loop. Only finalize can seal loop N. A sealed loop retains its mandatory archive and verified backup even when no task or criterion remains active.
- [`s-verification`](#s-verification) — Detailed command-level operations for session entry, search, permits, heartbeat, recovery, health, backup, closure and loop transitions.

<a id="s-overview"></a>
## Overview

> Capsule: Run examples from kairos_harness/ with ..\kairos_workspace as the prepared workspace.

Run examples from `kairos_harness/` with `..\kairos_workspace` as the prepared workspace.

<a id="s-session-entry"></a>
## Session entry

> Capsule: Read, in order:

If no project workspace exists yet, do not invent one merely to read KAIROS rules. Install the
harness and run `kairos search "<question>"` without `--workspace`; the packaged framework
projection is read-only and source-hash verified. Use that path to retrieve project-initiation,
retrieval, Workshop, authority-migration and limitation rules before creating project state.

Read, in order:

1. `AGENTS.md#s-operating-loop`
2. `current.json`
3. `_LOOP_GATE.md#s-verdict`
4. `NEURAL_CORTEX.md#s-orientation`
5. `ACTIVE.md#s-active-frontier`
6. the active task and first uncovered criterion

Then ask one explicit question before broad body reads.

<a id="s-search-and-freshness"></a>
## Search and freshness

> Capsule: SEARCH_INDEX.md lists every retrieval surface side by side — which command answers

`SEARCH_INDEX.md` lists every retrieval surface side by side — which command answers
which shape of question. `RETRIEVAL_METHOD.md` says which to pick when several would
work, with the measurements behind each rule. Read both once before reaching for a door;
the sections below cover the flow around them.

```powershell
python -m kairos search --workspace ..\kairos_workspace --mode depth --max-hops 3 "How does KAIROS restore metadata freshness on normal access?"
```

The returned `routing_receipt.routing_receipt_id` is the prerequisite for normal source inspection:

```powershell
python -m kairos source-permit --workspace ..\kairos_workspace --routing-receipt SRR_... --purpose implementation_verification --path kairos_harness/kairos/search_policy.py --pattern "issue_source_permit|execute_source_search" --reason "Verify the routed implementation symbols."
python -m kairos source-search --workspace ..\kairos_workspace --permit SIP_...
```

Paths are relative to configured `inspection_root`, which must remain inside the project boundary. Permits are active-task and criterion scoped, expire after 30–900 seconds, bind file snapshots, and are consumed once. Only `exact_file_request` and `metadata_repair` may omit the routing receipt; they remain bounded and attributable. If SQLite is unavailable, metadata repair additionally requires `--emergency --metadata-error "<exact diagnostic>"` and exact files.

Search checks governed document and goal-source reconciliation first. If a valid source changed, `freshness.refreshed` is true and the response names the implicit verification heartbeat. Exact declared answer questions receive deterministic ranking priority. Search traces are off by default; use `--trace`, or use BREATHE where tracing is on unless `--no-trace` is explicit.

`--no-refresh` is a diagnostic escape hatch. Its result is potentially stale and cannot support a freshness or completion claim.

<a id="s-ingested-documents-and-the-graph-layer"></a>
## Ingested documents and the graph layer

> Capsule: A document authored in another workspace is transferred unchanged. Its origin must be

A document authored in another workspace is transferred unchanged. Its origin must be
declared in `.kairos/config.json` under `imported_workspace_ids`; an undeclared origin is
refused. A declared origin keeps its own goal, milestone and task identifiers, which are
recorded rather than resolved, and its pointers are stored as external leaves.

Where such a document declares `relations`, `contracts`, `artifacts` or `drift_records` in
its header, promotion projects them into their own tables. Read them back with:

```powershell
python -m kairos graph --workspace ..\kairos_workspace --artifact CODE_EXAMPLE_L0001_V01
python -m kairos graph --workspace ..\kairos_workspace --asset "reports/example_result.json"
python -m kairos graph --workspace ..\kairos_workspace --node "src/example_module.cpp"
python -m kairos graph --workspace ..\kairos_workspace --predicate gates
python -m kairos graph --workspace ..\kairos_workspace --census
python -m kairos graph --workspace ..\kairos_workspace --vocabulary
python -m kairos graph --workspace ..\kairos_workspace --integrity
```

`--vocabulary` names stored values outside their declared set, `--integrity` names rows
whose evidence anchor resolves to no section. Both are findings, not rejections: a single
deviating row never costs a document that may not be edited. The full contract is in
`INGESTED_DOCUMENT_SPEC.md`.

### Naming an entity the graph will recognise

The graph matches an identifier by exact identity. A symbol is stored the way the source
declares it, so `app::intake::BuildSourceProbeV1` matches and the bare
`BuildSourceProbeV1` does not. A name read off a log line, a stack trace or an artifact is
usually the bare one, and an exact-identity miss returns nothing — which reads the same as
"this does not exist".

`--resolve` closes that gap without ever substituting one identity for another:

```powershell
python -m kairos graph --workspace ..\kairos_workspace --resolve BuildSourceProbeV1
python -m kairos graph --workspace ..\kairos_workspace --resolve parser_extraction_semantics_v1.cpp
```

It reports every canonical identity ending in that name, with occurrence count and the
table and column it was found in. One candidate is an answer; several candidates are also
an answer, and the caller picks. No candidates means the name is not in the graph.

`search` applies the same courtesy on its own: when a query named identifiers and none of
them matched, the response carries `graph_context.near_misses` with the canonical forms, so
one call is enough to learn what to ask next.

So do the exact lookups. A miss on `--node`, `--asset` or `--predicate` never returns a
bare empty result, because an empty result reads like a finding. It carries `no_match`,
which separates the two cases that look alike:

```
no row names this identity exactly            -> candidates listed, retry with one
no qualified form ends in it either           -> the corpus really does not mention it
```

For `--predicate` the vocabulary is closed, so the miss lists the declared predicates
rather than searching for near forms.

### Taking inventory

Per-document and per-entity reads answer "what does this one declare". They are the wrong
tool for "what does the corpus declare in total" — building that by walking 148 documents
costs two orders of magnitude more than the answer is worth.

```powershell
python -m kairos graph --workspace ..\kairos_workspace --inventory artifacts
python -m kairos graph --workspace ..\kairos_workspace --inventory drift --field status --value unresolved
python -m kairos graph --workspace ..\kairos_workspace --inventory contracts --field kind --value invariant
python -m kairos graph --workspace ..\kairos_workspace --inventory relations --field predicate --value gates
```

`--inventory` takes `relations`, `contracts`, `artifacts` or `drift`. Every answer carries
its true total, the number of documents involved, and a grouping over the table's
characteristic column — operation for artifacts, status for drift, kind for contracts,
predicate for relations — so the shape of the whole is visible even when the rows are
bounded. `--field` and `--value` narrow it to one column value; an unknown column is
refused with the list of columns that table has.

### Walking the graph from a symptom

`--chase` follows `graph_relations` outward from one or more named entities. This is the
chain from a failure literal back to whatever gates it and onward into what that depends on:

```powershell
python -m kairos graph --workspace ..\kairos_workspace --chase FAIL_STAGE_DEPENDENCY --max-hops 3
python -m kairos graph --workspace ..\kairos_workspace --chase FAIL_STAGE_DEPENDENCY --predicate gates,calls,depends_on
python -m kairos graph --workspace ..\kairos_workspace --chase "SymbolOne,SymbolTwo" --max-hops 2
```

Seeds are comma-separated. With `--chase`, `--predicate` narrows which edges are followed
instead of selecting a query of its own. Hops are capped at 4.

Every edge keeps its predicate, its direction relative to where the hop started, the
document that declared it and its evidence anchor. An edge belongs to exactly one hop, so a
chain never reads deeper than it is. Each hop states `edge_total` against `edge_read`, and a
hop that hit its bound says `truncated`.

`search` runs the same walk when the query named entities the graph knows, and returns it as
`graph_chase` alongside the existing `context_chase`. The two are different chains and both
are reported: `context_chase` follows declared header references between documents,
`graph_chase` follows the code graph.

<a id="s-withdrawing-an-action-permit"></a>
## Withdrawing an action permit

> Capsule: An action permit is retired automatically once its edit has landed and reconciled. A permit

An action permit is retired automatically once its edit has landed and reconciled. A permit
that will never see that edit — issued by mistake, or for bytes already in place — is
withdrawn explicitly, otherwise it holds every later freshness check closed until its TTL
expires:

```powershell
python -m kairos revoke-permit --workspace ..\kairos_workspace --permit GAP_... --reason "The planned edit will not be made."
```

Only an `ACTIVE` permit can be withdrawn, and never one that backs a running action.

<a id="s-material-heartbeat"></a>
## Material heartbeat

> Capsule: A successful receipt includes the trigger, mode and rationale, candidates, promoted artifacts and receipts, missing sources, pending and failed counts, lifecycle transition, and verified: true.

```powershell
python -m kairos heartbeat --workspace ..\kairos_workspace --mode auto
```

A successful receipt includes the trigger, mode and rationale, candidates, promoted artifacts and receipts, missing sources, pending and failed counts, lifecycle transition, and `verified: true`.

Creation commands run this cycle automatically:

```powershell
python -m kairos new-task --workspace ..\kairos_workspace --id TASK_0042 --title "Validate project import" --objective "Prove that imported context is complete and searchable." --goal GOAL_KAIROS_001 --milestone MILESTONE_KAIROS_01 --criteria CRIT_KAIROS_001

python -m kairos new-report --workspace ..\kairos_workspace --id REPORT_TASK_0042_L0007_V01 --task TASK_0042 --title "Project import validation" --outcome "All declared checks passed." --evidence "Bind exact commands, receipts, hashes, and observed outputs." --goal GOAL_KAIROS_001 --milestone MILESTONE_KAIROS_01 --criteria CRIT_KAIROS_001 --state success
```

Never write SQLite directly. Never directly promote `ACTIVE.md`, `CLOSED.md`, `NEURAL_CORTEX.md`, `_LOOP_GATE.md`, or `_SESSION.md`; a heartbeat regenerates them from promoted state.

<a id="s-reconciliation-and-failure-recovery"></a>
## Reconciliation and failure recovery

> Capsule: Reconciliation is a stat comparison over configured sources with hard ceilings of 10,000 documents and 256 MiB; goal JSON is limited to 1,024 files and 64 MiB total. Content hashing occurs only for promotion candidates. If a managed source

```powershell
python -m kairos reconcile --workspace ..\kairos_workspace
```

Reconciliation is a stat comparison over configured sources with hard ceilings of 10,000 documents and 256 MiB; goal JSON is limited to 1,024 files and 64 MiB total. Content hashing occurs only for promotion candidates. If a managed source disappears, a goal source disappears, or validation fails, the heartbeat blocks and each artifact's last good projection remains.

Recovery:

1. inspect the failed heartbeat and promotion event;
2. correct the source;
3. increment its revision when promoted bytes changed;
4. run a heartbeat;
5. verify zero pending and failed events.

For a clean checkout where every derived surface is absent:

```powershell
python -m kairos rebuild-derived --workspace ..\kairos_workspace
```

The command reconstructs SQLite, manifests, runtime state, `current.json`, and dynamic routers from tracked sources. It refuses any existing derived surface rather than overwriting it.

<a id="s-health-backup-and-restore"></a>
## Health, backup, and restore

> Capsule: Backup retention prunes only older verified KAIROS packages after the newest package passes reopen verification. Verification compares every promoted Markdown hash with the packaged source, rejects duplicate or unsafe members, and checks SQ

```powershell
python -m kairos backup --workspace ..\kairos_workspace --keep 3
python -m kairos backup-verify --workspace ..\kairos_workspace --id BACKUP_ID
python -m kairos restore-drill --workspace ..\kairos_workspace --id BACKUP_ID
python -m kairos health --workspace ..\kairos_workspace --full
```

Backup retention prunes only older verified KAIROS packages after the newest package passes reopen verification. Verification compares every promoted Markdown hash with the packaged source, rejects duplicate or unsafe members, and checks SQLite integrity and foreign keys. Restore drills never overwrite the live workspace and must replay a declared query handle.

<a id="s-task-closure-and-finalization"></a>
## Task closure and finalization

> Capsule: Task closure requires a promoted success report for every criterion and at least one linked artifact of every type declared by required_artifact_types. It then marks the task's evidenced criteria complete in the authoritative goal JSON and

```powershell
python -m kairos close-task --workspace ..\kairos_workspace --id TASK_0042 --next-task TASK_0043 --mode verify
python -m kairos finalize --workspace ..\kairos_workspace
```

Task closure requires a promoted success report for every criterion and at least one linked artifact of every type declared by `required_artifact_types`. It then marks the task's evidenced criteria complete in the authoritative goal JSON and closes the milestone and goal only when all children are closed. `--next-task` may atomically route runtime state to an already-promoted open successor; omitting it leaves no active task. Finalization applies the same coverage gate and additionally requires archive promotion and a verified backup. After `FINALIZED`, ordinary heartbeats are rejected.

<a id="s-numbered-loop-transition"></a>
## Numbered loop transition

> Capsule: BREATHE checkpoints prompt context and never increments the semantic loop. Only finalize can seal loop N. A sealed loop retains its mandatory archive and verified backup even when no task or criterion remains active.

`BREATHE` checkpoints prompt context and never increments the semantic loop. Only `finalize` can seal loop N. A sealed loop retains its mandatory archive and verified backup even when no task or criterion remains active.

After finalization, formulate fresh goal and task sources with new identifiers; the task header must declare loop N+1. Then run:

```powershell
python -m kairos project-kickoff --workspace ..\kairos_workspace --spec-json '<kairos-project-kickoff/v1 JSON>'
```

`project-kickoff` is the normal Human-triggered re-entry path. It derives fresh governance scope from the validated contract, stages deterministic goal and task sources, and activates Loop N+1 through one resumable exact-once receipt. Use `new-loop --goal ... --milestone ... --task ...` only when those successor sources already exist under governed staging.

Build a reusable starter only into an empty target:

```powershell
python -m kairos export-golden --workspace ..\kairos_workspace --source-root .. --target <empty-target> --source-commit <immutable-commit>
```

The export is source-only and sealed. First `project-kickoff` verifies every source hash, reconstructs derived metadata, creates and reopens a fresh backup, restores the FINALIZED receipt, and only then starts the successor project.

The command verifies the predecessor finalization file and database row, final heartbeat, immutable archive bytes and promotion receipt, retained backup, next-source scope, and exact loop number. It uses a deterministic transition ID, unique predecessor and target-loop constraints, and the workspace lock. An identical retry returns the existing receipt; a conflicting retry or invalid lifecycle fails closed. A prepared failure is retried with the same arguments after repairing the recorded cause—never by editing runtime JSON or SQLite.

<a id="s-verification"></a>
## Verification

> Capsule: Detailed command-level operations for session entry, search, permits, heartbeat, recovery, health, backup, closure and loop transitions.

```powershell
python -m compileall -q kairos
python -m unittest discover -s tests -v
python -m kairos validate --workspace ..\kairos_workspace --all
python -m kairos health --workspace ..\kairos_workspace --full
```
