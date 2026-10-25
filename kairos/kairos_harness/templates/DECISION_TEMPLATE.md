+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "DECISION_XXXX_L0001_V01"
type = "decision"
revision = 1
state = "completed"
authority = "architecture_authority"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX/DECISION_XXXX_L0001_V01"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "State the selected option and its operative boundary."
claim_boundary = "This record owns the selected decision and rationale; it does not prove implementation or runtime validation."
entities = ["DECISION_XXXX_L0001_V01", "TASK_XXXX"]
facets = ["decision", "tradeoff", "risk", "rollback", "validation"]
criteria = []
does_not_answer = ["implementation completion", "runtime test pass"]

[[answers]]
intent = "decision"
question = "What did DECISION_XXXX_L0001_V01 decide?"
target = "s-decision"

[[answers]]
intent = "rationale"
question = "Why was DECISION_XXXX_L0001_V01 selected?"
target = "s-rationale"

[refs]
task = "[ref:tasks/task_TASK_XXXX.md#s-acceptance|id:TASK_XXXX|v:1|rel:informs|tags:decision,task|src:declared]"

[[search_contract]]
query = "What did DECISION_XXXX_L0001_V01 decide?"
expected = "DECISION_XXXX_L0001_V01#s-decision"
required_top_k = 5
+++
# DECISION_XXXX_L0001_V01: TITLE

## CONTEXT INDEX

- [`s-question`](#s-question) — Exact decision question.
- [`s-context`](#s-context) — Constraints and authority boundary.
- [`s-options`](#s-options) — Alternatives considered.
- [`s-decision`](#s-decision) — Selected option.
- [`s-rationale`](#s-rationale) — Evidence-bound rationale.
- [`s-risks`](#s-risks) — Residual risk and rollback.
- [`s-validation`](#s-validation) — Checks required after implementation.

<a id="s-question"></a>
## DECISION QUESTION

> Capsule: State one bounded question.

Write the question.

<a id="s-context"></a>
## CONTEXT AND CONSTRAINTS

> Capsule: State the goal, scope, constraints, and authority boundary.

Write the context.

<a id="s-options"></a>
## OPTIONS CONSIDERED

> Capsule: Compare at least the selected option and credible alternatives.

Write the options and tradeoffs.

<a id="s-decision"></a>
## SELECTED DECISION

> Capsule: State exactly what is selected and excluded.

Write the selected decision.

<a id="s-rationale"></a>
## RATIONALE

> Capsule: Bind the selection to evidence and constraints.

Write the rationale.

<a id="s-risks"></a>
## RISKS, REVERSIBILITY, AND ROLLBACK

> Capsule: State residual risks, reversal cost, and rollback triggers.

Write the risk and rollback contract.

<a id="s-validation"></a>
## VALIDATION CONTRACT

> Capsule: Define the checks that must pass after implementation.

Write the validation checks.
