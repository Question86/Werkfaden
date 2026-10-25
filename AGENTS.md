+++
schema = "kairos-context/v1"
id = "KAIROS_FRAMEWORK_RULES"
type = "documentation"
revision = 5
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_FRAMEWORK_RULES"
updated_at = "2026-10-25T16:00:00Z"
capsule = "Binding operating rules for KAIROS authority, retrieval-first context acquisition, and mandatory metadata aftercheck before ordinary Workshop source patches can be applied."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_FRAMEWORK_RULES"]
facets = ["operating-rules", "authority", "bootstrap", "change-discipline", "metadata-aftercheck"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "operating_rules"
question = "How must an agent operate KAIROS?"
target = "s-overview"

[[answers]]
intent = "project_kickoff"
question = "How should a new project be initiated in KAIROS?"
target = "s-project-initiation-authority"

[[answers]]
intent = "retrieval"
question = "What should an agent do before project intake or source inspection?"
target = "s-mandatory-context-loop"

[[answers]]
intent = "fail_closed"
question = "What does KAIROS refuse to infer or bypass?"
target = "s-fail-closed-boundaries"

[refs]
bootstrap = "[ref:docs/FRAMEWORK_BOOTSTRAP.md#s-before-project-intake|id:KAIROS_FRAMEWORK_BOOTSTRAP|v:1|rel:requires|tags:bootstrap,retrieval|src:framework]"
kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-1-project-intent-precedes-code-discovery|id:KAIROS_PROJECT_KICKSTART|v:1|rel:requires|tags:intent,kickstart|src:framework]"
workshop = "[ref:docs/WORKSHOP.md#s-overview|id:KAIROS_WORKSHOP|v:6|rel:requires|tags:workshop,mutation,metadata-aftercheck|src:framework]"

