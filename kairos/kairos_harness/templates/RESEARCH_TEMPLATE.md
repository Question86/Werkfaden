+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "RESEARCH_XXXX_L0001_V01"
type = "research"
revision = 1
state = "partial"
authority = "research_evidence"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX/RESEARCH_XXXX_L0001_V01"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "State the bounded source fact, not the model conclusion."
claim_boundary = "Source fact and model interpretation are separate; independent verification is required before criterion acceptance."
entities = ["RESEARCH_XXXX_L0001_V01", "TASK_XXXX", "SOURCE_URI"]
facets = ["research", "context-acquisition", "internet"]
criteria = []
does_not_answer = ["independent source truth", "formal criterion acceptance"]
source_uri = "SOURCE_URI"
source_kind = "internet"
source_sha256 = ""

[[answers]]
intent = "evidence"
question = "Which source fact answers the research question?"
target = "s-fact"
language = "en"

[[answers]]
intent = "contradiction"
question = "Which competing evidence remains open?"
target = "s-contradictions"
language = "en"

[refs]
task = "[ref:tasks/task_TASK_XXXX.md#s-acceptance|id:TASK_XXXX|v:1|rel:informs|tags:research,task|src:declared]"

[[search_contract]]
query = "Which source fact answers the research question?"
expected = "RESEARCH_XXXX_L0001_V01#s-fact"
required_top_k = 5
+++
# RESEARCH_XXXX_L0001_V01: TITLE

## CONTEXT INDEX

- [`s-question`](#s-question) — Exact research question.
- [`s-source`](#s-source) — Source identity and retrieval binding.
- [`s-fact`](#s-fact) — Extracted source fact.
- [`s-interpretation`](#s-interpretation) — Model inference, explicitly separate.
- [`s-contradictions`](#s-contradictions) — Competing evidence and excluded branches.
- [`s-next-query`](#s-next-query) — Next information gap.

<a id="s-question"></a>
## RESEARCH QUESTION

> Capsule: State one answerable question.

Write the question.

<a id="s-source"></a>
## SOURCE BINDING

> Capsule: Bind source kind, URI or path, retrieval time, locator, and optional SHA-256.

Write the source binding.

<a id="s-fact"></a>
## EXTRACTED SOURCE FACT

> Capsule: State only what the source directly supports.

Write the bounded fact.

<a id="s-interpretation"></a>
## MODEL INTERPRETATION

> Capsule: State the inference and its uncertainty.

Write the interpretation.

<a id="s-contradictions"></a>
## CONTRADICTIONS AND EXCLUDED BRANCHES

> Capsule: Preserve competing evidence, unresolved ambiguity, and uninspected branches.

Write the contradiction state.

<a id="s-next-query"></a>
## NEXT QUERY

> Capsule: State the next query that most reduces the active goal gap.

Write the next query.
