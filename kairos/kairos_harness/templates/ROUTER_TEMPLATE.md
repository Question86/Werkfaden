+++
template_only = true
template_warning = "Placeholders are not live facts, authority, evidence, or searchable workspace content; replace every placeholder before validation and promotion."
schema = "kairos-context/v1"
id = "KAIROS_ROUTER_XXXX"
type = "router"
revision = 1
state = "ready"
authority = "routing"
workspace = "WORKSPACE_ID"
route = "WORKSPACE_ID/KAIROS_ROUTER_XXXX"
updated_at = "2026-08-02T00:00:00Z"
capsule = "Bounded pointer-only router for one context frontier."
claim_boundary = "This router locates context; evidence remains in referenced artifacts."
entities = ["KAIROS_ROUTER_XXXX", "WORKSPACE_ID"]
facets = ["canonical", "context-routing", "navigation"]
criteria = []
does_not_answer = ["implementation evidence", "unreferenced history"]

[[answers]]
intent = "orientation"
question = "Where does this context route begin?"
target = "s-frontier"
language = "en"

[refs]
primary = "[ref:tasks/task_TASK_XXXX.md#s-objective|id:TASK_XXXX|v:dynamic|rel:references|tags:active,task|src:system]"

[[search_contract]]
query = "Where does this context route begin?"
expected = "KAIROS_ROUTER_XXXX#s-frontier"
required_top_k = 5
+++
# KAIROS ROUTER

## CONTEXT INDEX

- [`s-frontier`](#s-frontier) — Current bounded pointer frontier.
- [`s-next`](#s-next) — Immediate next read or action.

<a id="s-frontier"></a>
## CONTEXT FRONTIER

> Capsule: List no more than eight prioritized typed pointers.

Write the pointer list.

<a id="s-next"></a>
## NEXT ACTION

> Capsule: State the single next read or query.

Write the next route.
