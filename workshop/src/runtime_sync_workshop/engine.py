from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

from ._engine_core_v1 import load_engine_core

_CoreWorkshopEngine, PRE_APPLY_STATES, TERMINAL_STATES = load_engine_core()
from .corpus import build_corpus_manifest
from .guardian import InvestigationGuardian, guardian_required
from .util import WorkshopError, read_json


class WorkshopEngine(_CoreWorkshopEngine):
    """Guarded Workshop authority surface.

    The quarantined v1 engine remains the byte-for-byte synchronization core. This wrapper
    moves Guardian enforcement below the CLI so direct use of the public WorkshopEngine API
    cannot bypass the patch-session contract. Every transaction type that uses this engine's
    lease/state primitives inherits the same binding and lifecycle checks.
    """

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self._guardian_context: dict[str, Any] | None = None
        self._assert_canonical_control_plane()

    def _assert_canonical_control_plane(self) -> None:
        canonical = (self.config.kairos_workspace / ".kairos" / "workshop.config.json").resolve()
        if canonical.is_file() and self.config.config_path.resolve() != canonical:
            raise WorkshopError(
                "CONFIG_CONTROL_PLANE_MISMATCH",
                "a bound KAIROS workspace may be mutated only through its canonical .kairos/workshop.config.json",
                details={"canonical": str(canonical), "requested": str(self.config.config_path.resolve())},
            )

    def _guardian_enabled(self) -> bool:
        return guardian_required(self.config.raw)

    def guardian(self) -> InvestigationGuardian:
        raw_memory = self.config.raw.get("guardian_memory_file")
        if not isinstance(raw_memory, str) or not raw_memory.strip():
            raw_memory = os.environ.get("WERKFADEN_GUARDIAN_MEMORY_FILE", "")
        memory_file = None
        if isinstance(raw_memory, str) and raw_memory.strip():
            candidate = Path(raw_memory)
            memory_file = candidate if candidate.is_absolute() else self.config.machine_root / candidate
        elif self._guardian_enabled():
            raise WorkshopError(
                "GUARDIAN_MEMORY_AUTHORITY_MISSING",
                "Guardian enforcement requires guardian_memory_file in the canonical config or WERKFADEN_GUARDIAN_MEMORY_FILE",
            )
        requirements = self.config.raw.get("guardian_memory_requirements", {})
        requirements_file = os.environ.get("WERKFADEN_GUARDIAN_MEMORY_REQUIREMENTS_FILE", "").strip()
        if requirements_file:
            loaded = json.loads(Path(requirements_file).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise WorkshopError("GUARDIAN_MEMORY_CONTRACT_INVALID", "Memory requirements file must contain a JSON object")
            requirements = loaded
        if self._guardian_enabled():
            required_steps = {
                "HUMAN_PROBLEM", "HYPOTHESIS", "FALSIFIER_1", "FALSIFIER_2",
                "MAP", "COUNTERPROBE", "EXACT_SOURCE", "PROVE", "PATCH", "WORKSHOP",
                "HEARTBEAT", "FRESH_RUN",
            }
            missing = sorted(step for step in required_steps if not isinstance(requirements, dict) or not requirements.get(step))
            if missing:
                raise WorkshopError(
                    "GUARDIAN_MEMORY_CONTRACT_INCOMPLETE",
                    "Guardian enforcement requires state-specific canonical Memory selectors",
                    details={"missing_states": missing},
                )
        return InvestigationGuardian(
            state_directory=self.config.state_directory,
            kairos_database=self.config.kairos_database,
            kairos_workspace=self.config.kairos_workspace,
            codebase_root=self.config.codebase_root,
            transaction_directory=self.config.transaction_directory,
            canonical_memory_file=memory_file,
            memory_requirements=requirements if isinstance(requirements, dict) else {},
            required_run_validations=(self.config.raw.get("guardian_fresh_run_required_validations") or []),
        )

    def _verified_package(self, *, require_seal: bool = True) -> str:
        manifest = build_corpus_manifest(self.config)
        if not manifest.get("verified"):
            raise WorkshopError("GUARDIAN_BASELINE_UNVERIFIED", "Guardian requires a verified Workshop corpus", details=manifest.get("issues"))
        if require_seal:
            seal = read_json(self.seal_path) if self.seal_path.is_file() else None
            if not isinstance(seal, dict) or seal.get("package_sha256") != manifest.get("package_sha256"):
                raise WorkshopError("GUARDIAN_BASELINE_UNSEALED", "Guardian requires the current corpus to equal the trusted Workshop seal")
        return str(manifest["package_sha256"])

    @contextmanager
    def guardian_transaction(self, *, guardian_id: str, kind: str, request: dict[str, Any], state_ticket_id: str) -> Iterator[dict[str, Any]]:
        if self._guardian_context is not None:
            raise WorkshopError("GUARDIAN_CONTEXT_NESTED", "nested Guardian transaction authorization is not allowed")
        package = self._verified_package(require_seal=True)
        reservation = self.guardian().reserve_transaction(guardian_id, kind=kind, request=request, state_ticket_id=state_ticket_id, package_sha256=package)
        self._guardian_context = {"guardian_id": guardian_id, "reservation_id": reservation["reservation_id"], "kind": kind, "package_sha256": package}
        try:
            yield reservation
        finally:
            self._guardian_context = None

    def _acquire_lease(self, transaction_id: str) -> dict[str, Any]:
        required = self._guardian_enabled()
        context = self._guardian_context
        if required and context is None:
            raise WorkshopError("GUARDIAN_REQUIRED", "transaction checkout is blocked until an active Guardian patch session authorizes this mutation")
        lease = super()._acquire_lease(transaction_id)
        if context is not None:
            try:
                self.guardian().bind_transaction(str(context["guardian_id"]), reservation_id=str(context["reservation_id"]), transaction_id=transaction_id)
            except Exception:
                try:
                    super()._release_lease(transaction_id)
                finally:
                    raise
        return lease

    def _require_lease(self, transaction_id: str) -> None:
        super()._require_lease(transaction_id)
        if self._guardian_enabled():
            self.guardian().assert_transaction_active(transaction_id)

    def _save_transaction(self, root: Path, state: dict[str, Any], *, event: str, details: Any = None) -> None:
        super()._save_transaction(root, state, event=event, details=details)
        if self._guardian_enabled():
            transaction_id = str(state.get("transaction_id", ""))
            if transaction_id:
                self.guardian().observe_transaction_state(transaction_id, event=event, state=state)

    def _release_lease(self, transaction_id: str) -> None:
        if not self._guardian_enabled():
            return super()._release_lease(transaction_id)
        guard = self.guardian()
        root = self._transaction_path(transaction_id)
        state_path = root / "state.json"
        if not state_path.is_file():
            super()._release_lease(transaction_id)
            guard.cancel_transaction(transaction_id, reason="checkout failed before transaction state materialized")
            return
        state = read_json(state_path)
        terminal = str(state.get("state", ""))
        if terminal in {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK", "ABORTED_ORPHANED_CHECKOUT"}:
            package = self._verified_package(require_seal=terminal == "POSTCHECK_VERIFIED")
            guard.assert_release_transaction(transaction_id, state=state, package_sha256=package)
            super()._release_lease(transaction_id)
            guard.release_transaction(transaction_id, state=state, package_sha256=package)
            return
        super()._release_lease(transaction_id)
        guard.cancel_transaction(transaction_id, reason=f"lease released from non-terminal checkout failure state {terminal or '<missing>'}")

    def checkout(self, sources: Iterable[str], *, purpose: str, tests: Iterable[str] = (), guardian_id: str | None = None, guardian_state_ticket: str | None = None) -> dict[str, Any]:
        source_list = sorted({str(value).replace("\\", "/") for value in sources})
        test_list = sorted({str(value).replace("\\", "/") for value in tests})
        if not self._guardian_enabled() and not guardian_id:
            return super().checkout(source_list, purpose=purpose, tests=test_list)
        guardian_id = str(guardian_id or "").strip()
        if not guardian_id:
            raise WorkshopError("GUARDIAN_REQUIRED", "normal checkout requires --guardian while guardian_required=true")
        status = self.guardian().status(guardian_id, full=True)
        available = ((status.get("patch_session") or {}).get("available") or {}).get("normal") or {}
        request = {"sources": source_list, "headers": list(available.get("headers") or []), "dependent_tests": test_list}
        ticket = str(guardian_state_ticket or "").strip()
        if not ticket:
            raise WorkshopError("GUARDIAN_MEMORY_GATE_MISSING", "normal checkout requires a PATCH state ticket from guard-enter")
        with self.guardian_transaction(guardian_id=guardian_id, kind="normal", request=request, state_ticket_id=ticket):
            result = super().checkout(source_list, purpose=purpose, tests=test_list)
        result["guardian"] = self.guardian().status(guardian_id)
        return result

    def authority_recover(self, transaction_id: str, **kwargs: Any) -> dict[str, Any]:
        result = super().authority_recover(transaction_id, **kwargs)
        if self._guardian_enabled():
            root = self._transaction_path(transaction_id)
            state = read_json(root / "state.json")
            current = self._verified_package(require_seal=False)
            self.guardian().release_transaction(transaction_id, state=state, package_sha256=current)
        return result

    def guardian_reconcile(self, guardian_id: str) -> dict[str, Any]:
        if not self._guardian_enabled():
            raise WorkshopError("GUARDIAN_NOT_REQUIRED", "guardian reconcile is available only while Guardian enforcement is active")
        guard = self.guardian()
        status = guard.status(guardian_id, full=True)
        session = status.get("patch_session") or {}
        active = session.get("active_transaction")
        lease = read_json(self.lease_path) if self.lease_path.is_file() else None
        current = self._verified_package(require_seal=True)
        if active:
            root = self._transaction_path(str(active))
            state_path = root / "state.json"
            if not state_path.is_file():
                if current != status.get("rolling_package_sha256"):
                    raise WorkshopError("GUARDIAN_RECONCILE_DRIFT", "live package changed during missing-state checkout")
                rollback = root / "rollback" / "targets.json"
                if rollback.exists():
                    raise WorkshopError("GUARDIAN_RECONCILE_UNPROVEN", "rollback/apply evidence exists without transaction state; manual recovery authority is required")
                if isinstance(lease, dict):
                    if lease.get("transaction_id") != active:
                        raise WorkshopError("GUARDIAN_RECONCILE_UNPROVEN", "another transaction owns the Workshop lease")
                    super()._release_lease(str(active))
                return guard.cancel_transaction(str(active), reason="reconciled crash around lease/bind before transaction state materialized")
            state = read_json(state_path)
            terminal = str(state.get("state", ""))
            if terminal not in {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK", "ABORTED_ORPHANED_CHECKOUT"}:
                raise WorkshopError("GUARDIAN_RECONCILE_NOT_TERMINAL", f"transaction is not terminal: {terminal}")
            if isinstance(lease, dict) and lease.get("transaction_id") == active:
                super()._release_lease(str(active))
            return guard.release_transaction(str(active), state=state, package_sha256=current)
        reservation = session.get("reservation")
        if reservation and isinstance(lease, dict):
            txid = str(lease.get("transaction_id", ""))
            if txid and not self._transaction_path(txid).joinpath("state.json").is_file():
                if current != status.get("rolling_package_sha256"):
                    raise WorkshopError("GUARDIAN_RECONCILE_DRIFT", "live package changed during unbound checkout crash")
                super()._release_lease(txid)
                return guard.cancel_reservation(guardian_id, reason=f"reconciled unbound lease {txid} before Guardian transaction binding")
        raise WorkshopError("GUARDIAN_RECONCILE_NOT_REQUIRED", "no reconcilable Guardian/Workshop crash window is present")

    def seal(self) -> dict[str, Any]:
        if self._guardian_enabled():
            active = []
            directory = self.config.state_directory / "guardian-v2"
            if directory.is_dir():
                for path in directory.glob("GRD2_*.json"):
                    try:
                        payload = read_json(path)
                    except Exception:
                        continue
                    if not payload.get("closed") and (payload.get("patch_session") or {}).get("status") in {"OPEN", "WORKSHOP_COMPLETE"}:
                        active.append(payload.get("guardian_id"))
            if active:
                raise WorkshopError("GUARDIAN_SESSION_ACTIVE", "manual reseal is blocked while a Guardian patch session is open", details=sorted(active))
        return super().seal()
