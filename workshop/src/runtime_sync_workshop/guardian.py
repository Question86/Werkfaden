from __future__ import annotations

from .guardian_common import *
from .guardian_state import GuardianStateMixin
from .guardian_ledger import GuardianLedgerMixin
from .guardian_evidence import GuardianEvidenceMixin
from .guardian_receipts import GuardianReceiptMixin
from .guardian_patch import GuardianPatchMixin
from .guardian_transaction import GuardianTransactionMixin
from .guardian_close import GuardianCloseMixin


class InvestigationGuardian(GuardianCloseMixin, GuardianTransactionMixin, GuardianPatchMixin, GuardianEvidenceMixin, GuardianReceiptMixin, GuardianStateMixin, GuardianLedgerMixin):
    """Deterministic evidence-to-patch-session authority."""

    def advance(
        self,
        guardian_id: str,
        *,
        step: str,
        summary: str,
        state_ticket_id: str,
        evidence_refs: Iterable[str],
        package_sha256: str,
        sources: Iterable[str] = (),
        scope_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_step = str(step).strip().upper().replace("-", "_")
        if normalized_step not in PRE_PATCH_STEPS:
            raise WorkshopError("GUARDIAN_STEP_INVALID", f"guard-step only advances pre-patch states, not {normalized_step}")
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            if payload.get("closed"):
                raise WorkshopError("GUARDIAN_CLOSED", f"guardian session is closed: {guardian_id}")
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            self._assert_prepatch_package(payload, package_sha256)
            gate = self._consume_gate(
                payload,
                step=normalized_step,
                ticket_id=state_ticket_id,
                package_sha256=package_sha256,
            )
            if normalized_step != payload.get("next_step"):
                raise WorkshopError("GUARDIAN_STEP_ORDER", f"expected {payload.get('next_step')}, received {normalized_step}")
            memory_passages = list(gate.get("memory_passages") or [])
            structured = self._structured_summary(normalized_step, summary)
            evidence = self._evidence(payload, normalized_step, evidence_refs)
            if normalized_step == "MAP":
                mapped = _normalize_paths(sources, label="MAP expected source", allow_empty=False)
                payload["mapped_sources"] = mapped
            if normalized_step == "PROVE":
                exact = (payload.get("evidence") or {}).get("EXACT_SOURCE") or []
                inspected = {path for row in exact for path in row.get("paths", [])}
                matched = {match["path"] for row in exact for match in row.get("matches", [])}
                if not inspected or not matched:
                    raise WorkshopError("GUARDIAN_SOURCE_RECEIPT_REQUIRED", "PROVE requires prior positive exact-source line evidence")
                scope = _normalize_scope(scope_contract, fallback_sources=sources)
                direct = (
                    set(scope["normal"]["sources"])
                    | set(scope["normal"]["headers"])
                    | set(scope["normal"]["dependent_tests"])
                )
                if direct and not direct.issubset(matched):
                    raise WorkshopError(
                        "GUARDIAN_SCOPE_NOT_INSPECTED",
                        "PROVE direct normal scope exceeds exact-source line evidence",
                        details={"matched": sorted(matched), "proven": sorted(direct)},
                    )
                mapped = set(payload.get("mapped_sources") or [])
                structural = set(scope["auxiliary"]["documents"]) | set(scope["authority"]["add_headers"]) | set(scope["authority"]["remove_headers"])
                for operation in scope["source_set"]["operations"]:
                    structural.add(operation["path"])
                    if operation.get("to"):
                        structural.add(operation["to"])
                all_proven = direct | structural
                if all_proven and not all_proven.issubset(mapped):
                    raise WorkshopError(
                        "GUARDIAN_SCOPE_OUTSIDE_MAP",
                        "PROVE patch scope exceeds the paths named by MAP",
                        details={"mapped": sorted(mapped), "proven": sorted(all_proven)},
                    )
                windows: dict[str, list[list[int]]] = {}
                for row in exact:
                    for match in row.get("matches", []):
                        path = match["path"]
                        line = int(match["line"])
                        windows.setdefault(path, []).append([max(1, line - EDIT_CONTEXT_LINES), line + EDIT_CONTEXT_LINES])
                for path, rows in list(windows.items()):
                    merged: list[list[int]] = []
                    for start, end in sorted(rows):
                        if merged and start <= merged[-1][1] + 1:
                            merged[-1][1] = max(merged[-1][1], end)
                        else:
                            merged.append([start, end])
                    windows[path] = merged
                operational_scope = {key: json.loads(json.dumps(scope[key])) for key in ("normal", "auxiliary", "source_set", "authority")}
                payload["patch_session"] = {
                    "status": "OPEN",
                    "opened_at": utc_now(),
                    "scope": scope,
                    "inspection_windows": windows,
                    "available": operational_scope,
                    "consumed": {"normal": {"sources": [], "headers": [], "dependent_tests": []}, "auxiliary": {"documents": []}, "source_set": {"operations": []}, "authority": {"add_headers": [], "remove_headers": []}},
                    "active_transaction": None,
                    "transactions": [],
                    "final_heartbeat_id": None,
                }
                payload["rolling_package_sha256"] = package_sha256
            payload.setdefault("evidence", {})[normalized_step] = evidence
            event = self._append(payload, normalized_step, {
                "summary": structured,
                "summary_sha256": sha256_text(canonical_json(structured)),
                "state_ticket_id": state_ticket_id,
                "memory_passages": memory_passages,
                "evidence": evidence,
                "mapped_sources": payload.get("mapped_sources") if normalized_step == "MAP" else None,
                "patch_scope": payload.get("patch_session", {}).get("scope") if normalized_step == "PROVE" else None,
            })
            payload["current_step"] = normalized_step
            payload["next_step"] = PRE_PATCH_STEPS[PRE_PATCH_STEPS.index(normalized_step) + 1] if normalized_step != "PROVE" else "PATCH"
            payload["active_gate"] = None
            payload["last_step_event_hash"] = event["event_hash"]
            self._save(payload)
            return self._view(payload)

    def release_transaction(self, transaction_id: str, *, state: dict[str, Any], package_sha256: str) -> dict[str, Any]:
        result = super().release_transaction(
            transaction_id,
            state=state,
            package_sha256=package_sha256,
        )
        if str(state.get("state")) != "ABORTED_STALE_BASELINE":
            return result
        guardian_id = str(result["guardian_id"])
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            session = payload.get("patch_session") or {}
            session["status"] = "INVALIDATED_BY_DRIFT"
            session["invalidated_at"] = utc_now()
            payload["rolling_package_sha256"] = package_sha256
            payload["active_gate"] = None
            payload["current_step"] = "WORKSHOP"
            payload["next_step"] = "REFRAME_REQUIRED"
            self._append(payload, "PATCH_INVALIDATED", {
                "transaction_id": transaction_id,
                "reason": "stale baseline recovery proved that the live package changed outside the patch session",
                "current_package_sha256": package_sha256,
            })
            self._save(payload)
            return self._view(payload)
