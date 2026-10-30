+++
schema = "kairos-context/v1"
id = "KAIROS_CANONICAL_DOCUMENTS"
type = "documentation"
revision = 2
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_CANONICAL_DOCUMENTS"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Canonical document roles, complete distribution/index coverage and the seven explicitly catalogued render templates."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_CANONICAL_DOCUMENTS"]
facets = ["documents", "authority", "placement", "distribution-coverage", "render-templates"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]
distribution_role = "current_authority"

[[answers]]
intent = "documents"
question = "Which documents are canonical in KAIROS and what are their roles?"
target = "s-overview"

[[answers]]
intent = "templates"
question = "Which Markdown templates are render inputs rather than project evidence?"
target = "s-render-templates"

[[search_contract]]
query = "Which documents are canonical in KAIROS and what are their roles?"
expected = "KAIROS_CANONICAL_DOCUMENTS#s-overview"
required_top_k = 1

[[search_contract]]
query = "Which Markdown templates are render inputs rather than project evidence?"
expected = "KAIROS_CANONICAL_DOCUMENTS#s-render-templates"
required_top_k = 1

[refs]
+++
# Canonical documents

## CONTEXT INDEX

- [Overview](#s-overview)
- [Distribution coverage](#s-distribution-coverage)
- [Render templates](#s-render-templates)

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

<a id="s-distribution-coverage"></a>
## Distribution coverage

> Capsule: Every distributed Markdown file has an explicit source or render-template role; relevant sources must be in the immutable framework index.

The version2 framework index manifest records every distributed Markdown input with an exact
SHA-256 and role. Current procedures, navigation, reference, historical evidence, publication
views and quarantine notices remain separate declared roles. Every such source has a complete
context header, resolvable section answers and actual database admission. A source omitted from
the index, an unknown role or incomplete metadata blocks build/export. The seven named render
inputs below are the only template exceptions; their hashes and this searchable catalog are
recorded too. Historical release material describes its own epoch and grants no current
execution authority. Successful selected queries alone do not prove corpus coverage.

<a id="s-render-templates"></a>
## Render templates

> Capsule: Seven typed Markdown templates are render inputs; this catalog exposes their purpose without treating placeholder content as project evidence.

All paths below are relative to the framework checkout under
kairos/kairos_harness/templates/. Their placeholders become a new project artifact only
through native rendering, validation and promotion; the template itself proves no task,
result, diagnosis or implementation in a consumer project.

| Template | Rendered artifact purpose |
| --- | --- |
| TASK_TEMPLATE.md | Bounded objective and acceptance criteria |
| REPORT_TEMPLATE.md | Performed work, evidence and limitations |
| BUG_TEMPLATE.md | Observed causal defect and resolution frontier |
| CODE_TEMPLATE.md | Exact implementation mapping and source evidence |
| DECISION_TEMPLATE.md | Chosen tradeoff, rationale and validation |
| RESEARCH_TEMPLATE.md | Bounded source facts and evidential limits |
| ROUTER_TEMPLATE.md | Typed navigation to owning authorities |
