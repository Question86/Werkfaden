from __future__ import annotations

import unittest
import os

from kairos.database import KnowledgeDatabase
from kairos.heartbeat import run_heartbeat
from kairos.query import compile_query
from kairos.search import search_database
from kairos.templates import research_document
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


class PromotionSearchTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))

    def test_strict_reconciliation_detects_same_size_same_mtime_source_drift(self) -> None:
        from kairos.reconcile import reconcile_documents
        path = next((self.workspace / "reports").glob("*.md"))
        initial = path.read_bytes()
        self.assertIn(b"Capsule:", initial)
        stat = path.stat()
        altered = initial.replace(b"Capsule:", b"Copsule:", 1)
        self.assertEqual(len(initial), len(altered))
        path.write_bytes(altered)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        fast = reconcile_documents(self.workspace)
        strict = reconcile_documents(self.workspace, verify_content_hashes=True)
        self.assertNotIn(path.resolve(), fast.changed)
        self.assertIn(path.resolve(), strict.changed)

    def test_causal_question_returns_root_cause_and_depth_chain(self) -> None:
        payload = search_database(
            self.database,
            "Why can metadata become stale after an out-of-band document write?",
            mode="depth",
            max_hops=3,
            record_trace=False,
        )
        self.assertEqual(payload["primary"]["artifact_id"], "BUG_0001_L001_V01")
        self.assertEqual(payload["primary"]["section_id"], "s-root-cause")
        chain = [(item["via"], item["section_id"]) for item in payload["context_chase"]]
        self.assertGreaterEqual(len(chain), 2)
        self.assertEqual(chain[0], ("next", "s-resolution"))
        self.assertEqual(chain[1], ("validated_by", "s-regression"))

    def test_exact_identifier_returns_task_sections(self) -> None:
        payload = search_database(
            self.database,
            "What must TASK_0001 achieve?",
            mode="breadth",
            record_trace=False,
        )
        self.assertTrue(any(item["artifact_id"] == "TASK_0001" for item in payload["results"]))
        self.assertIn("TASK_0001", payload["query_frame"]["exact_identifiers"])

    def test_query_handles_are_english(self) -> None:
        connection = self.database.connect(read_only=True)
        try:
            languages = {row[0] for row in connection.execute("SELECT DISTINCT language FROM query_handles")}
        finally:
            connection.close()
        self.assertEqual(languages, {"en"})

    def test_architecture_placement_and_contextual_knowledge_intents_are_explicit(self) -> None:
        frame = compile_query(
            "Where should I put future cultural knowledge in the KAIROS architecture?"
        )
        self.assertIn("architecture", frame.intents)
        self.assertIn("content_placement", frame.intents)
        self.assertIn("contextual_knowledge", frame.intents)
        self.assertIn("belongs_to", frame.preferred_relations)
        self.assertIn("derived_from", frame.preferred_relations)

        promotion = compile_query(
            "How does a new document become searchable without a full scan?"
        )
        self.assertIn("promotion", promotion.intents)

        authority = compile_query("Which source decides whether KAIROS may finalize?")
        self.assertIn("authority", authority.intents)

    def test_external_research_enters_the_same_heartbeat_search_graph(self) -> None:
        path = self.workspace / "research" / "RESEARCH_9001_L001_V01.md"
        question = "Which source fact explains why context metadata must be promoted in the same heartbeat?"
        atomic_write_text(
            path,
            research_document(
                research_id="RESEARCH_9001_L001_V01",
                task_id="TASK_0001",
                title="External evidence ingestion",
                question=question,
                source_uri="https://docs.python.org/3/library/sqlite3.html",
                source_kind="internet",
                source_fact="The bounded source fact distinguishes durable source records from a delayed derived search projection.",
                interpretation="KAIROS should promote the derived projection before the heartbeat can be verified.",
                workspace_id="KAIROS_TEST",
                task_path="tasks/task_TASK_0001.md",
                goal_id="GOAL_KAIROS_001",
                milestone_id="MILESTONE_KAIROS_01",
                criteria=["CRIT_KAIROS_002"],
                loop=1,
            ),
        )
        heartbeat = run_heartbeat(
            self.workspace,
            requested_mode="breadth",
            changed_paths=["research/RESEARCH_9001_L001_V01.md"],
        )
        self.assertTrue(heartbeat["verified"])
        payload = search_database(self.database, question, mode="breadth", record_trace=False)
        self.assertEqual(payload["primary"]["artifact_id"], "RESEARCH_9001_L001_V01")
        self.assertEqual(payload["primary"]["section_id"], "s-fact")


if __name__ == "__main__":
    unittest.main()
