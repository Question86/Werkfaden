+++
schema = "kairos-context/v1"
id = "KAIROS_LOOP_PROCEDURE"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_LOOP_PROCEDURE"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Compact Orient-Act-Close-Recover loop procedure for governed KAIROS work."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_LOOP_PROCEDURE"]
facets = ["loop", "workflow", "recovery"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "loop"
question = "What is the KAIROS loop procedure?"
target = "s-overview"

[[search_contract]]
query = "What is the KAIROS loop procedure?"
expected = "KAIROS_LOOP_PROCEDURE#s-overview"
required_top_k = 1
+++
# Loop procedure

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Read workspace/current.json, _LOOP_GATE.md, NEURAL_CORTEX.md, and AGENTS.md.

<a id="s-overview"></a>
## Overview

> Capsule: Read workspace/current.json, _LOOP_GATE.md, NEURAL_CORTEX.md, and AGENTS.md.

### Orient

Read `workspace/current.json`, `_LOOP_GATE.md`, `NEURAL_CORTEX.md`, and `AGENTS.md`.
Search by question before broad inspection.

### Act and checkpoint

Select one criterion and one context mode (`work`, `depth`, `breadth`, `breathe`, or
`verify`). Perform only the bounded action selected by that mode, create the appropriate
source document, promote it, and inspect the heartbeat receipt.

### Close

Close a task only when its required evidence is searchable and validated. `finalize`
requires complete coverage, an immutable archive, and a verified backup. `new-loop` or
`project-kickoff` starts the next numbered frontier only after the predecessor is sealed.

### Recover

If a transaction or heartbeat fails, stop at the reported state. Re-read the state and
receipt, verify the exact baseline, and use the supported recovery command. Never delete a
lease, rewrite a seal, or adopt drift by hand.
