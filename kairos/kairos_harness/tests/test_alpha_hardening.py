from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

from kairos.backup import create_backup, restore_drill, verify_backup
from kairos.cli import build_parser, execute
from kairos.database import KnowledgeDatabase
from kairos.freshness import FreshnessError, ensure_workspace_fresh
from kairos.frontmatter import HeaderError, split_frontmatter
from kairos.goals import coverage_report, sync_goal_files, unmet_required_criteria
from kairos.health import health_audit
from kairos.heartbeat import run_heartbeat
from kairos.promoter import PromotionError, promote_document
from kairos.recovery import rebuild_derived_state
from kairos.search import search_database
from kairos.templates import research_document, task_document
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class AlphaHardeningTests(WorkspaceTestCase):
    def test_tracked_sources_rebuild_absent_derived_state_and_search(self) -> None:
        recovered = self.workspace.parent / f"{self.workspace.name}-recovered"
        try:
            recovered.mkdir()
            shutil.copy2(self.workspace / "AGENTS.md", recovered / "AGENTS.md")
            (recovered / ".kairos").mkdir()
            shutil.copy2(
                self.workspace / ".kairos" / "config.json",
                recovered / ".kairos" / "config.json",
            )
            for name in (
                "tasks",
                "reports",
                "bugs",
                "code",
                "decisions",
                "research",
                "docs",
                "archive",
                "goals",
            ):
                shutil.copytree(self.workspace / name, recovered / name)
            result = rebuild_derived_state(recovered)
            self.assertTrue(result["verified"])
            database = KnowledgeDatabase(database_path(recovered))
            query = search_database(
                database,
                "Which acceptance criteria must TASK_0001 satisfy?",
                record_trace=False,
            )
            self.assertEqual(query["primary"]["artifact_id"], "TASK_0001")
        finally:
            shutil.rmtree(recovered, ignore_errors=True)

    def test_goal_json_edit_is_implicitly_synchronized_and_type_requirements_are_enforced(self) -> None:
        goal_path = self.workspace / "goals" / "GOAL_KAIROS_001.json"
        goal = json.loads(goal_path.read_text(encoding="utf-8"))
        criterion = goal["milestones"][0]["criteria"][0]
        criterion["description"] = "Goal source drift must be synchronized before ordinary metadata access."
        criterion["required_artifact_types"] = ["report", "research"]
        atomic_write_text(goal_path, json.dumps(goal, indent=2, sort_keys=True) + "\n")
        args = build_parser().parse_args(["coverage", "--workspace", str(self.workspace)])
        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["freshness"]["refreshed"])
        self.assertEqual(payload["freshness"]["changed_goals"], ["goals/GOAL_KAIROS_001.json"])
        row = next(
            value
            for value in coverage_report(KnowledgeDatabase(database_path(self.workspace)))
            if value["criterion_id"] == criterion["id"]
        )
        self.assertEqual(row["description"], criterion["description"])
        self.assertIn("research", row["missing_artifact_types"])
        self.assertFalse(row["requirements_met"])

    def test_completed_criterion_cannot_hide_a_missing_artifact_class(self) -> None:
        goal_path = self.workspace / "goals" / "GOAL_KAIROS_001.json"
        goal = json.loads(goal_path.read_text(encoding="utf-8"))
        goal["state"] = "completed"
        goal["milestones"][0]["state"] = "completed"
        for criterion in goal["milestones"][0]["criteria"]:
            criterion["state"] = "completed"
        target = goal["milestones"][0]["criteria"][0]
        target["required_artifact_types"] = [
            *target["required_artifact_types"],
            "research",
        ]
        atomic_write_text(goal_path, json.dumps(goal, indent=2, sort_keys=True) + "\n")
        database = KnowledgeDatabase(database_path(self.workspace))
        sync_goal_files(self.workspace, database)
        unmet = {row["criterion_id"]: row for row in unmet_required_criteria(database)}
        self.assertIn(target["id"], unmet)
        self.assertEqual(unmet[target["id"]]["declared_state"], "completed")
        self.assertIn("research", unmet[target["id"]]["missing_artifact_types"])

    def test_search_access_promotes_out_of_band_document_without_explicit_heartbeat(self) -> None:
        question = "Which unique alpha source fact proves implicit metadata refresh?"
        path = self.workspace / "research" / "RESEARCH_ALPHA_IMPLICIT_001.md"
        atomic_write_text(
            path,
            research_document(
                research_id="RESEARCH_ALPHA_IMPLICIT_001",
                task_id="TASK_0001",
                title="Implicit freshness proof",
                question=question,
                source_uri="https://docs.python.org/3/library/sqlite3.html",
                source_kind="internet",
                source_fact="A normal KAIROS search access must reconcile this source before querying its derived projection.",
                interpretation="The access gateway owns the fallback when a writer omitted an explicit heartbeat.",
                workspace_id="KAIROS_TEST",
                task_path="tasks/task_TASK_0001.md",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                loop=1,
            ),
        )
        args = build_parser().parse_args(
            ["search", "--workspace", str(self.workspace), question]
        )
        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["freshness"]["refreshed"])
        self.assertEqual(payload["primary"]["artifact_id"], "RESEARCH_ALPHA_IMPLICIT_001")
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT receipt_json FROM heartbeat_receipts WHERE heartbeat_id=?",
                (payload["freshness"]["heartbeat_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertTrue(json.loads(row["receipt_json"])["trigger"].startswith("implicit:search access"))

    def test_malformed_out_of_band_revision_fails_closed_and_preserves_projection(self) -> None:
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            before = connection.execute(
                "SELECT content_sha256 FROM artifacts WHERE artifact_id='BUG_0001_L001_V01'"
            ).fetchone()[0]
        finally:
            connection.close()
        path = self.workspace / "bugs" / "BUG_0001_L001_V01.md"
        atomic_write_text(path, path.read_text(encoding="utf-8") + "\nUnversioned material change.\n")
        with self.assertRaises(FreshnessError):
            ensure_workspace_fresh(self.workspace, reason="adversarial malformed edit")
        connection = database.connect(read_only=True)
        try:
            after = connection.execute(
                "SELECT content_sha256 FROM artifacts WHERE artifact_id='BUG_0001_L001_V01'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(after, before)

    def test_generated_canonical_tamper_is_replaced_not_promoted(self) -> None:
        database = KnowledgeDatabase(database_path(self.workspace))
        connection = database.connect(read_only=True)
        try:
            revision_before = int(
                connection.execute(
                    "SELECT revision FROM artifacts WHERE artifact_id='KAIROS_ACTIVE'"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        active = self.workspace / "ACTIVE.md"
        atomic_write_text(active, active.read_text(encoding="utf-8") + "\nTAMPERED ROUTER CLAIM\n")
        args = build_parser().parse_args(["status", "--workspace", str(self.workspace)])
        payload, code = execute(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["freshness"]["refreshed"])
        self.assertNotIn("TAMPERED ROUTER CLAIM", active.read_text(encoding="utf-8"))
        connection = database.connect(read_only=True)
        try:
            revision_after = int(
                connection.execute(
                    "SELECT revision FROM artifacts WHERE artifact_id='KAIROS_ACTIVE'"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(revision_after, revision_before + 1)

    def test_concurrent_heartbeats_are_serialized(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: run_heartbeat(self.workspace), range(2)))
        self.assertTrue(all(receipt["verified"] for receipt in receipts))
        sequences = sorted(receipt["sequence"] for receipt in receipts)
        self.assertEqual(sequences[1], sequences[0] + 1)

    def test_twenty_thousand_active_and_closed_task_projections_remain_bounded(self) -> None:
        target_root = self.workspace / "stress_targets"
        target_root.mkdir()
        for index in range(16):
            atomic_write_text(
                target_root / f"task_{index:05d}.md",
                '# Stress target\n\n<a id="s-objective"></a>\n## OBJECTIVE\n',
            )
        database = KnowledgeDatabase(database_path(self.workspace))
        rows = []
        for state, label, target_offset in (
            ("active", "ACTIVE", 0),
            ("completed", "CLOSED", 8),
        ):
            for index in range(10_000):
                artifact_id = f"TASK_STRESS_{label}_{index:05d}"
                relative = (
                    f"stress_targets/task_{target_offset + index:05d}.md"
                    if index < 8
                    else f"omitted/{label.casefold()}/task_{index:05d}.md"
                )
                rows.append(
                    (
                        artifact_id,
                        relative,
                        "task",
                        state,
                        "task_contract",
                        "KAIROS_TEST",
                        f"GOAL_STRESS/MILESTONE_STRESS/{artifact_id}",
                        None,
                        artifact_id,
                        None,
                        None,
                        1,
                        "2030-01-01T00:00:00Z",
                        "Synthetic scale row used only to test bounded dynamic routing.",
                        "Synthetic scale row; not execution evidence.",
                        "0" * 64,
                        128,
                        "{}",
                        "2030-01-01T00:00:00Z",
                    )
                )
        with database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO artifacts(
                    artifact_id,path,document_type,state,authority,workspace_id,route,
                    loop_id,task_id,goal_id,milestone_id,revision,updated_at,capsule,
                    claim_boundary,content_sha256,header_bytes,metadata_json,promoted_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        receipt = run_heartbeat(self.workspace)
        self.assertTrue(receipt["verified"])
        text = (self.workspace / "ACTIVE.md").read_text(encoding="utf-8")
        frontier = text.split('<a id="s-active-frontier"></a>', 1)[1].split(
            '<a id="s-next-action"></a>',
            1,
        )[0]
        self.assertLessEqual(len(text.encode("utf-8")), 64 * 1024)
        self.assertLessEqual(
            sum(
                "[ref:tasks/" in line or "[ref:stress_targets/" in line
                for line in frontier.splitlines()
            ),
            8,
        )
        self.assertLessEqual(
            sum(
                "[ref:tasks/" in line or "[ref:stress_targets/" in line
                for line in text.splitlines()
            ),
            16,
        )
        next_action = text.split('<a id="s-next-action"></a>', 1)[1]
        self.assertIn("TASK_0001#s-objective", next_action)
        self.assertNotIn("[ref:", next_action)
        self.assertIn("additional active task(s) are omitted", text)
        closed_text = (self.workspace / "CLOSED.md").read_text(encoding="utf-8")
        recent = closed_text.split('<a id="s-recent"></a>', 1)[1].split(
            '<a id="s-history"></a>',
            1,
        )[0]
        self.assertLessEqual(len(closed_text.encode("utf-8")), 64 * 1024)
        self.assertLessEqual(
            sum(
                "[ref:tasks/" in line or "[ref:stress_targets/" in line
                for line in recent.splitlines()
            ),
            8,
        )
        self.assertIn("additional closure(s) are omitted", closed_text)

    def test_backup_restore_drill_and_corruption_detection(self) -> None:
        result = create_backup(self.workspace, keep=3)
        self.assertTrue(result["verification"]["verified"])
        backup_database = self.workspace / result["path"] / "kairos.db"
        self.assertFalse((backup_database.parent / "kairos.db-wal").exists())
        self.assertFalse((backup_database.parent / "kairos.db-shm").exists())
        drill = restore_drill(self.workspace, result["backup_id"])
        self.assertTrue(drill["verified"])
        self.assertTrue(drill["search_replay"]["matched"])
        source_archive = self.workspace / result["path"] / "sources.zip"
        with source_archive.open("ab") as stream:
            stream.write(b"corruption")
        corrupted = verify_backup(self.workspace, result["backup_id"])
        self.assertFalse(corrupted["verified"])
        self.assertIn("source archive hash mismatch", corrupted["failures"])

    def test_unknown_authority_is_rejected(self) -> None:
        goal = json.loads(
            (self.workspace / "goals" / "GOAL_KAIROS_001.json").read_text(encoding="utf-8")
        )
        text = task_document(
            task_id="TASK_AUTHORITY_TEST",
            title="Authority test",
            objective="Reject unscoped authority values.",
            workspace_id="KAIROS_TEST",
            goal_id="GOAL_KAIROS_001",
            milestone_id="MILESTONE_KAIROS_01",
            criteria=goal["milestones"][0]["criteria"],
            loop=1,
        ).replace('authority = "task_contract"', 'authority = "generic_authority"', 1)
        with self.assertRaises(HeaderError):
            split_frontmatter(text)

    def test_promotion_rejects_foreign_workspace_identity(self) -> None:
        path = self.workspace / "research" / "RESEARCH_FOREIGN_WORKSPACE_001.md"
        atomic_write_text(
            path,
            research_document(
                research_id="RESEARCH_FOREIGN_WORKSPACE_001",
                task_id="TASK_0001",
                title="Foreign workspace rejection",
                question="Can a document claim a foreign workspace?",
                source_uri="urn:kairos:test:foreign-workspace",
                source_kind="workspace",
                source_fact="This fixture must never enter the local projection.",
                interpretation="Configured workspace identity is an ownership boundary.",
                workspace_id="FOREIGN_WORKSPACE",
                task_path="tasks/task_TASK_0001.md",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                loop=1,
            ),
        )
        with self.assertRaisesRegex(PromotionError, "nor a declared imported workspace"):
            promote_document(
                path,
                self.workspace,
                KnowledgeDatabase(database_path(self.workspace)),
            )

    def test_promotion_accepts_declared_imported_workspace(self) -> None:
        config_path = self.workspace / ".kairos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["imported_workspace_ids"] = ["FOREIGN_WORKSPACE"]
        atomic_write_text(config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")
        path = self.workspace / "research" / "RESEARCH_IMPORTED_WORKSPACE_001.md"
        atomic_write_text(
            path,
            research_document(
                research_id="RESEARCH_IMPORTED_WORKSPACE_001",
                task_id="TASK_FOREIGN_0001",
                title="Declared import acceptance",
                question="Can a declared origin keep its own scope identifiers?",
                source_uri="urn:kairos:test:imported-workspace",
                source_kind="workspace",
                source_fact="The origin scope is recorded, not resolved.",
                interpretation="A declared origin owns its own identifier namespace.",
                workspace_id="FOREIGN_WORKSPACE",
                task_path="tasks/task_TASK_FOREIGN_0001.md",
                goal_id="GOAL_FOREIGN_0001",
                milestone_id="MILESTONE_FOREIGN_01",
                loop=1,
            ),
        )
        receipt = promote_document(
            path,
            self.workspace,
            KnowledgeDatabase(database_path(self.workspace)),
        )
        # The goal, milestone and task above exist in no local contract. Promotion succeeds
        # because a declared origin is recorded rather than resolved.
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["external_origin"], "FOREIGN_WORKSPACE")
        self.assertEqual(receipt["search_contract_policy"], "host_enforced")
        ownership = health_audit(self.workspace)["database"]["ownership"]
        self.assertEqual(ownership["accepted_imported_artifacts"]["count"], 1)
        self.assertEqual(ownership["foreign_workspace_artifacts"], [])
        self.assertEqual(ownership["goal_or_milestone_violations"], [])
        self.assertEqual(ownership["parent_task_violations"], [])

    def test_imported_duplicate_search_contracts_can_be_origin_recorded(self) -> None:
        config_path = self.workspace / ".kairos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["imported_workspace_ids"] = ["FOREIGN_WORKSPACE"]
        config["imported_search_contract_policies"] = {
            "FOREIGN_WORKSPACE": "origin_recorded"
        }
        atomic_write_text(config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")
        receipts = []
        for index in range(7):
            research_id = f"RESEARCH_IMPORTED_COLLISION_{index:02d}"
            path = self.workspace / "research" / f"{research_id}.md"
            atomic_write_text(
                path,
                research_document(
                    research_id=research_id,
                    task_id="TASK_FOREIGN_0001",
                    title="Imported duplicate-query contract",
                    question="Which shared origin fact is recorded?",
                    source_uri=f"urn:kairos:test:imported-collision:{index}",
                    source_kind="workspace",
                    source_fact=f"Origin fact {index} is preserved verbatim.",
                    interpretation="The host records the origin claim without owning its ranking surface.",
                    workspace_id="FOREIGN_WORKSPACE",
                    task_path="tasks/task_TASK_FOREIGN_0001.md",
                    goal_id="GOAL_FOREIGN_0001",
                    milestone_id="MILESTONE_FOREIGN_01",
                    loop=1,
                ),
            )
            receipts.append(
                promote_document(
                    path,
                    self.workspace,
                    KnowledgeDatabase(database_path(self.workspace)),
                )
            )

        self.assertTrue(all(receipt["verified"] for receipt in receipts))
        self.assertTrue(
            all(
                receipt["search_contract_policy"] == "origin_recorded"
                for receipt in receipts
            )
        )
        self.assertTrue(
            all(receipt["search_contract_unverified_count"] == 1 for receipt in receipts)
        )
        contracts = [
            contract
            for receipt in receipts
            for contract in receipt["search_contracts"]
        ]
        self.assertTrue(all(contract["status"] == "recorded_not_resolved" for contract in contracts))
        self.assertTrue(all("actual" not in contract for contract in contracts))

    def test_origin_recorded_contract_still_requires_a_real_section(self) -> None:
        config_path = self.workspace / ".kairos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["imported_workspace_ids"] = ["FOREIGN_WORKSPACE"]
        config["imported_search_contract_policies"] = {
            "FOREIGN_WORKSPACE": "origin_recorded"
        }
        atomic_write_text(config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")
        research_id = "RESEARCH_IMPORTED_BAD_TARGET_001"
        path = self.workspace / "research" / f"{research_id}.md"
        text = research_document(
            research_id=research_id,
            task_id="TASK_FOREIGN_0001",
            title="Imported invalid contract target",
            question="Which imported target exists?",
            source_uri="urn:kairos:test:imported-bad-target",
            source_kind="workspace",
            source_fact="The declared target must exist.",
            interpretation="Structural target validation is independent of host ranking.",
            workspace_id="FOREIGN_WORKSPACE",
            task_path="tasks/task_TASK_FOREIGN_0001.md",
            goal_id="GOAL_FOREIGN_0001",
            milestone_id="MILESTONE_FOREIGN_01",
            loop=1,
        ).replace(
            f'expected = "{research_id}#s-fact"',
            f'expected = "{research_id}#s-does-not-exist"',
            1,
        )
        atomic_write_text(path, text)
        with self.assertRaisesRegex(PromotionError, "expected target does not exist"):
            promote_document(
                path,
                self.workspace,
                KnowledgeDatabase(database_path(self.workspace)),
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
