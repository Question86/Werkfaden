from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workshop" / "src"))
from runtime_sync_workshop.util import sqlite_snapshot


class SQLiteWALSnapshotTests(unittest.TestCase):
    def test_uncheckpointed_committed_wal_rows_are_in_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="werkfaden-wal-") as temp:
            source = Path(temp) / "live.db"
            backup = Path(temp) / "candidate-baseline.db"
            connection = sqlite3.connect(source)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("CREATE TABLE work (id INTEGER PRIMARY KEY, claim TEXT NOT NULL)")
                connection.execute("INSERT INTO work(id, claim) VALUES (1,'retained WAL fact')")
                connection.commit()
                self.assertTrue(Path(str(source) + "-wal").is_file())
                sqlite_snapshot(source, backup)
                with closing(sqlite3.connect(backup)) as restored:
                    self.assertEqual(
                        restored.execute("SELECT claim FROM work WHERE id=1").fetchone(),
                        ("retained WAL fact",),
                    )
                    self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
