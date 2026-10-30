from __future__ import annotations

import json
import os
import subprocess
import ast
import hashlib
import io
from datetime import datetime
import sqlite3
from contextlib import closing, redirect_stderr
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "workshop" / "src", ROOT / "kairos" / "kairos_harness"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from workshop.tests.test_context_transactions import ContextTransactionTests
from kairos.frontmatter import split_frontmatter
from kairos.transaction_document import document_id, document_path


def _replay_ast_sha256(node):
    """Pin full replay structure without version-dependent empty list fields."""
    def structural(value):
        if isinstance(value, ast.AST):
            return [type(value).__name__,
                    [[name, structural(field)] for name, field in ast.iter_fields(value)
                     if field != []]]
        if isinstance(value, list):
            return [structural(item) for item in value]
        return value

    encoded = json.dumps(structural(node), ensure_ascii=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TransactionDocumentIntegrationTests(unittest.TestCase):
    def test_replay_ast_fingerprint_keeps_semantic_changes_and_ignores_locations(self):
        source = "def read(value):\n    if value:\n        return 1\n    return 0\n"
        original = ast.parse(source).body[0]
        fingerprint = _replay_ast_sha256(original)
        self.assertEqual(fingerprint, _replay_ast_sha256(ast.parse("\n# location only\n" + source).body[0]))
        for changed in (source.replace("if value:", "if not value:"),
                        source.replace("return 1", "return 2"),
                        source.replace("return 0", "raise RuntimeError()")):
            with self.subTest(changed=changed):
                self.assertNotEqual(fingerprint, _replay_ast_sha256(ast.parse(changed).body[0]))

    def test_five_native_kinds_project_every_material_save_and_endreceipt(self):
        from types import SimpleNamespace
        from runtime_sync_workshop.engine import WorkshopEngine
        from workshop.tests.test_authority_migration import AuthorityMigrationTests
        writers = {name: getattr(WorkshopEngine, name)
                   for name in ("_save_transaction", "_save_terminal_history")}
        expected_phases = {
            "normal": {"CREATED", "PREPARED", "VERIFIED", "APPLYING", "POSTCHECKED"},
            "auxiliary": {"CREATED", "VERIFIED", "APPLYING", "POSTCHECKED"},
            "source_set": {"CREATED", "PREPARED", "VERIFIED", "APPLYING", "POSTCHECKED"},
            # Bootstrap stage combines creation/preparation/verification before its first persisted state.
            "bootstrap": {"VERIFIED", "APPLYING", "POSTCHECKED"},
            "authority": {"CREATED", "EDITING", "PREPARED", "VERIFIED", "APPLYING", "POSTCHECKED"},
        }
        for kind, phases in expected_phases.items():
            with self.subTest(kind=kind):
                observed = {}
                endpoints = {}
                receipts = {}

                def wrapper(writer):
                    def save(engine, root, state, *, event, **kwargs):
                        expected_state = state["state"]
                        result = writer(engine, root, state, event=event, **kwargs)
                        fixture = SimpleNamespace(engine=engine,
                            workspace=engine.config.kairos_workspace,
                            database=engine.config.kairos_database)
                        current = self.assert_special_projected(fixture, state["transaction_id"], expected_state)
                        identity = current["tx_event"]
                        rows = observed.setdefault(current["transaction_id"], [])
                        if not rows or rows[-1]["event_id"] != identity["event_id"]:
                            self.assertEqual(identity["event"], event)
                            rows.append(dict(identity))
                        endpoints[current["transaction_id"]] = current
                        terminal = current.get("terminal_receipt", {})
                        if terminal.get("status") == "COMMITTED":
                            receipt_path = engine.config.state_directory / terminal["path"]
                            receipts[current["transaction_id"]] = json.loads(receipt_path.read_text(encoding="utf-8"))
                        return result
                    return save

                with patch.object(WorkshopEngine, "_save_transaction", new=wrapper(writers["_save_transaction"])), \
                     patch.object(WorkshopEngine, "_save_terminal_history", new=wrapper(writers["_save_terminal_history"])):
                    if kind == "normal":
                        fixture, tx = self.prepared_normal_receipt()
                        result = fixture.engine.apply(tx)
                    elif kind == "auxiliary":
                        fixture, owner, tx, _, _ = self.prepared_auxiliary()
                        result = owner.apply(tx)
                    elif kind in ("source_set", "bootstrap"):
                        fixture, owner, tx = self.prepared_special_receipt(kind)
                        result = owner.apply(tx)
                    else:
                        case = AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
                        self.addCleanup(case.doCleanups)
                        case.test_header_add_remove_and_source_change_migrate_atomically()
                self.assertEqual(len(observed), 1)
                tx = next(iter(observed))
                rows, final = observed[tx], endpoints[tx]
                self.assertEqual(final["state"], "POSTCHECK_VERIFIED")
                self.assertEqual(rows, final["tx_event_history"] + [final["tx_event"]])
                self.assertEqual([row["sequence"] for row in rows], list(range(1, len(rows) + 1)))
                self.assertEqual([row["previous_event_id"] for row in rows],
                                 [""] + [row["event_id"] for row in rows[:-1]])
                self.assertTrue(phases.issubset({row["phase"] for row in rows}),
                                (kind, phases, [row["phase"] for row in rows]))
                names = [row["event"] for row in rows]
                self.assertEqual(names.count("TX_TERMINAL_RECEIPT_PENDING"), 1)
                self.assertEqual(names.count("TX_TERMINAL_RECEIPT_COMMITTED"), 1)
                receipt = receipts[tx]
                self.assertIn(receipt["terminal_event_id"], [row["event_id"] for row in rows])
                self.assertEqual(receipt["terminal_payload_sha256"], final["terminal_receipt"]["payload_sha256"])

    def policy_event_fixture(self):
        # Native fixture setup, with no sealed-config or cached-policy edits.
        fixture = ContextTransactionTests("test_runtime_policy_writer_native_roundtrip_and_terminal_replay")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_policy_direct_lifecycle_projects_each_material_event_and_receipt(self):
        fixture = self.policy_event_fixture()
        engine = fixture.engine
        save = engine._save_transaction
        observed = []

        def projected_save(root, state, *, event, **kwargs):
            expected_state = state["state"]
            result = save(root, state, event=event, **kwargs)
            current = self.assert_special_projected(fixture, state["transaction_id"], expected_state)
            self.assertEqual(current["tx_event"]["event"], event)
            observed.append(dict(current["tx_event"]))
            return result

        with patch.object(engine, "_save_transaction", side_effect=projected_save):
            checkout, _ = fixture._policy_candidate()
            tx = checkout["transaction_id"]
            for operation, expected in ((engine.policy_prepare, "PREPARED"),
                                        (engine.policy_verify, "SHADOW_VERIFIED"),
                                        (engine.policy_apply, "POSTCHECK_VERIFIED")):
                result = operation(tx)
                self.assertEqual(result["state"], expected)
                self.assert_special_projected(fixture, tx, expected)
        _, final = engine._load_transaction(tx)
        chain = final["tx_event_history"] + [final["tx_event"]]
        self.assertEqual(observed, chain)
        self.assertEqual([x["sequence"] for x in observed], list(range(1, len(observed) + 1)))
        self.assertEqual([x["previous_event_id"] for x in observed], [""] + [x["event_id"] for x in observed[:-1]])
        names = [x["event"] for x in observed]
        for required in ("POLICY_CHECKOUT_STAGING", "POLICY_CHECKOUT_COMPLETED",
                         "POLICY_PREPARED", "POLICY_VERIFIED", "POLICY_APPLY_STARTED",
                         "POLICY_HEARTBEAT_STARTED", "POLICY_POSTCHECK_VERIFIED",
                         "TX_TERMINAL_RECEIPT_PENDING", "TX_TERMINAL_RECEIPT_COMMITTED"):
            self.assertEqual(names.count(required), 1, names)
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        postcheck = next(x for x in observed if x["event"] == "POLICY_POSTCHECK_VERIFIED")
        self.assertEqual(receipt["terminal_event_id"], postcheck["event_id"])
        self.assertEqual(receipt["terminal_payload_sha256"], final["terminal_receipt"]["payload_sha256"])
        self.assertFalse(engine.lease_path.exists())

    def test_policy_direct_abort_projects_all_preapply_states_with_pending_identity(self):
        from runtime_sync_workshop.engine import RuntimePolicyTransaction
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        for target_state in ("CHECKOUT_STAGING", "CHECKED_OUT", "PREPARED", "SHADOW_VERIFIED"):
            for pending in (False, True):
                with self.subTest(state=target_state, pending=pending):
                    fixture = self.policy_event_fixture()
                    engine = fixture.engine
                    before = engine.config.config_path.read_bytes()
                    if target_state == "CHECKOUT_STAGING":
                        plan = engine.policy_activation_plan("mandatory-runtime/v1")
                        with patch.object(RuntimePolicyTransaction, "_baseline",
                                          side_effect=OSError("Stop after the actual native staging event")):
                            with self.assertRaises(OSError):
                                engine.policy_checkout("mandatory-runtime/v1", expected_plan_sha256=plan["plan_sha256"],
                                                       purpose="Exercise safe policy abort after native staging admission")
                        lease = json.loads(engine.lease_path.read_text(encoding="utf-8"))
                        tx = lease["transaction_id"]
                    else:
                        checkout, _ = fixture._policy_candidate()
                        tx = checkout["transaction_id"]
                        if target_state in ("PREPARED", "SHADOW_VERIFIED"):
                            engine.policy_prepare(tx)
                        if target_state == "SHADOW_VERIFIED":
                            engine.policy_verify(tx)
                    self.assert_special_projected(fixture, tx, target_state)
                    if pending:
                        with patch("runtime_sync_workshop.tx_documents.publish",
                                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Policy abort pending-event fixture")):
                            with self.assertRaises(WorkshopError):
                                engine.annotate_transaction(tx, kind="observation",
                                    summary="Retain the exact policy pending event before safe direct abort",
                                    evidence="native policy abort matrix fixture")
                        marker = json.loads(pending_path(engine, tx).read_text(encoding="utf-8"))
                        with self.assertRaises(WorkshopError) as blocked:
                            engine.checkout(["app.py"], purpose="New work must refuse this exact pending policy event")
                        self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                        self.assertEqual(blocked.exception.references["items"][0]["event_id"], marker["event_id"])
                    _, previous = engine._load_transaction(tx)
                    with patch.object(engine, "policy_apply", side_effect=AssertionError("Abort cannot Apply")):
                        result = engine.policy_abort(tx)
                    self.assertEqual(result["state"], "ABORTED")
                    final = self.assert_special_projected(fixture, tx, "ABORTED")
                    self.assertEqual(final["tx_event"]["event"], "POLICY_ABORTED")
                    self.assertEqual(final["tx_event"]["previous_event_id"], previous["tx_event_id"])
                    self.assertGreater(final["tx_event_sequence"], previous["tx_event_sequence"])
                    self.assertEqual(engine.config.config_path.read_bytes(), before)
                    self.assertFalse(pending_path(engine, tx).exists())
                    self.assertFalse(engine.lease_path.exists())
                    self.assertTrue(engine.status(persist=False)["verified"])

    def test_policy_rollback_and_baseline_recovery_project_exact_event_without_apply(self):
        from runtime_sync_workshop import util
        from runtime_sync_workshop.engine import WorkshopEngine
        class Interrupted(BaseException):
            pass
        for failure in (OSError, Interrupted):
            with self.subTest(failure=failure.__name__):
                fixture = self.policy_event_fixture()
                checkout, _ = fixture._verified_policy()
                engine = fixture.engine
                tx = checkout["transaction_id"]
                path = engine.config.config_path
                before, stamp = path.read_bytes(), path.stat().st_mtime_ns
                original = util.atomic_write_bytes
                fired = []
                def stop_write(destination, data):
                    if Path(destination) == path and not fired:
                        fired.append(True)
                        raise failure("Stop at native policy config replacement")
                    return original(destination, data)
                with patch.object(util, "atomic_write_bytes", side_effect=stop_write):
                    with self.assertRaises(failure):
                        engine.policy_apply(tx)
                self.assertEqual(fired, [True])
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                if failure is Interrupted:
                    self.assert_special_projected(fixture, tx, "APPLYING")
                    fresh = WorkshopEngine(path)
                    with patch.object(fresh, "policy_apply", side_effect=AssertionError("Recovery cannot reapply")):
                        self.assertEqual(fresh.recover(tx)["state"], "ROLLED_BACK")
                    fixture.engine = fresh
                    expected_event = "POLICY_BASELINE_RECOVERED"
                else:
                    expected_event = "POLICY_ROLLED_BACK"
                final = self.assert_special_projected(fixture, tx, "ROLLED_BACK")
                self.assertEqual(final["tx_event"]["event"], expected_event)
                self.assertEqual(sum(x["event"] == "POLICY_APPLY_STARTED" for x in final["journal"]), 1)
                self.assertFalse(fixture.engine.lease_path.exists())

    def enter_native_phase_fixture(self, workspace, phase):
        from kairos.governance import (runtime_scope, issue_action_permit,
                                       consume_action_permit, finish_cli_action)
        scope = runtime_scope(workspace)
        permit = issue_action_permit(
            workspace, phase=phase, command_name="isolated-phase-admission-probe",
            scope=scope, reason="Enter a native phase solely to test external publication admission.")
        action = consume_action_permit(
            workspace, permit_id=permit["permit_id"],
            command_name="isolated-phase-admission-probe", phase=phase,
            expected_scope=scope)
        finish_cli_action(workspace, action, success=True,
                          result={"scope": "native phase admission only; no task closure is claimed"})

    def test_phase_refusal_keeps_pending_event_repairable_after_real_context_query(self):
        from kairos.cli import build_parser, execute
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(5)
        engine = fixture.engine
        inspection = engine.inspect_recycling(tx)
        proposal = self.fill_recycling_page(inspection)
        document = fixture.workspace / document_path(tx)
        before_source = document.read_bytes()
        self.enter_native_phase_fixture(fixture.workspace, "VERIFICATION")
        self.enter_native_phase_fixture(fixture.workspace, "CLOSURE")
        with closing(sqlite3.connect(fixture.database)) as connection:
            grants_before = connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit'").fetchone()[0]
            revoked_before = connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit' AND status='REVOKED'").fetchone()[0]
        with self.assertRaises(WorkshopError) as refusal:
            engine.submit_recycling(tx, proposal)
        self.assertEqual(refusal.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        state = engine.transaction_status(tx)
        pending_event = state["tx_event_id"]
        self.assertTrue(pending_path(engine, tx).is_file())
        self.assertEqual(document.read_bytes(), before_source)
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit'").fetchone()[0], grants_before)
        result, status = execute(build_parser().parse_args([
            "search", "Which source owns the current transaction recycling procedure?",
            "--workspace", str(fixture.workspace)]))
        self.assertEqual(status, 0)
        self.assertTrue(result["routing_receipt"]["routing_receipt_id"].startswith("SRR_"))
        with patch.object(engine, "apply", side_effect=AssertionError("Recovery must never repeat Apply")):
            self.assertEqual(engine.submit_recycling(tx, proposal)["status"], "ALREADY_REVIEWED")
        self.assertEqual(engine.transaction_status(tx)["tx_event_id"], pending_event)
        self.assertFalse(pending_path(engine, tx).exists())
        assert_projected(engine, tx)
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit' AND status='REVOKED'").fetchone()[0], revoked_before)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM quarantine_entries WHERE status='OPEN'").fetchone()[0], 0)
        self.assertTrue(engine.compact_transaction(tx)["archive"]["complete"])

    def test_phase_preflight_never_revives_an_explicitly_revoked_publication(self):
        from kairos.governance import (GovernanceError, issue_external_edit_permit,
                                       revoke_action_permit)
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        path = fixture.workspace / document_path(tx)
        before = path.read_bytes()
        self.enter_native_phase_fixture(fixture.workspace, "VERIFICATION")
        arguments = dict(paths=[document_path(tx)],
                         reason="Exact unchanged native source for a revocation regression.",
                         publication_id="EXPLICIT_REVOKED_PUBLICATION_PROBE",
                         expected_sha256={document_path(tx): hashlib.sha256(before).hexdigest()})
        permit = issue_external_edit_permit(fixture.workspace, **arguments)
        revoke_action_permit(fixture.workspace, permit_id=permit["permit_id"],
                             reason="Explicitly withdraw this unused fixture publication authority.")
        with self.assertRaises(GovernanceError) as refusal:
            issue_external_edit_permit(fixture.workspace, **arguments)
        self.assertIn("not resumable", str(refusal.exception))
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM action_permits WHERE permit_id=?", (permit["permit_id"],)).fetchone()[0], "REVOKED")

    def test_external_publication_refuses_closure_before_issuing_write_grant(self):
        from kairos.governance import GovernanceError, issue_external_edit_permit
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        path = fixture.workspace / document_path(tx)
        before_source = path.read_bytes()
        self.enter_native_phase_fixture(fixture.workspace, "VERIFICATION")
        self.enter_native_phase_fixture(fixture.workspace, "CLOSURE")
        with closing(sqlite3.connect(fixture.database)) as connection:
            before = connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit'").fetchone()[0]
        with self.assertRaises(GovernanceError) as refusal:
            issue_external_edit_permit(
                fixture.workspace, paths=[document_path(tx)],
                reason="A closure phase must not create an external source write grant.",
                publication_id="NATIVE_PHASE_PREFLIGHT_PROBE",
                expected_sha256={document_path(tx): hashlib.sha256(before_source).hexdigest()})
        self.assertIn("CLOSURE -> MUTATION", str(refusal.exception))
        self.assertEqual(path.read_bytes(), before_source)
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit'").fetchone()[0], before)
            self.assertEqual(connection.execute(
                "SELECT current_phase FROM governance_state WHERE singleton_id=1").fetchone()[0], "CLOSURE")

    def test_small_explicit_paging_keeps_native_completion_across_restart(self):
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(5)
        engine = fixture.engine
        legacy = engine.inspect_recycling(tx)
        self.assertEqual(json.loads(Path(legacy["proposal_path"]).read_text())["schema"],
                         "werkfaden-tx-recycling-analysis/v1")
        first = engine.inspect_recycling(tx, chunk_index=0, chunk_size=2)
        self.assertEqual(first["changed_unit_count"], 5)
        engine.submit_recycling(tx, self.fill_recycling_page(first))
        engine = WorkshopEngine(engine.config.config_path)
        incomplete = engine.inspect_recycling(tx)
        self.assertEqual(json.loads(Path(incomplete["proposal_path"]).read_text())["schema"],
                         "werkfaden-tx-recycling-analysis/v2")
        self.assertFalse(incomplete["coverage_complete"])
        self.assertEqual(incomplete["remaining_unit_count"], 3)
        with self.assertRaises(WorkshopError) as refusal:
            engine.submit_recycling(tx, self.fill_recycling_page(incomplete))
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_CHUNK_COVERAGE")
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM tx_recycling_reviews").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM tx_recycling_chunk_atoms WHERE parent_reviewed<>0").fetchone()[0], 0)
        second = engine.inspect_recycling(tx, chunk_index=1, chunk_size=3)
        engine.submit_recycling(tx, self.fill_recycling_page(second))
        completion = WorkshopEngine(engine.config.config_path).inspect_recycling(tx)
        self.assertTrue(completion["coverage_complete"])
        self.assertEqual(json.loads(Path(completion["proposal_path"]).read_text())["schema"],
                         "werkfaden-tx-recycling-analysis/v2")
        self.assertEqual(engine.submit_recycling(tx, self.fill_recycling_page(completion))["status"], "REVIEWED")
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM tx_recycling_chunk_atoms WHERE parent_reviewed=1").fetchone()[0], 5)
            self.assertEqual(connection.execute("SELECT count(*) FROM tx_recycling_chunks WHERE status='PARTIAL'").fetchone()[0], 2)
        self.assertTrue(engine.compact_transaction(tx)["archive"]["complete"])

    def test_paged_recycling_upgrades_actual_legacy_schema_once_through_native_owner(self):
        from tempfile import TemporaryDirectory
        import kairos.database as database_module
        from kairos.database import KnowledgeDatabase
        from runtime_sync_workshop.tx_documents import _ensure_chunk_projection_schema
        # Reconstruct the exact pre-change DDL, without altering an existing DB.
        legacy = database_module.SCHEMA_SQL
        start = legacy.index("-- Immutable bounded intermediate sources.")
        stop = legacy.index("CREATE TABLE IF NOT EXISTS tx_recycling_atoms (", start)
        legacy = legacy[:start] + legacy[stop:]
        start = legacy.index("-- A query view, not another copy")
        stop = legacy.index("CREATE TABLE IF NOT EXISTS search_policy_violations", start)
        legacy = legacy[:start] + legacy[stop:]
        normalized = "\n".join(line.rstrip() for line in legacy.splitlines() if line.strip())
        self.assertEqual(hashlib.sha256(normalized.encode()).hexdigest(),
                         "051acd5cf663b3fd1ba69437a606794c2b787eb4e872d5162ab116e2c279cd4b")
        with TemporaryDirectory(prefix="legacy-chunk-schema-") as temporary:
            database = KnowledgeDatabase(Path(temporary) / "knowledge.db")
            with patch.object(database_module, "SCHEMA_SQL", legacy):
                database.initialize()
            def names():
                with closing(database.connect(read_only=True)) as connection:
                    return {row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master")}
            self.assertNotIn("tx_recycling_chunks", names())
            # Merely opening a read-only connection must not migrate anything.
            self.assertNotIn("tx_recycling_chunk_atoms", names())
            _ensure_chunk_projection_schema(database)
            self.assertTrue({"tx_recycling_chunks", "tx_recycling_chunk_atoms"} <= names())
            with closing(database.connect(read_only=True)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM tx_recycling_chunks").fetchone()[0], 0)
            with patch.object(database, "initialize", side_effect=AssertionError("redundant migration")):
                _ensure_chunk_projection_schema(database)

    def test_paged_recycling_source_budget_refuses_before_commit_and_allows_smaller_chunk(self):
        from runtime_sync_workshop.tx_recycling import _chunk_path, inventory
        from runtime_sync_workshop.tx_documents import assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(513)
        engine = fixture.engine
        root, _ = engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        # Preserve the legacy512 input limit; paging is a separate typed route.
        legacy_path = self.fill_recycling_page(engine.inspect_recycling(tx))
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        legacy.update(schema="werkfaden-tx-recycling-analysis/v1",
                      decisions=[{"unit_id": row["unit_id"]} for row in inventory(engine, tx, persist=False)["units"]])
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertRaises(WorkshopError) as refusal:
            engine.submit_recycling(tx, legacy_path)
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_DECISION_LIMIT")
        first = engine.inspect_recycling(tx, chunk_index=0)
        path = self.fill_recycling_page(first)
        packet = json.loads(path.read_text(encoding="utf-8"))
        prefix = "Quoted diagnostic rationale to stress the unchanged byte budget: "
        reason = prefix + '"' * (2000 - len(prefix))
        for row in packet["decisions"]:
            row["reason"] = reason
        path.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(WorkshopError) as refusal:
            engine.submit_recycling(tx, path)
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_CHUNK_DOCUMENT_LIMIT")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertFalse(_chunk_path(root, first["inventory_sha256"], 0).exists())
        smaller = engine.inspect_recycling(tx, chunk_index=0, chunk_size=128)
        small_path = self.fill_recycling_page(smaller)
        small = json.loads(small_path.read_text(encoding="utf-8"))
        for row in small["decisions"]:
            row["reason"] = reason
        small_path.write_text(json.dumps(small), encoding="utf-8")
        self.assertEqual(engine.submit_recycling(tx, small_path)["status"], "CHUNK_RECORDED")
        self.assertEqual(engine.transaction_status(tx)["recycling_progress"]["chunks"][0]["range"], [0, 128])
        assert_projected(engine, tx)
        with closing(sqlite3.connect(fixture.database)) as con:
            relative = con.execute("SELECT source_path FROM tx_recycling_chunks").fetchone()[0]
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_reviews").fetchone()[0], 0)
        self.assertLess((fixture.workspace / relative).stat().st_size, 2097152)

    def exercise_large_paged_review(self, count, size):
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import assert_projected
        from kairos.frontmatter import split_frontmatter
        from kairos.sections import parse_sections
        from kairos.transaction_document import document_path
        fixture, tx = self.large_recycling_fixture(count)
        engine = fixture.engine
        index = 0
        global_hash = None
        while True:
            inspection = engine.inspect_recycling(tx, chunk_index=index, chunk_size=size)
            if global_hash is None:
                global_hash = inspection["inventory_sha256"]
            self.assertEqual(inspection["inventory_sha256"], global_hash)
            self.assertGreater(len(inspection["units"]), 0)
            self.assertLessEqual(len(inspection["units"]), 512)
            result = engine.submit_recycling(tx, self.fill_recycling_page(inspection))
            self.assertEqual(result["status"], "CHUNK_RECORDED")
            assert_projected(engine, tx)
            engine = WorkshopEngine(engine.config.config_path)
            index += 1
            if engine.inspect_recycling(tx)["remaining_unit_count"] == 0:
                break
        completion = engine.inspect_recycling(tx)
        self.assertEqual(engine.submit_recycling(tx, self.fill_recycling_page(completion))["status"], "REVIEWED")
        assert_projected(engine, tx)
        raw = (fixture.workspace / document_path(tx)).read_bytes()
        self.assertLess(len(raw), 2097152)
        parsed = split_frontmatter(raw.decode())
        self.assertEqual(len(parse_sections(parsed.body, header_bytes=parsed.header_bytes)), 18)
        with closing(sqlite3.connect(fixture.database)) as con:
            self.assertEqual(con.execute("SELECT decision_count FROM tx_recycling_reviews").fetchone()[0], count)
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_atoms").fetchone()[0], count)
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_chunks").fetchone()[0], index)
        self.assertTrue(engine.compact_transaction(tx)["archive"]["complete"])

    def test_paged_recycling_real_1025_with_smaller_ranges_and_fresh_engines(self):
        self.exercise_large_paged_review(1025, 256)

    def test_paged_recycling_real_4097_beyond_legacy_projection_cap(self):
        self.exercise_large_paged_review(4097, 512)

    def test_paged_recycling_publication_outage_recovers_exact_partial_event(self):
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(513)
        engine = fixture.engine
        page = engine.inspect_recycling(tx, chunk_index=0)
        proposal = self.fill_recycling_page(page)
        with patch("kairos.heartbeat.run_heartbeat", side_effect=OSError("Injected subordinate source heartbeat outage")):
            with self.assertRaises(WorkshopError) as refusal:
                engine.submit_recycling(tx, proposal)
        self.assertEqual(refusal.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        event = engine.transaction_status(tx)["tx_event_id"]
        self.assertEqual(json.loads(pending_path(engine, tx).read_bytes())["event_id"], event)
        fresh = WorkshopEngine(engine.config.config_path)
        with self.assertRaises(WorkshopError):
            fresh.checkout(["app.py"], purpose="Refuse work until the real partial event is projected")
        with patch.object(fresh, "apply", side_effect=AssertionError("Reconcile must not repeat Apply")):
            fresh.reconcile_transaction_document(tx)
        assert_projected(fresh, tx)
        self.assertEqual(fresh.transaction_status(tx)["tx_event_id"], event)
        self.assertEqual(fresh.submit_recycling(tx, proposal)["status"], "CHUNK_ALREADY_RECORDED")
        self.assertEqual(fresh.transaction_status(tx)["tx_event_id"], event)
        self.assertFalse(pending_path(fresh, tx).exists())
        self.assertNotEqual((fresh.transaction_status(tx).get("recycling") or {}).get("status"), "REVIEWED")

    def test_paged_recycling_complete_manifest_rejects_gaps_duplicates_and_drift(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(513)
        engine = fixture.engine
        for invalid in (0, 513, True, "2"):
            with self.assertRaises(WorkshopError):
                engine.inspect_recycling(tx, chunk_index=0, chunk_size=invalid)
        for index in range(2):
            engine.submit_recycling(tx, self.fill_recycling_page(engine.inspect_recycling(tx, chunk_index=index)))
        path = self.fill_recycling_page(engine.inspect_recycling(tx))
        original = json.loads(path.read_text(encoding="utf-8"))
        root, _ = engine._load_transaction(tx)
        state_bytes = (root / "state.json").read_bytes()
        variants = [
            {**original, "chunks": original["chunks"][:-1]},
            {**original, "chunks": [original["chunks"][0], original["chunks"][0]]},
            {**original, "chunks": list(reversed(original["chunks"]))},
            {**original, "inventory_sha256": "b" * 64},
            {**original, "decisions": []},
            {**original, "chunks": [{**original["chunks"][0], "receipt_sha256": "f" * 64}, original["chunks"][1]]},
        ]
        for variant in variants:
            path.write_text(json.dumps(variant), encoding="utf-8")
            with self.assertRaises(WorkshopError):
                engine.submit_recycling(tx, path)
            self.assertEqual((root / "state.json").read_bytes(), state_bytes)
        path.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(engine.submit_recycling(tx, path)["status"], "REVIEWED")

    def test_paged_recycling_orphan_receipt_replay_and_source_tampering_refuse(self):
        from runtime_sync_workshop.tx_recycling import _chunk_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.large_recycling_fixture(513)
        engine = fixture.engine
        page = engine.inspect_recycling(tx, chunk_index=0)
        proposal = self.fill_recycling_page(page)
        root, _ = engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        with patch.object(engine, "_save_terminal_history", side_effect=OSError("Injected crash after receipt before native state")):
            with self.assertRaises(OSError):
                engine.submit_recycling(tx, proposal)
        self.assertEqual((root / "state.json").read_bytes(), before)
        packet_path = _chunk_path(root, page["inventory_sha256"], 0)
        packet_bytes = packet_path.read_bytes()
        self.assertEqual(engine.submit_recycling(tx, proposal)["status"], "CHUNK_RECORDED")
        self.assertEqual(packet_path.read_bytes(), packet_bytes)
        packet_path.write_bytes(packet_bytes + b" ")
        with self.assertRaises(WorkshopError):
            engine.inspect_recycling(tx)
        packet_path.write_bytes(packet_bytes)
        final = self.fill_recycling_page(engine.inspect_recycling(tx))
        with closing(sqlite3.connect(fixture.database)) as con:
            relative = con.execute("SELECT source_path FROM tx_recycling_chunks").fetchone()[0]
        source = fixture.workspace / relative
        original = source.read_bytes()
        source.write_bytes(original + b"\n")
        with self.assertRaises(WorkshopError):
            engine.submit_recycling(tx, final)
        source.write_bytes(original)
        changed = next((root / "work/runtime").glob("page-*.bin"))
        previous = changed.read_bytes()
        changed.write_bytes(previous + b"changed")
        with self.assertRaises(WorkshopError):
            engine.inspect_recycling(tx)
        changed.write_bytes(previous)
        self.assertNotEqual((engine.transaction_status(tx).get("recycling") or {}).get("status"), "REVIEWED")

    def large_recycling_fixture(self, count):
        fixture = self.fixture()
        checkout = fixture.checkout()
        root = Path(checkout["work_directory"])
        for index in range(count - 1):
            path = root / "runtime" / f"page-{index:05d}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            # Repeated payloads deliberately prove that distinct path-bound
            # File-Change IDs survive object deduplication without collapsing.
            path.write_bytes(f"bounded diagnostic fixture payload {index % 8}\n".encode())
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        return fixture, tx

    def fill_recycling_page(self, inspection):
        path = Path(inspection["proposal_path"])
        proposal = json.loads(path.read_text(encoding="utf-8"))
        proposal.update(
            planned_approach="Review the exact global inventory as bounded immutable source fragments.",
            attempted_approach="Use the native page protocol and retain each exact diagnostic fixture file.",
            outcome_reason="Keep attributable per-file evidence; partial chunks never grant global recycling clearance.")
        for row in proposal.get("decisions", []):
            row.update(disposition="RETAIN_EVIDENCE",
                       reason="Exact diagnostic fixture bytes are retained as bounded historical evidence, not reused or deployed.",
                       evidence_refs=["TX:baseline/manifest.json"])
        path.write_text(json.dumps(proposal, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return path

    def test_paged_recycling_real_513_partial_sources_and_complete_receipt(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        from runtime_sync_workshop.util import WorkshopError
        from kairos.graph import inventory as graph_inventory
        from runtime_sync_workshop.cli import _parser, execute
        fixture, tx = self.large_recycling_fixture(513)
        engine = fixture.engine
        first = engine.inspect_recycling(tx, chunk_index=0)
        self.assertEqual(first["changed_unit_count"], 513)
        self.assertEqual(len(first["units"]), 512)
        result = engine.submit_recycling(tx, self.fill_recycling_page(first))
        self.assertEqual(result["status"], "CHUNK_RECORDED")
        self.assertNotEqual((engine.transaction_status(tx).get("recycling") or {}).get("status"), "REVIEWED")
        assert_projected(engine, tx)
        with closing(sqlite3.connect(fixture.database)) as con:
            con.row_factory = sqlite3.Row
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_reviews").fetchone()[0], 0)
            rows = graph_inventory(con, "tx_chunks", field="transaction_id", value=tx)
            self.assertEqual(rows["total"], 1)
            self.assertEqual(rows["rows"][0]["status"], "PARTIAL")
            self.assertEqual(rows["rows"][0]["decision_count"], 512)
            found = graph_inventory(con, "tx_chunk_atoms", field="path", value="runtime/page-00100.bin")
            self.assertEqual(found["total"], 1)
            self.assertEqual(found["rows"][0]["status"], "PARTIAL")
            self.assertEqual(found["rows"][0]["parent_reviewed"], 0)
        with self.assertRaises(WorkshopError) as refusal:
            engine.compact_transaction(tx)
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_REVIEW_REQUIRED")
        incomplete = engine.inspect_recycling(tx)
        with self.assertRaises(WorkshopError) as refusal:
            engine.submit_recycling(tx, self.fill_recycling_page(incomplete))
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_CHUNK_COVERAGE")
        prefix = ["--config", str(engine.config.config_path)]
        final_page = execute(_parser().parse_args(prefix + ["tx-recycle-inspect", tx, "--chunk-index", "1"]))
        self.assertEqual(len(final_page["units"]), 1)
        execute(_parser().parse_args(prefix + ["tx-recycle-submit", tx, "--analysis", str(self.fill_recycling_page(final_page))]))
        completion = engine.inspect_recycling(tx)
        result = engine.submit_recycling(tx, self.fill_recycling_page(completion))
        self.assertEqual(result["status"], "REVIEWED")
        assert_projected(engine, tx)
        with closing(sqlite3.connect(fixture.database)) as con:
            self.assertEqual(con.execute("SELECT decision_count FROM tx_recycling_reviews").fetchone()[0], 513)
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_atoms").fetchone()[0], 513)
            self.assertEqual(con.execute("SELECT count(*) FROM tx_recycling_chunks").fetchone()[0], 2)
        self.assertTrue(engine.compact_transaction(tx)["archive"]["complete"])

    def test_paged_recycling_geometry_retains_global_identity_and_strict_ranges(self):
        from runtime_sync_workshop.tx_recycling import _page, _matches_page
        from runtime_sync_workshop.util import WorkshopError
        for count in (1, 512, 513, 1025, 4097):
            manifest = {"inventory_sha256": "a" * 64,
                        "units": [{"unit_id": f"FILE_CHANGE_{i:032X}"} for i in range(count)]}
            covered = []
            for index in range((count + 511) // 512):
                page = _page(manifest, index)
                self.assertEqual(page["inventory_sha256"], manifest["inventory_sha256"])
                self.assertEqual(page["range"], [index * 512, min(count, (index + 1) * 512)])
                self.assertLessEqual(len(page["unit_ids"]), 512)
                self.assertTrue(_matches_page(page, page))
                self.assertFalse(_matches_page({**page, "range": [False, page["range"][1]]}, page))
                other = _page({**manifest, "inventory_sha256": "b" * 64}, index)
                self.assertNotEqual(page["chunk_sha256"], other["chunk_sha256"])
                covered.extend(page["unit_ids"])
            self.assertEqual(covered, [row["unit_id"] for row in manifest["units"]])
            with self.assertRaises(WorkshopError):
                _page(manifest, (count + 511) // 512)
            for invalid in (True, False, -1, 1.0, "0", None):
                with self.subTest(count=count, invalid=invalid), self.assertRaises(WorkshopError):
                    _page(manifest, invalid)

    def test_verified_workshop_method_migration_preserves_native_timestamp_and_intake(self):
        for version, previous in ((2, self._history_method_v2_fixture()),
                                  (3, self._history_method_v3_fixture())):
            with self.subTest(previous_version=version):
                self._verified_workshop_method_migration(previous)

    def _verified_workshop_method_migration(self, previous):
        from kairos import governance
        from kairos import transaction_document as renderer
        from runtime_sync_workshop.tx_documents import ensure_history_contract
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        expected = renderer.render_history_contract("KAIROS_CONTEXT_TEST")
        self.assertIn(hashlib.sha256(previous.encode("utf-8")).hexdigest(), {
            "fd2f0e142783ec62ba4d8a4d0b89b5caa6dc3ed3eadb40ae9855392886521a55",
            "c114bd65438155443a1a5657eb4d5b79c965f11e8238c79b840ba9fef9446904"})
        with patch.object(renderer, "render_history_contract", return_value=previous):
            ensure_history_contract(fixture.engine)
        method = fixture.workspace / renderer.HISTORY_METHOD_PATH
        self.assertEqual(method.read_bytes(), previous.encode("utf-8"))
        with self.assertRaises(WorkshopError) as refusal:
            ensure_history_contract(fixture.engine)
        self.assertEqual(refusal.exception.code, "TX_HISTORY_METHOD_DRIFT")

        # Each native document migration advances exactly one revision; no gate waiver.
        candidates = ([self._history_method_v3_fixture()]
                      if split_frontmatter(previous).metadata["revision"] == 2 else []) + [expected]
        for candidate in candidates:
            checkout = fixture.checkout(documents=(renderer.HISTORY_METHOD_PATH,))
            work = Path(checkout["work_directory"])
            (work / "app.py").write_bytes(b"value = 1\n")  # Actual documentation-only migration.
            (work / "documents" / renderer.HISTORY_METHOD_PATH).write_bytes(candidate.encode("utf-8"))
            fixture.review_document(checkout, renderer.HISTORY_METHOD_PATH)
            tx = checkout["transaction_id"]
            prepared = fixture.engine.prepare(tx)
            self.assertFalse(prepared["metadata_aftercheck"]["required"])
            self.assertEqual(fixture.engine.verify(tx)["state"], "SHADOW_VERIFIED")
            self.assertEqual(fixture.engine.apply(tx)["state"], "POSTCHECK_VERIFIED")
            metadata = split_frontmatter(method.read_text(encoding="utf-8")).metadata
            old_metadata = split_frontmatter(candidate).metadata
            self.assertEqual(metadata["revision"], old_metadata["revision"])
            self.assertEqual(metadata["updated_at"], fixture.engine.transaction_status(tx)["deterministic_updated_at"])
            self.assertEqual(method.read_text(encoding="utf-8"), candidate.replace(
                'updated_at = "' + old_metadata["updated_at"] + '"',
                'updated_at = "' + metadata["updated_at"] + '"', 1))
        raw = method.read_bytes()
        self.assertEqual(metadata["revision"], 4)
        self.assertNotEqual(raw, expected.encode("utf-8"))
        self.assertEqual(raw, renderer.render_history_contract(
            "KAIROS_CONTEXT_TEST", updated_at=metadata["updated_at"]).encode("utf-8"))
        report = fixture.engine.inspect_transaction_scope(tx)
        original = governance.issue_external_edit_permit
        with patch.object(governance, "issue_external_edit_permit", wraps=original) as issued:
            with patch.object(fixture.engine, "apply", side_effect=AssertionError("Intake must not repeat Apply")):
                fixture.engine.intake_transaction_history(
                    tx, expected_report_sha256=report["report_sha256"],
                    reason="Admit the terminal record after verified source-independent canonical method migration")
        self.assertEqual(method.read_bytes(), raw)
        self.assertFalse(any(str(call.kwargs.get("publication_id", "")).startswith("HISTORY_METHOD:")
                             for call in issued.call_args_list))
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")

    def test_canonical_method_refuses_unpublished_instance_timestamp_before_grant(self):
        from kairos import governance
        from kairos.transaction_document import HISTORY_METHOD_PATH
        from runtime_sync_workshop.tx_documents import ensure_history_contract
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        ensure_history_contract(fixture.engine)
        method = fixture.workspace / HISTORY_METHOD_PATH
        raw = method.read_bytes()
        altered = raw.replace(b"2026-10-29T00:00:00Z", b"2026-10-27T01:02:03Z", 1)
        self.assertNotEqual(altered, raw)
        method.write_bytes(altered)  # Isolated negative probe; never a live recovery method.
        seal = fixture.engine.seal_path.read_bytes()
        with patch.object(governance, "issue_external_edit_permit",
                          side_effect=AssertionError("An unpromoted timestamp cannot authorize a grant")):
            with self.assertRaises(WorkshopError) as refusal:
                ensure_history_contract(fixture.engine)
        self.assertEqual(refusal.exception.code, "TX_HISTORY_METHOD_DRIFT")
        self.assertEqual(method.read_bytes(), altered)
        self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)

    def test_promoted_instance_timestamp_cannot_authorize_altered_canonical_policy(self):
        from kairos import governance
        from kairos import transaction_document as renderer
        from kairos.promoter import prepare_document
        from runtime_sync_workshop.tx_documents import ensure_history_contract
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        altered = renderer.render_history_contract(
            "KAIROS_CONTEXT_TEST", updated_at="2026-10-27T01:02:03Z").replace(
            "Timestamp variance never grants", "Changed policy now grants", 1)
        self.assertIn("Changed policy now grants", altered)
        # A natively projected foreign first-version policy, never direct SQLite editing.
        with patch.object(renderer, "render_history_contract", return_value=altered):
            ensure_history_contract(fixture.engine)
        method = fixture.workspace / renderer.HISTORY_METHOD_PATH
        with closing(sqlite3.connect(fixture.database)) as connection:
            projected = connection.execute("SELECT content_sha256 FROM artifacts WHERE artifact_id=?",
                                           (renderer.HISTORY_METHOD_ID,)).fetchone()[0]
        self.assertEqual(projected, prepare_document(method, fixture.workspace).content_sha256)
        raw = method.read_bytes()
        with patch.object(governance, "issue_external_edit_permit",
                          side_effect=AssertionError("A projected timestamp cannot authorize policy drift")):
            with self.assertRaises(WorkshopError) as refusal:
                ensure_history_contract(fixture.engine)
        self.assertEqual(refusal.exception.code, "TX_HISTORY_METHOD_DRIFT")
        self.assertEqual(method.read_bytes(), raw)

    def test_legacy_event_admission_precommit_intent_recovers_exact_receipt_without_apply(self):
        from runtime_sync_workshop import engine as owner
        from runtime_sync_workshop.tx_documents import pending_path, load_scope_transition
        from runtime_sync_workshop.tx_lifecycle import validate_event_chain
        fixture, tx, root, before = self._pre_material_epoch_fixture()
        prior = (root / "state.json").read_bytes()
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Recover versioned legacy admission after its controlled intent but before authoritative state commit"
        class Interrupted(BaseException):
            pass
        with patch.object(owner._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.engine.intake_transaction_history(
                    tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertEqual((root / "state.json").read_bytes(), prior)
        marker = pending_path(fixture.engine, tx)
        intended = json.loads(marker.read_text(encoding="utf-8"))["event_id"]
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Legacy intent recovery must not Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        state = self.assert_special_projected(fixture, tx, "ABORTED")
        validate_event_chain(state)
        self.assertEqual(state["tx_event_id"], intended)
        self.assertEqual(state["tx_event_history"], [before["tx_event"]])
        self.assertEqual(state["original_scope"], before["original_scope"])
        receipt = load_scope_transition(fixture.engine, state)
        self.assertEqual(receipt["previous_state_utf8"].encode("utf-8"), prior)
        self.assertEqual(receipt["legacy_event_format"]["prior_event_id"], before["tx_event_id"])
        self.assertFalse(marker.exists())
        stable = (root / "state.json").read_bytes()
        replay = fixture.engine.intake_transaction_history(
            tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertTrue(replay["replayed"])
        self.assertEqual((root / "state.json").read_bytes(), stable)

    def test_legacy_event_method_revision_and_content_bound_grant_are_canonical(self):
        from kairos import governance
        from kairos.database import KnowledgeDatabase
        from kairos.search import search_database
        from kairos.transaction_document import HISTORY_METHOD_ID, HISTORY_METHOD_PATH
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, before = self._pre_material_epoch_fixture()
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Publish the single canonical versioned legacy event admission method through an exact-content grant"
        original = governance.issue_external_edit_permit
        with patch.object(governance, "issue_external_edit_permit", wraps=original) as issued:
            fixture.engine.intake_transaction_history(
                tx, expected_report_sha256=report["report_sha256"], reason=reason)
        method = fixture.workspace / HISTORY_METHOD_PATH
        raw = method.read_bytes()
        metadata = split_frontmatter(raw.decode("utf-8")).metadata
        self.assertEqual(metadata["revision"], 4)
        self.assertIn(b"UNATTESTED", raw)
        self.assertIn(b"TX_HISTORY_METHOD_DRIFT", raw)
        expected = "HISTORY_METHOD:" + hashlib.sha256(raw).hexdigest()
        grants = [call.kwargs["publication_id"] for call in issued.call_args_list
                  if str(call.kwargs.get("publication_id", "")).startswith("HISTORY_METHOD:")]
        self.assertEqual(grants, [expected])
        result = search_database(KnowledgeDatabase(fixture.database),
            "How are pre-material-epoch transaction event anchors migrated?", limit=5)
        self.assertTrue(any(row["artifact_id"] == HISTORY_METHOD_ID and row["section_id"] == "s-workflow"
                            for row in result["results"]))
        state = (root / "state.json").read_bytes()
        altered = raw.replace(b"revision = 4", b"revision = 1", 1)
        self.assertNotEqual(altered, raw)
        method.write_bytes(altered)  # Isolated incompatible method; never silently adopted.
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.intake_transaction_history(
                tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_HISTORY_METHOD_DRIFT")
        self.assertEqual(method.read_bytes(), altered)
        self.assertEqual((root / "state.json").read_bytes(), state)

    def test_history_method_publication_supports_maximum_workspace_identifier(self):
        import tempfile
        from kickstart.universal_init import initialize_universal_markdown
        from workshop.tests.test_context_transactions import project_spec
        from runtime_sync_workshop.engine import WorkshopEngine
        from kairos import governance
        from kairos.transaction_document import HISTORY_METHOD_PATH
        workspace_id = "W" * 128
        with tempfile.TemporaryDirectory(prefix="history-method-id-boundary-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_bytes(b"value = 1\n")
            workspace = root / "workspace"
            intake = initialize_universal_markdown(
                project_root=project, workspace=workspace,
                workspace_id=workspace_id, project_spec=project_spec())
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.config.raw["transaction_documents_required"] = True
            engine.seal()
            tx = engine.checkout(["app.py"], purpose="Verify maximum workspace ID in native history publication")["transaction_id"]
            engine.abort(tx)
            report = engine.inspect_transaction_scope(tx)
            original = governance.issue_external_edit_permit
            with patch.object(governance, "issue_external_edit_permit", wraps=original) as issued:
                engine.intake_transaction_history(
                    tx, expected_report_sha256=report["report_sha256"],
                    reason="Verify native exact-content publication for the maximum supported workspace identifier")
            raw = (workspace / HISTORY_METHOD_PATH).read_bytes()
            self.assertEqual(split_frontmatter(raw.decode("utf-8")).metadata["workspace"], workspace_id)
            grants = [call.kwargs["publication_id"] for call in issued.call_args_list
                      if str(call.kwargs.get("publication_id", "")).startswith("HISTORY_METHOD:")]
            self.assertEqual(grants, ["HISTORY_METHOD:" + hashlib.sha256(raw).hexdigest()])
            self.assertEqual(len(grants[0]), 79)

    def _pre_material_epoch_fixture(self, *, extended=False, required=False, bound=False):
        """Synthetic old bytes only inside a real disposable native fixture."""
        import copy
        from runtime_sync_workshop.tx_lifecycle import canonical
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        if bound:
            report = fixture.engine.inspect_transaction_scope(tx)
            reason = "Establish native fixture history before simulating an older bound event format"
            fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
            report = fixture.engine.inspect_transaction_scope(tx)
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        root, state = fixture.engine._load_transaction(tx)
        keys = {"schema", "transaction_id", "event", "event_id", "sequence",
                "previous_event_id", "state_before", "state_after", "phase",
                "tx_kind", "candidate_sha256", "scope_receipt_sha256"}
        if extended:
            keys |= {"baseline_sha256", "occurred_at", "required_projection"}
        anchor = {key: copy.deepcopy(state["tx_event"][key]) for key in keys}
        if extended:
            anchor["required_projection"] = required
        unsigned = {key: value for key, value in anchor.items() if key != "event_id"}
        anchor["event_id"] = "TXE_" + hashlib.sha256(canonical(unsigned)).hexdigest()[:32].upper()
        state["tx_event"] = anchor
        state["tx_event_id"] = anchor["event_id"]
        state.pop("tx_event_history", None)
        state.pop("document_projection_required", None)
        (root / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return fixture, tx, root, state

    def test_explicit_intake_migrates_both_legacy_event_formats_without_attesting_old_material(self):
        from runtime_sync_workshop.tx_documents import load_scope_transition
        from runtime_sync_workshop.tx_lifecycle import validate_event_chain, validate_prior_endpoint
        from runtime_sync_workshop.util import WorkshopError
        for extended, required, bound in ((False, False, False), (True, False, False),
                                          (True, True, False), (True, True, True)):
            with self.subTest(extended=extended, required=required, bound=bound):
                fixture, tx, root, before = self._pre_material_epoch_fixture(
                    extended=extended, required=required, bound=bound)
                prior = (root / "state.json").read_bytes()
                document = fixture.workspace / document_path(tx)
                old_document = document.read_bytes()
                source = (fixture.project / "app.py").read_bytes()
                seal = fixture.engine.seal_path.read_bytes()
                with self.assertRaises(ValueError):
                    validate_event_chain(before)
                with self.assertRaises(WorkshopError):
                    validate_prior_endpoint(fixture.engine, before, before, event="TX_NOTE_OBSERVATION")
                report = fixture.engine.inspect_transaction_scope(tx)
                self.assertEqual(report["status"], "VERIFIED")
                reason = "Explicit versioned legacy format intake preserves exact old bytes without claiming historical execution"
                result = fixture.engine.intake_transaction_history(
                    tx, expected_report_sha256=report["report_sha256"], reason=reason)
                _, state = fixture.engine._load_transaction(tx)
                validate_event_chain(state)
                self.assertEqual(state["original_scope"], before["original_scope"])
                self.assertEqual(state["tx_event_history"], [before["tx_event"]])
                self.assertEqual(state["tx_event_sequence"], before["tx_event_sequence"] + 1)
                self.assertTrue(state["tx_event"]["required_projection"])
                self.assertEqual(state["historical_scope"]["scope_status"], "SCOPE_UNRESOLVED")
                receipt = load_scope_transition(fixture.engine, state)
                self.assertEqual(receipt["previous_state_utf8"].encode("utf-8"), prior)
                self.assertEqual(receipt["previous_document_utf8"].encode("utf-8"), old_document)
                admission = receipt["legacy_event_format"]
                self.assertEqual(admission["previous_state_sha256"], hashlib.sha256(prior).hexdigest())
                self.assertEqual(admission["prior_event_id"], before["tx_event_id"])
                self.assertEqual(admission["source_format"], "pre-material-epoch-anchor/v1" if extended else "pre-material-epoch-anchor/v0")
                self.assertIn("source_epoch", admission["unattested_fields"])
                self.assertEqual("baseline_sha256" in admission["unattested_fields"], not extended)
                self.assertEqual((fixture.project / "app.py").read_bytes(), source)
                self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
                stable = ((root / "state.json").read_bytes(), document.read_bytes())
                replay = fixture.engine.intake_transaction_history(
                    tx, expected_report_sha256=report["report_sha256"], reason=reason)
                self.assertTrue(replay["replayed"])
                self.assertEqual(replay["event_id"], result["event_id"])
                self.assertEqual(((root / "state.json").read_bytes(), document.read_bytes()), stable)
                fresh = fixture.engine.inspect_transaction_scope(tx)
                fixture.engine.bind_transaction_scope(
                    tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
                after = self.assert_special_projected(fixture, tx, "ABORTED")
                validate_event_chain(after)
                self.assertEqual(after["tx_event_history"][0], before["tx_event"])
                for key in ("workspace_id", "goal_id", "milestone_id", "task_id", "loop"):
                    self.assertEqual(after["original_scope"][key], before["original_scope"][key])
                self.assertFalse(fixture.engine.lease_path.exists())

    def test_legacy_event_admission_refuses_unknown_or_inconsistent_anchors_before_receipt_write(self):
        import copy
        from runtime_sync_workshop.tx_lifecycle import canonical
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, original = self._pre_material_epoch_fixture(extended=True)
        document = fixture.workspace / document_path(tx)
        old_document = document.read_bytes()
        cases = ("self_hash", "header_id", "header_sequence", "state", "kind", "baseline",
                 "candidate", "partial_epoch", "unknown_field", "history", "scope")
        for case in cases:
            with self.subTest(case=case):
                state = copy.deepcopy(original)
                event = state["tx_event"]
                if case == "self_hash": event["event"] += "_ALTERED"
                elif case == "header_id": state["tx_event_id"] = "TXE_" + "0" * 32
                elif case == "header_sequence": state["tx_event_sequence"] += 1
                elif case == "state": event["state_after"] = "POSTCHECK_VERIFIED"
                elif case == "kind": event["tx_kind"] = "other"
                elif case == "baseline": event["baseline_sha256"] = "0" * 64
                elif case == "candidate": event["candidate_sha256"] = "0" * 64
                elif case == "partial_epoch": event["source_epoch_kind"] = "werkfaden-tx-material-state/v1"
                elif case == "unknown_field": event["unknown_authority"] = True
                elif case == "history": state["tx_event_history"] = [copy.deepcopy(event)]
                elif case == "scope": event["scope_receipt_sha256"] = "0" * 64
                if case not in ("self_hash", "header_id"):
                    unsigned = {k: v for k, v in event.items() if k != "event_id"}
                    event["event_id"] = "TXE_" + hashlib.sha256(canonical(unsigned)).hexdigest()[:32].upper()
                    state["tx_event_id"] = event["event_id"]
                raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
                (root / "state.json").write_bytes(raw)
                report = fixture.engine.inspect_transaction_scope(tx)
                receipts = fixture.engine.config.state_directory / "transaction-scope-history" / tx
                paths = sorted(receipts.glob("*.json")) if receipts.exists() else []
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
                        reason="Reject unsupported or inconsistent old anchor bytes before recording a migration receipt")
                self.assertEqual(caught.exception.code, "TX_LEGACY_EVENT_ADMISSION_INVALID")
                self.assertEqual((root / "state.json").read_bytes(), raw)
                self.assertEqual(document.read_bytes(), old_document)
                self.assertEqual(sorted(receipts.glob("*.json")) if receipts.exists() else [], paths)

    def test_legacy_event_admission_requires_fresh_inspection_and_exact_observed_preimage(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, state = self._pre_material_epoch_fixture()
        report = fixture.engine.inspect_transaction_scope(tx)
        state["purpose"] = "Changed isolated preimage after the reviewed legacy inspection"
        raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        (root / "state.json").write_bytes(raw)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
                reason="Refuse changed historical source after review rather than adopting new bytes silently")
        self.assertEqual(caught.exception.code, "TX_SCOPE_INSPECTION_STALE")
        self.assertEqual((root / "state.json").read_bytes(), raw)

    def test_legacy_event_admission_publication_outage_replays_exact_current_event(self):
        from runtime_sync_workshop.tx_documents import load_scope_transition
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, before = self._pre_material_epoch_fixture()
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Recover exact legacy admission publication without repeating Apply or changing historical source evidence"
        with patch("runtime_sync_workshop.tx_documents.publish", side_effect=WorkshopError("INJECTED_OUTAGE", "isolated publication cut")):
            with self.assertRaises(WorkshopError):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, saved = fixture.engine._load_transaction(tx)
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Migration recovery must never Apply")):
            replay = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertTrue(replay["replayed"])
        after = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(after["tx_event_id"], saved["tx_event_id"])
        self.assertEqual(after["tx_event_sequence"], saved["tx_event_sequence"])
        self.assertEqual(after["tx_event_history"][0], before["tx_event"])
        self.assertIsNotNone(load_scope_transition(fixture.engine, after)["legacy_event_format"])

    def test_legacy_event_admission_receipt_declaration_cannot_be_missing_or_changed(self):
        import copy
        from runtime_sync_workshop.tx_documents import load_scope_transition
        from runtime_sync_workshop.util import WorkshopError, canonical_json
        fixture, tx, root, before = self._pre_material_epoch_fixture()
        report = fixture.engine.inspect_transaction_scope(tx)
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
            reason="Bind the versioned legacy admission declaration to its exact immutable native receipt")
        _, state = fixture.engine._load_transaction(tx)
        receipt = load_scope_transition(fixture.engine, state)
        for change in ("missing", "hash", "event", "format", "unattested"):
            with self.subTest(change=change):
                altered = copy.deepcopy(receipt)
                if change == "missing": altered.pop("legacy_event_format")
                elif change == "hash": altered["legacy_event_format"]["previous_state_sha256"] = "0" * 64
                elif change == "event": altered["legacy_event_format"]["prior_event_id"] = "TXE_" + "0" * 32
                elif change == "format": altered["legacy_event_format"]["source_format"] = "other"
                else: altered["legacy_event_format"]["unattested_fields"] = []
                raw = canonical_json(altered).encode()
                digest = hashlib.sha256(raw).hexdigest()
                target = fixture.engine.config.state_directory / "transaction-scope-history" / tx / (digest + ".json")
                target.write_bytes(raw)
                counterprobe = copy.deepcopy(state)
                counterprobe["historical_scope"]["receipt_sha256"] = digest
                with self.assertRaises(WorkshopError) as caught:
                    load_scope_transition(fixture.engine, counterprobe)
                self.assertEqual(caught.exception.code, "TX_SCOPE_RECEIPT_INVALID")

    def test_first_required_recycling_review_latches_genuinely_optional_state(self):
        from runtime_sync_workshop import tx_lifecycle, tx_recycling, engine as engine_module
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        for explicit_false in (False, True):
            for interruption in ("none", "before_outbox", "after_projection"):
                with self.subTest(explicit_false=explicit_false, interruption=interruption):
                    fixture = self.fixture()
                    fixture.engine.config.raw["transaction_documents_required"] = False
                    original_stage = engine_module.stage_lifecycle_event
                    def optional_first_state(engine, root, state, **kwargs):
                        if explicit_false and not (root / "state.json").exists():
                            state["document_projection_required"] = False
                        return original_stage(engine, root, state, **kwargs)
                    with patch.object(engine_module, "stage_lifecycle_event", side_effect=optional_first_state):
                        tx = fixture.checkout()["transaction_id"]
                    fixture.engine.abort(tx)
                    root, prior = fixture.engine._load_transaction(tx)
                    if explicit_false:
                        self.assertIs(prior["document_projection_required"], False)
                    else:
                        self.assertNotIn("document_projection_required", prior)
                    fixture.engine.reconcile_transaction_document(tx)
                    analysis, _ = self.completed_analysis(fixture, tx, disposition="RETAIN_EVIDENCE")
                    marker = pending_path(fixture.engine, tx)
                    intent = root / "recycling-review-intent.json"
                    if interruption == "none":
                        fixture.engine.submit_recycling(tx, analysis)
                        intended = fixture.engine._load_transaction(tx)[1]["tx_event_id"]
                    else:
                        if interruption == "before_outbox":
                            original_atomic = tx_lifecycle.atomic_write_json
                            def fail_marker(path, value, *args, **kwargs):
                                if Path(path) == marker:
                                    raise Interrupted("before optional review outbox")
                                return original_atomic(path, value, *args, **kwargs)
                            context = patch.object(tx_lifecycle, "atomic_write_json", side_effect=fail_marker)
                        else:
                            context = patch.object(tx_recycling, "_remove_review_intent", side_effect=Interrupted("after optional review projection"))
                        with context, self.assertRaises(Interrupted):
                            fixture.engine.submit_recycling(tx, analysis)
                        intended = json.loads(intent.read_bytes())["target_state"]["tx_event_id"]
                        with self.assertRaises(WorkshopError) as blocked:
                            fixture.checkout()
                        self.assertEqual(blocked.exception.code, "TX_RECYCLING_INTENT_PENDING")
                        with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
                            fixture.engine.submit_recycling(tx, analysis)
                    state = self.assert_special_projected(fixture, tx, "ABORTED")
                    self.assertTrue(state["document_projection_required"])
                    self.assertEqual(state["tx_event_id"], intended)
                    self.assertFalse(intent.exists())
                    self.assertFalse(marker.exists())
                    before = (fixture.workspace / document_path(tx)).read_bytes()
                    fixture.engine.submit_recycling(tx, analysis)
                    self.assertEqual((fixture.workspace / document_path(tx)).read_bytes(), before)
                    fixture.engine.compact_transaction(tx)
                    self.assertFalse((root / "work").exists())
                    self.assertFalse(fixture.engine.lease_path.exists())

    def test_recycling_review_rejects_nonboolean_and_downgraded_latch_before_writes(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        analysis, _ = self.completed_analysis(fixture, tx)
        root, _ = fixture.engine._load_transaction(tx)
        with patch.object(tx_recycling, "_recover_review_intent", side_effect=Interrupted("inspect exact native intent")):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        intent = root / "recycling-review-intent.json"
        original = intent.read_bytes()
        proposal = json.loads(analysis.read_bytes())
        def inventory():
            return {p.relative_to(fixture.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in fixture.root.rglob("*") if p.is_file()}
        for endpoint, value in (("target_state", "absent"), ("target_state", False),
                                ("target_state", None), ("target_state", 1),
                                ("target_state", "true"), ("prior_state", None),
                                ("prior_state", 0), ("prior_state", "true")):
            with self.subTest(endpoint=endpoint, value=value):
                data = json.loads(original)
                if value == "absent":
                    data[endpoint].pop("document_projection_required")
                else:
                    data[endpoint]["document_projection_required"] = value
                data[endpoint + "_sha256"] = tx_recycling.sha256_bytes(tx_recycling._canonical(data[endpoint]))
                intent.write_bytes(json.dumps(data, sort_keys=True).encode("utf-8"))
                before = inventory()
                with self.assertRaises(WorkshopError) as caught:
                    tx_recycling._recover_review_intent(fixture.engine, root, proposal)
                self.assertEqual(caught.exception.code, "TX_RECYCLING_INTENT_INVALID")
                self.assertIn("documentation latch", str(caught.exception.details))
                self.assertEqual(inventory(), before)
        intent.write_bytes(original)
        fixture.engine.submit_recycling(tx, analysis)
        self.assert_special_projected(fixture, tx, "ABORTED")

    def test_transaction_document_latch_is_strict_and_legacy_reads_do_not_mutate(self):
        from types import SimpleNamespace
        from runtime_sync_workshop.tx_lifecycle import requires_projection
        from runtime_sync_workshop.util import WorkshopError
        engine = SimpleNamespace(config=SimpleNamespace(raw={}))
        for state, expected in (({}, False), ({"document_projection_required": False}, False),
                                ({"document_projection_required": True}, True),
                                ({"tx_event": {"required_projection": True}}, True)):
            before = json.dumps(state, sort_keys=True)
            self.assertIs(requires_projection(engine, state), expected)
            self.assertEqual(json.dumps(state, sort_keys=True), before)
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(WorkshopError) as caught:
                requires_projection(engine, {"document_projection_required": value})
            self.assertEqual(caught.exception.code, "TX_DOCUMENT_REQUIREMENT_INVALID")

    def test_mandatory_transaction_latch_survives_disabled_policy_and_publication_outage(self):
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root, state = fixture.engine._load_transaction(tx)
        self.assertIs(state["document_projection_required"], True)
        self.assertTrue(state["tx_event"]["required_projection"])
        # Isolated cached policy view models rollback/recovery before the
        # activation config is visible. Never edit the fixture's sealed config.
        fixture.engine.config.raw["transaction_documents_required"] = False
        state["policy_observation"] = "activation not yet globally visible"
        with patch("runtime_sync_workshop.tx_documents.publish",
                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "isolated publication outage")):
            fixture.engine._save_transaction(root, state, event="TX_NOTE")
            # Native persistence keeps the technical event and pending outbox;
            # the public owner's publication barrier reports the outage.
            with self.assertRaises(WorkshopError) as caught:
                assert_projected(fixture.engine, tx)
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        self.assertTrue(fixture.engine.lease_path.exists())
        _, persisted = fixture.engine._load_transaction(tx)
        self.assertTrue(persisted["document_projection_required"])
        self.assertTrue(persisted["tx_event"]["required_projection"])
        self.assertEqual(json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"],
                         persisted["tx_event_id"])
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Reconciliation must not Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        assert_projected(fixture.engine, tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        fixture.engine.config.raw["transaction_documents_required"] = True
        fixture.engine.abort(tx)
        terminal = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertIs(terminal["document_projection_required"], True)

    def test_document_latch_downgrade_is_refused_before_state_outbox_or_report_write(self):
        import copy
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root, original = fixture.engine._load_transaction(tx)
        fixture.engine.config.raw["transaction_documents_required"] = False
        def inventory():
            return {p.relative_to(fixture.root).as_posix(): p.read_bytes() if p.is_file() else None
                    for p in fixture.root.rglob("*")}
        before = inventory()
        for value in (False, None, 0, "true", "absent"):
            state = copy.deepcopy(original)
            if value == "absent":
                state.pop("document_projection_required")
            else:
                state["document_projection_required"] = value
            with self.subTest(value=value), self.assertRaises(WorkshopError) as caught:
                fixture.engine._save_transaction(root, state, event="TX_NOTE")
            self.assertEqual(caught.exception.code,
                             "TX_DOCUMENT_REQUIREMENT_DOWNGRADE" if value is False or value == "absent"
                             else "TX_DOCUMENT_REQUIREMENT_INVALID")
            self.assertEqual(inventory(), before)
        fixture.engine.config.raw["transaction_documents_required"] = True
        fixture.engine.abort(tx)

    def test_native_first_event_can_force_documentation_before_global_activation(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        engine = fixture.engine
        tx = fixture.checkout()["transaction_id"]
        engine.abort(tx)
        # Exercise the existing native persistence primitive, not a new
        # public migration or a fabricated projection receipt.
        engine.config.raw["transaction_documents_required"] = False
        tx = "TXN_" + "f" * 24
        root = engine._transaction_path(tx)
        engine._acquire_lease(tx)
        state = dict(schema="runtime-sync-transaction/v1", transaction_id=tx,
                     state="CHECKED_OUT", purpose="Isolated native first-event documentation requirement",
                     sources=[], document_projection_required=True, journal=[])
        root.mkdir()
        engine._save_transaction(root, state, event="CHECKOUT_COMPLETED")
        assert_projected(engine, tx)
        _, first = engine._load_transaction(tx)
        self.assertEqual(first["tx_event_sequence"], 1)
        self.assertTrue(first["tx_event"]["required_projection"])
        self.assertTrue(first["document_projection_required"])
        state["state"] = "ABORTED"
        engine._save_transaction(root, state, event="TRANSACTION_ABORTED")
        assert_projected(engine, tx)
        engine._release_lease(tx)
        self.assertFalse(engine.lease_path.exists())

    def test_pending_outcome_and_exact_reference_reach_direct_api_and_cli(self):
        from runtime_sync_workshop import cli
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        from runtime_sync_workshop.util import WorkshopError

        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        with patch("runtime_sync_workshop.tx_documents.publish",
                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "isolated publication outage")):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.abort(tx)
        marker = pending_path(fixture.engine, tx)
        raw = marker.read_bytes()
        pending = json.loads(raw)
        error = caught.exception
        self.assertEqual(error.code, "TX_DOCUMENT_SYNC_PENDING")
        self.assertEqual(error.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(error.details, pending)
        item = error.references["items"][0]
        self.assertEqual(item["event_id"], pending["event_id"])
        self.assertEqual(item["event_sequence"], pending["event_sequence"])
        self.assertEqual(item["marker_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item["status"], "OBSERVED")

        with self.assertRaises(WorkshopError) as blocked:
            fixture.engine.checkout(["app.py"], purpose="Probe canonical pending outcome and exact event identity")
        error = blocked.exception
        self.assertEqual(error.code, "TX_DOCUMENT_SYNC_PENDING")
        self.assertEqual(error.details, [marker.name])
        self.assertEqual(error.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(error.references["items"][0]["event_id"], pending["event_id"])
        self.assertTrue(error.references["total_exact"])

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = cli.main(["--config", str(fixture.engine.config.config_path), "checkout",
                             "--source", "app.py", "--purpose", "Probe pending outcome through the public CLI"])
        self.assertEqual(code, 2)
        public = json.loads(stderr.getvalue())["error"]
        self.assertEqual(public["code"], "TX_DOCUMENT_SYNC_PENDING")
        self.assertEqual(public["details"], [marker.name])
        self.assertEqual(public["outcome"], "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(public["references"]["items"][0]["event_id"], pending["event_id"])
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Pending repair must not Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        assert_projected(fixture.engine, tx)
        self.assertFalse(marker.exists())

    def test_pending_references_are_bounded_and_malformed_markers_still_block(self):
        from runtime_sync_workshop import tx_documents as module
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json

        fixture = self.fixture()
        directory = fixture.engine.config.state_directory / "transaction-document-pending"
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(25):
            tx = "TXN_" + f"{number:024x}"
            atomic_write_json(directory / (tx + ".json"), {
                "schema": "werkfaden-tx-projection-pending/v1", "pending": True,
                "transaction_id": tx, "event_id": "TXE_" + f"{number:032X}", "event_sequence": number + 1,
            })
        with self.assertRaises(WorkshopError) as caught:
            module.assert_no_pending(fixture.engine)
        error = caught.exception
        self.assertEqual(error.code, "TX_DOCUMENT_SYNC_PENDING")
        self.assertEqual(error.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual((error.references["total"], error.references["omitted"]), (25, 5))
        self.assertTrue(error.references["total_exact"])
        self.assertTrue(error.references["omitted_exact"])
        self.assertEqual(len(error.details), 20)
        self.assertTrue(all(item["status"] == "OBSERVED" for item in error.references["items"]))
        with patch.object(module, "MAX_PENDING_MARKERS", 3), patch.object(module, "MAX_PENDING_REFERENCES", 2):
            with self.assertRaises(WorkshopError) as caught:
                module.assert_no_pending(fixture.engine)
        self.assertFalse(caught.exception.references["total_exact"])
        self.assertFalse(caught.exception.references["omitted_exact"])
        self.assertEqual(caught.exception.references["total"], 4)
        self.assertEqual(caught.exception.references["omitted"], 2)

        for path in directory.glob("TXN_*.json"):
            path.unlink()
        corrupt = directory / ("TXN_" + "a" * 24 + ".json")
        oversized = directory / ("TXN_" + "b" * 24 + ".json")
        mismatch = directory / ("TXN_" + "c" * 24 + ".json")
        not_file = directory / ("TXN_" + "d" * 24 + ".json")
        wrong_schema = directory / ("TXN_" + "e" * 24 + ".json")
        corrupt.write_bytes(b"not JSON")
        oversized.write_bytes(b"x" * (module.MAX_PENDING_MARKER_BYTES + 1))
        atomic_write_json(mismatch, {"schema": "werkfaden-tx-projection-pending/v1", "pending": True,
                                     "transaction_id": "TXN_other", "event_id": "TXE_" + "A" * 32,
                                     "event_sequence": 1})
        atomic_write_json(wrong_schema, {"schema": "untrusted-schema", "pending": True,
                                        "transaction_id": wrong_schema.stem,
                                        "event_id": "TXE_" + "E" * 32, "event_sequence": 1})
        not_file.mkdir()
        with self.assertRaises(WorkshopError) as caught:
            module.assert_no_pending(fixture.engine)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        observations = caught.exception.references["items"]
        self.assertEqual(len(observations), 5)
        self.assertTrue(all(item["status"] == "UNVERIFIED" and item["event_id"] is None for item in observations))
        reasons = {item["marker"]: item["reason"] for item in observations}
        self.assertEqual(reasons[corrupt.name], "JSONDecodeError")
        self.assertEqual(reasons[oversized.name], "MARKER_TOO_LARGE")
        self.assertEqual(reasons[mismatch.name], "marker identity is missing or invalid")
        self.assertEqual(reasons[not_file.name], "NOT_REGULAR_FILE")
        self.assertEqual(reasons[wrong_schema.name], "marker identity is missing or invalid")
        with self.assertRaises(WorkshopError) as caught:
            module.assert_projected(fixture.engine, corrupt.stem)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["status"], "UNVERIFIED")

    def test_pending_marker_disappearing_during_scan_does_not_allow_checkout(self):
        from runtime_sync_workshop import tx_documents as module
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        fixture = self.fixture()
        directory = fixture.engine.config.state_directory / "transaction-document-pending"
        marker = directory / ("TXN_" + "e" * 24 + ".json")
        atomic_write_json(marker, {"schema": "werkfaden-tx-projection-pending/v1", "pending": True,
                                   "transaction_id": marker.stem, "event_id": "TXE_" + "E" * 32,
                                   "event_sequence": 1})
        observe = module._pending_marker_observation

        def disappear(path):
            path.unlink()
            return observe(path)

        with patch.object(module, "_pending_marker_observation", side_effect=disappear):
            with self.assertRaises(WorkshopError) as caught:
                module.assert_no_pending(fixture.engine)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["status"], "UNVERIFIED")
        self.assertEqual(caught.exception.references["items"][0]["reason"], "FileNotFoundError")
        self.assertIsNone(caught.exception.references["items"][0]["event_id"])

    def test_pending_linked_marker_is_not_followed_and_still_blocks(self):
        from runtime_sync_workshop.tx_documents import assert_no_pending
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        directory = fixture.engine.config.state_directory / "transaction-document-pending"
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / ("TXN_" + "f" * 24 + ".json")
        try:
            marker.symlink_to(directory / "missing-target")
        except OSError as exc:
            self.skipTest(f"platform cannot create an isolated symlink: {exc}")
        with self.assertRaises(WorkshopError) as caught:
            assert_no_pending(fixture.engine)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["status"], "UNVERIFIED")
        self.assertIsNone(caught.exception.references["items"][0]["event_id"])

    def test_pending_reparse_detection_and_unreadable_directory_fail_closed(self):
        from runtime_sync_workshop import tx_documents as module
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        fixture = self.fixture()
        directory = fixture.engine.config.state_directory / "transaction-document-pending"
        marker = directory / ("TXN_" + "9" * 24 + ".json")
        atomic_write_json(marker, {"schema": "werkfaden-tx-projection-pending/v1", "pending": True,
                                   "transaction_id": marker.stem, "event_id": "TXE_" + "9" * 32,
                                   "event_sequence": 1})
        reject = module.reject_link_components

        def detected_link(path, *, label):
            if path == marker:
                raise WorkshopError("PATH_LINK", "isolated reparse detection")
            return reject(path, label=label)

        with patch.object(module, "reject_link_components", side_effect=detected_link):
            with self.assertRaises(WorkshopError) as caught:
                module.assert_no_pending(fixture.engine)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["status"], "UNVERIFIED")
        self.assertEqual(caught.exception.references["items"][0]["reason"], "PATH_LINK")
        self.assertIsNone(caught.exception.references["items"][0]["event_id"])

        with patch.object(module.os, "scandir", side_effect=PermissionError("isolated unreadable directory")):
            with self.assertRaises(WorkshopError) as caught:
                module.assert_no_pending(fixture.engine)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["status"], "UNVERIFIED_DIRECTORY")

    def test_publication_identity_binds_exact_bytes_event_and_permit_scope(self):
        from runtime_sync_workshop.tx_documents import _publication_identity
        scope = {"goal_id": "GOAL_CONTEXT", "milestone_id": "MILESTONE_CONTEXT_01",
                 "task_id": "TASK_CONTEXT_001", "criterion_id": "CRIT_CONTEXT_001"}
        txid = "TXN_" + "a" * 24
        identity = _publication_identity(txid, "TXE_ORIGINAL", "b" * 64, scope)
        self.assertEqual(identity, _publication_identity(txid, "TXE_ORIGINAL", "b" * 64,
                         dict(reversed(list(scope.items())))))
        self.assertLessEqual(len(identity), 200)
        self.assertNotEqual(identity, _publication_identity(txid, "TXE_NEW", "b" * 64, scope))
        self.assertNotEqual(identity, _publication_identity(txid, "TXE_ORIGINAL", "c" * 64, scope))
        for field in scope:
            with self.subTest(field=field):
                self.assertNotEqual(identity, _publication_identity(txid, "TXE_ORIGINAL", "b" * 64,
                                    {**scope, field: scope[field] + "_NEXT"}))

    def test_corrected_renderer_recovers_failed_publication_without_new_tx_event(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        from runtime_sync_workshop.util import WorkshopError
        from kairos import transaction_document
        fixture = self.fixture()
        txid = fixture.checkout()["transaction_id"]
        with patch("kairos.heartbeat.promote_document", side_effect=ValueError("Isolated projection failure")):
            with self.assertRaises(WorkshopError):
                fixture.engine.annotate_transaction(txid, kind="observation",
                    summary="Reproduce a failed publication before correcting renderer metadata",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        self.assertTrue(pending_path(fixture.engine, txid).is_file())
        state_path = fixture.engine._transaction_path(txid) / "state.json"
        source_state = state_path.read_bytes()
        original = transaction_document.render_transaction_document
        def corrected(**arguments):
            return original(**arguments).replace('capsule = "', 'capsule = "Corrected fixture: ', 1)
        with patch.object(transaction_document, "render_transaction_document", corrected):
            with patch.object(fixture.engine, "apply", side_effect=AssertionError("Recovery must never repeat Apply")):
                result = fixture.engine.reconcile_transaction_document(txid)
        self.assertEqual(state_path.read_bytes(), source_state)
        self.assertFalse(pending_path(fixture.engine, txid).exists())
        assert_projected(fixture.engine, txid)
        path = fixture.workspace / document_path(txid)
        self.assertTrue(split_frontmatter(path.read_text(encoding="utf-8")).metadata["capsule"].startswith("Corrected fixture: "))
        acknowledged = path.read_bytes()
        replay = fixture.engine.reconcile_transaction_document(txid)
        self.assertTrue(replay["replayed"])
        self.assertEqual(path.read_bytes(), acknowledged)
        fixture.engine.abort(txid)

    def fixture(self, fixture_type=ContextTransactionTests):
        fixture = fixture_type("test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.engine.config.raw["transaction_documents_required"] = True
        return fixture

    def assert_special_projected(self, fixture, tx, expected_state):
        from runtime_sync_workshop.tx_documents import assert_projected
        assert_projected(fixture.engine, tx)
        _, state = fixture.engine._load_transaction(tx)
        self.assertEqual(state["state"], expected_state)
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        with closing(sqlite3.connect(fixture.database)) as con:
            row = con.execute("SELECT metadata_json FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone()
        sql = json.loads(row[0])
        self.assertEqual(state["tx_event_id"], metadata["tx_event_id"])
        self.assertEqual(state["tx_event_id"], sql["tx_event_id"])
        self.assertEqual(state["state"], sql["workshop_state"])
        return state

    def test_bootstrap_initial_stage_and_abort_have_native_event_and_sql_identity(self):
        from runtime_sync_workshop.repair import BootstrapRepair, _shadow_config
        fixture = self.fixture()
        config = fixture.engine.config
        source_before = (fixture.project / "app.py").read_bytes()
        view = _shadow_config(config, blueprint_root=config.blueprint_root,
                              managed_root=config.managed_blueprint_root,
                              workspace=config.kairos_workspace, database=config.kairos_database)
        self.assertIs(view.machine_root, config.machine_root)
        self.assertIs(view.raw, config.raw)
        self.assertEqual(view.machine_authority_files, config.machine_authority_files)
        result = BootstrapRepair(fixture.engine).stage()
        tx = result["transaction_id"]
        state = self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        self.assertEqual(state["tx_event"]["event"], "BOOTSTRAP_SHADOW_VERIFIED")
        self.assertEqual(state["tx_event_sequence"], 1)
        self.assertEqual(state["tx_event"]["baseline_sha256"], state["baseline_package_sha256"])
        self.assertEqual(state["staged_package_sha256"], state["baseline_package_sha256"])
        self.assertFalse((fixture.engine._transaction_path(tx) / "staged.config.json").exists())
        BootstrapRepair(fixture.engine).abort(tx)
        after = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(after["tx_event_sequence"], 2)
        self.assertEqual(after["tx_event"]["previous_event_id"], state["tx_event_id"])
        self.assertEqual((fixture.project / "app.py").read_bytes(), source_before)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_bootstrap_stage_projection_failure_retains_recoverable_lease_and_event(self):
        from runtime_sync_workshop.repair import BootstrapRepair
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        with patch("kairos.heartbeat.run_heartbeat", return_value={"verified": False, "failures": ["isolated projection outage"]}):
            with self.assertRaises(WorkshopError) as caught:
                BootstrapRepair(fixture.engine).stage()
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        lease = json.loads(fixture.engine.lease_path.read_text(encoding="utf-8"))
        tx = lease["transaction_id"]
        _, state = fixture.engine._load_transaction(tx)
        pending = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        self.assertEqual(pending["event_id"], state["tx_event_id"])
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Projection recovery cannot Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        BootstrapRepair(fixture.engine).abort(tx)
        self.assert_special_projected(fixture, tx, "ABORTED")

    def test_bootstrap_shadow_failure_before_state_releases_only_its_lease(self):
        from runtime_sync_workshop.repair import BootstrapRepair
        fixture = self.fixture()
        with patch("runtime_sync_workshop.repair._shadow_config", side_effect=RuntimeError("isolated shadow failure")):
            with self.assertRaisesRegex(RuntimeError, "isolated shadow failure"):
                BootstrapRepair(fixture.engine).stage()
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_supported_transaction_constructors_do_not_drift(self):
        from runtime_sync_workshop import _engine_core_v1
        package = Path(_engine_core_v1.__file__).resolve().parent
        sources = sorted(package.rglob("*.py"))
        rows = []
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                        and any(isinstance(t, ast.Name) and t.id == "state" for t in node.targets)):
                    continue
                values = {k.value: v for k, v in zip(node.value.keys, node.value.values) if isinstance(k, ast.Constant)}
                if "transaction_id" not in values:
                    continue
                schema = values.get("schema")
                kind = values.get("transaction_kind", ast.Constant(None))
                self.assertIsInstance(schema, ast.Constant, "New/dynamic TX constructor requires explicit schema review")
                self.assertIsInstance(kind, ast.Constant, "Dynamic TX kind requires explicit constructor review")
                rows.append((source.relative_to(package).as_posix(), schema.value, kind.value))
        self.assertEqual(sorted(rows), sorted([
            ("_core_checkout.py", "runtime-sync-transaction/v1", None),
            ("auxiliary.py", "runtime-sync-auxiliary-transaction/v1", None),
            ("migration.py", "runtime-sync-authority-migration/v1", "authority_migration"),
            ("universal_migration.py", "runtime-sync-universal-source-set/v1", "universal_source_set"),
            ("repair.py", "runtime-sync-bootstrap-repair/v1", None),
            ("engine.py", "werkfaden-runtime-policy-transaction/v1", "runtime_policy"),
        ]), "Supported TX constructors changed; review kind and direct lifecycle/SQL coverage")

    def test_auxiliary_fixture_uses_canonical_serialized_authority_and_fresh_engine(self):
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.corpus import build_corpus_manifest
        fixture, name = self.auxiliary_fixture()
        fresh = WorkshopEngine(fixture.engine.config.config_path)
        raw = json.loads(fresh.config.config_path.read_text(encoding="utf-8"))
        self.assertEqual(fresh.config.auxiliary_documents, (name,))
        self.assertEqual(raw["auxiliary_documents"], [name])
        self.assertIn(name, raw["blueprint_ignore"])
        self.assertTrue(raw["transaction_documents_required"])
        original = fixture.root / name
        self.assertTrue(original.is_file())
        self.assertEqual(original.read_bytes(), (fresh.config.blueprint_root / name).read_bytes())
        self.assertNotEqual(original, fresh.config.blueprint_root / name)
        manifest = build_corpus_manifest(fresh.config)
        self.assertTrue(manifest["verified"], manifest["issues"])
        self.assertEqual(manifest["counts"]["auxiliary_documents"], 1)
        self.assertEqual(manifest["package_sha256"], fixture.engine._verified_package())
        self.assertEqual(manifest["package_sha256"], fresh._verified_package())
        self.assertEqual((fixture.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertFalse(fresh.lease_path.exists())

    def auxiliary_fixture(self):
        from runtime_sync_workshop.engine import WorkshopEngine
        name = "PROCESS_AUTHORITY.md"

        class CanonicalAuxiliaryFixture(ContextTransactionTests):
            def initial_auxiliary_documents(self):
                document = self.root / name
                document.write_text("Initial isolated process authority.\n", encoding="utf-8")
                return (document,)

        fixture = self.fixture(CanonicalAuxiliaryFixture)
        # Exercise only authority that survives a fresh public Engine load.
        fixture.engine = WorkshopEngine(fixture.engine.config.config_path)
        return fixture, name

    def test_direct_abort_matrix_preserves_identity_despite_pending_projection(self):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        from runtime_sync_workshop.universal_migration import UniversalSourceSetMigration
        from runtime_sync_workshop.repair import BootstrapRepair
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        for kind in ("normal", "auxiliary", "source_set", "bootstrap"):
            for pending in (False, True):
                with self.subTest(kind=kind, pending=pending):
                    if kind == "auxiliary":
                        fixture, document = self.auxiliary_fixture()
                        owner = AuxiliaryDocumentTransaction(fixture.engine)
                        created = owner.checkout([document], purpose="Prove direct auxiliary abort with exact event identity")
                    else:
                        fixture = self.fixture()
                        if kind == "normal":
                            owner = fixture.engine
                            created = fixture.checkout()
                        elif kind == "source_set":
                            owner = UniversalSourceSetMigration(fixture.engine)
                            created = owner.checkout(purpose="Prove direct source-set abort without changing membership")
                        else:
                            owner = BootstrapRepair(fixture.engine)
                            created = owner.stage()
                    tx = created["transaction_id"]
                    _, initial = fixture.engine._load_transaction(tx)
                    initial_state = "SHADOW_VERIFIED" if kind == "bootstrap" else "CHECKED_OUT"
                    self.assert_special_projected(fixture, tx, initial_state)
                    baseline = initial["baseline_package_sha256"]
                    if pending:
                        with patch("runtime_sync_workshop.tx_documents.publish",
                                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Isolated abort matrix publication outage")):
                            with self.assertRaises(WorkshopError):
                                fixture.engine.annotate_transaction(
                                    tx, kind="observation",
                                    summary="A pending projection must not prevent safe direct abort",
                                    evidence="isolated direct-API abort matrix fixture",
                                )
                        _, noted = fixture.engine._load_transaction(tx)
                        marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
                        self.assertEqual(marker["event_id"], noted["tx_event_id"])
                        with self.assertRaises(WorkshopError) as caught:
                            fixture.engine.checkout(["app.py"], purpose="Must reject new source work while abort projection remains pending")
                        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                        self.assertEqual(caught.exception.references["items"][0]["event_id"], marker["event_id"])
                    _, before = fixture.engine._load_transaction(tx)
                    result = owner.abort(tx)
                    self.assertEqual(result["state"], "ABORTED")
                    after = self.assert_special_projected(fixture, tx, "ABORTED")
                    self.assertGreater(after["tx_event_sequence"], before["tx_event_sequence"])
                    self.assertEqual(after["tx_event"]["previous_event_id"], before["tx_event_id"])
                    self.assertFalse(pending_path(fixture.engine, tx).exists())
                    self.assertFalse(fixture.engine.lease_path.exists())
                    status = fixture.engine.status()
                    self.assertTrue(status["verified"], status)
                    self.assertEqual(status["package_sha256"], baseline)
                    self.assertTrue(status["seal"]["matches_current"])

    def test_auxiliary_corrupt_rollback_preimage_never_overwrites_live_target(self):
        from runtime_sync_workshop import auxiliary as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, name = self.auxiliary_fixture()
        owner = module.AuxiliaryDocumentTransaction(fixture.engine)
        checkout = owner.checkout([name], purpose="Reject a corrupted isolated rollback preimage")
        tx = checkout["transaction_id"]
        work = Path(checkout["work_directory"]) / name
        candidate = b"Reviewed isolated process authority.\n"
        work.write_bytes(candidate)
        owner.verify(tx)
        root = fixture.engine._transaction_path(tx)
        live = fixture.engine.config.blueprint_root / name
        original_copy = module.copy_exact
        faulted = []

        def corrupt_after_write(source, target):
            result = original_copy(source, target)
            if not faulted and Path(source) == work and Path(target) == live:
                (root / "rollback" / name).write_bytes(b"untrusted preimage")
                faulted.append(True)
                raise OSError("isolated failure after corrupting the saved preimage")
            return result

        with patch.object(module, "copy_exact", side_effect=corrupt_after_write):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assertEqual(faulted, [True])
        self.assertEqual(live.read_bytes(), candidate)
        self.assertTrue(fixture.engine.lease_path.exists())
        _, state = fixture.engine._load_transaction(tx)
        self.assertEqual(state["state"], "RECOVERY_REQUIRED")
        self.assertFalse(caught.exception.details["rollback"][0]["restored"])

    def test_auxiliary_direct_api_checkout_verify_apply_projects_each_event(self):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        fixture, name = self.auxiliary_fixture()
        owner = AuxiliaryDocumentTransaction(fixture.engine)
        checkout = owner.checkout([name], purpose="Update the isolated process authority with native lifecycle evidence")
        tx = checkout["transaction_id"]
        previous = self.assert_special_projected(fixture, tx, "CHECKED_OUT")
        target = Path(checkout["work_directory"]) / name
        target.write_text("Reviewed isolated process authority.\n", encoding="utf-8")
        owner.verify(tx)
        verified = self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        self.assertGreater(verified["tx_event_sequence"], previous["tx_event_sequence"])
        result = owner.apply(tx)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertGreater(final["tx_event_sequence"], verified["tx_event_sequence"])
        self.assertEqual((fixture.engine.config.blueprint_root / name).read_bytes(), target.read_bytes())
        self.assertEqual((fixture.project / "app.py").read_bytes(), b"value = 1\n")

    def test_auxiliary_direct_abort_projects_a_new_terminal_event(self):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        fixture, name = self.auxiliary_fixture()
        owner = AuxiliaryDocumentTransaction(fixture.engine)
        tx = owner.checkout([name], purpose="Abort an isolated auxiliary transaction without changing authority")["transaction_id"]
        previous = self.assert_special_projected(fixture, tx, "CHECKED_OUT")
        owner.abort(tx)
        final = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(final["tx_event"]["previous_event_id"], previous["tx_event_id"])
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_universal_source_set_direct_api_projects_checkout_prepare_verify_apply(self):
        from runtime_sync_workshop.universal_migration import UniversalSourceSetMigration
        fixture = self.fixture()
        owner = UniversalSourceSetMigration(fixture.engine)
        checkout = owner.checkout(purpose="Add one isolated Python source through native source membership migration")
        tx = checkout["transaction_id"]
        previous = self.assert_special_projected(fixture, tx, "CHECKED_OUT")
        (Path(checkout["candidate_root"]) / "added.py").write_text("answer = 42\n", encoding="utf-8")
        for operation, state in ((owner.prepare, "PREPARED"), (owner.verify, "SHADOW_VERIFIED"), (owner.apply, "POSTCHECK_VERIFIED")):
            operation(tx)
            current = self.assert_special_projected(fixture, tx, state)
            self.assertGreater(current["tx_event_sequence"], previous["tx_event_sequence"])
            previous = current
        self.assertEqual((fixture.project / "added.py").read_text(encoding="utf-8"), "answer = 42\n")

    def test_compiler_intake_rejects_unbound_projection_policy_mutation(self):
        import tempfile
        from workshop.tests.test_project_intake_binding import initialized_workspace
        from runtime_sync_workshop.util import WorkshopError
        temporary = tempfile.TemporaryDirectory(prefix="compiler-policy-binding-")
        self.addCleanup(temporary.cleanup)
        workspace, engine = initialized_workspace(Path(temporary.name))
        self.assertIs(engine.config.raw["transaction_documents_required"], True)
        # Isolated negative input: the source intake still binds the original
        # generated config. The actual seal must reject an unbound toggle.
        engine.config.raw["transaction_documents_required"] = False
        with self.assertRaises(WorkshopError) as caught:
            engine.seal()
        self.assertEqual(caught.exception.code, "CORPUS_NOT_VERIFIED")
        self.assertIn("PROJECT_INTAKE_CONFIG_HASH_MISMATCH", {row["code"] for row in caught.exception.details})

    def test_bootstrap_repairs_native_indexed_legacy_blank_noise_without_source_write(self):
        import re
        from runtime_sync_workshop.repair import BootstrapRepair
        from kairos.governance import issue_external_edit_permit
        from kairos.heartbeat import run_heartbeat
        from workshop.tests import test_context_transactions as context_tests
        initialize = context_tests.initialize_universal_markdown
        def initial_two_line_project(**kwargs):
            # Define the initial fixture source before native intake/seal.
            (Path(kwargs["project_root"]) / "app.py").write_bytes(b"value = 1\nother = 2\n")
            return initialize(**kwargs)
        with patch.object(context_tests, "initialize_universal_markdown", side_effect=initial_two_line_project):
            fixture = self.fixture()
        config = fixture.engine.config
        external = next(config.blueprint_root.glob("PROJECT_CODE_*.md"))
        managed = config.managed_blueprint_root / external.name
        original = managed.read_text(encoding="utf-8")
        incomplete = re.sub(r"(?m)^revision = 1$", "revision = 2", original, count=1)
        incomplete, removed = re.subn(r"(?m)^(C:0001[^\n]*\n)(C:0002)", r"\1\n\2", incomplete, count=1)
        self.assertEqual(removed, 1)
        relative = "code/" + external.name
        issue_external_edit_permit(fixture.workspace, paths=[relative],
                                   reason="Isolated historical blank-noise ledger fixture before native bootstrap repair")
        # Explicit corruption fixture in its temporary project only. SQL is
        # populated by native heartbeat, never fabricated or hand-edited.
        managed.write_text(incomplete, encoding="utf-8")
        external.write_text(incomplete, encoding="utf-8")
        receipt = run_heartbeat(fixture.workspace, requested_mode="verify", changed_paths=[relative],
                                use_reconciliation=True, trigger="isolated-legacy-ledger-fixture")
        self.assertTrue(receipt["verified"], receipt)
        source_before = (fixture.project / "app.py").read_bytes()
        owner = BootstrapRepair(fixture.engine)
        result = owner.stage()
        tx = result["transaction_id"]
        self.assertTrue(result["changed_documents"])
        self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        applied = owner.apply(tx)
        self.assertEqual(applied["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(applied["bit_exact"])
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertEqual((fixture.project / "app.py").read_bytes(), source_before)
        self.assertEqual(external.read_bytes(), managed.read_bytes())
        self.assertIn("### SOURCE", managed.read_text(encoding="utf-8"))

    def test_fresh_intake_requires_projection_before_its_first_transaction(self):
        fixture = ContextTransactionTests("test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        raw = json.loads(fixture.engine.config.config_path.read_text(encoding="utf-8"))
        self.assertIs(raw["transaction_documents_required"], True)
        self.assertIs(fixture.engine.config.raw["transaction_documents_required"], True)
        self.assertEqual(list(fixture.engine.config.transaction_directory.glob("TXN_*/state.json")), [])
        checkout = fixture.engine.checkout(["app.py"], purpose="Verify a fresh generated mandatory projection policy without inherited transaction history")
        tx = checkout["transaction_id"]
        state = self.assert_special_projected(fixture, tx, "CHECKED_OUT")
        self.assertEqual(state["tx_event_sequence"], 1)
        self.assertTrue(state["tx_event"]["required_projection"])
        fixture.engine.abort(tx)
        self.assert_special_projected(fixture, tx, "ABORTED")

    def test_legacy_missing_projection_policy_is_not_silently_rewritten(self):
        from types import SimpleNamespace
        from runtime_sync_workshop.tx_lifecycle import requires_projection
        raw = {"schema": "runtime-sync-workshop-config/v1"}
        before = json.dumps(raw, sort_keys=True)
        engine = SimpleNamespace(config=SimpleNamespace(raw=raw))
        self.assertFalse(requires_projection(engine, {}))
        self.assertEqual(json.dumps(raw, sort_keys=True), before)
        self.assertNotIn("transaction_documents_required", raw)
        # Actual adopted-history projection remains covered by native receipt
        # tests; this unit test checks the legacy absent-key policy boundary.

    @unittest.skipUnless(sys.platform == "win32", "Windows open-handle rename acceptance")
    def test_projection_read_checks_release_sqlite_file_handles_without_gc(self):
        import gc
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        tx = fixture.engine.checkout(["app.py"], purpose="Verify projection read handles close before Windows temporary workspace cleanup")["transaction_id"]
        fixture.engine.abort(tx)
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        moved = fixture.database.with_suffix(".handle-check")
        try:
            assert_projected(fixture.engine, tx)
            # Isolated resource test only: Windows refuses this rename if the
            # preceding native read left a database handle open. No bytes edit.
            fixture.database.rename(moved)
            moved.rename(fixture.database)
        finally:
            if moved.exists():
                moved.rename(fixture.database)
            if enabled:
                gc.enable()

    def prepared_auxiliary(self):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        fixture, name = self.auxiliary_fixture()
        owner = AuxiliaryDocumentTransaction(fixture.engine)
        checkout = owner.checkout([name], purpose="Verify isolated terminal receipt and recovery boundaries without repeated Apply")
        tx = checkout["transaction_id"]
        target = Path(checkout["work_directory"]) / name
        target.write_text("Verified isolated final authority.\n", encoding="utf-8")
        owner.verify(tx)
        return fixture, owner, tx, name, target

    def prepared_bootstrap_receipt(self):
        import re
        from runtime_sync_workshop.repair import BootstrapRepair
        from kairos.governance import issue_external_edit_permit
        from kairos.heartbeat import run_heartbeat
        from workshop.tests import test_context_transactions as context_tests
        initialize = context_tests.initialize_universal_markdown
        def initial_two_line_project(**kwargs):
            # Define the initial fixture source before native intake/seal.
            (Path(kwargs["project_root"]) / "app.py").write_bytes(b"value = 1\nother = 2\n")
            return initialize(**kwargs)
        with patch.object(context_tests, "initialize_universal_markdown", side_effect=initial_two_line_project):
            fixture = self.fixture()
        config = fixture.engine.config
        external = next(config.blueprint_root.glob("PROJECT_CODE_*.md"))
        managed = config.managed_blueprint_root / external.name
        original = managed.read_text(encoding="utf-8")
        incomplete = re.sub(r"(?m)^revision = 1$", "revision = 2", original, count=1)
        incomplete, removed = re.subn(r"(?m)^(C:0001[^\n]*\n)(C:0002)", r"\1\n\2", incomplete, count=1)
        self.assertEqual(removed, 1)
        relative = "code/" + external.name
        issue_external_edit_permit(fixture.workspace, paths=[relative],
                                   reason="Isolated historical blank-noise ledger fixture before native bootstrap repair")
        # Explicit corruption fixture in its temporary project only. SQL is
        # populated by native heartbeat, never fabricated or hand-edited.
        managed.write_text(incomplete, encoding="utf-8")
        external.write_text(incomplete, encoding="utf-8")
        receipt = run_heartbeat(fixture.workspace, requested_mode="verify", changed_paths=[relative],
                                use_reconciliation=True, trigger="isolated-legacy-ledger-fixture")
        self.assertTrue(receipt["verified"], receipt)
        source_before = (fixture.project / "app.py").read_bytes()
        owner = BootstrapRepair(fixture.engine)
        result = owner.stage()
        tx = result["transaction_id"]
        self.assertTrue(result["changed_documents"])
        self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        return fixture, owner, tx

    def prepared_special_receipt(self, kind):
        if kind == "bootstrap":
            return self.prepared_bootstrap_receipt()
        from runtime_sync_workshop.universal_migration import UniversalSourceSetMigration
        fixture = self.fixture()
        owner = UniversalSourceSetMigration(fixture.engine)
        checked = owner.checkout(purpose="Verify exact source-set terminal receipt and recovery boundaries")
        candidate = Path(checked["candidate_root"])
        (candidate / "added_receipt_fixture.py").write_bytes(b"ADDED = 3\n")
        tx = checked["transaction_id"]
        owner.prepare(tx)
        owner.verify(tx)
        return fixture, owner, tx

    def test_partial_normal_and_source_set_apply_restores_freshness_and_projects(self):
        from runtime_sync_workshop import _core_apply, universal_migration
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        for kind in ("normal", "source_set"):
            with self.subTest(kind=kind):
                if kind == "normal":
                    fixture = self.fixture()
                    checkout = fixture.checkout()
                    fixture.stage_behavior(checkout)
                    fixture.prepare_and_verify(checkout)
                    tx, owner, module = checkout["transaction_id"], fixture.engine, _core_apply
                else:
                    fixture, owner, tx = self.prepared_special_receipt("source_set")
                    module = universal_migration
                root, state = fixture.engine._load_transaction(tx)
                sources = list(fixture.workspace.joinpath("code").glob("*.md"))
                sources += [p for p in (fixture.workspace / "docs").glob("*.md")]
                sources += [fixture.engine.config.dataflow_index]
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sources if p.is_file()}
                seal = fixture.engine.seal_path.read_bytes()
                original_copy = module.copy_exact
                faulted = []

                def fail_after_managed_write(source, target):
                    result = original_copy(source, target)
                    if not faulted and Path(target).parent == fixture.engine.config.managed_blueprint_root:
                        faulted.append(str(target))
                        raise OSError("isolated fault after managed document write")
                    return result

                with patch.object(module, "copy_exact", side_effect=fail_after_managed_write):
                    with self.assertRaises(WorkshopError) as caught:
                        owner.apply(tx)
                self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK", caught.exception.details)
                self.assertEqual(len(faulted), 1)
                self.assert_special_projected(fixture, tx, "ROLLED_BACK")
                for path, signature in before.items():
                    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), signature, str(path))
                self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
                self.assertFalse(pending_path(fixture.engine, tx).exists())
                self.assertFalse(fixture.engine.lease_path.exists())

    def _prove_bootstrap_partial_copy_rollback(self, role):
        from runtime_sync_workshop import repair as module
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.prepared_bootstrap_receipt()
        root, state = fixture.engine._load_transaction(tx)
        before_documents = {
            path: path.read_bytes()
            for row in state["changed_documents"]
            for path in (fixture.engine.config.blueprint_root / row["filename"],
                         fixture.engine.config.managed_blueprint_root / row["filename"])
        }
        before_mtimes = {path: path.stat().st_mtime_ns for path in before_documents}
        before_source = (fixture.project / "app.py").read_bytes()
        before_seal = fixture.engine.seal_path.read_bytes()
        copy_exact, save = module.copy_exact, fixture.engine._save_transaction
        faults, states = [], []

        def interrupted_copy(source, target):
            result = copy_exact(source, target)
            if not faults and Path(source).parent == root / "work" / ("blueprints" if role == "external" else "managed"):
                faults.append(str(target))
                raise OSError("Isolated failure after the first actual bootstrap target write")
            return result

        def observed_save(path, value, *, event, details=None):
            result = save(path, value, event=event, details=details)
            projected = self.assert_special_projected(fixture, tx, value["state"])
            states.append((projected["state"], projected["tx_event_id"]))
            return result

        with patch.object(module, "copy_exact", side_effect=interrupted_copy), \
             patch.object(fixture.engine, "_save_transaction", side_effect=observed_save):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK",
                         f"faults={faults}; states={states}; "
                         f"all_bytes_restored={all(path.read_bytes() == data for path, data in before_documents.items())}; "
                         f"changed_mtimes={[path.name for path, value in before_mtimes.items() if path.stat().st_mtime_ns != value]}; "
                         f"details={caught.exception.details}")
        self.assertEqual(len(faults), 1)
        self.assertEqual([state for state, _ in states], ["APPLYING", "ROLLED_BACK"])
        self.assertNotEqual(states[0][1], states[1][1])
        final = self.assert_special_projected(fixture, tx, "ROLLED_BACK")
        self.assertEqual(final["tx_event"]["event"], "BOOTSTRAP_APPLY_ROLLED_BACK")
        self.assertEqual(final["tx_event"]["previous_event_id"], states[0][1])
        for path, before in before_documents.items():
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, before_mtimes[path])
        self.assertEqual((fixture.project / "app.py").read_bytes(), before_source)
        self.assertEqual(fixture.engine.seal_path.read_bytes(), before_seal)
        self.assertFalse(fixture.engine.lease_path.exists())
        self.assertFalse(pending_path(fixture.engine, tx).exists())

    def test_bootstrap_preheartbeat_partial_copy_rolls_back_exact_bytes_and_projects(self):
        for role in ("external", "managed"):
            with self.subTest(role=role):
                self._prove_bootstrap_partial_copy_rollback(role)

    def test_bootstrap_rollback_rejects_untrusted_preimage_and_missing_freshness(self):
        fixture, owner, tx = self.prepared_bootstrap_receipt()
        root, state = fixture.engine._load_transaction(tx)
        rows = owner._backup_document_targets(root, state)
        row = next(item for item in rows if item["role"] == "managed")
        live, backup = Path(row["live"]), Path(row["backup"])
        before = (live.read_bytes(), live.stat().st_mtime_ns)
        backup.write_bytes(b"deliberately altered isolated preimage")
        result = owner._rollback_document_targets([row])
        self.assertFalse(result[0]["restored"])
        self.assertIn("preimage differs", result[0]["error"])
        self.assertEqual((live.read_bytes(), live.stat().st_mtime_ns), before)
        missing = dict(row)
        missing.pop("baseline_mtime_ns")
        result = owner._rollback_document_targets([missing])
        self.assertFalse(result[0]["restored"])
        self.assertIn("captured baseline modification time", result[0]["error"])
        self.assertEqual((live.read_bytes(), live.stat().st_mtime_ns), before)
        owner.abort(tx)
        self.assert_special_projected(fixture, tx, "ABORTED")

    def test_bootstrap_preimage_capture_change_refuses_and_revokes_unused_permit(self):
        from runtime_sync_workshop import repair as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.prepared_bootstrap_receipt()
        root, state = fixture.engine._load_transaction(tx)
        managed = fixture.engine.config.managed_blueprint_root / state["changed_documents"][0]["filename"]
        original = managed.read_bytes()
        state_bytes = (root / "state.json").read_bytes()
        original_seal = fixture.engine.seal_path.read_bytes()
        with closing(sqlite3.connect(fixture.database)) as connection:
            existing = {row[0] for row in connection.execute("SELECT permit_id FROM action_permits")}
        copy_exact = module.copy_exact
        injected = []

        def concurrent_change(source, target):
            if not injected and Path(source) == managed and Path(target).parent.name == "managed":
                # Simulate an untrusted concurrent writer only in the temporary fixture.
                managed.write_bytes(original + b"\nConcurrent fixture mutation.\n")
                injected.append(True)
            return copy_exact(source, target)

        with patch.object(module, "copy_exact", side_effect=concurrent_change):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "BOOTSTRAP_PREIMAGE_CHANGED")
        self.assertEqual(injected, [True])
        self.assertNotEqual(managed.read_bytes(), original)
        self.assertEqual((root / "state.json").read_bytes(), state_bytes)
        self.assertEqual(fixture.engine.seal_path.read_bytes(), original_seal)
        self.assertTrue(fixture.engine.lease_path.exists())
        with closing(sqlite3.connect(fixture.database)) as connection:
            new_permits = [(permit_id, status) for permit_id, status in
                           connection.execute("SELECT permit_id,status FROM action_permits")
                           if permit_id not in existing]
        self.assertEqual(len(new_permits), 1)
        self.assertEqual(new_permits[0][1], "REVOKED")

    def test_special_terminal_outages_recover_without_reapply_or_receipt_rewrite(self):
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.util import WorkshopError
        for kind in ("source_set", "bootstrap"):
            for durable in (False, True):
                with self.subTest(kind=kind, receipt_durable=durable):
                    fixture, owner, tx = self.prepared_special_receipt(kind)
                    prefix = "BOOTSTRAP-POSTCHECK_" if kind == "bootstrap" else "UNIVERSAL-SOURCE-SET-POSTCHECK_"
                    writer = module.atomic_write_json
                    def fault(path, payload):
                        if path.name.startswith(prefix):
                            if durable:
                                writer(path, payload)
                            raise OSError("isolated special receipt interruption")
                        return writer(path, payload)
                    with patch.object(module, "atomic_write_json", side_effect=fault):
                        with self.assertRaises(WorkshopError) as caught:
                            owner.apply(tx)
                    self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
                    state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                    self.assertEqual(state["terminal_receipt"]["status"], "PENDING")
                    self.assertNotIn("terminal_receipt_payload", state)
                    self.assertTrue(fixture.engine.lease_path.exists())
                    self.assertTrue(fixture.engine.status(persist=False)["seal"]["matches_current"])
                    before_source = (fixture.project / "app.py").read_bytes()
                    destination = fixture.engine.config.state_directory / state["terminal_receipt"]["path"]
                    original_receipt = destination.read_bytes() if durable else None
                    def no_receipt_rewrite(path, payload):
                        if durable and path == destination:
                            raise AssertionError("Existing exact receipt must not be rewritten")
                        return writer(path, payload)
                    with patch.object(owner.__class__, "apply", side_effect=AssertionError("No repeated Apply during recovery")):
                        with patch.object(module, "atomic_write_json", side_effect=no_receipt_rewrite):
                            result = fixture.engine.recover(tx)
                    final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                    self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
                    self.assertFalse(fixture.engine.lease_path.exists())
                    self.assertEqual((fixture.project / "app.py").read_bytes(), before_source)
                    if durable:
                        self.assertEqual(destination.read_bytes(), original_receipt)
                    root, _ = fixture.engine._load_transaction(tx)
                    document = fixture.workspace / document_path(tx)
                    before = [(root / "state.json").read_bytes(), document.read_bytes(), destination.read_bytes()]
                    replay = owner.recover(tx)
                    self.assertTrue(replay["replayed"])
                    self.assertEqual(replay["terminal_receipt_id"], result["terminal_receipt_id"])
                    self.assertEqual([(root / "state.json").read_bytes(), document.read_bytes(), destination.read_bytes()], before)

    def test_special_postcheck_seed_recovers_before_obligation_staging(self):
        for kind in ("source_set", "bootstrap"):
            with self.subTest(kind=kind):
                fixture, owner, tx = self.prepared_special_receipt(kind)
                with patch.object(fixture.engine, "_finish_transaction_receipt", side_effect=OSError("isolated terminal entry boundary")):
                    with self.assertRaises(OSError):
                        owner.apply(tx)
                state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertNotIn("terminal_receipt", state)
                seed = state["terminal_receipt_payload"]
                self.assertTrue(fixture.engine.lease_path.exists())
                result = fixture.engine.recover(tx)
                final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertNotIn("terminal_receipt_payload", final)
                self.assertEqual(final["terminal_receipt"]["payload"], seed)
                self.assertEqual(result["schema"], seed["schema"])
                self.assertFalse(fixture.engine.lease_path.exists())
                if kind == "bootstrap":
                    from runtime_sync_workshop.cli import _parser, execute
                    args = _parser().parse_args(["--config", str(fixture.engine.config.config_path), "bootstrap-transaction", tx])
                    view = execute(args)
                    self.assertEqual(view["transaction_id"], tx)
                    self.assertFalse(view["lease_owned"])

    def test_special_committed_receipt_conflict_refused_and_loss_reconstructed(self):
        from runtime_sync_workshop.util import WorkshopError
        for kind in ("source_set", "bootstrap"):
            with self.subTest(kind=kind):
                fixture, owner, tx = self.prepared_special_receipt(kind)
                result = owner.apply(tx)
                receipt = Path(result["receipt"])
                original = receipt.read_bytes()
                root, _ = fixture.engine._load_transaction(tx)
                before = (root / "state.json").read_bytes()
                receipt.write_bytes(b"{}")  # Disposable fixture only.
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.recover(tx)
                self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_CONFLICT")
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertEqual(receipt.read_bytes(), b"{}")
                receipt.unlink()  # Model loss of exact temporary derived receipt.
                fixture.engine.recover(tx)
                self.assertEqual(receipt.read_bytes(), original)
                self.assertEqual((root / "state.json").read_bytes(), before)

    def test_bootstrap_pending_receipt_refuses_runtime_drift(self):
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.prepared_special_receipt("bootstrap")
        writer = module.atomic_write_json
        def outage(path, payload):
            if path.name.startswith("BOOTSTRAP-POSTCHECK_"):
                raise OSError("isolated bootstrap receipt outage")
            return writer(path, payload)
        with patch.object(module, "atomic_write_json", side_effect=outage):
            with self.assertRaises(WorkshopError):
                owner.apply(tx)
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        source = fixture.project / "app.py"
        original = source.read_bytes()
        source.write_bytes(original + b"# isolated drift\n")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "RUNTIME_WRITE_INVARIANT_FAILED")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertTrue(fixture.engine.lease_path.exists())
        source.write_bytes(original)  # Restore only isolated fault.
        fixture.engine.recover(tx)
        self.assertFalse(fixture.engine.lease_path.exists())

    def interrupted_sealed_source_set(self, boundary):
        from runtime_sync_workshop import universal_migration as module
        from runtime_sync_workshop.util import WorkshopError
        from workshop.tests import test_context_transactions as context_tests
        initialize = context_tests.initialize_universal_markdown
        def initial_membership(**kwargs):
            project = Path(kwargs["project_root"])
            (project / "rename_me.py").write_bytes(b"RENAME = 1\n")
            (project / "delete_me.py").write_bytes(b"DELETE = 2\n")
            return initialize(**kwargs)
        with patch.object(context_tests, "initialize_universal_markdown", side_effect=initial_membership):
            fixture = self.fixture()
        owner = module.UniversalSourceSetMigration(fixture.engine)
        checked = owner.checkout(purpose="Verify source-set sealed recovery preserves create delete rename authority")
        candidate = Path(checked["candidate_root"])
        (candidate / "rename_me.py").rename(candidate / "renamed.py")
        (candidate / "delete_me.py").unlink()
        (candidate / "new_file.py").write_bytes(b"NEW = 3\n")
        tx = checked["transaction_id"]
        owner.prepare(tx)
        owner.verify(tx)
        writer = module.atomic_write_json
        saver = fixture.engine._save_transaction
        def interrupted_write(path, payload):
            writer(path, payload)
            if path == fixture.engine.seal_path and payload.get("transaction_id") == tx:
                raise OSError("isolated interruption after source-set postcheck seal")
        def interrupted_save(root, state, *, event, details=None):
            if event == "UNIVERSAL_SOURCE_SET_POSTCHECK_VERIFIED":
                raise OSError("isolated interruption before source-set source save")
            return saver(root, state, event=event, details=details)
        fault = (patch.object(module, "atomic_write_json", side_effect=interrupted_write)
                 if boundary == "after_seal" else patch.object(fixture.engine, "_save_transaction", side_effect=interrupted_save))
        with fault:
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assert_special_projected(fixture, tx, "RECOVERY_REQUIRED")
        self.assertTrue(fixture.engine.lease_path.exists())
        return fixture, owner, tx

    def test_source_set_sealed_recovery_reverifies_both_interruption_boundaries_without_apply(self):
        from runtime_sync_workshop import universal_migration as module
        from runtime_sync_workshop.cli import _parser, execute
        for boundary in ("after_seal", "before_source"):
            with self.subTest(boundary=boundary):
                fixture, owner, tx = self.interrupted_sealed_source_set(boundary)
                seal = fixture.engine.seal_path.read_bytes()
                source = {p.name: p.read_bytes() for p in fixture.project.glob("*.py")}
                with patch.object(module, "copy_exact", side_effect=AssertionError("No Apply-target copying during sealed recovery")):
                    with patch.object(owner.__class__, "apply", side_effect=AssertionError("Recovery must not repeat Apply")):
                        if boundary == "after_seal":
                            result = fixture.engine.recover(tx)
                        else:
                            args = _parser().parse_args(["--config", str(fixture.engine.config.config_path), "recover", tx])
                            result = execute(args)
                state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertEqual(state["terminal_receipt"]["status"], "COMMITTED")
                self.assertEqual(state["recovery_verification"]["prior_state"], "RECOVERY_REQUIRED")
                self.assertEqual(state["recovery_verification"]["health"]["verdict"], "PASS")
                self.assertEqual(result["heartbeat"]["heartbeat_id"], state["recovery_verification"]["heartbeat_id"])
                self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
                self.assertEqual({p.name: p.read_bytes() for p in fixture.project.glob("*.py")}, source)
                self.assertFalse((fixture.project / "rename_me.py").exists())
                self.assertFalse((fixture.project / "delete_me.py").exists())
                self.assertTrue((fixture.project / "renamed.py").exists())
                self.assertFalse(fixture.engine.lease_path.exists())
                root, _ = fixture.engine._load_transaction(tx)
                before = (root / "state.json").read_bytes()
                self.assertTrue(fixture.engine.recover(tx)["replayed"])
                self.assertEqual((root / "state.json").read_bytes(), before)

    def test_source_set_sealed_recovery_refuses_work_or_foreign_seal_drift(self):
        from runtime_sync_workshop.util import WorkshopError
        for fault in ("work", "seal"):
            with self.subTest(fault=fault):
                fixture, owner, tx = self.interrupted_sealed_source_set("after_seal")
                root, _ = fixture.engine._load_transaction(tx)
                before = (root / "state.json").read_bytes()
                target = root / "work" / "candidate" / "new_file.py" if fault == "work" else fixture.engine.seal_path
                original = target.read_bytes()
                if fault == "work":
                    target.write_bytes(original + b"# isolated unverified work\n")
                    expected = "SOURCE_SET_RECOVERY_WORK_DRIFT"
                else:
                    seal = json.loads(original)
                    seal["transaction_id"] = "TXN_" + "f" * 24
                    target.write_text(json.dumps(seal), encoding="utf-8")
                    expected = "SOURCE_SET_RECOVERY_SEAL_MISMATCH"
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.recover(tx)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertTrue(fixture.engine.lease_path.exists())
                target.write_bytes(original)  # Restore exact isolated fault only.
                fixture.engine.recover(tx)
                self.assertFalse(fixture.engine.lease_path.exists())

    def test_source_set_sealed_recovery_refuses_extra_static_membership(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.interrupted_sealed_source_set("after_seal")
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        added = fixture.project / "unapproved.py"
        added.write_bytes(b"UNAPPROVED = 1\n")  # Disposable Runtime drift only.
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "SOURCE_SET_RECOVERY_TARGET_DRIFT")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertTrue(fixture.engine.lease_path.exists())
        added.unlink()  # Restore only exact injected file.
        fixture.engine.recover(tx)

    def interrupted_unsealed_source_set(self, boundary="mirror", *, legacy=False):
        from runtime_sync_workshop import universal_migration as module
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.util import WorkshopError
        saver = WorkshopEngine._save_transaction
        def legacy_save(engine, root, state, *, event, details=None):
            if event == "UNIVERSAL_SOURCE_SET_CHECKOUT_STAGING":
                state.pop("baseline_seal_sha256", None)
            return saver(engine, root, state, event=event, details=details)
        if legacy:
            with patch.object(WorkshopEngine, "_save_transaction", legacy_save):
                fixture, owner, tx = self.prepared_special_receipt("source_set")
        else:
            fixture, owner, tx = self.prepared_special_receipt("source_set")
        if boundary == "mirror":
            with patch.object(fixture.engine, "_mirror", side_effect=OSError("isolated unsealed result")):
                with self.assertRaises(WorkshopError) as caught:
                    owner.apply(tx)
            self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
            expected = "RECOVERY_REQUIRED"
        elif boundary == "heartbeat":
            _, heartbeat = fixture.engine._import_kairos()
            original = heartbeat.run_heartbeat
            def stop_before_projection(*args, **kwargs):
                if kwargs.get("trigger") == f"runtime-sync-universal-source-set:{tx}":
                    raise SystemExit("isolated process-like cut before code heartbeat")
                return original(*args, **kwargs)
            with patch.object(heartbeat, "run_heartbeat", side_effect=stop_before_projection):
                with self.assertRaises(SystemExit):
                    owner.apply(tx)
            expected = "APPLYING"
        else:
            original = module.copy_exact
            def stop_before_first_target(source, destination):
                if destination == fixture.project / "added_receipt_fixture.py":
                    raise SystemExit("isolated cut before first target copy")
                return original(source, destination)
            with patch.object(module, "copy_exact", side_effect=stop_before_first_target):
                with self.assertRaises(SystemExit):
                    owner.apply(tx)
            expected = "APPLYING"
        self.assert_special_projected(fixture, tx, expected)
        self.assertTrue(fixture.engine.lease_path.exists())
        return fixture, owner, tx

    def test_source_set_unsealed_complete_result_recovers_before_or_after_heartbeat(self):
        from runtime_sync_workshop import universal_migration as module
        from runtime_sync_workshop.cli import _parser, execute
        for boundary in ("mirror", "heartbeat"):
            with self.subTest(boundary=boundary):
                fixture, owner, tx = self.interrupted_unsealed_source_set(boundary)
                original = {p.name:p.read_bytes() for p in fixture.project.glob("*.py")}
                seal = fixture.engine.seal_path.read_bytes()
                with patch.object(module, "copy_exact", side_effect=AssertionError("Recovery must not copy Apply targets")):
                    args = _parser().parse_args(["--config", str(fixture.engine.config.config_path), "recover", tx])
                    result = execute(args)
                state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertTrue(state["recovery_verification"]["seal_created"])
                self.assertEqual(state["terminal_receipt"]["status"], "COMMITTED")
                self.assertNotEqual(fixture.engine.seal_path.read_bytes(), seal)
                self.assertEqual({p.name:p.read_bytes() for p in fixture.project.glob("*.py")}, original)
                self.assertFalse(fixture.engine.lease_path.exists())
                self.assertEqual(result["state"], "POSTCHECK_VERIFIED")

    def test_source_set_unchanged_baseline_retires_without_apply(self):
        from runtime_sync_workshop import universal_migration as module
        fixture, owner, tx = self.interrupted_unsealed_source_set("before_copy")
        original = {p.name:p.read_bytes() for p in fixture.project.glob("*.py")}
        seal = fixture.engine.seal_path.read_bytes()
        with patch.object(module, "copy_exact", side_effect=AssertionError("Baseline retirement must not copy targets")):
            result = fixture.engine.recover(tx)
        self.assert_special_projected(fixture, tx, "ROLLED_BACK")
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertFalse(result["runtime_write_performed"])
        self.assertFalse(fixture.engine.lease_path.exists())
        self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
        self.assertEqual({p.name:p.read_bytes() for p in fixture.project.glob("*.py")}, original)

    def test_source_set_unsealed_recovery_refuses_each_changed_target_role(self):
        from runtime_sync_workshop.util import WorkshopError
        for role in ("candidate", "blueprints", "managed", "authority", "machine", "external-final"):
            with self.subTest(role=role):
                fixture, owner, tx = self.interrupted_unsealed_source_set()
                root, _ = fixture.engine._load_transaction(tx)
                before = (root / "state.json").read_bytes()
                seal = fixture.engine.seal_path.read_bytes()
                row = next(r for r in owner._work_package(root)["files"] if r["role"] == role)
                roots = {"candidate": owner.config.codebase_root, "blueprints": owner.config.blueprint_root,
                         "external-final": owner.config.blueprint_root, "managed": owner.config.managed_blueprint_root,
                         "authority": owner.config.kairos_workspace, "machine": owner.config.machine_root}
                target = roots[role] / row["path"]
                original = target.read_bytes()
                target.write_bytes(original + b"# isolated role drift\n")
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.recover(tx)
                self.assertIn(caught.exception.code, {"SOURCE_SET_RECOVERY_TARGET_DRIFT", "C_FAMILY_NORMAL_TRANSACTION_REQUIRED"})
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
                self.assertTrue(fixture.engine.lease_path.exists())
                target.write_bytes(original)  # Restore bytes only; a native quarantine remains authoritative.

    def test_source_set_unsealed_recovery_refuses_missing_baseline_pin_or_seal_drift(self):
        from runtime_sync_workshop.util import WorkshopError
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                fixture, owner, tx = self.interrupted_unsealed_source_set(legacy=legacy)
                root, _ = fixture.engine._load_transaction(tx)
                before = (root / "state.json").read_bytes()
                if not legacy:
                    seal = json.loads(fixture.engine.seal_path.read_bytes())
                    seal["created_at"] = "isolated changed baseline identity"
                    fixture.engine.seal_path.write_text(json.dumps(seal), encoding="utf-8")
                observed = fixture.engine.seal_path.read_bytes()
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.recover(tx)
                expected = "SOURCE_SET_RECOVERY_BASELINE_UNBOUND" if legacy else "SOURCE_SET_RECOVERY_SEAL_MISMATCH"
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertEqual(fixture.engine.seal_path.read_bytes(), observed)
                self.assertTrue(fixture.engine.lease_path.exists())

    def test_source_set_unsealed_failed_health_cannot_create_result_seal(self):
        import importlib
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.interrupted_unsealed_source_set()
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        seal = fixture.engine.seal_path.read_bytes()
        health = importlib.import_module("kairos.health")
        with patch.object(health, "health_audit", return_value={"verdict":"FAIL", "failures":["isolated health refusal"]}):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "SOURCE_SET_RECOVERY_STILL_BLOCKED")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
        self.assertTrue(fixture.engine.lease_path.exists())
        fixture.engine.recover(tx)

    def test_source_set_recovery_refuses_unpostchecked_candidate(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx = self.prepared_special_receipt("source_set")
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "TX_RECOVERY_ROUTE_UNSUPPORTED")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertTrue(fixture.engine.lease_path.exists())
        owner.abort(tx)

    def prepared_normal_receipt(self):
        from workshop.tests.test_metadata_gate import MetadataGateTests
        fixture = MetadataGateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.checkout()
        fixture.prepare_and_check()
        fixture.engine.verify(fixture.transaction_id)
        fixture.database = fixture.engine.config.kairos_database
        return fixture, fixture.transaction_id

    def test_normal_terminal_outage_or_write_then_crash_recovers_without_apply(self):
        from runtime_sync_workshop import engine as module, _core_apply
        from runtime_sync_workshop.util import WorkshopError
        for durable in (False, True):
            with self.subTest(receipt_durable=durable):
                fixture, tx = self.prepared_normal_receipt()
                writer = module.atomic_write_json
                def interrupted(path, payload):
                    if path.name.startswith("POSTCHECK_"):
                        if durable:
                            writer(path, payload)
                        raise OSError("isolated normal receipt outage")
                    return writer(path, payload)
                with patch.object(module, "atomic_write_json", side_effect=interrupted):
                    with self.assertRaises(WorkshopError) as caught:
                        fixture.engine.apply(tx)
                self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
                state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertEqual(state["terminal_receipt"]["status"], "PENDING")
                self.assertNotIn("postcheck_receipt_payload", state)
                self.assertTrue(fixture.engine.lease_path.exists())
                self.assertTrue(fixture.engine.status(persist=False)["seal"]["matches_current"])
                source = (fixture.project / "app.py").read_bytes()
                destination = fixture.engine.config.state_directory / state["terminal_receipt"]["path"]
                receipt_before = destination.read_bytes() if durable else None
                def no_receipt_rewrite(path, payload):
                    if durable and path == destination:
                        raise AssertionError("Durable receipt must not be rewritten")
                    return writer(path, payload)
                with patch.object(_core_apply, "copy_exact", side_effect=AssertionError("No Apply target copying during receipt recovery")):
                    with patch.object(fixture.engine, "apply", side_effect=AssertionError("Recovery must not repeat Apply")):
                        with patch.object(module, "atomic_write_json", side_effect=no_receipt_rewrite):
                            result = fixture.engine.recover(tx)
                final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
                self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
                self.assertFalse(fixture.engine.lease_path.exists())
                self.assertEqual((fixture.project / "app.py").read_bytes(), source)
                self.assertEqual(Path(result["receipt"]), destination)
                if durable:
                    self.assertEqual(destination.read_bytes(), receipt_before)
                root, _ = fixture.engine._load_transaction(tx)
                document = fixture.workspace / document_path(tx)
                before = [(root / "state.json").read_bytes(), document.read_bytes(), destination.read_bytes()]
                replay = fixture.engine.recover(tx)
                self.assertTrue(replay["replayed"])
                self.assertEqual([(root / "state.json").read_bytes(), document.read_bytes(), destination.read_bytes()], before)

    def test_normal_crash_before_receipt_obligation_recovers_exact_seed(self):
        from runtime_sync_workshop import _core_apply
        fixture, tx = self.prepared_normal_receipt()
        with patch.object(fixture.engine, "_finish_transaction_receipt", side_effect=OSError("isolated public terminal boundary interruption")):
            with self.assertRaises(OSError):
                fixture.engine.apply(tx)
        state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertTrue(fixture.engine.lease_path.exists())
        self.assertNotIn("terminal_receipt", state)
        seed = state["postcheck_receipt_payload"]
        with patch.object(_core_apply, "copy_exact", side_effect=AssertionError("Recovery must not copy Apply targets")):
            result = fixture.engine.recover(tx)
        final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertNotIn("postcheck_receipt_payload", final)
        self.assertEqual(final["terminal_receipt"]["payload"], seed)
        self.assertEqual(result["schema"], "runtime-sync-postcheck/v1")
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_normal_postheartbeat_recovery_commits_its_terminal_receipt(self):
        from runtime_sync_workshop import _core_apply
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.prepared_normal_receipt()
        with patch.object(fixture.engine, "_mirror", side_effect=OSError("isolated result mirror interruption")):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.apply(tx)
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assert_special_projected(fixture, tx, "RECOVERY_REQUIRED")
        self.assertTrue(fixture.engine.lease_path.exists())
        source = (fixture.project / "app.py").read_bytes()
        with patch.object(_core_apply, "copy_exact", side_effect=AssertionError("Recovery must not copy Apply targets")):
            result = fixture.engine.recover(tx)
        final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertEqual(result["schema"], "runtime-sync-recovery/v1")
        self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
        self.assertNotIn("postcheck_receipt_payload", final)
        self.assertEqual((fixture.project / "app.py").read_bytes(), source)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_normal_committed_receipt_conflict_refused_and_loss_reconstructed(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.prepared_normal_receipt()
        result = fixture.engine.apply(tx)
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        receipt = Path(result["receipt"])
        original = receipt.read_bytes()
        root, _ = fixture.engine._load_transaction(tx)
        document = fixture.workspace / document_path(tx)
        before = [(root / "state.json").read_bytes(), document.read_bytes()]
        receipt.write_bytes(b"{}")  # Disposable fixture fault only.
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_CONFLICT")
        self.assertEqual(receipt.read_bytes(), b"{}")
        self.assertEqual([(root / "state.json").read_bytes(), document.read_bytes()], before)
        receipt.unlink()  # Model loss of this exact disposable derived receipt.
        recovered = fixture.engine.recover(tx)
        self.assertTrue(recovered["replayed"])
        self.assertEqual(receipt.read_bytes(), original)
        self.assertEqual([(root / "state.json").read_bytes(), document.read_bytes()], before)

    def test_normal_pending_receipt_refuses_foreign_postcheck_seal(self):
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self.prepared_normal_receipt()
        writer = module.atomic_write_json
        def outage(path, payload):
            if path.name.startswith("POSTCHECK_"):
                raise OSError("isolated receipt outage")
            return writer(path, payload)
        with patch.object(module, "atomic_write_json", side_effect=outage):
            with self.assertRaises(WorkshopError):
                fixture.engine.apply(tx)
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        original = fixture.engine.seal_path.read_bytes()
        seal = json.loads(original)
        seal["transaction_id"] = "TXN_" + "f" * 24
        fixture.engine.seal_path.write_text(json.dumps(seal), encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.recover(tx)
        self.assertEqual(caught.exception.code, "TX_TERMINAL_POSTCHECK_DRIFT")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertTrue(fixture.engine.lease_path.exists())
        fixture.engine.seal_path.write_bytes(original)  # Restore only isolated fault.
        fixture.engine.recover(tx)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_auxiliary_terminal_write_outage_keeps_postcheck_and_recovers_without_apply(self):
        from runtime_sync_workshop import engine as engine_module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        writer = engine_module.atomic_write_json
        def fail_receipt(path, payload):
            if path.name.startswith("AUXILIARY-POSTCHECK_"):
                raise OSError("isolated terminal receipt outage")
            return writer(path, payload)
        with patch.object(engine_module, "atomic_write_json", side_effect=fail_receipt):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
        state = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertEqual(state["terminal_receipt"]["status"], "PENDING")
        self.assertTrue(fixture.engine.lease_path.exists())
        self.assertEqual((fixture.engine.config.blueprint_root / name).read_bytes(), target.read_bytes())
        self.assertTrue(fixture.engine.status(persist=False)["seal"]["matches_current"])
        with patch.object(owner.__class__, "apply", side_effect=AssertionError("Recovery must never repeat Apply")):
            result = fixture.engine.recover(tx)
        final = self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
        self.assertFalse(fixture.engine.lease_path.exists())
        document = fixture.workspace / document_path(tx)
        self.assertIn(final["terminal_receipt"]["payload_sha256"], document.read_text(encoding="utf-8"))
        root, _ = fixture.engine._load_transaction(tx)
        before = [(root / "state.json").read_bytes(), document.read_bytes(), Path(result["receipt"]).read_bytes()]
        replay = owner.recover(tx)
        from runtime_sync_workshop.cli import _parser, execute
        arguments = _parser().parse_args(["--config", str(fixture.engine.config.config_path), "auxiliary-recover", tx])
        cli_result = execute(arguments)
        self.assertTrue(cli_result["replayed"])
        self.assertEqual(cli_result["terminal_receipt_id"], result["terminal_receipt_id"])
        self.assertEqual(replay["terminal_receipt_id"], result["terminal_receipt_id"])
        self.assertEqual([(root / "state.json").read_bytes(), document.read_bytes(), Path(result["receipt"]).read_bytes()], before)

    def test_auxiliary_receipt_write_then_crash_reuses_exact_file_and_event_obligation(self):
        from runtime_sync_workshop import engine as engine_module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        writer = engine_module.atomic_write_json
        paths = []
        def after_write(path, payload):
            writer(path, payload)
            if path.name.startswith("AUXILIARY-POSTCHECK_"):
                paths.append(path)
                raise OSError("isolated crash after durable receipt before state acknowledgement")
        with patch.object(engine_module, "atomic_write_json", side_effect=after_write):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
        self.assertEqual(len(paths), 1)
        original = paths[0].read_bytes()
        def no_second_write(path, payload):
            if path == paths[0]:
                raise AssertionError("Already durable terminal receipt must not be rewritten")
            return writer(path, payload)
        with patch.object(engine_module, "atomic_write_json", side_effect=no_second_write):
            result = owner.recover(tx)
        self.assertEqual(Path(result["receipt"]), paths[0])
        self.assertEqual(paths[0].read_bytes(), original)
        self.assertEqual(len(list(paths[0].parent.glob("AUXILIARY-POSTCHECK_*.json"))), 1)
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")

    def test_auxiliary_committed_receipt_corruption_is_refused_and_missing_file_is_reconstructed(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        result = owner.apply(tx)
        receipt = Path(result["receipt"])
        original = receipt.read_bytes()
        root, state = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        receipt.write_bytes(b"{}")  # Isolated corruption fixture only.
        with self.assertRaises(WorkshopError) as caught:
            owner.recover(tx)
        self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_CONFLICT")
        self.assertEqual(receipt.read_bytes(), b"{}")
        self.assertEqual((root / "state.json").read_bytes(), before)
        receipt.unlink()  # Model loss of only this exact temporary receipt.
        recovered = owner.recover(tx)
        self.assertTrue(recovered["replayed"])
        self.assertEqual(receipt.read_bytes(), original)
        self.assertEqual((root / "state.json").read_bytes(), before)

    def test_auxiliary_candidate_change_after_verify_is_refused_before_live_write(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        live = fixture.engine.config.blueprint_root / name
        before = live.read_bytes()
        target.write_text("Unverified replacement authority.\n", encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            owner.apply(tx)
        self.assertEqual(caught.exception.code, "WORK_CHANGED_AFTER_VERIFY")
        self.assertEqual(live.read_bytes(), before)
        self.assert_special_projected(fixture, tx, "SHADOW_VERIFIED")
        owner.abort(tx)

    def test_auxiliary_incomplete_rollback_with_exact_candidate_recovers_without_copying_apply(self):
        from runtime_sync_workshop import auxiliary as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        copy = module.copy_exact
        def broken_rollback(source, destination):
            if source.parent.name == "rollback":
                raise OSError("isolated rollback write outage")
            return copy(source, destination)
        with patch.object(fixture.engine, "_mirror", side_effect=OSError("isolated mirror outage")):
            with patch.object(module, "copy_exact", side_effect=broken_rollback):
                with self.assertRaises(WorkshopError) as caught:
                    owner.apply(tx)
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assert_special_projected(fixture, tx, "RECOVERY_REQUIRED")
        self.assertTrue(fixture.engine.lease_path.exists())
        with patch.object(module, "copy_exact", side_effect=AssertionError("Recovery cannot copy Apply targets")):
            with patch.object(owner, "apply", side_effect=AssertionError("Recovery cannot Apply")):
                result = owner.recover(tx)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_auxiliary_prepostcheck_failure_restores_baseline_bytes_and_original_seal(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        live = fixture.engine.config.blueprint_root / name
        before = live.read_bytes()
        seal = fixture.engine.seal_path.read_bytes()
        with patch.object(fixture.engine, "_mirror", side_effect=OSError("isolated pre-seal mirror outage")):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK")
        self.assertEqual(live.read_bytes(), before)
        self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
        self.assertFalse(fixture.engine.lease_path.exists())
        self.assert_special_projected(fixture, tx, "ROLLED_BACK")
        self.assertTrue(fixture.engine.status(persist=False)["seal"]["matches_current"])

    def test_auxiliary_postseal_state_save_failure_keeps_result_and_recovers_without_apply(self):
        from runtime_sync_workshop import auxiliary as module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        save = fixture.engine._save_transaction
        def fail_postcheck(root, state, *, event, details=None):
            if event == "AUXILIARY_POSTCHECK_VERIFIED":
                raise OSError("isolated state save interruption after sealing exact result")
            return save(root, state, event=event, details=details)
        with patch.object(fixture.engine, "_save_transaction", side_effect=fail_postcheck):
            with self.assertRaises(WorkshopError) as caught:
                owner.apply(tx)
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        self.assert_special_projected(fixture, tx, "APPLYING")
        self.assertEqual((fixture.engine.config.blueprint_root / name).read_bytes(), target.read_bytes())
        self.assertTrue(fixture.engine.lease_path.exists())
        self.assertTrue(fixture.engine.status(persist=False)["seal"]["matches_current"])
        with patch.object(module, "copy_exact", side_effect=AssertionError("Recovery cannot reapply live files")):
            result = owner.recover(tx)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assert_special_projected(fixture, tx, "POSTCHECK_VERIFIED")
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_pending_terminal_receipt_refuses_live_drift_without_state_or_receipt_change(self):
        from runtime_sync_workshop import engine as engine_module
        from runtime_sync_workshop.util import WorkshopError
        fixture, owner, tx, name, target = self.prepared_auxiliary()
        writer = engine_module.atomic_write_json
        def fail_receipt(path, payload):
            if path.name.startswith("AUXILIARY-POSTCHECK_"):
                raise OSError("isolated terminal receipt outage")
            return writer(path, payload)
        with patch.object(engine_module, "atomic_write_json", side_effect=fail_receipt):
            with self.assertRaises(WorkshopError):
                owner.apply(tx)
        root, state = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        live = fixture.engine.config.blueprint_root / name
        live.write_text("Isolated unauthorized live drift.\n", encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            owner.recover(tx)
        self.assertEqual(caught.exception.code, "GUARDIAN_BASELINE_UNSEALED")
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertTrue(fixture.engine.lease_path.exists())
        self.assertFalse((fixture.engine.config.state_directory / state["terminal_receipt"]["path"]).exists())
        live.write_bytes(target.read_bytes())  # Restore only the isolated fault fixture.
        owner.recover(tx)

    def test_legacy_checkpoint_epoch_backfill_preserves_source_revision(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        document = fixture.workspace / document_path(tx)
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        before = document.read_bytes()
        old = json.loads(checkpoint.read_text(encoding="utf-8"))
        for field in ("source_epoch", "source_epoch_kind", "source_state_sha256", "source_state_utf8"):
            old.pop(field)
        checkpoint.write_text(json.dumps(old), encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            assert_projected(fixture.engine, tx)
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_OUTBOX_UNACKNOWLEDGED")
        fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(document.read_bytes(), before)
        assert_projected(fixture.engine, tx)
        fixture.engine.abort(tx)

    def test_newer_material_source_is_never_acknowledged_as_the_older_projection(self):
        from kairos import heartbeat
        from runtime_sync_workshop.util import WorkshopError
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        original = heartbeat.run_heartbeat
        changed = [False]
        def after_sql(*args, **kwargs):
            result = original(*args, **kwargs)
            if not changed[0] and kwargs.get("trigger", "").startswith("workshop-transaction-document:"):
                changed[0] = True
                root, newer = fixture.engine._load_transaction(tx)
                newer["fixture_newer_material_fact"] = "observed after the older SQL promotion"
                fixture.engine._save_transaction(root, newer, event="FIXTURE_NEWER_MATERIAL_SOURCE")
            return result
        with patch.object(heartbeat, "run_heartbeat", after_sql):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.annotate_transaction(tx, kind="observation", summary="The old projection must not acknowledge a newer material state", evidence="docs/APP_BEHAVIOR.md#s-behavior")
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        _, actual = fixture.engine._load_transaction(tx)
        marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        self.assertNotEqual(marker["event_id"], actual["tx_event_id"])
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Projection repair must not Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        assert_projected(fixture.engine, tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        self.assertIn(marker["event_id"], [event["event_id"] for event in actual["tx_event_history"]])
        self.assertIn(marker["event_id"], (fixture.workspace / document_path(tx)).read_text(encoding="utf-8"))
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], actual["tx_event_id"])
        fixture.engine.abort(tx)

    def test_projection_checkpoint_binds_material_epoch_and_full_observed_source(self):
        from runtime_sync_workshop.tx_lifecycle import SOURCE_EPOCH_KIND, source_epoch
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root, state = fixture.engine._load_transaction(tx)
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
        raw = (root / "state.json").read_bytes()
        self.assertEqual(receipt["source_state_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt["source_state_utf8"].encode(), raw)
        self.assertEqual(receipt["source_epoch"], source_epoch(state))
        self.assertEqual(receipt["source_epoch_kind"], SOURCE_EPOCH_KIND)
        self.assertEqual(state["tx_event"]["source_epoch"], receipt["source_epoch"])
        fixture.engine.abort(tx)

    def test_same_phase_material_change_is_a_new_projected_event(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root, state = fixture.engine._load_transaction(tx)
        sequence = state["tx_event_sequence"]
        state["fixture_material_fact"] = {"outcome": "new evidence before phase change"}
        fixture.engine._save_transaction(root, state, event="FIXTURE_SAME_PHASE_MATERIAL_CHANGE")
        _, after = fixture.engine._load_transaction(tx)
        self.assertEqual(after["state"], "CHECKED_OUT")
        self.assertEqual(after["tx_event_sequence"], sequence + 1)
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], after["tx_event_id"])
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        acknowledged = json.loads(json.loads(checkpoint.read_text(encoding="utf-8"))["source_state_utf8"])
        self.assertEqual(acknowledged["fixture_material_fact"], state["fixture_material_fact"])
        fixture.engine.abort(tx)

    def test_diagnostic_save_and_delivery_keep_revision_and_checkpoint_bytes(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root, state = fixture.engine._load_transaction(tx)
        document = fixture.workspace / document_path(tx)
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        before_doc, before_ack = document.read_bytes(), checkpoint.read_bytes()
        event = state["tx_event_id"]
        fixture.engine._save_transaction(root, state, event="FIXTURE_DIAGNOSTIC_ONLY", details={"message": "diagnostic observation only"})
        _, after = fixture.engine._load_transaction(tx)
        self.assertEqual(after["tx_event_id"], event)
        self.assertNotEqual((root / "state.json").read_bytes(), json.loads(before_ack)["source_state_utf8"].encode())
        assert_projected(fixture.engine, tx)
        result = fixture.engine.reconcile_transaction_document(tx)
        self.assertTrue(result["replayed"])
        self.assertEqual(document.read_bytes(), before_doc)
        self.assertEqual(checkpoint.read_bytes(), before_ack)
        fixture.engine.abort(tx)

    def test_epoch_and_snapshot_corruption_are_not_acknowledged_and_reconcile_keeps_revision(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        document = fixture.workspace / document_path(tx)
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        before = document.read_bytes()
        for field, value in (("source_epoch", "0" * 64), ("source_state_utf8", "{}")):
            corrupted = json.loads(checkpoint.read_text(encoding="utf-8"))
            corrupted[field] = value  # Isolated receipt-corruption fixture, never a live repair.
            checkpoint.write_text(json.dumps(corrupted), encoding="utf-8")
            with self.assertRaises(WorkshopError) as caught:
                assert_projected(fixture.engine, tx)
            self.assertEqual(caught.exception.code, "TX_DOCUMENT_OUTBOX_UNACKNOWLEDGED")
            fixture.engine.reconcile_transaction_document(tx)
            self.assertEqual(document.read_bytes(), before)
            assert_projected(fixture.engine, tx)
        fixture.engine.abort(tx)

    def test_projection_refuses_a_caller_only_state_view(self):
        from runtime_sync_workshop.tx_documents import update_document, pending_path
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        _, state = fixture.engine._load_transaction(tx)
        proposed = json.loads(json.dumps(state))
        proposed["fixture_unpersisted_material_fact"] = True
        document = fixture.workspace / document_path(tx)
        before = document.read_bytes()
        update_document(fixture.engine, proposed, event="FIXTURE_UNPERSISTED_INPUT")
        marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        self.assertEqual(marker["code"], "TX_DOCUMENT_SOURCE_STATE_MISMATCH")
        self.assertEqual(document.read_bytes(), before)
        fixture.engine.reconcile_transaction_document(tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        fixture.engine.abort(tx)

    def test_canonical_history_method_is_single_indexed_and_referenced_by_native_records(self):
        from kairos.transaction_document import HISTORY_METHOD_ID, HISTORY_METHOD_PATH
        from kairos.database import KnowledgeDatabase
        from kairos.search import search_database
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Create the canonical history method once and retain its explicit reference in each admitted record"
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        path = fixture.workspace / HISTORY_METHOD_PATH
        original = path.read_bytes()
        stamp = path.stat().st_mtime_ns
        metadata = split_frontmatter(original.decode()).metadata
        self.assertEqual(metadata["id"], HISTORY_METHOD_ID)
        self.assertEqual(metadata["type"], "documentation")
        self.assertNotIn("goal", metadata)
        report = fixture.engine.inspect_transaction_scope(tx)
        fixture.engine.bind_transaction_scope(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        source = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertIn(HISTORY_METHOD_ID, source["refs"]["history_method"])
        result = search_database(KnowledgeDatabase(fixture.database), "How is a legacy transaction explicitly admitted and later scope bound?", limit=5)
        self.assertIn(HISTORY_METHOD_ID, json.dumps(result))
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM artifacts WHERE artifact_id=?", (HISTORY_METHOD_ID,)).fetchone()[0], 1)

    def test_history_method_sql_commit_interruption_recovers_before_tx_projection(self):
        from kairos.transaction_document import HISTORY_METHOD_ID, HISTORY_METHOD_PATH
        from kairos import heartbeat
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Recover an interrupted canonical method promotion before acknowledging the transaction history projection"
        class Interrupted(BaseException):
            pass
        original = heartbeat.promote_document
        interrupted = [False]
        def after_method_sql(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if Path(path) == fixture.workspace / HISTORY_METHOD_PATH and not interrupted[0]:
                interrupted[0] = True
                raise Interrupted()
            return result
        with patch.object(heartbeat, "promote_document", after_method_sql):
            with self.assertRaises(Interrupted):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, saved = fixture.engine._load_transaction(tx)
        result = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertTrue(result["replayed"])
        _, after = fixture.engine._load_transaction(tx)
        self.assertEqual(saved["tx_event_id"], after["tx_event_id"])
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT revision FROM artifacts WHERE artifact_id=?", (HISTORY_METHOD_ID,)).fetchone()[0], 4)
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], after["tx_event_id"])

    def test_adopted_history_keeps_note_and_compaction_projection_under_optional_default(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Keep the per-transaction documentation obligation after explicit historical intake under optional defaults"
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        fixture.engine.config.raw["transaction_documents_required"] = False
        note = fixture.engine.annotate_transaction(tx, kind="observation", summary="The historical record remains explicitly unassigned", evidence="docs/APP_BEHAVIOR.md#s-behavior")
        _, state = fixture.engine._load_transaction(tx)
        self.assertTrue(note["documented"])
        self.assertTrue(state["tx_event"]["required_projection"])
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], state["tx_event_id"])
        analysis, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis)
        fixture.engine.config.raw["tx_retention_required"] = True
        fixture.engine.compact_transaction(tx)
        _, compacted = fixture.engine._load_transaction(tx)
        self.assertTrue(compacted["tx_event"]["required_projection"])
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], compacted["tx_event_id"])
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")

    def test_pending_adopted_history_blocks_checkout_under_optional_default(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        fixture.engine.config.raw["transaction_documents_required"] = False
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "A mandatory adopted history outbox must block new code work even when the global legacy flag is optional"
        with patch("runtime_sync_workshop.tx_documents.publish", side_effect=WorkshopError("INJECTED_PUBLICATION_OUTAGE", "fixture interruption")):
            with self.assertRaises(WorkshopError):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.checkout(["app.py"], purpose="Must not bypass a mandatory history outbox under optional defaults")
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        fixture.engine.reconcile_transaction_document(tx)
        next_tx = fixture.engine.checkout(["app.py"], purpose="Safe checkout after the exact historical event is reconciled")["transaction_id"]
        fixture.engine.abort(next_tx)

    def test_scope_binding_requires_existing_owner_sources_and_unchanged_receipt_binding(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, state, scope = self.historical_scope_fixture()
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Reject a historical claim whose Task and Goal source owners cannot actually be verified"
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        fresh = fixture.engine.inspect_transaction_scope(tx)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_TASK_SOURCE_MISSING")
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")

    def test_scope_intake_projection_is_required_even_when_default_documents_are_optional(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        fixture.engine.config.raw["transaction_documents_required"] = False
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "An explicit historical intake has mandatory projection independent of the legacy optional default"
        result = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, state = fixture.engine._load_transaction(tx)
        self.assertTrue(state["tx_event"]["required_projection"])
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], result["event_id"])
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")

    def test_scope_intake_precommit_marker_recovers_original_state_without_inventing_binding(self):
        from runtime_sync_workshop import engine as owner
        from runtime_sync_workshop.tx_documents import pending_path
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        root, original = fixture.engine._load_transaction(tx)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Interrupt after staging the historical event but before its authoritative terminal state commit"
        class Interrupted(BaseException):
            pass
        with patch.object(owner._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, saved = fixture.engine._load_transaction(tx)
        self.assertEqual(saved["tx_event_id"], original["tx_event_id"])
        self.assertNotIn("historical_scope", saved)
        self.assertTrue(pending_path(fixture.engine, tx).is_file())
        intended_id = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"]
        fixture.engine.reconcile_transaction_document(tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        _, recovered = fixture.engine._load_transaction(tx)
        self.assertEqual(recovered["tx_event_id"], intended_id)
        self.assertEqual(recovered["historical_scope"]["event_id"], intended_id)
        self.assertEqual(recovered["historical_scope"]["scope_status"], "SCOPE_UNRESOLVED")
        self.assertEqual(recovered["state"], original["state"])

    def test_native_history_intake_and_explicit_binding_preserve_prior_bytes_and_events(self):
        from runtime_sync_workshop.tx_documents import load_scope_transition
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        root, before = fixture.engine._load_transaction(tx)
        source = root / "state.json"
        document = fixture.workspace / document_path(tx)
        prior_state, prior_document = source.read_bytes(), document.read_bytes()
        reason = "Explicitly preserve this historical transaction before a separately reviewed scope assignment"
        report = fixture.engine.inspect_transaction_scope(tx)
        intake = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, state = fixture.engine._load_transaction(tx)
        self.assertEqual(state["tx_event"]["event"], "TX_HISTORY_INTAKE")
        self.assertEqual(state["tx_event_sequence"], before["tx_event_sequence"] + 1)
        self.assertTrue(state["tx_event"]["required_projection"])
        self.assertEqual(state["tx_event"]["scope_receipt_sha256"], state["historical_scope"]["receipt_sha256"])
        metadata = split_frontmatter(document.read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")
        self.assertEqual(metadata["state"], "blocked")
        self.assertNotIn("goal", metadata)
        receipt = load_scope_transition(fixture.engine, state)
        self.assertEqual(receipt["previous_state_utf8"].encode(), prior_state)
        self.assertEqual(receipt["previous_document_utf8"].encode(), prior_document)
        stable = document.read_bytes()
        replay = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["event_id"], intake["event_id"])
        self.assertEqual(document.read_bytes(), stable)
        fresh = fixture.engine.inspect_transaction_scope(tx)
        bound = fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        _, after = fixture.engine._load_transaction(tx)
        self.assertEqual(after["tx_event"]["event"], "TX_SCOPE_BOUND")
        self.assertEqual(after["tx_event_sequence"], state["tx_event_sequence"] + 1)
        self.assertEqual(after["original_scope"]["binding_kind"], "HISTORICAL_VERIFIED")
        metadata = split_frontmatter(document.read_text(encoding="utf-8")).metadata
        for field, key in (("goal", "goal_id"), ("milestone", "milestone_id"), ("task", "task_id"), ("loop", "loop")):
            self.assertEqual(metadata[field], before["original_scope"][key])
        self.assertEqual(metadata["tx_event_id"], bound["event_id"])
        receipt = load_scope_transition(fixture.engine, after)
        self.assertEqual(len(receipt["owner_sources"]), 2)
        replay = fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["event_id"], bound["event_id"])
        self.assertFalse(fixture.engine.lease_path.exists())
        from runtime_sync_workshop.util import WorkshopError
        counterprobe = json.loads(json.dumps(after))
        counterprobe["original_scope"]["loop"] += 1
        with self.assertRaises(WorkshopError) as caught:
            load_scope_transition(fixture.engine, counterprobe)
        self.assertEqual(caught.exception.code, "TX_SCOPE_RECEIPT_INVALID")
        with closing(sqlite3.connect(fixture.database)) as connection:
            projected = json.loads(connection.execute("SELECT metadata_json FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone()[0])
        self.assertEqual(projected["tx_event_id"], after["tx_event_id"])

    def test_unresolved_intake_remains_searchable_and_cannot_bind_current_task(self):
        from kairos.database import KnowledgeDatabase
        from kairos.search import search_database
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Preserve an unassigned historical record without inventing a current project association"
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        path = fixture.workspace / document_path(tx)
        metadata = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")
        self.assertNotIn("task", metadata)
        result = search_database(KnowledgeDatabase(fixture.database), "HISTORICAL_UNRESOLVED/" + tx, limit=10)
        self.assertIn(document_id(tx), json.dumps(result))
        fresh = fixture.engine.inspect_transaction_scope(tx)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_EVIDENCE_UNRESOLVED")

    def test_native_vault_scope_witness_survives_legacy_state_loss_and_can_bind(self):
        from runtime_sync_workshop.tx_retention import snapshot
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        root, original = fixture.engine._load_transaction(tx)
        archived = snapshot(fixture.engine, tx, include_entire_tx=True)
        # Isolated legacy-loss fixture: the actual native vault retains the old observation.
        legacy = json.loads(json.dumps(original))
        legacy.pop("original_scope")
        (root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
        report = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(report["status"], "VERIFIED")
        witness = next(row for row in report["evidence"] if row.get("source", {}).get("manifest"))
        self.assertEqual(witness["source"]["manifest"]["sha256"], archived["sha256"])
        reason = "Bind only the original observation retained in a verified native complete transaction snapshot"
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        fresh = fixture.engine.inspect_transaction_scope(tx)
        fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        _, state = fixture.engine._load_transaction(tx)
        self.assertEqual(state["original_scope"]["loop"], original["original_scope"]["loop"])
        self.assertEqual(state["original_scope"]["task_id"], original["original_scope"]["task_id"])

    def test_scope_loss_archive_does_not_waive_unrelated_material_drift(self):
        from runtime_sync_workshop.tx_retention import snapshot
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        root, original = fixture.engine._load_transaction(tx)
        snapshot(fixture.engine, tx, include_entire_tx=True)
        legacy = json.loads(json.dumps(original))
        legacy.pop("original_scope")
        legacy["fixture_unrelated_material_drift"] = "No archive proves this modification"
        (root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
        before = (root / "state.json").read_bytes()
        report = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(report["status"], "VERIFIED")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
                reason="A verified historical scope witness cannot authorize unrelated transaction source drift")
        self.assertEqual(caught.exception.code, "TX_EVENT_INTENT_PENDING")
        self.assertEqual((root / "state.json").read_bytes(), before)

    def test_scope_transition_refuses_active_lease_stale_review_and_missing_intake(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Review exact historical observations without overlapping active code mutation or stale approval"
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_LEASE_ACTIVE")
        fixture.engine.abort(tx)
        root, before = fixture.engine._load_transaction(tx)
        raw = (root / "state.json").read_bytes()
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.intake_transaction_history(tx, expected_report_sha256="0" * 64, reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_INSPECTION_STALE")
        self.assertEqual((root / "state.json").read_bytes(), raw)
        report = fixture.engine.inspect_transaction_scope(tx)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_INTAKE_REQUIRED")

    def test_conflicting_scope_claims_remain_quarantined_after_intake(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, root, state, scope = self.historical_scope_fixture()
        path = root / "baseline" / "conflict.json"
        path.parent.mkdir()
        raw = json.dumps({"schema": "werkfaden-historical-scope-receipt/v1", "transaction_id": tx,
                          "workspace_id": scope["workspace_id"], "scope": {**scope, "loop": 11}}).encode()
        path.write_bytes(raw)
        state["historical_scope_receipts"] = [{"tier": "TX_STATE", "path": "baseline/conflict.json", "sha256": hashlib.sha256(raw).hexdigest()}]
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Quarantine contradictory historical observations while retaining all previous source evidence"
        self.assertEqual(report["status"], "CONFLICT")
        fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        fresh = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(fresh["status"], "CONFLICT")
        self.assertTrue(fresh["conflicts"])
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256=fresh["report_sha256"], reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_EVIDENCE_UNRESOLVED")
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")
        self.assertNotIn("goal", metadata)

    def test_pending_scope_intake_recovers_without_a_second_transition(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        report = fixture.engine.inspect_transaction_scope(tx)
        reason = "Recover a scope intake publication failure from its already committed event and immutable receipt"
        with patch("runtime_sync_workshop.tx_documents.publish", side_effect=WorkshopError("INJECTED_SQL_OUTAGE", "fixture interruption")):
            with self.assertRaises(WorkshopError):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, saved = fixture.engine._load_transaction(tx)
        result = fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"], reason=reason)
        _, after = fixture.engine._load_transaction(tx)
        self.assertTrue(result["replayed"])
        self.assertEqual(after["tx_event_id"], saved["tx_event_id"])
        self.assertEqual(after["tx_event_sequence"], saved["tx_event_sequence"])
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], saved["tx_event_id"])

    def test_history_receipt_corruption_and_cli_contract_fail_closed(self):
        from runtime_sync_workshop import cli
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        args = cli._parser().parse_args(["--config", str(fixture.engine.config.config_path), "tx-scope-inspect", tx])
        report = cli.execute(args)
        reason = "Explicit CLI legacy intake with exact inspection identity and preserved prior source bytes"
        args = cli._parser().parse_args(["--config", str(fixture.engine.config.config_path), "tx-history-intake", tx,
                                        "--expected-report-sha256", report["report_sha256"], "--reason", reason])
        result = cli.execute(args)
        self.assertEqual(result["scope"]["scope_status"], "SCOPE_UNRESOLVED")
        _, state = fixture.engine._load_transaction(tx)
        receipt = fixture.engine.config.state_directory / "transaction-scope-history" / tx / (state["historical_scope"]["receipt_sha256"] + ".json")
        receipt.write_bytes(receipt.read_bytes() + b"corrupt")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.inspect_transaction_scope(tx)
            fixture.engine.bind_transaction_scope(tx, expected_report_sha256="0" * 64, reason=reason)
        self.assertEqual(caught.exception.code, "TX_SCOPE_RECEIPT_INVALID")

    def historical_scope_fixture(self, *, recorded=True):
        from kairos.workspace import load_config
        fixture = self.fixture()
        tx = "TXN_" + "d" * 24
        root = fixture.engine._transaction_path(tx)
        root.mkdir(parents=True)
        scope = {"workspace_id": load_config(fixture.workspace)["workspace_id"],
                 "goal_id": "GOAL_HISTORICAL", "milestone_id": "MILESTONE_HISTORICAL",
                 "task_id": "TASK_HISTORICAL", "loop": 3}
        state = {"transaction_id": tx, "state": "ABORTED", "baseline_package_sha256": "b" * 64}
        if recorded:
            state["original_scope"] = scope
        # Synthetic legacy controlled state in an isolated unit fixture only.
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return fixture, tx, root, state, scope

    def test_existing_document_declaration_alone_does_not_become_checkout_evidence(self):
        from kairos.transaction_document import render_transaction_document
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        path = fixture.workspace / document_path(tx)
        path.parent.mkdir(exist_ok=True)
        path.write_text(render_transaction_document(
            transaction=state, workspace_id=scope["workspace_id"], goal_id=scope["goal_id"],
            milestone_id=scope["milestone_id"], task_id=scope["task_id"], loop=scope["loop"]), encoding="utf-8")
        before = path.read_bytes()
        result = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(result["status"], "MISSING")
        self.assertEqual(result["observed_claims"], scope)
        self.assertIsNone(result["scope"])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(result["automatic_binding"])

    def test_pinned_evidence_tiers_are_ordered_and_preserve_exact_file_identity(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        state["historical_scope_receipts"] = []
        sources = (("TRANSACTION_DOCUMENT", fixture.workspace, "reports/history.json"),
                   ("LOOP_ARCHIVE", fixture.workspace, "archive/history.json"),
                   ("TASK_GOAL", fixture.workspace, "tasks/history.json"),
                   ("TX_STATE", root, "baseline/history.json"))
        for tier, owner, relative in sources:
            path = owner / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({"schema": "werkfaden-historical-scope-receipt/v1", "transaction_id": tx,
                              "workspace_id": scope["workspace_id"], "scope": scope}).encode()
            path.write_bytes(raw)
            state["historical_scope_receipts"].append({"tier": tier, "path": relative,
                                                      "sha256": hashlib.sha256(raw).hexdigest()})
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        result = fixture.engine.inspect_transaction_scope(tx)
        eligible = [row for row in result["evidence"] if row.get("binding_eligible")]
        self.assertEqual([row["tier"] for row in eligible],
                         ["TX_STATE", "TASK_GOAL", "LOOP_ARCHIVE", "TRANSACTION_DOCUMENT"])
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["scope"], scope)
        for row in eligible:
            self.assertEqual(len(row["source"]["sha256"]), 64)
        self.assertFalse(result["automatic_binding"])

    def test_historical_inspection_detects_transaction_change_during_discovery(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture()
        original = Path.read_bytes
        reads = [0]
        def concurrent_observation(path):
            raw = original(path)
            if path == root / "state.json":
                reads[0] += 1
                if reads[0] == 2:
                    return raw.replace(b'"loop": 3', b'"loop": 4')
            return raw
        with patch.object(Path, "read_bytes", concurrent_observation):
            result = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(result["status"], "CONFLICT")
        self.assertIsNone(result["scope"])
        self.assertTrue(any("changed during historical discovery" in row["reason"] for row in result["evidence"]))

    def test_historical_inspection_retains_loop_three_without_reading_live_scope(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture()
        before = (root / "state.json").read_bytes()
        current_path = fixture.workspace / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        current["loop"] = 11  # Synthetic active observation; not authority for historical attribution.
        current_path.write_text(json.dumps(current), encoding="utf-8")
        current_bytes = current_path.read_bytes()
        self.assertEqual(json.loads(current_bytes)["loop"], 11)
        original = Path.read_bytes
        original_text = Path.read_text
        def no_current_text(path, *args, **kwargs):
            if path.name in {"current.json", "runtime_state.json"}:
                raise AssertionError("Historical inspection must not read active scope")
            return original_text(path, *args, **kwargs)
        def no_current(path):
            if path.name in {"current.json", "runtime_state.json"}:
                raise AssertionError("Historical inspection must not read active scope")
            return original(path)
        with patch.object(Path, "read_bytes", no_current), patch.object(Path, "read_text", no_current_text):
            first = fixture.engine.inspect_transaction_scope(tx)
            second = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(first["status"], "VERIFIED")
        self.assertEqual(first["scope"], scope)
        self.assertEqual(first["scope"]["loop"], 3)
        self.assertEqual(current_path.read_bytes(), current_bytes)
        self.assertFalse(first["automatic_binding"])
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        self.assertEqual((root / "state.json").read_bytes(), before)
        self.assertFalse((fixture.workspace / document_path(tx)).exists())

    def test_missing_historical_scope_stays_unresolved(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        result = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(result["status"], "MISSING")
        self.assertEqual(result["scope_status"], "SCOPE_UNRESOLVED")
        self.assertIsNone(result["scope"])
        self.assertFalse(result["automatic_binding"])
        self.assertEqual([row["tier"] for row in result["evidence"]],
                         ["TX_STATE", "GUARDIAN", "TASK_GOAL", "LOOP_ARCHIVE", "TRANSACTION_DOCUMENT"])

    def test_pinned_snapshot_is_identified_but_conflicting_receipts_are_not_merged(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        payload = {"schema": "werkfaden-historical-scope-receipt/v1", "transaction_id": tx,
                   "workspace_id": scope["workspace_id"], "scope": scope}
        path = root / "baseline" / "historical-scope.json"
        path.parent.mkdir()
        raw = json.dumps(payload).encode()
        path.write_bytes(raw)
        state["historical_scope_receipts"] = [{"tier": "TX_STATE", "path": "baseline/historical-scope.json",
                                                "sha256": hashlib.sha256(raw).hexdigest()}]
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        result = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["scope"]["loop"], 3)
        identified = next(row for row in result["evidence"] if row.get("binding_eligible"))
        self.assertEqual(identified["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        state["original_scope"] = {**scope, "loop": 11}
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        conflict = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(conflict["status"], "CONFLICT")
        self.assertIsNone(conflict["scope"])
        self.assertEqual(conflict["conflicts"][0]["field"], "loop")
        self.assertFalse(conflict["automatic_binding"])

    def test_historical_reference_hash_identity_path_and_missing_file_fail_closed(self):
        for failure in ("hash", "identity", "path", "missing"):
            with self.subTest(failure=failure):
                fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
                path = root / "baseline" / "scope.json"
                path.parent.mkdir()
                payload = {"schema": "werkfaden-historical-scope-receipt/v1", "transaction_id": tx,
                           "workspace_id": scope["workspace_id"], "scope": scope}
                if failure == "identity":
                    payload["transaction_id"] = "TXN_" + "e" * 24
                raw = json.dumps(payload).encode()
                if failure != "missing":
                    path.write_bytes(raw)
                state["historical_scope_receipts"] = [{"tier": "TX_STATE",
                    "path": "../current.json" if failure == "path" else "baseline/scope.json",
                    "sha256": "0" * 64 if failure == "hash" else hashlib.sha256(raw).hexdigest()}]
                (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
                result = fixture.engine.inspect_transaction_scope(tx)
                self.assertEqual(result["status"], "MISSING" if failure == "missing" else "CONFLICT")
                self.assertIsNone(result["scope"])

    def test_legacy_guardian_scope_cannot_invent_loop_or_authorize_reassignment(self):
        from runtime_sync_workshop.util import canonical_json, sha256_text
        fixture, tx, root, state, scope = self.historical_scope_fixture(recorded=False)
        directory = fixture.engine.config.state_directory / "guardian-v2"
        directory.mkdir(parents=True)
        guardian_id = "GRD2_" + "a" * 24
        event = {"sequence": 1, "kind": "TX_BIND", "prev_hash": "0" * 64,
                 "details": {"transaction_id": tx, "baseline_package_sha256": state["baseline_package_sha256"]}}
        event["event_hash"] = sha256_text(canonical_json(event))
        payload = {"schema": "werkfaden-guardian/v2", "guardian_id": guardian_id,
                   "events": [event], "head_hash": event["event_hash"],
                   "project_scope": {key: scope[key] for key in ("goal_id", "milestone_id", "task_id")},
                   "patch_session": {"transactions": [{"transaction_id": tx}]}}
        path = directory / (guardian_id + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(result["status"], "MISSING")
        self.assertNotIn("loop", result["observed_claims"])
        self.assertEqual(result["observed_claims"]["task_id"], scope["task_id"])
        event["details"]["transaction_id"] = "TXN_" + "f" * 24
        path.write_text(json.dumps(payload), encoding="utf-8")
        corrupt = fixture.engine.inspect_transaction_scope(tx)
        self.assertEqual(corrupt["status"], "CONFLICT")
        self.assertIsNone(corrupt["scope"])

    def test_historical_guardian_scan_budget_is_explicit_and_not_an_absence_proof(self):
        fixture, tx, root, state, scope = self.historical_scope_fixture()
        directory = fixture.engine.config.state_directory / "guardian-v2"
        directory.mkdir(parents=True)
        for index in range(257):
            (directory / ("GRD2_" + format(index, "024x") + ".json")).write_text("{}", encoding="utf-8")
        result = fixture.engine.inspect_transaction_scope(tx)
        self.assertFalse(result["discovery_complete"])
        self.assertEqual(result["status"], "MISSING")
        self.assertIsNone(result["scope"])
        self.assertTrue(any("256-file budget" in row["reason"] for row in result["evidence"]))

    def test_orphan_retirement_stamps_and_projects_a_new_event_without_a_lease(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        _, before = fixture.engine._load_transaction(tx)
        # Isolated abandoned-checkout scenario through the native lease owner.
        fixture.engine._release_lease(tx)
        fixture.engine.retire_orphan_transaction(
            tx, reason="Explicit isolated pre-apply orphan retirement; candidate bytes must remain archived")
        _, after = fixture.engine._load_transaction(tx)
        self.assertFalse(fixture.engine.lease_path.exists())
        self.assertEqual(after["state"], "ABORTED_ORPHANED_CHECKOUT")
        self.assertEqual(after["tx_event_sequence"], before["tx_event_sequence"] + 1)
        self.assertEqual(after["tx_event"]["event"], "TX_RECYCLING_ORPHAN_RETIRED")
        metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], after["tx_event_id"])
        with closing(sqlite3.connect(fixture.database)) as connection:
            projected = json.loads(connection.execute(
                "SELECT metadata_json FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone()[0])
        self.assertEqual(projected["tx_event_id"], after["tx_event_id"])

    def test_publication_resume_is_hash_bound_and_never_renews_or_widens_a_grant(self):
        from datetime import timedelta
        from kairos import governance
        from kairos.util import sha256_text, atomic_write_text
        fixture = self.fixture()
        fixture.checkout()
        relative = "docs/APP_BEHAVIOR.md"
        path = fixture.workspace / relative
        original = path.read_text(encoding="utf-8")
        intended = original + "\nPublication recovery fixture.\n"
        request = dict(paths=[relative], reason="Isolated exact publication recovery acceptance",
                       publication_id="FIXTURE_PUBLICATION_001",
                       expected_sha256={relative: sha256_text(intended)})
        permit = governance.issue_external_edit_permit(fixture.workspace, **request)
        replay = governance.issue_external_edit_permit(fixture.workspace, **request)
        self.assertEqual(replay["permit_id"], permit["permit_id"])
        with closing(sqlite3.connect(fixture.database)) as connection:
            expires = connection.execute("SELECT expires_at FROM action_permits WHERE permit_id=?",
                                         (permit["permit_id"],)).fetchone()[0]
        with patch.object(governance, "_now", return_value=datetime.fromisoformat(expires.replace("Z", "+00:00")) + timedelta(seconds=1)):
            with self.assertRaisesRegex(governance.GovernanceError, "expired"):
                governance.issue_external_edit_permit(fixture.workspace, **request)
        with self.assertRaisesRegex(governance.GovernanceError, "identity or scope conflict"):
            governance.issue_external_edit_permit(fixture.workspace, **{**request, "expected_sha256": {relative: "0" * 64}})
        with self.assertRaisesRegex(governance.GovernanceError, "already covers"):
            governance.issue_external_edit_permit(fixture.workspace, **{**request, "publication_id": "FIXTURE_UNRELATED"})
        scope = governance.runtime_scope(fixture.workspace)
        with patch.object(governance, "runtime_scope", return_value={**scope, "task_id": "TASK_FOREIGN"}):
            with self.assertRaisesRegex(governance.GovernanceError, "identity or scope conflict"):
                governance.issue_external_edit_permit(fixture.workspace, **request)
        atomic_write_text(path, intended)
        first = governance.guard_freshness_changes(fixture.workspace, changed=[path], missing=[], reason="Isolated authorized publication")
        resumed = governance.issue_external_edit_permit(fixture.workspace, **request)
        self.assertEqual(resumed["permit_id"], permit["permit_id"])
        recovered = governance.guard_freshness_changes(
            fixture.workspace, changed=[path], missing=[], reason="Explicit exact publication recovery",
            resume_publication_permit_id=permit["permit_id"])
        self.assertEqual(recovered["attributed_paths"], [relative])
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT expires_at FROM action_permits WHERE permit_id=?",
                                               (permit["permit_id"],)).fetchone()[0], expires)
            self.assertEqual(connection.execute("SELECT count(*) FROM governed_action_receipts WHERE permit_id=?",
                                               (permit["permit_id"],)).fetchone()[0], 1)
        # Unpermitted bytes in an isolated fixture model hostile drift, not a repair.
        atomic_write_text(path, intended + "Unattributed drift.\n")
        with self.assertRaisesRegex(governance.GovernanceError, "outside its original or intended hash"):
            governance.issue_external_edit_permit(fixture.workspace, **request)
        with self.assertRaisesRegex(governance.GovernanceError, "bytes differ"):
            governance.guard_freshness_changes(
                fixture.workspace, changed=[path], missing=[], reason="Reject changed recovery bytes",
                resume_publication_permit_id=permit["permit_id"])
        self.assertEqual(first["external_action"]["permit_id"], permit["permit_id"])

    def test_active_publication_cannot_attribute_unintended_bytes(self):
        from kairos import governance
        from kairos.util import sha256_text, atomic_write_text
        fixture = self.fixture()
        fixture.checkout()
        relative = "docs/APP_BEHAVIOR.md"
        path = fixture.workspace / relative
        text = path.read_text(encoding="utf-8")
        permit = governance.issue_external_edit_permit(
            fixture.workspace, paths=[relative], reason="Reject publication bytes outside exact intended hash",
            publication_id="FIXTURE_EXACT_TARGET", expected_sha256={relative: sha256_text(text + "\nExpected.\n")})
        atomic_write_text(path, text + "\nUnexpected.\n")
        with self.assertRaisesRegex(governance.GovernanceError, "exact intended hash"):
            governance.guard_freshness_changes(
                fixture.workspace, changed=[path], missing=[], reason="Negative target binding probe",
                resume_publication_permit_id=permit["permit_id"])

    def test_generic_edit_receipt_does_not_grant_publication_recovery(self):
        from kairos import governance
        from kairos.util import atomic_write_text
        fixture = self.fixture()
        fixture.checkout()
        relative = "docs/APP_BEHAVIOR.md"
        path = fixture.workspace / relative
        permit = governance.issue_external_edit_permit(
            fixture.workspace, paths=[relative], reason="Generic edit has no publication resume authority")
        atomic_write_text(path, path.read_text(encoding="utf-8") + "\nGeneric edit.\n")
        governance.guard_freshness_changes(fixture.workspace, changed=[path], missing=[], reason="Generic edit acceptance")
        with self.assertRaisesRegex(governance.GovernanceError, "exact original task scope"):
            governance.guard_freshness_changes(
                fixture.workspace, changed=[path], missing=[], reason="Reject generic historical receipt reuse",
                resume_publication_permit_id=permit["permit_id"])

    def test_projection_recovers_each_native_commit_boundary_without_reapplying(self):
        from contextlib import ExitStack
        from runtime_sync_workshop import engine as owner, tx_documents
        from kairos import heartbeat, util

        class ProcessInterrupted(BaseException):
            pass

        points = ("before_state_save", "after_state_save", "before_markdown_write",
                  "after_markdown_write", "before_sql_promotion", "after_sql_promotion",
                  "before_projection_checkpoint")
        for point in points:
            with self.subTest(killpoint=point):
                fixture = self.fixture()
                tx = fixture.checkout()["transaction_id"]
                _, before = fixture.engine._load_transaction(tx)
                document = fixture.workspace / document_path(tx)
                interrupted = [False]

                def inject(original, predicate, after=False):
                    def call(*args, **kwargs):
                        if predicate(*args, **kwargs) and not interrupted[0]:
                            interrupted[0] = True
                            if after:
                                original(*args, **kwargs)
                            raise ProcessInterrupted(point)
                        return original(*args, **kwargs)
                    return call

                with ExitStack() as stack:
                    if point in {"before_state_save", "after_state_save"}:
                        stack.enter_context(patch.object(owner._CoreWorkshopEngine, "_save_transaction", inject(
                            owner._CoreWorkshopEngine._save_transaction,
                            lambda *a, **k: k.get("event") == "TX_NOTE", point == "after_state_save")))
                    elif point in {"before_markdown_write", "after_markdown_write"}:
                        stack.enter_context(patch.object(util, "atomic_write_text", inject(
                            util.atomic_write_text, lambda path, *a, **k: Path(path) == document,
                            point == "after_markdown_write")))
                    elif point in {"before_sql_promotion", "after_sql_promotion"}:
                        stack.enter_context(patch.object(heartbeat, "promote_document", inject(
                            heartbeat.promote_document, lambda path, *a, **k: Path(path) == document,
                            point == "after_sql_promotion")))
                    else:
                        stack.enter_context(patch.object(tx_documents, "atomic_write_json", inject(
                            tx_documents.atomic_write_json,
                            lambda path, *a, **k: "transaction-document-projections" in Path(path).parts)))
                    with self.assertRaises(ProcessInterrupted):
                        fixture.engine.annotate_transaction(
                            tx, kind="hypothesis", summary="Isolated crash recovery acceptance probe",
                            evidence="docs/APP_BEHAVIOR.md#s-behavior")
                self.assertTrue(interrupted[0])
                _, saved = fixture.engine._load_transaction(tx)
                if point == "before_state_save":
                    self.assertEqual(saved["tx_event_id"], before["tx_event_id"])
                else:
                    self.assertEqual(saved["tx_event_sequence"], before["tx_event_sequence"] + 1)
                self.assertTrue(tx_documents.pending_path(fixture.engine, tx).is_file())
                pending_event = json.loads(tx_documents.pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"]
                with patch.object(fixture.engine, "apply", side_effect=AssertionError("Reconcile must not Apply")):
                    fixture.engine.reconcile_transaction_document(tx)
                    tx_documents.assert_projected(fixture.engine, tx)
                    _, recovered = fixture.engine._load_transaction(tx)
                    self.assertEqual(recovered["tx_event_id"], pending_event)
                    self.assertEqual(len(recovered.get("transaction_notes", [])), 1)
                    self.assertFalse(tx_documents.pending_path(fixture.engine, tx).exists())
                    recovered_bytes = document.read_bytes()
                    fixture.engine.reconcile_transaction_document(tx)
                    self.assertEqual(document.read_bytes(), recovered_bytes)
                fixture.engine.abort(tx)

    def test_scope_conflict_preserves_document_sql_and_pending_event_identity(self):
        from runtime_sync_workshop.tx_documents import pending_path, update_document
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        path = fixture.workspace / document_path(tx)
        before = path.read_bytes()
        with closing(sqlite3.connect(fixture.database)) as connection:
            projected = connection.execute("SELECT revision,content_sha256 FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone()
        _, state = fixture.engine._load_transaction(tx)
        proposed = json.loads(json.dumps(state))
        proposed["original_scope"]["loop"] += 1  # Isolated contradictory input, not a live state edit.
        update_document(fixture.engine, proposed, event="SCOPE_COUNTERPROBE")
        marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        self.assertEqual(marker["code"], "TX_DOCUMENT_SCOPE_CONFLICT")
        self.assertEqual(marker["event_id"], state["tx_event_id"])
        self.assertEqual(marker["event_sequence"], state["tx_event_sequence"])
        self.assertEqual(marker["details"]["scope_status"], "CONFLICT")
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT revision,content_sha256 FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone(), projected)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.checkout(["app.py"], purpose="Must not open another transaction while scope evidence conflicts")
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        # Re-read the unchanged authoritative state, never clear the marker by hand.
        fixture.engine.reconcile_transaction_document(tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        self.assertEqual(path.read_bytes(), before)

    def test_historical_scope_resolver_never_reads_current_state_or_hides_partial_conflict(self):
        from runtime_sync_workshop.tx_documents import _sync_scope
        from runtime_sync_workshop.util import WorkshopError
        old = {"goal": "GOAL_EXAMPLE", "milestone": "MILESTONE_EXAMPLE", "task": "TASK_EXAMPLE", "loop": 3, "scope_status": "BOUND"}
        with patch.object(Path, "read_text", side_effect=AssertionError("Historical scope must not consult current state")):
            self.assertEqual(_sync_scope(Path("unread-current-state"), old, {}), ("GOAL_EXAMPLE", "MILESTONE_EXAMPLE", "TASK_EXAMPLE", 3))
            self.assertEqual(_sync_scope(Path("unread-current-state"), {"scope_status": "SCOPE_UNRESOLVED"}, {}), (None, None, None, None))
            with self.assertRaises(WorkshopError) as caught:
                _sync_scope(Path("unread-current-state"), old, {"original_scope": {"loop": 11}})
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SCOPE_CONFLICT")
        self.assertEqual(caught.exception.details["conflicts"]["loop"], {"document": 3, "transaction": 11})

    def test_unresolved_or_foreign_scope_cannot_be_silently_bound(self):
        from runtime_sync_workshop.tx_documents import _sync_scope
        from runtime_sync_workshop.util import WorkshopError
        scope = {"goal_id": "GOAL_EXAMPLE", "milestone_id": "MILESTONE_EXAMPLE", "task_id": "TASK_EXAMPLE", "loop": 3, "workspace_id": "WORKSPACE_SOURCE"}
        with self.assertRaises(WorkshopError) as caught:
            _sync_scope(Path("unused"), {"scope_status": "SCOPE_UNRESOLVED"}, {"original_scope": scope})
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SCOPE_REBIND_REQUIRED")
        with self.assertRaises(WorkshopError) as caught:
            _sync_scope(Path("unused"), None, {"original_scope": scope}, expected_workspace_id="WORKSPACE_OTHER")
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SCOPE_WORKSPACE_MISMATCH")
        for invalid in (0, -1, True, "3"):
            with self.subTest(loop=invalid), self.assertRaises(WorkshopError) as caught:
                _sync_scope(Path("unused"), None, {"original_scope": {**scope, "loop": invalid}})
            self.assertEqual(caught.exception.code, "TX_DOCUMENT_SCOPE_INVALID")

    def test_failed_projection_keeps_the_precommit_outbox_identity_contract(self):
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        failure = WorkshopError("INJECTED_PROJECTION_FAILURE", "Isolated projection failure", details={"cause": "fixture"})
        with patch("runtime_sync_workshop.tx_lifecycle.atomic_write_json", wraps=atomic_write_json) as staged:
            with patch("runtime_sync_workshop.tx_documents.publish", side_effect=failure):
                with self.assertRaises(WorkshopError):
                    fixture.engine.annotate_transaction(tx, kind="hypothesis", summary="The isolated model needs a bounded projection counterprobe", evidence="docs/APP_BEHAVIOR.md#s-behavior")
        _, state = fixture.engine._load_transaction(tx)
        marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        staged_record = staged.call_args.args[1]
        for key in ("schema", "event_id", "event_sequence", "candidate_sha256", "baseline_sha256", "scope_receipt_sha256"):
            self.assertEqual(marker[key], staged_record[key], key)
        self.assertEqual(marker["event_id"], state["tx_event_id"])
        self.assertEqual(marker["details"], {"cause": "fixture"})
        fixture.engine.reconcile_transaction_document(tx)
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        fixture.engine.abort(tx)

    def test_metadata_review_transition_has_editing_event_and_exact_projection(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        state_path = fixture.engine.config.transaction_directory / tx / "state.json"
        before = json.loads(state_path.read_text(encoding="utf-8"))
        with self.assertRaises(WorkshopError):
            fixture.engine.prepare(tx)
        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(after["state"], "AWAITING_METADATA_REVIEW")
        self.assertEqual(after["tx_event"]["phase"], "EDITING")
        self.assertEqual(after["tx_event_sequence"], before["tx_event_sequence"] + 1)
        self.assertEqual(after["tx_event"]["baseline_sha256"], after["baseline_package_sha256"])
        self.assertTrue(after["tx_event"]["required_projection"])
        self.assertIsNotNone(datetime.fromisoformat(after["tx_event"]["occurred_at"].replace("Z", "+00:00")).tzinfo)
        self.assertIsNotNone(datetime.fromisoformat(after["original_scope"]["observed_at"].replace("Z", "+00:00")).tzinfo)
        header = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(header["workshop_state"], after["state"])
        self.assertEqual(header["tx_event_id"], after["tx_event_id"])
        fixture.engine.abort(tx)

    def test_unknown_lifecycle_state_is_rejected_without_writing_state_or_outbox(self):
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        root = fixture.engine.config.transaction_directory / tx
        before = (root / "state.json").read_bytes()
        for unsupported in ("UNREGISTERED_TRANSITION", "", None):
            with self.subTest(state=unsupported):
                candidate = json.loads(before)
                candidate["state"] = unsupported
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine._save_transaction(root, candidate, event="UNREGISTERED_EVENT")
                self.assertEqual(caught.exception.code, "TX_LIFECYCLE_UNKNOWN_STATE")
                self.assertEqual(caught.exception.details["phase"], "UNKNOWN_UNSUPPORTED")
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertFalse(pending_path(fixture.engine, tx).exists())
        fixture.engine.abort(tx)

    def test_verified_scope_loss_intent_recovers_before_state_commit(self):
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.tx_retention import snapshot
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        root, original = fixture.engine._load_transaction(tx)
        snapshot(fixture.engine, tx, include_entire_tx=True)
        legacy = json.loads(json.dumps(original)); legacy.pop("original_scope")
        (root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
        report = fixture.engine.inspect_transaction_scope(tx)
        class Interrupted(BaseException):
            pass
        with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
                    reason="Recover exact native archive-backed scope intake after its precommit interruption")
        intended = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"]
        fixture.engine.reconcile_transaction_document(tx)
        recovered = fixture.engine._load_transaction(tx)[1]
        self.assertEqual(recovered["tx_event_id"], intended)
        self.assertEqual(recovered["tx_event_source_gap"]["reason"], "ARCHIVED_ORIGINAL_SCOPE_FIELD_LOSS")
        assert_projected(fixture.engine, tx)

    def test_first_checkout_exception_resolves_intent_after_native_lease_release(self):
        from runtime_sync_workshop import engine as module, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=OSError("first commit interrupted")):
            with self.assertRaises(OSError):
                fixture.checkout()
        self.assertFalse(fixture.engine.lease_path.exists())
        roots = list(lifecycle.intent_directory(fixture.engine).iterdir())
        self.assertEqual(len(roots), 1)
        tx = roots[0].name
        intended = lifecycle.event_intents(fixture.engine, tx)[0][1]["target_state"]["tx_event_id"]
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("Failed checkout recovery cannot Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        recovered = fixture.engine._load_transaction(tx)[1]
        self.assertEqual(recovered["state"], "ABORTED_ORPHANED_CHECKOUT")
        ids = [e["event_id"] for e in recovered.get("tx_event_history", [])]
        ids += [e["event"]["event_id"] for e in recovered.get("tx_event_resolutions", [])]
        self.assertIn(intended, ids)
        self.assertFalse(fixture.engine.lease_path.exists())
        self.assertEqual(lifecycle.event_intents(fixture.engine, tx), [])
        assert_projected(fixture.engine, tx)

    def test_first_checkout_process_interruption_replays_its_exact_intent(self):
        from runtime_sync_workshop import engine as module, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        class Interrupted(BaseException):
            pass
        with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.checkout()
        self.assertTrue(fixture.engine.lease_path.exists())
        tx = json.loads(fixture.engine.lease_path.read_text(encoding="utf-8"))["transaction_id"]
        intended = lifecycle.event_intents(fixture.engine, tx)[0][1]["target_state"]
        fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(fixture.engine._load_transaction(tx)[1], intended)
        assert_projected(fixture.engine, tx)
        fixture.engine.abort(tx)

    def test_native_postcheck_precommit_recovery_preserves_all_four_owner_results(self):
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        for kind in ("normal", "source_set", "bootstrap", "auxiliary"):
            with self.subTest(kind=kind):
                if kind == "normal":
                    fixture = self.fixture(); owner = fixture.engine
                    checked = fixture.checkout(); tx = checked["transaction_id"]
                    fixture.stage_behavior(checked); fixture.prepare_and_verify(checked)
                elif kind == "auxiliary":
                    fixture, owner, tx, _, _ = self.prepared_auxiliary()
                else:
                    fixture, owner, tx = self.prepared_special_receipt(kind)
                save = module._CoreWorkshopEngine._save_transaction
                class Interrupted(BaseException):
                    pass
                def interrupt(engine, root, state, *, event, **kwargs):
                    if state.get("state") == "POSTCHECK_VERIFIED" and event.endswith("POSTCHECK_VERIFIED"):
                        raise Interrupted()
                    return save(engine, root, state, event=event, **kwargs)
                with patch.object(module._CoreWorkshopEngine, "_save_transaction", interrupt):
                    with self.assertRaises(Interrupted):
                        owner.apply(tx)
                intended = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"]
                with patch.object(owner.__class__, "apply", side_effect=AssertionError("Recovery must not repeat Apply")):
                    fixture.engine.reconcile_transaction_document(tx)
                    self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], intended)
                    assert_projected(fixture.engine, tx)
                    owner.recover(tx)
                self.assertFalse(fixture.engine.lease_path.exists())

    def test_safe_abort_preserves_an_unrelated_pending_marker(self):
        from runtime_sync_workshop.tx_documents import pending_path, pending_record
        from runtime_sync_workshop.tx_lifecycle import event_intents
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture(); tx = fixture.checkout()["transaction_id"]
        _, before = fixture.engine._load_transaction(tx)
        record = pending_record(before, event="UNRESOLVED_LEGACY_OBLIGATION", reason="Isolated independent marker witness")
        record["event_id"] = "TXE_" + "F" * 32
        record["event_sequence"] = before["tx_event_sequence"] + 100
        marker = pending_path(fixture.engine, tx)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(record), encoding="utf-8"); original = marker.read_bytes()
        with self.assertRaises(WorkshopError):
            fixture.engine.abort(tx)
        self.assertEqual(fixture.engine._load_transaction(tx)[1]["state"], "ABORTED")
        self.assertEqual(marker.read_bytes(), original)
        self.assertTrue(event_intents(fixture.engine, tx))
        # Only this isolated fixture introduced the unsupported witness. Its
        # removal below restores that known fixture; production cannot do this.
        marker.unlink()
        fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(event_intents(fixture.engine, tx), [])

    def test_generic_replay_rejects_a_reparse_transaction_before_state_access(self):
        if sys.platform != "win32":
            self.skipTest("real Windows junction required")
        import _winapi
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture(); tx = fixture.checkout()["transaction_id"]
        class Interrupted(BaseException):
            pass
        with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="A controlled event intent must never replay through a junction",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        root = fixture.engine.config.transaction_directory / tx
        kept = root.with_name("kept-" + tx)
        root.rename(kept)
        try:
            _winapi.CreateJunction(str(kept), str(root))
            self.assertTrue(root.is_junction())
            before = (kept / "state.json").read_bytes()
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.reconcile_transaction_document(tx)
            self.assertEqual(caught.exception.code, "PATH_LINK_UNSAFE")
            self.assertEqual((kept / "state.json").read_bytes(), before)
        finally:
            if root.is_junction():
                self.assertEqual(root.resolve(), kept.resolve())
                root.rmdir()
            kept.rename(root)
        fixture.engine.reconcile_transaction_document(tx)
        fixture.engine.abort(tx)

    def test_cancelled_guardian_checkout_stays_pending_without_reviving_authority(self):
        from workshop.tests.test_guardian_engine_boundary import GuardianEngineBoundaryTests
        from runtime_sync_workshop import engine as module, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        support = GuardianEngineBoundaryTests()
        fixture, engine, guardian, gid, ticket = support._guarded_transaction_fixture()
        self.addCleanup(support.doCleanups)
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=OSError("first guarded commit interrupted")):
                with self.assertRaises(OSError):
                    engine.checkout(["app.py"], purpose="Prove cancelled checkout cannot renew Guardian mutation authority",
                                    guardian_id=gid, guardian_state_ticket=ticket)
            self.assertFalse(engine.lease_path.exists())
            roots = list(lifecycle.intent_directory(engine).iterdir()); self.assertEqual(len(roots), 1)
            tx = roots[0].name; marker = pending_path(engine, tx); before = marker.read_bytes()
            state_before = guardian._load(gid)
            with self.assertRaises(WorkshopError) as blocked:
                engine.reconcile_transaction_document(tx)
            self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
            self.assertEqual(marker.read_bytes(), before)
            self.assertEqual(guardian._load(gid), state_before)
            self.assertFalse(engine.lease_path.exists())
            self.assertFalse((engine._transaction_path(tx) / "state.json").exists())
            with self.assertRaises(WorkshopError) as unsupported:
                engine.guardian_reconcile(gid)
            self.assertEqual(unsupported.exception.code, "GUARDIAN_RECONCILE_NOT_REQUIRED")
            self.assertEqual(len(lifecycle.event_intents(engine, tx)), 1)
        # This is required negative coverage of an explicit remaining gap, not
        # successful recovery or acceptance of the complete Guardian protocol.

    def test_compiler_postcheck_precommit_recovery_preserves_native_result(self):
        from workshop.tests.test_authority_migration import AuthorityMigrationTests
        from runtime_sync_workshop.migration import AuthorityMigration
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        original = AuthorityMigration.apply
        observed = []
        class Interrupted(BaseException):
            pass
        def recover_commit(owner, tx):
            root, state = owner.engine._load_transaction(tx)
            if owner._work_package(root, state)["package_sha256"] != state["verified_work_package_sha256"]:
                return original(owner, tx)
            save = module._CoreWorkshopEngine._save_transaction
            def interrupt(engine, root, state, *, event, **kwargs):
                if event == "AUTHORITY_POSTCHECK_VERIFIED":
                    raise Interrupted()
                return save(engine, root, state, event=event, **kwargs)
            with patch.object(module._CoreWorkshopEngine, "_save_transaction", interrupt):
                with self.assertRaises(Interrupted):
                    original(owner, tx)
            intended = json.loads(pending_path(owner.engine, tx).read_text(encoding="utf-8"))["event_id"]
            with patch.object(owner, "apply", side_effect=AssertionError("Compiler recovery must not reapply")):
                owner.engine.reconcile_transaction_document(tx)
                self.assertEqual(owner.engine._load_transaction(tx)[1]["tx_event_id"], intended)
                assert_projected(owner.engine, tx)
                result = owner.recover(tx)
            self.assertFalse(owner.engine.lease_path.exists())
            observed.append(intended)
            return result
        case = AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
        self.addCleanup(case.doCleanups)
        with patch.object(AuthorityMigration, "apply", recover_commit):
            case.test_header_add_remove_and_source_change_migrate_atomically()
        self.assertEqual(len(observed), 1)

    def test_guarded_abort_recovers_prior_intent_then_requires_a_fresh_retry(self):
        from workshop.tests.test_guardian_engine_boundary import GuardianEngineBoundaryTests
        from runtime_sync_workshop import engine as module
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.util import WorkshopError
        support = GuardianEngineBoundaryTests()
        fixture, engine, guardian, gid, ticket = support._guarded_transaction_fixture()
        self.addCleanup(support.doCleanups)
        class Interrupted(BaseException):
            pass
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            tx = engine.checkout(["app.py"], purpose="Prove Guardian cannot skip an interrupted native note during Abort",
                                 guardian_id=gid, guardian_state_ticket=ticket)["transaction_id"]
            with patch.object(module._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
                with self.assertRaises(Interrupted):
                    engine.annotate_transaction(tx, kind="hypothesis",
                        summary="The pending note must survive a guarded Abort retry",
                        evidence="docs/APP_BEHAVIOR.md#s-behavior")
            pending = json.loads(pending_path(engine, tx).read_text(encoding="utf-8"))
            with self.assertRaises(WorkshopError) as retry:
                engine.abort(tx)
            self.assertEqual(retry.exception.code, "TX_OPERATION_RETRY_REQUIRED")
            recovered = engine._load_transaction(tx)[1]
            self.assertEqual(recovered["tx_event_id"], pending["event_id"])
            self.assertEqual(recovered["state"], "CHECKED_OUT")
            self.assertEqual(len(recovered["transaction_notes"]), 1)
            self.assertTrue(engine.lease_path.exists())
            self.assertEqual(guardian._load(gid)["events"][-1]["details"]["tx_event_id"], pending["event_id"])
            assert_projected(engine, tx)
            engine.abort(tx)
            final = engine._load_transaction(tx)[1]
            self.assertEqual(final["state"], "ABORTED")
            self.assertEqual(final["tx_event_sequence"], pending["event_sequence"] + 1)
            self.assertEqual(len(final["transaction_notes"]), 1)
            self.assertFalse(engine.lease_path.exists())
            self.assertEqual(guardian._find_tx(guardian._load(gid), tx)["tx_event_id"], final["tx_event_id"])
            assert_projected(engine, tx)

    def test_reconcile_preserves_pending_until_guardian_observes_saved_event(self):
        from workshop.tests.test_guardian_engine_boundary import GuardianEngineBoundaryTests
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        support = GuardianEngineBoundaryTests()
        fixture, engine, guardian, gid, ticket = support._guarded_transaction_fixture()
        self.addCleanup(support.doCleanups)
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            tx = engine.checkout(["app.py"], purpose="Require Guardian acknowledgement before document projection recovery",
                                 guardian_id=gid, guardian_state_ticket=ticket)["transaction_id"]
            observe = guardian.observe_transaction_state
            def outage(transaction_id, *, event, state, **kwargs):
                if state.get("tx_event_sequence") == 2:
                    raise WorkshopError("INJECTED_GUARD_OBSERVATION_FAILURE", "isolated Guardian observation outage")
                return observe(transaction_id, event=event, state=state, **kwargs)
            with patch.object(guardian, "observe_transaction_state", side_effect=outage):
                with self.assertRaises(WorkshopError):
                    engine.annotate_transaction(tx, kind="observation",
                        summary="A saved event with missing Guardian observation must remain pending",
                        evidence="docs/APP_BEHAVIOR.md#s-behavior")
                marker = pending_path(engine, tx); before = marker.read_bytes()
                state_before = (engine._transaction_path(tx) / "state.json").read_bytes()
                with self.assertRaises(WorkshopError) as blocked:
                    engine.reconcile_transaction_document(tx)
                self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                self.assertEqual(marker.read_bytes(), before)
                self.assertEqual((engine._transaction_path(tx) / "state.json").read_bytes(), state_before)
                self.assertEqual(guardian._find_tx(guardian._load(gid), tx)["tx_event_sequence"], 1)
            engine.reconcile_transaction_document(tx)
            actual = engine._load_transaction(tx)[1]
            self.assertEqual(guardian._find_tx(guardian._load(gid), tx)["tx_event_id"], actual["tx_event_id"])
            self.assertFalse(marker.exists())
            engine.abort(tx)

    def test_recycling_review_waits_for_a_generic_pre_marker_intent(self):
        from runtime_sync_workshop import tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes(); marker = pending_path(fixture.engine, tx)
        write = lifecycle.atomic_write_json
        class Interrupted(BaseException):
            pass
        def before_marker(path, value):
            if Path(path) == marker:
                raise Interrupted()
            return write(path, value)
        with patch.object(lifecycle, "atomic_write_json", before_marker):
            with self.assertRaises(Interrupted):
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="Finish this exact generic event before creating a specialized review",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        intended = lifecycle.event_intents(fixture.engine, tx)[0][1]["target_state"]["tx_event_id"]
        self.assertFalse(marker.exists())
        with self.assertRaises(WorkshopError) as blocked:
            fixture.engine.submit_recycling(tx, analysis)
        self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(blocked.exception.references["items"][0]["event_id"], intended)
        self.assertFalse((root / "recycling-review-intent.json").exists())
        self.assertEqual((root / "state.json").read_bytes(), before)
        fixture.engine.reconcile_transaction_document(tx)
        analysis, _ = self.completed_analysis(fixture, tx)
        self.assertIn(fixture.engine.submit_recycling(tx, analysis)["status"], {"REVIEWED", "ALREADY_REVIEWED"})

    def test_pending_review_blocks_terminal_history_before_new_state_or_receipts(self):
        from runtime_sync_workshop import tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        root, _ = fixture.engine._load_transaction(tx)
        marker = pending_path(fixture.engine, tx); write = lifecycle.atomic_write_json
        class Interrupted(BaseException):
            pass
        def before_marker(path, value):
            if Path(path) == marker:
                raise Interrupted()
            return write(path, value)
        with patch.object(lifecycle, "atomic_write_json", before_marker):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        before = (root / "state.json").read_bytes()
        review = root / "recycling-review-intent.json"; review_before = review.read_bytes()
        report = fixture.engine.inspect_transaction_scope(tx)
        operations = [lambda: fixture.engine.reconcile_transaction_document(tx),
            lambda: fixture.engine.annotate_transaction(tx, kind="observation",
                summary="This terminal history write must wait for its original review",
                evidence="docs/APP_BEHAVIOR.md#s-behavior"),
            lambda: fixture.engine.intake_transaction_history(tx, expected_report_sha256=report["report_sha256"],
                reason="A historical intake must not create a receipt that strands the current native review")]
        for operation in operations:
            with self.assertRaises(WorkshopError) as blocked:
                operation()
            self.assertEqual(blocked.exception.code, "TX_RECYCLING_INTENT_PENDING")
            self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
            self.assertEqual((root / "state.json").read_bytes(), before)
            self.assertEqual(review.read_bytes(), review_before)
        self.assertEqual(lifecycle.event_intents(fixture.engine, tx), [])
        self.assertFalse((fixture.engine.config.state_directory / "transaction-scope-history" / tx).exists())
        self.assertEqual(fixture.engine.submit_recycling(tx, analysis)["status"], "REVIEWED")
        fixture.engine.annotate_transaction(tx, kind="observation",
            summary="The terminal history write is admissible after review completion",
            evidence="docs/APP_BEHAVIOR.md#s-behavior")

    def test_compaction_waits_for_a_generic_pre_marker_intent(self):
        from runtime_sync_workshop import tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        fixture.engine.submit_recycling(tx, analysis)
        root, _ = fixture.engine._load_transaction(tx)
        marker = pending_path(fixture.engine, tx); write = lifecycle.atomic_write_json
        class Interrupted(BaseException):
            pass
        def before_marker(path, value):
            if Path(path) == marker:
                raise Interrupted()
            return write(path, value)
        with patch.object(lifecycle, "atomic_write_json", before_marker):
            with self.assertRaises(Interrupted):
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="A new compaction plan must wait for the older exact native note",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        with self.assertRaises(WorkshopError) as blocked:
            fixture.engine.compact_transaction(tx)
        self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertTrue((root / "work").is_dir())
        self.assertFalse((root / "compaction-plan.json").exists())
        fixture.engine.reconcile_transaction_document(tx)
        analysis, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis)
        fixture.engine.compact_transaction(tx)
        self.assertFalse((root / "work").exists())

    def test_public_history_action_cannot_reenter_an_ongoing_review(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        root, _ = fixture.engine._load_transaction(tx)
        before = (root / "state.json").read_bytes()
        persist = tx_recycling._persist_review_intent
        def during_review(root, prior, target, proposal_sha):
            self.assertFalse((root / "recycling-review-intent.json").exists())
            with self.assertRaises(WorkshopError) as blocked:
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="A separate public action cannot reenter a parent's native writer lock",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
            self.assertEqual(blocked.exception.code, "TX_RETENTION_BUSY")
            self.assertEqual((root / "state.json").read_bytes(), before)
            return persist(root, prior, target, proposal_sha)
        with patch.object(tx_recycling, "_persist_review_intent", side_effect=during_review):
            self.assertEqual(fixture.engine.submit_recycling(tx, analysis)["status"], "REVIEWED")

    def test_generic_intent_precedes_marker_and_blocks_checkout_after_restart(self):
        from runtime_sync_workshop import tx_lifecycle as lifecycle
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        native_write = lifecycle.atomic_write_json
        class Interrupted(BaseException):
            pass
        def before_marker(path, value):
            if Path(path) == pending_path(fixture.engine, tx):
                raise Interrupted()
            return native_write(path, value)
        with patch.object(lifecycle, "atomic_write_json", before_marker):
            with self.assertRaises(Interrupted):
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="Persist a discoverable native intent before the marker exists",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        intents = lifecycle.event_intents(fixture.engine, tx)
        self.assertEqual(len(intents), 1)
        intended = intents[0][1]["target_state"]
        restarted = WorkshopEngine(fixture.engine.config.config_path)
        with self.assertRaises(WorkshopError) as caught:
            restarted.checkout(["app.py"], purpose="Pending intent must block unrelated checkout")
        self.assertEqual(caught.exception.code, "TX_EVENT_INTENT_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["event_id"], intended["tx_event_id"])
        with patch.object(restarted, "apply", side_effect=AssertionError("WAL recovery must not Apply")):
            restarted.reconcile_transaction_document(tx)
        _, recovered = restarted._load_transaction(tx)
        self.assertEqual(recovered, intended)
        assert_projected(restarted, tx)
        self.assertEqual(lifecycle.event_intents(restarted, tx), [])
        restarted.abort(tx)

    def test_generic_safe_abort_retains_superseded_precommit_identity(self):
        from runtime_sync_workshop import engine as owner, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        class Interrupted(BaseException):
            pass
        with patch.object(owner._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                fixture.engine.annotate_transaction(tx, kind="hypothesis",
                    summary="This note's state commit will be overtaken by a safe native abort",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
        intended = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
        fixture.engine.abort(tx)
        _, actual = fixture.engine._load_transaction(tx)
        self.assertEqual(actual["state"], "ABORTED")
        self.assertEqual(actual.get("transaction_notes", []), [])
        resolution = next(item for item in actual["tx_event_resolutions"] if item["event"]["event_id"] == intended["event_id"])
        self.assertEqual(resolution["outcome"], "STATE_COMMIT_NOT_OBSERVED")
        self.assertGreater(actual["tx_event_sequence"], intended["event_sequence"])
        self.assertIn(intended["event_id"], (fixture.workspace / document_path(tx)).read_text(encoding="utf-8"))
        self.assertEqual(lifecycle.event_intents(fixture.engine, tx), [])

    def test_multiple_precommit_intents_recover_latest_and_resolve_earlier(self):
        from runtime_sync_workshop import engine as owner, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        class Interrupted(BaseException):
            pass
        observed = []
        for number in range(2):
            with patch.object(owner._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
                with self.assertRaises(Interrupted):
                    fixture.engine.annotate_transaction(tx, kind="observation",
                        summary=f"Consecutive pending state-commit attempt number {number}",
                        evidence="docs/APP_BEHAVIOR.md#s-behavior")
            observed.append(json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"])
        fixture.engine.reconcile_transaction_document(tx)
        _, recovered = fixture.engine._load_transaction(tx)
        self.assertEqual(recovered["tx_event_id"], observed[-1])
        self.assertIn(observed[0], [item["event"]["event_id"] for item in recovered["tx_event_resolutions"]])
        self.assertEqual(lifecycle.event_intents(fixture.engine, tx), [])
        text = (fixture.workspace / document_path(tx)).read_text(encoding="utf-8")
        self.assertTrue(all(event in text for event in observed))
        fixture.engine.abort(tx)

    def test_generic_intent_corruption_or_changed_boundary_remains_pending(self):
        from runtime_sync_workshop import engine as owner, tx_lifecycle as lifecycle
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        for mode in ("target", "lease", "seal"):
            with self.subTest(mode=mode):
                fixture = self.fixture()
                tx = fixture.checkout()["transaction_id"]
                class Interrupted(BaseException):
                    pass
                with patch.object(owner._CoreWorkshopEngine, "_save_transaction", side_effect=Interrupted()):
                    with self.assertRaises(Interrupted):
                        fixture.engine.annotate_transaction(tx, kind="observation",
                            summary="Conflicting native intent evidence must never be replayed",
                            evidence="docs/APP_BEHAVIOR.md#s-behavior")
                root, before = fixture.engine._load_transaction(tx)
                marker = pending_path(fixture.engine, tx)
                marker_raw = marker.read_bytes()
                path, intent, raw = lifecycle.event_intents(fixture.engine, tx)[0]
                changed = path if mode == "target" else getattr(fixture.engine, mode + "_path")
                original = changed.read_bytes()
                try:
                    if mode == "target":
                        intent["target_state"]["transaction_notes"][0]["summary"] = "Changed material input"
                        intent["target_state_sha256"] = lifecycle.digest(intent["target_state"])
                        changed.write_text(json.dumps(intent), encoding="utf-8")
                    else:
                        changed.write_bytes(original + b" ")
                    with self.assertRaises(WorkshopError) as caught:
                        fixture.engine.reconcile_transaction_document(tx)
                    self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                    self.assertEqual(fixture.engine._load_transaction(tx)[1], before)
                    self.assertEqual(marker.read_bytes(), marker_raw)
                finally:
                    changed.write_bytes(original)
                fixture.engine.reconcile_transaction_document(tx)
                fixture.engine.abort(tx)

    def test_generic_intent_survives_actual_process_exit_before_state_commit(self):
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.tx_lifecycle import event_intents
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        code = """
import os, sys
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [sys.argv[1], sys.argv[2], sys.argv[3]]
from runtime_sync_workshop import engine as owner
engine = owner.WorkshopEngine(Path(sys.argv[4]))
with patch.object(owner._CoreWorkshopEngine, '_save_transaction', side_effect=lambda *a, **k: os._exit(73)):
    engine.annotate_transaction(sys.argv[5], kind='observation',
        summary='A real process exit must preserve the exact native event obligation',
        evidence='docs/APP_BEHAVIOR.md#s-behavior')
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT), str(ROOT / "workshop/src"),
                                 str(ROOT / "kairos/kairos_harness"), str(fixture.engine.config.config_path), tx],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 73, result.stderr)
        intended_id = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"]
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("WAL repair must not Apply")):
            fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], intended_id)
        assert_projected(fixture.engine, tx)
        self.assertEqual(event_intents(fixture.engine, tx), [])
        fixture.engine.abort(tx)

    def test_shared_writer_lock_reenters_only_for_its_own_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        from runtime_sync_workshop.tx_retention import _retention_lock
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        def contender():
            try:
                with _retention_lock(fixture.engine):
                    return "UNEXPECTED_ENTRY"
            except WorkshopError as exc:
                return exc.code
        with _retention_lock(fixture.engine):
            with _retention_lock(fixture.engine):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(contender).result(timeout=10), "TX_RETENTION_BUSY")
        with _retention_lock(fixture.engine):
            pass

    def test_authoritative_save_inventory_and_state_phase_contract_do_not_drift(self):
        from runtime_sync_workshop import _engine_core_v1
        from runtime_sync_workshop.tx_lifecycle import PHASES
        package = Path(_engine_core_v1.__file__).resolve().parent
        # This is a snapshot of declared write sites, not an alternative
        # runtime permission list or a claim of compiler/source membership.
        # New modules are part of this CI inspection surface automatically.
        # This does not admit them into any project's governed source set.
        sources = sorted(package.rglob("*.py"))
        rows = []
        states = set()
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            relative = source.relative_to(package).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                                and target.value.id == "state" and isinstance(target.slice, ast.Constant)
                                and target.slice.value == "state" and isinstance(node.value.value, str)):
                            states.add(node.value.value)
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for call in ast.walk(node):
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr in {"_save_transaction", "_save_terminal_history"}):
                        continue
                    keyword = next((value for value in call.keywords if value.arg == "event"), None)
                    if keyword and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        event = keyword.value.value
                    else:
                        self.assertIn((relative, node.name), {("engine.py", "_save_transaction"), ("engine.py", "_save_terminal_history")},
                                      "Unreviewed dynamic lifecycle event writer")
                        event = "<forwarded>"
                    rows.append((relative, node.name, event))
                # Terminal history is written without a released Guardian lease.
                # Its raw state commits still require the common lifecycle stamp.
                for call in ast.walk(node):
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "atomic_write_json" and call.args
                            and isinstance(call.args[0], ast.BinOp)
                            and isinstance(call.args[0].right, ast.Constant)
                            and call.args[0].right.value == "state.json"):
                        continue
                    if relative == "_core_state.py" and node.name == "_save_transaction":
                        continue  # Base atomic writer is wrapped by the public engine owner.
                    if relative == "tx_recycling.py" and node.name == "_recover_review_intent":
                        # Exact replay must keep its already-created event, not stamp a new one.
                        # Pin the complete reviewed writer AST and its single target state write.
                        writes = [item for item in ast.walk(node)
                                  if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                                  and item.func.id == "atomic_write_json" and item.args
                                  and isinstance(item.args[0], ast.BinOp)
                                  and isinstance(item.args[0].right, ast.Constant)
                                  and item.args[0].right.value == "state.json"]
                        self.assertEqual(len(writes), 1, "Review replay must have exactly one state writer")
                        self.assertEqual(len(call.args), 2, "Review replay state write must have two positional arguments")
                        self.assertIsInstance(call.args[1], ast.Name)
                        self.assertEqual(call.args[1].id, "target")
                        replay_ast = _replay_ast_sha256(node)
                        self.assertEqual(replay_ast, "11c65ce6a3a0c7aca11393f59a80132fcd47d953ae2798e29b87d607bc2705c5",
                                         "Review replay writer changed; inspect all intent, event, marker and evidence guards")
                        rows.append((relative, node.name, "direct-state:TX_RECYCLE_REVIEWED_REPLAY"))
                        continue
                    if relative == "tx_lifecycle.py" and node.name == "recover_event_intents":
                        # Native WAL replay keeps the validated existing event. Pin
                        # endpoints, owner boundary, postcheck and Guardian checks.
                        self.assertEqual(len(call.args), 2)
                        self.assertIsInstance(call.args[1], ast.Name)
                        self.assertEqual(call.args[1].id, "target")
                        replay_ast = _replay_ast_sha256(node)
                        self.assertEqual(replay_ast, "1b76258af9dd70bf41fa58bf38a2fdf8b4a30765d98c0eed1ef5ea43afc14e0d",
                                         "Generic replay writer changed; inspect all endpoint and owner guards")
                        rows.append((relative, node.name, "direct-state:NATIVE_EVENT_INTENT_REPLAY"))
                        continue
                    staged = [item for item in ast.walk(node)
                              if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                              and item.func.id == "stage_lifecycle_event"]
                    self.assertEqual(len(staged), 1, f"Unreviewed direct TX state writer: {relative}:{node.name}")
                    event_arg = next((item.value for item in staged[0].keywords if item.arg == "event"), None)
                    self.assertIsInstance(event_arg, ast.Constant)
                    rows.append((relative, node.name, "direct-state:" + event_arg.value))
        self.assertFalse(states - set(PHASES), f"Unmapped persisted transition states: {sorted(states - set(PHASES))}")
        fingerprint = hashlib.sha256(json.dumps(sorted(rows), separators=(",", ":")).encode()).hexdigest()
        # Reviewed 102 sites: the previous 98 plus framework heartbeat and
        # exact-target forward/verified-heartbeat/preimage-rollback recovery.
        self.assertEqual(fingerprint, "5fe34f5a4a3868a9f7a733afc172ae975eb83853ea9cac58a16f9d430a1a4f0f",
                         f"Authoritative TX event writers changed; review each new event/type and update the inventory evidence: {rows}")

    def _assert_event_inventory_rejects_new_module(
        self, relative, content, message,
        inventory_name="test_authoritative_save_inventory_and_state_phase_contract_do_not_drift",
    ):
        import shutil
        import tempfile
        from runtime_sync_workshop import _engine_core_v1
        package = Path(_engine_core_v1.__file__).resolve().parent
        check = getattr(self, inventory_name)
        with tempfile.TemporaryDirectory(prefix="event-writer-inventory-") as temporary:
            copied = Path(temporary) / "runtime_sync_workshop"
            shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            # First prove that the unchanged fixture has the reviewed identity.
            with patch.object(_engine_core_v1, "__file__", str(copied / "_engine_core_v1.py")):
                check()
                new_module = copied / relative
                new_module.parent.mkdir(parents=True, exist_ok=True)
                new_module.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(AssertionError, message):
                    check()

    def test_inventory_rejects_mutated_generic_replay_boundary(self):
        from runtime_sync_workshop import tx_lifecycle
        source = Path(tx_lifecycle.__file__).read_text(encoding="utf-8")
        old = 'if endpoint(actual) != endpoint(intent["prior_state"]):'
        self.assertEqual(source.count(old), 1)
        self._assert_event_inventory_rejects_new_module(
            "tx_lifecycle.py", source.replace(old, 'if endpoint(actual) == endpoint(intent["prior_state"]):', 1),
            "Generic replay writer changed")

    def test_inventory_rejects_mutated_review_replay_writer(self):
        import shutil
        import tempfile
        from runtime_sync_workshop import _engine_core_v1
        package = Path(_engine_core_v1.__file__).resolve().parent
        mutations = (
            ('if actual_sha == prior_sha:', 'if actual_sha == target_sha:'),
            ('or event.get("required_projection") is not True):', 'or event.get("required_projection") is not False):'),
            ('type(prior.get("document_projection_required", False)) is not bool',
             'type(prior.get("document_projection_required", False)) is bool'),
            ('or target.get("document_projection_required") is not True):',
             'or target.get("document_projection_required") is not False):'),
        )
        for old, replacement in mutations:
            with self.subTest(old=old), tempfile.TemporaryDirectory(prefix="review-replay-inventory-") as temporary:
                copied = Path(temporary) / "runtime_sync_workshop"
                shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                with patch.object(_engine_core_v1, "__file__", str(copied / "_engine_core_v1.py")):
                    self.test_authoritative_save_inventory_and_state_phase_contract_do_not_drift()
                    source = copied / "tx_recycling.py"
                    text = source.read_text(encoding="utf-8")
                    self.assertEqual(text.count(old), 1)
                    source.write_text(text.replace(old, replacement, 1), encoding="utf-8")
                    with self.assertRaisesRegex(AssertionError, "Review replay writer changed"):
                        self.test_authoritative_save_inventory_and_state_phase_contract_do_not_drift()

    def test_inventory_rejects_a_new_top_level_or_nested_writer_module(self):
        for relative in ("unreviewed_writer.py", "new_package/unreviewed_writer.py"):
            with self.subTest(module=relative):
                self._assert_event_inventory_rejects_new_module(
                    relative,
                    "def save(engine, root, state):\n"
                    "    engine._save_transaction(root, state, event='NEW_UNREVIEWED_EVENT')\n",
                    "Authoritative TX event writers changed",
                )

    def test_inventory_rejects_dynamic_events_in_a_previously_unknown_module(self):
        self._assert_event_inventory_rejects_new_module(
            "unreviewed_dynamic.py",
            "def save(engine, root, state, event):\n"
            "    engine._save_transaction(root, state, event=event)\n",
            "Unreviewed dynamic lifecycle event writer",
        )

    def test_inventory_rejects_new_unmapped_state_in_a_previously_unknown_module(self):
        self._assert_event_inventory_rejects_new_module(
            "unreviewed_state.py",
            "def transition(state):\n"
            "    state['state'] = 'NEW_UNMAPPED_STATE'\n",
            "Unmapped persisted transition states",
        )

    def test_constructor_inventory_rejects_a_previously_unknown_tx_kind(self):
        self._assert_event_inventory_rejects_new_module(
            "new_package/unreviewed_constructor.py",
            "def create():\n"
            "    state = {'transaction_id': 'TXN_fixture', 'schema': 'new-tx/v1', "
            "'transaction_kind': 'new_unsupported_kind'}\n",
            "Supported TX constructors changed",
            inventory_name="test_supported_transaction_constructors_do_not_drift",
        )

    def test_constructor_inventory_rejects_dynamic_schema_in_a_new_module(self):
        self._assert_event_inventory_rejects_new_module(
            "unreviewed_constructor.py",
            "def create(schema):\n"
            "    state = {'transaction_id': 'TXN_fixture', 'schema': schema}\n",
            "New/dynamic TX constructor requires explicit schema review",
            inventory_name="test_supported_transaction_constructors_do_not_drift",
        )

    def test_same_event_reconcile_is_read_only_and_keeps_revision(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        path = fixture.workspace / document_path(tx)
        before = path.read_bytes()
        before_stat = path.stat().st_mtime_ns
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        checkpoint_before = checkpoint.read_bytes()
        with patch("kairos.transaction_document.utc_now", return_value="2099-01-01T00:00:00Z"):
            result = fixture.engine.reconcile_transaction_document(tx)
        self.assertTrue(result["replayed"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_stat)
        self.assertEqual(checkpoint.read_bytes(), checkpoint_before)

    def test_missing_projection_checkpoint_recovers_without_new_revision(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        path = fixture.workspace / document_path(tx)
        before = path.read_bytes()
        checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
        checkpoint.unlink()  # Fixture killpoint: SQL committed before its checkpoint.
        with patch("kairos.transaction_document.utc_now", return_value="2099-01-01T00:00:00Z"):
            result = fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(checkpoint.is_file())
        restored = json.loads(checkpoint.read_text(encoding="utf-8"))
        state = json.loads((fixture.engine.config.transaction_directory / tx / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(restored["event_id"], state["tx_event_id"])

    def test_terminal_note_has_new_monotone_lifecycle_identity(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        state_path = fixture.engine.config.transaction_directory / tx / "state.json"
        before = json.loads(state_path.read_text(encoding="utf-8"))
        fixture.engine.annotate_transaction(
            tx, kind="observation", summary="The isolated observation has an explicit evidence boundary",
            evidence="docs/APP_BEHAVIOR.md#s-behavior",
        )
        after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(after["tx_event_sequence"], before["tx_event_sequence"] + 1)
        self.assertNotEqual(after["tx_event_id"], before["tx_event_id"])
        self.assertEqual(after["tx_event"]["previous_event_id"], before["tx_event_id"])
        header = split_frontmatter((fixture.workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
        self.assertEqual(header["tx_event_id"], after["tx_event_id"])

    def test_checkout_and_abort_are_both_projected(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        path = fixture.workspace / document_path(tx)
        self.assertTrue(path.is_file())
        header = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(header["type"], "transaction")
        self.assertEqual(header["workshop_state"], "CHECKED_OUT")
        self.assertEqual(header["revision"], 1)
        with closing(sqlite3.connect(fixture.database)) as con:
            row = con.execute("SELECT revision,document_type FROM artifacts WHERE artifact_id=?",
                              (document_id(tx),)).fetchone()
            self.assertEqual(row, (1, "transaction"))
        result = fixture.engine.abort(tx)
        self.assertEqual(result["state"], "ABORTED")
        revised = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["workshop_state"], "ABORTED")
        with closing(sqlite3.connect(fixture.database)) as con:
            row = con.execute("SELECT revision,state FROM artifacts WHERE artifact_id=?",
                              (document_id(tx),)).fetchone()
            self.assertEqual(row, (2, "failed"))

    def test_declared_finding_is_projected_without_fake_validation(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        note = fixture.engine.annotate_transaction(
            tx, kind="failure",
            summary="Parser experiment still requires further direct evidence",
            evidence="docs/APP_BEHAVIOR.md#s-behavior",
        )
        self.assertTrue(note["documented"])
        path = fixture.workspace / document_path(tx)
        fm = split_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm.metadata["revision"], 3)
        self.assertIn("DECLARED_NOT_INDEPENDENTLY_VERIFIED", fm.body)

    @staticmethod
    def completed_analysis(fixture, tx, *, disposition="REJECT_FOR_REUSE"):
        result = fixture.engine.inspect_recycling(tx)
        path = Path(result["proposal_path"])
        proposal = json.loads(path.read_text(encoding="utf-8"))
        proposal["planned_approach"] = "Correct the parser branch that mishandles the declared application value"
        proposal["attempted_approach"] = "Changed app.py in the isolated Workshop worktree and compared it to the baseline"
        proposal["outcome_reason"] = "The attempted change was not validated for deployment and cannot be claimed as a fix"
        proposal["no_changes_reason"] = "The checkout produced no changed bytes; only an unrealized plan existed"
        for row in proposal["decisions"]:
            row["disposition"] = disposition
            row["reason"] = (
                "The candidate file changed but no verified test proves this approach is reusable; "
                "preserve the audit evidence and stop pursuing the current unverified patch."
            )
            row["evidence_refs"] = ["TX:state.json"]
        path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        return path, result

    def test_rejected_code_and_reasons_are_in_markdown_and_sql(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        analysis_path, inventory = self.completed_analysis(fixture, tx)
        self.assertGreaterEqual(inventory["changed_unit_count"], 1)
        _, prior = fixture.engine._load_transaction(tx)
        review = fixture.engine.submit_recycling(tx, analysis_path)
        _, reviewed = fixture.engine._load_transaction(tx)
        self.assertEqual(reviewed["tx_event_sequence"], prior["tx_event_sequence"] + 1)
        self.assertEqual(reviewed["tx_event"]["event"], "TX_RECYCLE_REVIEWED")
        self.assertEqual(review["status"], "REVIEWED")
        path = fixture.workspace / document_path(tx)
        body = split_frontmatter(path.read_text(encoding="utf-8")).body
        self.assertIn("app.py", body)
        self.assertIn("Correct the parser branch", body)
        self.assertIn("stop pursuing the current unverified patch", body)
        metadata = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["tx_event_id"], reviewed["tx_event_id"])
        with closing(sqlite3.connect(fixture.database)) as con:
            atoms = con.execute(
                "SELECT path,disposition,reason,evidence_json FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchall()
            row = con.execute(
                "SELECT analysis_sha256,decision_count FROM tx_recycling_reviews WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchone()
        self.assertEqual(row[0], review["analysis_sha256"])
        self.assertEqual(row[1], len(atoms))
        self.assertTrue(any(row[0] == "app.py" and row[1] == "REJECT_FOR_REUSE" for row in atoms))
        for row in atoms:
            evidence = json.loads(row[3])
            self.assertTrue(evidence)
            self.assertEqual(evidence[0]["verification_kind"], "EXISTENCE_AND_HASH_ONLY")
            self.assertEqual(len(evidence[0]["content_sha256"]), 64)

    def prepare_review_outbox_fixture(self, *, mandatory):
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        assert_projected(fixture.engine, tx)
        fixture.engine.config.raw["transaction_documents_required"] = mandatory
        fixture.engine.config.raw["tx_retention_required"] = False
        analysis, _ = self.completed_analysis(fixture, tx, disposition="REJECT_FOR_REUSE")
        return fixture, tx, analysis

    def test_reconcile_refuses_precommit_marker_until_same_review_event_is_saved(self):
        from runtime_sync_workshop import cli, tx_recycling
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        root, prior = fixture.engine._load_transaction(tx)
        marker = pending_path(fixture.engine, tx)
        original = tx_recycling.atomic_write_json
        def fail_state(path, payload, *args, **kwargs):
            if Path(path) == root / "state.json" and (payload.get("recycling") or {}).get("status") == "REVIEWED":
                raise Interrupted("before state")
            return original(path, payload, *args, **kwargs)
        with patch.object(tx_recycling, "atomic_write_json", side_effect=fail_state):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        marker_bytes = marker.read_bytes()
        pending = json.loads(marker_bytes)
        self.assertNotEqual(pending["event_id"], prior["tx_event_id"])
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(caught.exception.references["items"][0]["event_id"], pending["event_id"])
        self.assertEqual(marker.read_bytes(), marker_bytes)
        self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], prior["tx_event_id"])
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = cli.main(["--config", str(fixture.engine.config.config_path), "tx-document-reconcile", tx])
        self.assertEqual(code, 2)
        public = json.loads(stderr.getvalue())["error"]
        self.assertEqual(public["outcome"], "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(public["references"]["items"][0]["event_id"], pending["event_id"])
        self.assertEqual(marker.read_bytes(), marker_bytes)
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
            fixture.engine.submit_recycling(tx, analysis)
        final = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(final["tx_event_id"], pending["event_id"])
        self.assertFalse(marker.exists())

    def test_reconcile_refuses_conflicting_pending_identity_fields(self):
        from runtime_sync_workshop import tx_documents
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        class Interrupted(BaseException): pass
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        with patch.object(tx_documents, "update_document", side_effect=Interrupted("after state")):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        marker = tx_documents.pending_path(fixture.engine, tx)
        original = json.loads(marker.read_text(encoding="utf-8"))
        for key in ("schema", "transaction_id", "event", "state", "candidate_sha256",
                    "baseline_sha256", "scope_receipt_sha256"):
            with self.subTest(field=key):
                changed = dict(original)
                changed[key] = "different-" + str(original[key])
                atomic_write_json(marker, changed)
                raw = marker.read_bytes()
                with patch.object(tx_documents, "publish", side_effect=AssertionError("conflicting identity must not be published")):
                    with self.assertRaises(WorkshopError) as caught:
                        fixture.engine.reconcile_transaction_document(tx)
                self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                self.assertEqual(marker.read_bytes(), raw)
        # Diagnostic fields may legitimately differ after a failed publication.
        diagnostic = dict(original, reason="publication failed with extra diagnostic context",
                          code="INJECTED_FAILURE", message="isolated diagnostic")
        atomic_write_json(marker, diagnostic)
        fixture.engine.reconcile_transaction_document(tx)
        fixture.engine.submit_recycling(tx, analysis)
        self.assertFalse(marker.exists())
        self.assert_special_projected(fixture, tx, "ABORTED")

    def test_reconcile_does_not_remove_a_marker_changed_during_publication(self):
        from runtime_sync_workshop import tx_documents
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        class Interrupted(BaseException): pass
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=False)
        with patch.object(tx_documents, "update_document", side_effect=Interrupted("after state")):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        marker = tx_documents.pending_path(fixture.engine, tx)
        original_marker = marker.read_bytes()
        original_publish = tx_documents.publish
        changed = {}
        def publish_then_replace(engine, state):
            result = original_publish(engine, state)
            pending = json.loads(marker.read_text(encoding="utf-8"))
            pending["event_id"] = "TXE_" + "E" * 32
            pending["event_sequence"] += 1
            atomic_write_json(marker, pending)
            changed["bytes"] = marker.read_bytes()
            return result
        with patch.object(tx_documents, "publish", side_effect=publish_then_replace):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.reconcile_transaction_document(tx)
        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
        self.assertEqual(marker.read_bytes(), changed["bytes"])
        # Restore only this isolated fixture's known marker to finish its own cleanup.
        marker.write_bytes(original_marker)
        fixture.engine.reconcile_transaction_document(tx)
        fixture.engine.submit_recycling(tx, analysis)
        self.assertFalse(marker.exists())

    def test_checkout_and_review_use_the_same_retention_lock(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        for mandatory in (False, True):
            with self.subTest(mandatory=mandatory):
                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=mandatory)
                original = tx_recycling._persist_review_intent
                observed = {}
                def concurrent_checkout(root, prior, target, proposal_sha):
                    original(root, prior, target, proposal_sha)
                    self.assertFalse(pending_path(fixture.engine, tx).exists())
                    with self.assertRaises(WorkshopError) as caught:
                        fixture.engine.checkout(["app.py"], purpose="Isolated checkout during a retained review intent")
                    observed["code"] = caught.exception.code
                    self.assertFalse(fixture.engine.lease_path.exists())
                with patch.object(tx_recycling, "_persist_review_intent", side_effect=concurrent_checkout):
                    result = fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(result["status"], "REVIEWED")
                self.assertEqual(observed["code"], "TX_RETENTION_BUSY")
                self.assert_special_projected(fixture, tx, "ABORTED")

                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=mandatory)
                active = fixture.engine.checkout(["app.py"], purpose="Isolated source lease before the recycling review")
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(caught.exception.code, "TX_RECYCLING_LEASE_ACTIVE")
                self.assertNotEqual((fixture.engine._load_transaction(tx)[1].get("recycling") or {}).get("status"), "REVIEWED")
                fixture.engine.abort(active["transaction_id"])

    def test_retention_lock_blocks_another_process_and_releases_after_kill(self):
        import os
        import subprocess
        import time
        from runtime_sync_workshop.tx_retention import _retention_lock
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
        config = str(fixture.engine.config.config_path)
        command = [sys.executable, str(ROOT / "workshop" / "workshop.py"), "--config", config,
                   "checkout", "--source", "app.py", "--purpose", "Isolated concurrent checkout must respect the retention lock"]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        with _retention_lock(fixture.engine):
            blocked = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertEqual(json.loads(blocked.stderr)["error"]["code"], "TX_RETENTION_BUSY")
        self.assertFalse(fixture.engine.lease_path.exists())

        ready = fixture.root / "retention-lock-ready"
        script = ("import sys\nfrom pathlib import Path\n"
                  "from runtime_sync_workshop.engine import WorkshopEngine\n"
                  "from runtime_sync_workshop.tx_retention import _retention_lock\n"
                  "engine=WorkshopEngine(Path(sys.argv[1]))\n"
                  "with _retention_lock(engine):\n"
                  " Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
                  " sys.stdin.read()\n")
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "workshop" / "src"), str(ROOT / "kairos" / "kairos_harness"), str(ROOT)])
        process = subprocess.Popen([sys.executable, "-c", script, config, str(ready)], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.is_file(), "lock-holder subprocess did not become ready")
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.checkout(["app.py"], purpose="Isolated checkout while another process holds retention lock")
            self.assertEqual(caught.exception.code, "TX_RETENTION_BUSY")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
            if process.stdin:
                process.stdin.close()
            if process.stderr:
                process.stderr.close()
        checkout = fixture.engine.checkout(["app.py"], purpose="Isolated checkout after killed lock holder")
        fixture.engine.abort(checkout["transaction_id"])

    def test_drain_and_compact_preserve_unresolved_review_intent(self):
        from runtime_sync_workshop import tx_documents, tx_recycling
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        for boundary in ("before_state", "after_state"):
            with self.subTest(boundary=boundary):
                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=True)
                root, prior = fixture.engine._load_transaction(tx)
                original = tx_recycling.atomic_write_json
                def interrupt_state(path, payload, *args, **kwargs):
                    if Path(path) == root / "state.json" and (payload.get("recycling") or {}).get("status") == "REVIEWED":
                        raise Interrupted(boundary)
                    return original(path, payload, *args, **kwargs)
                target = tx_recycling if boundary == "before_state" else tx_documents
                attribute = "atomic_write_json" if boundary == "before_state" else "update_document"
                side_effect = interrupt_state if boundary == "before_state" else Interrupted(boundary)
                fixture.engine.config.raw["tx_retention_required"] = True
                with patch.object(target, attribute, side_effect=side_effect):
                    with self.assertRaises(Interrupted):
                        fixture.engine.submit_recycling(tx, analysis)
                marker = tx_documents.pending_path(fixture.engine, tx)
                original_marker = marker.read_bytes()
                original_state = (root / "state.json").read_bytes()
                self.assertTrue((root / "recycling-review-intent.json").is_file())
                self.assertTrue((root / "work").is_dir())
                with self.assertRaises(WorkshopError) as direct:
                    fixture.engine.compact_transaction(tx)
                self.assertEqual(direct.exception.code, "TX_RECYCLING_INTENT_PENDING")
                pool = {"required": True, "materialized_count": 9, "target_max": 8,
                        "rows": [{"transaction_id": tx}]}
                with patch.object(tx_recycling, "pool_status", return_value=pool):
                    batch = fixture.engine.drain_recycling(max_items=1)
                self.assertEqual([item["code"] for item in batch["blocked"]], ["TX_RECYCLING_INTENT_PENDING"])
                self.assertEqual(marker.read_bytes(), original_marker)
                self.assertEqual((root / "state.json").read_bytes(), original_state)
                self.assertTrue((root / "work").is_dir())
                fixture.engine.submit_recycling(tx, analysis)
                self.assertFalse(marker.exists())
                self.assertFalse((root / "recycling-review-intent.json").exists())
                self.assert_special_projected(fixture, tx, "ABORTED")
                compacted = fixture.engine.compact_transaction(tx)
                self.assertFalse((root / "work").exists())
                self.assertTrue(compacted.get("archive", {}).get("complete"))

    def test_drain_repairs_missing_historical_document_without_an_intent(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture = self.fixture()
        fixture.engine.config.raw["transaction_documents_required"] = False
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        document = fixture.workspace / document_path(tx)
        self.assertFalse(document.exists())
        fixture.engine.config.raw["transaction_documents_required"] = True
        fixture.engine.config.raw["tx_retention_required"] = True
        pool = {"required": True, "materialized_count": 9, "target_max": 8,
                "rows": [{"transaction_id": tx}]}
        with patch.object(tx_recycling, "pool_status", return_value=pool):
            batch = fixture.engine.drain_recycling(max_items=1)
        self.assertEqual(batch["blocked"][0]["code"], "TX_RECYCLING_REVIEW_REQUIRED")
        self.assertTrue(document.is_file())
        assert_projected(fixture.engine, tx)

    def test_drain_preserves_review_intent_at_a_real_nine_tx_threshold(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.tx_documents import pending_path
        class Interrupted(BaseException): pass
        fixture = self.fixture()
        fixture.engine.config.raw["tx_retention_required"] = False
        for _ in range(9):
            tx = fixture.checkout()["transaction_id"]
            fixture.engine.abort(tx)
        fixture.engine.config.raw["tx_retention_required"] = True
        pool = tx_recycling.pool_status(fixture.engine)
        self.assertTrue(pool["required"])
        self.assertEqual(pool["materialized_count"], 9)
        tx = pool["rows"][0]["transaction_id"]
        analysis, _ = self.completed_analysis(fixture, tx, disposition="REJECT_FOR_REUSE")
        root, _ = fixture.engine._load_transaction(tx)
        original = tx_recycling.atomic_write_json
        def interrupt_state(path, payload, *args, **kwargs):
            if Path(path) == root / "state.json" and (payload.get("recycling") or {}).get("status") == "REVIEWED":
                raise Interrupted("before state")
            return original(path, payload, *args, **kwargs)
        with patch.object(tx_recycling, "atomic_write_json", side_effect=interrupt_state):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        marker = pending_path(fixture.engine, tx)
        original_marker = marker.read_bytes()
        batch = fixture.engine.drain_recycling(max_items=1)
        self.assertEqual(batch["blocked"][0]["transaction_id"], tx)
        self.assertEqual(batch["blocked"][0]["code"], "TX_RECYCLING_INTENT_PENDING")
        self.assertEqual(marker.read_bytes(), original_marker)
        self.assertTrue((root / "work").is_dir())
        fixture.engine.submit_recycling(tx, analysis)
        self.assertFalse(marker.exists())

    def test_review_outbox_after_state_interruption_replays_same_event(self):
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        for mandatory in (False, True):
            with self.subTest(mandatory=mandatory):
                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=mandatory)
                with patch("runtime_sync_workshop.tx_documents.update_document", side_effect=Interrupted("after state")):
                    with self.assertRaises(Interrupted):
                        fixture.engine.submit_recycling(tx, analysis)
                root, state = fixture.engine._load_transaction(tx)
                marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
                self.assertTrue(state["tx_event"]["required_projection"])
                self.assertEqual(marker["event_id"], state["tx_event_id"])
                self.assertTrue((root / "recycling-review-intent.json").is_file())
                with self.assertRaises(WorkshopError) as blocked:
                    fixture.engine.checkout(["app.py"], purpose="Refuse new work until reviewed transaction projection is repaired")
                self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
                    retry = fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(retry["status"], "ALREADY_REVIEWED")
                final = self.assert_special_projected(fixture, tx, "ABORTED")
                self.assertEqual(final["tx_event_id"], marker["event_id"])
                self.assertEqual(final["tx_event_sequence"], marker["event_sequence"])
                self.assertFalse(pending_path(fixture.engine, tx).exists())
                self.assertFalse((root / "recycling-review-intent.json").exists())
                again = fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(again["status"], "ALREADY_REVIEWED")
                self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], marker["event_id"])

    def test_review_outbox_before_state_interruption_replays_same_event(self):
        from runtime_sync_workshop import tx_recycling as module
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        for mandatory in (False, True):
            with self.subTest(mandatory=mandatory):
                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=mandatory)
                root, prior = fixture.engine._load_transaction(tx)
                original = module.atomic_write_json
                def fail_state(path, payload, *args, **kwargs):
                    if Path(path) == root / "state.json" and (payload.get("recycling") or {}).get("status") == "REVIEWED":
                        raise Interrupted("before state")
                    return original(path, payload, *args, **kwargs)
                with patch("runtime_sync_workshop.tx_lifecycle.utc_now", return_value="2026-10-27T01:00:00Z"):
                    with patch.object(module, "atomic_write_json", side_effect=fail_state):
                        with self.assertRaises(Interrupted):
                            fixture.engine.submit_recycling(tx, analysis)
                marker = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
                self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], prior["tx_event_id"])
                with self.assertRaises(WorkshopError) as blocked:
                    fixture.engine.checkout(["app.py"], purpose="Refuse source checkout while a precommit review event is pending")
                self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                with patch("runtime_sync_workshop.tx_lifecycle.utc_now", return_value="2026-10-27T01:00:01Z"):
                    with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
                        result = fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(result["status"], "REVIEWED")
                final = self.assert_special_projected(fixture, tx, "ABORTED")
                self.assertEqual(final["tx_event_id"], marker["event_id"])
                self.assertEqual(final["tx_event_sequence"], marker["event_sequence"])
                self.assertFalse(pending_path(fixture.engine, tx).exists())
                self.assertFalse((root / "recycling-review-intent.json").exists())

    def test_checkout_blocks_review_intent_without_a_pending_marker(self):
        from runtime_sync_workshop import tx_lifecycle, tx_recycling
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        class Interrupted(BaseException): pass
        for mandatory in (False, True):
            for boundary in ("before_outbox", "after_projection"):
                with self.subTest(mandatory=mandatory, boundary=boundary):
                    fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=mandatory)
                    root, prior = fixture.engine._load_transaction(tx)
                    marker = pending_path(fixture.engine, tx)
                    original = tx_lifecycle.atomic_write_json
                    def fail_marker(path, payload, *args, **kwargs):
                        if Path(path) == marker:
                            raise Interrupted(boundary)
                        return original(path, payload, *args, **kwargs)
                    module = tx_lifecycle if boundary == "before_outbox" else tx_recycling
                    name = "atomic_write_json" if boundary == "before_outbox" else "_remove_review_intent"
                    failure = fail_marker if boundary == "before_outbox" else Interrupted(boundary)
                    with patch.object(module, name, side_effect=failure):
                        with self.assertRaises(Interrupted):
                            fixture.engine.submit_recycling(tx, analysis)
                    intent = root / "recycling-review-intent.json"
                    intended = json.loads(intent.read_text(encoding="utf-8"))["target_state"]["tx_event_id"]
                    self.assertFalse(marker.exists())
                    with self.assertRaises(WorkshopError) as blocked:
                        fixture.engine.checkout(["app.py"], purpose="Refuse checkout while a review intent awaits native recovery")
                    self.assertEqual(blocked.exception.code, "TX_RECYCLING_INTENT_PENDING")
                    self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                    self.assertEqual(blocked.exception.references["items"][0]["event_id"], intended)
                    self.assertEqual(blocked.exception.references["items"][0]["status"], "OBSERVED")
                    self.assertFalse(blocked.exception.references["total_exact"])
                    self.assertFalse(blocked.exception.references["omitted_exact"])
                    self.assertFalse(fixture.engine.lease_path.exists())
                    fixture.engine.submit_recycling(tx, analysis)
                    self.assertFalse(intent.exists())
                    self.assertEqual(self.assert_special_projected(fixture, tx, "ABORTED")["tx_event_id"], intended)
                    checkout = fixture.engine.checkout(["app.py"], purpose="Checkout after review intent has been projected and cleared")
                    fixture.engine.abort(checkout["transaction_id"])

    def test_checkout_fails_closed_for_unreadable_review_intent_or_pool(self):
        from runtime_sync_workshop import tx_recycling
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=False)
        root, _ = fixture.engine._load_transaction(tx)
        intent = root / "recycling-review-intent.json"
        intent.write_text("not valid json", encoding="utf-8")
        with self.assertRaises(WorkshopError) as blocked:
            fixture.engine.checkout(["app.py"], purpose="Refuse checkout for an unreadable isolated review intent")
        self.assertEqual(blocked.exception.code, "TX_RECYCLING_INTENT_PENDING")
        self.assertEqual(blocked.exception.references["items"][0]["status"], "UNVERIFIED")
        self.assertFalse(fixture.engine.lease_path.exists())
        intent.unlink()  # Remove only the malformed intent created by this isolated fixture.
        original_iterdir = Path.iterdir
        def deny_pool(path):
            if path == fixture.engine.config.transaction_directory:
                raise PermissionError("isolated pool read failure")
            return original_iterdir(path)
        with patch.object(Path, "iterdir", deny_pool):
            with self.assertRaises(WorkshopError) as inaccessible:
                fixture.engine.checkout(["app.py"], purpose="Refuse checkout when the review intent pool cannot be read")
        self.assertEqual(inaccessible.exception.code, "TX_RECYCLING_INTENT_SCAN_FAILED")
        self.assertFalse(fixture.engine.lease_path.exists())
        original_reject = tx_recycling.reject_link_components
        def reject_intent(path, *, label):
            if Path(path) == intent:
                raise WorkshopError("TX_REPARSE_TEST", "isolated linked intent refused")
            return original_reject(path, label=label)
        with patch.object(tx_recycling, "reject_link_components", side_effect=reject_intent):
            with self.assertRaises(WorkshopError) as linked:
                fixture.engine.checkout(["app.py"], purpose="Refuse checkout if the review intent path is linked")
        self.assertEqual(linked.exception.code, "TX_REPARSE_TEST")
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_review_intent_recovers_before_outbox_and_after_projection(self):
        from runtime_sync_workshop import tx_lifecycle, tx_recycling
        from runtime_sync_workshop.tx_documents import pending_path
        class Interrupted(BaseException): pass

        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=False)
        root, prior = fixture.engine._load_transaction(tx)
        marker = pending_path(fixture.engine, tx)
        original = tx_lifecycle.atomic_write_json
        def fail_outbox(path, payload, *args, **kwargs):
            if Path(path) == marker:
                raise Interrupted("before outbox")
            return original(path, payload, *args, **kwargs)
        with patch.object(tx_lifecycle, "atomic_write_json", side_effect=fail_outbox):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        intent_path = root / "recycling-review-intent.json"
        intended = json.loads(intent_path.read_text(encoding="utf-8"))["target_state"]["tx_event_id"]
        self.assertFalse(marker.exists())
        self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], prior["tx_event_id"])
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
            fixture.engine.submit_recycling(tx, analysis)
        self.assertEqual(self.assert_special_projected(fixture, tx, "ABORTED")["tx_event_id"], intended)
        self.assertFalse(intent_path.exists())

        fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=False)
        root, _ = fixture.engine._load_transaction(tx)
        intent_path = root / "recycling-review-intent.json"
        with patch.object(tx_recycling, "_remove_review_intent", side_effect=Interrupted("after projection")):
            with self.assertRaises(Interrupted):
                fixture.engine.submit_recycling(tx, analysis)
        state = self.assert_special_projected(fixture, tx, "ABORTED")
        document = fixture.workspace / document_path(tx)
        before = document.read_bytes()
        self.assertTrue(intent_path.is_file())
        self.assertFalse(pending_path(fixture.engine, tx).exists())
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("recovery must not Apply")):
            result = fixture.engine.submit_recycling(tx, analysis)
        self.assertEqual(result["status"], "ALREADY_REVIEWED")
        self.assertEqual(document.read_bytes(), before)
        self.assertEqual(fixture.engine._load_transaction(tx)[1]["tx_event_id"], state["tx_event_id"])
        self.assertFalse(intent_path.exists())

    def test_review_intent_refuses_conflicting_proof_without_overwrite(self):
        from runtime_sync_workshop import tx_recycling as module
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.tx_retention import _object_path, vault_dir
        from runtime_sync_workshop.util import WorkshopError, atomic_write_json
        class Interrupted(BaseException): pass
        expected = {
            "proposal": "TX_RECYCLING_INTENT_INVALID",
            "intent": "TX_RECYCLING_INTENT_INVALID",
            "marker": "TX_RECYCLING_INTENT_CONFLICT",
            "state": "TX_RECYCLING_INTENT_CONFLICT",
            "inventory": "TX_RECYCLING_INTENT_INVALID",
            "vault": "TX_RECYCLING_INTENT_INVALID",
        }
        for case, code in expected.items():
            with self.subTest(case=case):
                fixture, tx, analysis = self.prepare_review_outbox_fixture(mandatory=False)
                root, _ = fixture.engine._load_transaction(tx)
                original = module.atomic_write_json
                def fail_state(path, payload, *args, **kwargs):
                    if Path(path) == root / "state.json" and (payload.get("recycling") or {}).get("status") == "REVIEWED":
                        raise Interrupted("before state")
                    return original(path, payload, *args, **kwargs)
                with patch.object(module, "atomic_write_json", side_effect=fail_state):
                    with self.assertRaises(Interrupted):
                        fixture.engine.submit_recycling(tx, analysis)
                intent_path = root / "recycling-review-intent.json"
                marker = pending_path(fixture.engine, tx)
                intent = json.loads(intent_path.read_text(encoding="utf-8"))
                if case == "proposal":
                    proposal = json.loads(analysis.read_text(encoding="utf-8"))
                    proposal["outcome_reason"] += " Altered proposal."
                    analysis = analysis.with_name("conflicting-analysis.json")
                    analysis.write_text(json.dumps(proposal), encoding="utf-8")
                elif case == "intent":
                    intent["target_state"]["updated_at"] = "conflicting"
                    atomic_write_json(intent_path, intent)
                elif case == "marker":
                    pending = json.loads(marker.read_text(encoding="utf-8"))
                    pending["event_id"] = "TXE_" + "F" * 32
                    atomic_write_json(marker, pending)
                elif case == "state":
                    state_path = root / "state.json"
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["unrelated_test_value"] = "different"
                    atomic_write_json(state_path, state)
                elif case == "inventory":
                    (root / "work" / "app.py").write_text("different = 99\\n", encoding="utf-8")
                else:
                    review = intent["target_state"]["recycling"]
                    manifest = json.loads(Path(review["vault_manifest"]).read_text(encoding="utf-8"))
                    digest = next(iter(manifest["files"].values()))["sha256"]
                    _object_path(vault_dir(fixture.engine), digest).unlink()
                before_state = (root / "state.json").read_bytes()
                before_marker = marker.read_bytes()
                before_intent = intent_path.read_bytes()
                with self.assertRaises(WorkshopError) as caught:
                    fixture.engine.submit_recycling(tx, analysis)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual((root / "state.json").read_bytes(), before_state)
                self.assertEqual(marker.read_bytes(), before_marker)
                self.assertEqual(intent_path.read_bytes(), before_intent)

    def test_applied_verified_candidate_requires_exact_receipt_for_evidence_retention(self):
        import copy
        from runtime_sync_workshop import tx_recycling as module
        from runtime_sync_workshop.util import WorkshopError

        fixture = self.fixture()
        checkout = fixture.checkout()
        fixture.stage_behavior(checkout)
        fixture.prepare_and_verify(checkout)
        tx = checkout["transaction_id"]
        self.assertEqual(fixture.engine.apply(tx)["state"], "POSTCHECK_VERIFIED")
        root, state = fixture.engine._load_transaction(tx)
        self.assertTrue(module._ordinary_postcheck_proven(fixture.engine, state))
        path, inventory = self.completed_analysis(fixture, tx, disposition="RETAIN_EVIDENCE")
        self.assertGreater(inventory["changed_unit_count"], 0)
        proposal = json.loads(path.read_text(encoding="utf-8"))

        bad_states = []
        for key, value in (("state", "ABORTED"), ("schema", "other"),
                           ("verified_work_package_sha256", "0" * 64),
                           ("postcheck_package_sha256", "0" * 64)):
            altered = copy.deepcopy(state)
            altered[key] = value
            bad_states.append((key, altered))
        for key, value in (("status", "PENDING"), ("kind", "other"),
                           ("payload_sha256", "0" * 64), ("postcheck_event_id", "invalid"),
                           ("path", "receipts/other.json")):
            altered = copy.deepcopy(state)
            altered["terminal_receipt"][key] = value
            bad_states.append((key, altered))
        altered = copy.deepcopy(state)
        altered["terminal_receipt"]["payload"]["bit_exact"] = False
        bad_states.append(("bit_exact", altered))
        altered = copy.deepcopy(state)
        altered["terminal_receipt"]["payload"]["heartbeat"]["verified"] = False
        bad_states.append(("heartbeat", altered))
        for label, altered in bad_states:
            with self.subTest(label=label):
                self.assertFalse(module._ordinary_postcheck_proven(fixture.engine, altered))

        unknown = copy.deepcopy(inventory)
        unknown["units"][0]["category"] = "baseline_unknown"
        rejected = copy.deepcopy(proposal)
        for row in rejected["decisions"]:
            row["disposition"] = "REJECT_FOR_REUSE"
        with self.assertRaises(WorkshopError) as caught:
            module._verify_analysis(fixture.engine, root, state, unknown, rejected)
        self.assertEqual(caught.exception.code, "TX_RECYCLING_UNPROVEN_BASELINE")

        receipt_path = fixture.engine.config.state_directory / state["terminal_receipt"]["path"]
        original = receipt_path.read_bytes()
        try:
            receipt_path.write_bytes(b"{}\\n")
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.submit_recycling(tx, path)
            self.assertEqual(caught.exception.code, "TX_RECYCLING_PROTECTED_CANDIDATE")
        finally:
            receipt_path.write_bytes(original)
        review = fixture.engine.submit_recycling(tx, path)
        self.assertEqual(review["status"], "REVIEWED")
        archive = json.loads(Path(review["vault_manifest"]).read_text(encoding="utf-8"))
        self.assertTrue(archive["complete"])
        self.assertEqual(archive["files"]["work/app.py"]["sha256"],
                         hashlib.sha256((root / "work" / "app.py").read_bytes()).hexdigest())
        with closing(sqlite3.connect(fixture.database)) as con:
            rows = con.execute(
                "SELECT disposition FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchall()
        self.assertEqual(len(rows), inventory["changed_unit_count"])
        self.assertTrue(all(row[0] == "RETAIN_EVIDENCE" for row in rows))

    def test_verified_unshipped_patch_cannot_be_classified_as_disposable(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        checkout = fixture.checkout()
        fixture.stage_behavior(checkout)
        tx = checkout["transaction_id"]
        fixture.prepare_and_verify(checkout)
        fixture.engine.abort(tx)
        path, inv = self.completed_analysis(fixture, tx)
        proposal = json.loads(path.read_text(encoding="utf-8"))
        if not proposal["decisions"]:
            self.skipTest("fixture had no changed bytes to classify")
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.submit_recycling(tx, path)
        self.assertEqual(caught.exception.code, "TX_RECYCLING_PROTECTED_CANDIDATE")
        for row in proposal["decisions"]:
            row["disposition"] = "RETAIN_FOR_REUSE"
            row["reason"] = "Previously shadow-verified code must remain available for reuse until actual deployment is established"
        path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        receipt = fixture.engine.submit_recycling(tx, path)
        self.assertEqual(receipt["status"], "REVIEWED")

    def test_fresh_source_status_finds_bundled_workshop_without_pythonpath(self):
        fixture = self.fixture()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "kairos_cli.py"), "status", "--workspace", str(fixture.workspace)],
            cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        self.assertTrue(result.stdout, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["tx_recycling_live"]["available"], data["tx_recycling_live"])
        self.assertTrue(data["tx_recycling_live"]["reconciliation_required"])
        self.assertEqual(data["tx_recycling_live"]["policy_state"], "INACTIVE")
        self.assertEqual(data["tx_recycling_live"]["next_action"], "TX_POLICY_ACTIVATION_REQUIRED")
        self.assertTrue(data["tx_recycling_live"]["policy"]["transaction_documents_required"]["value"])
        self.assertFalse(data["tx_recycling_live"]["policy"]["tx_retention_required"]["value"])
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_fresh_workshop_compact_imports_configured_harness_without_pythonpath(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        analysis, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "workshop" / "workshop.py"), "--config",
             str(fixture.engine.config.config_path), "tx-compact", tx],
            cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["archive"]["complete"])
        state = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(state["tx_event"]["event"], "TX_COMPACTED")
        self.assertTrue(state["tx_event"]["required_projection"])

    def test_repeated_source_status_does_not_duplicate_the_owner_path(self):
        fixture = self.fixture()
        code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from kairos.recycling_bridge import live_recycling_status; "
                "a=live_recycling_status(Path(sys.argv[2])); b=live_recycling_status(Path(sys.argv[2])); "
                "print(json.dumps({'first':a,'second':b,'count':sys.path.count(sys.argv[3])}))")
        owner = (ROOT / "workshop" / "src").resolve()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT / "kairos" / "kairos_harness"),
             str(fixture.workspace), str(owner)],
            cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["first"]["available"] and data["second"]["available"], data)
        self.assertEqual(data["count"], 1)

    def test_source_status_never_imports_a_workspace_named_workshop(self):
        fixture = self.fixture()
        fake = fixture.project / "runtime_sync_workshop"
        fake.mkdir()
        marker = fixture.project / "WRONG_IMPORT"
        (fake / "__init__.py").write_text(
            "from pathlib import Path\nPath('WRONG_IMPORT').write_text('yes')\n", encoding="utf-8")
        code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from kairos.recycling_bridge import live_recycling_status; "
                "print(json.dumps(live_recycling_status(Path(sys.argv[2]))))")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT / "kairos" / "kairos_harness"), str(fixture.workspace)],
            cwd=fixture.project, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "workspace module must never be imported as the Workshop owner")
        status = json.loads(result.stdout)
        self.assertTrue(status["available"], status)
        self.assertTrue(status["reconciliation_required"])
        self.assertEqual(status["policy_state"], "INACTIVE")
        self.assertEqual(status["next_action"], "TX_POLICY_ACTIVATION_REQUIRED")
        self.assertFalse(status["policy"]["tx_retention_required"]["value"])

    def test_malformed_workspace_config_fails_closed_with_bundled_owner(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="malformed-workshop-binding-") as temporary:
            workspace = Path(temporary)
            (workspace / ".kairos").mkdir()
            (workspace / ".kairos" / "workshop.config.json").write_text("{invalid", encoding="utf-8")
            code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                    "from kairos.recycling_bridge import live_recycling_status; "
                    "print(json.dumps(live_recycling_status(Path(sys.argv[2]))))")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, str(ROOT / "kairos" / "kairos_harness"), str(workspace)],
                cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            status = json.loads(result.stdout)
            self.assertTrue(status["configured"])
            self.assertFalse(status["available"])
            self.assertTrue(status["reconciliation_required"])

    def test_conflicting_preloaded_workshop_owner_fails_closed(self):
        fixture = self.fixture()
        code = ("import sys,json,types; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "fake=types.ModuleType('runtime_sync_workshop'); fake.__file__=sys.argv[3]; "
                "sys.modules['runtime_sync_workshop']=fake; "
                "from kairos.recycling_bridge import live_recycling_status; "
                "print(json.dumps(live_recycling_status(Path(sys.argv[2]))))")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(ROOT / "kairos" / "kairos_harness"),
             str(fixture.workspace), str(fixture.project / "foreign_owner.py")],
            cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = json.loads(result.stdout)
        self.assertFalse(status["available"])
        self.assertTrue(status["reconciliation_required"])
        self.assertIn("conflicting already-loaded Workshop", status["reason"])

    def test_installed_owner_resolution_ignores_project_package(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory(prefix="installed-workshop-owner-") as temporary:
            root = Path(temporary)
            harness = root / "harness"
            shutil.copytree(ROOT / "kairos" / "kairos_harness" / "kairos", harness / "kairos")
            installed = root / "site"
            package = installed / "runtime_sync_workshop"
            # A real installed-owner contract needs the complete native package
            # and a valid reviewed fixture config, not a fabricated writer stub.
            shutil.copytree(ROOT / "workshop" / "src" / "runtime_sync_workshop", package)
            from kickstart import universal_workshop as initial_binding
            native_write = initial_binding._atomic_json
            def installed_binding(path, value):
                # This is an initial synthetic fixture input, before any seal.
                # Preserve the real native writer and subsequent intake checks.
                if Path(path).name == "workshop.config.json":
                    value["kairos_harness"] = "@installed"
                return native_write(path, value)
            with patch.object(initial_binding, "_atomic_json", side_effect=installed_binding):
                fixture = self.fixture()
            workspace = fixture.workspace
            fake = workspace / "runtime_sync_workshop"
            fake.mkdir()
            (fake / "__init__.py").write_text("from pathlib import Path\nPath('WRONG_IMPORT').write_text('yes')\n", encoding="utf-8")
            code = ("import sys,json,site,sysconfig; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                    "sysconfig.get_path=lambda *a,**k: sys.argv[3]; site.getsitepackages=lambda: [sys.argv[3]]; "
                    "site.ENABLE_USER_SITE=False; from kairos.recycling_bridge import live_recycling_status; "
                    "x=live_recycling_status(Path(sys.argv[2])); "
                    "print(json.dumps({'status':x,'owner':sys.modules['runtime_sync_workshop'].__file__}))")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run([sys.executable, "-B", "-c", code, str(harness), str(workspace), str(installed)],
                                    cwd=workspace, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data["status"]["available"], data)
            self.assertEqual(data["status"]["policy_state"], "INACTIVE")
            self.assertEqual(data["status"]["next_action"], "TX_POLICY_ACTIVATION_REQUIRED")
            self.assertEqual(Path(data["owner"]).resolve(), (package / "__init__.py").resolve())
            self.assertFalse((workspace / "WRONG_IMPORT").exists())

    def test_missing_workshop_owner_does_not_import_from_workspace(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory(prefix="missing-workshop-owner-") as temporary:
            root = Path(temporary)
            harness = root / "harness"
            shutil.copytree(ROOT / "kairos" / "kairos_harness" / "kairos", harness / "kairos")
            workspace = root / "workspace"
            (workspace / ".kairos").mkdir(parents=True)
            (workspace / ".kairos" / "workshop.config.json").write_text("{}", encoding="utf-8")
            fake = workspace / "runtime_sync_workshop"
            fake.mkdir()
            (fake / "__init__.py").write_text("from pathlib import Path\nPath('WRONG_IMPORT').write_text('yes')\n", encoding="utf-8")
            code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                    "from kairos.recycling_bridge import live_recycling_status; "
                    "print(json.dumps(live_recycling_status(Path(sys.argv[2]))))")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(harness), str(workspace)],
                                    cwd=workspace, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            status = json.loads(result.stdout)
            self.assertFalse(status["available"])
            self.assertTrue(status["reconciliation_required"])
            self.assertFalse((workspace / "WRONG_IMPORT").exists())

    def test_terminal_compaction_updates_persistent_report(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        tx = checkout["transaction_id"]
        fixture.engine.abort(tx)
        analysis_path, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis_path)
        fixture.engine.config.raw["tx_retention_required"] = True
        _, prior = fixture.engine._load_transaction(tx)
        result = fixture.engine.compact_transaction(tx)
        _, compacted = fixture.engine._load_transaction(tx)
        self.assertEqual(compacted["tx_event_sequence"], prior["tx_event_sequence"] + 1)
        self.assertEqual(compacted["tx_event"]["event"], "TX_COMPACTED")
        self.assertGreater(result["reclaimed_worktree_bytes"], 0)
        self.assertFalse((fixture.engine.config.transaction_directory / tx / "work").exists())
        path = fixture.workspace / document_path(tx)
        fm = split_frontmatter(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(fm.metadata["revision"], 4)
        self.assertIn("manifest_sha256", fm.body)
        self.assertEqual(fm.metadata["tx_event_id"], compacted["tx_event_id"])
        stable = path.read_bytes()
        fixture.engine.compact_transaction(tx)
        _, repeated = fixture.engine._load_transaction(tx)
        self.assertEqual(repeated["tx_event_id"], compacted["tx_event_id"])
        self.assertEqual(path.read_bytes(), stable)
        with closing(sqlite3.connect(fixture.database)) as con:
            count = con.execute(
                "SELECT count(*) FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchone()[0]
        self.assertGreater(count, 0)

    def _legacy_reviewed_compaction(self):
        fixture = self.fixture()
        tx = fixture.checkout()["transaction_id"]
        fixture.engine.abort(tx)
        analysis, _ = self.completed_analysis(fixture, tx)
        fixture.engine.submit_recycling(tx, analysis)
        fixture.engine.config.raw.update({
            "transaction_documents_required": False,
            "tx_retention_required": False,
        })
        return fixture, tx

    def test_reviewed_legacy_compaction_requires_exact_event_projection(self):
        fixture, tx = self._legacy_reviewed_compaction()
        fixture.engine.compact_transaction(tx)
        state = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(state["tx_event"]["event"], "TX_COMPACTED")
        self.assertTrue(state["tx_event"]["required_projection"])
        root = fixture.engine._transaction_path(tx)
        self.assertTrue((root / "compaction-committed.json").is_file())
        self.assertFalse((root / "work").exists())

    def test_reviewed_legacy_compaction_publication_failure_is_recoverable(self):
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.tx_retention import materialized_worktrees
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self._legacy_reviewed_compaction()
        root = fixture.engine._transaction_path(tx)
        with patch("runtime_sync_workshop.tx_documents.publish", side_effect=RuntimeError("isolated projection outage")):
            with self.assertRaises(WorkshopError):
                fixture.engine.compact_transaction(tx)
        _, interrupted = fixture.engine._load_transaction(tx)
        self.assertEqual(interrupted["tx_event"]["event"], "TX_COMPACTED")
        self.assertTrue(interrupted["tx_event"]["required_projection"])
        self.assertEqual(json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))["event_id"], interrupted["tx_event_id"])
        self.assertFalse((root / "compaction-committed.json").exists())
        self.assertTrue((root / ".reclaim" / "work").is_dir())
        self.assertIn(root, materialized_worktrees(fixture.engine))
        with patch.object(fixture.engine, "apply", side_effect=AssertionError("compaction recovery must not apply")):
            fixture.engine.compact_transaction(tx)
        recovered = self.assert_special_projected(fixture, tx, "ABORTED")
        self.assertEqual(recovered["tx_event_id"], interrupted["tx_event_id"])
        self.assertEqual(recovered["tx_event_sequence"], interrupted["tx_event_sequence"])
        self.assertFalse((root / ".reclaim").exists())
        self.assertTrue((root / "compaction-committed.json").is_file())
        self.assertFalse(pending_path(fixture.engine, tx).exists())

    def test_committed_compaction_replay_refuses_stale_projection(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture, tx = self._legacy_reviewed_compaction()
        fixture.engine.compact_transaction(tx)
        with patch("runtime_sync_workshop.tx_retention._assert_document_projected",
                   side_effect=WorkshopError("TX_DOCUMENT_EVENT_STALE", "isolated stale projection")):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.compact_transaction(tx)
        self.assertEqual(caught.exception.code, "TX_DOCUMENT_EVENT_STALE")

    def test_ninth_checkout_announces_recycling_before_tenth_is_requested(self):
        fixture = self.fixture()
        fixture.engine.config.raw.update({
            "transaction_documents_required": True,
            "tx_retention_required": True,
            "tx_max_materialized": 10,
            "tx_recycle_trigger": 9,
        })
        for i in range(8):
            txid = "TXN_" + format(i, "x") * 24
            root = fixture.engine.config.transaction_directory / txid
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": txid, "state": "ABORTED",
                            "purpose": "Historical parser investigation awaiting a recycling analysis"}),
                encoding="utf-8",
            )
        checkout = fixture.checkout()
        self.assertTrue(checkout["tx_recycling"]["required"])
        self.assertEqual(checkout["tx_recycling"]["materialized"], 9)
        self.assertEqual(checkout["tx_recycling"]["target_max"], 8)
        self.assertTrue(checkout["tx_recycling"]["loop_heartbeat"]["verified"])
        current = json.loads((fixture.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")

    def test_recycling_frontier_routes_the_agent_but_engine_owns_gate(self):
        from runtime_sync_workshop.util import WorkshopError
        fixture = self.fixture()
        fixture.engine.config.raw.update({
            "transaction_documents_required": True,
            "tx_retention_required": True,
            "tx_max_materialized": 10,
            "tx_recycle_trigger": 9,
        })
        from pathlib import Path
        for i in range(9):
            tx = "TXN_" + format(i, "x") * 24
            root = fixture.engine.config.transaction_directory / tx
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": tx, "state": "ABORTED",
                            "purpose": "Legacy attempt requiring recycling review"}),
                encoding="utf-8",
            )
        status = fixture.engine.transaction_pool_status(refresh_loop=True)
        self.assertTrue(status["required"])
        self.assertTrue(status["loop_heartbeat"]["verified"], status["loop_heartbeat"])
        current = json.loads((fixture.workspace / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")
        gate = (fixture.workspace / "_LOOP_GATE.md").read_text(encoding="utf-8")
        self.assertIn("s-tx-recycling", gate)
        self.assertIn("Workshop checkout is blocked", gate)
        with self.assertRaises(WorkshopError) as caught:
            fixture.engine.checkout(
                ["app.py"], purpose="Try another independent source change without recycling",
            )
        self.assertEqual(caught.exception.code, "TX_RECYCLING_REQUIRED")

    def test_kairos_status_separates_live_pool_from_stale_cached_frontier(self):
        from kairos.heartbeat import status_report
        fixture = self.fixture()
        canonical = fixture.workspace / ".kairos" / "workshop.config.json"
        raw = json.loads(canonical.read_text(encoding="utf-8"))
        raw["tx_retention_required"] = True
        raw["transaction_documents_required"] = True
        raw["tx_recycle_trigger"] = 9
        canonical.write_text(json.dumps(raw, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        fixture.engine.config.raw.update(raw)
        for i in range(9):
            tx = "TXN_" + format(i, "024x")
            root = fixture.engine.config.transaction_directory / tx
            (root / "work").mkdir(parents=True)
            (root / "state.json").write_text(
                json.dumps({"transaction_id": tx, "state": "ABORTED",
                            "purpose": "Older code change requiring an explicit recycling analysis"}),
                encoding="utf-8",
            )
        # No Workshop status refresh has occurred yet; KAIROS must not trust
        # the old current.json as an accurate new-transaction admission.
        stale = status_report(fixture.workspace)
        self.assertTrue(stale["tx_recycling_live"]["required"])
        self.assertTrue(stale["tx_recycling_live"]["reconciliation_required"])
        self.assertEqual(stale["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")
        fixture.engine.transaction_pool_status(refresh_loop=True)
        fresh = status_report(fixture.workspace)
        self.assertTrue(fresh["tx_recycling_live"]["required"])
        self.assertFalse(fresh["tx_recycling_live"]["reconciliation_required"])

    def test_apply_projects_terminal_outcome_after_code_heartbeat(self):
        fixture = self.fixture()
        checkout = fixture.checkout()
        fixture.stage_behavior(checkout)
        fixture.prepare_and_verify(checkout)
        try:
            receipt = fixture.engine.apply(checkout["transaction_id"])
        except Exception as exc:
            marker = fixture.engine.config.state_directory / "transaction-document-pending" / (checkout["transaction_id"] + ".json")
            details = marker.read_text(encoding="utf-8") if marker.is_file() else "no pending marker"
            self.fail(f"apply/document integration failed: {exc}; pending={details}")
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        path = fixture.workspace / document_path(checkout["transaction_id"])
        header = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(header["workshop_state"], "POSTCHECK_VERIFIED")
        self.assertEqual(header["state"], "completed")
        with closing(sqlite3.connect(fixture.database)) as con:
            row = con.execute("SELECT state FROM artifacts WHERE artifact_id=?",
                              (document_id(checkout["transaction_id"]),)).fetchone()
            self.assertEqual(row, ("completed",))


    def special_abort_fixture(self, *, authority=False):
        """Disposable crash model; never repairs or modifies the real workspace."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from unittest.mock import patch
        from kairos.cli import main
        from workshop.tests import test_context_transactions as contexts
        from workshop.tests.metadata_test_support import complete_metadata_audit

        initialize = contexts.initialize_universal_markdown

        def two_sources(**arguments):
            (arguments["project_root"] / "other.py").write_bytes(b"other = 1\n")
            return initialize(**arguments)

        with patch.object(contexts, "initialize_universal_markdown", side_effect=two_sources):
            fixture = self.fixture()
        first = fixture.checkout()
        txid = first["transaction_id"]
        historical_seal = fixture.engine.seal_path.read_bytes()
        # Native fixture-only lease transitions simulate an abandoned checkout.
        fixture.engine._release_lease(txid)
        changed = fixture.engine.checkout(
            ["other.py"], purpose="Advance only unrelated source through regular verified native Apply"
        )
        (Path(changed["work_directory"]) / "other.py").write_bytes(b"other = 2\n")
        complete_metadata_audit(fixture.engine, changed)
        fixture.engine.prepare(changed["transaction_id"])
        fixture.engine.metadata_check(changed["transaction_id"])
        fixture.engine.verify(changed["transaction_id"])
        applied = fixture.engine.apply(changed["transaction_id"])
        kwargs = {"expected_current_package_sha256": applied["postcheck_package_sha256"]}
        if not authority:
            fixture.engine._acquire_lease(txid)
            return fixture, first, kwargs

        # Explicit disposable fault injection models a persisted historical seal
        # after interruption. No production seal or validator is replaced.
        fixture.engine.seal_path.write_bytes(historical_seal)
        workspace = fixture.engine.config.kairos_workspace
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        common = ["--workspace", str(workspace), "--task", current["active_task"],
                  "--goal", current["active_goal"], "--milestone", current["active_milestone"],
                  "--criteria", current["active_criterion"]]

        def native(arguments):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                code = main(arguments)
            self.assertEqual(code, 0, output.getvalue())

        native(["new-decision", *common, "--id", "DECISION_ISOLATED_AUTHORITY",
                "--title", "Disposable bounded recovery authority",
                "--question", "May this abandoned fixture checkout close with preserved work?",
                "--context", "Disposable two-source fixture with historical seal fault injection.",
                "--options", "Keep abandoned checkout; close only with exact authority.",
                "--selected", "Close only this fixture checkout and preserve its work.",
                "--rationale", "Exercise actual native authority records without mocked validators.",
                "--risks", "Only this test fixture simulates an interrupted historical seal.",
                "--validation", "Check exact identity and projection without repeating Apply."])
        for identifier, state in [("REPORT_ISOLATED_EXCEPTION", "partial"),
                                  ("REPORT_ISOLATED_BLOCKER", "blocked")]:
            native(["new-report", *common, "--id", identifier,
                    "--title", "Disposable bounded authority evidence",
                    "--outcome", "Test-only exception/blocker evidence; no real operator approval is inferred.",
                    "--work", "Preserve fixture work and verify the exact native terminal event.",
                    "--evidence", "Canonical two-source fixture and actual unrelated native Apply.",
                    "--limitations", "No production authorization or release acceptance.",
                    "--next-action", "Exercise native recovery and exact projection.",
                    "--state", state])

        for key, identifier, directory in [
                ("decision", "DECISION_ISOLATED_AUTHORITY", "decisions"),
                ("exception_report", "REPORT_ISOLATED_EXCEPTION", "reports"),
                ("blocker_report", "REPORT_ISOLATED_BLOCKER", "reports")]:
            paths = [p for p in (workspace / directory).glob("*.md")
                     if p.stem.casefold() == identifier.casefold()]
            self.assertEqual(len(paths), 1)
            kwargs[key + "_id"] = identifier
            kwargs[key + "_sha256"] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
        kwargs["expected_sealed_package_sha256"] = json.loads(historical_seal)["package_sha256"]
        return fixture, first, kwargs

    def assert_special_abort_endpoint(self, *, authority):
        from unittest.mock import patch
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.util import WorkshopError
        from runtime_sync_workshop.tx_documents import pending_path

        for outage in (False, True):
            with self.subTest(authority=authority, outage=outage):
                fixture, checkout, kwargs = self.special_abort_fixture(authority=authority)
                engine = fixture.engine
                txid = checkout["transaction_id"]
                root, before = engine._load_transaction(txid)
                state_bytes = (root / "state.json").read_bytes()
                seal_bytes = engine.seal_path.read_bytes()
                lease_bytes = engine.lease_path.read_bytes() if engine.lease_path.exists() else None
                method = engine.authority_recover if authority else engine.stale_abort
                bad = dict(kwargs)
                bad["decision_sha256" if authority else "expected_current_package_sha256"] = "0" * 64
                with self.assertRaises(WorkshopError) as refused:
                    method(txid, **bad)
                self.assertEqual(refused.exception.code,
                                 "AUTHORITY_RECORD_HASH_MISMATCH" if authority
                                 else "STALE_ABORT_PACKAGE_MISMATCH")
                self.assertEqual((root / "state.json").read_bytes(), state_bytes)
                self.assertEqual(engine.seal_path.read_bytes(), seal_bytes)
                self.assertEqual(engine.lease_path.read_bytes() if engine.lease_path.exists() else None,
                                 lease_bytes)
                if outage:
                    with patch("runtime_sync_workshop.tx_documents.publish",
                               side_effect=RuntimeError("isolated special Abort publication outage")):
                        with self.assertRaises(WorkshopError) as pending:
                            method(txid, **kwargs)
                    self.assertEqual(pending.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                    self.assertEqual(pending.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                    _, saved = engine._load_transaction(txid)
                    self.assertTrue(pending_path(engine, txid).exists())
                    references = pending.exception.references
                    self.assertEqual(references["total"], 1)
                    self.assertEqual(references["items"][0]["event_id"], saved["tx_event_id"])
                    event = dict(saved["tx_event"])
                    with patch.object(WorkshopEngine, "apply",
                                      side_effect=AssertionError("projection repair repeated Apply")):
                        fresh = WorkshopEngine(engine.config.config_path)
                        fresh.reconcile_transaction_document(txid)
                        projected = self.assert_special_projected(fixture, txid, "ABORTED_STALE_BASELINE")
                        first_repair = (root / "state.json").read_bytes()
                        fresh.reconcile_transaction_document(txid)
                        self.assertEqual((root / "state.json").read_bytes(), first_repair)
                    self.assertEqual(projected["tx_event"], event)
                    self.assertFalse(pending_path(engine, txid).exists())
                else:
                    output = method(txid, **kwargs)
                    self.assertEqual(output["state"], "ABORTED_STALE_BASELINE")
                    projected = self.assert_special_projected(fixture, txid, "ABORTED_STALE_BASELINE")
                self.assertEqual(projected["tx_event"]["previous_event_id"], before["tx_event_id"])
                self.assertEqual(projected["tx_event_sequence"], before["tx_event_sequence"] + 1)
                self.assertFalse(engine.lease_path.exists())
                self.assertEqual(engine.seal_path.read_bytes(), seal_bytes)
                self.assertEqual((fixture.project / "app.py").read_bytes(), b"value = 1\n")
                self.assertEqual((fixture.project / "other.py").read_bytes(), b"other = 2\n")
                self.assertEqual((Path(checkout["work_directory"]) / "app.py").read_bytes(), b"value = 2\n")

    def test_native_stale_abort_requires_projection_or_typed_pending(self):
        self.assert_special_abort_endpoint(authority=False)

    def test_native_authority_recovery_requires_projection_or_typed_pending(self):
        self.assert_special_abort_endpoint(authority=True)


    def test_all_six_native_kinds_retire_orphans_with_exact_projected_or_pending_identity(self):
        from types import SimpleNamespace
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.corpus import build_corpus_manifest
        from runtime_sync_workshop.tx_documents import pending_path
        from runtime_sync_workshop.util import WorkshopError
        from workshop.tests.test_authority_migration import AuthorityMigrationTests
        from runtime_sync_workshop.migration import AuthorityMigration

        observed = []

        def prove(fixture, tx, kind, outage):
            engine = fixture.engine
            root, before = engine._load_transaction(tx)
            self.assert_special_projected(fixture, tx, before["state"])
            state_bytes = (root / "state.json").read_bytes()
            with self.assertRaises(WorkshopError) as leased:
                engine.retire_orphan_transaction(
                    tx, reason="Explicit disposable orphan coverage must not bypass an active native lease")
            self.assertEqual(leased.exception.code, "TX_RECYCLING_ORPHAN_LEASE")
            self.assertEqual((root / "state.json").read_bytes(), state_bytes)
            manifest = build_corpus_manifest(engine.config)
            if kind == "bootstrap":
                # This real repair fixture intentionally starts with native-indexed
                # legacy ledger noise. Retirement must preserve that exact problem,
                # not claim an already-valid corpus or silently repair/adopt it.
                self.assertFalse(manifest["verified"], manifest)
                self.assertEqual({row["code"] for row in manifest["issues"]},
                                 {"BLUEPRINT_LEDGER_LINE_INVALID", "BLUEPRINT_MEMBERSHIP_MISMATCH"})
            else:
                self.assertTrue(manifest["verified"], manifest)
            seal = engine.seal_path.read_bytes()
            work = root / "work"
            work_hashes = {str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in work.rglob("*") if p.is_file()}
            self.assertTrue(work_hashes)
            # Only a disposable fixture is abandoned, through its native lease owner.
            engine._release_lease(tx)
            reason = "Explicit isolated six-kind pre-Apply orphan retirement; preserve exact candidate and archive evidence"
            if outage:
                with patch("runtime_sync_workshop.tx_documents.publish",
                           side_effect=RuntimeError("isolated six-kind orphan publication outage")):
                    with self.assertRaises(WorkshopError) as pending:
                        engine.retire_orphan_transaction(tx, reason=reason)
                self.assertEqual(pending.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                self.assertEqual(pending.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                _, saved = engine._load_transaction(tx)
                event = dict(saved["tx_event"])
                self.assertTrue(pending_path(engine, tx).exists())
                self.assertEqual(pending.exception.references["items"][0]["event_id"], event["event_id"])
                pending_state = (root / "state.json").read_bytes()
                existing_ids = sorted(p.name for p in engine.config.transaction_directory.glob("TXN_*"))
                with self.assertRaises(WorkshopError) as blocked:
                    engine.checkout([manifest["authority"]["translation_units"][0]],
                                    purpose="Refuse new work while this exact orphan event still awaits projection")
                if kind == "bootstrap":
                    # The native core refuses this deliberately invalid repair
                    # baseline before reaching pending-document admission.
                    self.assertEqual(blocked.exception.code, "CORPUS_NOT_VERIFIED")
                else:
                    self.assertEqual(blocked.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                    self.assertEqual(blocked.exception.references["items"][0]["event_id"], event["event_id"])
                self.assertEqual((root / "state.json").read_bytes(), pending_state)
                self.assertEqual(sorted(p.name for p in engine.config.transaction_directory.glob("TXN_*")),
                                 existing_ids)
                self.assertEqual(json.loads(pending_path(engine, tx).read_text(encoding="utf-8"))["event_id"],
                                 event["event_id"])
                with patch.object(WorkshopEngine, "apply",
                                  side_effect=AssertionError("orphan projection repair repeated Apply")), \
                     patch.object(WorkshopEngine, "retire_orphan_transaction",
                                  side_effect=AssertionError("orphan projection repair repeated retirement")):
                    fresh = WorkshopEngine(engine.config.config_path)
                    fresh.reconcile_transaction_document(tx)
                    projected = self.assert_special_projected(fixture, tx, "ABORTED_ORPHANED_CHECKOUT")
                    committed_state = (root / "state.json").read_bytes()
                    fresh.reconcile_transaction_document(tx)
                    self.assertEqual((root / "state.json").read_bytes(), committed_state)
                self.assertEqual(projected["tx_event"], event)
            else:
                result = engine.retire_orphan_transaction(tx, reason=reason)
                self.assertEqual(result["state"], "ABORTED_ORPHANED_CHECKOUT")
                projected = self.assert_special_projected(fixture, tx, "ABORTED_ORPHANED_CHECKOUT")
            self.assertEqual(projected["schema"], before["schema"])
            self.assertEqual(projected.get("transaction_kind"), before.get("transaction_kind"))
            self.assertEqual(projected["tx_event_sequence"], before["tx_event_sequence"] + 1)
            self.assertEqual(projected["tx_event"]["previous_event_id"], before["tx_event_id"])
            self.assertEqual(projected["tx_event"]["phase"], "ABORTED")
            self.assertEqual(projected["tx_event"]["event"], "TX_RECYCLING_ORPHAN_RETIRED")
            self.assertFalse(engine.lease_path.exists())
            self.assertFalse(pending_path(engine, tx).exists())
            self.assertEqual(engine.seal_path.read_bytes(), seal)
            current = build_corpus_manifest(engine.config)
            self.assertEqual(current["verified"], manifest["verified"])
            self.assertEqual(current["issues"], manifest["issues"])
            self.assertEqual(current["package_sha256"], manifest["package_sha256"])
            self.assertEqual({str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in work.rglob("*") if p.is_file()}, work_hashes)
            archive_hash = projected["orphan_retirement"]["vault_manifest_sha256"]
            archive = engine.config.state_directory / "tx-vault" / "manifests" / (tx + "-" + archive_hash + ".json")
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), archive_hash)
            self.assertTrue(json.loads(archive.read_text(encoding="utf-8"))["complete"])
            observed.append((kind, outage))

        for kind in ("normal", "auxiliary", "source_set", "bootstrap", "authority", "policy"):
            for outage in (False, True):
                with self.subTest(kind=kind, outage=outage):
                    if kind == "normal":
                        fixture, tx = self.prepared_normal_receipt()
                    elif kind == "auxiliary":
                        fixture, _, tx, _, _ = self.prepared_auxiliary()
                    elif kind in ("source_set", "bootstrap"):
                        fixture, _, tx = self.prepared_special_receipt(kind)
                    elif kind == "policy":
                        fixture = self.policy_event_fixture()
                        checkout, _ = fixture._verified_policy()
                        tx = checkout["transaction_id"]
                    else:
                        case = AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
                        self.addCleanup(case.doCleanups)
                        original_apply = AuthorityMigration.apply

                        class OrphanProved(Exception):
                            pass

                        def stop_before_apply(owner, tx):
                            root, state = owner.engine._load_transaction(tx)
                            if owner._work_package(root, state)["package_sha256"] != state.get("verified_work_package_sha256"):
                                return original_apply(owner, tx)
                            fixture = SimpleNamespace(engine=owner.engine,
                                workspace=owner.engine.config.kairos_workspace,
                                database=owner.engine.config.kairos_database)
                            prove(fixture, tx, kind, outage)
                            raise OrphanProved()

                        with patch.object(AuthorityMigration, "apply", new=stop_before_apply):
                            with self.assertRaises(OrphanProved):
                                case.test_header_add_remove_and_source_change_migrate_atomically()
                        continue
                    prove(fixture, tx, kind, outage)
        self.assertEqual(observed, [(kind, outage)
            for kind in ("normal", "auxiliary", "source_set", "bootstrap", "authority", "policy")
            for outage in (False, True)])


    @staticmethod
    def _history_method_v3_fixture():
        # Frozen historical DATA, not a second executable renderer or current policy.
        return (
            "+++\n"
            "schema = \"kairos-context/v1\"\n"
            "id = \"WERKFADEN_TRANSACTION_HISTORY_METHOD\"\n"
            "type = \"documentation\"\n"
            "revision = 3\n"
            "state = \"active\"\n"
            "authority = \"operating_contract\"\n"
            "workspace = \"KAIROS_CONTEXT_TEST\"\n"
            "route = \"KAIROS_CONTEXT_TEST/WERKFADEN_TRANSACTION_HISTORY_METHOD\"\n"
            "updated_at = \"2026-10-28T00:00:00Z\"\n"
            "capsule = \"Canonical explicit historical intake, versioned pre-material-epoch anchor admission, evidence-bound scope assignment and exact projection recovery.\"\n"
            "claim_boundary = \"Framework method only; no transaction provenance, project success, historical execution, provider attestation or deployment is certified by this document.\"\n"
            "entities = [\"WERKFADEN_TRANSACTION_HISTORY_METHOD\", \"tx-scope-inspect\", \"tx-history-intake\", \"tx-scope-bind\", \"werkfaden-tx-legacy-event-admission/v1\"]\n"
            "facets = [\"transaction-history\", \"legacy-intake\", \"scope-provenance\", \"legacy-event-format\", \"unattested-material-boundary\"]\n"
            "criteria = []\n"
            "does_not_answer = [\"the true scope of an unobserved transaction\", \"missing historical facts\"]\n"
            "\n"
            "[[answers]]\n"
            "intent = \"workflow\"\n"
            "question = \"How is a legacy transaction explicitly admitted and later scope bound?\"\n"
            "target = \"s-workflow\"\n"
            "\n"
            "[[answers]]\n"
            "intent = \"evidence\"\n"
            "question = \"Which historical scope evidence can authorize assignment?\"\n"
            "target = \"s-evidence\"\n"
            "\n"
            "[[answers]]\n"
            "intent = \"recovery\"\n"
            "question = \"How is interrupted historical scope projection recovered?\"\n"
            "target = \"s-recovery\"\n"
            "\n"
            "[[answers]]\n"
            "intent = \"workflow\"\n"
            "question = \"How are pre-material-epoch transaction event anchors migrated?\"\n"
            "target = \"s-workflow\"\n"
            "\n"
            "[[search_contract]]\n"
            "query = \"How is a legacy transaction explicitly admitted and later scope bound?\"\n"
            "expected = \"WERKFADEN_TRANSACTION_HISTORY_METHOD#s-workflow\"\n"
            "required_top_k = 5\n"
            "\n"
            "[[search_contract]]\n"
            "query = \"How are pre-material-epoch transaction event anchors migrated?\"\n"
            "expected = \"WERKFADEN_TRANSACTION_HISTORY_METHOD#s-workflow\"\n"
            "required_top_k = 5\n"
            "+++\n"
            "# Historical transaction scope: canonical intake and binding method\n"
            "\n"
            "## CONTEXT INDEX\n"
            "\n"
            "- [`s-workflow`](#s-workflow) — Explicit intake preserves the record before a separate verified assignment.\n"
            "- [`s-evidence`](#s-evidence) — Byte identity and historical scope claims have different evidentiary limits.\n"
            "- [`s-recovery`](#s-recovery) — Reconcile the persisted event without applying code again or renewing mutation authority.\n"
            "\n"
            "<a id=\"s-workflow\"></a>\n"
            "## WORKFLOW\n"
            "\n"
            "> Capsule: Explicit intake preserves the record before a separate verified assignment.\n"
            "\n"
            "Use the canonical Workshop configuration. Run `tx-scope-inspect TX` and review its exact report hash. Run `tx-history-intake TX --expected-report-sha256 HASH --reason TEXT` to preserve the previous state and document in an immutable receipt and quarantine the record under `HISTORICAL_UNRESOLVED/TX`. The explanation requires 40-2000 characters. Inspect again after intake. Only a complete, consistent VERIFIED inspection permits `tx-scope-bind TX --expected-report-sha256 HASH --reason TEXT`. Binding is a new material event. Missing or conflicting records remain unassigned; current.json and active task state never supply their missing history.\n"
            "\n"
            "The same explicit INTAKE admits two exact first-anchor field families without stored history: pre-material-epoch-anchor/v0 lacks old epoch, baseline, time and projection-obligation bindings; v1 records baseline/time/boolean obligation but still lacks the material epoch. Admission requires a VERIFIED native recorded-scope inspection and exact reviewed report hash. The versioned werkfaden-tx-legacy-event-admission/v1 declaration is stored in the immutable intake receipt, binding exact old state bytes and event identity. Original scope and old event bytes are preserved; separate BIND remains required. Partial, inconsistent or other old formats fail closed.\n"
            "\n"
            "<a id=\"s-evidence\"></a>\n"
            "## EVIDENCE\n"
            "\n"
            "> Capsule: Byte identity and historical scope claims have different evidentiary limits.\n"
            "\n"
            "Inspect controlled TX state and snapshots, Guardian evidence, pinned Task/Goal receipts, pinned loop archives and the associated document in order. Native complete-TX vault manifests and exact state objects can retain a lost original observation. Legacy Guardian fields or a document declaration alone cannot authorize assignment. A successful binding also checks the actual Task/Goal source ownership and preserves those bytes. Receipt hashes prove identity and recorded consistency, not independent historical truth. Scope receipts retain previous bytes and contradictory evidence; no API accepts arbitrary Goal/Task/Loop values as assignment arguments.\n"
            "\n"
            "Missing old material epochs, history-prefix hashes and the v0 baseline/time/obligation fields remain explicitly UNATTESTED. The new INTAKE event proves only the newly observed current state and its projection. It does not manufacture old execution, an old baseline binding or a committed Apply. Generic current-event validation remains strict; a mere old event self-hash or scope inspection never grants deployment authority.\n"
            "\n"
            "<a id=\"s-recovery\"></a>\n"
            "## RECOVERY\n"
            "\n"
            "> Capsule: Reconcile the persisted event without applying code again or renewing mutation authority.\n"
            "\n"
            "Intake and binding require a technically terminal, unleased TX and the retention writer lock. Outbox staging precedes the state commit. If publication fails after the state is saved, retry the exact operation with the same inspected hash and explanation, or use `tx-document-reconcile TX`. Do not repeat Apply. A committed operation has one event identity; replay does not create a new event or false document revision. Explicitly adopted history keeps its projection obligation for later annotations and compaction even under a legacy optional default. Pending mandatory documentation blocks new checkout. Conflict quarantine is retained until historical evidence is consistent; it never transfers ownership to the active Task.\n"
            "\n"
            "Canonical method revision3 and its renderer must be installed together through verified source/document migration. An existing nonmatching method is TX_HISTORY_METHOD_DRIFT, requiring verified document migration; INTAKE does not silently overwrite it. Publication grants bind exact canonical content bytes. Receipt admission declarations and raw preimages are revalidated during replay, retaining one current event identity without repeating Apply.\n"
            "\n"
            "For a fixed workspace and method revision, updated_at is the only instance-owned header variance accepted by the canonical matcher. It must come from the existing native artifact metadata, and the whole document must equal the current renderer for that exact timestamp with a matching artifact path, content hash and reconciled projection. Unpublished timestamp edits, other metadata/body drift and older method revisions are refused. Timestamp variance never grants a new publication permit or rewrites the live method.\n"
        )

    @classmethod
    def _history_method_v2_fixture(cls):
        from kairos.transaction_document import HISTORY_METHOD_INSTANCE_TIMESTAMP_POLICY
        value = cls._history_method_v3_fixture().replace(
            "revision = 3", "revision = 2", 1).replace(
            "Canonical method revision3", "Canonical method revision2", 1).replace(
            "\n\n" + HISTORY_METHOD_INSTANCE_TIMESTAMP_POLICY, "", 1)
        assert hashlib.sha256(value.encode("utf-8")).hexdigest() == "fd2f0e142783ec62ba4d8a4d0b89b5caa6dc3ed3eadb40ae9855392886521a55"
        return value


    def test_legacy_quarantine_missing_and_conflicting_origins_replay_without_binding(self):
        from kairos.database import KnowledgeDatabase
        from kairos.search import search_database
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import load_scope_transition, pending_path
        from runtime_sync_workshop.tx_lifecycle import canonical, validate_event_chain
        from runtime_sync_workshop.util import WorkshopError
        for extended in (False, True):
            for conflict in (False, True):
                for outage in (False, True):
                    with self.subTest(extended=extended, conflict=conflict, outage=outage):
                        fixture, tx, root, before = self._pre_material_epoch_fixture(extended=extended)
                        if conflict:
                            scope = before["original_scope"]
                            witness = root / "baseline" / "contradictory-origin.json"
                            witness.parent.mkdir(exist_ok=True)
                            raw = json.dumps({"schema": "werkfaden-historical-scope-receipt/v1",
                                "transaction_id": tx, "workspace_id": scope["workspace_id"],
                                "scope": {**scope, "loop": scope["loop"] + 10}}).encode()
                            witness.write_bytes(raw)
                            before["historical_scope_receipts"] = [{"tier": "TX_STATE",
                                "path": "baseline/contradictory-origin.json",
                                "sha256": hashlib.sha256(raw).hexdigest()}]
                        else:
                            before.pop("original_scope")
                            anchor = before["tx_event"]
                            anchor["scope_receipt_sha256"] = ""
                            anchor["event_id"] = "TXE_" + hashlib.sha256(canonical({
                                k: v for k, v in anchor.items() if k != "event_id"})).hexdigest()[:32].upper()
                            before["tx_event_id"] = anchor["event_id"]
                        # Deliberately old bytes inside disposable fixtures only.
                        prior = (json.dumps(before, indent=2, sort_keys=True) + "\n").encode()
                        (root / "state.json").write_bytes(prior)
                        document = fixture.workspace / document_path(tx)
                        old_document = document.read_bytes()
                        source = (fixture.project / "app.py").read_bytes()
                        seal = fixture.engine.seal_path.read_bytes()
                        owners = {p.relative_to(fixture.workspace).as_posix(): p.read_bytes()
                                  for folder in ("goals", "tasks")
                                  for p in (fixture.workspace / folder).rglob("*") if p.is_file()}
                        report = fixture.engine.inspect_transaction_scope(tx)
                        self.assertEqual(report["status"], "CONFLICT" if conflict else "MISSING")
                        self.assertIsNone(report["scope"])
                        self.assertFalse(report["automatic_binding"])
                        reason = "Explicit quarantine of exact old origin evidence without assigning current project scope"
                        if outage:
                            with patch("runtime_sync_workshop.tx_documents.publish",
                                       side_effect=WorkshopError("INJECTED_OUTAGE", "isolated quarantine publication cut")):
                                with self.assertRaises(WorkshopError):
                                    fixture.engine.intake_transaction_history(tx,
                                        expected_report_sha256=report["report_sha256"], reason=reason)
                            _, interrupted = fixture.engine._load_transaction(tx)
                            self.assertTrue(pending_path(fixture.engine, tx).exists())
                            intended = interrupted["tx_event_id"]
                        else:
                            result = fixture.engine.intake_transaction_history(tx,
                                expected_report_sha256=report["report_sha256"], reason=reason)
                            intended = result["event_id"]
                        fresh = WorkshopEngine(fixture.engine.config.config_path)
                        with patch.object(fresh, "apply", side_effect=AssertionError("Quarantine must never Apply")):
                            replay = fresh.intake_transaction_history(tx,
                                expected_report_sha256=report["report_sha256"], reason=reason)
                        self.assertTrue(replay["replayed"])
                        self.assertEqual(replay["event_id"], intended)
                        self.assertFalse(pending_path(fresh, tx).exists())
                        after = self.assert_special_projected(fixture, tx, "ABORTED")
                        validate_event_chain(after)
                        self.assertEqual(after["tx_event_history"], [before["tx_event"]])
                        self.assertEqual(after["tx_event_sequence"], before["tx_event_sequence"] + 1)
                        self.assertEqual(after.get("original_scope"), before.get("original_scope"))
                        receipt = load_scope_transition(fresh, after)
                        self.assertEqual(receipt["previous_state_utf8"].encode(), prior)
                        self.assertEqual(receipt["previous_document_utf8"].encode(), old_document)
                        admission = receipt["legacy_event_format"]
                        self.assertEqual(admission["schema"], "werkfaden-tx-legacy-event-admission/v2")
                        self.assertEqual(admission["admission_mode"], "QUARANTINE_ONLY")
                        self.assertIs(admission["scope_assignment_authority"], False)
                        self.assertEqual(admission["inspection_status"], report["status"])
                        self.assertIn("original_scope_provenance", admission["unattested_fields"])
                        metadata = split_frontmatter(document.read_text(encoding="utf-8")).metadata
                        self.assertEqual(metadata["scope_status"], "SCOPE_UNRESOLVED")
                        self.assertEqual(metadata["state"], "blocked")
                        self.assertEqual(metadata["type"], "transaction")
                        self.assertEqual(metadata["criteria"], [])
                        for key in ("goal", "milestone", "task", "loop"):
                            self.assertNotIn(key, metadata)
                        with closing(sqlite3.connect(fixture.database)) as con:
                            projected = json.loads(con.execute(
                                "SELECT metadata_json FROM artifacts WHERE artifact_id=?",
                                (document_id(tx),)).fetchone()[0])
                        for key in ("goal", "milestone", "task", "loop"):
                            self.assertNotIn(key, projected)
                        self.assertEqual(projected["criteria"], [])
                        with closing(sqlite3.connect(fixture.database)) as con:
                            self.assertEqual(con.execute(
                                "SELECT count(*) FROM goal_coverage WHERE artifact_id=?",
                                (document_id(tx),)).fetchone()[0], 0)
                        found = search_database(KnowledgeDatabase(fixture.database),
                                                "HISTORICAL_UNRESOLVED/" + tx, limit=10)
                        self.assertIn(document_id(tx), json.dumps(found))
                        unresolved = fresh.inspect_transaction_scope(tx)
                        self.assertEqual(unresolved["status"], report["status"])
                        with self.assertRaises(WorkshopError) as caught:
                            fresh.bind_transaction_scope(tx,
                                expected_report_sha256=unresolved["report_sha256"], reason=reason)
                        self.assertEqual(caught.exception.code, "TX_SCOPE_EVIDENCE_UNRESOLVED")
                        self.assertEqual((fixture.project / "app.py").read_bytes(), source)
                        self.assertEqual(fixture.engine.seal_path.read_bytes(), seal)
                        self.assertEqual({p.relative_to(fixture.workspace).as_posix(): p.read_bytes()
                                          for folder in ("goals", "tasks")
                                          for p in (fixture.workspace / folder).rglob("*") if p.is_file()}, owners)

    def test_legacy_quarantine_witnessed_origin_loss_requires_separate_verified_binding(self):
        from runtime_sync_workshop.tx_documents import load_scope_transition
        from runtime_sync_workshop.tx_retention import snapshot
        for extended in (False, True):
            with self.subTest(extended=extended):
                fixture, tx, root, before = self._pre_material_epoch_fixture(extended=extended)
                archived = snapshot(fixture.engine, tx, include_entire_tx=True)
                scope = before.pop("original_scope")
                (root / "state.json").write_text(json.dumps(before), encoding="utf-8")
                report = fixture.engine.inspect_transaction_scope(tx)
                self.assertEqual(report["status"], "VERIFIED")
                self.assertEqual(report["scope"]["task_id"], scope["task_id"])
                self.assertTrue(any(row.get("source", {}).get("manifest", {}).get("sha256") == archived["sha256"]
                                    for row in report["evidence"]))
                reason = "Quarantine lost original scope before a separate verified event backed by native archived source"
                fixture.engine.intake_transaction_history(tx,
                    expected_report_sha256=report["report_sha256"], reason=reason)
                _, quarantine = fixture.engine._load_transaction(tx)
                self.assertNotIn("original_scope", quarantine)
                receipt = load_scope_transition(fixture.engine, quarantine)
                self.assertEqual(receipt["legacy_event_format"]["admission_mode"], "QUARANTINE_ONLY")
                metadata = split_frontmatter((fixture.workspace / document_path(tx)).read_text()).metadata
                self.assertNotIn("goal", metadata)
                fresh = fixture.engine.inspect_transaction_scope(tx)
                fixture.engine.bind_transaction_scope(tx,
                    expected_report_sha256=fresh["report_sha256"], reason=reason)
                after = self.assert_special_projected(fixture, tx, "ABORTED")
                self.assertEqual(after["tx_event"]["event"], "TX_SCOPE_BOUND")
                self.assertEqual(after["tx_event_sequence"], quarantine["tx_event_sequence"] + 1)
                for key in ("workspace_id", "goal_id", "milestone_id", "task_id", "loop"):
                    self.assertEqual(after["original_scope"][key], scope[key])
                self.assertEqual(len(load_scope_transition(fixture.engine, after)["owner_sources"]), 2)


    def test_checkout_observation_is_exact_and_not_reassigned_after_current_loop_changes(self):
        from datetime import datetime
        from runtime_sync_workshop.tx_lifecycle import scope_from_checkout
        fixture = self.fixture()
        current_path = fixture.workspace / "current.json"
        raw = current_path.read_bytes()
        current = json.loads(raw)
        captured = scope_from_checkout(fixture.workspace)
        for key, field in (("goal_id", "active_goal"), ("milestone_id", "active_milestone"),
                           ("task_id", "active_task"), ("loop", "loop")):
            self.assertEqual(captured[key], current[field])
        self.assertEqual(captured["source_receipt_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(captured["source_path"], "current.json")
        self.assertEqual(captured["workspace_id"], json.loads((fixture.workspace / ".kairos/config.json").read_text())["workspace_id"])
        self.assertIsNotNone(datetime.fromisoformat(captured["observed_at"].replace("Z", "+00:00")).tzinfo)
        tx = fixture.checkout()["transaction_id"]
        root, state = fixture.engine._load_transaction(tx)
        original = json.loads(json.dumps(state["original_scope"]))
        # Simulate only a later active observation in a disposable fixture.
        current = json.loads(current_path.read_text(encoding="utf-8"))
        current["loop"] = 11
        current_path.write_text(json.dumps(current), encoding="utf-8")
        fixture.engine.abort(tx)
        _, after = fixture.engine._load_transaction(tx)
        self.assertEqual(after["original_scope"], original)
        self.assertNotEqual(original["loop"], 11)


    @staticmethod
    def _projection_process_cut_code():
        # Execute the same installed native owners in a real disposable child.
        return r"""
import os,sys,json,time
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = sys.argv[1:4]
from runtime_sync_workshop import engine as owner, tx_documents
from kairos import heartbeat,util
from kairos.transaction_document import document_path
config,tx,point,ready=sys.argv[4:]
engine=owner.WorkshopEngine(Path(config))
document=engine.config.kairos_workspace/document_path(tx)
fired=[False]
def stop():
    target=Path(ready);temporary=target.with_suffix(".writing")
    with temporary.open("x",encoding="utf-8") as f:
        json.dump({"point":point,"pid":os.getpid(),"transaction_id":tx},f)
        f.flush();os.fsync(f.fileno())
    os.replace(temporary,target)
    while True:time.sleep(0.05)
def inject(original,predicate,after=False):
    def call(*a,**k):
        if predicate(*a,**k) and not fired[0]:
            fired[0]=True
            if after:original(*a,**k)
            stop()
        return original(*a,**k)
    return call
if point in {"before_state_save","after_state_save"}:
    target=patch.object(owner._CoreWorkshopEngine,"_save_transaction",inject(
        owner._CoreWorkshopEngine._save_transaction,lambda *a,**k:k.get("event")=="TX_NOTE",point=="after_state_save"))
elif point in {"before_markdown_write","after_markdown_write"}:
    target=patch.object(util,"atomic_write_text",inject(util.atomic_write_text,
        lambda path,*a,**k:Path(path)==document,point=="after_markdown_write"))
elif point in {"before_sql_promotion","after_sql_promotion"}:
    target=patch.object(heartbeat,"promote_document",inject(heartbeat.promote_document,
        lambda path,*a,**k:Path(path)==document,point=="after_sql_promotion"))
else:
    target=patch.object(tx_documents,"atomic_write_json",inject(tx_documents.atomic_write_json,
        lambda path,*a,**k:"transaction-document-projections" in Path(path).parts))
with target:
    engine.annotate_transaction(tx,kind="observation",
        summary="A real parent-terminated process retains the exact native projection obligation",
        evidence="docs/APP_BEHAVIOR.md#s-behavior")
raise AssertionError("Requested native cut was not reached")
"""

    def test_projection_seven_real_process_kills_recover_once_without_apply(self):
        import time
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import pending_path, assert_projected
        from runtime_sync_workshop.tx_lifecycle import event_intents, source_epoch
        from runtime_sync_workshop.util import WorkshopError
        from kairos.promoter import prepare_document
        points = ("before_state_save", "after_state_save", "before_markdown_write",
                  "after_markdown_write", "before_sql_promotion", "after_sql_promotion",
                  "before_projection_checkpoint")
        for point in points:
            with self.subTest(killpoint=point):
                fixture = self.fixture()
                tx = fixture.checkout()["transaction_id"]
                _, before = fixture.engine._load_transaction(tx)
                document = fixture.workspace / document_path(tx)
                checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
                source = (fixture.project / "app.py").read_bytes()
                seal = fixture.engine.seal_path.read_bytes()
                ready = fixture.workspace.parent / "projection-cut-ready.json"
                child = subprocess.Popen([sys.executable, "-B", "-c", self._projection_process_cut_code(),
                    str(ROOT), str(ROOT / "workshop/src"), str(ROOT / "kairos/kairos_harness"),
                    str(fixture.engine.config.config_path), tx, point, str(ready)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    deadline = time.monotonic() + 20
                    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), "Child did not reach the exact native cut")
                    self.assertEqual(json.loads(ready.read_text(encoding="utf-8")),
                        {"point": point, "pid": child.pid, "transaction_id": tx})
                    child.kill()  # Windows TerminateProcess/SIGKILL: no Python unwinding.
                    stdout, stderr = child.communicate(timeout=5)
                    self.assertNotEqual(child.returncode, 0)
                    self.assertEqual(stderr, "")
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)
                pending = json.loads(pending_path(fixture.engine, tx).read_text(encoding="utf-8"))
                intended = pending["event_id"]
                self.assertNotEqual(intended, before["tx_event_id"])
                _, interrupted = fixture.engine._load_transaction(tx)
                self.assertEqual(interrupted["tx_event_id"],
                    before["tx_event_id"] if point == "before_state_save" else intended)
                fresh = WorkshopEngine(fixture.engine.config.config_path)
                with patch.object(fresh, "apply", side_effect=AssertionError("Repair must never Apply")):
                    with self.assertRaises(WorkshopError) as caught:
                        assert_projected(fresh, tx)
                    self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                    fresh.reconcile_transaction_document(tx)
                    assert_projected(fresh, tx)
                    _, state = fresh._load_transaction(tx)
                    self.assertEqual(state["tx_event_id"], intended)
                    self.assertEqual(state["tx_event_sequence"], before["tx_event_sequence"] + 1)
                    self.assertEqual(len(state["transaction_notes"]), 1)
                    self.assertEqual(event_intents(fresh, tx), [])
                    self.assertFalse(pending_path(fresh, tx).exists())
                    receipt = json.loads(checkpoint.read_text(encoding="utf-8"))
                    metadata = split_frontmatter(document.read_text(encoding="utf-8")).metadata
                    self.assertEqual(receipt["event_id"], metadata["tx_event_id"])
                    self.assertEqual(receipt["document_revision"], metadata["revision"])
                    self.assertEqual(receipt["source_epoch"], source_epoch(state))
                    self.assertEqual(hashlib.sha256(receipt["source_state_utf8"].encode()).hexdigest(),
                                     receipt["source_state_sha256"])
                    self.assertEqual(receipt["content_sha256"],
                                     prepare_document(document, fixture.workspace).content_sha256)
                    self.assertTrue(receipt["heartbeat_id"])
                    with closing(sqlite3.connect(fixture.database)) as con:
                        sql = con.execute("SELECT revision,content_sha256 FROM artifacts WHERE artifact_id=?",
                                          (document_id(tx),)).fetchone()
                        revisions = con.execute("SELECT count(*) FROM artifact_revisions WHERE artifact_id=?",
                                                (document_id(tx),)).fetchone()[0]
                    self.assertEqual(sql, (metadata["revision"], receipt["content_sha256"]))
                    self.assertEqual(revisions, 2)
                    stable = ((fresh._transaction_path(tx) / "state.json").read_bytes(),
                              document.read_bytes(), checkpoint.read_bytes())
                    fresh.reconcile_transaction_document(tx)
                    assert_projected(fresh, tx)
                    self.assertEqual(((fresh._transaction_path(tx) / "state.json").read_bytes(),
                                      document.read_bytes(), checkpoint.read_bytes()), stable)
                    with closing(sqlite3.connect(fixture.database)) as con:
                        self.assertEqual(con.execute(
                            "SELECT count(*) FROM artifact_revisions WHERE artifact_id=?",
                            (document_id(tx),)).fetchone()[0], revisions)
                self.assertEqual((fixture.project / "app.py").read_bytes(), source)
                self.assertEqual(fresh.seal_path.read_bytes(), seal)
                fresh.abort(tx)

    def test_restore_source_lineage_blocks_regression_and_forks_before_any_write(self):
        import tempfile
        from types import SimpleNamespace
        from runtime_sync_workshop.tx_documents import assert_projection_lineage
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "missing-workspace"
            state_dir = base / "state"
            def require_binding():
                raise RuntimeError("Harness binding is required for existing evidence")
            owner = SimpleNamespace(config=SimpleNamespace(
                state_directory=state_dir, kairos_workspace=workspace),
                _import_kairos=require_binding)
            state = {"transaction_id": "TXN_no_prior_publication"}
            assert_projection_lineage(owner, state)
            self.assertEqual(list(base.iterdir()), [])
            workspace.mkdir()
            with self.assertRaisesRegex(RuntimeError, "Harness binding is required"):
                assert_projection_lineage(owner, state)
            workspace.rmdir()
            checkpoint = state_dir / "transaction-document-projections" / (state["transaction_id"] + ".json")
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Harness binding is required"):
                assert_projection_lineage(owner, state)
            self.assertEqual(checkpoint.read_bytes(), b"{}")
            self.assertFalse(workspace.exists())
        import copy
        import shutil
        import zipfile
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        from runtime_sync_workshop.tx_lifecycle import canonical, source_epoch, validate_event_chain, event_intents
        from runtime_sync_workshop.util import WorkshopError
        from kairos.backup import create_backup, restore_drill
        cases = ("older_document_sql", "older_checkpoint", "source_prefix", "source_fork",
                 "source_fork_larger_sequence", "source_prefix_corrupt_checkpoint",
                 "source_prefix_missing_checkpoint")
        for mode in cases:
            with self.subTest(restore=mode):
                fixture = self.fixture()
                tx = fixture.checkout()["transaction_id"]
                root, old = fixture.engine._load_transaction(tx)
                source_path = root / "state.json"
                document = fixture.workspace / document_path(tx)
                checkpoint = fixture.engine.config.state_directory / "transaction-document-projections" / (tx + ".json")
                old_source, old_ack = source_path.read_bytes(), checkpoint.read_bytes()
                if mode == "older_document_sql":
                    backup = create_backup(fixture.workspace)
                    self.assertTrue(backup["verification"]["verified"])
                    self.assertTrue(restore_drill(fixture.workspace, backup["backup_id"])["verified"])
                fixture.engine.annotate_transaction(tx, kind="observation",
                    summary="A newer acknowledged material fact must not silently disappear after restore",
                    evidence="docs/APP_BEHAVIOR.md#s-behavior")
                _, current = fixture.engine._load_transaction(tx)
                current_raw = source_path.read_bytes()
                if mode == "older_document_sql":
                    package = fixture.workspace / backup["path"]
                    self.assertFalse(Path(str(fixture.database) + "-wal").exists())
                    self.assertFalse(Path(str(fixture.database) + "-shm").exists())
                    with zipfile.ZipFile(package / "sources.zip") as archive:
                        document.write_bytes(archive.read(document_path(tx)))
                    shutil.copy2(package / "kairos.db", fixture.database)
                elif mode == "older_checkpoint":
                    checkpoint.write_bytes(old_ack)
                else:
                    # Exact synthetic restore/fork inputs in disposable fixtures only.
                    restored = copy.deepcopy(old)
                    if "fork" in mode:
                        restored = copy.deepcopy(current)
                        restored["transaction_notes"][-1]["summary"] = "Different unacknowledged branch"
                        if mode == "source_fork_larger_sequence":
                            restored["tx_event_sequence"] += 10
                            restored["tx_event"]["sequence"] = restored["tx_event_sequence"]
                        event = restored["tx_event"]
                        event["source_epoch"] = source_epoch(restored)
                        event["event_id"] = "TXE_" + hashlib.sha256(canonical({
                            k: v for k, v in event.items() if k != "event_id"})).hexdigest()[:32].upper()
                        restored["tx_event_id"] = event["event_id"]
                        validate_event_chain(restored)
                    source_path.write_bytes((json.dumps(restored, indent=2, sort_keys=True) + "\n").encode())
                    if mode == "source_prefix_corrupt_checkpoint":
                        corrupted = json.loads(checkpoint.read_text(encoding="utf-8"))
                        corrupted["source_state_utf8"] = "{}"
                        checkpoint.write_text(json.dumps(corrupted), encoding="utf-8")
                    elif mode == "source_prefix_missing_checkpoint":
                        checkpoint.unlink()
                fresh = WorkshopEngine(fixture.engine.config.config_path)
                with self.assertRaises(WorkshopError):
                    assert_projected(fresh, tx)
                def evidence():
                    with closing(sqlite3.connect(fixture.database)) as con:
                        sql = con.execute("SELECT revision,content_sha256,metadata_json FROM artifacts WHERE artifact_id=?",
                                          (document_id(tx),)).fetchone()
                        count = con.execute("SELECT count(*) FROM artifact_revisions WHERE artifact_id=?",
                                            (document_id(tx),)).fetchone()[0]
                    return (source_path.read_bytes(), document.read_bytes(),
                            checkpoint.read_bytes() if checkpoint.exists() else None, sql, count)
                if mode.startswith("source_"):
                    stable = evidence()
                    with patch("kairos.governance.issue_external_edit_permit",
                               side_effect=AssertionError("Unproved source must not request publication authority")):
                        with self.assertRaises(WorkshopError) as caught:
                            fresh.reconcile_transaction_document(tx)
                        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SOURCE_HISTORY_CONFLICT")
                        self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                        self.assertEqual(evidence(), stable)
                        with self.assertRaises(WorkshopError) as caught:
                            fresh.annotate_transaction(tx, kind="observation",
                                summary="An unproved restored branch must not gain another native event",
                                evidence="docs/APP_BEHAVIOR.md#s-behavior")
                        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SOURCE_HISTORY_CONFLICT")
                        self.assertEqual(evidence(), stable)
                        _, diagnostic = fresh._load_transaction(tx)
                        with self.assertRaises(WorkshopError) as caught:
                            fresh._save_transaction(root, diagnostic, event="FIXTURE_DIAGNOSTIC_ONLY",
                                details={"reason": "A diagnostic cannot adopt an unproved restored source"})
                        self.assertEqual(caught.exception.code, "TX_DOCUMENT_SOURCE_HISTORY_CONFLICT")
                        self.assertEqual(evidence(), stable)
                    self.assertFalse(pending_path(fresh, tx).exists())
                    self.assertEqual(event_intents(fresh, tx), [])
                    # Undo this test's fault with the retained exact native source.
                    # This is NOT a production source-reconstruction or recovery API.
                    source_path.write_bytes(current_raw)
                with patch.object(fresh, "apply", side_effect=AssertionError("Restore repair must never Apply")):
                    fresh.reconcile_transaction_document(tx)
                    assert_projected(fresh, tx)
                    _, after = fresh._load_transaction(tx)
                    self.assertEqual(after["tx_event_id"], current["tx_event_id"])
                    self.assertEqual(len(after["transaction_notes"]), 1)
                    stable = evidence()
                    fresh.reconcile_transaction_document(tx)
                    self.assertEqual(evidence(), stable)
                fresh.abort(tx)




if __name__ == "__main__":
    unittest.main()
