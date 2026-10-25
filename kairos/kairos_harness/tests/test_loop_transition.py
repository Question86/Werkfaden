from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from kairos.cli import build_parser, execute
from kairos.database import KnowledgeDatabase
from kairos.heartbeat import (
    HeartbeatError,
    _archive_document,
    finalize_workspace,
    load_runtime_state,
    run_heartbeat,
)
from kairos.loops import LoopTransitionError, start_new_loop
from kairos.readiness import evaluate_finalization_readiness
from kairos.recovery import rebuild_derived_state
from kairos.templates import report_document, task_document
from kairos.util import atomic_write_json, atomic_write_text, utc_now
from kairos.workspace import database_path

from support import WorkspaceTestCase


class LoopTransitionTests(WorkspaceTestCase):
    def _close_loop_one_contracts(self) -> None:
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
                title="Seal loop one validation evidence",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=criteria,
                task_path="tasks/task_TASK_0001.md",
                loop=1,
                state="success",
                outcome="Every isolated loop-one criterion passed before rollover testing.",
                evidence="Heartbeat, retrieval, documentation, and fail-closed finalization assertions passed in this isolated workspace.",
            ),
        )
        heartbeat = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=["reports/report_TASK_0001_L001_V02.md"],
        )
        self.assertTrue(heartbeat["verified"])
        args = build_parser().parse_args(
            ["close-task", "--workspace", str(self.workspace), "--id", "TASK_0001"]
        )
        _, code = execute(args)
        self.assertEqual(code, 0)

    def _finalize_loop_one(self) -> dict:
        self._close_loop_one_contracts()
        database = KnowledgeDatabase(database_path(self.workspace))
        readiness = evaluate_finalization_readiness(
            database,
            source_check={"checked": True, "fresh": True},
        )
        self.assertTrue(readiness["ready"])
        self.assertIn(
            "READY_FOR_FINALIZATION.",
            (self.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8"),
        )
        result = finalize_workspace(self.workspace)
        self.assertEqual(result["status"], "FINALIZED")
        self.assertEqual(result["loop"], 1)
        return result

    def _stage_loop_two(self) -> tuple[str, str, str]:
        goal_id = "GOAL_LOOP_TWO"
        milestone_id = "MILESTONE_LOOP_TWO"
        task_id = "TASK_LOOP_TWO"
        criterion = {
            "id": "CRIT_LOOP_TWO_001",
            "description": "Prove that loop two begins from the sealed loop-one predecessor.",
            "state": "open",
            "evidence_required": "A loop-two validation report.",
            "required_artifact_types": ["report"],
        }
        atomic_write_json(
            self.workspace / "goals" / f"{goal_id}.json",
            {
                "schema": "kairos-goal/v1",
                "id": goal_id,
                "title": "Loop two test goal",
                "objective": "Exercise the governed loop transition without changing predecessor evidence.",
                "state": "active",
                "created_at": utc_now(),
                "milestones": [
                    {
                        "id": milestone_id,
                        "title": "Open loop two",
                        "objective": "Activate exactly one loop-two task.",
                        "state": "active",
                        "depends_on": [],
                        "criteria": [criterion],
                    }
                ],
            },
        )
        atomic_write_text(
            self.workspace / "tasks" / f"task_{task_id}.md",
            task_document(
                task_id=task_id,
                title="Execute loop two",
                objective="Continue governed work only after the verified predecessor transition.",
                workspace_id="KAIROS_TEST",
                goal_id=goal_id,
                milestone_id=milestone_id,
                criteria=[criterion],
                loop=2,
            ),
        )
        return goal_id, milestone_id, task_id

    def test_gate_and_finalizer_share_all_blocker_classes(self) -> None:
        heartbeat = run_heartbeat(self.workspace, requested_mode="verify")
        self.assertTrue(heartbeat["verified"])
        database = KnowledgeDatabase(database_path(self.workspace))
        readiness = evaluate_finalization_readiness(
            database,
            source_check={"checked": True, "fresh": True},
        )
        expected = {item["type"] for item in readiness["blockers"]}
        self.assertEqual(expected, {"goal_coverage", "task_closure", "goal_closure"})
        gate = (self.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8")
        self.assertTrue(all(f"`{name}`" in gate for name in expected))
        blocked = finalize_workspace(self.workspace)
        self.assertEqual({item["type"] for item in blocked["blockers"]}, expected)

    def test_breathe_never_closes_or_increments_and_pressure_is_bounded(self) -> None:
        before = json.loads((self.workspace / "current.json").read_text(encoding="utf-8"))
        receipt = run_heartbeat(
            self.workspace,
            requested_mode="auto",
            metrics={"context_pressure": 0.95},
        )
        after = json.loads((self.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["mode"], "breathe")
        self.assertEqual(before["loop"], after["loop"])
        self.assertNotEqual(after["lifecycle"], "FINALIZED")
        with self.assertRaises(HeartbeatError):
            run_heartbeat(self.workspace, metrics={"context_pressure": 1.01})
        with self.assertRaises(HeartbeatError):
            run_heartbeat(self.workspace, metrics={"context_pressure": float("nan")})

    def test_new_loop_rejects_every_nonfinalized_entry(self) -> None:
        with self.assertRaises(LoopTransitionError):
            start_new_loop(
                self.workspace,
                goal_id="GOAL_LOOP_TWO",
                milestone_id="MILESTONE_LOOP_TWO",
                task_id="TASK_LOOP_TWO",
            )
        self.assertEqual(
            json.loads((self.workspace / "current.json").read_text(encoding="utf-8"))["loop"],
            1,
        )

    def test_sealed_predecessor_rejects_each_nonfinalized_lifecycle(self) -> None:
        self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        runtime_path = self.workspace / ".kairos" / "runtime_state.json"
        sealed = json.loads(runtime_path.read_text(encoding="utf-8"))
        for lifecycle in (
            "READY",
            "ACTIVE",
            "BLOCKED",
            "FINALIZING_METADATA_BLOCKED",
            "FINALIZING",
            "STARTING_LOOP",
        ):
            candidate = dict(sealed)
            candidate["lifecycle"] = lifecycle
            atomic_write_json(runtime_path, candidate)
            with self.assertRaises(LoopTransitionError, msg=lifecycle):
                start_new_loop(
                    self.workspace,
                    goal_id=goal_id,
                    milestone_id=milestone_id,
                    task_id=task_id,
                )
        atomic_write_json(runtime_path, sealed)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 1)

    def test_atomic_rollover_is_exact_idempotent_and_preserves_ledgers(self) -> None:
        finalization = self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        args = build_parser().parse_args(
            [
                "new-loop",
                "--workspace",
                str(self.workspace),
                "--goal",
                goal_id,
                "--milestone",
                milestone_id,
                "--task",
                task_id,
            ]
        )
        first, code = execute(args)
        self.assertEqual(code, 0)
        self.assertEqual(first["status"], "VERIFIED")
        self.assertEqual(first["source_loop"], 1)
        self.assertEqual(first["target_loop"], 2)
        self.assertEqual(first["predecessor_finalization_id"], finalization["finalization_id"])
        self.assertTrue(
            all(
                first["ledger_counts_after"][key] >= value
                for key, value in first["ledger_counts_before"].items()
            )
        )
        runtime = load_runtime_state(self.workspace)
        self.assertEqual(runtime["loop"], 2)
        self.assertEqual(runtime["active_task"], task_id)
        self.assertEqual(runtime["context_pressure"], 0.0)
        self.assertEqual(runtime["contradiction_count"], 0)
        self.assertEqual(runtime["unresolved_branches"], 0)

        replay = start_new_loop(
            self.workspace,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
        self.assertEqual(replay["transition_id"], first["transition_id"])
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)

    def test_concurrent_rollover_has_one_transition_receipt(self) -> None:
        self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(
                pool.map(
                    lambda _: start_new_loop(
                        self.workspace,
                        goal_id=goal_id,
                        milestone_id=milestone_id,
                        task_id=task_id,
                    ),
                    range(2),
                )
            )
        self.assertEqual({item["transition_id"] for item in receipts}, {receipts[0]["transition_id"]})
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            count = int(connection.execute("SELECT count(*) FROM loop_transition_receipts").fetchone()[0])
        finally:
            connection.close()
        self.assertEqual(count, 1)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)

    def test_pending_transition_resumes_after_failed_heartbeat_without_reincrement(self) -> None:
        self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        poison = self.workspace / "reports" / "invalid_unattributed_source.md"
        atomic_write_text(poison, "not a KAIROS document\n")
        pending = start_new_loop(
            self.workspace,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)
        poison.unlink()
        recovered = start_new_loop(
            self.workspace,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
        self.assertEqual(recovered["status"], "VERIFIED")
        self.assertEqual(recovered["transition_id"], pending["transition_id"])
        self.assertEqual(recovered["source_loop"], 1)
        self.assertEqual(recovered["target_loop"], 2)
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)

    def test_archive_collision_is_rejected_without_overwriting_source(self) -> None:
        collision = self.workspace / "archive" / "ARCHIVE_L0001.md"
        original = b"occupied archive identity\n"
        collision.write_bytes(original)
        database = KnowledgeDatabase(database_path(self.workspace))
        with self.assertRaises(HeartbeatError):
            _archive_document(
                self.workspace,
                database,
                1,
                {"heartbeat_id": "HB_COLLISION_TEST"},
            )
        self.assertEqual(collision.read_bytes(), original)

    def test_corrupt_predecessor_backup_blocks_without_increment(self) -> None:
        finalization = self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        manifest_path = self.workspace / "backups" / finalization["backup_id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["database_sha256"] = "0" * 64
        atomic_write_json(manifest_path, manifest)
        with self.assertRaises(LoopTransitionError):
            start_new_loop(
                self.workspace,
                goal_id=goal_id,
                milestone_id=milestone_id,
                task_id=task_id,
            )
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 1)

    def test_source_only_recovery_keeps_loop_number_and_archive_identity(self) -> None:
        self._finalize_loop_one()
        goal_id, milestone_id, task_id = self._stage_loop_two()
        transition = start_new_loop(
            self.workspace,
            goal_id=goal_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
        self.assertEqual(transition["target_loop"], 2)
        derived = [
            database_path(self.workspace),
            self.workspace / ".kairos" / "runtime_state.json",
            self.workspace / ".kairos" / "manifest.json",
            self.workspace / ".kairos" / "goal_manifest.json",
            self.workspace / ".kairos" / "canonical_projection.json",
            self.workspace / "current.json",
            self.workspace / "NEURAL_CORTEX.md",
            self.workspace / "ACTIVE.md",
            self.workspace / "CLOSED.md",
            self.workspace / "_LOOP_GATE.md",
            self.workspace / "_SESSION.md",
        ]
        for path in derived:
            if path.is_file():
                path.unlink()
        rebuilt = rebuild_derived_state(self.workspace)
        self.assertTrue(rebuilt["verified"])
        self.assertEqual(load_runtime_state(self.workspace)["loop"], 2)
        self.assertEqual(load_runtime_state(self.workspace)["active_task"], task_id)
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            archive = connection.execute(
                "SELECT path,state FROM artifacts WHERE artifact_id='ARCHIVE_L0001'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(archive)
        self.assertEqual(archive["state"], "finalized")


if __name__ == "__main__":
    unittest.main()
