from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "workshop" / "src", ROOT / "kairos" / "kairos_harness"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from workshop.tests.test_context_transactions import ContextTransactionTests
from kairos.frontmatter import split_frontmatter
from kairos.transaction_document import document_id, document_path


class TransactionDocumentIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = ContextTransactionTests("test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.engine.config.raw["transaction_documents_required"] = True
        return fixture

    def test_checkout_and_abort_are_both_projected(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        path = fixture.workspace / document_path(tx)
        self.assertTrue(path.is_file())
        header = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(header["type"], "transaction")
        self.assertEqual(header["workshop_state"], "CHECKED_OUT")
        self.assertEqual(header["revision"], 1)
        with sqlite3.connect(fixture.database) as con:
            row = con.execute("SELECT revision,document_type FROM artifacts WHERE artifact_id=?",
                              (document_id(tx),)).fetchone()
            self.assertEqual(row, (1, "transaction"))
        result = fixture.engine.abort(tx)
        self.assertEqual(result["state"], "ABORTED")
        revised = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["workshop_state"], "ABORTED")
        with sqlite3.connect(fixture.database) as con:
            row = con.execute("SELECT revision,state FROM artifacts WHERE artifact_id=?",
                              (document_id(tx),)).fetchone()
            self.assertEqual(row, (2, "failed"))

    def test_declared_finding_is_projected_without_fake_validation(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        note = fixture.engine.annotate_transaction(
            tx, kind="failure",
            summary="Parser experiment still requires further direct evidence",
            evidence="docs/APP_BEHAVIOR.md#s-behavior",
        )
        self.assertTrue(note["documented"])
        path = fixture.workspace / document_path(tx)
        fm = split_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm.metadata["revision"], 3)
        self.assertIn("DECLARED_NOT_INDEPENDENTLY_VERIFIED", fm.body)

    @staticmethod
    def completed_analysis(fixture, tx, *, disposition="REJECT_FOR_REUSE"):
        result = fixture.engine.inspect_recycling(tx)
        path = Path(result["proposal_path"])
        proposal = json.loads(path.read_text(encoding="utf-8"))
        proposal["planned_approach"] = "Correct the parser branch that mishandles the declared application value"
        proposal["attempted_approach"] = "Changed app.py in the isolated Workshop worktree and compared it to the baseline"
        proposal["outcome_reason"] = "The attempted change was not validated for deployment and cannot be claimed as a fix"
        proposal["no_changes_reason"] = "The checkout produced no changed bytes; only an unrealized plan existed"
        for row in proposal["decisions"]:
            row["disposition"] = disposition
            row["reason"] = (
                "The candidate file changed but no verified test proves this approach is reusable; "
                "preserve the audit evidence and stop pursuing the current unverified patch."
            )
            row["evidence_refs"] = ["TX:state.json"]
        path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        return path, result

    def test_rejected_code_and_reasons_are_in_markdown_and_sql(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        analysis_path, inventory = self.completed_analysis(fixture, tx)
        self.assertGreaterEqual(inventory["changed_unit_count"], 1)
        review = fixture.engine.submit_recycling(tx, analysis_path)
        self.assertEqual(review["status"], "REVIEWED")
        path = fixture.workspace / document_path(tx)
        body = split_frontmatter(path.read_text(encoding="utf-8")).body
        self.assertIn("app.py", body)
        self.assertIn("Correct the parser branch", body)
        self.assertIn("stop pursuing the current unverified patch", body)
        with sqlite3.connect(fixture.database) as con:
            atoms = con.execute(
                "SELECT path,disposition,reason,evidence_json FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchall()
            row = con.execute(
                "SELECT analysis_sha256,decision_count FROM tx_recycling_reviews WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchone()
        self.assertEqual(row[0], review["analysis_sha256"])
        self.assertEqual(row[1], len(atoms))
        self.assertTrue(any(row[0] == "app.py" and row[1] == "REJECT_FOR_REUSE" for row in atoms))
        for row in atoms:
            evidence = json.loads(row[3])
            self.assertTrue(evidence)
            self.assertEqual(evidence[0]["verification_kind"], "EXISTENCE_AND_HASH_ONLY")
            self.assertEqual(len(evidence[0]["content_sha256"]), 64)

    def test_verified_unshipped_patch_cannot_be_classified_as_disposable(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        checkout = fixture.checkout()
        fixture.stage_behavior(checkout)
        tx = checkout["transaction_id"]
        fixture.prepare_and_verify(checkout)
        fixture.engine.abort(tx)
        path, inv = self.completed_analysis(fixture, tx)
        proposal = json.loads(path.read_text(encoding="utf-8"))
        if not proposal["decisions"]:
            self.skipTest("fixture had no changed bytes to classify")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.submit_recycling(tx, path)
        self.assertEqual(caught.exception.code, "TX_RECYCLING_PROTECTED_CANDIDATE")
        for row in proposal["decisions"]:
            row["disposition"] = "RETAIN_FOR_REUSE"
            row["reason"] = "Previously shadow-verified code must remain available for reuse until actual deployment is established"
        path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        receipt = fixture.engine.submit_recycling(tx, path)
        self.assertEqual(receipt["status"], "REVIEWED")

    def test_terminal_compaction_updates_persistent_report(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        analysis_path, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis_path)
        fixture.engine.config.raw["tx_retention_required"] = True
        result = fixture.engine.compact_transaction(tx)
        self.assertGreater(result["reclaimed_worktree_bytes"], 0)
        self.assertFalse((fixture.engine.config.transaction_directory / tx / "work").exists())
        path = fixture.workspace / document_path(tx)
        fm = split_frontmatter(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(fm.metadata["revision"], 4)
        self.assertIn("manifest_sha256", fm.body)
        with sqlite3.connect(fixture.database) as con:
            count = con.execute(
                "SELECT count(*) FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchone()[0]
        self.assertGreater(count, 0)

    def test_ninth_checkout_announces_recycling_before_tenth_is_requested(self):
        fixture = self.fixture()
        fixture.engine.config.raw.update({
            "transaction_documents_required": True,
            "tx_retention_required": True,
            "tx_max_materialized": 10,
            "tx_recycle_trigger": 9,
        })
        for i in range(8):
            txid = "TXN_" + format(i, "x") * 24
            root = fixture.engine.config.transaction_directory / txid
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": txid, "state": "ABORTED",
                            "purpose": "Historical parser investigation awaiting a recycling analysis"}),
                encoding="utf-8",
            )
        checkout = fixture.checkout()
        self.assertTrue(checkout["tx_recycling"]["required"])
        self.assertEqual(checkout["tx_recycling"]["materialized"], 9)
        self.assertEqual(checkout["tx_recycling"]["target_max"], 8)
        self.assertTrue(checkout["tx_recycling"]["loop_heartbeat"]["verified"])
        current = json.loads((fixture.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")

    def test_recycling_frontier_routes_the_agent_but_engine_owns_gate(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        fixture.engine.config.raw.update({
            "transaction_documents_required": True,
            "tx_retention_required": True,
            "tx_max_materialized": 10,
            "tx_recycle_trigger": 9,
        })
        from pathlib import Path
        for i in range(9):
            tx = "TXN_" + format(i, "x") * 24
            root = fixture.engine.config.transaction_directory / tx
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": tx, "state": "ABORTED",
                            "purpose": "Legacy attempt requiring recycling review"}),
                encoding="utf-8",
            )
        status = fixture.engine.transaction_pool_status(refresh_loop=True)
        self.assertTrue(status["required"])
        self.assertTrue(status["loop_heartbeat"]["verified"], status["loop_heartbeat"])
        current = json.loads((fixture.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")
        gate = (fixture.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8")
        self.assertIn("s-tx-recycling", gate)
        self.assertIn("Workshop checkout is blocked", gate)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.checkout(
                ["app.py"], purpose="Try another independent source change without recycling",
            )
        self.assertEqual(caught.exception.code, "TX_RECYCLING_REQUIRED")

    def test_kairos_status_separates_live_pool_from_stale_cached_frontier(self):
        from kairos.heartbeat import status_report
        fixture = self.fixture()
        canonical = fixture.workspace / ".kairos" / "workshop.config.json"
        raw = json.loads(canonical.read_text(encoding="utf-8"))
        raw["tx_retention_required"] = True
        raw["transaction_documents_required"] = True
        raw["tx_recycle_trigger"] = 9
        canonical.write_text(json.dumps(raw, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        fixture.engine.config.raw.update(raw)
        for i in range(9):
            tx = "TXN_" + format(i, "024x")
            root = fixture.engine.config.transaction_directory / tx
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": tx, "state": "ABORTED",
                            "purpose": "Older code change requiring an explicit recycling analysis"}),
                encoding="utf-8",
            )
        # No Workshop status refresh has occurred yet; KAIROS must not trust
        # the old current.json as an accurate new-transaction admission.
        stale = status_report(fixture.workspace)
        self.assertTrue(stale["tx_recycling_live"]["required"])
        self.assertTrue(stale["tx_recycling_live"]["reconciliation_required"])
        self.assertEqual(stale["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")
        fixture.engine.transaction_pool_status(refresh_loop=True)
        fresh = status_report(fixture.workspace)
        self.assertTrue(fresh["tx_recycling_live"]["required"])
        self.assertFalse(fresh["tx_recycling_live"]["reconciliation_required"])

    def test_apply_projects_terminal_outcome_after_code_heartbeat(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        fixture.stage_behavior(checkout)
        fixture.prepare_and_verify(checkout)
        try:
            receipt = fixture.engine.apply(checkout["transaction_id"])
        except Exception as exc:
            marker = fixture.engine.config.state_directory / "transaction-document-pending" / (checkout["transaction_id"] + ".json")
            details = marker.read_text(encoding="utf-8") if marker.is_file() else "no pending marker"
            self.fail(f"apply/document integration failed: {exc}; pending={details}")
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        path = fixture.workspace / document_path(checkout["transaction_id"])
        header = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(header["workshop_state"], "POSTCHECK_VERIFIED")
        self.assertEqual(header["state"], "completed")
        with sqlite3.connect(fixture.database) as con:
            row = con.execute("SELECT state FROM artifacts WHERE artifact_id=?",
                              (document_id(checkout["transaction_id"]),)).fetchone()
            self.assertEqual(row, ("completed",))

if __name__ == "__main__":
    unittest.main()
