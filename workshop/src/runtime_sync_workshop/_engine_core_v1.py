from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .util import WorkshopError

_ALLOWED_REQUESTER = "runtime_sync_workshop.engine"
_SOURCE = Path(__file__).resolve().parent / "_engine_implementation.py"


def load_engine_core() -> tuple[type[Any], set[str], set[str]]:
    """Return the regular implementation base only to the guarded public engine."""
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    del frame
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
    from . import _engine_implementation as implementation
    return (implementation.WorkshopEngine, set(implementation.PRE_APPLY_STATES),
            set(implementation.TERMINAL_STATES))
