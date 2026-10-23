+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_README"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_README"
updated_at = "2026-10-23T00:00:00Z"
capsule = "Runtime Sync Workshop entry surface for synchronized code and Markdown changes with mandatory metadata aftercheck before ordinary patch application."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_WORKSHOP_README"]
facets = ["workshop", "entry"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "workshop"
question = "What is the Runtime Sync Workshop?"
target = "s-overview"

[[search_contract]]
query = "What is the Runtime Sync Workshop?"
expected = "KAIROS_WORKSHOP_README#s-overview"
required_top_k = 1
+++
# Runtime Sync Workshop

## CONTEXT INDEX

- [`s-overview`](#s-overview) — This package is a project-neutral, fail-closed synchronizer for a configured source tree,

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
