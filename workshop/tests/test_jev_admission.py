from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workshop" / "src"))

from runtime_sync_workshop.jev_admission import candidate_fingerprint, require_candidate_jev_clearance
from runtime_sync_workshop.util import WorkshopError


class JevAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "work").mkdir()
        (self.root / "review").mkdir()
        (self.root / "work" / "example.py").write_text("value = 2\n", encoding="utf-8")
        self.state = {"transaction_id": "TXN_" + "a" * 24,
                      "state": "SHADOW_VERIFIED",
                      "verified_work_package_sha256": "prepared-sha"}
        self.config = SimpleNamespace()
        self.receipt = {
            "schema": "werkfaden-jev-workshop-attempt-workflow/v1",
            "transaction_id": self.state["transaction_id"],
            "decision": "APPROVED_FOR_NEXT_ACTION",
            "implementation_routing_allowed": True,
            "live_apply_authorized": False,
            "attempt_review": {"mode": "executed", "sha256": "a" * 64},
            "candidate_fingerprint": {"sha256": candidate_fingerprint(self.root)},
            "attempt_vector": {
                "passed": True, "threshold": 0.9,
                "positive": {"correctness": {"score": 0.97, "passed": True}},
                "negative": {"unsupported_assumptions": {"score": 0.03, "passed": True}},
            }
        }

    def write(self):
        (self.root / "review" / "jev_workflow.json").write_text(
            json.dumps(self.receipt), encoding="utf-8")

    def check(self):
        with patch("runtime_sync_workshop.jev_admission.transaction_work_manifest",
                   return_value={"package_sha256": "prepared-sha"}):
            return require_candidate_jev_clearance(self.config, self.root, self.state)

    def assert_refused(self, code):
        self.write()
        with self.assertRaises(WorkshopError) as caught:
            self.check()
        self.assertEqual(caught.exception.code, code)

    def test_exact_candidate_is_accepted(self):
        self.write()
        self.assertTrue(self.check()["reviewed"])

    def test_no_receipt_fails_closed(self):
        with self.assertRaises(WorkshopError) as caught:
            self.check()
        self.assertEqual(caught.exception.code, "JEV_RECEIPT_MISSING")

    def test_stale_candidate_rejected(self):
        (self.root / "work" / "example.py").write_text("value = 3\n", encoding="utf-8")
        self.assert_refused("JEV_CANDIDATE_STALE")

    def test_foreign_transaction_rejected(self):
        self.receipt["transaction_id"] = "TXN_" + "b" * 24
        self.assert_refused("JEV_TX_MISMATCH")

    def test_unexecuted_review_rejected(self):
        self.receipt["attempt_review"]["mode"] = "provided"
        self.assert_refused("JEV_REVIEW_UNTRUSTED")

    def test_empty_policy_rejected(self):
        self.receipt["attempt_vector"]["positive"] = {}
        self.assert_refused("JEV_POLICY_EMPTY")

    def test_negative_score_exceeds_threshold(self):
        self.receipt["attempt_vector"]["negative"]["unsupported_assumptions"]["score"] = 0.9
        self.assert_refused("JEV_VECTOR_REJECTED")

    def test_boolean_score_rejected(self):
        self.receipt["attempt_vector"]["positive"]["correctness"]["score"] = True
        self.assert_refused("JEV_SCORE_INVALID")

    def test_nonfinite_score_rejected(self):
        self.receipt["attempt_vector"]["positive"]["correctness"]["score"] = float("nan")
        self.assert_refused("JEV_RECEIPT_INVALID")


if __name__ == "__main__":
    unittest.main()