[[search_contract]]
query = "How must an agent operate KAIROS?"
expected = "KAIROS_FRAMEWORK_RULES#s-overview"
required_top_k = 1
+++
# KAIROS Framework operating rules

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Binding operating rules for KAIROS authority, retrieval-first context acquisition, and mandatory metadata aftercheck before ordinary Workshop source patches can be applied.
- [`s-authority-and-placement`](#s-authority-and-placement) — Authoritative claims live in source Markdown and goal contracts. SQLite, routers, search
- [`s-project-initiation-authority`](#s-project-initiation-authority) — For a fresh external project, Human intent comes first. A reviewed
- [`s-mandatory-context-loop`](#s-mandatory-context-loop) — Before a project workspace exists, query the immutable framework corpus with kairos search "<question>" and no --workspace; use it to retrieve the governing KAIROS procedure instead of reconstructing rules from the directory tree. Once proj
- [`s-fail-closed-boundaries`](#s-fail-closed-boundaries) — The harness owns document validation and deterministic promotion. The Workshop is the only
- [`s-change-discipline`](#s-change-discipline) — Keep source and affected semantic Markdown in the same bounded, reviewed and verified transaction.

<a id="s-overview"></a>
## Overview

> Capsule: Binding operating rules for KAIROS authority, retrieval-first context acquisition, and mandatory metadata aftercheck before ordinary Workshop source patches can be applied.

<a id="s-authority-and-placement"></a>
## Authority and placement

> Capsule: Authoritative claims live in source Markdown and goal contracts. SQLite, routers, search

Authoritative claims live in source Markdown and goal contracts. SQLite, routers, search
indexes, receipts, and runtime state are derived projections. Never hand-edit a derived
database, router, receipt, seal, lease, or loop state.

Place durable content by epistemic role: goals in `goals/`, bounded work in `tasks/`,
outcomes in `reports/`, causal failures in `bugs/`, implementation maps in `code/`, source
facts in `research/`, tradeoffs in `decisions/`, stable synthesis in `docs/`, and closed
loop material in `archive/`.

<a id="s-project-initiation-authority"></a>
## Project initiation authority

> Capsule: For a fresh external project, Human intent comes first. A reviewed

For a fresh external project, Human intent comes first. A reviewed
`kairos-project-kickoff/v1` contract establishes the project goal, milestone, task and
criteria before source discovery. The agent then runs `kickstart detect <project>` and
`kickstart init --auto`. Python/JavaScript/TypeScript/Rust/Go/JVM/.NET/Ruby/PHP use bounded
project-local source membership with exact hashes and ledgers; KAIROS does not invent dynamic
imports, runtime reachability or build participation. C/C++/CUDA translation-unit membership
remains compiler-produced and fail-closed, with compiler-guided local-header closure. Mixed
projects combine these authorities without projecting non-C-family files as translation units.

<a id="s-mandatory-context-loop"></a>
## Mandatory context loop

> Capsule: Before a project workspace exists, query the immutable framework corpus with kairos search "<question>" and no --workspace; use it to retrieve the governing KAIROS procedure instead of reconstructing rules from the directory tree. Once proj

Before a project workspace exists, query the immutable framework corpus with `kairos search "<question>"` and no `--workspace`; use it to retrieve the governing KAIROS procedure instead of reconstructing rules from the directory tree. Once project intake exists, project questions use the project workspace.

Orient from the current state and operating contract, ask a concrete question, search the
smallest authoritative sections, chase typed references, perform one bounded action, write
the matching source document, promote it in the same heartbeat, validate the result, and
checkpoint the frontier. A search miss is not proof of absence; inspect the owning source
when the contract requires it.

<a id="s-fail-closed-boundaries"></a>
## Fail-closed boundaries

> Capsule: The harness owns document validation and deterministic promotion. The Workshop is the only

The harness owns document validation and deterministic promotion. Initial intake observes the
original project read-only and may deterministically create the first KAIROS Markdown/database
baseline. After seal, the Workshop is the only writer for governed source, governed build/config
authority and governed Markdown. Humans, agents, editors, watchers, adapters, build tools and copy
utilities do not edit the configured live corpus directly. They may edit only Workshop transaction
candidate/work trees. Any analysis or external tool with plausible write side effects must run on
an isolated clone/snapshot rather than the governed original.

An optional parity adapter is project-supplied and must be explicitly bound. The canonical
package supplies no build command, scientific domain, fixture, runner, or result authority.

<a id="s-change-discipline"></a>
## Change discipline

> Capsule: Keep source and affected semantic Markdown in the same bounded, reviewed and verified transaction.

Keep transactions bounded and preserve exact bytes, hashes, mappings, topology and provenance.
For ordinary source patches, checkout the affected existing Markdown with repeatable `--document`
arguments; the selected source's implementation Markdown is already included. Edit and review
`capsule`, `answers`, `refs`, `claim_boundary`, `entities`, `facets`, `does_not_answer`,
`search_contract` and affected sections inside the same work tree.
Declare any additional context scope through `include-documents`, complete the document review,
and rerun preparation and verification. Preserve meaningful historical pinned references. Apply
must receive the finished source and Markdown package; the governed heartbeat projects that
package into the database before postcheck success. Do not defer semantic maintenance to a
post-apply edit or treat regenerated hashes as proof of semantic accuracy.

For every existing source/header whose code bytes change in an ordinary patch or an authority
migration, updating and checking its implementation metadata is mandatory BEFORE apply.
Change at least one of the eight semantic
header fields above to describe the actual change; revision, timestamp, generated mapping/ledger
changes and unrelated body additions do not count. Review every field, documenting why each
unchanged value remains accurate. Do not invent claims or cosmetic wording merely to clear a gate.
Run `metadata-plan`, inspect the selected source/document evidence, and complete the pending
field decisions and deterministic spot checks in `review/metadata_audit.json`. Derive findings
from the candidate source, preserve exact identifiers and historical provenance, and correct any
partial or failed finding before rerunning the check. The review must not assert build results,
scientific truth or complete semantic coverage that its evidence does not establish.

Ordinary code patches use `checkout -> edit code and metadata -> metadata-plan -> complete audit
-> prepare -> metadata-check -> verify -> apply`. Preparation validates the required header edits
and audit; `metadata-check` grants a hash-bound clearance that verification and apply require.
Changed source, Markdown, scope or review after clearance requires fresh preparation, metadata
checking and verification, with a new plan when its evidence has changed. A documentation-only
transaction does not require an invented code change or header rewrite; its existing document
review still applies. Never defer this required aftercheck to a post-apply task.

An authority migration that changes existing source/header bytes uses the same `metadata-plan`
and completed audit before `authority-prepare`, then `metadata-check` before `authority-verify`
and `authority-apply`. Newly added or removed files retain their explicit migration review;
do not invent a previous semantic header for a new file.

Create/delete/rename of static-
ecosystem sources use `source-set-checkout -> source-set-prepare -> source-set-verify -> source-set-apply`;
the live project remains unchanged until verified apply. Existing source paths must retain their
sealed bytes in that candidate; apply their code edits through the ordinary metadata workflow before
or after the membership migration. Candidate build/config changes and C-family
topology are refused by that generic source-set path. C-family owner-only header changes remain derived
inside the ordinary transaction; governed-header topology uses the compiler-backed authority migration,
and compiler-context/build-authority changes require stronger project-specific proof. After any failed
command, inspect state before retrying. Do not widen a scope or disable a gate to make a refusal disappear.
Git and remote publication require an explicit, separate approval. When Workshop rejects a new checkout at the recycling threshold, run tx-recycle-inspect, tx-recycle-submit and tx-recycle-drain until the current pool is below the threshold. A recycled candidate requires fresh validation in any successor.
