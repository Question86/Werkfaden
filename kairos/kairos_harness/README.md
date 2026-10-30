+++
schema = "kairos-context/v1"
id = "KAIROS_HARNESS_ENTRY"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_HARNESS_ENTRY"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Harness entry and command reference: source documents, same-heartbeat projection, reviewed project intake and bounded retrieval."
claim_boundary = "Defines harness usage; current project intent, source authority and verified receipts own project facts and outcomes."
entities = ["KAIROS", "KAIROS_HARNESS_ENTRY"]
facets = ["framework-documentation", "current-authority"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "current_authority"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "9c224e255b8b14f777a4d4a2da4e5e18aaf716fdf3d43fe1aede479c83be65ee"

[[answers]]
intent = "procedure"
question = "How should a new project enter the KAIROS harness safely?"
target = "s-requirements"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "How should a new project enter the KAIROS harness safely?"
expected = "KAIROS_HARNESS_ENTRY#s-requirements"
required_top_k = 5
+++
# KAIROS Context Harness

## CONTEXT INDEX

- [Requirements](#s-requirements)
- [Quick start](#s-quick-start)
- [Command surface](#s-command-surface)
- [Context modes](#s-context-modes)
- [Safety model](#s-safety-model)


KAIROS combines goal decomposition, structured evidence documents, deterministic promotion, section-level retrieval, typed context chasing, and bounded dynamic routing in one control plane.

The core invariant is: a material action is not checkpointed until its source document, section metadata, relations, goal coverage, and verification receipt are searchable in the same heartbeat.

<a id="s-requirements"></a>
## Requirements

> Capsule: The harness requires Python3.11 or newer and SQLite FTS5, with no third-party runtime packages.

- Python 3.11 or newer
- SQLite with FTS5
- no third-party runtime packages

<a id="s-quick-start"></a>
## Quick start

> Capsule: Start a real project with immutable framework retrieval, reviewed human intent, read-only detection and authorized intake.

For a real project, begin at the framework checkout root. Ask the immutable framework corpus and render a bounded intent prompt:

```text
python kairos_cli.py search "How do I onboard an arbitrary existing codebase into KAIROS?"
python -m kickstart prompt --idea "Describe the intended project outcome"
```

The prompt renderer does not call an LLM. Review the resulting kairos-project-kickoff/v1 goal/milestone/task contract and save it separately before source discovery. Then use read-only detect and explicitly authorized intake with a fresh workspace:

```text
python -m kickstart detect <project>
python -m kickstart init --auto --project-root <project> --workspace <fresh-workspace> --spec-file <reviewed-contract.json>
```

C-family and mixed intake need compiler authority as specified in the canonical project kickstart contract. Standalone harness init creates a prepared context workspace; it is not a replacement for project intent review or source binding. Query project knowledge with its explicit workspace. Install the harness only if you need its console entrypoint.

<a id="s-command-surface"></a>
## Command surface

> Capsule: Maps native harness commands to their bounded context, projection and recovery purposes.

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

<a id="s-context-modes"></a>
## Context modes

> Capsule: Defines work, depth, breadth, breathe and verification scopes; a mode change does not close a semantic loop.

- `work` — one bounded implementation or documentation action.
- `depth` — one prioritized causal, dependency, provenance, or validation chain.
- `breadth` — several competing roots or contradiction branches.
- `breathe` — persist a compact reload frontier; it never closes or increments a semantic loop, and retrieval tracing is enabled by default only here.
- `verify` — reopen evidence before a claim transition.
- `auto` — select a mode from the current evidence and context state.

<a id="s-safety-model"></a>
## Safety model

> Capsule: Source documents and goal contracts own claims; confined paths and verified projections preserve the authority boundary.

Source documents and goal contracts own their scoped claims. SQLite is a verified derived projection and is never edited directly. Workspace paths are canonicalized and confined; workspace, goal, milestone, parent-task, and criterion ownership are checked before promotion; write commands are serialized across processes; generated routers are regenerated rather than accepted as source. Managed documents are capped at 10,000 and 256 MiB, goal sources at 1,024 and 64 MiB, and backup source expansion at 352 MiB; input, retrieval expansion, generated files, and traces have separate bounds. Audit ledgers remain durable in SQLite and therefore require capacity monitoring rather than silent deletion.

`finalize` and the generated loop gate use the same readiness policy even after the last task and criterion leave runtime scope. A successful seal always includes the mandatory loop archive and a verified backup. For normal Human re-entry, run `project-kickoff --spec-json ...`; it validates and stages fresh goal, milestone, task, and criterion scope before activating Loop N+1. `new-loop` remains the expert interface for already governed staged sources. Both paths verify predecessor finalization, heartbeat, immutable archive, promotion receipt, and retained backup, and identical retries return one transition receipt.

See [SYSTEM_SYNTHESIS.md](docs/SYSTEM_SYNTHESIS.md), [CONTEXT_HEADER_SPEC.md](docs/CONTEXT_HEADER_SPEC.md), [LLM_OPERATING_CONTRACT.md](docs/LLM_OPERATING_CONTRACT.md), and [OPERATIONS.md](docs/OPERATIONS.md).

For retrieval specifically: [RETRIEVAL_METHOD.md](docs/RETRIEVAL_METHOD.md) is the measured decision procedure for choosing a surface — read it before the first query of a session — and [SEARCH_INDEX.md](docs/SEARCH_INDEX.md) is the flag-by-flag reference behind it.
