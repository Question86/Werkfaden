from __future__ import annotations

from ._core_contract import EngineCoreContract

from typing import Any

from .util import (
    WorkshopError,
)




class ScientificCore(EngineCoreContract):
    """Private scientific responsibility; composed only by the canonical engine base."""

    def scientific_baseline(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        gate = self.scientific_gate
        try:
            result = gate.baseline(root, state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_BASELINE_FAILED", details=details)
            raise
        state["scientific_parity"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_BASELINE_VERIFIED", details=result)
        return result

    def scientific_reproducibility(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        gate = self.scientific_gate
        try:
            result = gate.reproducibility(root, state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_REPRODUCIBILITY_FAILED", details=details)
            raise
        state["scientific_reproducibility"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
            "runner_sha256": result["runner_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_REPRODUCIBILITY_VERIFIED", details=result)
        return result

    def scientific_stage_runner_authority(
        self,
        transaction_id: str,
        *,
        decision_id: str,
        expected_runner_sha256: str,
    ) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        reproducibility_state = state.get("scientific_reproducibility")
        if not isinstance(reproducibility_state, dict):
            raise WorkshopError(
                "SCIENTIFIC_REPRODUCIBILITY_REQUIRED",
                "run scientific-reproducibility before staging a runner authority",
            )
        try:
            result = self.scientific_gate.stage_runner_authority(
                root,
                state,
                reproducibility_state,
                decision_id=decision_id,
                expected_runner_sha256=expected_runner_sha256,
            )
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_RUNNER_AUTHORITY_STAGE_FAILED", details=details)
            raise
        state["scientific_runner_authority"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "runner_path": result["runner_path"],
            "runner_sha256": result["runner_sha256"],
            "authority_receipt_path": result["authority_receipt_path"],
            "authority_receipt_sha256": result["authority_receipt_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_RUNNER_AUTHORITY_STAGED", details=result)
        return result

    def scientific_verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        baseline_state = state.get("scientific_parity")
        if not isinstance(baseline_state, dict):
            raise WorkshopError("SCIENTIFIC_BASELINE_REQUIRED", "run scientific-baseline before editing")
        gate = self.scientific_gate
        try:
            result = gate.verify_candidate(root, state, baseline_state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_CANDIDATE_FAILED", details=details)
            raise
        state["scientific_parity"] = {
            **baseline_state,
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
            "verified_work_package_sha256": result["verified_work_package_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_PARITY_VERIFIED", details=result)
        return result

