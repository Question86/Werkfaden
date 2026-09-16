from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .util import WorkshopError

_ALLOWED_REQUESTER = "runtime_sync_workshop.cli"
_SOURCE = Path(__file__).resolve().parent / "quarantine" / "cli_v1.py.txt"
_VIRTUAL_FILE = Path(__file__).resolve().parent / "cli.py"


def load_cli_core() -> SimpleNamespace:
    caller = inspect.currentframe().f_back
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
    namespace: dict[str, Any] = {
        "__name__": "runtime_sync_workshop._cli_core_v1_runtime",
        "__package__": "runtime_sync_workshop",
        "__file__": str(_VIRTUAL_FILE),
    }
    exec(compile(_SOURCE.read_text(encoding="utf-8"), str(_SOURCE), "exec"), namespace, namespace)
    return SimpleNamespace(**namespace)
