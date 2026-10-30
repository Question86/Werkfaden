+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_PORT_COMPARISON"
type = "documentation"
revision = 4
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_PORT_COMPARISON"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Project-neutral boundaries between standalone review tools, deterministic Workshop gates and optional candidate-bound Apply admission."
claim_boundary = "This document states framework review boundaries; it does not disclose consumer installations or establish provider approval, deployment or product release."
entities = ["KAIROS", "WERKFADEN_JEV_PORT_COMPARISON"]
facets = ["jev", "ai-review", "limitations", "source-binding"]
criteria = []
does_not_answer = ["consumer project architecture", "live project approval", "runtime correctness"]
distribution_role = "reference"

[[answers]]
intent = "ai_review"
question = "Which boundaries distinguish standalone review from Workshop Apply admission?"
target = "s-overview"

[[answers]]
intent = "source_binding"
question = "What binds review evidence to a candidate?"
target = "s-fresh-publication"

[[search_contract]]
query = "Which boundaries distinguish standalone review from Workshop Apply admission?"
expected = "WERKFADEN_JEV_PORT_COMPARISON#s-overview"
required_top_k = 5

[refs]
+++
# Review integration boundaries

## CONTEXT INDEX

- [Overview](#s-overview)
- [Candidate and evidence binding](#s-fresh-publication)

<a id="s-overview"></a>
## Overview

> Capsule: Standalone review tools and deterministic transaction gates establish different facts.

The optional tools under `review/` submit explicit question profiles, materialize changed source regions and retain request and response evidence. They do not establish consumer project intent, install a consumer policy or prove runtime activation.

Workshop separately owns leases, source and metadata review, shadow verification, Apply, heartbeat, postcheck and reseal. Guardian investigation receipts are deterministic evidence; they are not evidence of an AI provider call.

With `jev_apply_required=true`, ordinary supported Apply requires its matching bound workflow. Mutation routes without that integration reject the request explicitly. Feature flags remain opt-in for compatibility; enabling them requires the documented migration and recovery checks for the selected installation. See [Apply policy](JEV_APPLY_POLICY.md) for the current route boundaries and [operating instructions](README.md) for the actual commands.

<a id="s-fresh-publication"></a>
## Candidate and evidence binding

> Capsule: A review receipt is usable only within its declared candidate, baseline, transaction and question scope.

A source hash proves byte identity. A local reviewer field does not prove independent review. Provider responses are not cryptographic provider attestations unless an actual supported authenticated transport supplies that evidence. A model alias does not pin a provider model version.

Changed candidate bytes invalidate earlier clearance. New candidates need fresh preparation, metadata review and verification, plus the review required by their explicit policy. Source review does not imply review of omitted Markdown or configuration. Corpus Apply and runtime deployment require separate evidence.

Offline mocked transport tests establish protocol and error handling only. See [validation](VALIDATION.md) for recorded test boundaries. Local deployment histories and consumer architecture documents are excluded from the framework distribution.
