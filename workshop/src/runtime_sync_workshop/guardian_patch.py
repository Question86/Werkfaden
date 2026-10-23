from __future__ import annotations

from .guardian_common import *


class GuardianPatchMixin:
    def reframe(self, guardian_id: str, *, target: str, reason: str, memory_selectors: Iterable[str], package_sha256: str) -> dict[str, Any]:
        target = target.strip().upper()
        if target not in {"HYPOTHESIS", "MAP"}:
            raise WorkshopError("GUARDIAN_REFRAME_TARGET", "reframe target must be HYPOTHESIS or MAP")
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            session = payload.get("patch_session")
            if session and session.get("active_transaction"):
                raise WorkshopError("GUARDIAN_TRANSACTION_ACTIVE", "cannot reframe while a Workshop transaction is active")
            self._assert_rolling_package(payload, package_sha256)
            passages, memory_context = self._memory_passages(payload, target, memory_selectors)
            if session:
                session["status"] = "SUPERSEDED"
                session["superseded_at"] = utc_now()
            payload["patch_session"] = None
            payload["active_gate"] = None
            payload["baseline_package_sha256"] = package_sha256
            payload["rolling_package_sha256"] = package_sha256
            payload["mapped_sources"] = [] if target == "HYPOTHESIS" else payload.get("mapped_sources", [])
            if target == "HYPOTHESIS":
                payload["evidence"] = {}
                payload["current_step"] = "HUMAN_PROBLEM"
                payload["next_step"] = "HYPOTHESIS"
            else:
                for key in ("MAP", "COUNTERPROBE", "EXACT_SOURCE", "PROVE"):
                    payload.get("evidence", {}).pop(key, None)
                payload["current_step"] = "FALSIFIER_2"
                payload["next_step"] = "MAP"
            self._append(payload, "REFRAME", {"target": target, "reason": reason, "memory_passages": passages})
            self._save(payload)
            result = self._view(payload)
            result["memory_context"] = memory_context
            return result


    def _requested_scope(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        if kind == "normal":
            return {
                "sources": _normalize_paths(request.get("sources") or [], label="checkout source"),
                "headers": _normalize_paths(request.get("headers") or [], label="checkout header", allow_empty=True),
                "dependent_tests": _normalize_paths(request.get("dependent_tests") or [], label="checkout dependent test", allow_empty=True),
            }
        if kind == "auxiliary":
            return {"documents": _normalize_paths(request.get("documents") or [], label="auxiliary document")}
        if kind == "source_set":
            return {"operations": list(request.get("operations") or [])}
        if kind == "authority":
            return {
                "add_headers": _normalize_paths(request.get("add_headers") or [], label="authority add header", allow_empty=True),
                "remove_headers": _normalize_paths(request.get("remove_headers") or [], label="authority remove header", allow_empty=True),
            }
        raise WorkshopError("GUARDIAN_TRANSACTION_KIND", f"unsupported guarded transaction kind: {kind}")


    def reserve_transaction(
        self,
        guardian_id: str,
        *,
        kind: str,
        request: dict[str, Any],
        state_ticket_id: str,
        package_sha256: str,
    ) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            self._assert_rolling_package(payload, package_sha256)
            gate = self._consume_gate(
                payload, step="PATCH", ticket_id=state_ticket_id, package_sha256=package_sha256
            )
            if payload.get("next_step") != "PATCH":
                raise WorkshopError("GUARDIAN_STEP_ORDER", f"expected {payload.get('next_step')}, received PATCH transaction")
            session = payload.get("patch_session")
            if not session or session.get("status") != "OPEN":
                raise WorkshopError("GUARDIAN_PATCH_NOT_AUTHORIZED", "PROVE must open a patch session before checkout")
            if session.get("active_transaction"):
                raise WorkshopError("GUARDIAN_TRANSACTION_ACTIVE", "next transaction is blocked until the active one terminalizes")
            if session.get("reservation"):
                self._append(payload, "TX_RESERVATION_SUPERSEDED", {"reservation_id": session["reservation"].get("reservation_id")})
                session["reservation"] = None
            requested = self._requested_scope(kind, request)
            available = session["available"].get(kind) if kind in session["available"] else None
            if available is None:
                raise WorkshopError("GUARDIAN_TRANSACTION_KIND", f"patch scope does not admit transaction kind {kind}")
            if kind == "normal":
                for field in ("sources", "headers", "dependent_tests"):
                    if not set(requested[field]).issubset(set(available[field])):
                        raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", f"requested {field} exceed available proven scope", details={"available": available[field], "requested": requested[field]})
            elif kind == "auxiliary":
                if not set(requested["documents"]).issubset(set(available["documents"])):
                    raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", "requested auxiliary documents exceed proven scope")
            else:
                if requested != available:
                    raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", f"{kind} transaction must exactly match the proven structural scope")
            passages = list(gate.get("memory_passages") or [])
            reservation_id = "RES_" + sha256_text(f"{guardian_id}|{uuid.uuid4().hex}|{kind}|{canonical_json(requested)}")[:24]
            session["reservation"] = {
                "reservation_id": reservation_id,
                "kind": kind,
                "requested": requested,
                "package_sha256": package_sha256,
                "memory_passages": passages,
                "state_ticket_id": state_ticket_id,
                "created_at": utc_now(),
            }
            payload["active_gate"] = None
            payload["current_step"] = "PATCH"
            payload["next_step"] = "WORKSHOP"
            self._append(payload, "TX_RESERVE", session["reservation"])
            self._save(payload)
            return {"guardian_id": guardian_id, **session["reservation"]}


    def cancel_reservation(self, guardian_id: str, *, reason: str) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            session = payload.get("patch_session") or {}
            reservation = session.get("reservation")
            if not isinstance(reservation, dict):
                raise WorkshopError("GUARDIAN_RESERVATION_MISSING", "patch session has no active reservation")
            session["reservation"] = None
            payload["current_step"] = "WORKSHOP"
            payload["next_step"] = "PATCH"
            self._append(payload, "TX_RESERVATION_CANCEL", {
                "reservation_id": reservation.get("reservation_id"),
                "reason": str(reason)[:2000],
            })
            self._save(payload)
            return self._view(payload)


    def bind_transaction(self, guardian_id: str, *, reservation_id: str, transaction_id: str) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            session = payload.get("patch_session") or {}
            reservation = session.get("reservation") or {}
            if reservation.get("reservation_id") != reservation_id:
                raise WorkshopError("GUARDIAN_RESERVATION_MISMATCH", "transaction does not match the active reservation")
            if session.get("active_transaction"):
                raise WorkshopError("GUARDIAN_TRANSACTION_ACTIVE", "patch session already has an active transaction")
            record = {
                "transaction_id": transaction_id,
                "kind": reservation["kind"],
                "requested": reservation["requested"],
                "baseline_package_sha256": reservation["package_sha256"],
                "state": "LEASED",
                "scope_validated": False,
                "created_at": utc_now(),
            }
            session["active_transaction"] = transaction_id
            session["reservation"] = None
            session.setdefault("transactions", []).append(record)
            payload["current_step"] = "WORKSHOP"
            payload["next_step"] = None
            self._append(payload, "TX_BIND", record)
            self._save(payload)
            return record


    def _find_tx(self, payload: dict[str, Any], transaction_id: str) -> dict[str, Any]:
        session = payload.get("patch_session") or {}
        for record in session.get("transactions") or []:
            if record.get("transaction_id") == transaction_id:
                return record
        raise WorkshopError("GUARDIAN_TRANSACTION_UNKNOWN", f"transaction is not owned by guardian: {transaction_id}")


    def assert_transaction_active(self, transaction_id: str, *, package_sha256: str | None = None) -> dict[str, Any]:
        matches: list[str] = []
        for path in self.directory.glob("GRD2_*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            session = payload.get("patch_session") or {}
            if any(item.get("transaction_id") == transaction_id for item in session.get("transactions") or []):
                matches.append(payload.get("guardian_id"))
        if len(matches) != 1:
            raise WorkshopError("GUARDIAN_TRANSACTION_BINDING", f"transaction must have exactly one active guardian owner: {transaction_id}")
        guardian_id = str(matches[0])
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            session = payload.get("patch_session") or {}
            if session.get("active_transaction") != transaction_id:
                raise WorkshopError("GUARDIAN_TRANSACTION_NOT_ACTIVE", f"transaction is not the active patch-session transaction: {transaction_id}")
            record = self._find_tx(payload, transaction_id)
            if package_sha256 and record.get("baseline_package_sha256") != package_sha256:
                raise WorkshopError("GUARDIAN_TRANSACTION_BASELINE", "transaction baseline differs from current governed package")
            return {"guardian_id": guardian_id, "transaction": record, "session": session}
