+++
schema = "kairos-context/v1"
id = "KAIROS_REVIEW_METHOD_ENTRY"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_REVIEW_METHOD_ENTRY"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Navigation alias to the canonical provider-neutral evidence-based review method."
claim_boundary = "This alias adds no second review rule or provider approval; the referenced canonical method owns the method."
entities = ["KAIROS", "KAIROS_REVIEW_METHOD_ENTRY"]
facets = ["framework-documentation", "navigation"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "navigation"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "497b3e3c002c24c88f37f4a18efc3043eb0c7db2ad7c576b5b0d166853ecb3a3"

[[answers]]
intent = "procedure"
question = "Where is the canonical provider-neutral AI review method?"
target = "s-overview"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Where is the canonical provider-neutral AI review method?"
expected = "KAIROS_REVIEW_METHOD_ENTRY#s-overview"
required_top_k = 5
+++
# Evidence-based AI review

## CONTEXT INDEX

- [Overview](#s-overview)


<a id="s-overview"></a>
## Overview

> Capsule: Routes the historical JEV name to the provider-neutral Evidence-Based AI Review method.

The canonical, provider-neutral review method is [Evidence-Based AI Review](../workspace/docs/EVIDENCE_BASED_AI_REVIEW.md), document `WERKFADEN_EVIDENCE_BASED_AI_REVIEW`.

It defines atomic propositions, separate support and evidence-sufficiency judgments, falsification, calibration, request boundaries and exact delivery binding. This entry point is a navigation alias, not a second rule source. JEV transport configuration remains separate from the method.
