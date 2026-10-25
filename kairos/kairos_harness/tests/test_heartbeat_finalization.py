from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from kairos.cli import build_parser, execute
from kairos.database import KnowledgeDatabase
from kairos.governance import governance_status
from kairos.heartbeat import finalize_workspace, load_runtime_state, run_heartbeat
from kairos.health import health_audit
from kairos.promoter import PromotionError
from kairos.templates import report_document
from kairos.templates import task_document
from kairos.util import atomic_write_json, atomic_write_text, utc_now
from kairos.workspace import database_path

from support import WorkspaceTestCase


class HeartbeatFinalizationTests(WorkspaceTestCase):
    def test_health_treats_code_examples_as_literals_and_checks_live_links(self) -> None:
        # Reuse an existing valid document so this checks the real health path,
        # including both frontmatter and body, rather than a substitute scanner.
        report = next((self.workspace / "reports").glob("*.md"))
        original = report.read_text(encoding="utf-8")
        finding = f"outside-workspace file reference in reports/{report.name}"
        examples = [
            ("\n```python\nC:0001 pattern = 'file://'\n```\n", False),
            ("\n~~~text\nH:0001 sample = 'C:/external/file.txt'\n~~~\n", False),
            ("\nLiteral example: `file:///external/file.txt`.\n", False),
            ("\n[External file](file:///external/file.txt)\n", True),
            ("\nExternal path: C:/external/file.txt\n", True),
            ("\n```text\nfile:///example\n```\n[External](file:///actual)\n", True),
        ]
        for addition, rejected in examples:
            with self.subTest(addition=addition):
                atomic_write_text(report, original + addition)
                failures = health_audit(self.workspace)["failures"]
                self.assertEqual(finding in failures, rejected, failures)

    def _set_required(self) -> None:
        config_path = self.workspace / ".kairos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["governance_enforcement"] = "required"
        config.pop("governance_audit_allows_finalization", None)
        atomic_write_json(config_path, config)

    def _write_success_report(self) -> None:
        goal = json.loads((self.workspace / "goals" / "GOAL_KAIROS_001.json").read_text(encoding="utf-8"))
        criteria = [item["id"] for item in goal["milestones"][0]["criteria"]]
        report_path = self.workspace / "reports" / "report_TASK_0001_L001_V02.md"
        atomic_write_text(
            report_path,
            report_document(
                report_id="REPORT_TASK_0001_L001_V02",
                task_id="TASK_0001",
                title="Complete KAIROS validation evidence",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=criteria,
                task_path="tasks/task_TASK_0001.md",
                loop=1,
                revision=1,
                state="success",
                outcome="All four deterministic KAIROS acceptance checks passed in the isolated test workspace.",
                evidence="The isolated suite passed, the no-change heartbeat promoted zero artifacts, causal retrieval followed root cause to resolution to regression, and the blocked-finalization negative test returned BLOCKED.",
            ),
        )
        heartbeat = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=["reports/report_TASK_0001_L001_V02.md"],
        )
        self.assertTrue(heartbeat["verified"])

    def test_no_change_heartbeat_is_incremental_and_idempotent(self) -> None:
        first = run_heartbeat(self.workspace, requested_mode="work")
        stable_paths = [
            self.workspace / ".kairos" / "manifest.json",
            self.workspace / ".kairos" / "goal_manifest.json",
            self.workspace / ".kairos" / "canonical_projection.json",
        ]
        stable_mtimes = {path: path.stat().st_mtime_ns for path in stable_paths}
        second = run_heartbeat(self.workspace, requested_mode="breadth")
        self.assertTrue(first["verified"])
        self.assertTrue(second["verified"])
        self.assertEqual(second["changed_candidates"], [])
        self.assertEqual(second["promoted"], [])
        self.assertEqual(second["pending_count"], 0)
        self.assertEqual(second["failed_count"], 0)
        self.assertEqual(
            {path: path.stat().st_mtime_ns for path in stable_paths},
            stable_mtimes,
        )

    def test_finalization_fails_closed_without_criterion_evidence(self) -> None:
        result = finalize_workspace(self.workspace)
        self.assertEqual(result["status"], "BLOCKED")
        blocker_types = {item["type"] for item in result["blockers"]}
        self.assertIn("goal_coverage", blocker_types)
        self.assertEqual(load_runtime_state(self.workspace)["lifecycle"], "FINALIZING_METADATA_BLOCKED")

    def test_success_report_unlocks_archive_finalization(self) -> None:
        self._write_success_report()
        self._set_required()
        args = build_parser().parse_args(
            ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001", "--mode", "verify"]
        )
        _, code = execute(args)
        self.assertEqual(code, 0)
        result, code = execute(
            build_parser().parse_args(
                ["finalize", "--workspace", str(self.workspace)]
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "FINALIZED")
        self.assertEqual(result["governance_action"]["phase"], "CLOSURE")
        archive = self.workspace / "archive" / "ARCHIVE_L0001.md"
        self.assertTrue(archive.exists())
        archive_text = archive.read_text(encoding="utf-8")
        validation_question = "Which receipts prove KAIROS loop 1 finalization?"
        self.assertIn(f'question = "{validation_question}"', archive_text)
        self.assertIn(f'query = "{validation_question}"', archive_text)
        self.assertEqual(load_runtime_state(self.workspace)["lifecycle"], "FINALIZED")

    def test_failed_archive_promotion_is_recovered_by_idempotent_finalize_retry(self) -> None:
        self._write_success_report()
        self._set_required()
        _, close_code = execute(
            build_parser().parse_args(
                ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001"]
            )
        )
        self.assertEqual(close_code, 0)

        from kairos import heartbeat as heartbeat_module

        original_promote = heartbeat_module.promote_document

        def fail_archive_once(path, *args, **kwargs):
            if path.name == "ARCHIVE_L0001.md":
                raise PromotionError("simulated archive promotion interruption")
            return original_promote(path, *args, **kwargs)

        with patch("kairos.heartbeat.promote_document", side_effect=fail_archive_once):
            blocked, blocked_code = execute(
                build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
            )
        self.assertEqual(blocked_code, 2)
        self.assertEqual(blocked["status"], "BLOCKED")
        archive = self.workspace / "archive" / "ARCHIVE_L0001.md"
        marker = self.workspace / ".kairos" / "finalization_pending" / "ARCHIVE_L0001.json"
        self.assertTrue(archive.is_file())
        self.assertTrue(marker.is_file())

        retried, retry_code = execute(
            build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
        )
        self.assertEqual(retry_code, 0)
        self.assertEqual(retried["status"], "FINALIZED")
        self.assertEqual(retried["recovered_unpromoted_archives"], ["archive/ARCHIVE_L0001.md"])
        self.assertTrue(archive.is_file())
        self.assertFalse(marker.exists())
        self.assertEqual(governance_status(self.workspace)["open_quarantine"], 0)

    def test_failed_archive_contract_event_is_superseded_on_retry(self) -> None:
        self._write_success_report()
        self._set_required()
        _, close_code = execute(
            build_parser().parse_args(
                ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001"]
            )
        )
        self.assertEqual(close_code, 0)

        from kairos import promoter as promoter_module

        original_verify = promoter_module._verify_contracts

        def fail_archive_contract_once(connection, metadata):
            if metadata["id"] == "ARCHIVE_L0001":
                raise PromotionError("simulated archive search-contract failure")
            return original_verify(connection, metadata)

        with patch("kairos.promoter._verify_contracts", side_effect=fail_archive_contract_once):
            blocked, blocked_code = execute(
                build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
            )
        self.assertEqual(blocked_code, 2)
        self.assertEqual(blocked["status"], "BLOCKED")

        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            failed_event = connection.execute(
                """
                SELECT event_id,status FROM promotion_events
                WHERE artifact_id='ARCHIVE_L0001' AND status='failed'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(failed_event)

        retried, retry_code = execute(
            build_parser().parse_args(["finalize", "--workspace", str(self.workspace)])
        )
        self.assertEqual(retry_code, 0)
        self.assertEqual(retried["status"], "FINALIZED")
        self.assertIn(failed_event["event_id"], retried["recovered_finalization_events"])
        connection = database.connect(read_only=True)
        try:
            status = connection.execute(
                "SELECT status FROM promotion_events WHERE event_id=?",
                (failed_event["event_id"],),
            ).fetchone()["status"]
        finally:
            connection.close()
        self.assertEqual(status, "superseded")

    def test_close_task_requires_evidence_and_deactivates_completed_task(self) -> None:
        self._write_success_report()
        args = build_parser().parse_args(
            ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001", "--mode", "verify"]
        )
        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertEqual(payload["closed"], "TASK_0001")
        self.assertTrue(payload["heartbeat"]["verified"])
        self.assertIsNone(load_runtime_state(self.workspace)["active_task"])
        task_text = (self.workspace / "tasks" / "task_TASK_0001.md").read_text(encoding="utf-8")
        self.assertIn('state = "completed"', task_text)
        self.assertIn("closure_evidence", task_text)
        self.assertIn("- [x] **CRIT_KAIROS_001:**", task_text)
        self.assertIn("No active task contract is promoted.", (self.workspace / "ACTIVE.md").read_text(encoding="utf-8"))
        self.assertIn("No active task is promoted.", (self.workspace / "NEURAL_CORTEX.md").read_text(encoding="utf-8"))
        current = json.loads((self.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["next_required_read"], "NEURAL_CORTEX.md#s-active-frontier")
        goal = json.loads(
            (self.workspace / "goals" / "GOAL_KAIROS_001.json").read_text(encoding="utf-8")
        )
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["milestones"][0]["state"], "completed")
        self.assertTrue(
            all(
                criterion["state"] == "completed"
                for criterion in goal["milestones"][0]["criteria"]
            )
        )

    def test_required_close_task_recovers_atomic_scope_transition_once(self) -> None:
        self._write_success_report()
        self._set_required()
        args = build_parser().parse_args(
            ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001"]
        )
        with patch("kairos.governance._running_action_paths", return_value=(None, set())):
            with self.assertRaisesRegex(Exception, "quarantined"):
                execute(args)
        failed_status = governance_status(self.workspace)
        self.assertEqual(failed_status["open_quarantine"], 2)
        self.assertIsNone(load_runtime_state(self.workspace)["active_task"])

        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed_closure"])
        self.assertTrue(payload["heartbeat"]["verified"])
        self.assertEqual(len(payload["heartbeat"]["governance_attribution"]["resolved_quarantine"]), 2)
        self.assertIsNone(load_runtime_state(self.workspace)["active_task"])
        settled = governance_status(self.workspace)
        self.assertEqual(settled["open_quarantine"], 0)
        task_text = (self.workspace / "tasks" / "task_TASK_0001.md").read_text(encoding="utf-8")
        self.assertIn('revision = 2', task_text)
        self.assertIn('state = "completed"', task_text)

    def test_close_task_can_explicitly_activate_a_promoted_open_successor(self) -> None:
        self._write_success_report()
        successor_goal = {
            "schema": "kairos-goal/v1",
            "id": "GOAL_SUCCESSOR",
            "title": "Successor task goal",
            "objective": "Prove explicit same-loop task handoff.",
            "state": "active",
            "created_at": utc_now(),
            "milestones": [
                {
                    "id": "MILESTONE_SUCCESSOR",
                    "title": "Successor milestone",
                    "objective": "Activate the promoted successor only after predecessor closure.",
                    "state": "active",
                    "depends_on": [],
                    "criteria": [
                        {
                            "id": "CRIT_SUCCESSOR_001",
                            "description": "The successor becomes the exact runtime route.",
                            "state": "open",
                            "evidence_required": "A successor handoff report.",
                            "required_artifact_types": ["report"],
                        }
                    ],
                }
            ],
        }
        atomic_write_json(
            self.workspace / "goals" / "GOAL_SUCCESSOR.json",
            successor_goal,
        )
        atomic_write_text(
            self.workspace / "tasks" / "task_TASK_SUCCESSOR.md",
            task_document(
                task_id="TASK_SUCCESSOR",
                title="Execute successor work",
                objective="Become active only through explicit predecessor closure handoff.",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_SUCCESSOR",
                milestone_id="MILESTONE_SUCCESSOR",
                criteria=[successor_goal["milestones"][0]["criteria"][0]],
                loop=1,
            ),
        )
        prepared = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=["tasks/task_TASK_SUCCESSOR.md"],
        )
        self.assertTrue(prepared["verified"])
        args = build_parser().parse_args(
            [
                "close-task",
                "--workspace",
                str(self.workspace),
                "--id",
                "TASK_0001",
                "--next-task",
                "TASK_SUCCESSOR",
            ]
        )
        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertEqual(payload["activated"], "TASK_SUCCESSOR")
        runtime = load_runtime_state(self.workspace)
        self.assertEqual(runtime["active_goal"], "GOAL_SUCCESSOR")
        self.assertEqual(runtime["active_milestone"], "MILESTONE_SUCCESSOR")
        self.assertEqual(runtime["active_task"], "TASK_SUCCESSOR")
        self.assertEqual(runtime["active_criterion"], "CRIT_SUCCESSOR_001")


if __name__ == "__main__":
    unittest.main()
