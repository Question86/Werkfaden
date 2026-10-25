from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from kairos.database import KnowledgeDatabase
from kairos.promoter import PromotionError, promote_document
from kairos.templates import report_document
from kairos.util import atomic_write_text
from kairos.workspace import database_path
from kairos.heartbeat import run_heartbeat

from support import WorkspaceTestCase


class CanonicalProjectionTests(WorkspaceTestCase):
    def _write_report(self) -> None:
        atomic_write_text(
            self.workspace / "reports" / "report_CANONICAL_STALE_TEST_001.md",
            report_document(
                report_id="REPORT_CANONICAL_STALE_TEST_001",
                task_id="TASK_0001",
                title="Canonical projection retry fixture",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=[],
                task_path="tasks/task_TASK_0001.md",
                loop=1,
                revision=1,
                outcome="Exercise a failed promotion followed by an exact event retry.",
                evidence="The fixture only tests canonical refresh semantics.",
            ),
        )

    def test_retry_refreshes_canonical_bytes_after_failed_readiness_snapshot(self) -> None:
        initial = run_heartbeat(self.workspace, requested_mode="verify")
        self.assertTrue(initial["verified"])
        self._write_report()
        report_path = self.workspace / "reports" / "report_CANONICAL_STALE_TEST_001.md"
        database = KnowledgeDatabase(database_path(self.workspace))

        import kairos.promoter as promoter_module

        original_verify = promoter_module._verify_contracts

        def fail_once(connection, metadata):
            raise PromotionError("forced promotion failure for canonical retry")

        with patch.object(promoter_module, "_verify_contracts", side_effect=fail_once):
            with self.assertRaises(PromotionError):
                promote_document(report_path, self.workspace, database)

        blocked = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            use_reconciliation=False,
        )
        self.assertFalse(blocked["verified"])
        self.assertEqual(blocked["failed_count"], 1)
        self.assertIn(
            "Failed promotions: `1`",
            (self.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8"),
        )

        promoter_module._verify_contracts = original_verify
        promote_document(report_path, self.workspace, database)
        repaired = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            use_reconciliation=False,
        )

        self.assertTrue(repaired["verified"])
        self.assertEqual(repaired["failed_count"], 0)
        self.assertIn("KAIROS_LOOP_GATE", repaired["promoted"])
        gate = (self.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8")
        self.assertIn("Failed promotions: `0`", gate)
        self.assertIn("0 quarantined change(s)", gate)
        projection = json.loads(
            (self.workspace / ".kairos" / "canonical_projection.json").read_text(encoding="utf-8")
        )
        self.assertTrue(projection["signature"])


if __name__ == "__main__":
    unittest.main()
