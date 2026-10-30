+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_QUARANTINE_NOTICE"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_QUARANTINE_NOTICE"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Explains retained legacy Workshop source placement and its supported-access boundary."
claim_boundary = "Describes retained source placement only; it does not authorize direct legacy execution or prove current runtime behavior."
entities = ["KAIROS", "KAIROS_WORKSHOP_QUARANTINE_NOTICE"]
facets = ["framework-documentation", "quarantine-notice"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "quarantine_notice"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "a2de6d9931bb1b04d61d72b54a5da5b533b737c4222b2c0025f5ab0feb93a096"

[[answers]]
intent = "procedure"
question = "Why does Workshop retain quarantined legacy source?"
target = "s-overview"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Why does Workshop retain quarantined legacy source?"
expected = "KAIROS_WORKSHOP_QUARANTINE_NOTICE#s-overview"
required_top_k = 5
+++
# Quarantined Guardian v1 implementation

## CONTEXT INDEX

- [Overview](#s-overview)


<a id="s-overview"></a>
## Overview

> Capsule: Retained implementation texts support provenance; current executable admission belongs to verified owners and explicit adapters.

These files preserve the pre-v2 implementation byte-for-byte for audit, regression archaeology, and recovery analysis.

They are deliberately stored as `.txt` and are **historical provenance, not blanket approval to import or execute**.

Guardian v1 was retired because its architecture bound PATCH to one transaction, enforced the mutation gate primarily in the CLI, accepted opaque evidence handles, and allowed later Workshop commands to proceed without persistent patch-session authority.

The supported implementation is Guardian v2 in the parent package. The private `_engine_core_v1.py`, `_cli_core_v1.py`, and KAIROS `_search_policy_core_v1.py` loaders may execute selected quarantined synchronization/search logic only from their exact guarded wrapper modules. Direct use is rejected.

Do not repair v2 by re-enabling these files as public modules. If a regression reveals a useful v1 mechanism, move that mechanism behind the current owning v2 boundary and retain the quarantine as historical evidence.

The text-file suffix alone does not prove that a retained resource is inactive. Exact current admission belongs to the canonical entrypoint, explicit compatibility adapter and verified source authority; this inventory is not a complete admission attestation.
