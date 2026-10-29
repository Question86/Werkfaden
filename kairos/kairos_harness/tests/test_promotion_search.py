from __future__ import annotations

import unittest
import os
import gc
import sqlite3
from contextlib import closing
from unittest.mock import patch

from kairos.database import KnowledgeDatabase
from kairos.heartbeat import run_heartbeat
from kairos.query import compile_query
from kairos.search import search_database, _candidate_rows, fts_match
from kairos.templates import research_document
from kairos.util import atomic_write_text, atomic_write_json, read_json
from kairos.workspace import database_path, initialize_workspace

from support import WorkspaceTestCase


# Independent pre-optimization oracle: preserve columns, weights, joins and LIMIT.
# Do not derive this SQL from the implementation being checked.
_CLASSIC_CANDIDATE_SQL = """
SELECT f.artifact_id, f.section_id, f.title, f.capsule AS search_capsule,
       s.capsule AS section_capsule, a.path, a.document_type, a.state,
       a.authority, a.route, a.goal_id, a.milestone_id, a.task_id,
       a.updated_at, a.claim_boundary,
       bm25(section_fts,0.0,0.0,10.0,8.0,12.0,6.0,4.0,1.0) AS fts_rank
FROM section_fts f
JOIN sections s ON s.artifact_id=f.artifact_id AND s.section_id=f.section_id
JOIN artifacts a ON a.artifact_id=f.artifact_id
WHERE section_fts MATCH ?
ORDER BY fts_rank
LIMIT ?
"""


def _classic_candidates(connection, frame, candidate_limit):
    match = fts_match(frame.tokens)
    if not match:
        return []
    return [dict(row) for row in connection.execute(
        _CLASSIC_CANDIDATE_SQL, (match, candidate_limit)
    )]


class PromotionSearchTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))

    def test_native_rank_preserves_classic_candidates_and_complete_search_payload(self) -> None:
        queries = (
            "Where does workspace orientation begin?",
            "Which authority decides current state?",
            "How do documents become searchable without a full scan?",
            "Why can metadata become stale after an out-of-band document write?",
            "What must TASK_0001 achieve?",
            "reports/report_TASK_0001_L001_V01.md",
            "zznonexistenttermzz",
        )
        with closing(self.database.connect(read_only=True)) as connection:
            for query in queries:
                for budget in (1, 10, 100):
                    with self.subTest(query=query, budget=budget):
                        frame = compile_query(query)
                        self.assertEqual(
                            [dict(row) for row in _candidate_rows(connection, frame, budget)],
                            _classic_candidates(connection, frame, budget),
                        )
        for query in queries:
            for mode in ("breadth", "depth", "work", "breathe", "verify"):
                with self.subTest(query=query, mode=mode):
                    actual = search_database(self.database, query, mode=mode, record_trace=False)
                    with patch("kairos.search._candidate_rows", _classic_candidates):
                        expected = search_database(self.database, query, mode=mode, record_trace=False)
                    actual.pop("elapsed_ms")
                    expected.pop("elapsed_ms")
                    self.assertEqual(actual, expected)

    def test_native_rank_avoids_external_order_sort(self) -> None:
        statements = []
        with closing(self.database.connect(read_only=True)) as connection:
            connection.set_trace_callback(statements.append)
            _candidate_rows(connection, compile_query("workspace authority"), 10)
            connection.set_trace_callback(None)
            sql = next(sql for sql in statements if "FROM section_fts" in sql)
            plan = [row[3].upper() for row in connection.execute("EXPLAIN QUERY PLAN " + sql)]
        self.assertTrue(any("VIRTUAL TABLE" in item for item in plan), plan)
        self.assertFalse(any("TEMP B-TREE FOR ORDER BY" in item for item in plan), plan)

    def test_native_rank_preserves_ties_and_filters_orphans_before_limit(self) -> None:
        # Deliberately corrupt only this disposable test projection. Never
        # promote it or treat injected index rows as positive source evidence.
        with closing(self.database.connect()) as connection:
            identities = [tuple(row) for row in connection.execute(
                "SELECT s.artifact_id,s.section_id FROM sections s "
                "JOIN artifacts a ON a.artifact_id=s.artifact_id "
                "ORDER BY s.artifact_id,s.section_id LIMIT 2"
            )]
            self.assertEqual(len(identities), 2)
            rows = [("MISSING_ARTIFACT", "missing-section"),
                    (identities[0][0], "missing-section"), *identities]
            try:
                for index, identity in enumerate(rows):
                    connection.execute(
                        "INSERT INTO section_fts(rowid,artifact_id,section_id,title,capsule,questions,entities,facets,body) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (-100 + index, *identity, "tieprobezz", "tieprobezz",
                         "tieprobezz", "tieprobezz", "tieprobezz", "tieprobezz"),
                    )
                frame = compile_query("tieprobezz")
                for budget in (1, 2, 10):
                    with self.subTest(budget=budget):
                        actual = [dict(row) for row in _candidate_rows(connection, frame, budget)]
                        self.assertEqual(actual, _classic_candidates(connection, frame, budget))
                        self.assertEqual(len(actual), min(2, budget))
                        self.assertEqual(len({row["fts_rank"] for row in actual}), 1)
                        self.assertTrue(all((row["artifact_id"], row["section_id"]) in identities for row in actual))
            finally:
                connection.rollback()

    def test_initializer_refreshes_legacy_small_corpus_statistics_after_growth(self) -> None:
        original_connect = sqlite3.connect
        class LegacyOptimizeConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                # Reproduce the old native initializer's optimizer selection;
                # all schema, source creation and promotion remain native.
                if sql == "PRAGMA optimize(0x10012)":
                    sql = "PRAGMA optimize"
                return super().execute(sql, *args, **kwargs)
        def legacy_connect(*args, **kwargs):
            kwargs["factory"] = LegacyOptimizeConnection
            return original_connect(*args, **kwargs)
        legacy_workspace = self.workspace / "legacy-growth-fixture"
        with patch("sqlite3.connect", legacy_connect):
            initialize_workspace(legacy_workspace, workspace_id="KAIROS_LEGACY_STATS_TEST")
            config_path = legacy_workspace / ".kairos" / "config.json"
            config = read_json(config_path)
            config["governance_enforcement"] = "audit"
            config["governance_audit_allows_finalization"] = True
            atomic_write_json(config_path, config)
            changed = []
            for number in range(128):
                artifact_id = f"RESEARCH_STATS_{number:04d}_L001_V01"
                relative = f"research/{artifact_id}.md"
                atomic_write_text(legacy_workspace / relative, research_document(
                    research_id=artifact_id, task_id="TASK_0001",
                    title=f"Statistics growth evidence {number}",
                    question=f"What does statistics fixture evidence {artifact_id} establish?",
                    source_uri="https://www.sqlite.org/pragma.html#pragma_optimize",
                    source_kind="internet", source_fact=f"Synthetic growth item {number} for optimizer regression only.",
                    interpretation="This fixture establishes no project or scientific outcome.",
                    workspace_id="KAIROS_LEGACY_STATS_TEST", task_path="tasks/task_TASK_0001.md",
                    goal_id="GOAL_KAIROS_001", milestone_id="MILESTONE_KAIROS_01",
                    criteria=["CRIT_KAIROS_002"], loop=1,
                ))
                changed.append(relative)
            heartbeat = run_heartbeat(legacy_workspace, requested_mode="breadth", changed_paths=changed)
            self.assertTrue(heartbeat["verified"], heartbeat)
        database = KnowledgeDatabase(database_path(legacy_workspace))
        with closing(database.connect(read_only=True)) as connection:
            count = connection.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            before = connection.execute(
                "SELECT stat FROM sqlite_stat1 WHERE idx='sqlite_autoindex_artifacts_1'"
            ).fetchone()
        self.assertGreater(count, 1)
        self.assertTrue(before is None or int(before[0].split()[0]) < count, before)
        database.initialize()
        with closing(database.connect(read_only=True)) as connection:
            after = connection.execute(
                "SELECT stat FROM sqlite_stat1 WHERE idx='sqlite_autoindex_artifacts_1'"
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(int(after[0].split()[0]), count)
            self.assertEqual(connection.execute("SELECT count(*) FROM artifacts").fetchone()[0], count)

    def test_failed_optimizer_does_not_commit_version_marker_and_closes_connection(self) -> None:
        with closing(self.database.connect(read_only=True)) as connection:
            before = tuple(connection.execute(
                "SELECT rowid, value FROM schema_meta WHERE key='schema_version'"
            ).fetchone())
        captured = []
        original_connect = sqlite3.connect
        class FailedOptimizeConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("PRAGMA optimize"):
                    raise sqlite3.OperationalError("isolated optimizer failure")
                return super().execute(sql, *args, **kwargs)
        def failing_connect(*args, **kwargs):
            kwargs["factory"] = FailedOptimizeConnection
            connection = original_connect(*args, **kwargs)
            captured.append(connection)
            self.addCleanup(connection.close)
            return connection
        with patch("sqlite3.connect", failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "isolated optimizer failure"):
                self.database.initialize()
        self.assertEqual(len(captured), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")
        with closing(self.database.connect(read_only=True)) as connection:
            after = tuple(connection.execute(
                "SELECT rowid, value FROM schema_meta WHERE key='schema_version'"
            ).fetchone())
        self.assertEqual(after, before)

    def test_untraced_search_uses_only_closed_read_only_connections(self) -> None:
        captured = []
        original = KnowledgeDatabase.connect
        def capture(database, **kwargs):
            self.assertEqual(kwargs, {"read_only": True})
            connection = original(database, **kwargs)
            self.addCleanup(connection.close)
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            captured.append(connection)
            return connection
        with patch.object(KnowledgeDatabase, "connect", capture):
            with patch.object(KnowledgeDatabase, "initialize", side_effect=AssertionError("read-only search must not initialize")):
                result = search_database(self.database, "workspace authority", record_trace=False)
        self.assertTrue(result["results"])
        self.assertTrue(captured)
        for connection in captured:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_strict_reconciliation_closes_its_real_connection_before_return(self) -> None:
        from kairos.reconcile import reconcile_documents
        captured = []
        original = KnowledgeDatabase.connect
        def capture(database, **kwargs):
            connection = original(database, **kwargs)
            self.addCleanup(connection.close)
            captured.append(connection)
            return connection
        enabled = gc.isenabled()
        gc.disable()
        try:
            with patch.object(KnowledgeDatabase, "connect", capture):
                result = reconcile_documents(self.workspace, verify_content_hashes=True)
            self.assertEqual(len(captured), 1)
            self.assertFalse(result.missing)
            with self.assertRaises(sqlite3.ProgrammingError):
                captured[0].execute("SELECT 1")
        finally:
            if enabled:
                gc.enable()

    def test_strict_reconciliation_closes_connection_when_query_or_iteration_fails(self) -> None:
        from kairos.reconcile import reconcile_documents
        original = KnowledgeDatabase.connect
        for failure in ("query", "iteration"):
            captured = []
            class QueryFailure:
                def __init__(self, connection):
                    self.connection = connection
                def execute(self, sql):
                    if failure == "query":
                        raise RuntimeError("isolated query failure")
                    cursor = self.connection.execute(sql)
                    def rows():
                        yield next(iter(cursor))
                        raise RuntimeError("isolated iteration failure")
                    return rows()
                def close(self):
                    self.connection.close()
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return self.connection.__exit__(*args)
            def capture(database, **kwargs):
                connection = original(database, **kwargs)
                self.addCleanup(connection.close)
                captured.append(connection)
                return QueryFailure(connection)
            enabled = gc.isenabled()
            gc.disable()
            try:
                with self.subTest(failure=failure):
                    with patch.object(KnowledgeDatabase, "connect", capture):
                        with self.assertRaisesRegex(RuntimeError, "isolated " + failure + " failure"):
                            reconcile_documents(self.workspace, verify_content_hashes=True)
                    self.assertEqual(len(captured), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        captured[0].execute("SELECT 1")
            finally:
                if enabled:
                    gc.enable()

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
