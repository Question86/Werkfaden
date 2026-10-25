from __future__ import annotations

import unittest
from unittest.mock import patch

from kairos.database import KnowledgeDatabase
from kairos.promoter import PromotionError, promote_document
from kairos.templates import report_document
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class SQLiteTempStoreTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))
        self.path = self.workspace / "reports" / "REPORT_TEMPSTORE_L001_V01.md"

    def _write_report(self, *, revision: int = 1, outcome: str = "Temp-store promotion test.", criteria: list[str] | None = None) -> None:
        atomic_write_text(
            self.path,
            report_document(
                report_id="REPORT_TEMPSTORE_L001_V01",
                task_id="TASK_0001",
                title="SQLite temp-store promotion",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=criteria or [],
                task_path="tasks/task_TASK_0001.md",
                loop=1,
                revision=revision,
                outcome=outcome,
                evidence="The governed connection and relation write set are under test.",
            ),
        )

    def test_every_governed_connection_pins_temp_store_to_memory(self) -> None:
        self.database.initialize()
        writable = self.database.connect()
        try:
            self.assertEqual(writable.execute("PRAGMA temp_store").fetchone()[0], 2)
        finally:
            writable.close()

        read_only = self.database.connect(read_only=True)
        try:
            self.assertEqual(read_only.execute("PRAGMA temp_store").fetchone()[0], 2)
        finally:
            read_only.close()

    def test_failed_relation_promotion_retries_same_event_and_is_idempotent(self) -> None:
        self._write_report()

        import kairos.promoter as promoter_module

        original_verify = promoter_module._verify_contracts
        calls = 0

        def fail_once(connection, metadata):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PromotionError("forced relation-promotion failure")
            return original_verify(connection, metadata)

        with patch.object(promoter_module, "_verify_contracts", side_effect=fail_once):
            with self.assertRaises(PromotionError):
                promote_document(self.path, self.workspace, self.database)

            connection = self.database.connect(read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM artifacts WHERE artifact_id=?",
                        ("REPORT_TEMPSTORE_L001_V01",),
                    ).fetchone()[0],
                    0,
                )
                failed = connection.execute(
                    "SELECT event_id,status FROM promotion_events WHERE artifact_id=?",
                    ("REPORT_TEMPSTORE_L001_V01",),
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(failed)
            self.assertEqual(failed["status"], "failed")

            receipt = promote_document(self.path, self.workspace, self.database)
            replay = promote_document(self.path, self.workspace, self.database)

        self.assertTrue(receipt["verified"])
        self.assertEqual(replay["receipt_id"], receipt["receipt_id"])
        self.assertEqual(self.database.pending_counts(), (0, 0))
        connection = self.database.connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT event_id,status FROM promotion_events WHERE artifact_id=?",
                ("REPORT_TEMPSTORE_L001_V01",),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], failed["event_id"])
        self.assertEqual(rows[0]["status"], "applied")

    def test_relation_replacement_accepts_a_larger_revision(self) -> None:
        self._write_report()
        first = promote_document(self.path, self.workspace, self.database)
        self._write_report(revision=2, outcome="Temp-store promotion test, revised.")
        second = promote_document(self.path, self.workspace, self.database)

        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(self.database.pending_counts(), (0, 0))
        connection = self.database.connect(read_only=True)
        try:
            revisions = connection.execute(
                "SELECT revision FROM artifact_revisions WHERE artifact_id=? ORDER BY revision",
                ("REPORT_TEMPSTORE_L001_V01",),
            ).fetchall()
            relations = connection.execute(
                "SELECT count(*) FROM relations WHERE source_artifact_id=?",
                ("REPORT_TEMPSTORE_L001_V01",),
            ).fetchone()[0]
            current = connection.execute(
                "SELECT revision FROM artifacts WHERE artifact_id=?",
                ("REPORT_TEMPSTORE_L001_V01",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual([row["revision"] for row in revisions], [1, 2])
        self.assertEqual(relations, 1)
        self.assertEqual(current, 2)


if __name__ == "__main__":
    unittest.main()
