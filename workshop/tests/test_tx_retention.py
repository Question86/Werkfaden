from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workshop" / "src"))
from runtime_sync_workshop.tx_retention import (
    snapshot, compact, rehydrate, enforce_pool_limit, assert_verified_snapshot,
    recover_interrupted_compactions, storage_inventory,
)
from runtime_sync_workshop.tx_recycling import pool_status, drain, inventory
from runtime_sync_workshop.util import WorkshopError


class FakeEngine:
    def __init__(self, root):
        self.config = SimpleNamespace(
            state_directory=root / "state", transaction_directory=root / "transactions",
            kairos_workspace=root / "workspace",
            kairos_database=root / "workspace" / ".kairos" / "kairos.db",
            raw={"tx_retention_required": True, "transaction_documents_required": True,
                 "tx_max_materialized": 2, "tx_recycle_trigger": 2},
        )
        self.lease_path = self.config.state_directory / "lease.json"
        self.seal_path = self.config.state_directory / "seal.json"
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

    def make_tx(self, suffix, state, *, verified=False):
        txid = "TXN_" + suffix * 24
        root = self.engine._transaction_path(txid)
        (root / "work").mkdir(parents=True)
        (root / "baseline").mkdir(parents=True)
        (root / "review").mkdir(parents=True)
        (root / "work" / "code.py").write_text("value = " + suffix, encoding="utf-8")
        (root / "baseline" / "code.py").write_text("value = 0", encoding="utf-8")
        (root / "review" / "receipt.json").write_text('{"checked": true}', encoding="utf-8")
        data = {"transaction_id": txid, "state": state, "purpose": "Investigate parser correctness and error handling",
                "verified_work_package_sha256": "verified-" + suffix if verified else None}
        (root / "state.json").write_text(json.dumps(data), encoding="utf-8")
        return txid, root

    def compact_mocks(self):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("runtime_sync_workshop.tx_recycling.assert_reviewed"))
        stack.enter_context(patch("runtime_sync_workshop.tx_retention._assert_document_projected"))
        stack.enter_context(patch("runtime_sync_workshop.tx_documents.update_document"))
        stack.enter_context(patch("runtime_sync_workshop.tx_documents.assert_projected"))
        return stack

    def framework_tx(self, suffix="f"):
        import hashlib
        from runtime_sync_workshop.corpus import FRAMEWORK_PROJECTION_SCHEMA
        txid, root = self.make_tx(suffix, "POSTCHECK_VERIFIED", verified=True)
        rows = []
        for name, raw in {"docs/kept.md": b"kept", "docs/changed.md": b"before",
                          "docs/retired.md": b"historical"}.items():
            path = root / "baseline/framework" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            rows.append(dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        for name, raw in {"docs/kept.md": b"kept", "docs/changed.md": b"after",
                          "docs/new.md": b"new"}.items():
            path = root / "work/framework" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        old = root / "baseline/framework-machine/workshop.config.json"
        old.parent.mkdir(parents=True)
        old.write_bytes(b'{"framework_documents":null}')
        current = root / "work/machine/workshop.config.json"
        current.parent.mkdir(parents=True)
        current.write_bytes(b'{"framework_documents":{"namespace":"KAIROS_FRAMEWORK"}}')
        manifest = root / "baseline/manifest.json"
        manifest.write_bytes(b"{}\n")
        scope = dict(schema=FRAMEWORK_PROJECTION_SCHEMA, baseline_product=rows,
                     baseline_machine={"workshop.config.json": hashlib.sha256(old.read_bytes()).hexdigest()},
                     baseline_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        path = root / "review/framework_scope.json"
        path.write_text(json.dumps(scope), encoding="utf-8")
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        state["framework_projection"] = dict(schema=FRAMEWORK_PROJECTION_SCHEMA,
                                             scope_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return txid, root

    def test_framework_prepare_retry_accepts_only_the_exact_selected_native_overlay(self):
        import copy
        import hashlib
        from contextlib import ExitStack
        from runtime_sync_workshop import corpus

        for selected_as in ("sources", "dependent_tests"):
            with self.subTest(selected_as=selected_as):
                root = self.root / selected_as
                live = root / "live"
                work = root / "work/framework"
                (root / "review").mkdir(parents=True)
                live.mkdir()
                work.mkdir(parents=True)
                (live / "code.py").write_bytes(b"old")
                (work / "code.py").write_bytes(b"old")
                (root / "work/code.py").write_bytes(b"native candidate")
                original = [dict(path="code.py", size=3, sha256=hashlib.sha256(b"old").hexdigest())]
                config = SimpleNamespace(codebase_root=live)
                pristine = dict(transaction_id="TXN_retry", framework_projection=dict(overlay_sources={}),
                                sources=[], dependent_tests=[])
                pristine[selected_as] = ["code.py"]
                review = root / "review/framework_review.json"
                review.write_text("{}", encoding="utf-8")

                def inspect_product(config, path, kind):
                    raw = (path / "code.py").read_bytes()
                    return [dict(path="code.py", size=len(raw), sha256=hashlib.sha256(raw).hexdigest())]

                with ExitStack() as stack:
                    stack.enter_context(patch.object(corpus, "_framework_scope", return_value=dict(baseline_product=original)))
                    stack.enter_context(patch.object(corpus, "_framework_inspect", side_effect=inspect_product))
                    builder = stack.enter_context(patch.object(corpus.subprocess, "run", side_effect=RuntimeError("builder-boundary")))
                    with self.assertRaises(WorkshopError) as refused:
                        corpus.framework_projection_prepare(config, root, copy.deepcopy(pristine))
                    self.assertEqual(refused.exception.code, "FRAMEWORK_REVIEW_REQUIRED")
                    builder.assert_not_called()
                    self.assertEqual((live / "code.py").read_bytes(), b"old")
                    self.assertEqual((work / "code.py").read_bytes(), b"native candidate")
                    review.write_text(json.dumps(dict(schema=corpus.FRAMEWORK_PROJECTION_SCHEMA,
                        transaction_id="TXN_retry", reviewer="test", entries=[],
                        reason="No framework Markdown changed in this exact selected-source retry regression.")), encoding="utf-8")
                    # Fresh state on each invocation models the failed outer save.
                    for attempt in range(2):
                        with self.assertRaisesRegex(RuntimeError, "builder-boundary"):
                            corpus.framework_projection_prepare(config, root, copy.deepcopy(pristine))
                    self.assertEqual(builder.call_count, 2)
                    self.assertEqual((work / "code.py").read_bytes(), b"native candidate")
                    # Neither foreign bytes nor a now-unselected native copy may pass.
                    for raw, state in [(b"foreign", copy.deepcopy(pristine)),
                                       (b"native candidate", dict(pristine, sources=[], dependent_tests=[]))]:
                        (work / "code.py").write_bytes(raw)
                        with self.assertRaises(WorkshopError) as refused:
                            corpus.framework_projection_prepare(config, root, state)
                        self.assertEqual(refused.exception.code, "FRAMEWORK_SOURCE_SCOPE_INVALID")
                    self.assertEqual(builder.call_count, 2)
                    self.assertEqual((live / "code.py").read_bytes(), b"old")

    def test_framework_inventory_includes_deleted_members_and_exact_machine_preimage(self):
        import hashlib
        txid, root = self.framework_tx()
        result = inventory(self.engine, txid, persist=False)
        rows = {row["path"]: row for row in result["units"]}
        self.assertEqual(set(rows), {"code.py", "framework/docs/changed.md", "framework/docs/retired.md",
                                    "framework/docs/new.md", "machine/workshop.config.json"})
        retired = rows["framework/docs/retired.md"]
        self.assertEqual(retired["category"], "deleted")
        self.assertEqual(retired["baseline_sha256"], hashlib.sha256(b"historical").hexdigest())
        self.assertIsNone(retired["candidate_sha256"])
        machine = rows["machine/workshop.config.json"]
        self.assertEqual(machine["category"], "modified")
        self.assertEqual(machine["baseline_sha256"], hashlib.sha256(
            (root / "baseline/framework-machine/workshop.config.json").read_bytes()).hexdigest())
        self.assertEqual(rows["framework/docs/new.md"]["category"], "baseline_unknown")
        self.assertEqual(result["inventory_sha256"], inventory(self.engine, txid, persist=False)["inventory_sha256"])

    def test_framework_inventory_does_not_invent_deletion_of_unchanged_machine_authority(self):
        txid, root = self.framework_tx()
        (root / "work/machine/workshop.config.json").unlink()
        result = inventory(self.engine, txid, persist=False)
        self.assertNotIn("machine/workshop.config.json", {row["path"] for row in result["units"]})

    def test_framework_inventory_refuses_missing_changed_or_extra_frozen_baseline(self):
        for suffix, operation in [("a", "missing"), ("b", "changed"), ("c", "extra")]:
            with self.subTest(operation=operation):
                txid, root = self.framework_tx(suffix)
                path = root / "baseline/framework/docs/retired.md"
                if operation == "missing":
                    path.unlink()
                elif operation == "changed":
                    path.write_bytes(b"foreign historical bytes")
                else:
                    (path.parent / "unknown.md").write_bytes(b"not frozen")
                with self.assertRaises(WorkshopError) as caught:
                    inventory(self.engine, txid, persist=False)
                self.assertEqual(caught.exception.code, "FRAMEWORK_RECYCLING_BASELINE_DRIFT")

    def test_framework_inventory_refuses_missing_changed_or_conflicting_machine_preimage(self):
        for suffix, operation in [("a", "missing"), ("b", "changed"), ("c", "conflict")]:
            with self.subTest(operation=operation):
                txid, root = self.framework_tx(suffix)
                path = root / "baseline/framework-machine/workshop.config.json"
                if operation == "missing":
                    path.unlink()
                elif operation == "changed":
                    path.write_bytes(b"foreign config")
                else:
                    path = root / "baseline/machine/workshop.config.json"
                    path.parent.mkdir()
                    path.write_bytes(b"conflicting owner")
                with self.assertRaises(WorkshopError) as caught:
                    inventory(self.engine, txid, persist=False)
                self.assertEqual(caught.exception.code, "FRAMEWORK_RECYCLING_BASELINE_DRIFT")

    def test_framework_inventory_requires_unchanged_frozen_scope_and_manifest(self):
        for suffix, relative in [("a", "review/framework_scope.json"), ("b", "baseline/manifest.json")]:
            with self.subTest(relative=relative):
                txid, root = self.framework_tx(suffix)
                path = root / relative
                path.write_bytes(path.read_bytes() + b" ")
                with self.assertRaises(WorkshopError) as caught:
                    inventory(self.engine, txid, persist=False)
                self.assertEqual(caught.exception.code, "FRAMEWORK_SCOPE_CHANGED")

    def test_legacy_inventory_keeps_unknown_preimages_without_inventing_framework_authority(self):
        txid, root = self.framework_tx()
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        state.pop("framework_projection")
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        rows = {row["path"]: row for row in inventory(self.engine, txid, persist=False)["units"]}
        self.assertNotIn("framework/docs/retired.md", rows)
        self.assertEqual(rows["machine/workshop.config.json"]["category"], "baseline_unknown")

    def _junction(self, link, target):
        if sys.platform != "win32":
            self.skipTest("a real Windows junction is required")
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (AttributeError, OSError) as exc:
            self.skipTest(f"Windows junction creation unavailable: {exc}")
        self.assertEqual(link.lstat().st_reparse_tag, stat.IO_REPARSE_TAG_MOUNT_POINT)
        self.assertEqual(link.resolve(), target.resolve())

        def remove_link():
            self.assertEqual(link.lstat().st_reparse_tag, stat.IO_REPARSE_TAG_MOUNT_POINT)
            self.assertEqual(link.resolve(), target.resolve())
            self.assertTrue(link.parent.resolve().is_relative_to(self.root.resolve()))
            self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
            link.rmdir()

        self.addCleanup(remove_link)
        return link

    def test_windows_junction_descendant_root_and_ancestor_are_rejected(self):
        from runtime_sync_workshop.tx_retention import _safe_files
        from runtime_sync_workshop.tx_recycling import _files
        root = self.root / "source-tree"
        root.mkdir()
        canary = self.root / "canary"
        (canary / "nested").mkdir(parents=True)
        (canary / "nested" / "proof.txt").write_text("keep canary", encoding="utf-8")
        link = self._junction(root / "portal", canary)
        for target in (root, link, link / "nested"):
            with self.subTest(target=target):
                with self.assertRaises(WorkshopError) as caught:
                    list(_safe_files(target))
                self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        with self.assertRaises(WorkshopError) as caught:
            _files(link)
        self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        self.assertEqual((canary / "nested" / "proof.txt").read_text(encoding="utf-8"), "keep canary")

    def test_windows_junction_snapshot_refuses_before_storing_any_file(self):
        txid, root = self.make_tx("a", "ABORTED")
        canary = self.root / "outside"
        canary.mkdir()
        (canary / "proof.txt").write_text("keep canary", encoding="utf-8")
        self._junction(root / "work" / "portal", canary)
        with patch("runtime_sync_workshop.tx_retention._store", side_effect=AssertionError("archive read before path validation")) as store:
            with self.assertRaises(WorkshopError) as caught:
                snapshot(self.engine, txid, include_entire_tx=True)
        self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        store.assert_not_called()
        self.assertTrue((root / "work" / "code.py").is_file())
        self.assertEqual((canary / "proof.txt").read_text(encoding="utf-8"), "keep canary")

    def test_windows_junction_staging_is_refused_before_move_or_delete(self):
        from runtime_sync_workshop.tx_retention import _resume_compaction_locked
        txid, root = self.make_tx("b", "ABORTED")
        canary = self.root / "outside"
        canary.mkdir()
        (canary / "proof.txt").write_text("keep canary", encoding="utf-8")
        self._junction(root / ".reclaim", canary)
        plan = {"manifest_sha256": "test", "reclaimed_worktree_bytes": 0}
        with patch("runtime_sync_workshop.tx_retention._verified_plan", return_value=(plan, self.root / "manifest")), \
             patch("runtime_sync_workshop.tx_retention.os.replace") as move, \
             patch("runtime_sync_workshop.tx_retention.shutil.rmtree") as remove:
            with self.assertRaises(WorkshopError) as caught:
                _resume_compaction_locked(self.engine, txid)
        self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        move.assert_not_called()
        remove.assert_not_called()
        self.assertTrue((root / "work" / "code.py").is_file())
        self.assertEqual((canary / "proof.txt").read_text(encoding="utf-8"), "keep canary")

    def test_windows_junction_transaction_alias_is_rejected_by_pool_and_recovery(self):
        from runtime_sync_workshop.tx_retention import materialized_worktrees
        txid, root = self.make_tx("d", "ABORTED")
        alias_id = "TXN_" + "e" * 24
        self._junction(self.engine.config.transaction_directory / alias_id, root)
        operations = (
            lambda: materialized_worktrees(self.engine),
            lambda: recover_interrupted_compactions(self.engine),
            lambda: storage_inventory(self.engine),
            lambda: snapshot(self.engine, alias_id, include_entire_tx=True),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(WorkshopError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        self.assertTrue((root / "work" / "code.py").is_file())

    def test_windows_junction_recycling_receipt_root_is_rejected(self):
        txid, root = self.make_tx("f", "ABORTED")
        canary = self.root / "outside-receipts"
        canary.mkdir()
        (canary / "proof.txt").write_text("keep canary", encoding="utf-8")
        self.engine.config.state_directory.mkdir(parents=True, exist_ok=True)
        self._junction(self.engine.config.state_directory / "tx-recycling", canary)
        with self.assertRaises(WorkshopError) as caught:
            inventory(self.engine, txid, persist=True)
        self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        self.assertFalse((canary / "inventories").exists())
        self.assertEqual((canary / "proof.txt").read_text(encoding="utf-8"), "keep canary")
        self.assertTrue((root / "work" / "code.py").is_file())

    def test_windows_junction_restore_destination_ancestor_is_rejected(self):
        txid, root = self.make_tx("c", "ABORTED")
        archived = snapshot(self.engine, txid, include_entire_tx=True)
        canary = self.root / "outside"
        canary.mkdir()
        (canary / "proof.txt").write_text("keep canary", encoding="utf-8")
        link = self._junction(self.root / "restore-link", canary)
        with self.assertRaises(WorkshopError) as caught:
            rehydrate(self.engine, txid, Path(archived["manifest"]), link / "restored")
        self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
        self.assertFalse((canary / "restored").exists())
        self.assertEqual((canary / "proof.txt").read_text(encoding="utf-8"), "keep canary")

    def test_storage_census_and_budget_block_only_new_work(self):
        txid, root = self.make_tx("a", "ABORTED")
        self.engine.config.raw["tx_total_storage_budget_bytes"] = 1
        census = storage_inventory(self.engine)
        self.assertTrue(census["over_budget"])
        self.assertGreater(census["totals"]["tx_directory_bytes"], 0)
        with self.assertRaises(WorkshopError) as blocked:
            enforce_pool_limit(self.engine)
        self.assertEqual(blocked.exception.code, "TX_STORAGE_BUDGET_EXCEEDED")
        self.assertTrue((root / "work").is_dir())

    def test_verified_candidate_snapshots_are_immutable_across_revisions(self):
        txid, root = self.make_tx("a", "SHADOW_VERIFIED", verified=True)
        initial = snapshot(self.engine, txid)
        (root / "work" / "code.py").write_text("value = changed", encoding="utf-8")
        newer = snapshot(self.engine, txid)
        self.assertNotEqual(initial["sha256"], newer["sha256"])
        self.assertTrue(Path(initial["manifest"]).is_file())
        assert_verified_snapshot(self.engine, txid)

    def test_compact_requires_recycling_and_preserves_all_bytes(self):
        txid, root = self.make_tx("b", "ABORTED")
        with self.assertRaises(WorkshopError) as caught:
            compact(self.engine, txid)
        self.assertEqual(caught.exception.code, "TX_RECYCLING_REVIEW_REQUIRED")
        with self.compact_mocks():
            result = compact(self.engine, txid)
        self.assertFalse((root / "work").exists())
        self.assertTrue((root / "state.json").is_file())
        restored = self.root / "rehydrated"
        recovered = rehydrate(self.engine, txid, Path(result["archive"]["manifest"]), restored)
        self.assertGreaterEqual(recovered["restored_files"], 4)
        self.assertEqual((restored / "work" / "code.py").read_text(encoding="utf-8"), "value = b")

    def test_pool_blocks_at_trigger_without_erasing_terminal_or_open_work(self):
        txid_a, root_a = self.make_tx("c", "CHECKED_OUT")
        txid_b, root_b = self.make_tx("d", "ABORTED")
        with self.assertRaises(WorkshopError) as caught:
            enforce_pool_limit(self.engine)
        self.assertEqual(caught.exception.code, "TX_RECYCLING_REQUIRED")
        self.assertEqual(caught.exception.details["need_to_retire"], 1)
        self.assertTrue((root_a / "work").is_dir())
        self.assertTrue((root_b / "work").is_dir())

    def test_archived_terminal_releases_slot_after_explicit_review(self):
        txid_a, root_a = self.make_tx("e", "ABORTED")
        txid_b, root_b = self.make_tx("f", "CHECKED_OUT")
        with self.compact_mocks():
            compact(self.engine, txid_a)
        result = enforce_pool_limit(self.engine)
        self.assertFalse(result["required"])
        self.assertEqual(result["materialized_count"], 1)
        self.assertFalse((root_a / "work").exists())
        self.assertTrue((root_b / "work").exists())

    def test_compaction_crash_can_be_retried_without_resnapshotting_partial_tx(self):
        txid, root = self.make_tx("a", "ABORTED")
        with self.compact_mocks():
            with patch("runtime_sync_workshop.tx_retention.shutil.rmtree", side_effect=OSError("synthetic interruption")):
                with self.assertRaisesRegex(OSError, "synthetic interruption"):
                    compact(self.engine, txid)
            self.assertTrue((root / "compaction-plan.json").is_file())
            self.assertTrue((root / ".reclaim" / "work" / "code.py").is_file())
            retry = compact(self.engine, txid)
            self.assertFalse((root / ".reclaim").exists())
        restore = self.root / "crash-restored"
        rehydrate(self.engine, txid, Path(retry["archive"]["manifest"]), restore)
        self.assertEqual((restore / "work" / "code.py").read_text(encoding="utf-8"), "value = a")

    def test_staged_compaction_still_owns_pool_slot_until_projection_commit(self):
        from runtime_sync_workshop.tx_retention import materialized_worktrees
        txid, root = self.make_tx("a", "ABORTED")
        with patch("runtime_sync_workshop.tx_recycling.assert_reviewed"), \
             patch("runtime_sync_workshop.tx_retention._assert_document_projected"), \
             patch("runtime_sync_workshop.tx_documents.update_document"), \
             patch("runtime_sync_workshop.tx_documents.assert_projected",
                   side_effect=WorkshopError("SYNTHETIC_SQL_OUTAGE", "projection interrupted")):
            with self.assertRaises(WorkshopError):
                compact(self.engine, txid)
        self.assertFalse((root / "work").exists())
        self.assertTrue((root / ".reclaim" / "work").exists())
        self.assertEqual(len(materialized_worktrees(self.engine)), 1)
        self.assertFalse((root / "compaction-committed.json").exists())
        with self.compact_mocks():
            recovered = recover_interrupted_compactions(self.engine)
        self.assertEqual(recovered[0]["status"], "RECOVERED")
        self.assertEqual(len(materialized_worktrees(self.engine)), 0)
        with patch("runtime_sync_workshop.tx_retention._verified_plan",
                   side_effect=AssertionError("committed plans must not be rehashed")):
            self.assertEqual(recover_interrupted_compactions(self.engine), [])

    def test_fair_drain_reaches_tail_after_200_blocked_predecessors(self):
        self.engine.config.raw.update({"tx_max_materialized": 10, "tx_recycle_trigger": 9})
        target = None
        for number in range(221):
            txid = "TXN_" + format(number, "024x")
            root = self.engine._transaction_path(txid)
            (root / "work").mkdir(parents=True)
            (root / "baseline").mkdir()
            (root / "review").mkdir()
            (root / "work" / "patch.py").write_text("x = 1", encoding="utf-8")
            payload = {"transaction_id": txid, "state": "ABORTED",
                       "purpose": "Historical individual transaction under review"}
            if number == 220:
                payload["recycling"] = {"status": "REVIEWED"}
                target = root
            (root / "state.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.compact_mocks(), patch("runtime_sync_workshop.tx_documents.assert_projected"):
            for _ in range(3):
                drain(self.engine, max_items=100)
        self.assertIsNotNone(target)
        self.assertFalse((target / "work").exists())

    def test_recovery_queue_rotates_beyond_200_corrupt_predecessors(self):
        from runtime_sync_workshop.tx_retention import recover_interrupted_compactions
        seen = []
        for i in range(220):
            txid = "TXN_" + format(i, "024x")
            root = self.engine._transaction_path(txid)
            root.mkdir(parents=True, exist_ok=True)
            (root / "compaction-plan.json").write_text("{}", encoding="utf-8")
        def recover_one(engine, txid):
            seen.append(txid)
            if txid != "TXN_" + format(219, "024x"):
                raise WorkshopError("SYNTHETIC_CORRUPT", "archived bytes require operator recovery")
            return {"transaction_id": txid, "state": "RECOVERED"}
        with patch("runtime_sync_workshop.tx_retention._resume_compaction_locked", side_effect=recover_one):
            recover_interrupted_compactions(self.engine, max_items=90)
            recover_interrupted_compactions(self.engine, max_items=90)
            final = recover_interrupted_compactions(self.engine, max_items=90)
        # Three bounded batches of 90 may revisit early blockers after reaching
        # the end; fairness means all 220 unique TX were reached, not no replay.
        self.assertEqual(len(set(seen)), 220)
        self.assertLessEqual(len(seen), 270)
        self.assertTrue(any(row["status"] == "RECOVERED" and
                            row["transaction_id"] == "TXN_" + format(219, "024x")
                            for row in final))

    def test_legacy_excess_over_ten_is_batched_without_global_failure(self):
        self.engine.config.raw.update({"tx_max_materialized": 10, "tx_recycle_trigger": 9})
        for i in range(14):
            tid, root = self.make_tx(format(i, "x"), "ABORTED" if i != 13 else "CHECKED_OUT")
            if i == 5:
                (root / "state.json").write_text("{broken json", encoding="utf-8")
            elif i < 12:
                data = json.loads((root / "state.json").read_text(encoding="utf-8"))
                data["recycling"] = {"status": "REVIEWED"}
                (root / "state.json").write_text(json.dumps(data), encoding="utf-8")
        with self.compact_mocks():
            result = drain(self.engine, max_items=100)
        self.assertEqual(result["initial_materialized"], 14)
        self.assertTrue(result["target_reached"])
        self.assertEqual(result["materialized"], 8)
        self.assertEqual(len(result["processed"]), 6)
        self.assertTrue((self.engine._transaction_path("TXN_" + "5" * 24) / "work").exists())
        self.assertTrue((self.engine._transaction_path("TXN_" + "d" * 24) / "work").exists())

    def test_unreviewed_backlog_generates_proposals_and_keeps_bytes(self):
        self.engine.config.raw.update({"tx_max_materialized": 10, "tx_recycle_trigger": 9})
        for i in range(12):
            self.make_tx(format(i, "x"), "ABORTED")
        with patch("runtime_sync_workshop.tx_documents.assert_projected"):
            result = drain(self.engine, max_items=100)
        self.assertEqual(result["materialized"], 12)
        self.assertFalse(result["target_reached"])
        self.assertTrue(result["blocked"])
        self.assertTrue(all("proposal_path" in item for item in result["blocked"]))
        for item in result["blocked"]:
            self.assertTrue(Path(item["proposal_path"]).is_file())
        self.assertEqual(len([root for root in self.engine.config.transaction_directory.iterdir() if (root / "work").is_dir()]), 12)

if __name__ == "__main__":
    unittest.main()
