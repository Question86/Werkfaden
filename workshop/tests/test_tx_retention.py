from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workshop" / "src"))
from runtime_sync_workshop.tx_retention import snapshot, compact, rehydrate, enforce_pool_limit, assert_verified_snapshot
from runtime_sync_workshop.util import WorkshopError


class FakeEngine:
    def __init__(self, root):
        self.config = SimpleNamespace(
            state_directory=root / "state", transaction_directory=root / "transactions",
            kairos_workspace=root / "workspace",
            kairos_database=root / "workspace" / ".kairos" / "kairos.db",
            raw={"tx_retention_required": True, "transaction_documents_required": True,
                 "tx_max_materialized": 2},
        )
        self.lease_path = self.config.state_directory / "lease.json"
        self.config.transaction_directory.mkdir(parents=True, exist_ok=True)

    def _transaction_path(self, tx):
        return self.config.transaction_directory / tx

    def _load_transaction(self, tx):
        directory = self._transaction_path(tx)
        return directory, json.loads((directory / "state.json").read_text(encoding="utf-8"))


class TransactionRetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="workshop-vault-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.engine = FakeEngine(self.root)

    def make_tx(self, suffix, state):
        txid = "TXN_" + suffix * 24
        root = self.engine._transaction_path(txid)
        (root / "work").mkdir(parents=True)
        (root / "baseline").mkdir(parents=True)
        (root / "review").mkdir(parents=True)
        (root / "work" / "code.py").write_text("value = " + suffix, encoding="utf-8")
        (root / "baseline" / "code.py").write_text("value = 0", encoding="utf-8")
        (root / "review" / "receipt.json").write_text('{"checked": true}', encoding="utf-8")
        data = {"transaction_id": txid, "state": state,
                "verified_work_package_sha256": "verified-" + suffix}
        (root / "state.json").write_text(json.dumps(data), encoding="utf-8")
        return txid, root

    def test_verified_candidate_snapshots_are_immutable_across_revisions(self):
        txid, root = self.make_tx("a", "SHADOW_VERIFIED")
        initial = snapshot(self.engine, txid)
        (root / "work" / "code.py").write_text("value = changed", encoding="utf-8")
        newer = snapshot(self.engine, txid)
        self.assertNotEqual(initial["sha256"], newer["sha256"])
        self.assertTrue(Path(initial["manifest"]).is_file())
        with patch.dict({}, {}, clear=True):
            assert_verified_snapshot(self.engine, txid)

    def test_compact_requires_projection_and_preserves_all_bytes(self):
        txid, root = self.make_tx("b", "ABORTED")
        with self.assertRaises(WorkshopError) as caught:
            compact(self.engine, txid)
        self.assertIn(caught.exception.code, {"TX_DOCUMENT_MISSING", "TX_DOCUMENT_NOT_PROJECTED"})
        with patch("runtime_sync_workshop.tx_retention._assert_document_projected"), \
                patch("runtime_sync_workshop.tx_documents.update_document"), \
                patch("runtime_sync_workshop.tx_documents.assert_projected"):
            result = compact(self.engine, txid)
        self.assertFalse((root / "work").exists())
        self.assertTrue((root / "state.json").is_file())
        restored = self.root / "rehydrated"
        recovered = rehydrate(self.engine, txid, Path(result["archive"]["manifest"]), restored)
        self.assertGreaterEqual(recovered["restored_files"], 4)
        self.assertEqual((restored / "work" / "code.py").read_text(encoding="utf-8"), "value = b")

    def test_full_pool_cannot_delete_unfinished_tx(self):
        txid_a, root_a = self.make_tx("c", "CHECKED_OUT")
        txid_b, root_b = self.make_tx("d", "CHECKED_OUT")
        with self.assertRaises(WorkshopError) as caught:
            enforce_pool_limit(self.engine)
        self.assertEqual(caught.exception.code, "TX_POOL_CAPACITY")
        self.assertTrue((root_a / "work").is_dir())
        self.assertTrue((root_b / "work").is_dir())

    def test_terminal_tx_can_release_a_slot_after_documented_archive(self):
        txid_a, root_a = self.make_tx("e", "ABORTED")
        txid_b, root_b = self.make_tx("f", "CHECKED_OUT")
        with patch("runtime_sync_workshop.tx_retention._assert_document_projected"), \
                patch("runtime_sync_workshop.tx_documents.update_document"), \
                patch("runtime_sync_workshop.tx_documents.assert_projected"):
            status = enforce_pool_limit(self.engine)
        self.assertEqual(status["materialized"], 1)
        self.assertFalse((root_a / "work").exists())
        self.assertTrue((root_b / "work").exists())

if __name__ == "__main__":
    unittest.main()
