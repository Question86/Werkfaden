+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "CODE_XXXX_L0001_V01"
type = "code"
revision = 1
state = "ready"
authority = "implementation_documentation"
workspace = "WORKSPACE_ID"
route = "GOAL_XXXX/MILESTONE_XX/TASK_XXXX/CODE_XXXX_L0001_V01"
loop = 1
task = "TASK_XXXX"
goal = "GOAL_XXXX"
milestone = "MILESTONE_XX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "Locate the implementation surfaces, invariants, dependencies, and tests."
claim_boundary = "This navigation document does not prove runtime correctness."
entities = ["CODE_XXXX_L0001_V01", "TASK_XXXX"]
facets = ["implementation", "interfaces", "testing"]
criteria = []
does_not_answer = ["runtime test pass"]

[[answers]]
intent = "implementation_location"
question = "Where is the behavior implemented?"
target = "s-components"
language = "en"

[[answers]]
intent = "validation"
question = "Which tests protect the implementation?"
target = "s-testing"
language = "en"

[refs]
task = "[ref:tasks/task_TASK_XXXX.md#s-objective|id:TASK_XXXX|v:1|rel:implements|tags:implementation,task|src:declared]"

[[search_contract]]
query = "Where is the behavior implemented?"
expected = "CODE_XXXX_L0001_V01#s-components"
required_top_k = 5
+++
# CODE_XXXX_L0001_V01: TITLE

## CONTEXT INDEX

- [`s-purpose`](#s-purpose) — Responsibility and claim boundary.
- [`s-components`](#s-components) — Files, symbols, and interfaces.
- [`s-invariants`](#s-invariants) — Conditions the implementation preserves.
- [`s-dependencies`](#s-dependencies) — Runtime and build dependencies.
- [`s-testing`](#s-testing) — Tests and evidence routes.

<a id="s-purpose"></a>
## PURPOSE

> Capsule: State the implementation responsibility.

Write the purpose.

<a id="s-components"></a>
## COMPONENTS AND SYMBOLS

> Capsule: List exact source paths and symbols.

Write the navigation map.

<a id="s-invariants"></a>
## INVARIANTS

> Capsule: State what must remain true across calls and failures.

Write the invariants.

<a id="s-dependencies"></a>
## DEPENDENCIES

> Capsule: State required modules, services, schemas, and versions.

Write the dependencies.

<a id="s-testing"></a>
## TESTING

> Capsule: Bind each behavior to positive, negative, and failure-boundary tests.

Write the tests.
