from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
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


class LivePolicyStatusTests(WorkspaceTestCase):
    def pool(self, enabled=False, count=30, trigger=9):
        return dict(schema="werkfaden-tx-recycling-pool/v1", enabled=enabled,
                    required=enabled and count >= trigger, materialized_count=count,
                    trigger_at=trigger, target_max=trigger-1,
                    need_to_retire=max(0, count-trigger+1) if enabled else 0,
                    active_lease=None, rows=[],
                    next_action="TX_RECYCLING_DISABLED" if not enabled else
                                "TX_RECYCLING_REQUIRED" if count >= trigger else "NORMAL_WORK_ALLOWED")

    def config(self, values):
        path = self.workspace / '.kairos/workshop.config.json'
        atomic_write_json(path, values)
        return path

    def cache(self, pool):
        value = {k: pool[k] for k in ('required', 'materialized_count', 'trigger_at',
                                     'target_max', 'need_to_retire', 'next_action')}
        value['schema'] = 'werkfaden-tx-recycling-frontier/v1'
        atomic_write_json(self.workspace / '.kairos/tx_recycling_frontier.json', value)
        return value

    def observe(self, pool):
        from kairos.recycling_bridge import live_recycling_status
        factory = lambda path: SimpleNamespace(config=SimpleNamespace(raw=json.loads(path.read_text(encoding='utf-8'))))
        with patch('kairos.recycling_bridge._workshop_imports', return_value=(factory, lambda engine, publish: pool)):
            return live_recycling_status(self.workspace)

    def test_disabled_or_absent_flags_never_become_normal_work(self):
        cases = ({}, {'transaction_documents_required': False, 'tx_retention_required': False},
                 {'transaction_documents_required': True, 'tx_retention_required': False})
        for raw in cases:
            with self.subTest(raw=raw):
                self.config(raw); pool = self.pool(); self.cache(pool)
                before = {p: p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()}
                result = self.observe(pool)
                after = {p: p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()}
                self.assertEqual(before, after)
                self.assertTrue(result['available'])
                self.assertEqual(result['policy_state'], 'INACTIVE')
                self.assertEqual(result['next_action'], 'TX_POLICY_ACTIVATION_REQUIRED')
                self.assertTrue(result['policy_activation_required'])
                self.assertTrue(result['cached_frontier_fresh'])
                self.assertFalse(result['required'])  # Native capacity admission remains distinct.
                for field in ('transaction_documents_required', 'tx_retention_required'):
                    self.assertEqual(result['policy'][field]['present'], field in raw)
                self.assertIn('sealed-policy migration', result['policy_activation_boundary'])

    def test_invalid_or_incompatible_policy_is_unverified(self):
        for raw in ({'tx_retention_required': True},
                    {'tx_retention_required': True, 'transaction_documents_required': False},
                    *({'tx_retention_required': value} for value in (None, 0, 1, 'true', [], {})),
                    {'transaction_documents_required': 'false'}):
            with self.subTest(raw=raw):
                self.config(raw)
                result = self.observe(self.pool(enabled=raw.get('tx_retention_required') is True))
                self.assertFalse(result['available'])
                self.assertEqual(result['policy_state'], 'UNVERIFIED')
                self.assertTrue(result['reconciliation_required'])
                self.assertEqual(result['next_action'], 'TX_POLICY_UNVERIFIED')
                self.assertIn('TX_POLICY_INVALID', result['reason'])

    def test_epoch_binds_policy_bytes_and_native_capacity_without_stale_cache_claims(self):
        self.config({}); pool = self.pool(); self.cache(pool)
        initial = self.observe(pool)
        self.config({'transaction_documents_required': True})
        policy_change = self.observe(pool)
        self.assertNotEqual(initial['source_epoch'], policy_change['source_epoch'])
        capacity_change = self.observe(self.pool(trigger=10))
        self.assertNotEqual(policy_change['source_epoch'], capacity_change['source_epoch'])
        self.assertFalse(capacity_change['cached_frontier_fresh'])
        self.assertTrue(capacity_change['reconciliation_required'])
        path = self.workspace / '.kairos/tx_recycling_frontier.json'
        for malformed in ('[]', 'null', '{broken'):
            atomic_write_text(path, malformed)
            self.assertFalse(self.observe(pool)['cached_frontier_fresh'])
        cached = self.cache(pool); cached['required'] = 0
        atomic_write_json(path, cached)
        self.assertFalse(self.observe(pool)['cached_frontier_fresh'])

    def test_active_policy_observes_native_pressure_and_missing_package(self):
        self.config({'transaction_documents_required': True, 'tx_retention_required': True})
        for count in (0, 9):
            with self.subTest(count=count):
                pool = self.pool(enabled=True, count=count); self.cache(pool)
                result = self.observe(pool)
                self.assertEqual(result['policy_state'], 'ACTIVE')
                self.assertEqual(result['required'], count == 9)
                self.assertFalse(result['policy_activation_required'])
                self.assertEqual(result['next_action'], 'tx-recycle-inspect or tx-recycle-drain'
                                 if count == 9 else 'NORMAL_WORK_ALLOWED')
        from kairos.recycling_bridge import live_recycling_status
        with patch('kairos.recycling_bridge._workshop_imports', side_effect=ImportError('unavailable')):
            result = live_recycling_status(self.workspace)
        self.assertFalse(result['available'])
        self.assertNotEqual(result['next_action'], 'NORMAL_WORK_ALLOWED')

    def test_malformed_native_pool_never_becomes_normal_work(self):
        self.config({'transaction_documents_required': True, 'tx_retention_required': True})
        for key, value in (('schema', 'unknown'), ('required', []), ('materialized_count', '30'),
                           ('trigger_at', True), ('target_max', 99), ('rows', None), ('rows', [{}]),
                           ('active_lease', 1), ('next_action', 'unrecognized')):
            pool = self.pool(enabled=True); pool[key] = value
            with self.subTest(key=key, value=value):
                result = self.observe(pool)
                self.assertFalse(result['available'])
                self.assertTrue(result['reconciliation_required'])
                self.assertIn('TX_POOL_UNVERIFIED', result['reason'])
                self.assertNotEqual(result['next_action'], 'NORMAL_WORK_ALLOWED')

    def test_config_race_is_not_a_verified_live_observation(self):
        from kairos.recycling_bridge import live_recycling_status
        self.config({}); pool = self.pool(); self.cache(pool)
        factory = lambda path: SimpleNamespace(config=SimpleNamespace(raw=json.loads(path.read_text(encoding='utf-8'))))
        def mutate(engine, publish):
            self.config({'transaction_documents_required': True})
            return pool
        with patch('kairos.recycling_bridge._workshop_imports', return_value=(factory, mutate)):
            result = live_recycling_status(self.workspace)
        self.assertFalse(result['available'])
        self.assertIn('TX_POLICY_CHANGED', result['reason'])
        self.assertNotIn('source_epoch', result)

    def test_real_native_observation_does_not_initialize_control_state(self):
        from kairos.recycling_bridge import live_recycling_status
        from runtime_sync_workshop.engine import WorkshopEngine
        # Loader-valid observation fixture only; no source/compiler seal is asserted.
        raw = dict(schema='runtime-sync-workshop-config/v1',
                   codebase_root=str(self.workspace), runtime_root=str(self.workspace),
                   cmake_file=str(self.workspace / 'AGENTS.md'),
                   dataflow_index=str(self.workspace / 'AGENTS.md'),
                   blueprint_root=str(self.workspace / 'code'),
                   managed_blueprint_root=str(self.workspace / 'code'),
                   kairos_workspace=str(self.workspace),
                   kairos_harness=str(Path(__file__).resolve().parents[1]),
                   kairos_database=str(self.workspace / '.kairos/kairos.db'),
                   state_directory='observer_control_state', transaction_directory='observer_transactions')
        self.config(raw)
        state = self.workspace / '.kairos/observer_control_state'
        transactions = self.workspace / '.kairos/observer_transactions'
        self.assertFalse(state.exists()); self.assertFalse(transactions.exists())
        before = {p.relative_to(self.workspace).as_posix(): p.read_bytes() if p.is_file() else None
                  for p in self.workspace.rglob('*')}
        with patch.object(WorkshopEngine, '__init__', side_effect=AssertionError('writer constructor forbidden')):
            result = live_recycling_status(self.workspace)
        self.assertTrue(result['available'], result)
        self.assertEqual(result['policy_state'], 'INACTIVE')
        self.assertEqual(result['materialized_count'], 0)
        self.assertFalse(state.exists()); self.assertFalse(transactions.exists())
        after = {p.relative_to(self.workspace).as_posix(): p.read_bytes() if p.is_file() else None
                 for p in self.workspace.rglob('*')}
        self.assertEqual(before, after)

    def test_session_status_reads_policy_guidance_and_old_workspace_fallback(self):
        from kairos.heartbeat import status_report
        self.config({}); pool = self.pool(); self.cache(pool)
        factory = lambda path: SimpleNamespace(config=SimpleNamespace(raw=json.loads(path.read_text(encoding='utf-8'))))
        with patch('kairos.recycling_bridge._workshop_imports', return_value=(factory, lambda engine, publish: pool)):
            result = status_report(self.workspace)
            self.assertEqual(result['tx_recycling_live']['next_action'], 'TX_POLICY_ACTIVATION_REQUIRED')
            self.assertEqual(result['next_required_read'], 'docs/KAIROS_WORKSPACE_TOOLS.md#s-retention')
            (self.workspace / 'docs/KAIROS_WORKSPACE_TOOLS.md').unlink()
            result = status_report(self.workspace)
            self.assertEqual(result['next_required_read'], '_LOOP_GATE.md#s-verdict')


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
