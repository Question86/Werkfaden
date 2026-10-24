from __future__ import annotations

import hashlib
import json
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
for value in (ROOT, ROOT / "workshop" / "src", ROOT / "kairos" / "kairos_harness"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.universal_init import initialize_universal_markdown  # noqa: E402
from kairos.database import KnowledgeDatabase  # noqa: E402
from kairos.frontmatter import split_frontmatter  # noqa: E402
from kairos.governance import issue_external_edit_permit  # noqa: E402
from kairos.heartbeat import run_heartbeat  # noqa: E402
from kairos.search import search_database  # noqa: E402
from kairos.templates import pointer, render_document  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402
from runtime_sync_workshop.util import WorkshopError, copy_exact  # noqa: E402


BEHAVIOR = "docs/APP_BEHAVIOR.md"
REFERENCE = "docs/APP_REFERENCE.md"
REVIEW_FIELDS = ["capsule", "answers", "refs", "claim_boundary", "sections"]


def project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_CONTEXT",
            "title": "Code and context transaction",
            "objective": "Keep application behavior and retrieval context synchronized.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_CONTEXT_01",
                "title": "Verified semantic context",
                "objective": "Apply code and its reviewed Markdown in one transaction.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_CONTEXT_001",
                    "description": "A successful apply exposes the matching context in search.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck and context retrieval assertions.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_CONTEXT_001",
            "title": "Synchronize code and context",
            "objective": "Verify an existing-file code patch and its Markdown as one package.",
            "milestone": "MILESTONE_CONTEXT_01",
            "criteria": ["CRIT_CONTEXT_001"],
        },
    }


