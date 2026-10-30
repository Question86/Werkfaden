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
    def test_declared_paths_references_and_fences_remain_exact_literal_data(self):
        import json
        import re
        from kairos.health import ABSOLUTE_FILE_REFERENCE
        from kairos.markdown import mask_markdown_code
        text = (r"Recorded physical location Q:\isolated\candidate" +
                "\n``````\n[ref:missing.md#s-no|id:MISSING|v:1|rel:uses|tags:test|src:fixture]\n" +
                "[unsafe](file:///outside/evidence)\n## Injected section\n``````")
        decisions = [{"unit_id": "FILE_CHANGE_" + "a" * 32, "path": "code.py",
                      "disposition": "REJECT_FOR_REUSE", "reason": text}]
        tx = {"transaction_id": "TXN_" + "a" * 24, "state": "POSTCHECK_VERIFIED",
              "purpose": text, "recycling": {"status": "REVIEWED",
                  "analysis_sha256": "b" * 64, "inventory_sha256": "c" * 64,
                  "planned_approach": text, "attempted_approach": text,
                  "outcome_reason": text, "decisions": decisions}}
        document = render_transaction_document(transaction=tx, workspace_id="KAIROS_TEST",
            goal_id="GOAL_KAIROS_001", milestone_id="MILESTONE_KAIROS_01", task_id="TASK_0001", loop=1)
        parsed = split_frontmatter(document)
        sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
        self.assertEqual([part.section_id for part in sections], [row[0] for row in SECTION_SPECS])
        validate_answer_targets(parsed.metadata, sections)
        self.assertIn(text, parsed.body)
        masked = mask_markdown_code(document)
        self.assertFalse(ABSOLUTE_FILE_REFERENCE.search(masked))
        self.assertNotIn("[ref:missing", masked)
        self.assertNotIn("Injected section", masked)
        graph = next(part.body for part in sections if part.section_id == "s-graph-metadata")
        payload = json.loads(re.search(r"(?ms)^```json\n(.*?)^```", graph).group(1))
        self.assertEqual(payload["decisions"], decisions)
        self.assertEqual(payload["analysis_sha256"], "b" * 64)

    def test_literal_purpose_caption_keeps_exact_full_body_and_safe_header(self):
        from kairos.health import ABSOLUTE_FILE_REFERENCE
        from kairos.markdown import mask_markdown_code
        for purpose in [r"Inspect D:\external\file.py", "Inspect file:///external/file.py",
                        "Inspect [ref:missing.md#s-no|id:MISSING|v:1|rel:uses|tags:test|src:fixture]"]:
            with self.subTest(purpose=purpose):
                document = render_transaction_document(transaction={
                    "transaction_id": "TXN_" + "b" * 24, "state": "ABORTED", "purpose": purpose},
                    workspace_id="KAIROS_TEST", goal_id=None, milestone_id=None, task_id=None, loop=None)
                parsed = split_frontmatter(document)
                self.assertIn(purpose, parsed.body)
                self.assertNotIn(purpose, parsed.metadata["capsule"])
                self.assertFalse(ABSOLUTE_FILE_REFERENCE.search(mask_markdown_code(document)))

    def test_search_contract_questions_match_exact_answer_handles(self):
        for state in ("CHECKED_OUT", "ABORTED", "POSTCHECK_VERIFIED"):
            with self.subTest(state=state):
                tx = {"transaction_id": "TXN_" + "f" * 24, "state": state,
                      "purpose": "Verify exact transaction retrieval questions"}
                text = render_transaction_document(transaction=tx, workspace_id="KAIROS_TX_TEST",
                    goal_id=None, milestone_id=None, task_id=None, loop=None,
                    task_reference_exists=False)
                metadata = split_frontmatter(text).metadata
                answers = {(row["question"], row["target"]) for row in metadata["answers"]}
                for contract in metadata["search_contract"]:
                    target = contract["expected"].split("#", 1)[1]
                    self.assertIn((contract["query"], target), answers)

    def test_repeated_transaction_vocabulary_promotes_with_exact_contracts(self):
        from kairos.database import KnowledgeDatabase
        from kairos.promoter import promote_document
        from kairos.workspace import initialize_workspace
        with tempfile.TemporaryDirectory(prefix="tx-contract-collision-") as directory:
            workspace = Path(directory) / "workspace"
            initialize_workspace(workspace, workspace_id="KAIROS_TX_COLLISION_TEST")
            database = KnowledgeDatabase(workspace / ".kairos" / "kairos.db")
            database.initialize()
            for index in range(24):
                transaction_id = "TXN_" + format(index, "024x")
                text = render_transaction_document(transaction={
                    "transaction_id": transaction_id,
                    "state": "ABORTED" if index % 2 else "POSTCHECK_VERIFIED",
                    "purpose": "Distinct transaction evidence with repeated generic failure vocabulary",
                }, workspace_id="KAIROS_TX_COLLISION_TEST", goal_id=None,
                    milestone_id=None, task_id=None, loop=None, task_reference_exists=False)
                path = workspace / document_path(transaction_id)
                path.write_text(text, encoding="utf-8")
                with self.subTest(transaction_id=transaction_id):
                    promote_document(path, workspace, database)

    def test_bound_history_with_long_scope_retains_first_window_contract(self):
        from kairos.constants import MAX_HEADER_BYTES
        purpose = "Recover complete verified framework source membership without repeating Apply and preserve native terminal evidence and scope"
        tx = {"transaction_id":"TXN_"+"a"*24,"state":"POSTCHECK_VERIFIED","purpose":purpose,
              "baseline_package_sha256":"b"*64,"verified_work_package_sha256":"c"*64,
              "tx_event_id":"TXE_"+"D"*32,
              "historical_scope":{"operation":"BIND","scope_status":"BOUND","receipt_sha256":"e"*64}}
        scope={"workspace_id":"FRAMEWORK_DEVELOPMENT","goal_id":"GOAL_FRAMEWORK_PRODUCT_READINESS_20261025",
               "milestone_id":"MILESTONE_FRAMEWORK_P0_BASELINE","task_id":"TASK_FRAMEWORK_P0_BOOTSTRAP","loop":1}
        text=render_transaction_document(transaction=tx,**scope,revision=100,task_reference_exists=True)
        parsed=split_frontmatter(text)
        sections=parse_sections(parsed.body,header_bytes=parsed.header_bytes)
        self.assertLessEqual(parsed.header_bytes,MAX_HEADER_BYTES)
        self.assertEqual([s.section_id for s in sections],[s[0] for s in SECTION_SPECS])
        self.assertEqual(len(parsed.metadata["answers"]),6)
        self.assertEqual(len(parsed.metadata["search_contract"]),2)
        self.assertEqual(parsed.metadata["task"],scope["task_id"])
        self.assertEqual(parsed.metadata["tx_event_id"],tx["tx_event_id"])
        self.assertIn(purpose,parsed.body)
        self.assertIn("task",parsed.metadata["refs"])
        self.assertIn("history_method",parsed.metadata["refs"])
        validate_answer_targets(parsed.metadata,sections)

    def test_bound_history_recycling_hash_and_escaped_utf8_purpose_fit_budget(self):
        from kairos.constants import MAX_HEADER_BYTES
        for purpose in ("Complete original purpose "*20, 'ä😀"\n'*80):
            with self.subTest(purpose=purpose[:20]):
                tx={"transaction_id":"TXN_"+"a"*24,"state":"POSTCHECK_VERIFIED","purpose":purpose,
                    "baseline_package_sha256":"b"*64,"verified_work_package_sha256":"c"*64,
                    "tx_event_id":"TXE_"+"D"*32,
                    "historical_scope":{"operation":"BIND","scope_status":"BOUND","receipt_sha256":"e"*64},
                    "recycling":{"status":"REVIEWED","analysis_sha256":"f"*64,"decisions":[]}}
                text=render_transaction_document(transaction=tx,workspace_id="FRAMEWORK_DEVELOPMENT",
                    goal_id="GOAL_FRAMEWORK_PRODUCT_READINESS_20261025",milestone_id="MILESTONE_FRAMEWORK_P0_BASELINE",
                    task_id="TASK_FRAMEWORK_P0_BOOTSTRAP",loop=1,revision=100000,task_reference_exists=True)
                parsed=split_frontmatter(text)
                sections=parse_sections(parsed.body,header_bytes=parsed.header_bytes)
                self.assertLessEqual(parsed.header_bytes,MAX_HEADER_BYTES)
                self.assertEqual(parsed.metadata["recycling_analysis_sha256"],"f"*64)
                self.assertEqual(parsed.metadata["tx_event_id"],tx["tx_event_id"])
                self.assertIn(purpose,parsed.body)
                self.assertEqual(len(parsed.metadata["answers"]),6)
                self.assertEqual(len(parsed.metadata["search_contract"]),2)
                self.assertEqual([s.section_id for s in sections],[s[0] for s in SECTION_SPECS])
                validate_answer_targets(parsed.metadata,sections)

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
