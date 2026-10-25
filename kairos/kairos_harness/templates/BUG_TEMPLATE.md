+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "BUG_XXXX_L0001_V01"
type = "bug"
revision = 1
state = "active"
authority = "diagnostic_record"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX/BUG_XXXX_L0001_V01"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "State the failure mechanism, not only the symptom."
claim_boundary = "The diagnosis remains provisional until the regression contract passes."
entities = ["BUG_XXXX_L0001_V01", "TASK_XXXX"]
facets = ["root-cause", "resolution", "regression"]
criteria = []
does_not_answer = ["formal repair acceptance"]

[[answers]]
intent = "root_cause"
question = "Why does BUG_XXXX occur?"
target = "s-root-cause"
language = "en"

[[answers]]
intent = "resolution"
question = "How is BUG_XXXX resolved?"
target = "s-resolution"
language = "en"

[[answers]]
intent = "validation"
question = "Which regression evidence validates the repair?"
target = "s-regression"
language = "en"

[refs]
task = "[ref:tasks/task_TASK_XXXX.md#s-acceptance|id:TASK_XXXX|v:1|rel:informs|tags:criteria,task|src:declared]"

[[search_contract]]
query = "Why does BUG_XXXX occur?"
expected = "BUG_XXXX_L0001_V01#s-root-cause"
required_top_k = 5
+++
# BUG_XXXX_L0001_V01: TITLE

## CONTEXT INDEX

- [`s-observation`](#s-observation) — Reproducible symptom.
- [`s-impact`](#s-impact) — Goal and system impact.
- [`s-root-cause`](#s-root-cause) — Evidence-bound failure mechanism.
- [`s-resolution`](#s-resolution) — Repair design.
- [`s-regression`](#s-regression) — Tests that distinguish repair from narrative.

<a id="s-observation"></a>
## OBSERVATION

> Capsule: State the reproducible symptom and conditions.

Write the observation.

<a id="s-impact"></a>
## IMPACT

> Capsule: State which goal, criterion, or downstream consumer is affected.

Write the impact.

<a id="s-root-cause"></a>
## ROOT CAUSE

> Capsule: State the mechanism and bind its source evidence.

Write the causal chain and point to `s-resolution`.

<a id="s-resolution"></a>
## RESOLUTION DESIGN

> Capsule: State the smallest repair that removes the mechanism.

Write the resolution and point to `s-regression`.

<a id="s-regression"></a>
## REGRESSION CONTRACT

> Capsule: Define the positive, negative, and idempotency checks.

Write the regression tests.
