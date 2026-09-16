from __future__ import annotations

from .guardian_common import *


class GuardianStateMixin:
    def _required_memory_selectors(self, step: str) -> list[str]:
        values = list(self.memory_requirements.get(step, []))
        # A transaction opens both PATCH and WORKSHOP responsibility. Keeping the two
        # authored rule groups separate in Memory is useful; the preflight deliberately
        # returns both before a work tree can be created.
        if step == "PATCH":
            values.extend(self.memory_requirements.get("WORKSHOP", []))
        return list(dict.fromkeys(values))


    def _gate(self, payload: dict[str, Any], step: str, ticket_id: str) -> dict[str, Any]:
        gate = payload.get("active_gate")
        if not isinstance(gate, dict):
            raise WorkshopError("GUARDIAN_MEMORY_GATE_MISSING", f"{step} requires guard-enter before execution")
        if gate.get("step") != step or gate.get("ticket_id") != ticket_id:
            raise WorkshopError(
                "GUARDIAN_MEMORY_GATE_MISMATCH",
                f"active Memory gate does not authorize {step}",
                details={"active_gate": gate.get("step"), "requested": step},
            )
        return gate


    def enter_state(
        self,
        guardian_id: str,
        *,
        step: str,
        memory_selectors: Iterable[str],
        package_sha256: str,
    ) -> dict[str, Any]:
        normalized = str(step).strip().upper().replace("-", "_")
        allowed = set(PRE_PATCH_STEPS) | {"PATCH", "HEARTBEAT", "FRESH_RUN"}
        if normalized not in allowed:
            raise WorkshopError("GUARDIAN_STEP_INVALID", f"unsupported guard-enter state: {normalized}")
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            if payload.get("closed"):
                raise WorkshopError("GUARDIAN_CLOSED", f"guardian session is closed: {guardian_id}")
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            expected = payload.get("next_step")
            if normalized != expected:
                raise WorkshopError(
                    "GUARDIAN_STEP_ORDER",
                    f"expected {expected}, received guard-enter {normalized}",
                )
            if payload.get("active_gate"):
                raise WorkshopError(
                    "GUARDIAN_MEMORY_GATE_ACTIVE",
                    "complete or explicitly abandon the active state gate before entering another state",
                    details={"active_gate": payload["active_gate"].get("step")},
                )
            if normalized in PRE_PATCH_STEPS:
                self._assert_prepatch_package(payload, package_sha256)
            else:
                self._assert_rolling_package(payload, package_sha256)
            required = self._required_memory_selectors(normalized)
            values = required + [str(value).strip() for value in memory_selectors if str(value).strip()]
            values = list(dict.fromkeys(values))
            if not values:
                raise WorkshopError("GUARDIAN_MEMORY_REQUIRED", f"{normalized} requires at least one Memory selector")
            passages, context = self._memory_passages(
                payload,
                normalized,
                values,
                required_override=required,
            )
            ticket_id = "GST_" + sha256_text(
                f"{guardian_id}|{normalized}|{uuid.uuid4().hex}|{payload.get('head_hash')}"
            )[:24]
            evidence_floor = (
                self._evidence_highwater()
                if normalized in {"FALSIFIER_1", "FALSIFIER_2", "MAP", "COUNTERPROBE", "EXACT_SOURCE"}
                else {"srr_rowid": 0, "sir_rowid": 0}
            )
            gate = {
                "ticket_id": ticket_id,
                "step": normalized,
                "opened_at": utc_now(),
                "package_sha256": package_sha256,
                "memory_passages": passages,
                "head_hash_before": payload.get("head_hash"),
                "evidence_floor": evidence_floor,
            }
            payload["active_gate"] = gate
            self._append(payload, "STATE_ENTER", {
                "ticket_id": ticket_id,
                "step": normalized,
                "memory_passages": passages,
            })
            self._save(payload)
            result = self._view(payload)
            result["state_ticket_id"] = ticket_id
            result["memory_context"] = context
            return result


    def _consume_gate(
        self,
        payload: dict[str, Any],
        *,
        step: str,
        ticket_id: str,
        package_sha256: str,
    ) -> dict[str, Any]:
        gate = self._gate(payload, step, ticket_id)
        if gate.get("package_sha256") != package_sha256:
            raise WorkshopError(
                "GUARDIAN_MEMORY_GATE_BASELINE_DRIFT",
                "governed package changed after the state Memory gate opened",
                details={"gate": gate.get("package_sha256"), "current": package_sha256},
            )
        return gate


    def start(
        self,
        *,
        problem: str,
        memory_file: Path,
        memory_selectors: Iterable[str],
        package_sha256: str,
    ) -> dict[str, Any]:
        problem = str(problem).strip()
        if not 12 <= len(problem) <= 8000:
            raise WorkshopError("GUARDIAN_PROBLEM_INVALID", "Human problem must contain 12 to 8000 characters")
        memory = _memory_facts(memory_file)
        if self.canonical_memory_file and memory["path"] != self.canonical_memory_file:
            raise WorkshopError(
                "GUARDIAN_MEMORY_IDENTITY_MISMATCH",
                "guard-start Memory path does not equal the configured canonical Memory authority",
                details={"configured": self.canonical_memory_file, "requested": memory["path"]},
            )
        raw_memory = Path(memory["path"]).read_bytes()
        if sha256_bytes(raw_memory) != memory["sha256"]:
            raise WorkshopError("GUARDIAN_MEMORY_DRIFT", "canonical Memory changed during guardian start")
        try:
            text = raw_memory.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkshopError("GUARDIAN_MEMORY_INVALID", "canonical Memory is not UTF-8") from exc
        selectors = list(self.memory_requirements.get("HUMAN_PROBLEM", []))
        selectors.extend(str(value).strip() for value in memory_selectors if str(value).strip())
        selectors = list(dict.fromkeys(selectors))
        if not selectors:
            raise WorkshopError("GUARDIAN_MEMORY_REQUIRED", "HUMAN PROBLEM requires at least one Memory selector")
        passages = [_passage(text, selector) for selector in selectors]
        created_at = utc_now()
        guardian_id = "GRD2_" + sha256_text(f"{uuid.uuid4().hex}|{created_at}|{package_sha256}|{memory['sha256']}|{problem}")[:24]
        payload = {
            "schema": SCHEMA,
            "guardian_id": guardian_id,
            "revision": 0,
            "created_at": created_at,
            "updated_at": created_at,
            "closed": False,
            "baseline_package_sha256": package_sha256,
            "rolling_package_sha256": package_sha256,
            "memory": memory,
            "project_scope": self._scope_now(),
            "current_step": "HUMAN_PROBLEM",
            "next_step": "HYPOTHESIS",
            "mapped_sources": [],
            "evidence": {},
            "patch_session": None,
            "active_gate": None,
            "events": [],
            "head_hash": "0" * 64,
        }
        self._append(payload, "HUMAN_PROBLEM", {
            "problem": problem,
            "problem_sha256": sha256_text(problem),
            "memory_passages": [{key: item[key] for key in ("selector", "start_line", "end_line", "sha256", "bytes")} for item in passages],
        })
        self._save(payload)
        result = self._view(payload)
        result["memory_context"] = passages
        return result


    def _memory_passages(self, payload: dict[str, Any], step: str, selectors: Iterable[str], *, required_override: Iterable[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        required = list(required_override) if required_override is not None else self._required_memory_selectors(step)
        values = required + [str(value).strip() for value in selectors if str(value).strip()]
        values = list(dict.fromkeys(values))
        if not values:
            raise WorkshopError("GUARDIAN_MEMORY_REQUIRED", f"{step} requires at least one Memory selector")
        memory_path = Path(payload["memory"]["path"])
        raw = memory_path.read_bytes()
        if sha256_bytes(raw) != payload["memory"]["sha256"]:
            raise WorkshopError("GUARDIAN_MEMORY_DRIFT", "canonical Memory changed during passage retrieval")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkshopError("GUARDIAN_MEMORY_INVALID", "canonical Memory is not UTF-8") from exc
        passages = [_passage(text, value) for value in values]
        ledger = [
            {
                **{key: p[key] for key in ("selector", "start_line", "end_line", "sha256", "bytes")},
                "for_step": step,
                "required": p["selector"] in required,
            }
            for p in passages
        ]
        context = [
            {
                "selector": p["selector"],
                "start_line": p["start_line"],
                "end_line": p["end_line"],
                "sha256": p["sha256"],
                "required": p["selector"] in required,
                "text": p["text"],
            }
            for p in passages
        ]
        return ledger, context


    @staticmethod
    def _event_time(payload: dict[str, Any], kind: str) -> datetime:
        rows = [event for event in payload.get("events") or [] if event.get("kind") == kind]
        if not rows:
            raise WorkshopError("GUARDIAN_STATE_EVIDENCE_MISSING", f"no prior {kind} event is bound to this session")
        return _parse_time(str(rows[-1]["at"]))


    def _evidence_after(self, payload: dict[str, Any], purpose: str) -> datetime:
        predecessor = {
            "falsifier1": "HYPOTHESIS",
            "falsifier2": "FALSIFIER_1",
            "map": "FALSIFIER_2",
            "counterprobe": "MAP",
            "exact_source": "COUNTERPROBE",
        }.get(purpose)
        return self._event_time(payload, predecessor) if predecessor else _parse_time(payload["created_at"])


    def status(self, guardian_id: str, *, full: bool = False) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            result = self._view(payload)
            if full:
                result["memory"] = payload["memory"]
                result["project_scope"] = payload["project_scope"]
                result["events"] = payload["events"]
                result["evidence"] = payload["evidence"]
                result["patch_session"] = payload["patch_session"]
                result["fresh_run"] = payload.get("fresh_run")
            return result


    @staticmethod
    def _view(payload: dict[str, Any]) -> dict[str, Any]:
        session = payload.get("patch_session") or {}
        return {
            "schema": SCHEMA,
            "guardian_id": payload["guardian_id"],
            "revision": payload.get("revision", 0),
            "head_hash": payload.get("head_hash"),
            "current_step": payload.get("current_step"),
            "next_step": payload.get("next_step"),
            "closed": bool(payload.get("closed")),
            "baseline_package_sha256": payload.get("baseline_package_sha256"),
            "rolling_package_sha256": payload.get("rolling_package_sha256"),
            "memory_sha256": (payload.get("memory") or {}).get("sha256"),
            "mapped_sources": list(payload.get("mapped_sources") or []),
            "patch_status": session.get("status"),
            "active_transaction": session.get("active_transaction"),
            "transaction_count": len(session.get("transactions") or []),
            "available_scope": session.get("available"),
            "consumed_scope": session.get("consumed"),
            "active_gate": (payload.get("active_gate") or {}).get("step"),
        }
