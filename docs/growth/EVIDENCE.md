+++
schema = "kairos-context/v1"
id = "KAIROS_PUBLIC_EVIDENCE_BASELINE"
type = "documentation"
revision = 1
state = "ready"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PUBLIC_EVIDENCE_BASELINE"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Historical alpha.6 evidence/claim map with explicit limits on marketing and validation assertions."
claim_boundary = "Retains the evidence claims made for the named historical engine. It does not establish current preview regression or release readiness."
entities = ["KAIROS", "KAIROS_PUBLIC_EVIDENCE_BASELINE"]
facets = ["framework-documentation", "historical-evidence"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "historical_evidence"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "d50fb905f4cd71b593fd0614c974f3df9c76b26176136fd8c3686db80b976b3b"

[[answers]]
intent = "historical_evidence"
question = "Which claims were recorded for the historical alpha.6 evidence baseline?"
target = "s-what-we-can-point-to"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Which claims were recorded for the historical alpha.6 evidence baseline?"
expected = "KAIROS_PUBLIC_EVIDENCE_BASELINE#s-what-we-can-point-to"
required_top_k = 5
+++
# Evidence and claim boundaries

## CONTEXT INDEX

- [What we can point to](#s-what-we-can-point-to)
- [Release evidence](#s-release-evidence)
- [What we do not claim](#s-what-we-do-not-claim)
- [Licensing and attribution](#s-licensing-and-attribution)


Historical source material captured from commit `6d8d498c62c411ecad9c49e1ab41e2a72b724e56`. Statements about release, rollout, branch or tests below belong to that source epoch. Current operating contracts and verified current receipts own current behavior. This material does not authorize publication or future execution.

Public brand: Werkfaden. Engine evidence: KAIROS `v0.1.0-alpha.6`, commit `13db71aa841c7859e68f5728d0c925ea76343e20`.

This page distinguishes shipped mechanisms, scripted regression evidence and outcomes not yet measured. Marketing edits on `growth-rollout` do not create a new software seal.

<a id="s-what-we-can-point-to"></a>
## What we can point to

> Capsule: Maps inspectable implementation and test sources to the bounded claims they support.

| Claim | Inspectable source | Boundary |
|---|---|---|
| Workspace-free framework search | [Bootstrap tests](../../kairos/kairos_harness/tests/test_framework_bootstrap.py) | A deterministic retrieval test, not an independent agent trial |
| Static multi-ecosystem source intake and exact Markdown ledgers | [Universal intake tests](../../kickstart/tests/test_universal.py) and [operating contract](../UNIVERSAL_ONBOARDING.md) | Does not establish dynamic imports or runtime/build behavior |
| Existing Python, TypeScript, Rust and mixed-source transactions | [Workshop roundtrip tests](../../kickstart/tests/test_universal_workshop.py) | Fixture evidence, not every possible foreign repository |
| Static source create/delete/rename plus nested-workspace and orphan recovery regression | [Source-set tests](../../kickstart/tests/test_universal_source_set.py) | Does not expand the generic path to unsupported build/config changes |
| Direct C-family include-edge authority propagated through a transaction | [Include-edge tests](../../workshop/tests/test_include_edge_authority.py) | Conservative compiler-guided literal-include analysis, not complete preprocessor emulation |
| Application-level write boundary | [Universal contract](../UNIVERSAL_ONBOARDING.md#s-single-mutation-boundary) and [security boundary](../../workshop/SECURITY_BOUNDARY.md) | OS permissions must be configured separately; a clone is not a universal hostile-code sandbox |

<a id="s-release-evidence"></a>
## Release evidence

> Capsule: Records the historical alpha.6 seal evidence and its retention limits; it is not a fresh preview validation.

The [alpha.6 final-seal run](https://github.com/Question86/Werkfaden/actions/runs/34479342556) records 145 harness, 34 Kickstart and 26 Workshop regression tests: 205 total. Its scripted cold-start matrix has 10 cases, each reported as `POSTCHECK_VERIFIED` and bit-exact. It also records the write-boundary audit and deterministic framework/ZIP checks.

The [sealed artifact pair](https://github.com/Question86/Werkfaden/actions/runs/34479342556/artifacts/10152933992) contains the release ZIP and seal manifest. Actions artifacts have retention limits; do not treat their URLs as permanent release hosting.

Source ZIP: `KAIROS-GOLDEN-v0.1.0-alpha.6.zip`.

SHA-256: `10bc8ac3c02ec87b46edd3b3f20fc79e959b1331b0606d1dc2eaa63e8051a356`.

Size recorded by the seal: 825,846 bytes. The GitHub Actions outer artifact ZIP has a different digest; do not compare it with the inner source ZIP digest. GitHub's autogenerated tag download is also not promised to have the same ZIP bytes.

<a id="s-what-we-do-not-claim"></a>
## What we do not claim

> Capsule: No controlled accuracy, speed, token-saving, adoption or production-certification result is established here.

No controlled baseline comparison is recorded here for model accuracy, token savings, developer speed or fewer hallucinations. No independent-user adoption count, universal compatibility claim, production certification or bug-free guarantee is implied by the regression counts.

A successful postcheck does not prove application builds, runtime correctness, security or scientific correctness. Those require project-specific evidence. A natural-language answer still depends on the model's interpretation of retrieved evidence.

<a id="s-licensing-and-attribution"></a>
## Licensing and attribution

> Capsule: The actual license owns usage rights; presentation text and attribution grant no additional permission.

The [license text](../../LICENSE), not the brand name or a social post, grants usage rights. It currently restricts use to personal, private, non-commercial projects unless written permission is obtained. Do not call this OSI open source or invite unlicensed public forks and redistribution.

Attribute the project to Yannick Wende. Cite the exact engine tag/commit for a technical result and include the command/environment used. Edited demos must disclose cuts; screenshots must show real output. Public bug reports and critiques remain evidence, not material to conceal.
