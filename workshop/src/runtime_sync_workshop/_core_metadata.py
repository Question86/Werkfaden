from __future__ import annotations

from ._core_contract import EngineCoreContract

from pathlib import Path
from typing import Any

from .scientific import transaction_work_manifest
from .metadata_gate import POLICY_SCHEMA
from .util import (
    WorkshopError,
    atomic_write_json,
    read_json,
    utc_now,
)


from ._core_state import PRE_APPLY_STATES


class MetadataCore(EngineCoreContract):
    """Private metadata responsibility; composed only by the canonical engine base."""

    def metadata_plan(self, transaction_id: str, *, refresh: bool = False) -> dict[str, Any]:
        """Stage an unanswered, deterministic aftercheck for the edited candidate."""
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state.get("schema") not in {"runtime-sync-transaction/v1", "runtime-sync-authority-migration/v1"} or state["state"] not in PRE_APPLY_STATES:
            raise WorkshopError("TRANSACTION_STATE_INVALID", "metadata-plan requires a source patch or authority migration before apply")
        self._assert_live_baseline(state)
        plan = self.metadata_gate.plan(root, state)
        target = root / "review" / "metadata_audit.json"
        preserve = False
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1:
                raise WorkshopError("WORK_FILE_INVALID", "metadata audit must be a regular transaction file")
            existing = read_json(target)
            preserve = isinstance(existing, dict) and existing.get("plan_sha256") == plan["plan_sha256"]
            if not preserve and not refresh:
                raise WorkshopError("METADATA_PLAN_STALE", "candidate changed; run metadata-plan --refresh and repeat the aftercheck")
        if not preserve or refresh:
            atomic_write_json(target, self.metadata_gate.draft(plan))
            state["state"] = "CHECKED_OUT"
            for key in ("prepared_work_package_sha256", "verified_work_package_sha256", "shadow", "metadata_check"):
                state.pop(key, None)
            self._save_transaction(root, state, event="METADATA_PLAN_CREATED", details={
                "plan_sha256": plan["plan_sha256"], "required": plan["required"],
            })
        prefix = "authority-" if state.get("transaction_kind") == "authority_migration" else ""
        return {
            "schema": "runtime-sync-metadata-plan-result/v1", "transaction_id": transaction_id,
            "state": state["state"], "plan": plan, "metadata_audit": str(target),
            "existing_review_preserved": preserve and not refresh,
            "instructions": f"Review every listed header field and source sample against the edited files. Fill exact document claims, reasons and pass/partial/fail verdicts in metadata_audit.json; correct partial/fail findings before {prefix}prepare. Then run metadata-check, {prefix}verify and {prefix}apply. The tool does not supply the semantic judgment.",
            "review_format": {
                "field_reviews": "For every field, set action to updated or unchanged according to the actual values, explain why in reason, and list supporting sample_ids from this subject.",
                "samples": "Preserve every generated sample fact. Set verdict to pass, partial or fail, explain the code-to-metadata relationship in reason, and supply nonempty metadata_evidence.",
                "metadata_evidence_example": {"field": "capsule", "quote": "<exact quotation from the candidate capsule, at least 8 characters>"},
                "review_identity": "Set reviewer and reviewed_at; reviewed_at must be a valid timezone-aware RFC3339 timestamp.",
                "completion": "Every explanation needs at least 12 characters; all samples must pass. Length and exact quotations do not establish semantic truth: inspect and correct the claims yourself.",
            },
        }

    def _metadata_work_manifest(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("schema") == "runtime-sync-authority-migration/v1":
            from .migration import AuthorityMigration
            return AuthorityMigration(self)._work_package(root, state)
        return transaction_work_manifest(self.config, root, state)

    def metadata_check(self, transaction_id: str) -> dict[str, Any]:
        """Verify the completed aftercheck and bind its receipt to prepared bytes."""
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state.get("schema") not in {"runtime-sync-transaction/v1", "runtime-sync-authority-migration/v1"} or state["state"] not in {"PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", "metadata-check requires a prepared source patch or authority migration")
        self._assert_live_baseline(state)
        before = self._metadata_work_manifest(root, state)
        if before["package_sha256"] != state.get("prepared_work_package_sha256"):
            raise WorkshopError("WORK_CHANGED_AFTER_PREPARE", "candidate or audit changed; repeat prepare before metadata-check")
        result = self.metadata_gate.validate(root, state)
        after = self._metadata_work_manifest(root, state)
        if before["package_sha256"] != after["package_sha256"]:
            raise WorkshopError("WORK_CHANGED_DURING_METADATA_CHECK", "candidate changed during metadata aftercheck")
        receipt = {
            "schema": "runtime-sync-metadata-check/v1", "policy": POLICY_SCHEMA,
            "transaction_id": transaction_id, "work_package_sha256": after["package_sha256"],
            "verified": True, "checked_at": utc_now(), "review": result,
        }
        self._write_receipt("metadata-check", receipt)
        state["metadata_check"] = receipt
        self._save_transaction(root, state, event="METADATA_CHECKED", details={
            "work_package_sha256": after["package_sha256"], "required": result.get("required", True),
        })
        return {**receipt, "state": state["state"]}

    def _assert_metadata_checked(self, root: Path, state: dict[str, Any], work_sha256: str) -> dict[str, Any]:
        result = self.metadata_gate.validate(root, state)
        if not result.get("required", True):
            return result
        receipt = state.get("metadata_check")
        if not isinstance(receipt, dict):
            raise WorkshopError("METADATA_CHECK_REQUIRED", "run metadata-check after prepare before verify or apply")
        if (receipt.get("schema") != "runtime-sync-metadata-check/v1"
                or receipt.get("policy") != POLICY_SCHEMA
                or receipt.get("transaction_id") != state["transaction_id"]
                or receipt.get("verified") is not True
                or receipt.get("work_package_sha256") != work_sha256
                or receipt.get("review") != result):
            raise WorkshopError("METADATA_CHECK_STALE", "metadata aftercheck does not bind this exact prepared candidate")
        return result

    def _review_map(self, root: Path, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        review = read_json(root / "review" / "metadata_review.json")
        if review.get("schema") != "runtime-sync-metadata-review/v1" or review.get("transaction_id") != state["transaction_id"]:
            raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review schema or transaction binding is invalid")
        entries = review.get("entries")
        if not isinstance(entries, list):
            raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review entries must be an array")
        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review entry has no source")
            source = entry["source"].replace("\\", "/")
            if source in result:
                raise WorkshopError("METADATA_REVIEW_INVALID", f"duplicate metadata review entry: {source}")
            result[source] = entry
        return result

