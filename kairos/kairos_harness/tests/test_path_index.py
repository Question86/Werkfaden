from __future__ import annotations

import unittest

from kairos.database import KnowledgeDatabase
from kairos.search import _path_rows
from kairos.query import compile_query
from kairos.workspace import database_path
from support import WorkspaceTestCase


class IndexedPathSearchTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))

    def test_promoted_paths_are_indexed_and_discoverable(self) -> None:
        with self.database.connect(read_only=True) as con:
            expected = {
                (row["artifact_id"], row["path"])
                for row in con.execute("SELECT artifact_id,path FROM artifacts")
            }
            observed = {
                (row["artifact_id"], row["path"])
                for row in con.execute("SELECT artifact_id,path FROM artifact_path_fts")
            }
            self.assertTrue(expected)
            self.assertEqual(expected, observed)
            indexed = list(con.execute(
                'SELECT artifact_id FROM artifact_path_fts WHERE artifact_path_fts MATCH ?',
                ('"tasks"',),
            ))
            self.assertTrue(indexed)
            results = _path_rows(con, compile_query("tasks"), 25)
            self.assertTrue(results)

    def test_missing_path_index_is_backfilled_on_one_time_schema_upgrade(self) -> None:
        # Simulate a pre-index derived database, not a user-owned source edit.
        with self.database.transaction() as con:
            con.execute("DELETE FROM artifact_path_fts")
            con.execute(
                "DELETE FROM schema_meta WHERE key='artifact_path_fts_bootstrapped'"
            )
        self.database.initialize()
        with self.database.connect(read_only=True) as con:
            count = con.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            indexed = con.execute("SELECT count(*) FROM artifact_path_fts").fetchone()[0]
            self.assertEqual(count, indexed)

    def test_regular_reinitialization_never_duplicates_path_rows(self) -> None:
        self.database.initialize()
        self.database.initialize()
        with self.database.connect(read_only=True) as con:
            count = con.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            indexed = con.execute("SELECT count(*) FROM artifact_path_fts").fetchone()[0]
        self.assertEqual(count, indexed)


if __name__ == "__main__":
    unittest.main()
