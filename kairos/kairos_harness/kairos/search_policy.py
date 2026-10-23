from __future__ import annotations

import json
from typing import Any

from ._search_policy_core_v1 import load_search_policy_core
from .guardian_bridge import GuardianBridgeError, active_guardian_context, source_permit_guard, source_search_guard

_legacy = load_search_policy_core()
SearchPolicyError = _legacy.SearchPolicyError
NORMAL_PURPOSES = _legacy.NORMAL_PURPOSES
EXCEPTION_PURPOSES = _legacy.EXCEPTION_PURPOSES
ALL_PURPOSES = _legacy.ALL_PURPOSES
MAX_ROUTING_AGE_SECONDS = _legacy.MAX_ROUTING_AGE_SECONDS
MAX_PERMIT_TTL_SECONDS = _legacy.MAX_PERMIT_TTL_SECONDS
MAX_SCOPE_PATHS = _legacy.MAX_SCOPE_PATHS
MAX_SCOPE_PATTERNS = _legacy.MAX_SCOPE_PATTERNS
MAX_SCOPE_FILES = _legacy.MAX_SCOPE_FILES
MAX_SCOPE_BYTES = _legacy.MAX_SCOPE_BYTES
MAX_FILE_BYTES = _legacy.MAX_FILE_BYTES
MAX_INSPECTION_MATCHES = _legacy.MAX_INSPECTION_MATCHES
MAX_EMERGENCY_RECEIPTS = _legacy.MAX_EMERGENCY_RECEIPTS
TEXT_SUFFIXES = _legacy.TEXT_SUFFIXES


def _bridge_error(exc: Exception) -> SearchPolicyError:
    return SearchPolicyError(str(exc))


def _bind_routing_receipt(database, receipt: dict[str, Any], guardian: dict[str, Any] | None) -> dict[str, Any]:
    if not guardian:
        return receipt
    bound = {**receipt, "guardian": dict(guardian)}
    with database.transaction() as connection:
        connection.execute("UPDATE search_routing_receipts SET receipt_json=? WHERE routing_receipt_id=?", (_legacy.json_dumps(bound, pretty=False), receipt["routing_receipt_id"]))
    return bound


def record_routing_receipt(workspace, database, search_result, freshness):
    receipt = _legacy.record_routing_receipt(workspace, database, search_result, freshness)
    try:
        guardian = active_guardian_context(workspace, require=False)
    except GuardianBridgeError as exc:
        raise _bridge_error(exc) from exc
    return _bind_routing_receipt(database, receipt, guardian)


def issue_source_permit(workspace, database, *, routing_receipt_id, purpose, paths, patterns, reason, ttl_seconds=300):
    try:
        guardian = source_permit_guard(workspace, purpose)
    except GuardianBridgeError as exc:
        raise _bridge_error(exc) from exc
    receipt = _legacy.issue_source_permit(workspace, database, routing_receipt_id=routing_receipt_id, purpose=purpose, paths=paths, patterns=patterns, reason=reason, ttl_seconds=ttl_seconds)
    if guardian:
        receipt = {**receipt, "guardian": dict(guardian)}
        with database.transaction() as connection:
            connection.execute("UPDATE source_inspection_permits SET receipt_json=? WHERE permit_id=?", (_legacy.json_dumps(receipt, pretty=False), receipt["permit_id"]))
    return receipt


def execute_source_search(workspace, database, *, permit_id):
    connection = database.connect(read_only=True)
    try:
        row = connection.execute("SELECT purpose,receipt_json FROM source_inspection_permits WHERE permit_id=?", (permit_id,)).fetchone()
    finally:
        connection.close()
    if not row:
        raise SearchPolicyError("source inspection permit does not exist")
    permit = json.loads(row["receipt_json"])
    try:
        source_search_guard(workspace, permit.get("guardian"), str(row["purpose"]))
    except GuardianBridgeError as exc:
        raise _bridge_error(exc) from exc
    receipt = _legacy.execute_source_search(workspace, database, permit_id=permit_id)
    guardian = permit.get("guardian")
    if isinstance(guardian, dict):
        receipt = {**receipt, "guardian": dict(guardian)}
        with database.transaction() as connection:
            connection.execute("UPDATE source_inspection_receipts SET receipt_json=? WHERE inspection_id=?", (_legacy.json_dumps(receipt, pretty=False), receipt["inspection_id"]))
    return receipt


def issue_emergency_metadata_repair_permit(workspace, **kwargs):
    try:
        source_permit_guard(workspace, "metadata_repair")
    except GuardianBridgeError as exc:
        raise _bridge_error(exc) from exc
    return _legacy.issue_emergency_metadata_repair_permit(workspace, **kwargs)


def execute_emergency_source_search(workspace, *, permit_id):
    try:
        source_search_guard(workspace, None, "metadata_repair")
    except GuardianBridgeError as exc:
        raise _bridge_error(exc) from exc
    return _legacy.execute_emergency_source_search(workspace, permit_id=permit_id)


def __getattr__(name: str):
    return getattr(_legacy, name)
