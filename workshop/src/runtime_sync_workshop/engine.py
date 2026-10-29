from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

from ._engine_implementation import (
    WorkshopEngine as _CoreWorkshopEngine,
    PRE_APPLY_STATES as PRE_APPLY_STATES,
    TERMINAL_STATES as TERMINAL_STATES,
)
from .corpus import build_corpus_manifest
from .guardian import InvestigationGuardian, guardian_required
from .guardian_common import SCHEMA as GUARDIAN_SCHEMA
from .util import WorkshopError, read_json, atomic_write_json, canonical_json, sha256_bytes, reject_link_components, utc_now
from .jev_admission import require_candidate_jev_clearance
from .tx_documents import update_document, assert_projected, assert_no_pending
from .tx_retention import enforce_pool_limit, snapshot, assert_verified_snapshot, compact, rehydrate, storage_inventory
from .tx_lifecycle import stage_lifecycle_event, requires_projection


class WorkshopEngine(_CoreWorkshopEngine):
    """Guarded Workshop authority surface.

    The regular responsibility modules supply the synchronization core. This wrapper
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

    def policy_activation_plan(self, profile: str) -> dict[str, Any]:
        """Read-only stage1 proof; no lease, Apply clearance or config mutation."""
        from .corpus import runtime_policy_candidate, verify_runtime_policy_candidate
        self._assert_canonical_control_plane()
        reject_link_components(self.seal_path, label="runtime-policy baseline seal")
        if not self.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "runtime-policy planning requires a verified sealed baseline")
        seal_bytes = self.seal_path.read_bytes()
        baseline = self._verified_package(require_seal=True)
        candidate = runtime_policy_candidate(self.config, profile)
        hashes = {name: sha256_bytes(data) for name, data in candidate["source_files"].items()}
        result = verify_runtime_policy_candidate(self.config, profile, candidate["candidate_files"],
                                                 expected_source_hashes=hashes)
        if self.seal_path.read_bytes() != seal_bytes or self._verified_package(require_seal=True) != baseline:
            raise WorkshopError("POLICY_BASELINE_CHANGED", "sealed corpus changed during policy planning")
        result.update(schema="werkfaden-runtime-policy-plan/v1", baseline_package_sha256=baseline,
                      baseline_seal_sha256=sha256_bytes(seal_bytes), transaction_required=True,
                      activation_supported=True,
                      next_step="Review this exact plan, then policy-checkout -> review -> policy-prepare -> policy-verify -> policy-apply; use policy-recover after interruption.")
        result["plan_sha256"] = sha256_bytes(canonical_json(result).encode("utf-8"))
        return result

    def policy_checkout(self, profile: str, *, expected_plan_sha256: str, purpose: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).checkout(profile, expected_plan_sha256=expected_plan_sha256, purpose=purpose)

    def policy_prepare(self, transaction_id: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).prepare(transaction_id)

    def policy_verify(self, transaction_id: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).verify(transaction_id)

    def policy_apply(self, transaction_id: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).apply(transaction_id)

    def policy_recover(self, transaction_id: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).recover(transaction_id)

    def policy_abort(self, transaction_id: str) -> dict[str, Any]:
        return RuntimePolicyTransaction(self).abort(transaction_id)

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        if self._load_transaction(transaction_id)[1].get("schema") == RuntimePolicyTransaction.SCHEMA:
            return self.policy_prepare(transaction_id)
        return super().prepare(transaction_id)

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
        # Serialize pending checks and lease creation with review/compaction.
        from .tx_retention import _retention_lock
        from .tx_recycling import assert_no_review_intent
        with _retention_lock(self, allow_reentry=False):
            assert_no_pending(self)
            assert_no_review_intent(self)
            from .tx_lifecycle import assert_no_event_intents
            assert_no_event_intents(self)
            if self.config.raw.get("tx_retention_required", False) is True:
                enforce_pool_limit(self)
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
            guardian = self.guardian()
            guardian.assert_transaction_active(transaction_id)
            state_path = self._transaction_path(transaction_id) / "state.json"
            if state_path.is_file():
                state = read_json(state_path)
                if any(state.get(key) is not None for key in ("tx_event", "tx_event_id", "tx_event_sequence")):
                    tx_event = state.get("tx_event")
                    if not isinstance(tx_event, dict):
                        raise WorkshopError("GUARDIAN_TX_EVENT_INVALID", "transaction event identity is incomplete")
                    guardian.observe_transaction_state(transaction_id, event=str(tx_event.get("event") or ""),
                                                       state=state, only_if_missing=True)

    def _save_transaction(self, root: Path, state: dict[str, Any], *, event: str, details: Any = None) -> None:
        from .tx_retention import _retention_lock, _checked_transaction_path
        from .tx_lifecycle import event_intents, endpoint, recover_event_intents, intent_error
        with _retention_lock(self):
            txid = str(state.get("transaction_id") or "")
            _checked_transaction_path(self, txid)
            if root.resolve() != self._transaction_path(txid).resolve():
                raise WorkshopError("TX_EVENT_ROOT_MISMATCH", "state writer root differs from its native transaction")
            if self._guardian_enabled():
                try:
                    prior = read_json(root / "state.json") if (root / "state.json").is_file() else {}
                    if not isinstance(prior, dict):
                        raise ValueError("saved state is not an object")
                except (OSError, ValueError) as exc:
                    raise intent_error(txid, "Guardian preflight cannot verify the saved native state") from exc
                if isinstance(prior.get("tx_event"), dict):
                    self.guardian().observe_transaction_state(txid, event=str(prior["tx_event"].get("event") or ""),
                                                               state=prior, only_if_missing=True)
                unfinished = any(endpoint(prior) == endpoint(item[1]["prior_state"])
                                 for item in event_intents(self, txid))
                if unfinished:
                    # The caller computed its operation from the old state. Do
                    # not skip Guardian's next event or merge stale material
                    # data. Complete that exact prior commit, then require an
                    # explicit fresh operation against the recovered endpoint.
                    recover_event_intents(self, txid)
                    _, recovered = self._load_transaction(txid)
                    update_document(self, recovered, event=str(recovered["tx_event"]["event"]))
                    assert_projected(self, txid)
                    raise WorkshopError("TX_OPERATION_RETRY_REQUIRED",
                        "the preceding native event was recovered; inspect its current state before retrying an operation",
                        details={"transaction_id": txid, "requested_event": event,
                                 "recovered_event_id": recovered["tx_event_id"],
                                 "recovered_event_sequence": recovered["tx_event_sequence"],
                                 "state": recovered["state"], "retry_required": True})
                # Validate the proposed scope without recording a Guardian event.
                # A refused candidate must leave the prior admitted endpoint abortable.
                self.guardian().validate_transaction_scope(txid, event=event, state=state)
            state["updated_at"] = utc_now()
            state.setdefault("journal", []).append({"event": event, "at": state["updated_at"], "details": details})
            authoritative_event = stage_lifecycle_event(self, root, state, event=event)
            super()._save_transaction(root, state, event=event, details=details, diagnostics_prepared=True)
            if self._guardian_enabled():
                transaction_id = str(state.get("transaction_id", ""))
                if transaction_id:
                    self.guardian().observe_transaction_state(transaction_id, event=event, state=state)
            if authoritative_event is not None and authoritative_event["required_projection"]:
                # Project only meaningful transitions and explicit knowledge changes.
                # The pending outbox marker predates the state save, so a crash does
                # not silently erase the obligation to project the persisted event.
                update_document(self, state, event=event)

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
        if terminal in {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"} and isinstance(state.get("tx_event"), dict):
            raise WorkshopError("GUARDIAN_NONTERMINAL_RECOVERY_REQUIRED",
                                "persisted transaction requires Guardian reconciliation; lease retained",
                                details={"transaction_id": transaction_id, "state": terminal})
        super()._release_lease(transaction_id)
        guard.cancel_transaction(transaction_id, reason=f"lease released from non-terminal checkout failure state {terminal or '<missing>'}")

    def _announce_checkout_recycling(self, result: dict[str, Any]) -> dict[str, Any]:
        # Admission checked before creation; re-evaluate AFTER creation so the
        # ninth successful checkout immediately becomes the KAIROS next action.
        if self.config.raw.get("tx_retention_required", False) is True:
            from .tx_recycling import pool_status
            status = pool_status(self, publish=True)
            if status["required"]:
                updated = self.transaction_pool_status(refresh_loop=True)
                result["tx_recycling"] = {
                    "required": True,
                    "materialized": updated["materialized_count"],
                    "target_max": updated["target_max"],
                    "next_required_read": "_LOOP_GATE.md#s-tx-recycling",
                    "loop_heartbeat": updated.get("loop_heartbeat"),
                }
            else:
                result["tx_recycling"] = {"required": False,
                                          "materialized": status["materialized_count"]}
        return result

    def checkout(self, sources: Iterable[str], *, purpose: str, tests: Iterable[str] = (), documents: Iterable[str] = (), guardian_id: str | None = None, guardian_state_ticket: str | None = None) -> dict[str, Any]:
        document_list = list(documents)
        if document_list and (self._guardian_enabled() or guardian_id):
            raise WorkshopError("GUARDIAN_CONTEXT_DOCUMENTS_UNSUPPORTED", "Guardian v2 scope does not authorize normal-transaction context documents; use a separately proven auxiliary transaction")
        source_list = sorted({str(value).replace("\\", "/") for value in sources})
        test_list = sorted({str(value).replace("\\", "/") for value in tests})
        if not self._guardian_enabled() and not guardian_id:
            result = super().checkout(source_list, purpose=purpose, tests=test_list, documents=document_list)
            if self.config.raw.get("transaction_documents_required", False) is True:
                try:
                    assert_projected(self, result["transaction_id"])
                except Exception:
                    super().abort(result["transaction_id"])
                    raise
            return self._announce_checkout_recycling(result)
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
            result = super().checkout(source_list, purpose=purpose, tests=test_list, documents=document_list)
        result["guardian"] = self.guardian().status(guardian_id)
        if self.config.raw.get("transaction_documents_required", False) is True:
            try:
                assert_projected(self, result["transaction_id"])
            except Exception:
                super().abort(result["transaction_id"])
                raise
        return self._announce_checkout_recycling(result)

    def include_documents(self, transaction_id: str, documents: Iterable[str], *, reason: str) -> dict[str, Any]:
        if self._guardian_enabled():
            raise WorkshopError("GUARDIAN_CONTEXT_DOCUMENTS_UNSUPPORTED", "Guardian v2 requires separately proven auxiliary document scope")
        return super().include_documents(transaction_id, documents, reason=reason)

    def verify(self, transaction_id: str) -> dict[str, Any]:
        if self._load_transaction(transaction_id)[1].get("schema") == RuntimePolicyTransaction.SCHEMA:
            return self.policy_verify(transaction_id)
        result = super().verify(transaction_id)
        if self.config.raw.get("tx_retention_required", False) is True:
            snapshot(self, transaction_id)
        return result

    def apply(self, transaction_id: str) -> dict[str, Any]:
        if self._load_transaction(transaction_id)[1].get("schema") == RuntimePolicyTransaction.SCHEMA:
            return self.policy_apply(transaction_id)
        # Check at the authoritative public Engine boundary (not merely CLI).
        # Never treat a standalone Jev workflow result as an implicit approval.
        root, state = self._load_transaction(transaction_id)
        if self.config.raw.get("tx_retention_required", False) is True:
            assert_verified_snapshot(self, transaction_id)
        if self.config.raw.get("jev_apply_required", False) is True:
            require_candidate_jev_clearance(self.config, root, state)
        result = super().apply(transaction_id)
        root, state = self._load_transaction(transaction_id)
        # Receipt I/O is outside the core mutation/rollback exception boundary.
        result = self._finish_transaction_receipt(root, state, kind="postcheck", payload=result)
        if requires_projection(self, self._load_transaction(transaction_id)[1]):
            assert_projected(self, transaction_id)
        return result

    def abort(self, transaction_id: str) -> dict[str, Any]:
        if self._load_transaction(transaction_id)[1].get("schema") == RuntimePolicyTransaction.SCHEMA:
            return self.policy_abort(transaction_id)
        result = super().abort(transaction_id)
        if requires_projection(self, self._load_transaction(transaction_id)[1]):
            assert_projected(self, transaction_id)
        return result

    def recover(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        schema = state.get("schema")
        if schema == RuntimePolicyTransaction.SCHEMA:
            result = self.policy_recover(transaction_id)
        elif schema == "runtime-sync-auxiliary-transaction/v1":
            from .auxiliary import AuxiliaryDocumentTransaction
            result = AuxiliaryDocumentTransaction(self).recover(transaction_id)
        elif state.get("transaction_kind") == "authority_migration":
            from .migration import AuthorityMigration
            result = AuthorityMigration(self).recover(transaction_id)
        elif schema == "runtime-sync-universal-source-set/v1":
            from .universal_migration import UniversalSourceSetMigration
            result = UniversalSourceSetMigration(self).recover(transaction_id)
        elif schema == "runtime-sync-bootstrap-repair/v1":
            from .repair import BootstrapRepair
            result = BootstrapRepair(self).recover(transaction_id)
        elif schema != "runtime-sync-transaction/v1":
            raise WorkshopError("TX_RECOVERY_ROUTE_UNSUPPORTED", "no proven recovery route exists for this transaction kind")
        else:
            if state.get("state") == "POSTCHECK_VERIFIED":
                entry = state.get("terminal_receipt")
                payload = entry.get("payload") if isinstance(entry, dict) else state.get("postcheck_receipt_payload")
                if not isinstance(payload, dict):
                    raise WorkshopError("TX_TERMINAL_SEED_MISSING", "normal postcheck has no exact preserved terminal payload")
                result = self._finish_transaction_receipt(root, state, kind="postcheck", payload=payload)
            else:
                result = super().recover(transaction_id)
                root, state = self._load_transaction(transaction_id)
                if state.get("state") == "POSTCHECK_VERIFIED":
                    result = self._finish_transaction_receipt(root, state, kind="postcheck", payload=result)
        if requires_projection(self, self._load_transaction(transaction_id)[1]):
            assert_projected(self, transaction_id)
        return result

    def annotate_transaction(self, transaction_id: str, *, kind: str, summary: str,
                             evidence: str, atomic_id: str = "", source_tx: str = "") -> dict[str, Any]:
        from .tx_retention import _retention_lock
        with _retention_lock(self, allow_reentry=False):
            # These are declared observations, NOT verified code or release receipts.
            allowed = {"observation", "hypothesis", "failure", "reused_candidate",
                       "discarded_candidate", "next_action", "verification"}
            if kind not in allowed:
                raise WorkshopError("TX_NOTE_KIND_INVALID", "unsupported transaction annotation type")
            if len(summary.strip()) < 12 or len(summary) > 2000:
                raise WorkshopError("TX_NOTE_INVALID", "TX annotation must be 12-2000 characters")
            if not evidence.strip() or len(evidence) > 2000:
                raise WorkshopError("TX_NOTE_EVIDENCE_MISSING", "every annotation requires an explicit evidence locator")
            root, state = self._load_transaction(transaction_id)
            terminal = state.get("state") in {"POSTCHECK_VERIFIED", "ABORTED", "ROLLED_BACK",
                                              "ABORTED_STALE_BASELINE", "ABORTED_ORPHANED_CHECKOUT"}
            if terminal:
                if self.lease_path.is_file():
                    raise WorkshopError("TX_NOTE_LEASE_ACTIVE", "terminal TX annotation cannot be written while a Workshop lease is held")
            else:
                self._require_lease(transaction_id)
            entries = state.setdefault("transaction_notes", [])
            if len(entries) >= 128:
                raise WorkshopError("TX_NOTE_LIMIT", "transaction has reached the bounded note limit")
            entries.append({"kind": kind, "summary": summary.strip(), "evidence_locator": evidence.strip(),
                            "atomic_id": atomic_id.strip(), "source_transaction": source_tx.strip(),
                            "verification": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"})
            if terminal:
                # Guardian has already released this transaction. Update the retained
                # history, not the former active Guardian patch-session authority.
                self._save_terminal_history(root, state, event="TX_NOTE",
                                            details={"kind": kind, "evidence": evidence.strip()})
            else:
                self._save_transaction(root, state, event="TX_NOTE",
                                       details={"kind": kind, "evidence": evidence.strip()})
            if requires_projection(self, state):
                assert_projected(self, transaction_id)
            return {"transaction_id": transaction_id, "kind": kind,
                    "notes": len(entries), "documented": requires_projection(self, state)}

    def transaction_storage_audit(self) -> dict[str, Any]:
        return storage_inventory(self)

    def transaction_pool_status(self, *, refresh_loop: bool = False) -> dict[str, Any]:
        from .tx_recycling import pool_status
        result = pool_status(self, publish=True)
        if refresh_loop:
            try:
                _, heartbeat = self._import_kairos()
                receipt = heartbeat.run_heartbeat(
                    self.config.kairos_workspace, requested_mode="verify",
                    changed_paths=[], use_reconciliation=True,
                    trigger="workshop-tx-recycling-frontier",
                )
                result["loop_heartbeat"] = {
                    "verified": bool(receipt.get("verified")),
                    "heartbeat_id": receipt.get("heartbeat_id"),
                }
            except Exception as exc:
                result["loop_heartbeat"] = {
                    "verified": False,
                    "error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
                }
        return result

    def inspect_recycling(self, transaction_id: str, *, chunk_index: int | None = None, chunk_size: int = 512) -> dict[str, Any]:
        from .tx_recycling import inspect
        return inspect(self, transaction_id, chunk_index=chunk_index, chunk_size=chunk_size)

    def submit_recycling(self, transaction_id: str, analysis_path: Path) -> dict[str, Any]:
        from .tx_recycling import submit
        return submit(self, transaction_id, analysis_path)

    def drain_recycling(self, *, max_items: int = 100) -> dict[str, Any]:
        from .tx_recycling import drain
        result = drain(self, max_items=max_items)
        status = self.transaction_pool_status(refresh_loop=True)
        result["loop_heartbeat"] = status.get("loop_heartbeat")
        return result

    def retire_orphan_transaction(self, transaction_id: str, *, reason: str) -> dict[str, Any]:
        from .tx_recycling import retire_orphan
        return retire_orphan(self, transaction_id, reason=reason)

    def compact_transaction(self, transaction_id: str) -> dict[str, Any]:
        return compact(self, transaction_id)

    def rehydrate_transaction(self, transaction_id: str, manifest_path: Path, destination: Path) -> dict[str, Any]:
        return rehydrate(self, transaction_id, manifest_path, destination)

    def _finish_transaction_receipt(self, root: Path, state: dict[str, Any], *,
                                    kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Acknowledge an already postchecked TX; never execute Apply or rollback.

        The complete payload is staged in source state before immutable receipt
        I/O. Its identity survives a crash after write and before acknowledgement.
        """
        from .tx_retention import _retention_lock
        from .tx_documents import capture_projection_source
        with _retention_lock(self):
            tx = str(state.get("transaction_id", ""))
            actual_root, actual = self._load_transaction(tx)
            if root.resolve() != actual_root.resolve() or canonical_json(actual) != canonical_json(state):
                raise WorkshopError("TX_TERMINAL_SOURCE_MISMATCH", "terminal receipt requires the exact persisted transaction")
            capture_projection_source(self, actual)
            if state.get("state") != "POSTCHECK_VERIFIED" or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", kind):
                raise WorkshopError("TX_TERMINAL_RECEIPT_INVALID", "terminal receipt requires a postchecked transaction and safe receipt kind")
            payload = json.loads(canonical_json(payload))
            if (payload.get("transaction_id") != tx or payload.get("state") != "POSTCHECK_VERIFIED"
                    or payload.get("postcheck_package_sha256") != state.get("postcheck_package_sha256")):
                raise WorkshopError("TX_TERMINAL_PAYLOAD_MISMATCH", "receipt does not bind the recorded postcheck")
            lease = read_json(self.lease_path) if self.lease_path.exists() else None
            owned = isinstance(lease, dict) and lease.get("transaction_id") == tx
            entry = state.get("terminal_receipt")
            replayed = entry is not None
            if entry is None:
                self._require_lease(tx)
                package = self._verified_package(require_seal=True)
                seal = read_json(self.seal_path)
                if package != state.get("postcheck_package_sha256") or seal.get("transaction_id") != tx:
                    raise WorkshopError("TX_TERMINAL_POSTCHECK_DRIFT", "current seal does not prove this transaction postcheck")
                digest = sha256_bytes(canonical_json(payload).encode("utf-8"))
                technical_event = str(state.get("tx_event_id") or "")
                identity = {"kind": kind, "transaction_id": tx, "postcheck_event_id": technical_event,
                            "payload_sha256": digest}
                receipt_id = kind.upper() + "_" + sha256_bytes(canonical_json(identity).encode("utf-8"))[:32]
                entry = {"schema": "werkfaden-tx-terminal-receipt/v1", "status": "PENDING", **identity,
                         "receipt_id": receipt_id, "path": "receipts/" + receipt_id + ".json", "payload": payload}
                seed_key = "postcheck_receipt_payload" if state.get("schema") == "runtime-sync-transaction/v1" else "terminal_receipt_payload"
                seed = state.get(seed_key)
                if seed is not None:
                    allowed_seeds = {
                        ("runtime-sync-transaction/v1", "postcheck"),
                        ("runtime-sync-universal-source-set/v1", "universal-source-set-postcheck"),
                        ("runtime-sync-bootstrap-repair/v1", "bootstrap-postcheck"),
                    }
                    if ((state.get("schema"), kind) not in allowed_seeds
                            or canonical_json(seed) != canonical_json(payload)):
                        raise WorkshopError("TX_TERMINAL_PAYLOAD_MISMATCH", "preserved technical result differs from receipt payload")
                    del state[seed_key]
                state["terminal_receipt"] = entry
                self._save_transaction(root, state, event="TX_TERMINAL_RECEIPT_PENDING", details={"receipt_id": receipt_id})
            if (not isinstance(entry, dict) or entry.get("schema") != "werkfaden-tx-terminal-receipt/v1"
                    or entry.get("kind") != kind or entry.get("transaction_id") != tx
                    or entry.get("status") not in {"PENDING", "COMMITTED"}
                    or canonical_json(entry.get("payload")) != canonical_json(payload)
                    or entry.get("payload_sha256") != sha256_bytes(canonical_json(payload).encode("utf-8"))):
                raise WorkshopError("TX_TERMINAL_RECEIPT_CONFLICT", "persisted terminal obligation differs from the exact payload")
            identity = {key: entry[key] for key in ("kind", "transaction_id", "postcheck_event_id", "payload_sha256")}
            receipt_id = kind.upper() + "_" + sha256_bytes(canonical_json(identity).encode("utf-8"))[:32]
            if not identity["postcheck_event_id"] or entry.get("receipt_id") != receipt_id or entry.get("path") != "receipts/" + receipt_id + ".json":
                raise WorkshopError("TX_TERMINAL_RECEIPT_CONFLICT", "terminal receipt identity/path is invalid")
            if entry["status"] != "COMMITTED":
                self._require_lease(tx)
            if replayed and owned:
                # A pending live transaction must still own its exact sealed
                # result. Unleased completed replay is historical evidence only.
                package = self._verified_package(require_seal=True)
                if (package != state.get("postcheck_package_sha256")
                        or read_json(self.seal_path).get("transaction_id") != tx):
                    raise WorkshopError("TX_TERMINAL_POSTCHECK_DRIFT", "the pending transaction no longer owns its exact sealed result")
            destination = self.config.state_directory / entry["path"]
            reject_link_components(destination, label="terminal receipt")
            record = {**payload, "receipt_id": receipt_id,
                      "terminal_receipt_schema": entry["schema"],
                      "terminal_event_id": entry["postcheck_event_id"],
                      "terminal_payload_sha256": entry["payload_sha256"]}
            expected = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if destination.exists():
                if not destination.is_file() or destination.stat().st_nlink != 1 or destination.read_bytes() != expected:
                    raise WorkshopError("TX_TERMINAL_RECEIPT_CONFLICT", "immutable terminal receipt bytes differ; no overwrite is allowed")
            else:
                if lease is not None and not owned:
                    raise WorkshopError("TX_TERMINAL_RECEIPT_LEASE_ACTIVE", "another transaction owns receipt recovery authority")
                try:
                    atomic_write_json(destination, record)
                except OSError as exc:
                    raise WorkshopError("TX_TERMINAL_RECEIPT_PENDING", "postcheck succeeded; terminal receipt storage is pending, do not repeat Apply",
                                        details={"transaction_id": tx, "event_id": state.get("tx_event_id"), "receipt_id": receipt_id}) from exc
                if destination.read_bytes() != expected:
                    raise WorkshopError("TX_TERMINAL_RECEIPT_CONFLICT", "terminal receipt did not persist its exact expected bytes")
            if entry["status"] != "COMMITTED":
                entry["status"] = "COMMITTED"
                self._save_transaction(root, state, event="TX_TERMINAL_RECEIPT_COMMITTED", details={"receipt_id": receipt_id})
            if requires_projection(self, state):
                # A crash after source acknowledgement may leave projection
                # pending. Resume that event, never an Apply operation.
                try:
                    assert_projected(self, tx)
                except WorkshopError:
                    if lease is not None and not owned:
                        raise
                    self._reconcile_transaction_document_locked(tx)
                    assert_projected(self, tx)
            if owned:
                self._release_lease(tx)
            return {**payload, "receipt": str(destination), "replayed": replayed,
                    "terminal_receipt_id": receipt_id}

    def _save_terminal_history(self, root: Path, state: dict[str, Any], *, event: str,
                               details: Any = None, force_projection: bool = False) -> None:
        """Common terminal writer; callers hold the retention writer lock."""
        from .tx_recycling import assert_no_review_intent
        assert_no_review_intent(self, str(state.get("transaction_id") or ""))
        from .tx_retention import TERMINAL
        if state.get("state") not in TERMINAL or self.lease_path.exists() or self.lease_path.is_symlink():
            raise WorkshopError("TX_HISTORY_WRITE_BOUNDARY", "history writes require a terminal, unleased transaction")
        state["updated_at"] = utc_now()
        state.setdefault("journal", []).append({"event": event, "at": state["updated_at"], "details": details})
        stage_lifecycle_event(self, root, state, event=event, required_projection=force_projection)
        if event in {"TX_HISTORY_INTAKE", "TX_SCOPE_BOUND"}:
            state["historical_scope"]["event_id"] = state["tx_event_id"]
            state["historical_scope"]["event_sequence"] = state["tx_event_sequence"]
        super()._save_transaction(root, state, event=event, details=details, diagnostics_prepared=True)
        if force_projection or requires_projection(self, state):
            update_document(self, state, event=event)
            assert_projected(self, str(state["transaction_id"]))

    def intake_transaction_history(self, transaction_id: str, *, expected_report_sha256: str, reason: str) -> dict[str, Any]:
        from .tx_retention import _retention_lock
        from .tx_documents import prepare_scope_transition
        with _retention_lock(self, allow_reentry=False):
            from .tx_recycling import assert_no_review_intent
            assert_no_review_intent(self, transaction_id)
            root, state, replay = prepare_scope_transition(self, transaction_id, operation="INTAKE",
                expected_report_sha256=expected_report_sha256, reason=reason)
            if replay:
                self._reconcile_transaction_document_locked(transaction_id)
            else:
                self._save_terminal_history(root, state, event="TX_HISTORY_INTAKE", force_projection=True)
            return {"transaction_id": transaction_id, "event_id": state["historical_scope"]["event_id"],
                    "current_event_id": state.get("tx_event_id"), "scope": state["historical_scope"], "replayed": replay}

    def bind_transaction_scope(self, transaction_id: str, *, expected_report_sha256: str, reason: str) -> dict[str, Any]:
        from .tx_retention import _retention_lock
        from .tx_documents import prepare_scope_transition
        with _retention_lock(self, allow_reentry=False):
            from .tx_recycling import assert_no_review_intent
            assert_no_review_intent(self, transaction_id)
            root, state, replay = prepare_scope_transition(self, transaction_id, operation="BIND",
                expected_report_sha256=expected_report_sha256, reason=reason)
            if replay:
                self._reconcile_transaction_document_locked(transaction_id)
            else:
                self._save_terminal_history(root, state, event="TX_SCOPE_BOUND", force_projection=True)
            return {"transaction_id": transaction_id, "event_id": state["historical_scope"]["event_id"],
                    "current_event_id": state.get("tx_event_id"), "scope": state["historical_scope"], "replayed": replay}

    def inspect_transaction_scope(self, transaction_id: str) -> dict[str, Any]:
        """Read historical provenance without touching current scope or TX state."""
        from .tx_documents import inspect_historical_scope
        return inspect_historical_scope(self, transaction_id)

    def _reconcile_transaction_document_locked(self, transaction_id: str) -> dict[str, Any]:
        from .tx_documents import (PENDING_OUTCOME, _pending_marker_observation, pending_path, publish,
                                   pending_identity_matches, acknowledge_pending_marker)
        from .tx_lifecycle import (recover_event_intents, event_intents, event_for_marker,
                                   acknowledge_event_intents, endpoint)
        from .tx_retention import _checked_transaction_path
        _checked_transaction_path(self, transaction_id)
        root = self._transaction_path(transaction_id)
        try:
            _, state = self._load_transaction(transaction_id)
        except WorkshopError as exc:
            if exc.code != "TRANSACTION_MISSING":
                raise
            state = {}
        from .tx_recycling import assert_no_review_intent
        try:
            assert_no_review_intent(self, transaction_id)
        except WorkshopError as exc:
            observation = exc.details if isinstance(exc.details, dict) else {}
            if (exc.code != "TX_RECYCLING_INTENT_PENDING" or observation.get("status") != "OBSERVED"
                    or observation.get("event_id") != state.get("tx_event_id")
                    or observation.get("event_sequence") != state.get("tx_event_sequence")):
                raise
            # The exact review state is already committed: project it without
            # changing the separate submit owner's intent/evidence boundary.
        marker = pending_path(self, transaction_id)

        def pending_error(observation: dict[str, Any], message: str) -> WorkshopError:
            return WorkshopError("TX_DOCUMENT_SYNC_PENDING", message, details=[marker.name], outcome=PENDING_OUTCOME,
                references={"total": 1, "total_exact": True, "omitted": 0, "omitted_exact": True, "items": [observation]})

        observed = None
        try:
            marker.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            observation, _ = _pending_marker_observation(marker)
            raise pending_error(observation, "pending marker cannot be safely inspected") from exc
        else:
            observed, record = _pending_marker_observation(marker)
            if observed.get("status") != "OBSERVED" or not isinstance(record, dict):
                raise pending_error(observed, "pending marker is untrusted")
            try:
                identity = event_for_marker(state, str(record.get("event_id") or ""))
            except ValueError:
                # Only a separately validated exact native intent may explain a
                # broken prior material endpoint (e.g. proved scope-field loss).
                identity = None
            intended = next((item[1]["target_state"] for item in event_intents(self, transaction_id)
                             if item[1]["target_state"].get("tx_event_id") == record.get("event_id")), None)
            if identity is None and intended is not None:
                identity = intended["tx_event"]
            if identity is None:
                raise pending_error(observed, "pending identity has neither a native commit intent nor durable event resolution")
            if not pending_identity_matches(record, identity):
                raise pending_error(observed, "pending identity fields differ from their native event proof")


        retired = recover_event_intents(self, transaction_id)
        _, state = self._load_transaction(transaction_id)
        if observed is not None and observed.get("event_id") in retired:
            if event_for_marker(state, str(observed["event_id"])) is None:
                raise pending_error(observed, "native orphan retirement lost the original event identity")
            # The existing native owner already acknowledged its new terminal
            # event and the original obligation under the same writer lock.
            observed = None
        lease = read_json(self.lease_path) if self.lease_path.is_file() else None
        if self._guardian_enabled() and isinstance(lease, dict) and lease.get("transaction_id") == transaction_id:
            try:
                self._require_lease(transaction_id)
            except WorkshopError as exc:
                observation = observed or {"transaction_id": transaction_id,
                    "event_id": state.get("tx_event_id"), "event_sequence": state.get("tx_event_sequence"),
                    "status": "GUARDIAN_ACKNOWLEDGEMENT_PENDING"}
                raise pending_error(observation, "Guardian cannot acknowledge the exact saved event: " + exc.code) from exc
        result = publish(self, state)
        assert_projected(self, transaction_id, ignore_pending=True)
        if endpoint(self._load_transaction(transaction_id)[1]) != endpoint(state):
            raise pending_error(observed or {}, "source changed before pending acknowledgement")
        acknowledge_pending_marker(self, state, observed)
        acknowledge_event_intents(self, state)
        return result

    def reconcile_transaction_document(self, transaction_id: str) -> dict[str, Any]:
        from .tx_retention import _retention_lock
        with _retention_lock(self, allow_reentry=False):
            return self._reconcile_transaction_document_locked(transaction_id)

    def stale_abort(self, transaction_id: str, *, expected_current_package_sha256: str) -> dict[str, Any]:
        result = super().stale_abort(
            transaction_id, expected_current_package_sha256=expected_current_package_sha256
        )
        if requires_projection(self, self._load_transaction(transaction_id)[1]):
            assert_projected(self, transaction_id)
        return result

    def authority_recover(self, transaction_id: str, **kwargs: Any) -> dict[str, Any]:
        result = super().authority_recover(transaction_id, **kwargs)
        if self._guardian_enabled():
            root = self._transaction_path(transaction_id)
            state = read_json(root / "state.json")
            current = self._verified_package(require_seal=False)
            self.guardian().release_transaction(transaction_id, state=state, package_sha256=current)
        if requires_projection(self, self._load_transaction(transaction_id)[1]):
            assert_projected(self, transaction_id)
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
                if terminal not in {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}:
                    raise WorkshopError("GUARDIAN_RECONCILE_NOT_TERMINAL", f"transaction is not safely reconcilable: {terminal}")
                if (not isinstance(lease, dict) or lease.get("transaction_id") != active
                        or state.get("baseline_package_sha256") != current
                        or status.get("rolling_package_sha256") != current):
                    raise WorkshopError("GUARDIAN_RECONCILE_UNPROVEN", "nonterminal reconciliation requires its exact lease and sealed baseline")
                tx_event = state.get("tx_event")
                if not isinstance(tx_event, dict):
                    raise WorkshopError("GUARDIAN_TX_EVENT_INVALID", "nonterminal reconciliation requires a complete native event")
                guard.observe_transaction_state(str(active), event=str(tx_event.get("event") or ""),
                                                state=state, only_if_missing=True)
                result = guard.status(guardian_id, full=True)
                result["reconciled_transaction_id"] = str(active)
                result["lease_retained"] = True
                return result
            tx_event = state.get("tx_event")
            if any(state.get(key) is not None for key in ("tx_event", "tx_event_id", "tx_event_sequence")):
                if not isinstance(tx_event, dict):
                    raise WorkshopError("GUARDIAN_TX_EVENT_INVALID", "terminal transaction event identity is incomplete")
                guard.observe_transaction_state(str(active), event=str(tx_event.get("event") or ""),
                                                state=state, only_if_missing=True)
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
            active: list[str] = []
            directory = self.config.state_directory / "guardian-v2"
            if directory.is_dir():
                for path in directory.glob("GRD2_*.json"):
                    try:
                        payload = read_json(path)
                    except Exception as exc:
                        raise WorkshopError(
                            "GUARDIAN_SESSION_UNREADABLE",
                            "manual reseal requires readable Guardian session state",
                            details={"path": str(path), "cause": type(exc).__name__},
                        ) from exc
                    if (not isinstance(payload, dict)
                            or payload.get("schema") != GUARDIAN_SCHEMA
                            or payload.get("guardian_id") != path.stem
                            or len(path.stem) != 29
                            or not isinstance(payload.get("closed"), bool)
                            or (payload.get("patch_session") is not None
                                and not isinstance(payload.get("patch_session"), dict))):
                        raise WorkshopError(
                            "GUARDIAN_SESSION_CORRUPT",
                            "manual reseal requires structurally valid Guardian session identity and state",
                            details={"path": str(path)},
                        )
                    if not payload["closed"] and (payload.get("patch_session") or {}).get("status") in {"OPEN", "WORKSHOP_COMPLETE"}:
                        active.append(path.stem)
            if active:
                raise WorkshopError("GUARDIAN_SESSION_ACTIVE", "manual reseal is blocked while a Guardian patch session is open", details=sorted(active))
        return super().seal()

