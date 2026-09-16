from __future__ import annotations

from .guardian_common import *


class GuardianLedgerMixin:
    def __init__(
        self,
        *,
        state_directory: Path,
        kairos_database: Path,
        kairos_workspace: Path | None = None,
        codebase_root: Path | None = None,
        transaction_directory: Path | None = None,
        canonical_memory_file: Path | None = None,
        memory_requirements: dict[str, list[str]] | None = None,
        required_run_validations: Iterable[str] = (),
    ) -> None:
        self.directory = Path(state_directory) / "guardian-v2"
        self.kairos_database = Path(kairos_database)
        self.kairos_workspace = Path(kairos_workspace).resolve() if kairos_workspace else None
        self.codebase_root = Path(codebase_root).resolve() if codebase_root else None
        self.transaction_directory = Path(transaction_directory).resolve() if transaction_directory else None
        self.canonical_memory_file = (
            _memory_facts(Path(canonical_memory_file))["path"] if canonical_memory_file else None
        )
        raw_requirements = memory_requirements or {}
        if not isinstance(raw_requirements, dict):
            raise WorkshopError("GUARDIAN_MEMORY_CONTRACT_INVALID", "guardian_memory_requirements must be an object")
        self.memory_requirements: dict[str, list[str]] = {}
        for key, values in raw_requirements.items():
            step = str(key).strip().upper().replace("-", "_")
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise WorkshopError("GUARDIAN_MEMORY_CONTRACT_INVALID", f"invalid Memory requirement list for {step}")
            self.memory_requirements[step] = list(dict.fromkeys(value.strip() for value in values))
        self.required_run_validations = sorted({str(value).strip() for value in required_run_validations if str(value).strip()})
        self.directory.mkdir(parents=True, exist_ok=True)


    def _path(self, guardian_id: str) -> Path:
        if not guardian_id.startswith("GRD2_") or len(guardian_id) != 29:
            raise WorkshopError("GUARDIAN_ID_INVALID", f"invalid guardian id: {guardian_id}")
        target = (self.directory / f"{guardian_id}.json").resolve()
        try:
            target.relative_to(self.directory.resolve())
        except ValueError as exc:
            raise WorkshopError("GUARDIAN_PATH_ESCAPE", "guardian path escapes state directory") from exc
        return target


    def _lock_path(self, guardian_id: str) -> Path:
        return self.directory / ".locks" / f"{guardian_id}.lock"


    def _verify_chain(self, payload: dict[str, Any]) -> None:
        previous = "0" * 64
        for index, event in enumerate(payload.get("events") or [], 1):
            if event.get("prev_hash") != previous:
                raise WorkshopError("GUARDIAN_LEDGER_TAMPER", f"event {index} previous hash mismatch")
            body = {key: value for key, value in event.items() if key != "event_hash"}
            expected = sha256_text(canonical_json(body))
            if event.get("event_hash") != expected:
                raise WorkshopError("GUARDIAN_LEDGER_TAMPER", f"event {index} hash mismatch")
            previous = expected
        if payload.get("head_hash") != previous:
            raise WorkshopError("GUARDIAN_LEDGER_TAMPER", "guardian head hash mismatch")


    def _load(self, guardian_id: str) -> dict[str, Any]:
        path = self._path(guardian_id)
        if not path.is_file():
            raise WorkshopError("GUARDIAN_MISSING", f"guardian session does not exist: {guardian_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA or payload.get("guardian_id") != guardian_id:
            raise WorkshopError("GUARDIAN_CORRUPT", f"invalid guardian session: {guardian_id}")
        self._verify_chain(payload)
        return payload


    def _append(self, payload: dict[str, Any], kind: str, details: dict[str, Any]) -> dict[str, Any]:
        event = {
            "sequence": len(payload.get("events") or []) + 1,
            "kind": kind,
            "at": utc_now(),
            "prev_hash": payload.get("head_hash", "0" * 64),
            "memory_sha256": payload["memory"]["sha256"],
            "rolling_package_sha256": payload.get("rolling_package_sha256"),
            "details": details,
        }
        event["event_hash"] = sha256_text(canonical_json(event))
        payload.setdefault("events", []).append(event)
        payload["head_hash"] = event["event_hash"]
        payload["revision"] = int(payload.get("revision", 0)) + 1
        payload["updated_at"] = event["at"]
        return event


    def _save(self, payload: dict[str, Any]) -> None:
        self._verify_chain(payload)
        atomic_write_json(self._path(str(payload["guardian_id"])), payload)


    def _scope_now(self) -> dict[str, Any]:
        if not self.kairos_workspace:
            return {"goal_id": None, "milestone_id": None, "task_id": None, "criterion_id": None}
        state_path = self.kairos_workspace / ".kairos" / "runtime_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return {
                "goal_id": state.get("active_goal"),
                "milestone_id": state.get("active_milestone"),
                "task_id": state.get("active_task"),
                "criterion_id": state.get("active_criterion"),
            }
        return {"goal_id": None, "milestone_id": None, "task_id": None, "criterion_id": None}


    def _assert_memory(self, payload: dict[str, Any]) -> None:
        current = _memory_facts(Path(str(payload["memory"]["path"])))
        if current["sha256"] != payload["memory"]["sha256"] or current["size"] != payload["memory"]["size"]:
            raise WorkshopError("GUARDIAN_MEMORY_DRIFT", "canonical Memory changed; restart/reframe from current evidence")


    def _assert_scope_identity(self, payload: dict[str, Any]) -> None:
        current = self._scope_now()
        expected = payload.get("project_scope") or {}
        for key in ("goal_id", "milestone_id", "task_id"):
            if expected.get(key) and current.get(key) != expected.get(key):
                raise WorkshopError(
                    "GUARDIAN_PROJECT_SCOPE_DRIFT",
                    f"project {key} changed during guardian session",
                    details={"expected": expected, "current": current},
                )
        if expected.get("criterion_id") and current.get("criterion_id") not in {expected.get("criterion_id"), None}:
            raise WorkshopError(
                "GUARDIAN_PROJECT_SCOPE_DRIFT",
                "active criterion changed during guardian session",
                details={"expected": expected, "current": current},
            )


    def _assert_prepatch_package(self, payload: dict[str, Any], package_sha256: str) -> None:
        if payload.get("patch_session"):
            return
        if payload.get("baseline_package_sha256") != package_sha256:
            raise WorkshopError(
                "GUARDIAN_BASELINE_DRIFT",
                "governed package changed before PROVE; restart from HUMAN PROBLEM",
                details={"expected": payload.get("baseline_package_sha256"), "current": package_sha256},
            )


    def _assert_rolling_package(self, payload: dict[str, Any], package_sha256: str) -> None:
        expected = payload.get("rolling_package_sha256")
        if expected != package_sha256:
            raise WorkshopError(
                "GUARDIAN_ROLLING_BASELINE_DRIFT",
                "current package does not equal the patch-session rolling baseline",
                details={"expected": expected, "current": package_sha256},
            )
