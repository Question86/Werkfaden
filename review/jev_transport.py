#!/usr/bin/env python3
"""Single direct TypeSafe Jev transport.

The route is fixed to the TypeSafe endpoint and deliberately bypasses the
machine's dead local proxy configuration.  The credential is never printed,
included in receipts, or emitted in diagnostics.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_CREDENTIAL_TARGET = "Kairos/TypeSafe/Jev"
DEFAULT_RETRY_ATTEMPTS = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
WINDOWS_CREDENTIAL_TYPE_GENERIC = 1
WINDOWS_CREDENTIAL_PERSIST_LOCAL_MACHINE = 2
DIRECT_TYPESAFE_OPENER = build_opener(ProxyHandler({}))


class JevTransportError(RuntimeError):
    """Raised when the configured direct Jev transport cannot be used."""


def credential_target() -> str:
    return os.environ.get("JEV_CREDENTIAL_TARGET", DEFAULT_CREDENTIAL_TARGET)


def _read_secret_file(variable_name: str) -> str | None:
    raw_path = os.environ.get(variable_name)
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise JevTransportError(
            f"{variable_name} points to an unreadable credential file: {path}"
        ) from exc
    if not value:
        raise JevTransportError(f"{variable_name} points to an empty credential file: {path}")
    return value


def _windows_credential_type():
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return Credential


def read_windows_credential(target: str | None = None) -> str | None:
    """Read the current user's generic Credential Manager value if present."""

    if os.name != "nt":
        return None
    target = target or credential_target()
    try:
        from ctypes import wintypes

        credential_type = _windows_credential_type()
        advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_read = advapi.CredReadW
        cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(credential_type)),
        ]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        cred_free.restype = wintypes.BOOL

        result = ctypes.POINTER(credential_type)()
        if not cred_read(target, WINDOWS_CREDENTIAL_TYPE_GENERIC, 0, ctypes.byref(result)):
            return None
        try:
            record = result.contents
            if not record.CredentialBlob or not record.CredentialBlobSize:
                return None
            raw = ctypes.string_at(record.CredentialBlob, int(record.CredentialBlobSize))
            value = raw.decode("utf-8").strip()
            return value or None
        finally:
            cred_free(result)
    except Exception:
        # Missing Credential Manager support is equivalent to a missing value;
        # the caller still gets the actionable, non-secret setup instruction.
        return None


def save_windows_credential(secret: str, target: str | None = None) -> None:
    """Store a Jev key in the current user's Windows Credential Manager."""

    if os.name != "nt":
        raise JevTransportError("the persistent Jev credential store requires Windows Credential Manager")
    secret = secret.strip()
    if not secret:
        raise JevTransportError("refusing to store an empty Jev credential")
    blob = secret.encode("utf-8")
    target = target or credential_target()

    try:
        from ctypes import wintypes

        credential_type = _windows_credential_type()
        advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_write = advapi.CredWriteW
        cred_write.argtypes = [ctypes.POINTER(credential_type), wintypes.DWORD]
        cred_write.restype = wintypes.BOOL

        blob_buffer = ctypes.create_string_buffer(blob)
        record = credential_type(
            0,
            WINDOWS_CREDENTIAL_TYPE_GENERIC,
            target,
            None,
            wintypes.FILETIME(),
            len(blob),
            ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte)),
            WINDOWS_CREDENTIAL_PERSIST_LOCAL_MACHINE,
            0,
            None,
            None,
            None,
        )
        if not cred_write(ctypes.byref(record), 0):
            error = ctypes.get_last_error()
            raise OSError(error, "CredWriteW failed")
    except JevTransportError:
        raise
    except Exception as exc:
        raise JevTransportError(f"could not store the Jev credential in Windows Credential Manager: {exc}") from exc


def resolve_api_key() -> str:
    """Read the current TypeSafe credential without logging it."""

    key = read_windows_credential()
    if not key:
        raise JevTransportError("Jev credential is missing from Windows Credential Manager")
    return key


def call_jev(
    state: dict[str, Any],
    questions: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Send one direct TypeSafe Jev request and return its JSON object."""

    api_url = DEFAULT_API_URL
    api_key = resolve_api_key()
    payload = json.dumps(
        {
            "model": DEFAULT_MODEL,
            "state": state,
            "questions": questions,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        attempts = max(1, int(os.environ.get("JEV_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS)))
    except ValueError:
        attempts = DEFAULT_RETRY_ATTEMPTS
    try:
        backoff = max(0.0, float(os.environ.get("JEV_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS)))
    except ValueError:
        backoff = DEFAULT_RETRY_BACKOFF_SECONDS

    retryable_http = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(attempts):
        request = Request(
            api_url,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with DIRECT_TYPESAFE_OPENER.open(request, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()[:2000]
            except Exception:
                detail = ""
            if exc.code in retryable_http and attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
                continue
            suffix = f": {detail}" if detail else ""
            raise JevTransportError(f"Jev request failed: HTTP {exc.code} {exc.reason}{suffix}") from exc
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
                continue
            raise JevTransportError(f"Jev request failed: {exc}") from exc
        except Exception as exc:
            raise JevTransportError(f"Jev request failed: {exc}") from exc
    else:  # pragma: no cover - loop either breaks or raises
        raise JevTransportError("Jev request failed after bounded retries")

    if not isinstance(decoded, dict):
        raise JevTransportError("Jev request returned a non-object JSON response")
    return decoded
