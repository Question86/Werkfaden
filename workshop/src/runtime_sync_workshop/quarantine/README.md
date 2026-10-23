+++
schema = "kairos-context/v1"
id = "WERKFADEN_QUARANTINED_SYNC_CORES"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_QUARANTINED_SYNC_CORES"
updated_at = "2026-10-23T23:52:16Z"
capsule = "Restricted Guardian loaders select metadata-aware cores while retaining historical v1 sources."
claim_boundary = "This describes code placement and supported loaders, not immunity to arbitrary Python code execution."
entities = ["KAIROS", "WERKFADEN_QUARANTINED_SYNC_CORES"]
facets = ["guardian", "metadata", "quarantine"]
criteria = []
does_not_answer = ["AI approval", "live project state"]
[[answers]]
intent = "implementation_location"
question = "Which synchronization core does the guarded Workshop engine load?"
target = "s-overview"
+++

## CONTEXT INDEX

- `s-overview` — Historical files and selected metadata-aware synchronization cores.

<a id="s-overview"></a>
# Quarantined Guardian v1 implementation

These files preserve the pre-v2 implementation byte-for-byte for audit, regression archaeology, and recovery analysis.

They are deliberately stored as `.txt` and are **not supported import or execution surfaces**.

Guardian v1 was retired because its architecture bound PATCH to one transaction, enforced the mutation gate primarily in the CLI, accepted opaque evidence handles, and allowed later Workshop commands to proceed without persistent patch-session authority.

The supported implementation is Guardian v2 in the parent package. The private `_engine_core_v1.py`, `_cli_core_v1.py`, and KAIROS `_search_policy_core_v1.py` loaders may execute selected quarantined synchronization/search logic only from their exact guarded wrapper modules. Direct use is rejected.

Do not repair v2 by re-enabling these files as public modules. If a regression reveals a useful v1 mechanism, move that mechanism behind the current owning v2 boundary and retain the quarantine as historical evidence.

The metadata branch integration adds `engine_metadata_v2.py.txt` and `cli_metadata_v2.py.txt`. These are the current synchronization cores behind the same restricted Guardian loaders. The original v1 files above remain historical; they are no longer the selected core.
