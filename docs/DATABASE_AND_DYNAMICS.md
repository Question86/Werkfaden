+++
schema = "kairos-context/v1"
id = "KAIROS_DATABASE_DYNAMICS"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_DATABASE_DYNAMICS"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Explains the derived SQLite projection, dynamic state, freshness and the boundary between source authority and retrieval state."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_DATABASE_DYNAMICS"]
facets = ["sqlite", "derived-state", "freshness"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "database"
question = "What is the role of SQLite in KAIROS?"
target = "s-overview"

[[answers]]
intent = "authority"
question = "Is the KAIROS database authoritative or derived?"
target = "s-overview"

[[search_contract]]
query = "What is the role of SQLite in KAIROS?"
expected = "KAIROS_DATABASE_DYNAMICS#s-overview"
required_top_k = 1
+++
# Database and dynamics

## CONTEXT INDEX

- [`s-overview`](#s-overview) — The .kairos/kairos.db file is a derived, verified projection. It stores artifacts, stable

<a id="s-overview"></a>
## Overview

> Capsule: The .kairos/kairos.db file is a derived, verified projection. It stores artifacts, stable

KAIROS has two deliberately separate SQLite projection classes. The packaged
`kairos/kairos_harness/kairos/framework.db` is an immutable, hash-verified projection of the
admitted framework Markdown and exists before any project workspace. It serves workspace-free
`kairos search` bootstrap queries and is rebuilt only from the framework source inventory.

A project workspace later owns `.kairos/kairos.db`, a mutable derived projection of that
project's governed sources. It stores artifacts, stable sections, answer handles, typed relations,
goal coverage, receipts and governance facts; it is never edited directly. `heartbeat` is the
project projection writer and is idempotent for the same source bytes. Framework and project
projections are not merged or copied into one another.

Dynamic files such as `current.json`, `_LOOP_GATE.md`, `ACTIVE.md`, and `NEURAL_CORTEX.md`
are bounded views of the current frontier. They are regenerated from authoritative sources
and receipts. Event/receipt state exists for diagnosis and recovery but is not a second
project truth.

For a fresh external project, the database does not exist before the project intent and
compiler-backed source documents are materialized. A refused governed command on an
unmaterialized source snapshot must not create the database as a side effect.

Project intake promotes goal/task scope, implementation mirrors and source-index authority
into the database in one bounded initialization. Later Workshop apply uses the heartbeat as
the only live projection writer. When an owner-only include relationship changes,
`PROJECT_SOURCE_INDEX.md` and the affected implementation document are promoted together so
the database cannot remain on the previous topology while the code moves forward.
