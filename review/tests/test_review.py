from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "review"))
import jev_transport as transport
import jev_workshop_attempt_gate_typesafe as gate
import jev_workshop_attempt_workflow as workflow
import jev_adaptive_patch_tree as tree


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_transport_sends_actual_wire_contract_to_direct_adapter(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"answers":{"P":{"type":"noul","noul":0.93}}}'
        opener = Mock()
        opener.open.return_value = response
        state = {"transaction": {"transaction_id": "TXN_fixture"}, "evidence": "exact changed bytes"}
        questions = {"P": {"type": "noul", "instructions": "Does evidence support P?"}}
        with patch.object(transport, "resolve_api_key", return_value="synthetic-test-token"), patch.object(transport, "DIRECT_TYPESAFE_OPENER", opener):
            result = transport.call_jev(state, questions, timeout=3)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, transport.DEFAULT_API_URL)
        self.assertEqual(json.loads(request.data), {"model": transport.DEFAULT_MODEL, "state": state, "questions": questions})
        self.assertEqual(result["answers"]["P"]["noul"], 0.93)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3)

    def test_attempt_entry_point_reaches_adapter_with_actual_candidate_context(self):
        live = self.root / "live" / "runtime" / "example.py"
        candidate = self.root / "transactions" / "TXN_fixture" / "work" / "runtime" / "example.py"
        live.parent.mkdir(parents=True)
        candidate.parent.mkdir(parents=True)
        live.write_text("value = 1\n")
        candidate.write_text("value = 2\n")
        (self.root / "workshop.py").write_text("# fixture entry point\n")
        config = self.root / "config.json"
        config.write_text(json.dumps({"codebase_root": str(live.parents[1]), "kairos_workspace": str(self.root / "workspace")}))
        profile = self.root / "questions.json"
        profile.write_text(json.dumps({"P": {"type": "noul", "instructions": "Does the supplied change preserve validation?"}}))
        args = ["gate", "TXN_fixture", "--workshop-root", str(self.root), "--config", str(config)]
        with patch.dict(os.environ, {"JEV_QUESTIONS_FILE": str(profile)}), patch.object(sys, "argv", args), patch.object(gate, "load_transaction_state", return_value={"state": "CHECKED_OUT", "purpose": "preserve validation"}), patch.object(gate, "direct_call_jev", return_value={"answers": {"P": {"type": "noul", "noul": .94}}}) as adapter, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main(), 0)
        state, questions = adapter.call_args.args
        self.assertEqual(state["transaction"]["transaction_id"], "TXN_fixture")
        self.assertEqual(state["system_materialized_code_changes"][0]["regions"][0]["after"], "value = 2\n")
        self.assertEqual(set(questions), {"P"})

    def test_transport_network_failure_does_not_return_approval(self):
        with patch.object(transport, "resolve_api_key", return_value="synthetic-test-token"), patch.object(transport.DIRECT_TYPESAFE_OPENER, "open", side_effect=URLError("unreachable")), patch.dict(os.environ, {"JEV_RETRY_ATTEMPTS": "1"}):
            with self.assertRaises(transport.JevTransportError):
                transport.call_jev({}, {}, timeout=1)

    def test_transport_missing_credential_never_contacts_provider(self):
        with patch.object(transport, "read_windows_credential", return_value=None), patch.object(transport.DIRECT_TYPESAFE_OPENER, "open") as opener:
            with self.assertRaises(transport.JevTransportError):
                transport.call_jev({}, {}, timeout=1)
            opener.assert_not_called()

    def test_explicit_question_profile_is_required_and_checked(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(gate.GateError):
                gate.questions()
        profile = self.root / "questions.json"
        with patch.dict(os.environ, {"JEV_QUESTIONS_FILE": str(profile)}):
            for value in ({}, {"P": {"type": "text", "instructions": "P"}}, {"P": {"type": "noul", "instructions": ""}}):
                profile.write_text(json.dumps(value))
                with self.assertRaises(gate.GateError):
                    gate.questions()
            expected = {"P": {"type": "noul", "instructions": "Does evidence support P?"}}
            profile.write_text(json.dumps(expected))
            self.assertEqual(gate.questions(), expected)

    def test_transaction_state_uses_same_explicit_config_as_review(self):
        config = self.root / "workshop.config.json"
        with patch.object(gate, "run_json", return_value={"state": "CHECKED_OUT"}) as run:
            gate.load_transaction_state(self.root, "TXN_fixture", config)
        self.assertEqual(run.call_args.args[0], [sys.executable, "workshop.py", "--config", str(config), "transaction", "TXN_fixture"])

    def test_materialized_diff_contains_actual_before_and_after(self):
        live = self.root / "live" / "runtime" / "example.py"
        candidate = self.root / "transactions" / "TXN_fixture" / "work" / "runtime" / "example.py"
        live.parent.mkdir(parents=True)
        candidate.parent.mkdir(parents=True)
        live.write_text("value = 1\n")
        candidate.write_text("value = 2\n")
        result = gate.materialize_changes(candidate.parents[2], {"codebase_root": str(live.parents[1])}, 0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "runtime/example.py")
        self.assertEqual(result[0]["regions"][0]["before"], "value = 1\n")
        self.assertEqual(result[0]["regions"][0]["after"], "value = 2\n")

    def test_candidate_fingerprint_changes_for_candidate_B(self):
        candidate = self.root / "transactions" / "TXN_fixture" / "work" / "runtime" / "example.py"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("candidate A")
        a = workflow.candidate_fingerprint(self.root, "TXN_fixture")
        candidate.write_text("candidate B")
        b = workflow.candidate_fingerprint(self.root, "TXN_fixture")
        self.assertNotEqual(a["sha256"], b["sha256"])

    def test_missing_and_rejected_scores_block_workflow_vector(self):
        for scores in ({}, {"P": .89, "bad": .01}, {"P": .99, "bad": .2}):
            self.assertFalse(workflow.evaluate_attempt_vector(scores, positive_questions=("P",), negative_questions=("bad",), threshold=.90)["passed"])
        self.assertTrue(workflow.evaluate_attempt_vector({"P": .90, "bad": .09}, positive_questions=("P",), negative_questions=("bad",), threshold=.90)["passed"])

    def test_two_lanes_share_context_and_evidence_cache_invalidates(self):
        node = tree.QuestionNode("P", None, 0, "proposition", "Does the supplied candidate satisfy its contract?")
        cache = {}
        with patch.object(tree, "call_jev", return_value={"answers": {"P": {"noul": .94}, "P_E": {"noul": .91}}}) as call:
            support, sufficient = tree.ask_nodes_and_evidence([node], {"evidence": "A"}, score_cache=cache)
            self.assertEqual((support["P"], sufficient["P"]), (.94, .91))
            self.assertEqual(set(call.call_args.args[1]), {"P", "P_E"})
            tree.ask_nodes_and_evidence([node], {"evidence": "A"}, score_cache=cache)
            self.assertEqual(call.call_count, 1)
            tree.ask_nodes_and_evidence([node], {"evidence": "B"}, score_cache=cache)
            self.assertEqual(call.call_count, 2)

    def test_missing_tree_answer_raises_instead_of_approval(self):
        node = tree.QuestionNode("P", None, 0, "proposition", "P?")
        with patch.object(tree, "call_jev", return_value={"answers": {}}):
            with self.assertRaises(tree.TreeError):
                tree.ask_nodes_and_evidence([node], {}, score_cache={})

    def test_characterization_supplied_review_has_no_candidate_binding(self):
        # Existing local gap: --attempt-review checks transaction ID but accepts
        # an old result for changed candidate bytes. It is never Apply authority.
        candidate = self.root / "transactions" / "TXN_fixture" / "work" / "runtime" / "example.py"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("candidate B")
        review = self.root / "review.json"
        review.write_text(json.dumps({"transaction_id": "TXN_fixture", "scores": {"P": .99, "bad": .01}, "candidate": "A"}))
        output = self.root / "workflow.json"
        args = ["workflow", "TXN_fixture", "--workshop-root", str(self.root), "--config", str(self.root / "config.json"), "--attempt-review", str(review), "--out", str(output), "--require-positive", "P", "--reject-positive", "bad"]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(workflow.main(), 0)
        result = json.loads(output.read_text())
        self.assertEqual(result["decision"], "APPROVED_FOR_NEXT_ACTION")
        self.assertFalse(result["live_apply_authorized"])

    def test_characterization_response_scores_are_not_strictly_validated(self):
        # Preserve and expose the deployed behavior; do not claim schema validation.
        self.assertEqual(workflow.review_scores({"scores": {"P": 99, "Q": True}}), {"P": 1.0, "Q": 1.0})


if __name__ == "__main__":
    unittest.main()
