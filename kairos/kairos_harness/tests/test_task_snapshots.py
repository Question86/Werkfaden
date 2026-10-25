from __future__ import annotations

import json
import unittest

from kairos.backup import create_backup
from kairos.database import KnowledgeDatabase
from kairos.health import health_audit
from kairos.heartbeat import _archive_document, run_heartbeat
from kairos.promoter import prepare_document, promote_document
from kairos.task_snapshots import (
    TaskSnapshotError,
    ensure_task_snapshot_package,
    task_snapshot_path,
    verify_task_snapshot_history,
    verify_task_snapshot_package,
)
from kairos.templates import report_document, task_document
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class TaskSnapshotTests(WorkspaceTestCase):
    def _stage_loop_five_task(self) -> KnowledgeDatabase:
        path = self.workspace / "tasks" / "task_TASK_SNAPSHOT_TEST.md"
        atomic_write_text(
            path,
            task_document(
                task_id="TASK_SNAPSHOT_TEST",
                title="Task snapshot fixture",
                objective="Provide a current-loop task contract for snapshot verification.",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=[],
                loop=5,
            ),
        )
        database = KnowledgeDatabase(database_path(self.workspace))
        promote_document(path, self.workspace, database)
        return database

    def test_loop_five_package_is_deterministic_and_exact(self) -> None:
        database = self._stage_loop_five_task()
        first = ensure_task_snapshot_package(self.workspace, database, 5, "HB_SNAPSHOT_TEST")
        package = task_snapshot_path(self.workspace, 5)
        first_bytes = package.read_bytes()
        second = ensure_task_snapshot_package(self.workspace, database, 5, "HB_OTHER")

        self.assertTrue(first["verified"])
        self.assertEqual(first["package_sha256"], second["package_sha256"])
        self.assertEqual(first_bytes, package.read_bytes())
        self.assertEqual(first["previous_package_sha256"], None)
        self.assertEqual(len(first["entries"]), 1)
        entry = first["entries"][0]
        self.assertEqual(entry["path"], "tasks/task_TASK_SNAPSHOT_TEST.md")
        verified = verify_task_snapshot_package(self.workspace, package, expected_loop=5)
        self.assertEqual(verified["package_sha256"], first["package_sha256"])
        self.assertEqual(verified["entries"], first["entries"])

    def test_archive_binding_and_missing_package_fail_closed(self) -> None:
        database = self._stage_loop_five_task()
        task_snapshot = ensure_task_snapshot_package(self.workspace, database, 5, "HB_SNAPSHOT_TEST")
        archive_path, archive_id = _archive_document(
            self.workspace,
            database,
            5,
            {"heartbeat_id": "HB_SNAPSHOT_TEST"},
            task_snapshot=task_snapshot,
        )
        promote_document(archive_path, self.workspace, database)
        prepared = [prepare_document(archive_path, self.workspace)]
        history = verify_task_snapshot_history(self.workspace, prepared)
        self.assertEqual(history["verdict"], "PASS")
        self.assertEqual(history["finalized_loops"], [5])
        self.assertEqual(history["checked_packages"][0]["archive_id"], archive_id)

        package = task_snapshot_path(self.workspace, 5)
        package.write_bytes(package.read_bytes() + b"tampered")
        tampered = verify_task_snapshot_history(self.workspace, prepared)
        self.assertEqual(tampered["verdict"], "FAIL")
        self.assertTrue(any("hash" in item for item in tampered["failures"]))

        package.unlink()
        broken = verify_task_snapshot_history(self.workspace, prepared)
        self.assertEqual(broken["verdict"], "FAIL")
        self.assertTrue(any("missing" in item for item in broken["failures"]))

    def test_loop_six_cannot_start_a_snapshot_chain_without_loop_five(self) -> None:
        database = self._stage_loop_five_task()
        with self.assertRaisesRegex(TaskSnapshotError, "predecessor is missing"):
            ensure_task_snapshot_package(self.workspace, database, 6, "HB_SNAPSHOT_TEST")

    def test_verified_backup_carries_the_snapshot_package(self) -> None:
        database = self._stage_loop_five_task()
        ensure_task_snapshot_package(self.workspace, database, 5, "HB_SNAPSHOT_TEST")
        backup = create_backup(self.workspace)
        manifest = json.loads(
            (self.workspace / backup["path"] / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "archive/task_snapshots/TASK_SNAPSHOT_L0005.zip",
            {entry["path"] for entry in manifest["sources"]},
        )
        self.assertTrue(backup["verification"]["verified"])

    def test_legacy_revision_gap_is_diagnostic_not_operational_failure(self) -> None:
        report_path = self.workspace / "reports" / "report_LEGACY_REVISION_GAP.md"
        content = report_document(
            report_id="REPORT_LEGACY_REVISION_GAP",
            task_id="TASK_0001",
            title="Legacy reference diagnostic",
            workspace_id="KAIROS_TEST",
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            criteria=[],
            task_path="tasks/task_TASK_0001.md",
            loop=1,
            state="partial",
            outcome="A deliberately stale revision pointer is retained as historical evidence.",
            evidence="The report is only a fixture for the operational-health classification.",
        ).replace("v:dynamic", "v:999")
        atomic_write_text(report_path, content)
        heartbeat = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=["reports/report_LEGACY_REVISION_GAP.md"],
        )
        self.assertTrue(heartbeat["verified"])
        result = health_audit(self.workspace, full=True)
        self.assertNotIn(
            "reference revisions absent from immutable ledger",
            "\n".join(result["failures"]),
        )
        self.assertIn(
            "reports/report_LEGACY_REVISION_GAP.md->TASK_0001@999",
            result["database"]["reference_revision_bindings"]["missing"],
        )
        self.assertFalse(result["database"]["reference_revision_bindings"]["gating"])


if __name__ == "__main__":
    unittest.main()
