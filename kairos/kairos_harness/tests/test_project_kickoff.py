from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from kairos.cli import build_parser, execute
from kairos.database import KnowledgeDatabase
from kairos.heartbeat import finalize_workspace, load_runtime_state, run_heartbeat
from kairos.loops import LoopTransitionError, kickoff_project
from kairos.templates import report_document
from kairos.util import atomic_write_json, atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class ProjectKickoffTests(WorkspaceTestCase):
    def _set_required(self) -> None:
        config_path = self.workspace / ".kairos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["governance_enforcement"] = "required"
        config.pop("governance_audit_allows_finalization", None)
        atomic_write_json(config_path, config)

    def _close_loop_one(self) -> None:
        goal = json.loads(
            (self.workspace / "goals" / "GOAL_KAIROS_001.json").read_text(encoding="utf-8")
        )
        criteria = [item["id"] for item in goal["milestones"][0]["criteria"]]
        report_path = self.workspace / "reports" / "report_TASK_0001_L001_V02.md"
        atomic_write_text(
            report_path,
            report_document(
                report_id="REPORT_TASK_0001_L001_V02",
                task_id="TASK_0001",
                title="Complete the generic starter verification",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=criteria,
                task_path="tasks/task_TASK_0001.md",
                loop=1,
                state="success",
                outcome="The isolated starter verification completed before finalization.",
                evidence="Heartbeat, retrieval, code-navigation, and fail-closed finalization checks passed.",
            ),
        )
        receipt = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=["reports/report_TASK_0001_L001_V02.md"],
        )
        self.assertTrue(receipt["verified"])
        payload, code = execute(
            build_parser().parse_args(
                ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001"]
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["closed"], "TASK_0001")

    def _finalize_loop_one(self) -> dict:
        self._close_loop_one()
        result = finalize_workspace(self.workspace)
        self.assertEqual(result["status"], "FINALIZED")
        return result

    def _finalize_loop_one_governed(self) -> dict:
        self._close_loop_one()
        self._set_required()
        result, code = execute(
            build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "FINALIZED")
        return result

    def _spec(self, *, suffix: str = "PROJECT") -> dict:
        goal_id = f"GOAL_{suffix}"
        milestone_id = f"MILESTONE_{suffix}"
        task_id = f"TASK_{suffix}"
        criterion_id = f"CRIT_{suffix}_001"
        return {
            "schema": "kairos-project-kickoff/v1",
            "goal": {
                "schema": "kairos-goal/v1",
                "id": goal_id,
                "title": "New Human project",
                "objective": "Start a fresh governed project from the immutable starter seal.",
                "state": "active",
                "milestones": [
                    {
                        "id": milestone_id,
                        "title": "First project milestone",
                        "objective": "Establish the first independently evidenced outcome.",
                        "state": "active",
                        "depends_on": [],
                        "criteria": [
                            {
                                "id": criterion_id,
                                "description": "The first project outcome is documented and verified.",
                                "state": "active",
                                "evidence_required": "A promoted success report.",
                                "required_artifact_types": ["report"],
                            }
                        ],
                    }
                ],
            },
            "task": {
                "id": task_id,
                "title": "Execute the first project task",
                "objective": "Produce the first governed project result under the new Human goal.",
                "milestone": milestone_id,
                "criteria": [criterion_id],
            },
        }

    def test_finalization_uses_terminal_database_scope_when_runtime_scope_is_null(self) -> None:
        self._close_loop_one()
        runtime_path = self.workspace / ".kairos" / "runtime_state.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime.update(
            active_goal=None,
            active_milestone=None,
            active_task=None,
            active_criterion=None,
        )
        atomic_write_json(runtime_path, runtime)
        self._set_required()
        result, code = execute(
            build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "FINALIZED")
        self.assertTrue((self.workspace / "archive" / "ARCHIVE_L0001.md").is_file())
        self.assertTrue(
            (self.workspace / "backups" / result["backup_id"] / "manifest.json").is_file()
        )

    def test_cli_project_kickoff_creates_scope_and_exactly_replays(self) -> None:
        finalized = self._finalize_loop_one_governed()
        spec = self._spec()
        args = build_parser().parse_args(
            [
                "project-kickoff",
                "--workspace",
                str(self.workspace),
                "--spec-json",
                json.dumps(spec),
            ]
        )
        first, code = execute(args)
        self.assertEqual(code, 0)
        self.assertEqual(first["status"], "VERIFIED")
        self.assertEqual(first["source_loop"], 1)
        self.assertEqual(first["target_loop"], 2)
        self.assertEqual(first["predecessor_finalization_id"], finalized["finalization_id"])
        self.assertEqual(first["command"], "project-kickoff")
        self.assertEqual(first["governance_action"]["phase"], "ROLLOVER")
        runtime = load_runtime_state(self.workspace)
        self.assertEqual(runtime["loop"], 2)
        self.assertEqual(runtime["active_task"], spec["task"]["id"])
        self.assertTrue(
            (self.workspace / "goals" / f"{spec['goal']['id']}.json").is_file()
        )
        self.assertTrue(
            (self.workspace / "tasks" / f"task_{spec['task']['id']}.md").is_file()
        )

        replay, replay_code = execute(args)
        self.assertEqual(replay_code, 0)
        self.assertEqual(replay["transition_id"], first["transition_id"])
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)

    def test_kickoff_rejects_nonfinalized_and_pre_staged_sources(self) -> None:
        spec = self._spec()
        with self.assertRaises(LoopTransitionError):
            kickoff_project(self.workspace, spec=spec)

        self._finalize_loop_one()
        goal_path = self.workspace / "goals" / f"{spec['goal']['id']}.json"
        atomic_write_json(goal_path, spec["goal"])
        with self.assertRaisesRegex(LoopTransitionError, "pre-staged"):
            kickoff_project(self.workspace, spec=spec)
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            count = int(
                connection.execute("SELECT count(*) FROM loop_transition_receipts").fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_changed_replay_contract_is_rejected_without_reincrement(self) -> None:
        self._finalize_loop_one()
        spec = self._spec()
        first = kickoff_project(self.workspace, spec=spec)
        changed = json.loads(json.dumps(spec))
        changed["task"]["objective"] = "A different replay objective must not be accepted."
        with self.assertRaisesRegex(LoopTransitionError, "replay contract differs"):
            kickoff_project(self.workspace, spec=changed)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)
        self.assertEqual(first["target_loop"], 2)

    def test_partial_staging_resumes_from_pending_receipt(self) -> None:
        self._finalize_loop_one()
        spec = self._spec()
        from kairos import loops

        original = loops._stage_kickoff_source
        calls = 0

        def fail_second(path, content, expected_sha256):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated second-source write failure")
            return original(path, content, expected_sha256)

        with patch("kairos.loops._stage_kickoff_source", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second-source"):
                kickoff_project(self.workspace, spec=spec)
        self.assertEqual(load_runtime_state(self.workspace)["lifecycle"], "FINALIZED")
        self.assertTrue(
            (self.workspace / "goals" / f"{spec['goal']['id']}.json").is_file()
        )
        self.assertFalse(
            (self.workspace / "tasks" / f"task_{spec['task']['id']}.md").exists()
        )

        recovered = kickoff_project(self.workspace, spec=spec)
        self.assertEqual(recovered["status"], "VERIFIED")
        self.assertEqual(recovered["target_loop"], 2)

    def test_concurrent_identical_kickoff_has_one_transition(self) -> None:
        self._finalize_loop_one()
        spec = self._spec()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(
                pool.map(lambda _: kickoff_project(self.workspace, spec=spec), range(2))
            )
        self.assertEqual(
            {receipt["transition_id"] for receipt in receipts},
            {receipts[0]["transition_id"]},
        )
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            count = int(
                connection.execute("SELECT count(*) FROM loop_transition_receipts").fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(count, 1)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)

    def test_corrupt_predecessor_backup_blocks_before_source_creation(self) -> None:
        finalized = self._finalize_loop_one()
        spec = self._spec()
        manifest_path = (
            self.workspace / "backups" / finalized["backup_id"] / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["database_sha256"] = "0" * 64
        atomic_write_json(manifest_path, manifest)
        with self.assertRaises(LoopTransitionError):
            kickoff_project(self.workspace, spec=spec)
        self.assertFalse(
            (self.workspace / "goals" / f"{spec['goal']['id']}.json").exists()
        )
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 1)

    def test_malformed_contract_is_rejected_before_transition(self) -> None:
        self._finalize_loop_one()
        malformed = self._spec()
        malformed["task"]["criteria"] = []
        with self.assertRaisesRegex(LoopTransitionError, "non-empty"):
            kickoff_project(self.workspace, spec=malformed)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 1)


if __name__ == "__main__":
    unittest.main()
