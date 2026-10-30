from __future__ import annotations

import hashlib
import json
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
for value in (ROOT, ROOT / "workshop" / "src", ROOT / "kairos" / "kairos_harness"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.universal_init import initialize_universal_markdown  # noqa: E402
from kairos.database import KnowledgeDatabase  # noqa: E402
from kairos.frontmatter import split_frontmatter  # noqa: E402
from kairos.governance import issue_external_edit_permit  # noqa: E402
from kairos.heartbeat import run_heartbeat  # noqa: E402
from kairos.search import search_database  # noqa: E402
from kairos.templates import pointer, render_document  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402
from runtime_sync_workshop.util import WorkshopError, copy_exact  # noqa: E402


BEHAVIOR = "docs/APP_BEHAVIOR.md"
REFERENCE = "docs/APP_REFERENCE.md"
REVIEW_FIELDS = ["capsule", "answers", "refs", "claim_boundary", "sections"]


def project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_CONTEXT",
            "title": "Code and context transaction",
            "objective": "Keep application behavior and retrieval context synchronized.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_CONTEXT_01",
                "title": "Verified semantic context",
                "objective": "Apply code and its reviewed Markdown in one transaction.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_CONTEXT_001",
                    "description": "A successful apply exposes the matching context in search.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck and context retrieval assertions.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_CONTEXT_001",
            "title": "Synchronize code and context",
            "objective": "Verify an existing-file code patch and its Markdown as one package.",
            "milestone": "MILESTONE_CONTEXT_01",
            "criteria": ["CRIT_CONTEXT_001"],
        },
    }


