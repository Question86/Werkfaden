+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_README"
type = "documentation"
revision = 4
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_README"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Runtime Sync Workshop entry surface for synchronized code and Markdown changes with mandatory metadata aftercheck before ordinary patch application."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_WORKSHOP_README"]
facets = ["ai-review", "guardian", "workshop", "entry"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]
distribution_role = "current_authority"

[[answers]]
intent = "workshop"
question = "What is the Runtime Sync Workshop?"
target = "s-overview"

[[answers]]
intent = "ai_review"
question = "Does Workshop enforce JEV AI approval before Apply?"
target = "s-standalone-ai-review"

[[search_contract]]
query = "What is the Runtime Sync Workshop?"
expected = "KAIROS_WORKSHOP_README#s-overview"
required_top_k = 1

[refs]
+++
# Runtime Sync Workshop

## CONTEXT INDEX

- `s-standalone-ai-review` â€” Standalone JEV submission, combined Guardian/metadata checks and missing AI enforcement.

- [`s-overview`](#s-overview) â€” This package is a project-neutral, fail-closed synchronizer for a configured source tree,
- [Overview](#s-overview)
- [Standalone AI review and combined branch](#s-standalone-ai-review)

<a id="s-overview"></a>
## Overview

> Capsule: This package is a project-neutral, fail-closed synchronizer for a configured source tree,

This package is a project-neutral, fail-closed synchronizer for a configured source tree,
blueprints, and KAIROS projection. It is intentionally unbound: copy
`../templates/workshop.config.template.json`, bind every absolute authority path, and run
`python workshop.py status` before any transaction.

The machine owns only its configured transaction and state trees. It never edits a live
source or blueprint path until a sealed checkout, honest metadata review, mechanical
preparation, isolated verification, and a governed postcheck all pass. SQLite remains a
KAIROS heartbeat projection; the Workshop does not write it directly.

Ordinary code patches require semantic header edits and an explicit aftercheck:

```text
checkout -> edit code and metadata -> metadata-plan -> complete audit
-> prepare -> metadata-check -> verify -> apply
```

Every changed source/header needs an actual semantic header change and field-by-field review.
The plan supplies reproducible source/document spot checks; `metadata-check` grants the bound
clearance required by verification and apply. Regenerated hashes and an unrelated prose addition
do not satisfy this obligation. Metadata must be finished before apply. See [operations](OPERATIONS.md)
and the [metadata aftercheck contract](../docs/WORKSHOP.md#s-metadata-aftercheck) for scope and evidence.
The check validates review work and evidence bindings; it does not prove natural-language truth.

Authority migrations apply the same aftercheck to changes in existing source/header bytes,
using `authority-prepare`, then `metadata-check`, `authority-verify` and `authority-apply`.
Static source-set migrations keep retained existing source bytes unchanged; code edits to those
paths require the ordinary workflow. New and removed files retain their migration reviews.

No parity/build adapter is shipped. A project that needs one must provide and hash its own
adapter under the generic parity extension boundary.

<a id="s-standalone-ai-review"></a>
## Standalone AI review and combined branch

> Capsule: The standalone JEV port submits actual changed code but does not authorize or gate Workshop Apply.

The publication combines Guardian v2 with the metadata-aware Workshop core. Existing deterministic gates retain their own scope. Optional JEV submission, separate evidence-sufficiency questions and reconsideration tools live under `review/`; see [the operating instructions](../review/README.md) and [the comparison](../review/COMPARISON.md). No supported Apply path consumes a JEV approval. A metadata reviewer field or Guardian receipt is not evidence of an AI call.

When Guardian is required, normal-transaction context documents remain unsupported and fail closed; separately proven auxiliary scope is required. Without Guardian enforcement, the existing context-document transaction path retains its metadata checks.