class RuntimePolicyTransaction:
    """A narrow machine-policy owner using the native lease/event/vault protocol."""

    SCHEMA = "werkfaden-runtime-policy-transaction/v1"
    KIND = "runtime_policy"

    def __init__(self, engine: WorkshopEngine) -> None:
        self.engine = engine

    def _supported(self) -> None:
        from .jev_admission import require_supported_apply_route
        self.engine._assert_canonical_control_plane()
        if self.engine._guardian_enabled():
            raise WorkshopError("POLICY_GUARDIAN_SCOPE_UNSUPPORTED",
                                "Guardian has no runtime-policy scope contract; no policy gate may be bypassed")
        require_supported_apply_route(self.engine.config, "runtime-policy")

    def _load(self, transaction_id: str, states: set[str] | None = None):
        self._supported()
        root, state = self.engine._load_transaction(transaction_id)
        if state.get("schema") != self.SCHEMA or state.get("transaction_kind") != self.KIND:
            raise WorkshopError("POLICY_TRANSACTION_KIND_INVALID", "transaction is not a native runtime-policy migration")
        if states is not None and state.get("state") not in states:
            raise WorkshopError("POLICY_TRANSACTION_STATE_INVALID", "operation is not supported from this policy state")
        return root, state

    def _files(self, directory: Path, names: set[str]) -> dict[str, bytes]:
        from .corpus import _policy_authority_bytes
        reject_link_components(directory, label="policy candidate root")
        observed = set()
        for path in directory.rglob("*"):
            reject_link_components(path, label="policy candidate entry")
            if path.is_file():
                observed.add(path.relative_to(directory).as_posix())
        if observed != names:
            raise WorkshopError("POLICY_CANDIDATE_SCOPE_INVALID", "policy tree differs from its exact admitted file set")
        return {name: _policy_authority_bytes(directory / name) for name in sorted(names)}

    def _package(self, root: Path, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
        from dataclasses import replace
        from .corpus import (_policy_json, runtime_policy_candidate, verify_runtime_policy_candidate)
        from .util import package_hash
        config = self.engine.config
        relative = config.config_path.relative_to(config.machine_root).as_posix()
        pins = state.get("policy_source_hashes")
        if (not isinstance(pins, dict) or not pins or set(pins) - {relative, "project-intake.json"}
                or relative not in pins):
            raise WorkshopError("POLICY_CANDIDATE_SCOPE_INVALID", "policy preimage paths are not the admitted machine targets")
        source = self._files(root / "baseline" / "machine", set(pins))
        if {name: sha256_bytes(data) for name, data in source.items()} != pins:
            raise WorkshopError("POLICY_BASELINE_CHANGED", "frozen policy preimages changed")
        manifest_path = root / "baseline" / "manifest.json"
        from .corpus import _policy_authority_bytes
        manifest_bytes = _policy_authority_bytes(manifest_path)
        if (sha256_bytes(manifest_bytes) != state.get("policy_baseline_manifest_sha256")
                or _policy_json(manifest_bytes).get("package_sha256") != state["baseline_package_sha256"]):
            raise WorkshopError("POLICY_BASELINE_CHANGED", "frozen complete baseline manifest changed")
        # Reuse the installed deterministic validator against the frozen authority,
        # without touching current machine bytes or inventing another allowlist.
        frozen = replace(config, machine_root=root / "baseline" / "machine",
                         config_path=root / "baseline" / "machine" / relative,
                         raw=_policy_json(source[relative]))
        derived = runtime_policy_candidate(frozen, state.get("policy_profile"))
        if derived["source_files"] != source:
            raise WorkshopError("POLICY_BASELINE_CHANGED", "frozen authority differs from its original dependency set")
        target = self._files(root / "work" / "machine", set(pins))
        proof = verify_runtime_policy_candidate(frozen, state["policy_profile"], target,
                                                expected_source_hashes=pins)
        review_path = root / "review" / "policy_review.json"
        from .corpus import _policy_authority_bytes
        review_bytes = _policy_authority_bytes(review_path)
        review = _policy_json(review_bytes)
        if (review.get("schema") != "werkfaden-runtime-policy-review/v1"
                or review.get("transaction_id") != state["transaction_id"]
                or review.get("plan_sha256") != state.get("policy_plan_sha256")
                or review.get("reviewed_files") != sorted(pins)
                or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
                or not isinstance(review.get("reason"), str) or len(review["reason"].strip()) < 40):
            raise WorkshopError("POLICY_REVIEW_INCOMPLETE", "review must explain the exact plan and all machine targets")
        package = {"schema": "werkfaden-runtime-policy-work/v1", "profile": state["policy_profile"],
                   "baseline_package_sha256": state["baseline_package_sha256"],
                   "baseline_seal_sha256": state["baseline_seal_sha256"],
                   "plan_sha256": state["policy_plan_sha256"],
                   "source_hashes": pins,
                   "target_hashes": {name: sha256_bytes(data) for name, data in target.items()},
                   "review_sha256": sha256_bytes(review_bytes), "candidate_proof": proof}
        package["package_sha256"] = package_hash(package)
        return package, target

    def _baseline(self, root: Path, state: dict[str, Any]) -> None:
        self.engine._require_lease(state["transaction_id"])
        reject_link_components(self.engine.seal_path, label="policy baseline seal")
        if sha256_bytes(self.engine.seal_path.read_bytes()) != state["baseline_seal_sha256"]:
            raise WorkshopError("POLICY_BASELINE_CHANGED", "policy baseline seal changed")
        self.engine._assert_live_baseline(state)
        from .corpus import _policy_authority_bytes
        for name, digest in state["policy_source_hashes"].items():
            if sha256_bytes(_policy_authority_bytes(self.engine.config.machine_root / name)) != digest:
                raise WorkshopError("POLICY_BASELINE_CHANGED", "live machine preimage differs from checkout")

    def checkout(self, profile: str, *, expected_plan_sha256: str, purpose: str) -> dict[str, Any]:
        import uuid
        from .corpus import runtime_policy_candidate
        from .util import atomic_write_bytes, sqlite_snapshot
        self._supported()
        if not isinstance(purpose, str) or len(purpose.strip()) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "policy checkout requires a concrete purpose")
        plan = self.engine.policy_activation_plan(profile)
        if expected_plan_sha256 != plan["plan_sha256"]:
            raise WorkshopError("POLICY_PLAN_CHANGED", "the reviewed exact policy plan is no longer current")
        candidate = runtime_policy_candidate(self.engine.config, profile)
        txid = "TXN_" + uuid.uuid4().hex[:24]
        root = self.engine._transaction_path(txid)
        self.engine._acquire_lease(txid)
        # Any failure after lease acquisition retains the native lease and the
        # staged state/outbox. Never orphan a required first publication.
        state = {"schema": "werkfaden-runtime-policy-transaction/v1", "transaction_kind": "runtime_policy", "transaction_id": txid,
                 "state": "CHECKOUT_STAGING", "created_at": utc_now(), "purpose": purpose.strip(),
                 "sources": [], "documents": [], "dependent_tests": [], "journal": [],
                 "document_projection_required": True, "policy_profile": profile,
                 "policy_plan_sha256": plan["plan_sha256"],
                 "baseline_package_sha256": plan["baseline_package_sha256"],
                 "baseline_seal_sha256": plan["baseline_seal_sha256"],
                 "policy_source_hashes": {name: sha256_bytes(data)
                                          for name, data in candidate["source_files"].items()}}
        root.mkdir(parents=True)
        self.engine._save_transaction(root, state, event="POLICY_CHECKOUT_STAGING")
        self._baseline(root, state)
        for name, data in candidate["source_files"].items():
            atomic_write_bytes(root / "baseline" / "machine" / name, data)
        for name, data in candidate["candidate_files"].items():
            atomic_write_bytes(root / "work" / "machine" / name, data)
        atomic_write_json(root / "baseline" / "manifest.json", build_corpus_manifest(self.engine.config))
        state["policy_baseline_manifest_sha256"] = sha256_bytes((root / "baseline" / "manifest.json").read_bytes())
        sqlite_snapshot(self.engine.config.kairos_database, root / "baseline" / "kairos.db")
        atomic_write_json(root / "review" / "policy_review.json",
                          {"schema": "werkfaden-runtime-policy-review/v1", "transaction_id": txid,
                           "plan_sha256": plan["plan_sha256"], "reviewed_files": [],
                           "reviewer": "", "reason": ""})
        self._baseline(root, state)
        state["state"] = "CHECKED_OUT"
        self.engine._save_transaction(root, state, event="POLICY_CHECKOUT_COMPLETED",
                                      details={"profile": profile, "files": sorted(candidate["source_files"])})
        assert_projected(self.engine, txid)
        return {"schema": self.SCHEMA, "transaction_id": txid, "state": state["state"],
                "work_directory": str(root / "work"), "review": str(root / "review" / "policy_review.json"),
                "plan_sha256": plan["plan_sha256"]}

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id, {"CHECKED_OUT", "PREPARED", "SHADOW_VERIFIED"})
        self._baseline(root, state)
        package, _ = self._package(root, state)
        state.pop("verified_work_package_sha256", None)
        state["prepared_work_package_sha256"] = package["package_sha256"]
        state["state"] = "PREPARED"
        self.engine._save_transaction(root, state, event="POLICY_PREPARED",
                                      details={"work_package_sha256": package["package_sha256"]})
        return {"transaction_id": transaction_id, "state": state["state"],
                "work_package_sha256": package["package_sha256"]}

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id, {"PREPARED", "SHADOW_VERIFIED"})
        self._baseline(root, state)
        package, _ = self._package(root, state)
        if package["package_sha256"] != state.get("prepared_work_package_sha256"):
            raise WorkshopError("WORK_CHANGED_AFTER_PREPARE", "policy candidate or review changed")
        state["verified_work_package_sha256"] = package["package_sha256"]
        state["state"] = "SHADOW_VERIFIED"
        self.engine._save_transaction(root, state, event="POLICY_VERIFIED",
                                      details={"candidate_proof": package["candidate_proof"]})
        archived = snapshot(self.engine, transaction_id)
        assert_verified_snapshot(self.engine, transaction_id)
        return {"transaction_id": transaction_id, "state": state["state"],
                "work_package_sha256": package["package_sha256"], "archive": archived}

    def _verified(self, root: Path, state: dict[str, Any]):
        package, target = self._package(root, state)
        if (package["package_sha256"] != state.get("verified_work_package_sha256")
                or package["package_sha256"] != state.get("prepared_work_package_sha256")):
            raise WorkshopError("WORK_CHANGED_AFTER_VERIFY", "policy candidate/review no longer matches verified work")
        assert_verified_snapshot(self.engine, state["transaction_id"])
        return package, target

    def _reload(self) -> None:
        from .corpus import load_config
        from .scientific import ScientificParityGate
        from .metadata_gate import MetadataGate
        from .context_documents import ContextDocuments
        engine = self.engine
        engine.config = load_config(engine.config.config_path)
        engine.scientific_gate = ScientificParityGate(engine.config)
        engine.metadata_gate = MetadataGate(engine.config)
        engine.context_documents = ContextDocuments(engine.config)
        engine._assert_canonical_control_plane()

    def _post_manifest(self, root: Path, state: dict[str, Any], target: dict[str, bytes]) -> dict[str, Any]:
        from .corpus import _policy_authority_bytes
        manifest = build_corpus_manifest(self.engine.config)
        baseline = read_json(root / "baseline" / "manifest.json")
        if not manifest.get("verified") or manifest["records"] != baseline["records"]:
            raise WorkshopError("POLICY_POSTCHECK_DRIFT", "source, metadata or database authority changed during activation")
        before = dict(baseline["authority"])
        after = dict(manifest["authority"])
        before_machine = before.pop("machine_authority_files")
        after_machine = after.pop("machine_authority_files")
        if before != after or set(before_machine) != set(after_machine):
            raise WorkshopError("POLICY_POSTCHECK_DRIFT", "membership/build authority changed during activation")
        for name, facts in after_machine.items():
            if name in target:
                if (facts["sha256"] != sha256_bytes(target[name])
                        or _policy_authority_bytes(self.engine.config.machine_root / name) != target[name]):
                    raise WorkshopError("POLICY_POSTCHECK_DRIFT", "policy target differs from exact verified bytes")
            elif facts != before_machine[name]:
                raise WorkshopError("POLICY_POSTCHECK_DRIFT", "unselected machine authority changed")
        return manifest

    def _health(self) -> dict[str, Any]:
        import importlib
        health = importlib.import_module("kairos.health").health_audit(self.engine.config.kairos_workspace, full=True)
        if health.get("verdict") != "PASS":
            raise WorkshopError("KAIROS_HEALTH_FAILED", "runtime-policy requires a verified full health audit",
                                details=health.get("failures"))
        return health

    def _finish(self, root: Path, state: dict[str, Any], target: dict[str, bytes], *, recovered: bool):
        engine = self.engine
        _, heartbeat = engine._import_kairos()
        self._reload()
        self._post_manifest(root, state, target)
        receipt = heartbeat.run_heartbeat(engine.config.kairos_workspace, requested_mode="verify",
                                          changed_paths=[], use_reconciliation=True,
                                          trigger="runtime-policy-result:" + state["transaction_id"])
        if not receipt.get("verified"):
            raise WorkshopError("HEARTBEAT_NOT_VERIFIED", "policy result heartbeat did not verify")
        health = self._health()
        post = self._post_manifest(root, state, target)
        mirror = engine._mirror(post)
        seal = {"schema": "runtime-sync-seal/v1", "created_at": utc_now(),
                "package_sha256": post["package_sha256"], "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(engine.config.state_directory / "mirrors" / (mirror["mirror_sha256"] + ".json")),
                "counts": post["counts"], "transaction_id": state["transaction_id"], "runtime_policy": True}
        atomic_write_json(engine.seal_path, seal)
        payload = {"schema": "werkfaden-runtime-policy-postcheck/v1", "transaction_id": state["transaction_id"],
                   "state": "POSTCHECK_VERIFIED", "profile": state["policy_profile"],
                   "baseline_package_sha256": state["baseline_package_sha256"],
                   "work_package_sha256": state["verified_work_package_sha256"],
                   "postcheck_package_sha256": post["package_sha256"], "heartbeat": receipt,
                   "health": {"verdict": health["verdict"]}, "bit_exact": True,
                   "recovered": recovered}
        state.update(state="POSTCHECK_VERIFIED", postcheck_package_sha256=post["package_sha256"],
                     policy_result=payload)
        engine._save_transaction(root, state, event="POLICY_POSTCHECK_VERIFIED",
                                 details={"package_sha256": post["package_sha256"]})
        return engine._finish_transaction_receipt(root, state, kind="policy-postcheck", payload=payload)

    def _restore(self, root: Path, state: dict[str, Any], target: dict[str, bytes]) -> None:
        from .corpus import _policy_authority_bytes
        from .util import restore_rollback_preimage, atomic_write_bytes
        rows = state.get("policy_rollback_preimages")
        if not isinstance(rows, dict) or set(rows) != set(target):
            raise WorkshopError("POLICY_ROLLBACK_UNPROVEN", "rollback lacks exact complete preimages")
        # Check ALL targets and preimages before the first recovery write.
        for name, data in target.items():
            raw = _policy_authority_bytes(self.engine.config.machine_root / name)
            original = _policy_authority_bytes(root / "rollback" / "machine" / name)
            if (sha256_bytes(original) != state["policy_source_hashes"][name]
                    or rows[name].get("sha256") != state["policy_source_hashes"][name]
                    or type(rows[name].get("mtime_ns")) is not int
                    or raw not in (original, data)):
                raise WorkshopError("POLICY_ROLLBACK_UNPROVEN", "a target/preimage is foreign or incomplete")
        def atomic_copy(source: Path, destination: Path):
            atomic_write_bytes(destination, _policy_authority_bytes(source))
        config_relative = self.engine.config.config_path.relative_to(self.engine.config.machine_root).as_posix()
        for name in sorted(target, key=lambda n: (n == config_relative, n)):
            row = rows[name]
            restore_rollback_preimage(root / "rollback" / "machine" / name,
                                     self.engine.config.machine_root / name, row["sha256"], row["mtime_ns"],
                                     copier=atomic_copy)
        self._reload()
        self._baseline(root, state)
        state["state"] = "ROLLED_BACK"
        self.engine._save_transaction(root, state, event="POLICY_ROLLED_BACK",
                                      details={"exact_preimages_restored": True, "apply_repeated": False})
        self.engine._release_lease(state["transaction_id"])

    def apply(self, transaction_id: str):
        from .corpus import _policy_authority_bytes
        from .util import capture_rollback_preimage, atomic_write_bytes
        root, state = self._load(transaction_id, {"SHADOW_VERIFIED"})
        self._baseline(root, state)
        _, target = self._verified(root, state)
        assert_projected(self.engine, transaction_id)
        # Existing document or DB failures must refuse BEFORE any machine write.
        self._health()
        rows = {}
        for name in sorted(target):
            rows[name] = capture_rollback_preimage(self.engine.config.machine_root / name,
                                                   root / "rollback" / "machine" / name)
            if rows[name]["sha256"] != state["policy_source_hashes"][name]:
                raise WorkshopError("POLICY_BASELINE_CHANGED", "machine changed before Apply")
        state.update(state="APPLYING", policy_commit_boundary="FILES_WRITING", policy_rollback_preimages=rows)
        self.engine._save_transaction(root, state, event="POLICY_APPLY_STARTED")
        try:
            config_relative = self.engine.config.config_path.relative_to(self.engine.config.machine_root).as_posix()
            for name in sorted(target, key=lambda n: (n == config_relative, n)):
                if sha256_bytes(_policy_authority_bytes(self.engine.config.machine_root / name)) != rows[name]["sha256"]:
                    raise WorkshopError("POLICY_BASELINE_CHANGED", "machine target changed before atomic replacement")
                atomic_write_bytes(self.engine.config.machine_root / name, target[name])
            self._reload()
            self._post_manifest(root, state, target)
            state["policy_commit_boundary"] = "HEARTBEAT_STARTED"
            self.engine._save_transaction(root, state, event="POLICY_HEARTBEAT_STARTED")
        except Exception:
            self._restore(root, state, target)
            raise
        # Crossing heartbeat is a forward-recovery boundary. Never roll a
        # published result back by catching a later projection/receipt error.
        return self._finish(root, state, target, recovered=False)

    def recover(self, transaction_id: str):
        from .corpus import _policy_authority_bytes
        root, state = self._load(transaction_id)
        self.engine.reconcile_transaction_document(transaction_id)
        root, state = self._load(transaction_id)
        if state["state"] == "POSTCHECK_VERIFIED":
            payload = state.get("policy_result")
            if not isinstance(payload, dict):
                raise WorkshopError("POLICY_TERMINAL_SEED_MISSING", "policy postcheck has no preserved exact payload")
            # A committed native receipt is historical proof, even after work
            # compaction or a later deployment. Never require old bytes live.
            if not state.get("terminal_receipt"):
                _, target = self._verified(root, state)
                self._post_manifest(root, state, target)
                if self.engine._verified_package(require_seal=True) != state.get("postcheck_package_sha256"):
                    raise WorkshopError("POLICY_RECOVERY_SEAL_DRIFT", "uncommitted postcheck result changed")
            return self.engine._finish_transaction_receipt(root, state, kind="policy-postcheck", payload=payload)
        if state["state"] != "APPLYING":
            raise WorkshopError("POLICY_RECOVERY_STATE_INVALID", "policy recovery requires an interrupted Apply")
        self.engine._require_lease(transaction_id)
        _, target = self._verified(root, state)
        seal = read_json(self.engine.seal_path)
        baseline_seal = sha256_bytes(self.engine.seal_path.read_bytes()) == state["baseline_seal_sha256"]
        own_seal = seal.get("transaction_id") == transaction_id and seal.get("runtime_policy") is True
        if not baseline_seal and not own_seal:
            raise WorkshopError("POLICY_RECOVERY_SEAL_DRIFT", "seal is neither exact baseline nor this policy result")
        observed = {name: _policy_authority_bytes(self.engine.config.machine_root / name) for name in target}
        baseline = self._files(root / "baseline" / "machine", set(target))
        if observed == baseline and baseline_seal:
            self._reload()
            self._baseline(root, state)
            state["state"] = "ROLLED_BACK"
            self.engine._save_transaction(root, state, event="POLICY_BASELINE_RECOVERED",
                                          details={"exact_baseline": True, "apply_repeated": False})
            self.engine._release_lease(transaction_id)
            return {"transaction_id": transaction_id, "state": state["state"], "apply_repeated": False}
        if observed == target:
            self._reload()
            self._post_manifest(root, state, target)
            return self._finish(root, state, target, recovered=True)
        if state.get("policy_commit_boundary") == "FILES_WRITING" and baseline_seal:
            self._restore(root, state, target)
            return {"transaction_id": transaction_id, "state": state["state"], "apply_repeated": False}
        raise WorkshopError("POLICY_RECOVERY_TARGET_DRIFT", "mixed/foreign targets after heartbeat require explicit recovery evidence")

    def abort(self, transaction_id: str):
        root, state = self._load(transaction_id, {"CHECKOUT_STAGING", "CHECKED_OUT", "PREPARED", "SHADOW_VERIFIED"})
        self.engine.reconcile_transaction_document(transaction_id)
        root, state = self._load(transaction_id)
        self._baseline(root, state)
        state["state"] = "ABORTED"
        self.engine._save_transaction(root, state, event="POLICY_ABORTED")
        self.engine._release_lease(transaction_id)
        return {"transaction_id": transaction_id, "state": state["state"]}

