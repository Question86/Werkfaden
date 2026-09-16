from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .util import sha256_text


class GuardianBridgeError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _guardian_required(raw: dict[str, Any]) -> bool:
    forced = os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold()
    return bool(raw.get("guardian_required", False)) or forced in {"1", "true", "yes", "on"}


def _config(workspace: Path) -> tuple[Path, dict[str, Any]] | None:
    path = workspace / ".kairos" / "workshop.config.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardianBridgeError(f"cannot read Workshop config: {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != "runtime-sync-workshop-config/v1":
        raise GuardianBridgeError(f"unsupported Workshop config: {path}")
    return path, raw


def _state_directory(config_path: Path, raw: dict[str, Any]) -> Path:
    value = str(raw.get("state_directory", ".state")).replace("\\", "/").strip()
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise GuardianBridgeError("Workshop guardian state_directory must be config-relative")
    root = config_path.parent.resolve()
    state = (root / relative).resolve()
    try:
        state.relative_to(root)
    except ValueError as exc:
        raise GuardianBridgeError("Workshop guardian state_directory escapes the config root") from exc
    return state


def _verify_chain(payload: dict[str, Any]) -> None:
    previous = "0" * 64
    for index, event in enumerate(payload.get("events") or [], 1):
        if not isinstance(event, dict) or event.get("prev_hash") != previous:
            raise GuardianBridgeError(f"Guardian ledger event {index} has an invalid previous hash")
        body = {key: value for key, value in event.items() if key != "event_hash"}
        expected = sha256_text(_canonical_json(body))
        if event.get("event_hash") != expected:
            raise GuardianBridgeError(f"Guardian ledger event {index} hash mismatch")
        previous = expected
    if payload.get("head_hash") != previous:
        raise GuardianBridgeError("Guardian ledger head hash mismatch")


def _runtime_scope(workspace: Path) -> dict[str, Any]:
    from .governance import runtime_scope
    return runtime_scope(workspace)


def active_guardian_context(
    workspace: Path,
    *,
    expected_step: str | None = None,
    require: bool = False,
) -> dict[str, Any] | None:
    workspace = Path(workspace).resolve()
    loaded = _config(workspace)
    if loaded is None:
        if require and os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold() in {"1", "true", "yes", "on"}:
            raise GuardianBridgeError("Guardian is forced on but the canonical Workshop config is missing")
        return None
    config_path, raw = loaded
    if not _guardian_required(raw):
        return None
    state_root = _state_directory(config_path, raw) / "guardian-v2"
    scope = _runtime_scope(workspace)
    candidates: list[dict[str, Any]] = []
    if state_root.is_dir():
        for path in sorted(state_root.glob("GRD2_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise GuardianBridgeError(f"cannot read Guardian ledger {path}: {exc}") from exc
            if payload.get("schema") != "werkfaden-guardian/v2" or payload.get("closed"):
                continue
            _verify_chain(payload)
            project = payload.get("project_scope") or {}
            if project.get("task_id") and project.get("task_id") != scope.get("task_id"):
                continue
            if project.get("criterion_id") and scope.get("criterion_id") not in {project.get("criterion_id"), None}:
                continue
            gate = payload.get("active_gate")
            if not isinstance(gate, dict):
                continue
            if expected_step and gate.get("step") != expected_step:
                continue
            candidates.append({
                "guardian_id": payload.get("guardian_id"),
                "state_ticket_id": gate.get("ticket_id"),
                "step": gate.get("step"),
                "opened_at": gate.get("opened_at"),
                "head_hash_before": gate.get("head_hash_before"),
                "task_id": project.get("task_id"),
                "criterion_id": project.get("criterion_id"),
            })
    if len(candidates) == 1:
        return candidates[0]
    if require:
        if not candidates:
            suffix = f" at {expected_step}" if expected_step else ""
            raise GuardianBridgeError(f"Guardian source policy requires exactly one active session gate{suffix}")
        raise GuardianBridgeError("multiple Guardian sessions could authorize the same source operation")
    return None


def source_permit_guard(workspace: Path, purpose: str) -> dict[str, Any] | None:
    loaded = _config(Path(workspace).resolve())
    forced = os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold() in {"1", "true", "yes", "on"}
    if loaded is None:
        if forced:
            raise GuardianBridgeError("Guardian is forced on but Workshop config is missing")
        return None
    _, raw = loaded
    if not _guardian_required(raw):
        return None
    if purpose in {"exact_file_request", "metadata_repair"}:
        raise GuardianBridgeError(
            f"source-inspection exception {purpose!r} is not admissible during an active guarded development workflow"
        )
    expected = "COUNTERPROBE" if purpose == "negative_proof" else "EXACT_SOURCE"
    return active_guardian_context(workspace, expected_step=expected, require=True)


def source_search_guard(workspace: Path, guardian: dict[str, Any] | None, purpose: str) -> None:
    loaded = _config(Path(workspace).resolve())
    forced = os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold() in {"1", "true", "yes", "on"}
    if loaded is None:
        if forced:
            raise GuardianBridgeError("Guardian is forced on but Workshop config is missing")
        return
    _, raw = loaded
    if not _guardian_required(raw):
        return
    if purpose in {"exact_file_request", "metadata_repair"}:
        raise GuardianBridgeError(
            f"source-inspection exception {purpose!r} cannot be consumed during an active guarded development workflow"
        )
    expected = "COUNTERPROBE" if purpose == "negative_proof" else "EXACT_SOURCE"
    current = active_guardian_context(workspace, expected_step=expected, require=True)
    if not isinstance(guardian, dict):
        raise GuardianBridgeError("guarded source permit is not bound to a Guardian state ticket")
    for key in ("guardian_id", "state_ticket_id", "step"):
        if guardian.get(key) != current.get(key):
            raise GuardianBridgeError("source permit Guardian binding no longer matches the active state gate")
