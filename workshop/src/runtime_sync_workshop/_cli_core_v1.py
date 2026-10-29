from __future__ import annotations

import inspect
from pathlib import Path
from types import ModuleType

from .util import WorkshopError

_ALLOWED_REQUESTER = "runtime_sync_workshop.cli"
_SOURCE = Path(__file__).resolve().parent / "_cli_implementation.py"


def load_cli_core() -> ModuleType:
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    del frame
    caller_name = str(caller.f_globals.get("__name__", "")) if caller else ""
    caller_file = Path(str(caller.f_globals.get("__file__", ""))).resolve() if caller and caller.f_globals.get("__file__") else None
    expected_file = Path(__file__).resolve().parent / "cli.py"
    if caller_name != _ALLOWED_REQUESTER or caller_file != expected_file:
        raise WorkshopError(
            "QUARANTINED_CLI_DIRECT_USE",
            "the v1 CLI source is quarantined and may only be loaded by the guarded public CLI module",
        )
    if not _SOURCE.is_file():
        raise WorkshopError("QUARANTINED_CLI_MISSING", f"quarantined CLI source is missing: {_SOURCE}")
    from . import _cli_implementation
    return _cli_implementation
