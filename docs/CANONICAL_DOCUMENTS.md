+++
schema = "kairos-context/v1"
id = "KAIROS_CANONICAL_DOCUMENTS"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_CANONICAL_DOCUMENTS"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Defines the canonical KAIROS document surfaces and their epistemic roles."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_CANONICAL_DOCUMENTS"]
facets = ["documents", "authority", "placement"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "documents"
question = "Which documents are canonical in KAIROS and what are their roles?"
target = "s-overview"

[[search_contract]]
query = "Which documents are canonical in KAIROS and what are their roles?"
expected = "KAIROS_CANONICAL_DOCUMENTS#s-overview"
required_top_k = 1
+++
# Canonical documents

## CONTEXT INDEX

- [`s-overview`](#s-overview) — The initializer creates a minimal generic set of authoritative documents:

<a id="s-overview"></a>
## Overview

> Capsule: The initializer creates a minimal generic set of authoritative documents:

The initializer creates a minimal generic set of authoritative documents:

- `AGENTS.md` — operating contract and authority boundaries;
- `goals/` — the starter objective, milestone, and criteria;
- `tasks/` — the first bounded task;
- `reports/`, `bugs/`, and `code/` — typed evidence templates populated by the starter;
- `docs/KAIROS_STARTER_ARCHITECTURE.md` — the self-model used for first retrieval;
- `archive/` and `.kairos/` — generated loop and projection surfaces.

The harness templates under `kairos/kairos_harness/templates/` define the schemas for later
task, report, bug, code, decision, research, and router documents. Every maintained source
document must carry a complete `kairos-context/v1` header, stable section anchors, typed
references, and a resolvable search contract.