class ContextTransactionTests(unittest.TestCase):
    """Exercise real native heartbeat promotion against isolated Python projects."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workshop-context-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "app.py").write_bytes(b"value = 1\n")
        self.workspace = self.root / "workspace"
        intake = initialize_universal_markdown(
            project_root=self.project,
            workspace=self.workspace,
            workspace_id="KAIROS_CONTEXT_TEST",
            project_spec=project_spec(),
        )
        self.assertEqual(intake["state"], "VERIFIED_PENDING_SEAL")
        # Establish authoritative context before sealing, using the same governed
        # promotion path as initial intake; never fabricate database rows.
        issue_external_edit_permit(
            self.workspace,
            paths=[BEHAVIOR, REFERENCE],
            reason="Establish initial application context before the first Workshop seal",
        )
        (self.workspace / BEHAVIOR).write_bytes(self.behavior_document().encode("utf-8"))
        (self.workspace / REFERENCE).write_bytes(self.reference_document().encode("utf-8"))
        receipt = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=[BEHAVIOR, REFERENCE],
            use_reconciliation=True,
            trigger="context-transaction-test-baseline",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.engine = WorkshopEngine(Path(intake["workshop_config"]))
        self.engine.seal()
        self.database = self.workspace / ".kairos" / "kairos.db"

    def metadata(self, artifact_id: str, *, capsule: str, boundary: str) -> dict:
        return {
            "schema": "kairos-context/v1",
            "id": artifact_id,
            "type": "documentation",
            "revision": 1,
            "state": "active",
            "authority": "architecture_authority",
            "workspace": "KAIROS_CONTEXT_TEST",
            "route": f"KAIROS_CONTEXT_TEST/{artifact_id}",
            "updated_at": "2026-09-10T00:00:00Z",
            "capsule": capsule,
            "claim_boundary": boundary,
            "entities": [artifact_id],
            "facets": ["application-behavior"],
            "criteria": [],
            "does_not_answer": ["deployment outcome"],
        }

    def behavior_document(self, *, updated: bool = False) -> str:
        term = "freshcounter" if updated else "legacycounter"
        value = 2 if updated else 1
        metadata = self.metadata(
            "APP_BEHAVIOR",
            capsule=f"The {term} application exposes value {value}.",
            boundary=f"This document describes {term} behavior at value {value}.",
        )
        metadata["answers"] = [{
            "intent": "behavior",
            "question": f"How does {term} behave?",
            "target": "s-behavior",
        }]
        metadata["refs"] = {"contract": pointer(
            REFERENCE,
            section="s-current" if updated else "s-original",
            artifact_id="APP_REFERENCE",
            version="1",
            relation="requires",
            tags=("application",),
        )}
        metadata["search_contract"] = [{
            "query": f"How does {term} behave?",
            "expected": "APP_BEHAVIOR#s-behavior",
            "required_top_k": 1,
        }]
        return render_document(metadata, "Application behavior", [{
            "id": "s-behavior",
            "title": "Behavior",
            "capsule": f"The {term} application exposes value {value}.",
            "content": f"The {term} application sets `value = {value}` in `app.py`.",
        }])

    def reference_document(self) -> str:
        metadata = self.metadata(
            "APP_REFERENCE",
            capsule="Application contracts distinguish original and current values.",
            boundary="This document records application contracts, not execution evidence.",
        )
        metadata["answers"] = [{
            "intent": "contract",
            "question": "Which value does the current application contract require?",
            "target": "s-current",
        }]
        metadata["refs"] = {}
        metadata["search_contract"] = [{
            "query": "Which value does the current application contract require?",
            "expected": "APP_REFERENCE#s-current",
            "required_top_k": 1,
        }]
        return render_document(metadata, "Application contracts", [
            {"id": "s-original", "title": "Original contract", "capsule": "The original value is one.",
             "content": "The original contract requires value one."},
            {"id": "s-current", "title": "Current contract", "capsule": "The current value is two.",
             "content": "The current contract requires value two."},
        ])

    def checkout(self, documents: tuple[str, ...] = (BEHAVIOR,)) -> dict:
        result = self.engine.checkout(
            ["app.py"], documents=documents,
            purpose="Update application value together with its retrieval context",
        )
        work = Path(result["work_directory"])
        (work / "app.py").write_bytes(b"value = 2\n")
        path = Path(result["metadata_review"])
        review = json.loads(path.read_text(encoding="utf-8"))
        for entry in review["entries"]:
            entry.update(
                metadata_impact="none",
                reason="The generated source blueprint claims exact bytes only; semantic behavior is reviewed in the context document.",
                reviewer="context-integration-test", reviewed_at="2026-10-22T00:00:00Z",
            )
        path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        return result

    def review_document(self, checkout: dict, document: str, *, impact: str = "updated",
                        fields: list[str] | None = None) -> None:
        path = Path(checkout["document_review"])
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(review["schema"], "runtime-sync-context-review/v1")
        selected = [entry for entry in review["entries"] if entry["document"] == document]
        self.assertEqual(len(selected), 1)
        selected[0].update(
            metadata_impact=impact,
            reason="Reviewed behavior, answer targets, references, claim boundary and sections against the application change.",
            reviewer="context-integration-test", reviewed_at="2026-10-22T00:00:00Z",
            reviewed_fields=list(REVIEW_FIELDS if fields is None else fields),
        )
        path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")

    def stage_behavior(self, checkout: dict) -> Path:
        work_doc = Path(checkout["work_directory"]) / "documents" / BEHAVIOR
        work_doc.write_bytes(self.behavior_document(updated=True).encode("utf-8"))
        self.review_document(checkout, BEHAVIOR)
        return work_doc

    def prepare_and_verify(self, checkout: dict) -> None:
        transaction_id = checkout["transaction_id"]
        complete_metadata_audit(self.engine, checkout)
        self.assertEqual(self.engine.prepare(transaction_id)["state"], "PREPARED")
        self.engine.metadata_check(transaction_id)
        self.assertEqual(self.engine.verify(transaction_id)["state"], "SHADOW_VERIFIED")

    def test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        original = (self.workspace / BEHAVIOR).read_bytes()
        self.prepare_and_verify(checkout)
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), original)
        receipt = self.engine.apply(checkout["transaction_id"])
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(receipt["bit_exact"])
        self.assertIn("APP_BEHAVIOR", receipt["heartbeat"]["promoted"])
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        metadata = split_frontmatter(work_doc.read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["revision"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT revision,capsule,claim_boundary FROM artifacts WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchone()
            self.assertEqual(row, (2, metadata["capsule"], metadata["claim_boundary"]))
            self.assertEqual(connection.execute(
                "SELECT section_id,question FROM query_handles WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchall(), [("s-behavior", "How does freshcounter behave?")])
            self.assertEqual(connection.execute(
                "SELECT target_path,target_section_id,version FROM relations WHERE source_artifact_id='APP_BEHAVIOR'",
            ).fetchall(), [(REFERENCE, "s-current", "1")])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM section_fts WHERE section_fts MATCH 'legacycounter' AND artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM section_fts WHERE section_fts MATCH 'freshcounter' AND artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifact_revisions WHERE artifact_id='APP_BEHAVIOR' ORDER BY revision",
            ).fetchall(), [(1,), (2,)])
        found = search_database(KnowledgeDatabase(self.database), "How does freshcounter behave?")
        self.assertEqual(found["primary"]["artifact_id"], "APP_BEHAVIOR")
        self.assertEqual(found["primary"]["section_id"], "s-behavior")
        self.assertFalse(self.engine.lease_path.exists())

    def test_every_selected_context_document_requires_complete_consistent_review(self) -> None:
        checkout = self.checkout((BEHAVIOR, REFERENCE))
        self.stage_behavior(checkout)
        self.review_document(checkout, BEHAVIOR, fields=[field for field in REVIEW_FIELDS if field != "refs"])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, BEHAVIOR)
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, REFERENCE, impact="none")
        self.review_document(checkout, BEHAVIOR, impact="none")
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, BEHAVIOR)
        complete_metadata_audit(self.engine, checkout)
        self.assertEqual(self.engine.prepare(checkout["transaction_id"])["state"], "PREPARED")
        self.assertEqual(split_frontmatter(
            (Path(checkout["work_directory"]) / "documents" / REFERENCE).read_text(encoding="utf-8"),
        ).metadata["revision"], 1)
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_document_edit_after_prepare_requires_new_preparation(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        complete_metadata_audit(self.engine, checkout)
        self.engine.prepare(checkout["transaction_id"])
        self.engine.metadata_check(checkout["transaction_id"])
        work_doc.write_bytes(work_doc.read_bytes().replace(b"value = 2", b"value = 3"))
        with self.assertRaises(WorkshopError) as raised:
            self.engine.verify(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "WORK_CHANGED_AFTER_PREPARE")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_document_and_review_edits_after_verify_cannot_be_applied(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        reviewed_bytes = work_doc.read_bytes()
        review_path = Path(checkout["document_review"])
        original_review = review_path.read_bytes()
        source_review_path = Path(checkout["metadata_review"])
        original_source_review = source_review_path.read_bytes()
        for changed_path, changed_bytes in (
            (work_doc, reviewed_bytes.replace(b"value = 2", b"value = 3")),
            (review_path, original_review.replace(b"context-integration-test", b"other-reviewer")),
            (source_review_path, original_source_review.replace(b"context-integration-test", b"other-reviewer")),
        ):
            with self.subTest(path=changed_path.name):
                changed_path.write_bytes(changed_bytes)
                with self.assertRaises(WorkshopError) as raised:
                    self.engine.apply(checkout["transaction_id"])
                self.assertEqual(raised.exception.code, "WORK_CHANGED_AFTER_VERIFY")
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
                work_doc.write_bytes(reviewed_bytes)
                review_path.write_bytes(original_review)
                source_review_path.write_bytes(original_source_review)

    def test_live_document_drift_is_detected_before_apply(self) -> None:
        checkout = self.checkout()
        self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        live_doc = self.workspace / BEHAVIOR
        original = live_doc.read_bytes()
        # Deliberately simulate an out-of-band writer; the transaction must refuse it.
        live_doc.write_bytes(original.replace(b"value = 1", b"value = 9"))
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_BASELINE_CHANGED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertNotEqual(live_doc.read_bytes(), original)

    def test_including_a_document_preserves_edits_and_invalidates_verification(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        reviewed_bytes = work_doc.read_bytes()
        self.assertIn("metadata_check", self.engine.transaction_status(checkout["transaction_id"]))
        expanded = self.engine.include_documents(
            checkout["transaction_id"], [REFERENCE],
            reason="The application contract also needs a scoped impact review",
        )
        self.assertEqual(set(expanded["documents"]), {BEHAVIOR, REFERENCE})
        self.assertEqual(work_doc.read_bytes(), reviewed_bytes)
        state = self.engine.transaction_status(checkout["transaction_id"])
        self.assertEqual(state["state"], "CHECKED_OUT")
        self.assertNotIn("metadata_check", state)
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "TRANSACTION_STATE_INVALID")
        baseline = Path(expanded["work_directory"]).parent / "baseline" / "documents" / REFERENCE
        self.assertEqual(baseline.read_bytes(), (self.workspace / REFERENCE).read_bytes())
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(expanded, REFERENCE, impact="none")
        self.prepare_and_verify(checkout)
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(split_frontmatter((self.workspace / BEHAVIOR).read_text(encoding="utf-8")).metadata["revision"], 2)

    def test_invalid_answer_target_is_rejected_in_shadow(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        work_doc.write_bytes(work_doc.read_bytes().replace(b'target = "s-behavior"', b'target = "s-missing"'))
        complete_metadata_audit(self.engine, checkout)
        self.engine.prepare(checkout["transaction_id"])
        self.engine.metadata_check(checkout["transaction_id"])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.verify(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "SHADOW_PROMOTION_FAILED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertIn("legacycounter", (self.workspace / BEHAVIOR).read_text(encoding="utf-8"))

    def test_context_scope_rejects_escape_generated_and_unmapped_documents(self) -> None:
        blueprint = next((self.workspace / "code").glob("*.md")).relative_to(self.workspace).as_posix()
        for document in ("../outside.md", str(self.root / "outside.md"), "NEURAL_CORTEX.md", blueprint, "docs/MISSING.md"):
            with self.subTest(document=document):
                with self.assertRaises(WorkshopError) as raised:
                    self.engine.checkout(["app.py"], documents=[document], purpose="Reject an invalid context scope before writing")
                self.assertEqual(raised.exception.code, "CONTEXT_DOCUMENT_INVALID")
                self.assertFalse(self.engine.lease_path.exists())

    def test_failed_heartbeat_retains_complete_package_for_recovery(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        with patch("kairos.heartbeat.run_heartbeat", side_effect=RuntimeError("injected heartbeat interruption")):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
        self.assertTrue(self.engine.lease_path.exists())
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "RECOVERY_REQUIRED")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
        baseline = json.loads((Path(checkout["work_directory"]).parent / "baseline" / "manifest.json").read_text(encoding="utf-8"))
        blueprint = baseline["records"]["app.py"]["blueprint"]["filename"]
        receipt = run_heartbeat(
            self.workspace, requested_mode="verify", changed_paths=[BEHAVIOR, f"code/{blueprint}"],
            use_reconciliation=True, trigger="context-transaction-test-recovery",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.engine.recover(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertFalse(self.engine.lease_path.exists())

    def test_context_copy_failure_restores_code_blueprints_and_markdown(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        live_doc = self.workspace / BEHAVIOR
        original_paths = [self.project / "app.py", live_doc, *(self.workspace / "code").glob("*.md")]
        originals = {path: path.read_bytes() for path in original_paths}
        interrupted = False

        def interrupted_copy(source: Path, destination: Path) -> None:
            nonlocal interrupted
            copy_exact(source, destination)
            if source == work_doc and destination == live_doc and not interrupted:
                interrupted = True
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
                self.assertEqual(live_doc.read_bytes(), work_doc.read_bytes())
                raise OSError("injected interruption after context copy and before heartbeat")

        # The public guarded Engine now wraps the synchronization core; inject into
        # the actual core copy boundary, not the public admission wrapper.
        core_apply = super(type(self.engine), self.engine).apply
        with patch.dict(core_apply.__func__.__globals__, {"copy_exact": interrupted_copy}):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertTrue(interrupted)
        self.assertEqual(raised.exception.code, "APPLY_ROLLED_BACK")
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "ROLLED_BACK")
        self.assertFalse(self.engine.lease_path.exists())
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original, str(path))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifacts WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit' AND status='ACTIVE'",
            ).fetchone()[0], 0)
        self.assertTrue(self.engine.status(persist=False)["verified"])

    def test_repreparing_verified_context_keeps_one_revision_and_renews_verification(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        first_package = self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"]
        work_doc.write_bytes(work_doc.read_bytes().replace(b"freshcounter", b"revisedcounter"))
        self.review_document(checkout, BEHAVIOR, fields=[])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        # A failed attempt to prepare the new candidate must not retain the old
        # SHADOW_VERIFIED authorization.
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "TRANSACTION_STATE_INVALID")
        self.review_document(checkout, BEHAVIOR)
        self.prepare_and_verify(checkout)
        second_package = self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"]
        self.assertNotEqual(first_package, second_package)
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        self.assertIn("revisedcounter", work_doc.read_text(encoding="utf-8"))
        self.assertEqual(split_frontmatter(work_doc.read_text(encoding="utf-8")).metadata["revision"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifact_revisions WHERE artifact_id='APP_BEHAVIOR' ORDER BY revision",
            ).fetchall(), [(1,), (2,)])

    def test_context_only_apply_preserves_prior_mirror_and_binds_new_package(self) -> None:
        prior_seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        prior_mirror_path = Path(prior_seal["mirror_manifest"])
        prior_mirror_bytes = prior_mirror_path.read_bytes()
        checkout = self.checkout()
        (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 1\n")
        work_doc = self.stage_behavior(checkout)
        prepared = self.engine.prepare(checkout["transaction_id"])
        self.assertFalse(prepared["metadata_aftercheck"]["required"])
        audit = Path(checkout["work_directory"]).parent / "review" / "metadata_audit.json"
        self.assertFalse(audit.exists())
        self.assertEqual(self.engine.verify(checkout["transaction_id"])["state"], "SHADOW_VERIFIED")
        self.assertNotIn("metadata_check", self.engine.transaction_status(checkout["transaction_id"]))
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        mirror_path = Path(seal["mirror_manifest"])
        mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
        self.assertEqual(prior_seal["package_sha256"], seal["package_sha256"])
        self.assertEqual(prior_mirror_path.read_bytes(), prior_mirror_bytes)
        self.assertNotEqual(mirror_path, prior_mirror_path)
        self.assertEqual(mirror_path.name, f"{seal['mirror_sha256']}.json")
        self.assertEqual(mirror["mirror_sha256"], seal["mirror_sha256"])
        self.assertEqual(mirror["objects"][f"context:{BEHAVIOR}"], hashlib.sha256(work_doc.read_bytes()).hexdigest())
        self.assertEqual(seal["work_package_sha256"], self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"])

    def test_context_only_recovery_does_not_mistake_unchanged_code_for_rollback(self) -> None:
        checkout = self.checkout()
        (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 1\n")
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        with patch("kairos.heartbeat.run_heartbeat", side_effect=RuntimeError("injected context promotion interruption")):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        # The original code corpus hash is unchanged, but the context source is
        # already the candidate and its projection is stale. This is not a rollback.
        with self.assertRaises(WorkshopError):
            self.engine.recover(checkout["transaction_id"])
        self.assertTrue(self.engine.lease_path.exists())
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "RECOVERY_REQUIRED")
        receipt = run_heartbeat(
            self.workspace, requested_mode="verify", changed_paths=[BEHAVIOR],
            use_reconciliation=True, trigger="context-only-transaction-test-recovery",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.engine.recover(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertFalse(self.engine.lease_path.exists())


if __name__ == "__main__":
    unittest.main()
