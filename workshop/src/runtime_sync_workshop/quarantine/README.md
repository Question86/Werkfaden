+++
schema = "kairos-context/v1"
id = "WERKFADEN_QUARANTINED_SYNC_CORES"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_QUARANTINED_SYNC_CORES"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Supported synchronization is reached through guarded entrypoints; retained historical text is not an independent execution grant."
claim_boundary = "Describes supported entrypoint and retained-source boundaries. Current source-bound maps/tests own exact loaded implementation and execution facts."
entities = ["KAIROS", "WERKFADEN_QUARANTINED_SYNC_CORES"]
facets = ["guardian", "metadata", "quarantine"]
criteria = []
does_not_answer = ["AI approval", "live project state"]
distribution_role = "quarantine_notice"

[[answers]]
intent = "implementation_location"
question = "Which synchronization core does the guarded Workshop engine load?"
target = "s-overview"

[[search_contract]]
query = "Which synchronization core does the guarded Workshop engine load?"
expected = "WERKFADEN_QUARANTINED_SYNC_CORES#s-overview"
required_top_k = 5

[refs]
+++
# Quarantined Guardian v1 implementation

## CONTEXT INDEX
- [Overview](#s-overview)

<a id="s-overview"></a>
## Overview

These files preserve the pre-v2 implementation byte-for-byte for audit, regression archaeology, and recovery analysis.

They are deliberately stored as `.txt` and are **not supported import or execution surfaces**.

Guardian v1 was retired because its architecture bound PATCH to one transaction, enforced the mutation gate primarily in the CLI, accepted opaque evidence handles, and allowed later Workshop commands to proceed without persistent patch-session authority.

The supported implementation is Guardian v2 in the parent package. The private `_engine_core_v1.py`, `_cli_core_v1.py`, and KAIROS `_search_policy_core_v1.py` loaders may execute selected quarantined synchronization/search logic only from their exact guarded wrapper modules. Direct use is rejected.

Do not repair v2 by re-enabling these files as public modules. If a regression reveals a useful v1 mechanism, move that mechanism behind the current owning v2 boundary and retain the quarantine as historical evidence.

The metadata branch formerly introduced engine_metadata_v2.py.txt and cli_metadata_v2.py.txt behind restricted loaders. These historical text artifacts do not by themselves establish the current loaded method owners. The supported surface remains the guarded public WorkshopEngine; current source-bound implementation maps and tests own exact wrapper/composition identity. Do not execute quarantined text directly.