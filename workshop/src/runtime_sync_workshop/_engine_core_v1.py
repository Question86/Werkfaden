from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .util import WorkshopError

_ALLOWED_REQUESTER = "runtime_sync_workshop.engine"
_SOURCE = Path(__file__).resolve().parent / "quarantine" / "engine_v1.py.txt"
_VIRTUAL_FILE = Path(__file__).resolve().parent / "engine.py"


def load_engine_core() -> tuple[type[Any], set[str], set[str]]:
    """Load the quarantined v1 synchronization core only for the guarded public engine.

    The old implementation is retained byte-for-byte for audit/recovery, but it is no longer
    an importable WorkshopEngine API. All supported callers receive the guarded engine.py class.
    """
    caller = inspect.currentframe().f_back
    caller_name = str(caller.f_globals.get("__name__", "")) if caller else ""
    caller_file = Path(str(caller.f_globals.get("__file__", ""))).resolve() if caller and caller.f_globals.get("__file__") else None
    expected_file = Path(__file__).resolve().parent / "engine.py"
    if caller_name != _ALLOWED_REQUESTER or caller_file != expected_file:
        raise WorkshopError(
            "QUARANTINED_ENGINE_DIRECT_USE",
            "the v1 engine core is quarantined and may only be loaded by the guarded public engine module",
        )
    if not _SOURCE.is_file():
        raise WorkshopError("QUARANTINED_ENGINE_MISSING", f"quarantined engine source is missing: {_SOURCE}")
    namespace: dict[str, Any] = {
        "__name__": "runtime_sync_workshop._engine_core_v1_runtime",
        "__package__": "runtime_sync_workshop",
        "__file__": str(_VIRTUAL_FILE),
    }
    code = compile(_SOURCE.read_text(encoding="utf-8"), str(_SOURCE), "exec")
    exec(code, namespace, namespace)
    core = namespace.get("WorkshopEngine")
    if not isinstance(core, type):
        raise WorkshopError("QUARANTINED_ENGINE_INVALID", "quarantined source did not define WorkshopEngine")
    return core, set(namespace["PRE_APPLY_STATES"]), set(namespace["TERMINAL_STATES"])
