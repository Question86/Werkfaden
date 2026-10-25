from __future__ import annotations

import json
import ast
import hashlib
from datetime import datetime
import sqlite3
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


class TransactionDocumentIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = ContextTransactionTests("test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.engine.config.raw["transaction_documents_required"] = True
        return fixture

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
        with sqlite3.connect(fixture.database) as connection:
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
        with sqlite3.connect(fixture.database) as connection:
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
        with sqlite3.connect(fixture.database) as connection:
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
                with patch.object(fixture.engine, "apply", side_effect=AssertionError("Reconcile must not Apply")):
                    fixture.engine.reconcile_transaction_document(tx)
                    tx_documents.assert_projected(fixture.engine, tx)
                    _, recovered = fixture.engine._load_transaction(tx)
                    self.assertEqual(recovered["tx_event_id"], saved["tx_event_id"])
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
        with sqlite3.connect(fixture.database) as connection:
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
        with sqlite3.connect(fixture.database) as connection:
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

    def test_authoritative_save_inventory_and_state_phase_contract_do_not_drift(self):
        from runtime_sync_workshop import _engine_core_v1
        from runtime_sync_workshop.tx_lifecycle import PHASES
        package = Path(_engine_core_v1.__file__).resolve().parent
        # This is a snapshot of declared write sites, not an alternative
        # runtime permission list or a claim of compiler/source membership.
        sources = [_engine_core_v1._SOURCE, *[package / name for name in (
            "engine.py", "auxiliary.py", "migration.py", "universal_migration.py", "repair.py",
            "tx_recycling.py", "tx_retention.py",
        )]]
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
                            and call.func.attr == "_save_transaction"):
                        continue
                    keyword = next((value for value in call.keywords if value.arg == "event"), None)
                    if keyword and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        event = keyword.value.value
                    else:
                        self.assertEqual((relative, node.name), ("engine.py", "_save_transaction"),
                                         "Unreviewed dynamic lifecycle event writer")
                        event = "<forwarded>"
                    rows.append((relative, node.name, event))
                # Terminal history is written without a released Guardian lease.
                # Its raw state commits still require the common lifecycle stamp.
                for call in ast.walk(node):
                    if not (relative in {"tx_recycling.py", "tx_retention.py"}
                            and isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "atomic_write_json" and call.args
                            and isinstance(call.args[0], ast.BinOp)
                            and isinstance(call.args[0].right, ast.Constant)
                            and call.args[0].right.value == "state.json"):
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
        self.assertEqual(fingerprint, "d7c73b5b8e8b664d540b4ac9831e5098f6f09a61b5f4e238e9ab3e7690093eab",
                         f"Authoritative TX event writers changed; review each new event/type and update the inventory evidence: {rows}")

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
        with sqlite3.connect(fixture.database) as con:
            row = con.execute("SELECT revision,document_type FROM artifacts WHERE artifact_id=?",
                              (document_id(tx),)).fetchone()
            self.assertEqual(row, (1, "transaction"))
        result = fixture.engine.abort(tx)
        self.assertEqual(result["state"], "ABORTED")
        revised = split_frontmatter(path.read_text(encoding="utf-8")).metadata
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["workshop_state"], "ABORTED")
        with sqlite3.connect(fixture.database) as con:
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
        with sqlite3.connect(fixture.database) as con:
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
        with sqlite3.connect(fixture.database) as con:
            count = con.execute(
                "SELECT count(*) FROM tx_recycling_atoms WHERE artifact_id=?",
                (document_id(tx),),
            ).fetchone()[0]
        self.assertGreater(count, 0)

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
        with sqlite3.connect(fixture.database) as con:
            row = con.execute("SELECT state FROM artifacts WHERE artifact_id=?",
                              (document_id(checkout["transaction_id"]),)).fetchone()
            self.assertEqual(row, ("completed",))

if __name__ == "__main__":
    unittest.main()
