from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "workshop/src"):
    sys.path.insert(0, str(path))
from runtime_sync_workshop import cli
from runtime_sync_workshop._engine_core_v1 import load_engine_core
from runtime_sync_workshop.util import WorkshopError
import workshop.tests.test_metadata_gate as metadata_fixture


class ReviewIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = metadata_fixture.MetadataGateTests("test_verify_requires_separate_aftercheck_and_receipt_reaches_apply")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_merged_cli_retains_guardian_and_metadata_commands(self):
        parser = cli._parser()
        args = parser.parse_args(["checkout", "--source", "example.py", "--purpose", "fixture", "--document", "docs/example.md", "--guardian", "GRD2_fixture", "--guardian-state-ticket", "ticket"])
        self.assertEqual(args.document, ["docs/example.md"])
        self.assertEqual(args.guardian, "GRD2_fixture")
        self.assertEqual(parser.parse_args(["metadata-check", "TXN_fixture"]).command, "metadata-check")
        self.assertEqual(parser.parse_args(["guard-status", "GRD2_fixture"]).command, "guard-status")

    def test_core_loader_cannot_be_called_directly(self):
        with self.assertRaises(WorkshopError) as caught:
            load_engine_core()
        self.assertEqual(caught.exception.code, "QUARANTINED_ENGINE_DIRECT_USE")

    def test_guardian_context_documents_fail_closed(self):
        fixture = self.fixture()
        fixture.checkout()
        fixture.engine.abort(fixture.transaction_id)
        with patch.dict(os.environ, {"WERKFADEN_GUARDIAN_REQUIRED": "1"}):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.checkout(["app.py"], purpose="unproven context", documents=["docs/example.md"])
            self.assertEqual(caught.exception.code, "GUARDIAN_CONTEXT_DOCUMENTS_UNSUPPORTED")

    def test_characterization_real_workshop_apply_does_not_require_ai(self):
        # This passing native fixture proves the missing AI boundary, not AI approval.
        # All existing metadata/shadow/postcheck gates run normally.
        fixture = self.fixture()
        fixture.checkout()
        fixture.prepare_and_check()
        self.assertEqual(fixture.engine.verify(fixture.transaction_id)["state"], "SHADOW_VERIFIED")
        self.assertFalse(any("jev" in p.name.lower() for p in fixture.transaction.rglob("*.json")))
        result = fixture.engine.apply(fixture.transaction_id)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assertEqual((fixture.project / "app.py").read_bytes(), b"value = 2\n")


if __name__ == "__main__":
    unittest.main()
