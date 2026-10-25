from __future__ import annotations

import json
import unittest

from kairos.database import KnowledgeDatabase
from kairos.heartbeat import load_runtime_state, run_heartbeat
from kairos.promoter import PromotionError, _verify_contracts
from kairos.templates import task_document
from kairos.util import atomic_write_json, atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class TaskRoutingInvariantTests(WorkspaceTestCase):
    def test_exact_answer_handle_wins_search_contract_amid_competing_sections(self) -> None:
        database = KnowledgeDatabase(database_path(self.workspace))
        with database.transaction() as connection:
            for index in range(12):
                connection.execute(
                    """
                    INSERT INTO section_fts(
                        artifact_id,section_id,title,capsule,questions,entities,facets,body
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"COMPETING_{index:02d}",
                        "s-next",
                        "Which KAIROS task is active next",
                        "Which KAIROS task is active next",
                        "",
                        "KAIROS task active next",
                        "goal gap next action",
                        "Which KAIROS task is active next " * 8,
                    ),
                )
            verified = _verify_contracts(
                connection,
                {
                    "search_contract": [
                        {
                            "query": "Which KAIROS task is active next?",
                            "expected": "KAIROS_ACTIVE#s-active-frontier",
                            "required_top_k": 5,
                        }
                    ]
                },
            )
        self.assertEqual(
            verified[0]["actual"][0],
            "KAIROS_ACTIVE#s-active-frontier",
        )
        self.assertTrue(verified[0]["host_enforced"])
        self.assertEqual(verified[0]["status"], "matched")

    def test_local_search_contract_miss_remains_fail_closed(self) -> None:
        database = KnowledgeDatabase(database_path(self.workspace))
        with database.transaction() as connection:
            with self.assertRaisesRegex(PromotionError, "search contract failed"):
                _verify_contracts(
                    connection,
                    {
                        "search_contract": [
                            {
                                "query": "Which KAIROS task is active next?",
                                "expected": "MISSING_ARTIFACT#s-missing",
                                "required_top_k": 5,
                            }
                        ]
                    },
                )

    def test_active_criterion_is_scoped_to_active_task_contract(self) -> None:
        task_path = self.workspace / "tasks" / "task_TASK_0002.md"
        atomic_write_text(
            task_path,
            task_document(
                task_id="TASK_0002",
                title="Validate task-scoped criterion routing",
                objective="Prove that heartbeat selects criteria from the active task rather than the global goal backlog.",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=[
                    {
                        "id": "CRIT_KAIROS_003",
                        "description": "Heartbeat routes the active task to its own first unmet criterion.",
                        "evidence_required": "Task-scoped active-criterion regression test.",
                    }
                ],
                loop=1,
            ),
        )
        promoted = run_heartbeat(
            self.workspace,
            requested_mode="work",
            changed_paths=["tasks/task_TASK_0002.md"],
        )
        self.assertTrue(promoted["verified"])

        runtime_path = self.workspace / ".kairos" / "runtime_state.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime.update(
            {
                "active_goal": "GOAL_KAIROS_001",
                "active_milestone": "MILESTONE_KAIROS_01",
                "active_task": "TASK_0002",
                "active_criterion": None,
            }
        )
        atomic_write_json(runtime_path, runtime)

        routed = run_heartbeat(self.workspace, requested_mode="work")
        self.assertTrue(routed["verified"])
        self.assertEqual(
            load_runtime_state(self.workspace)["active_criterion"],
            "CRIT_KAIROS_003",
        )


if __name__ == "__main__":
    unittest.main()
