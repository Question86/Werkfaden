+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "TASK_XXXX"
type = "task"
revision = 1
state = "active"
authority = "task_contract"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "State the exact bounded objective and why it advances the active milestone."
claim_boundary = "This contract defines required work and evidence; it does not prove implementation or acceptance."
entities = ["TASK_XXXX", "GOAL_XXXX", "MILESTONE_XX"]
facets = ["goal-decomposition", "task-contract"]
criteria = ["CRIT_XXXX"]
does_not_answer = ["validated outcome", "finalization approval"]

[[answers]]
intent = "goal_gap"
question = "What must TASK_XXXX achieve?"
target = "s-objective"
language = "en"

[[answers]]
intent = "validation"
question = "Which evidence must TASK_XXXX produce?"
target = "s-acceptance"
language = "en"

[refs]
orientation = "[ref:NEURAL_CORTEX.md#s-active-frontier|id:KAIROS_NEURAL_CORTEX|v:dynamic|rel:belongs_to|tags:orientation,router|src:declared]"

[[search_contract]]
query = "Which evidence must TASK_XXXX produce?"
expected = "TASK_XXXX#s-acceptance"
required_top_k = 5
+++
# TASK_XXXX: TITLE

## CONTEXT INDEX

- [`s-objective`](#s-objective) — Exact bounded objective.
- [`s-context`](#s-context) — Goal and milestone position.
- [`s-dependencies`](#s-dependencies) — Required prerequisites.
- [`s-acceptance`](#s-acceptance) — Criterion and evidence contract.
- [`s-next`](#s-next) — Immediate next heartbeat action.

<a id="s-objective"></a>
## OBJECTIVE

> Capsule: Exact bounded objective.

Write the objective.

<a id="s-context"></a>
## CONTEXT

> Capsule: Goal, milestone, and active criterion route.

Write the route.

<a id="s-dependencies"></a>
## DEPENDENCIES

> Capsule: Preconditions that must be true before work starts.

List typed prerequisite pointers.

<a id="s-acceptance"></a>
## ACCEPTANCE CRITERIA

> Capsule: Every criterion requires explicit evidence.

- [ ] `CRIT_XXXX` — description and required evidence.

<a id="s-next"></a>
## NEXT ACTION

> Capsule: One bounded action for the next heartbeat.

State the action and mode.
