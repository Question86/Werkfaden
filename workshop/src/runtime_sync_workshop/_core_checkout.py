from __future__ import annotations

from ._core_contract import EngineCoreContract

from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .corpus import (
    build_corpus_manifest,
)
from .util import (
    WorkshopError,
    atomic_write_json,
    copy_exact,
    package_hash,
    read_json,
    sqlite_snapshot,
    utc_now,
)


from ._core_state import PRE_APPLY_STATES


class CheckoutCore(EngineCoreContract):
    """Private checkout responsibility; composed only by the canonical engine base."""

    def checkout(
        self, sources: Iterable[str], *, purpose: str, tests: Iterable[str] = (),
        documents: Iterable[str] = (),
    ) -> dict[str, Any]:
        normalized_sources = sorted({str(value).replace("\\", "/") for value in sources})
        selected_documents = self.context_documents.normalize(documents)
        if not normalized_sources:
            raise WorkshopError("CHECKOUT_SCOPE_EMPTY", "checkout requires at least one --source")
        if len(purpose.strip()) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "checkout purpose must contain at least 12 characters")
        if not self.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "run seal after a fully verified status before checkout")
        seal = read_json(self.seal_path)
        manifest = build_corpus_manifest(self.config)
        if not manifest["verified"]:
            raise WorkshopError("CORPUS_NOT_VERIFIED", "checkout is blocked by corpus drift", details=manifest["issues"])
        if seal.get("package_sha256") != manifest["package_sha256"]:
            raise WorkshopError("SEAL_DRIFT", "live corpus differs from the trusted seal; inspect status and reseal only after resolution")
        missing = sorted(set(normalized_sources) - set(manifest["records"]))
        if missing:
            raise WorkshopError(
                "CHECKOUT_SOURCE_UNKNOWN",
                "checkout source is not in the configured translation-unit authority",
                details=missing,
            )
        selected_tests = sorted(set(tests))
        admitted_tests = manifest["authority"].get("dependent_tests", {})
        for relative in selected_tests:
            if relative not in admitted_tests or admitted_tests[relative]["owner"] not in normalized_sources:
                raise WorkshopError("CHECKOUT_TEST_NOT_ADMITTED", f"test requires its configured selected Runtime owner: {relative}")
        created_at = utc_now()
        attempt_nonce = uuid4().hex
        transaction_id = "TXN_" + package_hash({
            "baseline": manifest["package_sha256"],
            "sources": normalized_sources,
            "dependent_tests": selected_tests,
            "documents": selected_documents,
            "purpose": purpose.strip(),
            "created_at": created_at,
            "attempt_nonce": attempt_nonce,
        })[:24]
        root = self._transaction_path(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self._acquire_lease(transaction_id)
        try:
            (root / "baseline" / "runtime").mkdir(parents=True)
            (root / "baseline" / "blueprints").mkdir(parents=True)
            (root / "baseline" / "managed").mkdir(parents=True)
            (root / "work").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            atomic_write_json(root / "baseline" / "manifest.json", manifest)
            sqlite_snapshot(self.config.kairos_database, root / "baseline" / "kairos.db")
            review_entries: list[dict[str, Any]] = []
            for relative in selected_tests:
                copy_exact(self.config.codebase_root / relative, root / "baseline" / relative)
                copy_exact(self.config.codebase_root / relative, root / "work" / relative)
                review_entries.append({
                    "source": relative, "metadata_impact": "pending",
                    "reason": "", "reviewer": "", "reviewed_at": "",
                })
            for source in normalized_sources:
                record = manifest["records"][source]
                source_live = self.config.codebase_root / source
                source_baseline = root / "baseline" / source
                source_work = root / "work" / source
                copy_exact(source_live, source_baseline)
                copy_exact(source_live, source_work)
                header = record.get("header")
                if header:
                    header_live = Path(header["path"])
                    header_relative = header_live.resolve().relative_to(self.config.codebase_root).as_posix()
                    copy_exact(header_live, root / "baseline" / header_relative)
                    copy_exact(header_live, root / "work" / header_relative)
                name = record["blueprint"]["filename"]
                copy_exact(self.config.blueprint_root / name, root / "baseline" / "blueprints" / name)
                copy_exact(self.config.blueprint_root / name, root / "work" / "blueprints" / name)
                copy_exact(self.config.managed_blueprint_root / name, root / "baseline" / "managed" / name)
                copy_exact(self.config.managed_blueprint_root / name, root / "work" / "managed" / name)
                review_entries.append({
                    "source": source,
                    "metadata_impact": "pending",
                    "reason": "",
                    "reviewer": "",
                    "reviewed_at": "",
                })
            review = {
                "schema": "runtime-sync-metadata-review/v1",
                "transaction_id": transaction_id,
                "semantic_fields": ["capsule", "answers", "refs", "claim_boundary", "sections"],
                "entries": review_entries,
            }
            atomic_write_json(root / "review" / "metadata_review.json", review)
            state = {
                "schema": "runtime-sync-transaction/v1",
                "transaction_id": transaction_id,
                "state": "CHECKED_OUT",
                "created_at": created_at,
                "attempt_nonce": attempt_nonce,
                "updated_at": created_at,
                "deterministic_updated_at": created_at,
                "purpose": purpose.strip(),
                "sources": normalized_sources,
                "dependent_tests": selected_tests,
                "baseline_package_sha256": manifest["package_sha256"],
                "journal": [],
            }
            self.context_documents.checkout(root, state, selected_documents)
            from .corpus import framework_projection_checkout
            framework_projection_checkout(self.config, root, state, getattr(self, "_framework_doc_request", None))
            self._save_transaction(root, state, event="CHECKOUT_COMPLETED", details={
                "sources": normalized_sources, "documents": selected_documents,
            })
        except Exception:
            if self.lease_path.exists():
                self._release_lease(transaction_id)
            raise
        return {
            "schema": "runtime-sync-checkout/v1",
            "transaction_id": transaction_id,
            "state": "CHECKED_OUT",
            "work_directory": str(root / "work"),
            "metadata_review": str(root / "review" / "metadata_review.json"),
            "sources": normalized_sources,
            "dependent_tests": selected_tests,
            "documents": selected_documents,
            "document_review": str(root / "review" / "context_review.json") if selected_documents else None,
            "metadata_aftercheck": {
                "required_for": "every changed source or header, in every supported language",
                "instructions": "Update the affected semantic header and sections in work/blueprints, then run metadata-plan. Inspect the selected source samples, complete review/metadata_audit.json, and run prepare -> metadata-check -> verify -> apply. A body-only edit or regenerated hashes do not satisfy the header obligation.",
            },
        }

    def include_documents(
        self, transaction_id: str, documents: Iterable[str], *, reason: str,
    ) -> dict[str, Any]:
        """Expand a normal transaction's context scope without discarding its edits."""
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state.get("schema") != "runtime-sync-transaction/v1" or state["state"] not in PRE_APPLY_STATES:
            raise WorkshopError("TRANSACTION_STATE_INVALID", "document inclusion requires a pre-apply source transaction")
        if len(reason.strip()) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "document inclusion reason must contain at least 12 characters")
        self._assert_live_baseline(state)
        selected = self.context_documents.normalize(documents)
        if not selected:
            raise WorkshopError("CONTEXT_SCOPE_EMPTY", "include-documents requires at least one document")
        added = sorted(set(selected) - set(state.get("documents", [])))
        if added:
            self.context_documents.checkout(root, state, added)
            state["state"] = "CHECKED_OUT"
            for key in ("prepared_work_package_sha256", "verified_work_package_sha256", "shadow", "metadata_check"):
                state.pop(key, None)
            self._save_transaction(root, state, event="CONTEXT_SCOPE_EXPANDED", details={
                "documents": added, "reason": reason.strip(),
            })
        return {
            "schema": "runtime-sync-context-inclusion/v1", "transaction_id": transaction_id,
            "state": state["state"], "documents": state.get("documents", []), "added": added,
            "work_directory": str(root / "work"),
            "document_review": str(root / "review" / "context_review.json"),
        }

