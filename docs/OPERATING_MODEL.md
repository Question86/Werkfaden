+++
schema = "kairos-context/v1"
id = "KAIROS_OPERATING_MODEL"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_OPERATING_MODEL"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Defines the operating model linking Human intent, bounded context acquisition, governed action, same-heartbeat promotion and verification."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_OPERATING_MODEL"]
facets = ["operating-model", "workflow", "context"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "operating_model"
question = "What is the KAIROS operating model?"
target = "s-overview"

[[search_contract]]
query = "What is the KAIROS operating model?"
expected = "KAIROS_OPERATING_MODEL#s-overview"
required_top_k = 1
+++
# Operating model

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Use the same bounded sequence for every material action:

<a id="s-overview"></a>
## Overview

> Capsule: Use the same bounded sequence for every material action:

Use the same bounded sequence for every material action:

1. Read the current frontier, operating contract, and loop gate.
2. Formulate a question that names the decision or dependency to resolve.
3. Search KAIROS first and follow only the typed references needed for the question.
4. Inspect an exact source file only under a valid source permit or a declared read-only
   exception.
5. Perform one bounded implementation or documentation action.
6. Record the result in the document type that owns the claim.
7. Promote and validate it in the same heartbeat; inspect the receipt and health result.
8. Update criterion coverage and checkpoint state before moving to another action.

When a check refuses, preserve the refusal as evidence and repair the owning source of
truth. Do not bypass a gate by changing derived state, widening a path, or inventing a
receipt.
