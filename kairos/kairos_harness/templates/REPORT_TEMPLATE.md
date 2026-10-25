+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "REPORT_TASK_XXXX_L0001_V01"
type = "report"
revision = 1
state = "partial"
authority = "execution_evidence"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX/REPORT_TASK_XXXX_L0001_V01"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "State the observed outcome without exceeding executed evidence."
claim_boundary = "Only checks listed in the evidence section count as validated."
entities = ["REPORT_TASK_XXXX_L0001_V01", "TASK_XXXX"]
facets = ["execution", "validation", "heartbeat"]
criteria = []
does_not_answer = ["external release approval"]

[[answers]]
intent = "current_state"
question = "What outcome did TASK_XXXX produce?"
target = "s-outcome"
language = "en"

[[answers]]
intent = "validation"
question = "Which evidence validates the reported outcome?"
target = "s-evidence"
language = "en"

[refs]
parent = "[ref:tasks/task_TASK_XXXX.md#s-objective|id:TASK_XXXX|v:1|rel:documents|tags:objective,task|src:declared]"

[[search_contract]]
query = "Which evidence validates the reported outcome?"
expected = "REPORT_TASK_XXXX_L0001_V01#s-evidence"
required_top_k = 5
+++
# REPORT: TASK_XXXX — TITLE

## CONTEXT INDEX

- [`s-outcome`](#s-outcome) — Bounded result.
- [`s-work`](#s-work) — Work actually performed.
- [`s-evidence`](#s-evidence) — Commands, receipts, and observations.
- [`s-limitations`](#s-limitations) — Unverified scope and excluded claims.
- [`s-next`](#s-next) — Next required action.

<a id="s-outcome"></a>
## OUTCOME

> Capsule: State the bounded result.

Write the outcome.

<a id="s-work"></a>
## WORK PERFORMED

> Capsule: List only actions that actually occurred.

Write the work.

<a id="s-evidence"></a>
## VALIDATION EVIDENCE

> Capsule: Bind exact commands, hashes, receipts, and observed outputs.

Write the evidence.

<a id="s-limitations"></a>
## LIMITATIONS AND OPEN WORK

> Capsule: Preserve every unresolved boundary and excluded branch.

Write the limitations.

<a id="s-next"></a>
## NEXT ACTION

> Capsule: State the next evidence-producing action.

Write the next action.
