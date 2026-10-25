# KAIROS Context Harness

KAIROS combines goal decomposition, structured evidence documents, deterministic promotion, section-level retrieval, typed context chasing, and bounded dynamic routing in one control plane.

The core invariant is: a material action is not checkpointed until its source document, section metadata, relations, goal coverage, and verification receipt are searchable in the same heartbeat.

## Requirements

- Python 3.11 or newer
- SQLite with FTS5
- no third-party runtime packages

## Quick start

```powershell
python -m kairos init --workspace ..\my_kairos_workspace --workspace-id MY_PROJECT
python -m kairos validate --workspace ..\my_kairos_workspace --all
python -m kairos heartbeat --workspace ..\my_kairos_workspace --mode auto
python -m kairos search --workspace ..\my_kairos_workspace "What evidence is still missing for the active goal?"
python -m kairos source-permit --workspace ..\my_kairos_workspace --routing-receipt SRR_... --purpose exact_location --path docs/KAIROS_ARCHITECTURE_ATLAS.md --pattern "authority|retrieval" --reason "Read the exact routed architecture section."
python -m kairos source-search --workspace ..\my_kairos_workspace --permit SIP_...
```

Initialization requires an empty target. KAIROS never silently adopts or overwrites an existing workspace.

## Command surface

| Command | Purpose |
|---|---|
| `init` | Create and promote a prepared workspace. |
| `rebuild-derived` | Reconstruct absent SQLite and dynamic state from tracked authoritative sources; never overwrite live derived state. |
| `status` | Refresh if needed, then show runtime, metadata, and goal state. |
| `validate` | Validate bounded headers, stable anchors, answer handles, authorities, and references. |
| `heartbeat` | Reconcile, promote, verify, regenerate bounded routers, and checkpoint. |
| `search` | Refresh if needed, retrieve answer sections, rerank, chase typed context, and persist a routing receipt. |
| `source-permit` | Exchange one fresh routing receipt for a narrow, expiring, snapshot-bound source permit, or record a declared exact-file/metadata-repair exception. |
| `source-search` | Consume one source permit and persist a bounded inspection or violation receipt. |
| `reconcile` | Preview stat-based source drift without promotion. |
| `goal-sync` | Validate and synchronize goal and milestone contracts through a verification heartbeat. |
| `coverage` | Refresh if needed, then show success evidence, required artifact types, and missing types per criterion. |
| `health` | Audit document, goal, FTS, database, authority, bounds, latency, generated-file retention, and backup parity. |
| `backup` | Create a consistent online database snapshot and compressed source package, then reopen it. |
| `backup-verify` | Verify package hashes, safe unique members, SQLite integrity, foreign keys, and artifact/source parity. |
| `restore-drill` | Restore into an isolated temporary target and replay a declared search handle. |
| `new-task` | Create and activate a goal-linked task, optionally scoped to a criterion subset, in one heartbeat. |
| `new-report` | Create and promote execution evidence. |
| `new-bug` | Create and promote a causal diagnostic. |
| `new-code` | Create and promote implementation navigation. |
| `new-decision` | Record options, selection, rationale, risks, rollback, and validation. |
| `new-research` | Bind a source fact and separate model interpretation. |
| `close-task` | Close only after complete evidence and optionally activate an explicit already-promoted successor. |
| `promote` | Diagnostic direct promotion for source documents; generated routers are rejected. |
| `finalize` | Require a final heartbeat, complete coverage, archive promotion, and verified backup. |
| `new-loop` | Verify the sealed predecessor and activate exactly one staged numbered successor with an idempotent receipt. |
| `project-kickoff` | Validate one bounded Human contract, create fresh goal and first-task sources, and activate the numbered successor exactly once. |
| `export-golden` | Build a sealed, sanitized, source-only starter in an empty target and bind it to a source commit. |

## Context modes

- `work` — one bounded implementation or documentation action.
- `depth` — one prioritized causal, dependency, provenance, or validation chain.
- `breadth` — several competing roots or contradiction branches.
- `breathe` — persist a compact reload frontier; it never closes or increments a semantic loop, and retrieval tracing is enabled by default only here.
- `verify` — reopen evidence before a claim transition.
- `auto` — select a mode from the current evidence and context state.

## Safety model

Source documents and goal contracts own their scoped claims. SQLite is a verified derived projection and is never edited directly. Workspace paths are canonicalized and confined; workspace, goal, milestone, parent-task, and criterion ownership are checked before promotion; write commands are serialized across processes; generated routers are regenerated rather than accepted as source. Managed documents are capped at 10,000 and 256 MiB, goal sources at 1,024 and 64 MiB, and backup source expansion at 352 MiB; input, retrieval expansion, generated files, and traces have separate bounds. Audit ledgers remain durable in SQLite and therefore require capacity monitoring rather than silent deletion.

`finalize` and the generated loop gate use the same readiness policy even after the last task and criterion leave runtime scope. A successful seal always includes the mandatory loop archive and a verified backup. For normal Human re-entry, run `project-kickoff --spec-json ...`; it validates and stages fresh goal, milestone, task, and criterion scope before activating Loop N+1. `new-loop` remains the expert interface for already governed staged sources. Both paths verify predecessor finalization, heartbeat, immutable archive, promotion receipt, and retained backup, and identical retries return one transition receipt.

See [SYSTEM_SYNTHESIS.md](docs/SYSTEM_SYNTHESIS.md), [CONTEXT_HEADER_SPEC.md](docs/CONTEXT_HEADER_SPEC.md), [LLM_OPERATING_CONTRACT.md](docs/LLM_OPERATING_CONTRACT.md), and [OPERATIONS.md](docs/OPERATIONS.md).

For retrieval specifically: [RETRIEVAL_METHOD.md](docs/RETRIEVAL_METHOD.md) is the measured decision procedure for choosing a surface — read it before the first query of a session — and [SEARCH_INDEX.md](docs/SEARCH_INDEX.md) is the flag-by-flag reference behind it.
