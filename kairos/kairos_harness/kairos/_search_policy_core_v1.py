from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_ALLOWED_REQUESTER = "kairos.search_policy"
_SOURCE = Path(__file__).resolve().parent / "quarantine" / "search_policy_v1.py.txt"
_VIRTUAL_FILE = Path(__file__).resolve().parent / "search_policy.py"


def load_search_policy_core() -> SimpleNamespace:
    caller = inspect.currentframe().f_back
    caller_name = str(caller.f_globals.get("__name__", "")) if caller else ""
    caller_file = Path(str(caller.f_globals.get("__file__", ""))).resolve() if caller and caller.f_globals.get("__file__") else None
    expected_file = Path(__file__).resolve().parent / "search_policy.py"
    if caller_name != _ALLOWED_REQUESTER or caller_file != expected_file:
        raise RuntimeError("quarantined search-policy core may only be loaded by kairos.search_policy")
    if not _SOURCE.is_file():
        raise RuntimeError(f"quarantined search-policy source is missing: {_SOURCE}")
    namespace: dict[str, Any] = {"__name__": "kairos._search_policy_core_v1_runtime", "__package__": "kairos", "__file__": str(_VIRTUAL_FILE)}
    source = _SOURCE.read_text(encoding="utf-8")
    exec(compile(source, str(_VIRTUAL_FILE), "exec"), namespace, namespace)
    return SimpleNamespace(**namespace)
