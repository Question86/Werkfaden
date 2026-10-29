from __future__ import annotations

from ._core_contract import EngineCoreContract

import importlib
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .blueprint import (
    parse_blueprint,
    verify_ledger,
)
from .corpus import (
    CorpusEntry,
    blueprint_inventory,
    verify_database_projection,
)
from .scientific import transaction_work_manifest
from .util import (
    WorkshopError,
    copy_exact,
    read_json,
    sha256_bytes,
)




class VerifyCore(EngineCoreContract):
    """Private verify responsibility; composed only by the canonical engine base."""

    def _shadow_native(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        shadow = root / "shadow"
        if shadow.exists():
            shutil.rmtree(shadow)
        (shadow / ".kairos" / "events").mkdir(parents=True)
        (shadow / ".kairos" / "receipts").mkdir(parents=True)
        (shadow / "code").mkdir(parents=True)
        copy_exact(self.config.kairos_workspace / ".kairos" / "config.json", shadow / ".kairos" / "config.json")
        copy_exact(root / "baseline" / "kairos.db", shadow / ".kairos" / "kairos.db")
        # A native shadow is a document-validation workspace, not merely a code
        # directory. Changed implementation documents retain typed references to
        # tasks, method documents, source indexes and other authoritative sources;
        # those targets must exist in the shadow or a correct first project patch
        # fails for reasons unrelated to the proposed change. Copy authoritative
        # source documents only; derived state and receipts remain isolated.
        workspace_config = read_json(self.config.kairos_workspace / ".kairos" / "config.json")
        roots = workspace_config.get("document_roots", []) if isinstance(workspace_config, dict) else []
        for root_name in roots:
            source_root = self.config.kairos_workspace / str(root_name)
            if not source_root.is_dir():
                continue
            for source_path in source_root.rglob("*"):
                if source_path.is_file():
                    relative_path = source_path.relative_to(self.config.kairos_workspace)
                    copy_exact(source_path, shadow / relative_path)
        for source_path in (self.config.kairos_workspace / "goals").glob("*.json"):
            if source_path.is_file():
                copy_exact(source_path, shadow / source_path.relative_to(self.config.kairos_workspace))
        for name in workspace_config.get("canonical_files", []) if isinstance(workspace_config, dict) else []:
            source_path = self.config.kairos_workspace / str(name)
            if source_path.is_file():
                copy_exact(source_path, shadow / str(name))
        current_path = self.config.kairos_workspace / "current.json"
        if current_path.is_file():
            copy_exact(current_path, shadow / "current.json")
        for relative in state.get("authority_documents", []):
            copy_exact(root / "work" / "authority" / relative, shadow / relative)
        for relative in state.get("documents", []):
            copy_exact(root / "work" / "documents" / relative, shadow / relative)
        entries: list[CorpusEntry] = []
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            work = root / "work" / "blueprints" / name
            copy_exact(work, shadow / "code" / name)
        for header in state.get("topology_blueprints", []):
            name = baseline_manifest["records"][header]["blueprint"]["filename"]
            work = root / "work" / "blueprints" / name
            copy_exact(work, shadow / "code" / name)
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        database_module = importlib.import_module("kairos.database")
        promoter_module = importlib.import_module("kairos.promoter")
        database = database_module.KnowledgeDatabase(shadow / ".kairos" / "kairos.db")
        receipts: list[dict[str, Any]] = []
        for relative in state.get("authority_documents", []):
            receipt = promoter_module.promote_document(shadow / relative, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError(
                    "SHADOW_PROMOTION_FAILED",
                    f"shadow authority promotion was not verified: {relative}",
                    details=receipt,
                )
            receipts.append(receipt)
        inventory, inventory_issues = blueprint_inventory(self.config)
        if inventory_issues:
            raise WorkshopError("LIVE_INVENTORY_INVALID", "cannot construct shadow entries", details=inventory_issues)
        by_source = {entry.record_key: entry for entry in inventory}
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            shadow_document = parse_blueprint(shadow / "code" / name)
            live_entry = by_source[source]
            shadow_entry = replace(live_entry, blueprint_path=shadow / "code" / name, managed_path=shadow / "code" / name, document=shadow_document)
            receipt = promoter_module.promote_document(shadow_entry.managed_path, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"shadow promotion was not verified: {name}", details=receipt)
            receipts.append(receipt)
            entries.append(shadow_entry)
        for relative in state.get("changed_documents", []):
            try:
                receipt = promoter_module.promote_document(shadow / relative, shadow, database)
            except (OSError, ValueError, promoter_module.PromotionError) as exc:
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"context validation failed: {relative}", details=str(exc)) from exc
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"shadow context promotion was not verified: {relative}", details=receipt)
            receipts.append(receipt)
        for header in state.get("topology_blueprints", []):
            name = baseline_manifest["records"][header]["blueprint"]["filename"]
            shadow_document = parse_blueprint(shadow / "code" / name)
            live_entry = by_source[header]
            shadow_entry = replace(
                live_entry,
                blueprint_path=shadow / "code" / name,
                managed_path=shadow / "code" / name,
                document=shadow_document,
            )
            receipt = promoter_module.promote_document(shadow_entry.managed_path, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"shadow topology promotion was not verified: {name}", details=receipt)
            receipts.append(receipt)
            entries.append(shadow_entry)
        include_edge_projection = None
        edge_stage = state.get("include_edge_authority")
        if isinstance(edge_stage, dict):
            include_module = importlib.import_module("kairos.include_edges")
            include_edge_projection = include_module.project_compiler_include_edges(
                shadow,
                database,
                authority_path=root / "work" / "machine" / str(edge_stage["path"]),
            )
            if not include_edge_projection.get("verified"):
                raise WorkshopError("SHADOW_INCLUDE_EDGE_PROJECTION_FAILED", "shadow include-edge projection was not verified", details=include_edge_projection)
        summary, issues = verify_database_projection(self.config, entries, database_path=shadow / ".kairos" / "kairos.db")
        if issues:
            raise WorkshopError("SHADOW_PROJECTION_MISMATCH", "shadow KAIROS projection is not exact", details=issues)
        context_summary = self.context_documents.verify_projection(shadow, state.get("documents", []), shadow / ".kairos" / "kairos.db")
        return {"adapter": "native", "receipts": receipts, "database": summary, "context_documents": context_summary, "compiler_include_edges": include_edge_projection, "shadow_workspace": str(shadow)}

    def _shadow_fixture(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        if not self.config.raw.get("test_mode"):
            raise WorkshopError("FIXTURE_ADAPTER_FORBIDDEN", "fixture shadow adapter is allowed only when test_mode=true")
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            document = parse_blueprint(root / "work" / "blueprints" / name)
            if document.revision != baseline_manifest["records"][source]["blueprint"]["revision"] + 1:
                raise WorkshopError("SHADOW_REVISION_INVALID", f"fixture revision is not baseline+1: {name}")
        return {"adapter": "fixture", "verified": True}

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in {"PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"verify is not allowed from {state['state']}")
        self._assert_live_baseline(state)
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        prepared_work = transaction_work_manifest(self.config, root, state)
        if prepared_work["package_sha256"] != state.get("prepared_work_package_sha256"):
            raise WorkshopError("WORK_CHANGED_AFTER_PREPARE", "code, context, or review changed after prepare; run prepare again before verify")
        self._assert_metadata_checked(root, state, prepared_work["package_sha256"])
        intake_stage = state.get("project_intake_authority")
        if isinstance(intake_stage, dict):
            staged_intake = root / "work" / "machine" / str(intake_stage.get("path", ""))
            if (
                not staged_intake.is_file()
                or sha256_bytes(staged_intake.read_bytes()) != str(intake_stage.get("sha256", ""))
            ):
                raise WorkshopError(
                    "PROJECT_INTAKE_CHANGED_AFTER_PREPARE",
                    "staged project-intake authority changed after prepare",
                )
        if self._test_changes(root, state, baseline_manifest) != state.get("test_changes", []):
            raise WorkshopError("TEST_CHANGED_AFTER_PREPARE", "test change classification differs from prepared state")
        for source in state["changed_sources"]:
            record = baseline_manifest["records"][source]
            name = record["blueprint"]["filename"]
            blueprint = root / "work" / "blueprints" / name
            managed = root / "work" / "managed" / name
            if not blueprint.is_file() or not managed.is_file() or blueprint.read_bytes() != managed.read_bytes():
                raise WorkshopError("WORK_BLUEPRINT_PAIR_MISMATCH", f"work external/managed pair differs: {name}")
            document = parse_blueprint(blueprint)
            work_source = root / "work" / source
            mapped_work_source = work_source if document.source_path is not None else None
            work_header = None
            if record.get("header"):
                live_header = Path(record["header"]["path"])
                work_header = root / "work" / live_header.resolve().relative_to(self.config.codebase_root)
            ledger_issues = verify_ledger(document, mapped_work_source, work_header)
            if ledger_issues:
                raise WorkshopError("WORK_LEDGER_MISMATCH", f"work ledger differs for {name}", details=ledger_issues)
            if document.revision != record["blueprint"]["revision"] + 1:
                raise WorkshopError("WORK_REVISION_INVALID", f"revision must be baseline+1 for {name}")
        for header in state.get("topology_blueprints", []):
            record = baseline_manifest["records"][header]
            name = record["blueprint"]["filename"]
            blueprint = root / "work" / "blueprints" / name
            managed = root / "work" / "managed" / name
            if not blueprint.is_file() or not managed.is_file() or blueprint.read_bytes() != managed.read_bytes():
                raise WorkshopError("WORK_BLUEPRINT_PAIR_MISMATCH", f"topology work external/managed pair differs: {name}")
            document = parse_blueprint(blueprint)
            work_header = root / "work" / header
            if not work_header.is_file():
                work_header = self.config.codebase_root / header
            ledger_issues = verify_ledger(document, None, work_header)
            if ledger_issues:
                raise WorkshopError("WORK_LEDGER_MISMATCH", f"topology work ledger differs for {name}", details=ledger_issues)
            if document.revision != record["blueprint"]["revision"] + 1:
                raise WorkshopError("WORK_REVISION_INVALID", f"topology revision must be baseline+1 for {name}")
        adapter = str(self.config.raw.get("shadow_adapter", "native"))
        shadow = self._shadow_native(root, state, baseline_manifest) if adapter == "native" else self._shadow_fixture(root, state, baseline_manifest)
        work_package = transaction_work_manifest(self.config, root, state)
        if work_package["package_sha256"] != prepared_work["package_sha256"]:
            raise WorkshopError("WORK_CHANGED_DURING_VERIFY", "transaction files changed during shadow verification; prepare again")
        state["state"] = "SHADOW_VERIFIED"
        state["shadow"] = shadow
        state["verified_work_package_sha256"] = work_package["package_sha256"]
        self._save_transaction(
            root,
            state,
            event="SHADOW_VERIFIED",
            details={"adapter": adapter, "work_package_sha256": work_package["package_sha256"]},
        )
        return {
            "schema": "runtime-sync-shadow-verification/v1",
            "transaction_id": transaction_id,
            "state": state["state"],
            "shadow": shadow,
            "work_package_sha256": work_package["package_sha256"],
        }

