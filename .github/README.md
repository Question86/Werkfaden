+++
schema = "kairos-context/v1"
id = "KAIROS_PUBLIC_LANDING"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PUBLIC_LANDING"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Navigation-only GitHub entry to the current framework contract, first-use guide and license."
claim_boundary = "This is a presentation view; referenced operating contracts and evidence own procedure and support claims."
entities = ["KAIROS", "KAIROS_PUBLIC_LANDING"]
facets = ["framework-documentation", "publication-view"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "publication_view"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "42716663959fea89d10e336b53ffec033f709980c5aadd61509e2631e252ce0e"

[[answers]]
intent = "procedure"
question = "Where is the current public Werkfaden entrypoint?"
target = "s-current-entry"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Where is the current public Werkfaden entrypoint?"
expected = "KAIROS_PUBLIC_LANDING#s-current-entry"
required_top_k = 5
+++
# Werkfaden

## CONTEXT INDEX

- [Current entry](#s-current-entry)
- [Attribution and license](#s-attribution-and-license)


<a id="s-current-entry"></a>
## Current entry

> Capsule: Routes to the canonical framework entry, reviewed first-use procedure and operating contract.

Use the [canonical framework entry](../README.md), [reviewed first-use procedure](../docs/growth/START_HERE.md) and [operating contract](../docs/UNIVERSAL_ONBOARDING.md). The engine retains the KAIROS command names. This navigation view duplicates no version-specific setup steps or release-readiness claims.

<a id="s-attribution-and-license"></a>
## Attribution and license

> Capsule: Attribution identifies the author; the repository license owns usage permissions.

Built by Yannick Wende. The repository [license](../LICENSE) owns usage terms. Recorded naming context is in [Brand identity](../docs/growth/BRAND.md).
