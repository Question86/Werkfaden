# Quarantined Guardian v1 implementation

These files preserve the pre-v2 implementation byte-for-byte for audit, regression archaeology, and recovery analysis.

They are deliberately stored as `.txt` and are **not supported import or execution surfaces**.

Guardian v1 was retired because its architecture bound PATCH to one transaction, enforced the mutation gate primarily in the CLI, accepted opaque evidence handles, and allowed later Workshop commands to proceed without persistent patch-session authority.

The supported implementation is Guardian v2 in the parent package. The private `_engine_core_v1.py`, `_cli_core_v1.py`, and KAIROS `_search_policy_core_v1.py` loaders may execute selected quarantined synchronization/search logic only from their exact guarded wrapper modules. Direct use is rejected.

Do not repair v2 by re-enabling these files as public modules. If a regression reveals a useful v1 mechanism, move that mechanism behind the current owning v2 boundary and retain the quarantine as historical evidence.