class ContextTransactionTests(unittest.TestCase):
    """Exercise real native heartbeat promotion against isolated Python projects."""

    def authored_code_fixture(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from kairos.cli import main

        original_seal = WorkshopEngine.seal

        def create_authored_map_before_seal(engine, *args, **kwargs):
            current = json.loads((engine.config.kairos_workspace / 'current.json').read_text(encoding='utf-8'))
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                status = main(['new-code', '--workspace', str(engine.config.kairos_workspace),
                    '--id', 'CODE_AUTHORED_MAP', '--task', current['active_task'],
                    '--goal', current['active_goal'], '--milestone', current['active_milestone'],
                    '--title', 'Authored application map', '--purpose', 'legacycodemap describes the application.',
                    '--components', 'app.py is the exact declared application source.',
                    '--interfaces', 'The application exposes one value.',
                    '--invariants', 'This prose map does not own source membership or exact ledgers.',
                    '--dependencies', 'The native source ledger remains independently governed.',
                    '--testing', 'Executed results require separate reports.', '--state', 'active', '--mode', 'verify'])
            self.assertEqual(status, 0, output.getvalue() + errors.getvalue())
            self.assertTrue(json.loads(output.getvalue())['heartbeat']['verified'])
            return original_seal(engine, *args, **kwargs)

        fixture = ContextTransactionTests()
        self.addCleanup(fixture.doCleanups)
        with patch.object(WorkshopEngine, 'seal', new=create_authored_map_before_seal):
            fixture.setUp()
        return fixture, 'code/CODE_AUTHORED_MAP.md'

    def authored_code_checkout(self, fixture, document):
        checkout = fixture.checkout(documents=(document,))
        # Context-only maintenance: the existing source bytes do not change.
        (Path(checkout['work_directory']) / 'app.py').write_bytes(b'value = 1\n')
        path = Path(checkout['work_directory']) / 'documents' / document
        path.write_bytes(path.read_bytes().replace(b'legacycodemap', b'freshcodemap'))
        fixture.review_document(checkout, document, fields=[
            'capsule', 'answers', 'refs', 'claim_boundary', 'entities', 'facets',
            'does_not_answer', 'search_contract', 'sections'])
        return checkout, path

    def test_authored_code_context_native_creation_apply_and_search_update(self):
        fixture, document = self.authored_code_fixture()
        source = (fixture.project / 'app.py').read_bytes()
        generated = {p.name: p.read_bytes() for p in fixture.engine.config.blueprint_root.glob('*.md')}
        checkout, candidate = self.authored_code_checkout(fixture, document)
        original = (fixture.workspace / document).read_bytes()
        fixture.prepare_and_verify(checkout)
        self.assertEqual((fixture.workspace / document).read_bytes(), original)
        result = fixture.engine.apply(checkout['transaction_id'])
        self.assertEqual(result['state'], 'POSTCHECK_VERIFIED')
        self.assertTrue(result['bit_exact'])
        self.assertIn('CODE_AUTHORED_MAP', result['heartbeat']['promoted'])
        self.assertEqual((fixture.workspace / document).read_bytes(), candidate.read_bytes())
        self.assertEqual((fixture.project / 'app.py').read_bytes(), source)
        self.assertEqual({p.name: p.read_bytes() for p in fixture.engine.config.blueprint_root.glob('*.md')}, generated)
        with closing(sqlite3.connect(fixture.database)) as connection:
            row = connection.execute("SELECT revision,capsule FROM artifacts WHERE artifact_id='CODE_AUTHORED_MAP'").fetchone()
            self.assertEqual(row[0], 2)
            self.assertIn('freshcodemap', row[1])
            self.assertEqual(connection.execute("SELECT count(*) FROM section_fts WHERE section_fts MATCH 'legacycodemap' AND artifact_id='CODE_AUTHORED_MAP'").fetchone()[0], 0)
            self.assertGreater(connection.execute("SELECT count(*) FROM section_fts WHERE section_fts MATCH 'freshcodemap' AND artifact_id='CODE_AUTHORED_MAP'").fetchone()[0], 0)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_authored_code_partial_copy_rolls_back_exact_bytes_freshness_and_projection(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture, document = self.authored_code_fixture()
        checkout, candidate = self.authored_code_checkout(fixture, document)
        fixture.prepare_and_verify(checkout)
        live = fixture.workspace / document
        paths = [fixture.project / 'app.py', *(fixture.workspace / 'code').glob('*.md')]
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        interrupted = False

        def fail_after_copy(source, destination):
            nonlocal interrupted
            copy_exact(source, destination)
            if source == candidate and destination == live and not interrupted:
                interrupted = True
                self.assertEqual(live.read_bytes(), candidate.read_bytes())
                raise OSError('isolated interruption after authored code map copy')

        core_apply = super(type(fixture.engine), fixture.engine).apply
        with patch.dict(core_apply.__func__.__globals__, {'copy_exact': fail_after_copy}):
            with self.assertRaises(WorkshopError) as raised:
                fixture.engine.apply(checkout['transaction_id'])
        self.assertTrue(interrupted)
        self.assertEqual(raised.exception.code, 'APPLY_ROLLED_BACK')
        self.assertEqual(fixture.engine.transaction_status(checkout['transaction_id'])['state'], 'ROLLED_BACK')
        for path, facts in original.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), facts, str(path))
        assert_projected(fixture.engine, checkout['transaction_id'])
        with closing(sqlite3.connect(fixture.database)) as connection:
            self.assertEqual(connection.execute("SELECT revision FROM artifacts WHERE artifact_id='CODE_AUTHORED_MAP'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM action_permits WHERE command_name='external-edit' AND status='ACTIVE'").fetchone()[0], 0)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_authored_code_context_abort_preserves_original_map_source_and_seal(self):
        fixture, document = self.authored_code_fixture()
        original = (fixture.workspace / document).read_bytes()
        baseline = fixture.engine.status()['package_sha256']
        checkout, _ = self.authored_code_checkout(fixture, document)
        fixture.prepare_and_verify(checkout)
        fixture.engine.abort(checkout['transaction_id'])
        self.assertEqual((fixture.workspace / document).read_bytes(), original)
        self.assertEqual((fixture.project / 'app.py').read_bytes(), b'value = 1\n')
        self.assertEqual(fixture.engine.status()['package_sha256'], baseline)
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_authored_code_candidate_cannot_become_a_source_mapping_or_change_role(self):
        fixture, document = self.authored_code_fixture()
        original = (fixture.workspace / document).read_bytes()
        for reserved in ('s-mapping', 's-source-ledger', 's-header-ledger', 'authority'):
            with self.subTest(reserved=reserved):
                checkout, candidate = self.authored_code_checkout(fixture, document)
                text = candidate.read_text(encoding='utf-8')
                if reserved == 'authority':
                    text = text.replace('authority = "implementation_documentation"', 'authority = "architecture_authority"')
                else:
                    text += '\n<a id="' + reserved + '"></a>\n## Reserved native authority\n\n> Capsule: This candidate attempts an unmapped source authority.\n\nUnregistered mapping must not be admitted.\n'
                candidate.write_bytes(text.encode())
                with self.assertRaises(WorkshopError) as raised:
                    fixture.engine.prepare(checkout['transaction_id'])
                self.assertEqual(raised.exception.code, 'CONTEXT_DOCUMENT_INVALID')
                self.assertEqual((fixture.workspace / document).read_bytes(), original)
                fixture.engine.abort(checkout['transaction_id'])
                self.assertFalse(fixture.engine.lease_path.exists())

    def test_authored_code_context_requires_verified_exact_source_ownership(self):
        from runtime_sync_workshop.context_documents import ContextDocuments
        fixture, document = self.authored_code_fixture()
        source = fixture.project / 'app.py'
        source.write_bytes(b'value = 99\n')
        with self.assertRaises(WorkshopError) as raised:
            ContextDocuments(fixture.engine.config).normalize([document])
        self.assertEqual(raised.exception.code, 'CONTEXT_DOCUMENT_INVALID')
        self.assertFalse(fixture.engine.lease_path.exists())

    def initial_auxiliary_documents(self):
        return ()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workshop-context-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "app.py").write_bytes(b"value = 1\n")
        self.workspace = self.root / "workspace"
        intake = initialize_universal_markdown(
            project_root=self.project,
            workspace=self.workspace,
            workspace_id="KAIROS_CONTEXT_TEST",
            project_spec=project_spec(),
            auxiliary_documents=self.initial_auxiliary_documents(),
        )
        self.assertEqual(intake["state"], "VERIFIED_PENDING_SEAL")
        # Establish authoritative context before sealing, using the same governed
        # promotion path as initial intake; never fabricate database rows.
        issue_external_edit_permit(
            self.workspace,
            paths=[BEHAVIOR, REFERENCE],
            reason="Establish initial application context before the first Workshop seal",
        )
        (self.workspace / BEHAVIOR).write_bytes(self.behavior_document().encode("utf-8"))
        (self.workspace / REFERENCE).write_bytes(self.reference_document().encode("utf-8"))
        receipt = run_heartbeat(
            self.workspace,
            requested_mode="verify",
            changed_paths=[BEHAVIOR, REFERENCE],
            use_reconciliation=True,
            trigger="context-transaction-test-baseline",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.engine = WorkshopEngine(Path(intake["workshop_config"]))
        self.engine.seal()
        self.database = self.workspace / ".kairos" / "kairos.db"

    def test_same_clock_checkout_attempts_are_distinct_and_preserve_prior_transactions(self):
        from uuid import UUID

        fixed = "2030-01-01T00:00:00Z"
        prior = {}
        with patch("runtime_sync_workshop._core_checkout.utc_now", return_value=fixed), \
             patch("runtime_sync_workshop._core_checkout.uuid4",
                   side_effect=[UUID(int=index + 1) for index in range(3)]) as nonce:
            for attempt in range(3):
                checkout = self.engine.checkout(["app.py"], documents=(BEHAVIOR,),
                    purpose="Repeat identical source admission while the clock is fixed")
                tx = checkout["transaction_id"]
                state_path = Path(checkout["work_directory"]).parent / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertNotIn(tx, prior)
                self.assertRegex(tx, r"^TXN_[0-9a-f]{24}$")
                self.assertEqual(state["created_at"], fixed)
                self.assertEqual(state["attempt_nonce"], UUID(int=attempt + 1).hex)
                for state_path, before in prior.values():
                    self.assertEqual(state_path.read_bytes(), before)
                self.assertEqual(self.engine.abort(tx)["state"], "ABORTED")
                prior[tx] = (state_path, state_path.read_bytes())
                self.assertFalse(self.engine.lease_path.exists())
        self.assertEqual(nonce.call_count, 3)
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_repeated_attempt_nonce_still_refuses_an_existing_transaction(self):
        from uuid import UUID

        with patch("runtime_sync_workshop._core_checkout.utc_now", return_value="2030-01-01T00:00:00Z"), \
             patch("runtime_sync_workshop._core_checkout.uuid4", return_value=UUID(int=1)):
            first = self.engine.checkout(["app.py"], documents=(BEHAVIOR,),
                purpose="Preserve exact collision refusal despite a repeated entropy value")
            tx = first["transaction_id"]
            self.assertEqual(self.engine.abort(tx)["state"], "ABORTED")
            state_path = Path(first["work_directory"]).parent / "state.json"
            before = state_path.read_bytes()
            with self.assertRaises(WorkshopError) as caught:
                self.engine.checkout(["app.py"], documents=(BEHAVIOR,),
                    purpose="Preserve exact collision refusal despite a repeated entropy value")
            self.assertEqual(caught.exception.code, "TRANSACTION_COLLISION")
            self.assertEqual(state_path.read_bytes(), before)
            self.assertFalse(self.engine.lease_path.exists())
            self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def _policy_candidate(self):
        profile = "mandatory-runtime/v1"
        plan = self.engine.policy_activation_plan(profile)
        checkout = self.engine.policy_checkout(profile, expected_plan_sha256=plan["plan_sha256"],
                                               purpose="Activate the exact mandatory runtime profile in an isolated native fixture")
        review_path = Path(checkout["review"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review.update(reviewer="native-policy-integration",
                      reviewed_files=sorted(row["path"] for row in plan["files"]),
                      reason="Reviewed exact source-bound plan; only mandatory flags and an existing governed intake binding may change.")
        review_path.write_text(json.dumps(review, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return checkout, plan

    def _verified_policy(self):
        checkout, plan = self._policy_candidate()
        tx = checkout["transaction_id"]
        self.engine.policy_prepare(tx)
        self.engine.policy_verify(tx)
        return checkout, plan

    def test_runtime_policy_writer_native_roundtrip_and_terminal_replay(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        before_source = (self.project / "app.py").read_bytes()
        before_config = dict(self.engine.config.raw)
        checkout, plan = self._verified_policy()
        tx = checkout["transaction_id"]
        self.assertIsNot(self.engine.config.raw.get("tx_retention_required"), True)
        result = self.engine.policy_apply(tx)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(result["heartbeat"]["verified"])
        self.assertEqual(self.engine.config.raw, {**before_config, "transaction_documents_required": True,
                                                  "tx_retention_required": True})
        self.assertEqual((self.project / "app.py").read_bytes(), before_source)
        self.assertFalse(self.engine.lease_path.exists())
        assert_projected(self.engine, tx)
        state = self.engine.transaction_status(tx)
        original_event = state["tx_event_id"]
        fresh = WorkshopEngine(self.engine.config.config_path)
        replay = fresh.policy_recover(tx)
        self.assertEqual(replay["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(fresh.transaction_status(tx)["tx_event_id"], original_event)
        self.assertEqual(sum(row["event"] == "POLICY_APPLY_STARTED" for row in state["journal"]), 1)
        self.assertTrue(fresh.status(persist=False)["verified"])
        # Prove activation changes subsequent native admission, not just JSON.
        ordinary = fresh.checkout(["app.py"], purpose="Prove activated policy forces documentation on the next ordinary checkout")
        next_tx = ordinary["transaction_id"]
        self.assertTrue(fresh.transaction_status(next_tx)["document_projection_required"])
        assert_projected(fresh, next_tx)
        self.assertTrue(fresh.transaction_pool_status()["enabled"])
        self.assertEqual(fresh.abort(next_tx)["state"], "ABORTED")

    def test_activated_policy_enforces_nine_real_worktrees_and_session_entry(self):
        from kairos.heartbeat import status_report
        from runtime_sync_workshop.tx_documents import assert_projected
        checkout, _ = self._verified_policy()
        policy_tx = checkout["transaction_id"]
        self.engine.policy_apply(policy_tx)
        fresh = WorkshopEngine(self.engine.config.config_path)
        before_config = fresh.config.config_path.read_bytes()
        before_source = (self.project / "app.py").read_bytes()
        before_seal = fresh.seal_path.read_bytes()
        actual_ids = {policy_tx}
        # No fabricated state/work directories or cached flag edits: the policy
        # transaction plus eight real checkout/abort pairs fill nine slots.
        for _ in range(8):
            ordinary = fresh.checkout(["app.py"], purpose="Exercise activated native admission with a real abandoned candidate")
            tx = ordinary["transaction_id"]
            actual_ids.add(tx)
            self.assertTrue(fresh.transaction_status(tx)["document_projection_required"])
            assert_projected(fresh, tx)
            self.assertEqual(fresh.abort(tx)["state"], "ABORTED")
            assert_projected(fresh, tx)
        pool = fresh.transaction_pool_status()
        self.assertEqual(pool["materialized_count"], 9)
        self.assertEqual({row["transaction_id"] for row in pool["rows"]}, actual_ids)
        self.assertTrue(pool["enabled"])
        self.assertTrue(pool["required"])
        observed = status_report(self.workspace)
        self.assertTrue(observed["tx_recycling_live"]["required"])
        self.assertEqual(observed["next_required_read"], "_LOOP_GATE.md#s-tx-recycling")
        before_ids = {path.name for path in fresh.config.transaction_directory.iterdir() if path.is_dir()}
        with self.assertRaises(WorkshopError) as refusal:
            fresh.checkout(["app.py"], purpose="Refuse a tenth candidate until native recycling completes")
        self.assertEqual(refusal.exception.code, "TX_RECYCLING_REQUIRED")
        self.assertEqual({path.name for path in fresh.config.transaction_directory.iterdir() if path.is_dir()}, before_ids)
        self.assertFalse(fresh.lease_path.exists())
        self.assertEqual(fresh.config.config_path.read_bytes(), before_config)
        self.assertEqual((self.project / "app.py").read_bytes(), before_source)
        self.assertEqual(fresh.seal_path.read_bytes(), before_seal)

    def test_activated_policy_publication_outage_blocks_fresh_checkout_until_exact_reconcile(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path
        checkout, _ = self._verified_policy()
        self.engine.policy_apply(checkout["transaction_id"])
        fresh = WorkshopEngine(self.engine.config.config_path)
        ordinary = fresh.checkout(["app.py"], purpose="Exercise activated mandatory publication across a terminal outage")
        tx = ordinary["transaction_id"]
        before_config = fresh.config.config_path.read_bytes()
        before_source = (self.project / "app.py").read_bytes()
        with patch("runtime_sync_workshop.tx_documents.publish",
                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Isolated publication-boundary fault")):
            with self.assertRaises(WorkshopError) as refusal:
                fresh.abort(tx)
        self.assertEqual(refusal.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        marker = pending_path(fresh, tx)
        pending = json.loads(marker.read_bytes())
        terminal = fresh.transaction_status(tx)
        self.assertEqual(terminal["state"], "ABORTED")
        self.assertTrue(terminal["document_projection_required"])
        self.assertEqual(pending["event_id"], terminal["tx_event_id"])
        restarted = WorkshopEngine(fresh.config.config_path)
        with self.assertRaises(WorkshopError) as blocked:
            restarted.checkout(["app.py"], purpose="Refuse progress while the actual terminal event remains unpublished")
        self.assertEqual(blocked.exception.code, "TX_DOCUMENT_SYNC_PENDING")
        self.assertEqual(blocked.exception.references["items"][0]["event_id"], terminal["tx_event_id"])
        with patch.object(restarted, "apply", side_effect=AssertionError("Reconcile must never repeat Apply")):
            restarted.reconcile_transaction_document(tx)
        assert_projected(restarted, tx)
        self.assertEqual(restarted.transaction_status(tx)["tx_event_id"], terminal["tx_event_id"])
        self.assertFalse(marker.exists())
        resumed = restarted.checkout(["app.py"], purpose="Resume only after the exact pending event is projected")
        assert_projected(restarted, resumed["transaction_id"])
        restarted.abort(resumed["transaction_id"])
        self.assertEqual(restarted.config.config_path.read_bytes(), before_config)
        self.assertEqual((self.project / "app.py").read_bytes(), before_source)


    def test_runtime_policy_writer_cli_and_generic_dispatch_share_native_owner(self):
        from runtime_sync_workshop.cli import _parser, execute
        profile = "mandatory-runtime/v1"
        plan = self.engine.policy_activation_plan(profile)
        parser = _parser()
        prefix = ["--config", str(self.engine.config.config_path)]
        checkout = execute(parser.parse_args(prefix + ["policy-checkout", "--profile", profile,
                              "--expected-plan-sha256", plan["plan_sha256"], "--purpose",
                              "Review and abandon an exact policy candidate through the real public CLI"]))
        tx = checkout["transaction_id"]
        state = self.engine.transaction_status(tx)
        self.assertTrue(state["document_projection_required"])
        result = execute(parser.parse_args(prefix + ["policy-abort", tx]))
        self.assertEqual(result["state"], "ABORTED")
        checkout, _ = self._policy_candidate()
        tx = checkout["transaction_id"]
        self.assertEqual(self.engine.prepare(tx)["state"], "PREPARED")
        self.assertEqual(self.engine.verify(tx)["state"], "SHADOW_VERIFIED")
        self.assertEqual(self.engine.abort(tx)["state"], "ABORTED")
        for command in ("policy-prepare", "policy-verify", "policy-apply", "policy-recover"):
            self.assertEqual(parser.parse_args(prefix + [command, tx]).command, command)

    def test_runtime_policy_writer_failed_health_refuses_before_any_machine_write(self):
        import kairos.health as health
        checkout, _ = self._verified_policy()
        tx = checkout["transaction_id"]
        before = self.engine.config.config_path.read_bytes()
        with patch.object(health, "health_audit", return_value={"verdict": "FAIL", "failures": ["injected stale document"]}):
            with self.assertRaises(WorkshopError) as refusal:
                self.engine.policy_apply(tx)
        self.assertEqual(refusal.exception.code, "KAIROS_HEALTH_FAILED")
        self.assertEqual(self.engine.config.config_path.read_bytes(), before)
        state = self.engine.transaction_status(tx)
        self.assertEqual(state["state"], "SHADOW_VERIFIED")
        self.assertFalse(any(row["event"] == "POLICY_APPLY_STARTED" for row in state["journal"]))
        self.assertTrue(self.engine.lease_path.exists())
        self.engine.policy_abort(tx)

    def test_runtime_policy_writer_frozen_manifest_tamper_refuses_before_apply(self):
        checkout, _ = self._policy_candidate()
        tx = checkout["transaction_id"]
        root = Path(checkout["work_directory"]).parent
        manifest = root / "baseline" / "manifest.json"
        original = manifest.read_bytes()
        raw = json.loads(original)
        raw["counts"]["unexpected"] = 1
        manifest.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        before = self.engine.config.config_path.read_bytes()
        with self.assertRaises(WorkshopError) as refusal:
            self.engine.policy_prepare(tx)
        self.assertEqual(refusal.exception.code, "POLICY_BASELINE_CHANGED")
        self.assertEqual(self.engine.config.config_path.read_bytes(), before)
        manifest.write_bytes(original)
        self.engine.policy_abort(tx)

    def test_runtime_policy_writer_stale_plan_and_active_lease_refuse_before_mutation(self):
        before = self.engine.config.config_path.read_bytes()
        with self.assertRaises(WorkshopError) as refusal:
            self.engine.policy_checkout("mandatory-runtime/v1", expected_plan_sha256="0" * 64,
                                        purpose="Refuse an unreviewed policy plan before acquiring any lease")
        self.assertEqual(refusal.exception.code, "POLICY_PLAN_CHANGED")
        self.assertFalse(self.engine.lease_path.exists())
        checkout, plan = self._policy_candidate()
        with self.assertRaises(WorkshopError):
            self.engine.policy_checkout("mandatory-runtime/v1", expected_plan_sha256=plan["plan_sha256"],
                                        purpose="Do not steal the already owned global Workshop lease")
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "CHECKED_OUT")
        self.assertEqual(self.engine.config.config_path.read_bytes(), before)
        self.engine.policy_abort(checkout["transaction_id"])

    def test_runtime_policy_writer_refuses_incomplete_review_and_extra_candidate(self):
        plan = self.engine.policy_activation_plan("mandatory-runtime/v1")
        checkout = self.engine.policy_checkout("mandatory-runtime/v1", expected_plan_sha256=plan["plan_sha256"],
                                               purpose="Prove candidate scope and human-readable review are mandatory")
        tx = checkout["transaction_id"]
        with self.assertRaises(WorkshopError) as refusal:
            self.engine.policy_prepare(tx)
        self.assertEqual(refusal.exception.code, "POLICY_REVIEW_INCOMPLETE")
        extra = Path(checkout["work_directory"]) / "machine" / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(WorkshopError) as refusal:
            self.engine.policy_prepare(tx)
        self.assertEqual(refusal.exception.code, "POLICY_CANDIDATE_SCOPE_INVALID")
        extra.unlink()
        self.engine.policy_abort(tx)

    def test_runtime_policy_writer_review_tamper_after_verify_refuses_apply(self):
        checkout, _ = self._verified_policy()
        before = self.engine.config.config_path.read_bytes()
        review = Path(checkout["review"])
        record = json.loads(review.read_text(encoding="utf-8"))
        record["reason"] += " Changed after verification."
        review.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(WorkshopError) as refusal:
            self.engine.policy_apply(checkout["transaction_id"])
        self.assertEqual(refusal.exception.code, "WORK_CHANGED_AFTER_VERIFY")
        self.assertEqual(self.engine.config.config_path.read_bytes(), before)
        self.engine.policy_abort(checkout["transaction_id"])

    def test_runtime_policy_writer_write_failure_restores_exact_bytes_and_freshness(self):
        from runtime_sync_workshop import util
        checkout, _ = self._verified_policy()
        path = self.engine.config.config_path
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        original = util.atomic_write_bytes
        fired = []
        def fail_once(destination, data):
            if Path(destination) == path and not fired:
                fired.append(True)
                raise OSError("injected atomic config replacement failure")
            return original(destination, data)
        with patch.object(util, "atomic_write_bytes", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.engine.policy_apply(checkout["transaction_id"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "ROLLED_BACK")
        self.assertFalse(self.engine.lease_path.exists())
        self.assertTrue(self.engine.status(persist=False)["verified"])

    def test_runtime_policy_writer_process_interruption_at_baseline_recovers_without_apply(self):
        from runtime_sync_workshop import util
        checkout, _ = self._verified_policy()
        path = self.engine.config.config_path
        before = path.read_bytes()
        original = util.atomic_write_bytes
        class Interrupted(BaseException):
            pass
        def interrupt(destination, data):
            if Path(destination) == path:
                raise Interrupted()
            return original(destination, data)
        with patch.object(util, "atomic_write_bytes", side_effect=interrupt):
            with self.assertRaises(Interrupted):
                self.engine.policy_apply(checkout["transaction_id"])
        self.assertTrue(self.engine.lease_path.exists())
        fresh = WorkshopEngine(path)
        with patch.object(fresh, "policy_apply", side_effect=AssertionError("Recovery must never repeat Apply")):
            result = fresh.recover(checkout["transaction_id"])
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(fresh.lease_path.exists())

    def test_runtime_policy_writer_process_interruption_after_target_finishes_without_apply(self):
        from runtime_sync_workshop import util
        checkout, _ = self._verified_policy()
        path = self.engine.config.config_path
        original = util.atomic_write_bytes
        class Interrupted(BaseException):
            pass
        def interrupt_after(destination, data):
            original(destination, data)
            if Path(destination) == path:
                raise Interrupted()
        with patch.object(util, "atomic_write_bytes", side_effect=interrupt_after):
            with self.assertRaises(Interrupted):
                self.engine.policy_apply(checkout["transaction_id"])
        target = path.read_bytes()
        fresh = WorkshopEngine(path)
        with patch.object(fresh, "policy_apply", side_effect=AssertionError("Recovery must never repeat Apply")):
            result = fresh.policy_recover(checkout["transaction_id"])
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(path.read_bytes(), target)
        self.assertFalse(fresh.lease_path.exists())

    def test_runtime_policy_writer_heartbeat_failure_requires_fresh_forward_recovery(self):
        import kairos.heartbeat as heartbeat
        checkout, _ = self._verified_policy()
        original = heartbeat.run_heartbeat
        def interrupted(*args, **kwargs):
            if kwargs.get("trigger", "").startswith("runtime-policy-result:"):
                raise OSError("injected policy result heartbeat outage")
            return original(*args, **kwargs)
        with patch.object(heartbeat, "run_heartbeat", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.engine.policy_apply(checkout["transaction_id"])
        self.assertTrue(self.engine.lease_path.exists())
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "APPLYING")
        fresh = WorkshopEngine(self.engine.config.config_path)
        target = fresh.config.config_path.read_bytes()
        result = fresh.policy_recover(checkout["transaction_id"])
        self.assertTrue(result["recovered"])
        self.assertEqual(fresh.config.config_path.read_bytes(), target)
        self.assertFalse(fresh.lease_path.exists())

    def test_runtime_policy_writer_terminal_receipt_outage_recovers_exact_event(self):
        import runtime_sync_workshop.engine as owner
        from runtime_sync_workshop.tx_documents import assert_projected
        checkout, _ = self._verified_policy()
        tx = checkout["transaction_id"]
        original = owner.atomic_write_json
        def storage_outage(destination, record):
            if (Path(destination).parent == self.engine.config.state_directory / "receipts"
                    and record.get("schema") == "werkfaden-runtime-policy-postcheck/v1"):
                raise OSError("injected immutable policy receipt storage outage")
            return original(destination, record)
        with patch.object(owner, "atomic_write_json", side_effect=storage_outage):
            with self.assertRaises(WorkshopError) as refusal:
                self.engine.policy_apply(tx)
        self.assertEqual(refusal.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
        state = self.engine.transaction_status(tx)
        self.assertEqual(state["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(state["terminal_receipt"]["status"], "PENDING")
        technical_event = state["terminal_receipt"]["postcheck_event_id"]
        pending_event = state["tx_event_id"]
        self.assertNotEqual(pending_event, technical_event)
        fresh = WorkshopEngine(self.engine.config.config_path)
        with patch.object(fresh, "policy_apply", side_effect=AssertionError("Acknowledgement must never repeat Apply")):
            result = fresh.policy_recover(tx)
        immutable = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(immutable["terminal_event_id"], technical_event)
        self.assertEqual(immutable["terminal_payload_sha256"], state["terminal_receipt"]["payload_sha256"])
        final = fresh.transaction_status(tx)
        self.assertIn(pending_event, [event["event_id"] for event in final["tx_event_history"]])
        self.assertEqual(sum(row["event"] == "POLICY_APPLY_STARTED" for row in final["journal"]), 1)
        assert_projected(fresh, tx)
        event = final["tx_event_id"]
        fresh.policy_recover(tx)
        self.assertEqual(fresh.transaction_status(tx)["tx_event_id"], event)
        self.assertFalse(fresh.lease_path.exists())

    def test_runtime_policy_writer_refuses_unsupported_guardian_and_jev_scopes(self):
        path = self.engine.config.config_path
        original = dict(self.engine.config.raw)
        for field, code in (("guardian_required", "POLICY_GUARDIAN_SCOPE_UNSUPPORTED"),
                            ("jev_apply_required", "JEV_APPLY_ROUTE_UNSUPPORTED")):
            with self.subTest(policy=field):
                # Isolated configured refusal injection; never a live migration.
                path.write_text(json.dumps({**original, field: True}) + "\n", encoding="utf-8")
                fresh = WorkshopEngine(path)
                with self.assertRaises(WorkshopError) as refusal:
                    fresh.policy_checkout("mandatory-runtime/v1", expected_plan_sha256="0" * 64,
                                          purpose="Refuse a policy migration without its stronger scope authority")
                self.assertEqual(refusal.exception.code, code)
                self.assertFalse(fresh.lease_path.exists())

    def test_runtime_policy_plan_is_sealed_read_only_and_publicly_reachable(self):
        from runtime_sync_workshop.cli import _parser, execute
        from runtime_sync_workshop.corpus import RUNTIME_POLICY_PROFILE
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() if p.is_file() else None
                  for p in self.root.rglob('*')}
        first = self.engine.policy_activation_plan(RUNTIME_POLICY_PROFILE)
        second = self.engine.policy_activation_plan(RUNTIME_POLICY_PROFILE)
        self.assertEqual(first, second)
        self.assertTrue(first['verified'])
        self.assertTrue(first['read_only'])
        self.assertTrue(first['transaction_required'])
        self.assertFalse(first['mutation_authorized'])
        self.assertTrue(first['activation_supported'])
        self.assertEqual(first['baseline_package_sha256'], self.engine.status(persist=False)['package_sha256'])
        args = _parser().parse_args(['--config', str(self.engine.config.config_path), 'manifest',
                                    '--policy-profile', RUNTIME_POLICY_PROFILE])
        self.assertEqual(execute(args), first)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() if p.is_file() else None
                 for p in self.root.rglob('*')}
        self.assertEqual(before, after)
        self.assertFalse(self.engine.lease_path.exists())
        self.assertIsNot(self.engine.config.raw.get('tx_retention_required'), True)
        self.assertEqual({row['path'] for row in first['files']}, {'workshop.config.json'})

    def test_runtime_policy_plan_refuses_unsealed_or_drifted_source(self):
        from runtime_sync_workshop.corpus import RUNTIME_POLICY_PROFILE
        seal = self.engine.seal_path.read_bytes()
        self.engine.seal_path.unlink()  # Isolated fixture fault injection only.
        with self.assertRaises(WorkshopError) as caught:
            self.engine.policy_activation_plan(RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'SEAL_MISSING')
        self.engine.seal_path.write_bytes(seal)
        (self.project / 'app.py').write_bytes(b'value = 999\n')
        with self.assertRaises(WorkshopError) as caught:
            self.engine.policy_activation_plan(RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'GUARDIAN_BASELINE_UNVERIFIED')
        self.assertFalse(self.engine.lease_path.exists())

    def test_runtime_policy_plan_detects_seal_change_during_candidate_verification(self):
        import runtime_sync_workshop.corpus as corpus
        original = corpus.verify_runtime_policy_candidate
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.engine.seal_path.write_bytes(self.engine.seal_path.read_bytes() + b'\n')
            return result
        with patch.object(corpus, 'verify_runtime_policy_candidate', side_effect=changed):
            with self.assertRaises(WorkshopError) as caught:
                self.engine.policy_activation_plan(corpus.RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_BASELINE_CHANGED')
        self.assertFalse(self.engine.lease_path.exists())

    def test_runtime_policy_plan_detects_config_change_after_derivation(self):
        import runtime_sync_workshop.corpus as corpus
        original = corpus.runtime_policy_candidate
        once = []
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if not once:
                once.append(True)
                path = self.engine.config.config_path
                raw = json.loads(path.read_bytes()); raw['tx_recycle_trigger'] = 8
                path.write_text(json.dumps(raw), encoding='utf-8')
            return result
        with patch.object(corpus, 'runtime_policy_candidate', side_effect=changed):
            with self.assertRaises(WorkshopError) as caught:
                self.engine.policy_activation_plan(corpus.RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_BASELINE_CHANGED')
        self.assertFalse(self.engine.lease_path.exists())

    def metadata(self, artifact_id: str, *, capsule: str, boundary: str) -> dict:
        return {
            "schema": "kairos-context/v1",
            "id": artifact_id,
            "type": "documentation",
            "revision": 1,
            "state": "active",
            "authority": "architecture_authority",
            "workspace": "KAIROS_CONTEXT_TEST",
            "route": f"KAIROS_CONTEXT_TEST/{artifact_id}",
            "updated_at": "2026-09-10T00:00:00Z",
            "capsule": capsule,
            "claim_boundary": boundary,
            "entities": [artifact_id],
            "facets": ["application-behavior"],
            "criteria": [],
            "does_not_answer": ["deployment outcome"],
        }

    def behavior_document(self, *, updated: bool = False) -> str:
        term = "freshcounter" if updated else "legacycounter"
        value = 2 if updated else 1
        metadata = self.metadata(
            "APP_BEHAVIOR",
            capsule=f"The {term} application exposes value {value}.",
            boundary=f"This document describes {term} behavior at value {value}.",
        )
        metadata["answers"] = [{
            "intent": "behavior",
            "question": f"How does {term} behave?",
            "target": "s-behavior",
        }]
        metadata["refs"] = {"contract": pointer(
            REFERENCE,
            section="s-current" if updated else "s-original",
            artifact_id="APP_REFERENCE",
            version="1",
            relation="requires",
            tags=("application",),
        )}
        metadata["search_contract"] = [{
            "query": f"How does {term} behave?",
            "expected": "APP_BEHAVIOR#s-behavior",
            "required_top_k": 1,
        }]
        return render_document(metadata, "Application behavior", [{
            "id": "s-behavior",
            "title": "Behavior",
            "capsule": f"The {term} application exposes value {value}.",
            "content": f"The {term} application sets `value = {value}` in `app.py`.",
        }])

    def reference_document(self) -> str:
        metadata = self.metadata(
            "APP_REFERENCE",
            capsule="Application contracts distinguish original and current values.",
            boundary="This document records application contracts, not execution evidence.",
        )
        metadata["answers"] = [{
            "intent": "contract",
            "question": "Which value does the current application contract require?",
            "target": "s-current",
        }]
        metadata["refs"] = {}
        metadata["search_contract"] = [{
            "query": "Which value does the current application contract require?",
            "expected": "APP_REFERENCE#s-current",
            "required_top_k": 1,
        }]
        return render_document(metadata, "Application contracts", [
            {"id": "s-original", "title": "Original contract", "capsule": "The original value is one.",
             "content": "The original contract requires value one."},
            {"id": "s-current", "title": "Current contract", "capsule": "The current value is two.",
             "content": "The current contract requires value two."},
        ])

    def checkout(self, documents: tuple[str, ...] = (BEHAVIOR,)) -> dict:
        result = self.engine.checkout(
            ["app.py"], documents=documents,
            purpose="Update application value together with its retrieval context",
        )
        work = Path(result["work_directory"])
        (work / "app.py").write_bytes(b"value = 2\n")
        path = Path(result["metadata_review"])
        review = json.loads(path.read_text(encoding="utf-8"))
        for entry in review["entries"]:
            entry.update(
                metadata_impact="none",
                reason="The generated source blueprint claims exact bytes only; semantic behavior is reviewed in the context document.",
                reviewer="context-integration-test", reviewed_at="2026-10-22T00:00:00Z",
            )
        path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        return result

    def review_document(self, checkout: dict, document: str, *, impact: str = "updated",
                        fields: list[str] | None = None) -> None:
        path = Path(checkout["document_review"])
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(review["schema"], "runtime-sync-context-review/v1")
        selected = [entry for entry in review["entries"] if entry["document"] == document]
        self.assertEqual(len(selected), 1)
        selected[0].update(
            metadata_impact=impact,
            reason="Reviewed behavior, answer targets, references, claim boundary and sections against the application change.",
            reviewer="context-integration-test", reviewed_at="2026-10-22T00:00:00Z",
            reviewed_fields=list(REVIEW_FIELDS if fields is None else fields),
        )
        path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")

    def stage_behavior(self, checkout: dict) -> Path:
        work_doc = Path(checkout["work_directory"]) / "documents" / BEHAVIOR
        work_doc.write_bytes(self.behavior_document(updated=True).encode("utf-8"))
        self.review_document(checkout, BEHAVIOR)
        return work_doc

    def prepare_and_verify(self, checkout: dict) -> None:
        transaction_id = checkout["transaction_id"]
        complete_metadata_audit(self.engine, checkout)
        self.assertEqual(self.engine.prepare(transaction_id)["state"], "PREPARED")
        self.engine.metadata_check(transaction_id)
        self.assertEqual(self.engine.verify(transaction_id)["state"], "SHADOW_VERIFIED")

    def test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        original = (self.workspace / BEHAVIOR).read_bytes()
        self.prepare_and_verify(checkout)
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), original)
        receipt = self.engine.apply(checkout["transaction_id"])
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(receipt["bit_exact"])
        self.assertIn("APP_BEHAVIOR", receipt["heartbeat"]["promoted"])
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        metadata = split_frontmatter(work_doc.read_text(encoding="utf-8")).metadata
        self.assertEqual(metadata["revision"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT revision,capsule,claim_boundary FROM artifacts WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchone()
            self.assertEqual(row, (2, metadata["capsule"], metadata["claim_boundary"]))
            self.assertEqual(connection.execute(
                "SELECT section_id,question FROM query_handles WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchall(), [("s-behavior", "How does freshcounter behave?")])
            self.assertEqual(connection.execute(
                "SELECT target_path,target_section_id,version FROM relations WHERE source_artifact_id='APP_BEHAVIOR'",
            ).fetchall(), [(REFERENCE, "s-current", "1")])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM section_fts WHERE section_fts MATCH 'legacycounter' AND artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM section_fts WHERE section_fts MATCH 'freshcounter' AND artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifact_revisions WHERE artifact_id='APP_BEHAVIOR' ORDER BY revision",
            ).fetchall(), [(1,), (2,)])
        found = search_database(KnowledgeDatabase(self.database), "How does freshcounter behave?")
        self.assertEqual(found["primary"]["artifact_id"], "APP_BEHAVIOR")
        self.assertEqual(found["primary"]["section_id"], "s-behavior")
        self.assertFalse(self.engine.lease_path.exists())

    def test_every_selected_context_document_requires_complete_consistent_review(self) -> None:
        checkout = self.checkout((BEHAVIOR, REFERENCE))
        self.stage_behavior(checkout)
        self.review_document(checkout, BEHAVIOR, fields=[field for field in REVIEW_FIELDS if field != "refs"])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, BEHAVIOR)
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, REFERENCE, impact="none")
        self.review_document(checkout, BEHAVIOR, impact="none")
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(checkout, BEHAVIOR)
        complete_metadata_audit(self.engine, checkout)
        self.assertEqual(self.engine.prepare(checkout["transaction_id"])["state"], "PREPARED")
        self.assertEqual(split_frontmatter(
            (Path(checkout["work_directory"]) / "documents" / REFERENCE).read_text(encoding="utf-8"),
        ).metadata["revision"], 1)
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_document_edit_after_prepare_requires_new_preparation(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        complete_metadata_audit(self.engine, checkout)
        self.engine.prepare(checkout["transaction_id"])
        self.engine.metadata_check(checkout["transaction_id"])
        work_doc.write_bytes(work_doc.read_bytes().replace(b"value = 2", b"value = 3"))
        with self.assertRaises(WorkshopError) as raised:
            self.engine.verify(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "WORK_CHANGED_AFTER_PREPARE")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_document_and_review_edits_after_verify_cannot_be_applied(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        reviewed_bytes = work_doc.read_bytes()
        review_path = Path(checkout["document_review"])
        original_review = review_path.read_bytes()
        source_review_path = Path(checkout["metadata_review"])
        original_source_review = source_review_path.read_bytes()
        for changed_path, changed_bytes in (
            (work_doc, reviewed_bytes.replace(b"value = 2", b"value = 3")),
            (review_path, original_review.replace(b"context-integration-test", b"other-reviewer")),
            (source_review_path, original_source_review.replace(b"context-integration-test", b"other-reviewer")),
        ):
            with self.subTest(path=changed_path.name):
                changed_path.write_bytes(changed_bytes)
                with self.assertRaises(WorkshopError) as raised:
                    self.engine.apply(checkout["transaction_id"])
                self.assertEqual(raised.exception.code, "WORK_CHANGED_AFTER_VERIFY")
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
                work_doc.write_bytes(reviewed_bytes)
                review_path.write_bytes(original_review)
                source_review_path.write_bytes(original_source_review)

    def test_live_document_drift_is_detected_before_apply(self) -> None:
        checkout = self.checkout()
        self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        live_doc = self.workspace / BEHAVIOR
        original = live_doc.read_bytes()
        # Deliberately simulate an out-of-band writer; the transaction must refuse it.
        live_doc.write_bytes(original.replace(b"value = 1", b"value = 9"))
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_BASELINE_CHANGED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertNotEqual(live_doc.read_bytes(), original)

    def test_including_a_document_preserves_edits_and_invalidates_verification(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        reviewed_bytes = work_doc.read_bytes()
        self.assertIn("metadata_check", self.engine.transaction_status(checkout["transaction_id"]))
        expanded = self.engine.include_documents(
            checkout["transaction_id"], [REFERENCE],
            reason="The application contract also needs a scoped impact review",
        )
        self.assertEqual(set(expanded["documents"]), {BEHAVIOR, REFERENCE})
        self.assertEqual(work_doc.read_bytes(), reviewed_bytes)
        state = self.engine.transaction_status(checkout["transaction_id"])
        self.assertEqual(state["state"], "CHECKED_OUT")
        self.assertNotIn("metadata_check", state)
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "TRANSACTION_STATE_INVALID")
        baseline = Path(expanded["work_directory"]).parent / "baseline" / "documents" / REFERENCE
        self.assertEqual(baseline.read_bytes(), (self.workspace / REFERENCE).read_bytes())
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        self.review_document(expanded, REFERENCE, impact="none")
        self.prepare_and_verify(checkout)
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(split_frontmatter((self.workspace / BEHAVIOR).read_text(encoding="utf-8")).metadata["revision"], 2)

    def test_invalid_answer_target_is_rejected_in_shadow(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        work_doc.write_bytes(work_doc.read_bytes().replace(b'target = "s-behavior"', b'target = "s-missing"'))
        complete_metadata_audit(self.engine, checkout)
        self.engine.prepare(checkout["transaction_id"])
        self.engine.metadata_check(checkout["transaction_id"])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.verify(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "SHADOW_PROMOTION_FAILED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertIn("legacycounter", (self.workspace / BEHAVIOR).read_text(encoding="utf-8"))

    def test_context_scope_rejects_escape_generated_and_unmapped_documents(self) -> None:
        blueprint = next((self.workspace / "code").glob("*.md")).relative_to(self.workspace).as_posix()
        for document in ("../outside.md", str(self.root / "outside.md"), "NEURAL_CORTEX.md", blueprint, "docs/MISSING.md"):
            with self.subTest(document=document):
                with self.assertRaises(WorkshopError) as raised:
                    self.engine.checkout(["app.py"], documents=[document], purpose="Reject an invalid context scope before writing")
                self.assertEqual(raised.exception.code, "CONTEXT_DOCUMENT_INVALID")
                self.assertFalse(self.engine.lease_path.exists())

    def test_failed_heartbeat_retains_complete_package_for_recovery(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        with patch("kairos.heartbeat.run_heartbeat", side_effect=RuntimeError("injected heartbeat interruption")):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
        self.assertTrue(self.engine.lease_path.exists())
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "RECOVERY_REQUIRED")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
        baseline = json.loads((Path(checkout["work_directory"]).parent / "baseline" / "manifest.json").read_text(encoding="utf-8"))
        blueprint = baseline["records"]["app.py"]["blueprint"]["filename"]
        receipt = run_heartbeat(
            self.workspace, requested_mode="verify", changed_paths=[BEHAVIOR, f"code/{blueprint}"],
            use_reconciliation=True, trigger="context-transaction-test-recovery",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.engine.recover(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertFalse(self.engine.lease_path.exists())

    def test_context_copy_failure_restores_code_blueprints_and_markdown(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        live_doc = self.workspace / BEHAVIOR
        original_paths = [self.project / "app.py", live_doc, *(self.workspace / "code").glob("*.md")]
        originals = {path: path.read_bytes() for path in original_paths}
        interrupted = False

        def interrupted_copy(source: Path, destination: Path) -> None:
            nonlocal interrupted
            copy_exact(source, destination)
            if source == work_doc and destination == live_doc and not interrupted:
                interrupted = True
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")
                self.assertEqual(live_doc.read_bytes(), work_doc.read_bytes())
                raise OSError("injected interruption after context copy and before heartbeat")

        # The public guarded Engine now wraps the synchronization core; inject into
        # the actual core copy boundary, not the public admission wrapper.
        core_apply = super(type(self.engine), self.engine).apply
        with patch.dict(core_apply.__func__.__globals__, {"copy_exact": interrupted_copy}):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertTrue(interrupted)
        self.assertEqual(raised.exception.code, "APPLY_ROLLED_BACK")
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "ROLLED_BACK")
        self.assertFalse(self.engine.lease_path.exists())
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original, str(path))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifacts WHERE artifact_id='APP_BEHAVIOR'",
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM action_permits WHERE command_name='external-edit' AND status='ACTIVE'",
            ).fetchone()[0], 0)
        self.assertTrue(self.engine.status(persist=False)["verified"])

    def test_repreparing_verified_context_keeps_one_revision_and_renews_verification(self) -> None:
        checkout = self.checkout()
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        first_package = self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"]
        work_doc.write_bytes(work_doc.read_bytes().replace(b"freshcounter", b"revisedcounter"))
        self.review_document(checkout, BEHAVIOR, fields=[])
        with self.assertRaises(WorkshopError) as raised:
            self.engine.prepare(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "CONTEXT_REVIEW_REQUIRED")
        # A failed attempt to prepare the new candidate must not retain the old
        # SHADOW_VERIFIED authorization.
        with self.assertRaises(WorkshopError) as raised:
            self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "TRANSACTION_STATE_INVALID")
        self.review_document(checkout, BEHAVIOR)
        self.prepare_and_verify(checkout)
        second_package = self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"]
        self.assertNotEqual(first_package, second_package)
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        self.assertIn("revisedcounter", work_doc.read_text(encoding="utf-8"))
        self.assertEqual(split_frontmatter(work_doc.read_text(encoding="utf-8")).metadata["revision"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT revision FROM artifact_revisions WHERE artifact_id='APP_BEHAVIOR' ORDER BY revision",
            ).fetchall(), [(1,), (2,)])

    def test_context_only_apply_preserves_prior_mirror_and_binds_new_package(self) -> None:
        prior_seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        prior_mirror_path = Path(prior_seal["mirror_manifest"])
        prior_mirror_bytes = prior_mirror_path.read_bytes()
        checkout = self.checkout()
        (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 1\n")
        work_doc = self.stage_behavior(checkout)
        prepared = self.engine.prepare(checkout["transaction_id"])
        self.assertFalse(prepared["metadata_aftercheck"]["required"])
        audit = Path(checkout["work_directory"]).parent / "review" / "metadata_audit.json"
        self.assertFalse(audit.exists())
        self.assertEqual(self.engine.verify(checkout["transaction_id"])["state"], "SHADOW_VERIFIED")
        self.assertNotIn("metadata_check", self.engine.transaction_status(checkout["transaction_id"]))
        self.assertEqual(self.engine.apply(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        mirror_path = Path(seal["mirror_manifest"])
        mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
        self.assertEqual(prior_seal["package_sha256"], seal["package_sha256"])
        self.assertEqual(prior_mirror_path.read_bytes(), prior_mirror_bytes)
        self.assertNotEqual(mirror_path, prior_mirror_path)
        self.assertEqual(mirror_path.name, f"{seal['mirror_sha256']}.json")
        self.assertEqual(mirror["mirror_sha256"], seal["mirror_sha256"])
        self.assertEqual(mirror["objects"][f"context:{BEHAVIOR}"], hashlib.sha256(work_doc.read_bytes()).hexdigest())
        self.assertEqual(seal["work_package_sha256"], self.engine.transaction_status(checkout["transaction_id"])["verified_work_package_sha256"])

    def test_context_only_recovery_does_not_mistake_unchanged_code_for_rollback(self) -> None:
        checkout = self.checkout()
        (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 1\n")
        work_doc = self.stage_behavior(checkout)
        self.prepare_and_verify(checkout)
        with patch("kairos.heartbeat.run_heartbeat", side_effect=RuntimeError("injected context promotion interruption")):
            with self.assertRaises(WorkshopError) as raised:
                self.engine.apply(checkout["transaction_id"])
        self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.workspace / BEHAVIOR).read_bytes(), work_doc.read_bytes())
        # The original code corpus hash is unchanged, but the context source is
        # already the candidate and its projection is stale. This is not a rollback.
        with self.assertRaises(WorkshopError):
            self.engine.recover(checkout["transaction_id"])
        self.assertTrue(self.engine.lease_path.exists())
        self.assertEqual(self.engine.transaction_status(checkout["transaction_id"])["state"], "RECOVERY_REQUIRED")
        receipt = run_heartbeat(
            self.workspace, requested_mode="verify", changed_paths=[BEHAVIOR],
            use_reconciliation=True, trigger="context-only-transaction-test-recovery",
        )
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.engine.recover(checkout["transaction_id"])["state"], "POSTCHECK_VERIFIED")
        self.assertFalse(self.engine.lease_path.exists())


if __name__ == "__main__":
    unittest.main()
