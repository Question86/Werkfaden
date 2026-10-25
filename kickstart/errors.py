from __future__ import annotations

from typing import Any


class KickstartError(RuntimeError):
    """Fail-closed, user-actionable initiation error."""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details is not None:
            payload["details"] = self.details
        return payload

