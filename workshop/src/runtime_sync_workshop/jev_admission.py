"""Candidate-bound JEV admission for ordinary Workshop Apply.

A local workflow JSON is diagnostic evidence, not a provider-signed authorization.
Policy is opt-in until the caller provisions JEV and sets jev_apply_required=true.
No fallback to a bare reviewer label or hash-only approval is allowed.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .scientific import transaction_work_manifest
from .util import WorkshopError


def require_supported_apply_route(config: Any, route: str) -> None:
    """Do not let special Apply implementations bypass a required AI policy.

    Their work manifests need distinct candidate proof contracts. Until those
    contracts are available, explicitly block rather than accept a normal-TX
    receipt for different authority.
    """
    if config.raw.get("jev_apply_required", False) is True:
        raise WorkshopError(
            "JEV_APPLY_ROUTE_UNSUPPORTED",
            f"jev_apply_required is active but {route} Apply has no candidate-bound JEV route; policy cannot be bypassed",
        )


def candidate_fingerprint(root: Path) -> str:
    work = root / "work"
    if not work.is_dir():
        raise WorkshopError("JEV_WORK_MISSING", "candidate work directory is missing")
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in work.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise WorkshopError("JEV_LINK_UNSAFE", "candidate contains a linked file")
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(work).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if not files:
        raise WorkshopError("JEV_WORK_EMPTY", "candidate work directory is empty")
    payload = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise WorkshopError("JEV_RECEIPT_MISSING", "required JEV review receipt is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, ValueError) as exc:
        raise WorkshopError("JEV_RECEIPT_INVALID", "JEV review receipt is not valid strict JSON") from exc
    if not isinstance(data, dict):
        raise WorkshopError("JEV_RECEIPT_INVALID", "JEV review receipt must be an object")
    return data


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkshopError("JEV_SCORE_INVALID", "JEV score must be a finite numeric probability")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise WorkshopError("JEV_SCORE_INVALID", "JEV score must be between zero and one")
    return number


def require_candidate_jev_clearance(config: Any, root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Reject unbound, stale, simulated or incomplete local workflow approvals.

    This validates evidence binding, not provider identity or semantic truth.
    A separately authenticated provider receipt remains necessary for a
    high-assurance deployment boundary.
    """
    if state.get("state") != "SHADOW_VERIFIED":
        raise WorkshopError("JEV_STATE_INVALID", "JEV admission requires shadow-verified transaction")
    raw = _read_json(root / "review" / "jev_workflow.json")
    if raw.get("schema") != "werkfaden-jev-workshop-attempt-workflow/v1":
        raise WorkshopError("JEV_RECEIPT_SCHEMA", "unexpected JEV workflow schema")
    if raw.get("transaction_id") != state.get("transaction_id"):
        raise WorkshopError("JEV_TX_MISMATCH", "JEV workflow belongs to another transaction")
    if raw.get("decision") != "APPROVED_FOR_NEXT_ACTION" or raw.get("implementation_routing_allowed") is not True:
        raise WorkshopError("JEV_NOT_APPROVED", "JEV workflow does not admit this candidate")
    if raw.get("live_apply_authorized") is not False:
        raise WorkshopError("JEV_RECEIPT_INVALID", "JEV workflow action boundary was changed")
    if (raw.get("attempt_review") or {}).get("mode") != "executed":
        raise WorkshopError("JEV_REVIEW_UNTRUSTED", "only a fresh executed review can support admission")
    fp = raw.get("candidate_fingerprint")
    if not isinstance(fp, dict) or fp.get("sha256") != candidate_fingerprint(root):
        raise WorkshopError("JEV_CANDIDATE_STALE", "JEV workflow does not match the exact current candidate")
    work = transaction_work_manifest(config, root, state)
    if work["package_sha256"] != state.get("verified_work_package_sha256"):
        raise WorkshopError("JEV_VERIFICATION_STALE", "candidate differs from verified Workshop package")
    vector = raw.get("attempt_vector")
    if not isinstance(vector, dict) or vector.get("passed") is not True:
        raise WorkshopError("JEV_VECTOR_REJECTED", "JEV vector not accepted")
    positive, negative = vector.get("positive"), vector.get("negative")
    if not isinstance(positive, dict) or not isinstance(negative, dict) or not positive or not negative:
        raise WorkshopError("JEV_POLICY_EMPTY", "JEV policy must have positive and negative propositions")
    threshold = _score(vector.get("threshold"))
    if threshold <= 0:
        raise WorkshopError("JEV_POLICY_INVALID", "JEV threshold must be positive")
    for key, row in positive.items():
        if not isinstance(row, dict) or _score(row.get("score")) < threshold or row.get("passed") is not True:
            raise WorkshopError("JEV_VECTOR_REJECTED", f"positive review proposition failed: {key}")
    for key, row in negative.items():
        if not isinstance(row, dict) or _score(row.get("score")) > 1.0 - threshold or row.get("passed") is not True:
            raise WorkshopError("JEV_VECTOR_REJECTED", f"negative review proposition failed: {key}")
    return {"candidate_sha256": fp["sha256"], "transaction_id": state["transaction_id"], "reviewed": True}
