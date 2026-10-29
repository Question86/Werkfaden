from __future__ import annotations

import json
from pathlib import Path
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
    def _task_creation_args(self, task_id):
        metadata = prepare_document(self.workspace / "tasks/task_TASK_0001.md", self.workspace).frontmatter.metadata
        return ["new-task", "--workspace", str(self.workspace), "--id", task_id,
                "--title", "Native task creation regression", "--objective",
                "Establish explicit acceptance criteria and evidence without changing a predecessor before promotion.",
                "--goal", metadata["goal"], "--milestone", metadata["milestone"],
                "--criteria", ",".join(metadata["criteria"])]

    def _invoke_task_creation(self, task_id):
        import contextlib
        import io
        from kairos.cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(self._task_creation_args(task_id))
        return code, json.loads(output.getvalue())

    def test_generated_task_search_contract_uses_exact_acceptance_answer(self):
        from kairos.frontmatter import split_frontmatter
        text = task_document(task_id="TASK_CONTRACT_TEST", title="Contract fixture",
            objective="Explicit acceptance criteria and evidence for a bounded task.",
            workspace_id="KAIROS_TEST", goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01", criteria=[], loop=1)
        metadata = split_frontmatter(text).metadata
        answers = {(item["question"], item["target"]) for item in metadata["answers"]}
        for contract in metadata["search_contract"]:
            self.assertIn((contract["query"], contract["expected"].split("#", 1)[1]), answers)

    def test_failed_task_promotion_preserves_valid_predecessor_scope(self):
        from unittest.mock import patch
        from kairos import heartbeat
        from kairos.util import read_json
        self.assertTrue(run_heartbeat(self.workspace)["verified"])
        before = read_json(self.workspace / ".kairos/runtime_state.json")
        original = heartbeat.promote_document
        def fail_task(path, *args, **kwargs):
            if Path(path).name == "task_TASK_REFUSED_TEST.md":
                raise ValueError("Isolated new task promotion refusal")
            return original(path, *args, **kwargs)
        with patch.object(heartbeat, "promote_document", side_effect=fail_task):
            code, result = self._invoke_task_creation("TASK_REFUSED_TEST")
        self.assertEqual(code, 2, result)
        self.assertFalse(result["heartbeat"]["verified"])
        after = read_json(self.workspace / ".kairos/runtime_state.json")
        current = read_json(self.workspace / "current.json")
        for field in ("active_goal", "active_milestone", "active_task", "active_criterion"):
            self.assertEqual(after[field], before[field], field)
            self.assertEqual(current[field], before[field], field)
        self.assertEqual(after["lifecycle"], "BLOCKED")
        self.assertTrue((self.workspace / "tasks/task_TASK_REFUSED_TEST.md").is_file())

    def test_successful_task_creation_activates_exact_promoted_source(self):
        from kairos.util import read_json
        self.assertTrue(run_heartbeat(self.workspace)["verified"])
        code, result = self._invoke_task_creation("TASK_ACTIVATED_TEST")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["heartbeat"]["task_activation"],
                         {"requested": "TASK_ACTIVATED_TEST", "activated": True})
        task = prepare_document(self.workspace / "tasks/task_TASK_ACTIVATED_TEST.md", self.workspace)
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            row = connection.execute("SELECT content_sha256 FROM artifacts WHERE artifact_id=?",
                                     ("TASK_ACTIVATED_TEST",)).fetchone()
        finally:
            connection.close()
        self.assertEqual(row["content_sha256"], task.content_sha256)
        for filename in (".kairos/runtime_state.json", "current.json"):
            state = read_json(self.workspace / filename)
            self.assertEqual(state["active_task"], "TASK_ACTIVATED_TEST")
            self.assertEqual(state["active_criterion"], task.frontmatter.metadata["criteria"][0])

    def test_direct_heartbeat_cannot_request_ungranted_task_activation(self):
        from kairos.heartbeat import HeartbeatError
        self.assertTrue(run_heartbeat(self.workspace)["verified"])
        path = self.workspace / ".kairos/runtime_state.json"
        before = path.read_bytes()
        with self.assertRaisesRegex(HeartbeatError, "matching RUNNING new-task"):
            run_heartbeat(self.workspace, task_activation="TASK_0001")
        self.assertEqual(path.read_bytes(), before)

    def test_task_activation_rejects_a_different_task_creation_owner(self):
        from unittest.mock import patch
        from kairos import cli
        from kairos.util import read_json
        self.assertTrue(run_heartbeat(self.workspace)["verified"])
        before = read_json(self.workspace / ".kairos/runtime_state.json")
        original = cli.run_heartbeat
        def wrong_task(*args, **kwargs):
            kwargs["task_activation"] = "TASK_UNGRANTED_TEST"
            return original(*args, **kwargs)
        with patch.object(cli, "run_heartbeat", side_effect=wrong_task):
            code, result = self._invoke_task_creation("TASK_OWNER_TEST")
        self.assertEqual(code, 2, result)
        self.assertIn("matching RUNNING new-task", result["message"])
        after = read_json(self.workspace / ".kairos/runtime_state.json")
        self.assertEqual(after["active_task"], before["active_task"])
        self.assertEqual(after["active_criterion"], before["active_criterion"])

    def test_postpromotion_router_failure_keeps_a_valid_blocked_task_scope(self):
        from unittest.mock import patch
        from kairos import heartbeat
        from kairos.util import read_json
        self.assertTrue(run_heartbeat(self.workspace)["verified"])
        with patch.object(heartbeat, "refresh_dynamic_canonicals",
                          side_effect=RuntimeError("Isolated router checkpoint failure")):
            code, result = self._invoke_task_creation("TASK_ROUTER_FAILURE_TEST")
        self.assertEqual(code, 2, result)
        self.assertTrue(result["heartbeat"]["task_activation"]["activated"])
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            row = connection.execute("SELECT metadata_json FROM artifacts WHERE artifact_id=? AND document_type='task'",
                                     ("TASK_ROUTER_FAILURE_TEST",)).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata_json"])
        for filename in (".kairos/runtime_state.json", "current.json"):
            state = read_json(self.workspace / filename)
            self.assertEqual(state["active_task"], "TASK_ROUTER_FAILURE_TEST")
            self.assertIn(state["active_criterion"], metadata["criteria"])
            self.assertEqual(state["lifecycle"], "BLOCKED")

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
