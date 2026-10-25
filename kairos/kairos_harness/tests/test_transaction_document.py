from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "kairos" / "kairos_harness"))

from kairos.frontmatter import split_frontmatter
from kairos.sections import parse_sections, validate_answer_targets
from kairos.transaction_document import (
    SECTION_SPECS, document_id, document_path, render_transaction_document,
)


class TransactionDocumentTests(unittest.TestCase):
    def test_machine_json_is_literal_bounded_data_without_live_external_references(self):
        from kairos.markdown import mask_markdown_code
        from kairos.health import ABSOLUTE_FILE_REFERENCE
        from kairos.transaction_document import _json
        evidence = {"candidate": r"Q:\isolated\candidate", "delimiter": "``````", "reference": "[ref:missing.md#s-no|id:MISSING|v:1|rel:uses|tags:test|src:fixture]"}
        encoded = _json(evidence)
        self.assertIn(r"Q:\\isolated\\candidate", encoded)
        self.assertFalse(ABSOLUTE_FILE_REFERENCE.search(mask_markdown_code(encoded)))
        self.assertNotIn("[ref:missing", mask_markdown_code(encoded))
        self.assertTrue(encoded.startswith("\n```````json\n"))
        truncated = _json({"payload": "x" * 200}, limit=20)
        self.assertIn("TRUNCATED", truncated)
        self.assertTrue(truncated.startswith("\n```text\n"))
        self.assertLess(len(truncated), 120)
        tx = {"transaction_id": "TXN_" + "e" * 24, "state": "CHECKED_OUT", "purpose": "Inspect bounded literal machine evidence", "journal": [{"event": "CHECKOUT_COMPLETED", "details": evidence}]}
        document = render_transaction_document(transaction=tx, workspace_id="KAIROS_TEST", goal_id="GOAL_KAIROS_001", milestone_id="MILESTONE_KAIROS_01", task_id="TASK_0001", loop=1)
        frontmatter = split_frontmatter(document)
        sections = parse_sections(frontmatter.body, header_bytes=frontmatter.header_bytes)
        self.assertEqual([part.section_id for part in sections], [row[0] for row in SECTION_SPECS])
        self.assertFalse(ABSOLUTE_FILE_REFERENCE.search(mask_markdown_code(document)))
        validate_answer_targets(frontmatter.metadata, sections)

    def test_explicit_historical_binding_is_not_described_as_a_new_checkout_observation(self):
        tx = {"transaction_id": "TXN_" + "d" * 24, "state": "ABORTED",
              "purpose": "Retain original historical evidence without inventing a checkout observation",
              "historical_scope": {"operation": "BIND", "scope_status": "BOUND", "receipt_sha256": "c" * 64}}
        document = render_transaction_document(
            transaction=tx, workspace_id="KAIROS_TEST", goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01", task_id="TASK_0001", loop=3)
        frontmatter = split_frontmatter(document)
        sections = parse_sections(frontmatter.body, header_bytes=frontmatter.header_bytes)
        ownership = next(section.body for section in sections if section.section_id == "s-ownership")
        self.assertIn("explicitly receipt-bound", ownership)
        self.assertNotIn("checkout-bound", ownership)
        self.assertEqual(frontmatter.metadata["loop"], 3)
        # Existing records without an explicit history transition retain their old rendering.
        tx.pop("historical_scope")
        ordinary = render_transaction_document(
            transaction=tx, workspace_id="KAIROS_TEST", goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01", task_id="TASK_0001", loop=3)
        self.assertIn("Historical scope is checkout-bound.", ordinary)

    def test_complete_method_grammar_and_scope(self):
        tx = {"transaction_id": "TXN_" + "a" * 24, "state": "CHECKED_OUT",
              "purpose": "Repair parser with bounded scope",
              "sources": ["runtime/parser.cpp"], "baseline_package_sha256": "b" * 64,
              "journal": [{"event": "CHECKOUT_COMPLETED"}]}
        rendered = render_transaction_document(
            transaction=tx, workspace_id="KAIROS_TEST",
            goal_id="GOAL_KAIROS_001", milestone_id="MILESTONE_KAIROS_01",
            task_id="TASK_0001", loop=1,
        )
        fm = split_frontmatter(rendered)
        sections = parse_sections(fm.body, header_bytes=fm.header_bytes)
        validate_answer_targets(fm.metadata, sections)
        self.assertEqual(fm.metadata["type"], "transaction")
        self.assertEqual(fm.metadata["state"], "partial")
        self.assertEqual([section.section_id for section in sections], [row[0] for row in SECTION_SPECS])
        self.assertEqual(len(fm.metadata["search_contract"]), 2)
        self.assertEqual(len(fm.metadata["answers"]), 6)
        self.assertEqual(document_path(tx["transaction_id"]), "reports/" + document_id(tx["transaction_id"]) + ".md")

    def test_legacy_transaction_never_borrows_current_task_scope(self):
        tx = {"transaction_id": "TXN_" + "c" * 24, "state": "ABORTED",
              "purpose": "Historical attempt without a trustworthy original scope"}
        document = render_transaction_document(
            transaction=tx, workspace_id="KAIROS_TEST",
            goal_id=None, milestone_id=None, task_id=None, loop=None,
        )
        meta = split_frontmatter(document).metadata
        self.assertEqual(meta["scope_status"], "SCOPE_UNRESOLVED")
        self.assertEqual(meta["route"], "HISTORICAL_UNRESOLVED/" + document_id(tx["transaction_id"]))
        for key in ("goal", "milestone", "task", "loop"):
            self.assertNotIn(key, meta)

    def test_postcheck_document_never_claims_production_deployment(self):
        tx = {"transaction_id": "TXN_" + "b" * 24, "state": "POSTCHECK_VERIFIED",
              "purpose": "Repair scoped parser behavior",
              "postcheck_package_sha256": "a" * 64}
        rendered = render_transaction_document(
            transaction=tx, workspace_id="KAIROS_TEST",
            goal_id="GOAL_KAIROS_001", milestone_id="MILESTONE_KAIROS_01",
            task_id="TASK_0001", loop=1,
        )
        metadata = split_frontmatter(rendered).metadata
        self.assertEqual(metadata["state"], "completed")
        self.assertIn("not JEV authenticity", metadata["claim_boundary"])
        self.assertIn("deployment beyond", rendered)

if __name__ == "__main__":
    unittest.main()
