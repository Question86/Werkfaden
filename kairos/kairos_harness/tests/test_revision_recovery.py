from __future__ import annotations

import unittest

from kairos.backup import BackupError, create_backup, recover_artifact_revisions
from kairos.database import KnowledgeDatabase
from kairos.frontmatter import split_frontmatter
from kairos.promoter import PromotionError, promote_document
from kairos.recovery import _derived_paths, rebuild_derived_state
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class RevisionRecoveryTests(WorkspaceTestCase):
    def _task_revision_two_backup(self) -> tuple[str, KnowledgeDatabase]:
        path = self.workspace / "tasks" / "task_TASK_0001.md"
        revised = path.read_text(encoding="utf-8").replace(
            "revision = 1", "revision = 2", 1
        )
        atomic_write_text(path, revised)
        database = KnowledgeDatabase(database_path(self.workspace))
        receipt = promote_document(path, self.workspace, database)
        self.assertTrue(receipt["verified"])
        backup = create_backup(self.workspace, keep=3)
        self.assertTrue(backup["verification"]["verified"])
        return backup["backup_id"], database

    @staticmethod
    def _delete_revision(database: KnowledgeDatabase, artifact_id: str, revision: int) -> None:
        with database.transaction() as connection:
            connection.execute(
                "DELETE FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, revision),
            )

    def test_verified_backup_recovers_exact_revision_and_replay_is_idempotent(self) -> None:
        backup_id, database = self._task_revision_two_backup()
        self._delete_revision(database, "TASK_0001", 1)

        recovered = recover_artifact_revisions(
            self.workspace,
            backup_id=backup_id,
            revision_identities=["TASK_0001@1"],
        )
        self.assertTrue(recovered["verified"])
        self.assertEqual([row["identity"] for row in recovered["inserted"]], ["TASK_0001@1"])

        replay = recover_artifact_revisions(
            self.workspace,
            backup_id=backup_id,
            revision_identities=["TASK_0001@1"],
        )
        self.assertEqual(replay["inserted"], [])
        self.assertEqual(
            [row["identity"] for row in replay["already_present"]],
            ["TASK_0001@1"],
        )

    def test_missing_backup_revision_aborts_without_partial_insert(self) -> None:
        backup_id, database = self._task_revision_two_backup()
        self._delete_revision(database, "TASK_0001", 1)

        with self.assertRaises(BackupError):
            recover_artifact_revisions(
                self.workspace,
                backup_id=backup_id,
                revision_identities=["TASK_0001@1", "TASK_0001@999"],
            )
        connection = database.connect(read_only=True)
        try:
            count = connection.execute(
                "SELECT count(*) FROM artifact_revisions WHERE artifact_id='TASK_0001' AND revision=1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_conflicting_live_revision_aborts(self) -> None:
        backup_id, database = self._task_revision_two_backup()
        with database.transaction() as connection:
            connection.execute(
                "UPDATE artifact_revisions SET content_sha256=? WHERE artifact_id='TASK_0001' AND revision=1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(BackupError, "conflicts"):
            recover_artifact_revisions(
                self.workspace,
                backup_id=backup_id,
                revision_identities=["TASK_0001@1"],
            )

    def test_rebuild_uses_same_exact_history_recovery(self) -> None:
        backup_id, _ = self._task_revision_two_backup()
        for path in _derived_paths(self.workspace):
            if path.is_file():
                path.unlink()

        result = rebuild_derived_state(
            self.workspace,
            history_backup_id=backup_id,
            history_references=["TASK_0001@1"],
        )
        self.assertTrue(result["verified"])
        self.assertEqual(
            [row["identity"] for row in result["revision_recovery"]["inserted"]],
            ["TASK_0001@1"],
        )

    def test_changed_bytes_require_revision_and_later_revision_supersedes_failure(self) -> None:
        path = self.workspace / "code" / "CODE_0001_L001_V01.md"
        original = path.read_text(encoding="utf-8")
        changed = original.replace(
            "Convert structured context artifacts",
            "Convert verified structured context artifacts",
            1,
        )
        atomic_write_text(path, changed)
        database = KnowledgeDatabase(database_path(self.workspace))
        with self.assertRaises(PromotionError):
            promote_document(path, self.workspace, database)
        pending, failed = database.pending_counts()
        self.assertEqual((pending, failed), (0, 1))

        revised = changed.replace("revision = 1", "revision = 2", 1)
        atomic_write_text(path, revised)
        receipt = promote_document(path, self.workspace, database)
        self.assertTrue(receipt["verified"])
        self.assertEqual(database.pending_counts(), (0, 0))
        connection = database.connect(read_only=True)
        try:
            statuses = {row[0] for row in connection.execute("SELECT status FROM promotion_events WHERE artifact_id='CODE_0001_L001_V01'")}
            revision = connection.execute("SELECT revision FROM artifacts WHERE artifact_id='CODE_0001_L001_V01'").fetchone()[0]
        finally:
            connection.close()
        self.assertIn("superseded", statuses)
        self.assertIn("applied", statuses)
        self.assertEqual(revision, 2)

    def test_missing_managed_source_blocks_heartbeat(self) -> None:
        from kairos.heartbeat import run_heartbeat

        first = run_heartbeat(self.workspace, requested_mode="work")
        self.assertTrue(first["verified"])
        path = self.workspace / "reports" / "report_TASK_0001_L001_V01.md"
        path.unlink()
        result = run_heartbeat(self.workspace, requested_mode="verify")
        self.assertFalse(result["verified"])
        self.assertIn("reports/report_TASK_0001_L001_V01.md", result["missing_sources"])


if __name__ == "__main__":
    unittest.main()
