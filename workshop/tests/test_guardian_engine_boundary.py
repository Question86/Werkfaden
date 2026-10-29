from __future__ import annotations

import json
import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = PACKAGE_ROOT / "workshop" / "src"
for value in (PACKAGE_ROOT, WORKSHOP_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.binding import initialize_project  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


def _spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_GUARD_BOUNDARY",
            "title": "Guardian boundary",
            "objective": "Verify that mutation authority cannot bypass the deterministic Guardian.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_GUARD_BOUNDARY_01",
                "title": "Guarded mutation",
                "objective": "Bind one source and prove the engine-level mutation gate.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_GUARD_BOUNDARY_001",
                    "description": "All Workshop mutation commands remain Guardian-bound.",
                    "state": "active",
                    "evidence_required": "Guardian refusal and transaction ownership evidence.",
                    "required_artifact_types": ["code"],
                }],
            }],
        },
        "task": {
            "id": "TASK_GUARD_BOUNDARY_001",
            "title": "Exercise Guardian boundary",
            "objective": "Create one synchronized baseline and test direct engine mutation paths.",
            "milestone": "MILESTONE_GUARD_BOUNDARY_01",
            "criteria": ["CRIT_GUARD_BOUNDARY_001"],
        },
    }


def _compile_commands(project: Path, source: Path) -> Path:
    build = project / "build"
    build.mkdir()
    path = build / "compile_commands.json"
    path.write_text(json.dumps([{
        "directory": str(build),
        "file": str(source),
        "arguments": ["c++", "-c", str(source)],
    }]), encoding="utf-8")
    return path


def _memory_contract(root: Path) -> tuple[Path, Path]:
    memory = root / "MEMORY.md"
    codes = [
        "MEM.HUMAN", "MEM.HYP", "MEM.F1", "MEM.F2", "MEM.MAP", "MEM.COUNTER",
        "MEM.SOURCE", "MEM.PROVE", "MEM.PATCH", "MEM.WORKSHOP", "MEM.HEARTBEAT", "MEM.FRESH",
    ]
    memory.write_text("\n\n".join(f"# {code}\n\n{code}\nRule for {code}." for code in codes) + "\n", encoding="utf-8")
    requirements = root / "memory-requirements.json"
    mapping = dict(zip(
        ["HUMAN_PROBLEM", "HYPOTHESIS", "FALSIFIER_1", "FALSIFIER_2", "MAP", "COUNTERPROBE", "EXACT_SOURCE", "PROVE", "PATCH", "WORKSHOP", "HEARTBEAT", "FRESH_RUN"],
        ([code] for code in codes),
    ))
    requirements.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    return memory, requirements


class GuardianEngineBoundaryTests(unittest.TestCase):
    def _guarded_transaction_fixture(self, *, scope_contract=None, extra_mapped_paths=(), fixture_factory=None):
        import io
        from contextlib import redirect_stdout
        from kairos import governance
        from kairos.cli import main, _write_new_artifact
        from kairos.frontmatter import split_frontmatter
        from kairos.heartbeat import run_heartbeat
        from kairos.templates import research_document
        from runtime_sync_workshop.corpus import build_corpus_manifest
        from workshop.tests.test_tx_documents_integration import TransactionDocumentIntegrationTests

        integration = TransactionDocumentIntegrationTests()
        self.addCleanup(integration.doCleanups)
        original_seal = WorkshopEngine.seal

        def initial_graph_then_seal(engine, *args, **kwargs):
            self.assertFalse(engine.seal_path.exists())
            current = build_corpus_manifest(engine.config)
            self.assertTrue(current["verified"], current["issues"])
            source = next(path for path, row in current["records"].items() if row.get("source"))
            record = current["records"][source]
            owner = split_frontmatter(
                (engine.config.kairos_workspace / record["database"]["path"]).read_text(encoding="utf-8")
            ).metadata
            text = research_document(
                research_id="RESEARCH_NATIVE_GUARDIAN_GRAPH", task_id=owner["task"],
                title="Native source mapping graph fixture",
                question="Which exact native source mapping is declared by this fixture?",
                source_uri=record["database"]["path"] + "#s-mapping", source_kind="workspace",
                source_fact="The verified native manifest maps source " + source + " to implementation artifact "
                    + record["blueprint"]["artifact_id"] + ". Only this exact mapping is declared.",
                interpretation="Initial fixture graph authority; no runtime reachability inference.",
                workspace_id=owner["workspace"], task_path="tasks/task_" + owner["task"] + ".md",
                goal_id=owner["goal"], milestone_id=owner["milestone"],
                criteria=owner.get("criteria", []), source_sha256=hashlib.sha256(
                    (engine.config.kairos_workspace / record["database"]["path"]).read_bytes()).hexdigest(),
                loop=owner["loop"],
            )
            parts = text.split("+++", 2)
            self.assertEqual(len(parts), 3)
            source_ref = ("[ref:" + record["database"]["path"] + "#s-mapping|id:"
                          + owner["id"] + "|v:" + str(owner["revision"])
                          + "|rel:informs|tags:source,initial-snapshot|src:declared]")
            self.assertIn("[refs]\n", parts[1])
            parts[1] = parts[1].replace("[refs]\n", "[refs]\nsource_owner = "
                                        + json.dumps(source_ref) + "\n", 1)
            relation = "\n[[relations]]\nsubject = " + json.dumps(record["blueprint"]["artifact_id"])
            relation += '\npredicate = "tracks"\nobject = ' + json.dumps(source)
            relation += '\nobject_kind = "source"\nscope = "native-source-map"\nevidence_target = "s-fact"\nevidence = "direct"\n'
            text = "+++".join([parts[0], parts[1] + relation, parts[2]])
            relative = "research/RESEARCH_NATIVE_GUARDIAN_GRAPH.md"
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            grant = governance.issue_external_edit_permit(
                engine.config.kairos_workspace, paths=[relative],
                reason="Initial pre-seal native manifest-backed graph source; no synthetic receipt insertion.",
                publication_id="INITIAL_NATIVE_GRAPH:" + digest, expected_sha256={relative: digest},
            )
            _write_new_artifact(engine.config.kairos_workspace, engine.config.kairos_workspace / relative, text)
            heartbeat = run_heartbeat(
                engine.config.kairos_workspace, requested_mode="verify", changed_paths=[relative],
                use_reconciliation=True, trigger="initial-native-guardian-graph-fixture",
                resume_publication_permit_id=grant["permit_id"],
            )
            self.assertTrue(heartbeat["verified"], heartbeat)
            return original_seal(engine, *args, **kwargs)

        with patch.object(WorkshopEngine, "seal", new=initial_graph_then_seal):
            fixture = (fixture_factory or integration.fixture)()
        engine = fixture.engine
        current = build_corpus_manifest(engine.config)
        self.assertTrue(current["verified"], current["issues"])
        source = next(path for path, row in current["records"].items() if row.get("source"))
        record = current["records"][source]
        package = current["package_sha256"]
        temporary = tempfile.TemporaryDirectory(prefix="native-guardian-proof-")
        self.addCleanup(temporary.cleanup)
        memory, requirements = _memory_contract(Path(temporary.name))
        self.enterContext(patch.dict(os.environ, {
            "WERKFADEN_GUARDIAN_REQUIRED": "1",
            "WERKFADEN_GUARDIAN_MEMORY_FILE": str(memory),
            "WERKFADEN_GUARDIAN_MEMORY_REQUIREMENTS_FILE": str(requirements),
        }, clear=False))
        guardian = engine.guardian()
        self.assertEqual(guardian.kairos_database.resolve(), engine.config.kairos_database.resolve())
        self.assertTrue(engine._guardian_enabled())

        def command(arguments, *, negative=False):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(arguments)
            result = json.loads(output.getvalue())
            expected = 1 if negative else 0
            self.assertEqual(status, expected, result)
            if negative:
                self.assertEqual(result["routing_receipt"]["status"], "NO_MATCH")
            return result

        def enter(step, selector):
            return guardian.enter_state(
                guardian_id, step=step, memory_selectors=[selector], package_sha256=package,
            )["state_ticket_id"]

        guardian_id = guardian.start(
            problem="Verify exact native source evidence and transaction identity in one project database.",
            memory_file=memory, memory_selectors=["MEM.HUMAN"], package_sha256=package,
        )["guardian_id"]
        ticket = enter("HYPOTHESIS", "MEM.HYP")
        guardian.advance(
            guardian_id, step="HYPOTHESIS",
            summary=json.dumps({
                "observation": "Native source/Guardian evidence integration requires exact proof.",
                "invariant": "State, Guardian and source/document/SQL identities must agree.",
                "suspected_mechanism": "Source receipt origin or transaction observer boundary.",
                "expected_owner": "Native source admission and Guardian observer.",
                "refutation": "A native same-database trace establishes the correct identity.",
            }), state_ticket_id=ticket, evidence_refs=[], package_sha256=package,
        )
        task = guardian._load(guardian_id)["project_scope"]["task_id"]
        blueprint = record["blueprint"]["artifact_id"]
        stages = [
            ("FALSIFIER_1", "MEM.F1", "Which acceptance criteria and evidence does " + task + " require?"),
            ("FALSIFIER_2", "MEM.F2", "Which declared source is tracked by " + blueprint + "?"),
            ("MAP", "MEM.MAP", "Which declared native source mapping constrains " + blueprint + "?"),
            ("COUNTERPROBE", "MEM.COUNTER", "zzqvnmplkrtwxyzfghjbcxsdwlkq"),
        ]
        for step, selector, question in stages:
            ticket = enter(step, selector)
            result = command(
                ["search", question, "--workspace", str(engine.config.kairos_workspace), "--limit", "2"],
                negative=step == "COUNTERPROBE",
            )
            guardian.advance(
                guardian_id, step=step, summary="Bounded native routing evidence, not a universal absence claim.",
                state_ticket_id=ticket, evidence_refs=[result["routing_receipt"]["routing_receipt_id"]],
                package_sha256=package, sources=[source, *extra_mapped_paths] if step == "MAP" else [],
            )
        ticket = enter("EXACT_SOURCE", "MEM.SOURCE")
        query = command(["search", "Which exact source bytes are bound to " + source + "?",
                         "--workspace", str(engine.config.kairos_workspace), "--limit", "2"])
        permit = command([
            "source-permit", "--workspace", str(engine.config.kairos_workspace),
            "--routing-receipt", query["routing_receipt"]["routing_receipt_id"],
            "--purpose", "implementation_verification", "--path", record["database"]["path"],
            "--pattern", "^C:", "--reason",
            "Inspect exact native manifest-owned source ledger under the active Guardian gate.",
        ])
        inspection = command(["source-search", "--workspace", str(engine.config.kairos_workspace),
                              "--permit", permit["permit_id"]])
        guardian.advance(
            guardian_id, step="EXACT_SOURCE", summary="Native positive stable exact-source line evidence.",
            state_ticket_id=ticket, evidence_refs=[inspection["inspection_id"]], package_sha256=package,
        )
        ticket = enter("PROVE", "MEM.PROVE")
        guardian.advance(
            guardian_id, step="PROVE",
            summary=json.dumps({
                "observation": "Actual native evidence binds the declared source owner.",
                "violated_invariant": "Canonical origin and transaction identity must be preserved.",
                "causal_owner": "The inspected native source and observer boundary.",
                "scope_reason": "Only the current manifest-owned inspected source is selected.",
                "general_fix_reason": "Correct the shared native boundary with explicit provenance.",
            }), state_ticket_id=ticket, evidence_refs=[], package_sha256=package, sources=[source],
            scope_contract=scope_contract,
        )
        ticket = enter("PATCH", "MEM.PATCH")
        return fixture, engine, guardian, guardian_id, ticket


    def _assert_native_guardian_projection(self, engine, guardian, guardian_id, tx, expected):
        from runtime_sync_workshop.tx_documents import assert_projected
        assert_projected(engine, tx)
        _, state = engine._load_transaction(tx)
        self.assertEqual(state["state"], expected)
        payload = guardian._load(guardian_id)
        record = next(row for row in payload["patch_session"]["transactions"]
                      if row["transaction_id"] == tx)
        self.assertEqual(record["state"], expected)
        self.assertEqual(record["tx_event_id"], state["tx_event_id"])
        self.assertEqual(record["tx_event_sequence"], state["tx_event_sequence"])
        links = [row["details"] for row in payload["events"] if row.get("kind") == "TX_STATE"
                 and row["details"].get("transaction_id") == tx]
        self.assertEqual(sum(row.get("tx_event_id") == state["tx_event_id"] for row in links), 1)
        return state

    def _native_guardian_source_set(self):
        from runtime_sync_workshop.guardian_common import PATCH_SCOPE_SCHEMA
        from runtime_sync_workshop.universal_migration import UniversalSourceSetMigration
        operations = [{"op": "add", "path": "added.py"}]
        fixture, engine, guardian, gid, ticket = self._guarded_transaction_fixture(
            extra_mapped_paths=["added.py"],
            scope_contract={"schema": PATCH_SCOPE_SCHEMA, "normal": {"sources": ["app.py"]},
                            "source_set": {"operations": operations}},
        )
        owner = UniversalSourceSetMigration(engine)
        with engine.guardian_transaction(guardian_id=gid, kind="source_set",
                                         request={"operations": operations}, state_ticket_id=ticket):
            created = owner.checkout(purpose="Native same-database Guardian source-set acceptance")
        tx = created["transaction_id"]
        self._assert_native_guardian_projection(engine, guardian, gid, tx, "CHECKED_OUT")
        return fixture, engine, guardian, gid, owner, created

    def test_native_guardian_source_set_apply_preserves_every_event_identity(self):
        fixture, engine, guardian, gid, owner, created = self._native_guardian_source_set()
        tx = created["transaction_id"]
        (Path(created["candidate_root"]) / "added.py").write_text("answer = 42\n", encoding="utf-8")
        _, previous = engine._load_transaction(tx)
        for operation, expected in ((owner.prepare, "PREPARED"), (owner.verify, "SHADOW_VERIFIED"),
                                    (owner.apply, "POSTCHECK_VERIFIED")):
            operation(tx)
            current = self._assert_native_guardian_projection(engine, guardian, gid, tx, expected)
            self.assertGreater(current["tx_event_sequence"], previous["tx_event_sequence"])
            self.assertIn(previous["tx_event_id"], [row["previous_event_id"]
                          for row in current["tx_event_history"]] + [current["tx_event"]["previous_event_id"]])
            previous = current
        self.assertEqual((fixture.project / "added.py").read_text(encoding="utf-8"), "answer = 42\n")
        self.assertFalse(engine.lease_path.exists())
        self.assertIsNone(guardian._load(gid)["patch_session"]["active_transaction"])

    def test_native_guardian_source_set_abort_at_each_safe_state_with_pending_identity(self):
        from runtime_sync_workshop.tx_documents import pending_path, assert_no_pending
        for target in ("CHECKED_OUT", "PREPARED", "SHADOW_VERIFIED"):
            for pending in (False, True):
                with self.subTest(state=target, pending=pending):
                    fixture, engine, guardian, gid, owner, created = self._native_guardian_source_set()
                    tx = created["transaction_id"]
                    baseline = engine._verified_package()
                    if target != "CHECKED_OUT":
                        (Path(created["candidate_root"]) / "added.py").write_text("answer = 42\n", encoding="utf-8")
                        owner.prepare(tx)
                    if target == "SHADOW_VERIFIED":
                        owner.verify(tx)
                    before = self._assert_native_guardian_projection(engine, guardian, gid, tx, target)
                    if pending:
                        with patch("runtime_sync_workshop.tx_documents.publish",
                                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Isolated pending special-kind publication")):
                            with self.assertRaises(WorkshopError):
                                engine.annotate_transaction(tx, kind="observation",
                                    summary="Native Guardian safe abort with exact pending identity",
                                    evidence="isolated source-set proof/abort fixture")
                        _, before = engine._load_transaction(tx)
                        marker = json.loads(pending_path(engine, tx).read_text(encoding="utf-8"))
                        self.assertEqual(marker["event_id"], before["tx_event_id"])
                        with self.assertRaises(WorkshopError) as caught:
                            owner.checkout(purpose="Pending native special event must block another checkout")
                        # This public special owner refuses the active lease first.
                        self.assertEqual(caught.exception.code, "LEASE_ACTIVE")
                        with self.assertRaises(WorkshopError) as pending_error:
                            assert_no_pending(engine)
                        self.assertEqual(pending_error.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                        self.assertEqual(pending_error.exception.references["items"][0]["event_id"], marker["event_id"])
                    with patch.object(owner, "apply", side_effect=AssertionError("Abort must never repeat Apply")):
                        owner.abort(tx)
                    after = self._assert_native_guardian_projection(engine, guardian, gid, tx, "ABORTED")
                    self.assertEqual(after["tx_event"]["previous_event_id"], before["tx_event_id"])
                    self.assertFalse(pending_path(engine, tx).exists())
                    self.assertFalse(engine.lease_path.exists())
                    self.assertEqual(engine._verified_package(), baseline)
                    self.assertFalse((fixture.project / "added.py").exists())

    def test_native_guardian_source_set_scope_refusal_still_allows_safe_abort(self):
        from runtime_sync_workshop.tx_documents import pending_path
        fixture, engine, guardian, gid, owner, created = self._native_guardian_source_set()
        tx = created["transaction_id"]
        baseline = engine._verified_package()
        _, admitted = engine._load_transaction(tx)
        (Path(created["candidate_root"]) / "outside.py").write_text("unproven = True\n", encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            owner.prepare(tx)
        self.assertEqual(caught.exception.code, "GUARDIAN_SCOPE_MISMATCH")
        refused = self._assert_native_guardian_projection(engine, guardian, gid, tx, "CHECKED_OUT")
        self.assertEqual(refused["tx_event_id"], admitted["tx_event_id"])
        self.assertEqual(refused["tx_event_sequence"], admitted["tx_event_sequence"])
        self.assertFalse(pending_path(engine, tx).exists())
        from runtime_sync_workshop.tx_lifecycle import event_intents
        self.assertEqual(event_intents(engine, tx), [])
        self.assertFalse((fixture.project / "outside.py").exists())
        with patch.object(owner, "apply", side_effect=AssertionError("Refused candidate must not be applied")):
            owner.abort(tx)
        self._assert_native_guardian_projection(engine, guardian, gid, tx, "ABORTED")
        self.assertFalse(pending_path(engine, tx).exists())
        self.assertFalse(engine.lease_path.exists())
        self.assertEqual(engine._verified_package(), baseline)


    def _native_guardian_authority(self):
        import shutil
        from types import SimpleNamespace
        from workshop.tests.test_authority_migration import write_compile_commands, project_spec
        from runtime_sync_workshop.guardian_common import PATCH_SCOPE_SCHEMA
        from runtime_sync_workshop.migration import AuthorityMigration

        def factory():
            root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="native-guardian-authority-")))
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            source = project / "src/main.cpp"
            source.write_text("#include <a.hpp>\nint main(){return a();}\n", encoding="utf-8")
            (project / "include/a.hpp").write_text("#pragma once\ninline int a(){return 1;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace,
                               compile_commands=write_compile_commands(project, [source]),
                               project_spec=project_spec())
            engine = WorkshopEngine(workspace / ".kairos/workshop.config.json")
            engine.seal()
            return SimpleNamespace(root=root, project=project, workspace=workspace, engine=engine)

        request = {"add_headers": ["include/c.hpp"], "remove_headers": ["include/a.hpp"]}
        fixture, engine, guardian, gid, ticket = self._guarded_transaction_fixture(
            fixture_factory=factory,
            extra_mapped_paths=["include/a.hpp", "include/c.hpp"],
            scope_contract={"schema": PATCH_SCOPE_SCHEMA, "normal": {"sources": ["src/main.cpp"]},
                            "authority": request},
        )
        candidate = fixture.root / "candidate"
        shutil.copytree(fixture.project, candidate)
        source = candidate / "src/main.cpp"
        source.write_text("#include <c.hpp>\nint main(){return c();}\n", encoding="utf-8")
        (candidate / "include/a.hpp").unlink()
        (candidate / "include/c.hpp").write_text("#pragma once\ninline int c(){return 3;}\n", encoding="utf-8")
        owner = AuthorityMigration(engine)
        with engine.guardian_transaction(guardian_id=gid, kind="authority", request=request,
                                         state_ticket_id=ticket):
            created = owner.checkout(candidate_root=candidate,
                                     compile_commands=write_compile_commands(candidate, [source]),
                                     purpose="Native same-database Guardian compiler-authority acceptance")
        self._assert_native_guardian_projection(engine, guardian, gid, created["transaction_id"], "CHECKED_OUT")
        return fixture, engine, guardian, gid, owner, created

    def _prepare_native_guardian_authority(self, engine, owner, created):
        import re
        from workshop.tests.test_authority_migration import fill_review
        from workshop.tests.metadata_test_support import complete_metadata_audit
        fill_review(Path(created["review"]), {"src/main.cpp": "updated",
                    "include/c.hpp": "created", "include/a.hpp": "removed"})
        work = Path(created["work_directory"])
        blueprint = next(path for path in (work / "blueprints").glob("PROJECT_CODE_*.md")
                         if 'target_source_file = "src/main.cpp"' in path.read_text(encoding="utf-8"))
        reviewed, count = re.subn(r"(?m)^capsule\s*=.*$",
            lambda _: "capsule = " + json.dumps("src/main.cpp includes c.hpp and returns c(); the new header declares c() returning 3."),
            blueprint.read_text(encoding="utf-8"), count=1)
        self.assertEqual(count, 1)
        blueprint.write_bytes(reviewed.encode("utf-8"))
        (work / "managed" / blueprint.name).write_bytes(reviewed.encode("utf-8"))
        complete_metadata_audit(engine, created, update_capsules=False)
        owner.prepare(created["transaction_id"])
        engine.metadata_check(created["transaction_id"])

    def test_native_guardian_authority_apply_preserves_event_and_compiler_source_authority(self):
        fixture, engine, guardian, gid, owner, created = self._native_guardian_authority()
        tx = created["transaction_id"]
        self._prepare_native_guardian_authority(engine, owner, created)
        previous = self._assert_native_guardian_projection(engine, guardian, gid, tx, "PREPARED")
        owner.verify(tx)
        current = self._assert_native_guardian_projection(engine, guardian, gid, tx, "SHADOW_VERIFIED")
        self.assertGreater(current["tx_event_sequence"], previous["tx_event_sequence"])
        result = owner.apply(tx)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self._assert_native_guardian_projection(engine, guardian, gid, tx, "POSTCHECK_VERIFIED")
        self.assertTrue(result["bit_exact"])
        self.assertFalse((fixture.project / "include/a.hpp").exists())
        self.assertEqual((fixture.project / "include/c.hpp").read_text(encoding="utf-8"),
                         "#pragma once\ninline int c(){return 3;}\n")
        self.assertEqual((fixture.project / "src/main.cpp").read_text(encoding="utf-8"),
                         "#include <c.hpp>\nint main(){return c();}\n")
        self.assertTrue(engine.status()["verified"])
        self.assertFalse(engine.lease_path.exists())

    def test_native_guardian_authority_abort_at_each_safe_state_with_pending_identity(self):
        from runtime_sync_workshop.tx_documents import pending_path
        from workshop.tests.test_authority_migration import fill_review
        for target in ("CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"):
            for pending in (False, True):
                with self.subTest(state=target, pending=pending):
                    fixture, engine, guardian, gid, owner, created = self._native_guardian_authority()
                    tx = created["transaction_id"]
                    baseline = engine._verified_package()
                    source = (fixture.project / "src/main.cpp").read_bytes()
                    if target == "AWAITING_METADATA_REVIEW":
                        fill_review(Path(created["review"]), {"src/main.cpp": "updated",
                                    "include/c.hpp": "created", "include/a.hpp": "removed"})
                        with self.assertRaises(WorkshopError) as caught:
                            owner.prepare(tx)
                        self.assertEqual(caught.exception.code, "METADATA_HEADER_UNCHANGED")
                    elif target in {"PREPARED", "SHADOW_VERIFIED"}:
                        self._prepare_native_guardian_authority(engine, owner, created)
                    if target == "SHADOW_VERIFIED":
                        owner.verify(tx)
                    previous = self._assert_native_guardian_projection(engine, guardian, gid, tx, target)
                    if pending:
                        with patch("runtime_sync_workshop.tx_documents.publish",
                                   side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Native authority pending publication")):
                            with self.assertRaises(WorkshopError):
                                engine.annotate_transaction(tx, kind="observation",
                                    summary="Guardian compiler authority Abort preserves exact pending identity",
                                    evidence="isolated native authority proof fixture")
                        _, previous = engine._load_transaction(tx)
                        marker = json.loads(pending_path(engine, tx).read_text(encoding="utf-8"))
                        self.assertEqual(marker["event_id"], previous["tx_event_id"])
                    with patch.object(owner, "apply", side_effect=AssertionError("Safe Abort must not repeat Apply")):
                        owner.abort(tx)
                    current = self._assert_native_guardian_projection(engine, guardian, gid, tx, "ABORTED")
                    self.assertEqual(current["tx_event"]["previous_event_id"], previous["tx_event_id"])
                    self.assertFalse(pending_path(engine, tx).exists())
                    self.assertEqual((fixture.project / "src/main.cpp").read_bytes(), source)
                    self.assertTrue((fixture.project / "include/a.hpp").exists())
                    self.assertFalse((fixture.project / "include/c.hpp").exists())
                    self.assertEqual(engine._verified_package(), baseline)
                    self.assertFalse(engine.lease_path.exists())

    def _native_guardian_declared_auxiliary(self):
        from workshop.tests.test_context_transactions import ContextTransactionTests
        from runtime_sync_workshop.guardian_common import PATCH_SCOPE_SCHEMA
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction

        name = 'PROCESS_AUTHORITY.md'

        class DeclaredAuxiliaryFixture(ContextTransactionTests):
            def initial_auxiliary_documents(self):
                document = self.root / name
                document.write_bytes(b'# Declared process authority\r\n\r\nInitial exact input.\r\n')
                return (document,)

        def factory():
            fixture = DeclaredAuxiliaryFixture('test_apply_promotes_reviewed_code_and_semantic_context_in_one_heartbeat')
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            return fixture

        fixture, engine, guardian, gid, ticket = self._guarded_transaction_fixture(
            fixture_factory=factory,
            extra_mapped_paths=[name],
            scope_contract={'schema': PATCH_SCOPE_SCHEMA, 'normal': {'sources': ['app.py']},
                            'auxiliary': {'documents': [name]}},
        )
        # Reload the actual serialized configuration; no in-memory declaration
        # or intake-hash rewrite supplies the positive fixture's authority.
        restarted = WorkshopEngine(engine.config.config_path)
        self.assertEqual(restarted.config.auxiliary_documents, (name,))
        self.assertEqual(restarted._verified_package(), engine._verified_package())
        owner = AuxiliaryDocumentTransaction(engine)
        with engine.guardian_transaction(guardian_id=gid, kind='auxiliary',
                                         request={'documents': [name]}, state_ticket_id=ticket):
            created = owner.checkout([name], purpose='Native same-database Guardian with canonically declared Auxiliary authority')
        self._assert_native_guardian_projection(engine, guardian, gid, created['transaction_id'], 'CHECKED_OUT')
        return fixture, engine, guardian, gid, owner, created, name

    def test_native_guardian_declared_auxiliary_apply_preserves_all_event_identities(self):
        fixture, engine, guardian, gid, owner, created, name = self._native_guardian_declared_auxiliary()
        tx = created['transaction_id']
        source_before = (fixture.project / 'app.py').read_bytes()
        input_before = (fixture.root / name).read_bytes()
        config_before = engine.config.config_path.read_bytes()
        candidate = input_before + b'\r\nReviewed process amendment.\r\n'
        (Path(created['work_directory']) / name).write_bytes(candidate)
        previous = self._assert_native_guardian_projection(engine, guardian, gid, tx, 'CHECKED_OUT')
        owner.verify(tx)
        current = self._assert_native_guardian_projection(engine, guardian, gid, tx, 'SHADOW_VERIFIED')
        self.assertGreater(current['tx_event_sequence'], previous['tx_event_sequence'])
        applied = owner.apply(tx)
        self.assertTrue(applied['bit_exact'])
        self._assert_native_guardian_projection(engine, guardian, gid, tx, 'POSTCHECK_VERIFIED')
        self.assertEqual((engine.config.blueprint_root / name).read_bytes(), candidate)
        self.assertEqual((fixture.root / name).read_bytes(), input_before)
        self.assertEqual((fixture.project / 'app.py').read_bytes(), source_before)
        self.assertEqual(engine.config.config_path.read_bytes(), config_before)
        self.assertFalse(engine.lease_path.exists())
        self.assertTrue(engine.status()['verified'])

    def test_native_guardian_declared_auxiliary_abort_preserves_pending_identity(self):
        from runtime_sync_workshop.tx_documents import pending_path
        for target in ('CHECKED_OUT', 'SHADOW_VERIFIED'):
            for pending in (False, True):
                with self.subTest(state=target, pending=pending):
                    fixture, engine, guardian, gid, owner, created, name = self._native_guardian_declared_auxiliary()
                    tx = created['transaction_id']
                    baseline = engine._verified_package()
                    original = (engine.config.blueprint_root / name).read_bytes()
                    source = (fixture.project / 'app.py').read_bytes()
                    (Path(created['work_directory']) / name).write_bytes(original + b'\r\nUnapplied process candidate.\r\n')
                    if target == 'SHADOW_VERIFIED':
                        owner.verify(tx)
                    previous = self._assert_native_guardian_projection(engine, guardian, gid, tx, target)
                    if pending:
                        with patch('runtime_sync_workshop.tx_documents.publish',
                                   side_effect=WorkshopError('INJECTED_PROJECTION_OUTAGE', 'Native declared Auxiliary pending publication')):
                            with self.assertRaises(WorkshopError):
                                engine.annotate_transaction(tx, kind='observation',
                                    summary='Native declared Auxiliary Abort preserves the pending event identity',
                                    evidence='isolated native declaration and Guardian proof')
                        _, previous = engine._load_transaction(tx)
                        marker = json.loads(pending_path(engine, tx).read_text(encoding='utf-8'))
                        self.assertEqual(marker['event_id'], previous['tx_event_id'])
                    with patch.object(owner, 'apply', side_effect=AssertionError('Safe Abort must not invoke Apply')):
                        owner.abort(tx)
                    current = self._assert_native_guardian_projection(engine, guardian, gid, tx, 'ABORTED')
                    self.assertEqual(current['tx_event']['previous_event_id'], previous['tx_event_id'])
                    self.assertFalse(pending_path(engine, tx).exists())
                    self.assertEqual((engine.config.blueprint_root / name).read_bytes(), original)
                    self.assertEqual((fixture.project / 'app.py').read_bytes(), source)
                    self.assertEqual(engine._verified_package(), baseline)
                    self.assertFalse(engine.lease_path.exists())

    def test_native_guardian_auxiliary_unconfigured_scope_refuses_without_lease_or_grant(self):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        fixture, engine, guardian, gid, ticket = self._guarded_transaction_fixture()
        self.assertFalse(engine.config.auxiliary_documents)
        before = guardian._load(gid)
        package = engine._verified_package()
        roots = set(engine.config.transaction_directory.glob("TXN_*"))
        with self.assertRaises(WorkshopError) as caught:
            AuxiliaryDocumentTransaction(engine).checkout(
                ["UNDECLARED_PROCESS_AUTHORITY.md"],
                purpose="Reject undeclared auxiliary authority without inventing canonical configuration",
            )
        self.assertEqual(caught.exception.code, "AUXILIARY_DOCUMENT_UNKNOWN")
        self.assertEqual(guardian._load(gid), before)
        self.assertFalse(engine.lease_path.exists())
        self.assertEqual(set(engine.config.transaction_directory.glob("TXN_*")), roots)
        self.assertEqual(engine._verified_package(), package)
        self.assertFalse((engine.config.blueprint_root / "UNDECLARED_PROCESS_AUTHORITY.md").exists())

    def test_native_same_database_proof_binds_real_source_and_direct_events(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        payload = guardian._load(guardian_id)
        exact = payload["evidence"]["EXACT_SOURCE"][0]
        self.assertEqual(exact["paths"], ["app.py"])
        self.assertTrue(exact["matches"])
        self.assertTrue(all(row["path"] == "app.py" for row in exact["matches"]))
        self.assertEqual(exact["matches"][0]["line"], 1)
        self.assertTrue(exact["inspection_paths"][0].startswith("code/"))
        before = (engine.config.codebase_root / "app.py").read_bytes()
        checkout = engine.checkout(
            ["app.py"], purpose="Native same-database source proof and direct Guardian event identity",
            guardian_id=guardian_id, guardian_state_ticket=ticket,
        )
        tx = checkout["transaction_id"]
        _, state = engine._load_transaction(tx)
        assert_projected(engine, tx)
        details = [event["details"] for event in guardian._load(guardian_id)["events"]
                   if event.get("kind") == "TX_STATE"]
        self.assertEqual(details[-1]["tx_event_id"], state["tx_event_id"])
        engine.abort(tx)
        _, state = engine._load_transaction(tx)
        assert_projected(engine, tx)
        details = [event["details"] for event in guardian._load(guardian_id)["events"]
                   if event.get("kind") == "TX_STATE"]
        self.assertEqual(details[-1]["tx_event_id"], state["tx_event_id"])
        self.assertEqual(state["state"], "ABORTED")
        self.assertEqual((engine.config.codebase_root / "app.py").read_bytes(), before)
        self.assertFalse(engine.lease_path.exists())


    def test_native_source_origin_rejects_unbound_stale_and_non_source_claims(self):
        import sqlite3
        from contextlib import closing
        fixture, engine, guardian, guardian_id, _ = self._guarded_transaction_fixture()
        payload = guardian._load(guardian_id)
        exact = payload["evidence"]["EXACT_SOURCE"][0]
        with closing(sqlite3.connect(engine.config.kairos_database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT receipt_json,permit_id FROM source_inspection_receipts WHERE inspection_id=?",
                (exact["id"],),
            ).fetchone()
            receipt = json.loads(row["receipt_json"])
            permit = dict(connection.execute(
                "SELECT routing_receipt_id,purpose,scope_sha256,receipt_json FROM source_inspection_permits WHERE permit_id=?",
                (row["permit_id"],),
            ).fetchone())
        origin = json.loads(permit["receipt_json"])
        physical_matches = exact["inspection_matches"]
        paths = exact["inspection_paths"]
        cases = [
            ("missing root", "root_missing", "GUARDIAN_SOURCE_ROOT_UNBOUND"),
            ("absolute root", "root_absolute", "GUARDIAN_SOURCE_ROOT_UNBOUND"),
            ("escaped root", "root_escape", "GUARDIAN_SOURCE_ROOT_ESCAPE"),
            ("scope mismatch", "scope", "GUARDIAN_SOURCE_SCOPE_MISMATCH"),
            ("permit ticket mismatch", "ticket", "GUARDIAN_EVIDENCE_GATE_MISMATCH"),
            ("different paths", "paths", "GUARDIAN_SOURCE_SCOPE_MISMATCH"),
            ("missing snapshot", "snapshot_missing", "GUARDIAN_SOURCE_SNAPSHOT_MISSING"),
            ("changed size", "snapshot_size", "GUARDIAN_SOURCE_SNAPSHOT_CHANGED"),
            ("changed time", "snapshot_time", "GUARDIAN_SOURCE_SNAPSHOT_CHANGED"),
            ("metadata line", "metadata", "GUARDIAN_SOURCE_LEDGER_MATCH_INVALID"),
            ("out of range line", "line_range", "GUARDIAN_SOURCE_LEDGER_MATCH_INVALID"),
            ("negative decorated view", "negative", "GUARDIAN_SOURCE_LEDGER_NEGATIVE_UNSUPPORTED"),
            ("unowned research", "research", "GUARDIAN_SOURCE_LEDGER_UNOWNED"),
        ]
        for title, change, expected in cases:
            with self.subTest(title=title):
                value = copy.deepcopy(origin)
                source_receipt = copy.deepcopy(receipt)
                current_paths = list(paths)
                matches = copy.deepcopy(physical_matches)
                positive = True
                if change == "root_missing":
                    value.pop("root_relative")
                elif change == "root_absolute":
                    value["root_relative"] = str(engine.config.codebase_root)
                elif change == "root_escape":
                    value["root_relative"] = "../../outside"
                elif change == "scope":
                    source_receipt["scope_sha256"] = "0" * 64
                elif change == "ticket":
                    value["guardian"]["state_ticket_id"] = "GST_" + "0" * 24
                elif change == "paths":
                    value["paths"] = ["code/not-the-inspected-document.md"]
                elif change == "snapshot_missing":
                    value["file_snapshot"] = []
                elif change == "snapshot_size":
                    value["file_snapshot"][0]["size"] += 1
                elif change == "snapshot_time":
                    value["file_snapshot"][0]["mtime_ns"] -= 1
                elif change == "metadata":
                    matches[0]["line"] = 1
                elif change == "line_range":
                    matches[0]["line"] = 1000000
                elif change == "negative":
                    positive = False
                elif change == "research":
                    relative = "research/RESEARCH_NATIVE_GUARDIAN_GRAPH.md"
                    path = engine.config.kairos_workspace / relative
                    stat = path.stat()
                    current_paths = [relative]
                    value["paths"] = current_paths
                    value["file_snapshot"] = [{"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}]
                    matches = [{"path": relative, "line": 1, "patterns": ["fixture"]}]
                candidate = {**permit, "receipt_json": json.dumps(value)}
                with self.assertRaises(WorkshopError) as caught:
                    guardian._source_receipt_facts(
                        payload, candidate, source_receipt, current_paths, matches, positive=positive,
                    )
                self.assertEqual(caught.exception.code, expected)
        self.assertFalse(engine.lease_path.exists())
        self.assertEqual(guardian._load(guardian_id), payload)

    def test_native_header_ledger_coordinates_use_verified_compiler_authority(self):
        import hashlib
        from runtime_sync_workshop.corpus import build_corpus_manifest
        from runtime_sync_workshop.guardian import InvestigationGuardian
        from workshop.tests.test_authority_migration import project_spec, write_compile_commands
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            source = project / "src" / "main.cpp"
            source.write_text('#include "main.h"\nint main(){return value();}\n', encoding="utf-8")
            header = project / "include" / "main.h"
            header.write_text("#pragma once\ninline int value(){return 1;}\n", encoding="utf-8")
            intake = initialize_project(
                project_root=project, workspace=root / "workspace",
                compile_commands=write_compile_commands(project, [source]), project_spec=project_spec(),
            )
            engine = WorkshopEngine(root / "workspace" / ".kairos" / "workshop.config.json")
            engine.seal()
            manifest = build_corpus_manifest(engine.config)
            self.assertTrue(manifest["verified"], manifest["issues"])
            record = manifest["records"][header.relative_to(project).as_posix()]
            self.assertEqual(record["kind"], "header_only")
            self.assertIsNone(record["source"])
            self.assertIsNotNone(record["header"])
            self.assertEqual(Path(record["header"]["path"]).resolve(), header.resolve())
            relative = record["database"]["path"]
            document = engine.config.kairos_workspace / relative
            lines = document.read_text(encoding="utf-8").splitlines()
            line = next(index + 1 for index, value in enumerate(lines) if value.startswith("H:0001 "))
            stat = document.stat()
            binding = {"guardian_id": "GRD2_COMPONENT", "state_ticket_id": "GST_COMPONENT", "step": "EXACT_SOURCE"}
            origin = {
                "root_relative": ".", "paths": [relative], "guardian": binding,
                "file_snapshot": [{"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}],
            }
            permit = {"scope_sha256": "f" * 64, "receipt_json": json.dumps(origin)}
            receipt = {"guardian": binding, "scope_sha256": "f" * 64}
            # This tests the fact normalizer only; it neither inserts receipts nor authorizes PROVE.
            guardian = InvestigationGuardian(
                state_directory=engine.config.state_directory, kairos_database=engine.config.kairos_database,
                kairos_workspace=engine.config.kairos_workspace, codebase_root=engine.config.codebase_root,
                transaction_directory=engine.config.transaction_directory,
            )
            paths, matches, facts, inspection_root = guardian._source_receipt_facts(
                {"rolling_package_sha256": manifest["package_sha256"]}, permit, receipt, [relative],
                [{"path": relative, "line": line, "patterns": ["^H:"]}], positive=True,
            )
            self.assertEqual(paths, ["include/main.h"])
            self.assertEqual(matches[0]["path"], "include/main.h")
            self.assertEqual(matches[0]["line"], 1)
            self.assertEqual(matches[0]["inspection_line"], line)
            self.assertEqual(facts[0]["sha256"], hashlib.sha256(header.read_bytes()).hexdigest())
            self.assertEqual(inspection_root, ".")

    def test_direct_guardian_checkout_and_abort_bind_exact_transaction_events(self):
        from runtime_sync_workshop.tx_documents import assert_projected
        from runtime_sync_workshop.guardian_transaction import _transaction_event_reference

        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            checkout = engine.checkout(["app.py"], purpose="Exercise Guardian event identity through direct checkout",
                                       guardian_id=guardian_id, guardian_state_ticket=ticket)
            tx = checkout["transaction_id"]
            root, state = engine._load_transaction(tx)
            assert_projected(engine, tx)
            checkout_id = state["tx_event_id"]
            recorded = guardian._load(guardian_id)["events"][-1]["details"]
            self.assertEqual(recorded["tx_event_id"], checkout_id)
            self.assertEqual(recorded["tx_event_sequence"], state["tx_event_sequence"])
            self.assertEqual(recorded["tx_event_relation"], "latest_persisted")

            # A diagnostic save is not a new material TX transition; the
            # Guardian explicitly references the unchanged latest TX event.
            engine._save_transaction(root, state, event="ISOLATED_DIAGNOSTIC")
            _, unchanged = engine._load_transaction(tx)
            self.assertEqual(unchanged["tx_event_id"], checkout_id)
            self.assertEqual(unchanged["tx_event_sequence"], state["tx_event_sequence"])
            detail = guardian._load(guardian_id)["events"][-1]["details"]
            self.assertEqual(detail["tx_event_id"], checkout_id)
            self.assertEqual(detail["tx_event_relation"], "latest_persisted")
            assert_projected(engine, tx)

            for field in ("tx_event_id", "tx_event_sequence"):
                tampered = copy.deepcopy(unchanged)
                tampered.pop(field)
                with self.assertRaises(WorkshopError) as caught:
                    guardian.observe_transaction_state(tx, event="ISOLATED_INVALID", state=tampered)
                self.assertEqual(caught.exception.code, "GUARDIAN_TX_EVENT_INVALID")
            missing = copy.deepcopy(unchanged)
            for field in ("tx_event", "tx_event_id", "tx_event_sequence"):
                missing.pop(field)
            with self.assertRaises(WorkshopError) as caught:
                guardian.observe_transaction_state(tx, event="ISOLATED_MISSING", state=missing)
            self.assertEqual(caught.exception.code, "GUARDIAN_TX_EVENT_INVALID")
            forged = copy.deepcopy(unchanged)
            forged["tx_event"]["state_after"] = "ABORTED"
            with self.assertRaises(WorkshopError) as caught:
                _transaction_event_reference(tx, forged)
            self.assertEqual(caught.exception.code, "GUARDIAN_TX_EVENT_INVALID")
            gap = copy.deepcopy(unchanged)
            gap["tx_event_sequence"] += 2
            event = gap["tx_event"]
            event["sequence"] = gap["tx_event_sequence"]
            event["previous_event_id"] = checkout_id
            event.pop("event_id")
            event_id = "TXE_" + hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32].upper()
            event["event_id"] = gap["tx_event_id"] = event_id
            with self.assertRaises(WorkshopError) as caught:
                guardian.observe_transaction_state(tx, event="ISOLATED_GAP", state=gap)
            self.assertEqual(caught.exception.code, "GUARDIAN_TX_EVENT_GAP")

            engine.abort(tx)
            _, final = engine._load_transaction(tx)
            assert_projected(engine, tx)
            events = [e["details"] for e in guardian._load(guardian_id)["events"]
                      if e.get("kind") == "TX_STATE"]
            self.assertEqual(events[-1]["tx_event_id"], final["tx_event_id"])
            self.assertNotEqual(final["tx_event_id"], checkout_id)
            self.assertEqual(final["tx_event"]["previous_event_id"], checkout_id)

    def test_guardian_terminal_event_recovers_once_after_observer_interruption(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path

        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            checkout = engine.checkout(["app.py"], purpose="Recover a Guardian observation after state persistence",
                                       guardian_id=guardian_id, guardian_state_ticket=ticket)
            tx = checkout["transaction_id"]
            observe = guardian.observe_transaction_state

            def interrupted(transaction_id, *, event, state, **kwargs):
                if event == "TRANSACTION_ABORTED":
                    raise OSError("isolated failure before Guardian observation")
                return observe(transaction_id, event=event, state=state, **kwargs)

            with patch.object(guardian, "observe_transaction_state", side_effect=interrupted):
                with self.assertRaises(OSError):
                    engine.abort(tx)
            _, state = engine._load_transaction(tx)
            self.assertEqual(state["state"], "ABORTED")
            self.assertTrue(pending_path(engine, tx).is_file())
            self.assertTrue(engine.lease_path.exists())
            original_id = state["tx_event_id"]
            engine.guardian_reconcile(guardian_id)
            self.assertFalse(engine.lease_path.exists())
            engine.reconcile_transaction_document(tx)
            assert_projected(engine, tx)
            _, after = engine._load_transaction(tx)
            self.assertEqual(after["tx_event_id"], original_id)
            events = [e["details"] for e in guardian._load(guardian_id)["events"]
                      if e.get("kind") == "TX_STATE" and e["details"].get("tx_event_id") == original_id]
            self.assertEqual(len(events), 1)

    def test_guardian_terminal_reconcile_does_not_duplicate_existing_event_link(self):
        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            checkout = engine.checkout(["app.py"], purpose="Recover after Guardian already observed terminal state",
                                       guardian_id=guardian_id, guardian_state_ticket=ticket)
            tx = checkout["transaction_id"]
            with patch.object(engine, "_release_lease", side_effect=OSError("isolated lease release outage")):
                with self.assertRaises(OSError):
                    engine.abort(tx)
            _, state = engine._load_transaction(tx)
            event_id = state["tx_event_id"]
            engine.guardian_reconcile(guardian_id)
            events = [e["details"] for e in guardian._load(guardian_id)["events"]
                      if e.get("kind") == "TX_STATE" and e["details"].get("tx_event_id") == event_id]
            self.assertEqual(len(events), 1)
            self.assertFalse(engine.lease_path.exists())

    def test_guardian_nonterminal_event_recovery_retains_lease_and_is_idempotent(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path

        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            checkout = engine.checkout(["app.py"], purpose="Recover a pending Guardian metadata-review event",
                                       guardian_id=guardian_id, guardian_state_ticket=ticket)
            tx = checkout["transaction_id"]
            (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 2\n")
            observe = guardian.observe_transaction_state

            def interrupted(transaction_id, *, event, state, **kwargs):
                if state.get("state") == "AWAITING_METADATA_REVIEW":
                    raise OSError("isolated failure before nonterminal Guardian observation")
                return observe(transaction_id, event=event, state=state, **kwargs)

            with patch.object(guardian, "observe_transaction_state", side_effect=interrupted):
                with self.assertRaises(OSError):
                    engine.prepare(tx)
            _, state = engine._load_transaction(tx)
            self.assertEqual(state["state"], "AWAITING_METADATA_REVIEW")
            event_id = state["tx_event_id"]
            self.assertTrue(pending_path(engine, tx).is_file())
            first = engine.guardian_reconcile(guardian_id)
            second = engine.guardian_reconcile(guardian_id)
            self.assertTrue(first["lease_retained"])
            self.assertTrue(second["lease_retained"])
            self.assertTrue(engine.lease_path.exists())
            events = [e["details"] for e in guardian._load(guardian_id)["events"]
                      if e.get("kind") == "TX_STATE" and e["details"].get("tx_event_id") == event_id]
            self.assertEqual(len(events), 1)
            engine.reconcile_transaction_document(tx)
            assert_projected(engine, tx)
            engine.abort(tx)
            self.assertFalse(engine.lease_path.exists())

    def test_guardian_checkout_interruption_preserves_owned_recovery_path(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path

        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        observe = guardian.observe_transaction_state
        interrupted_ids = []

        def interrupted(transaction_id, *, event, state, **kwargs):
            if event == "CHECKOUT_COMPLETED":
                interrupted_ids.append(transaction_id)
                raise OSError("isolated interruption after persisted checkout state")
            return observe(transaction_id, event=event, state=state, **kwargs)

        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            with patch.object(guardian, "observe_transaction_state", side_effect=interrupted):
                with self.assertRaises(WorkshopError) as caught:
                    engine.checkout(["app.py"], purpose="Recover a Guardian checkout observation interruption",
                                    guardian_id=guardian_id, guardian_state_ticket=ticket)
            self.assertEqual(caught.exception.code, "GUARDIAN_NONTERMINAL_RECOVERY_REQUIRED")
            self.assertEqual(len(interrupted_ids), 1)
            tx = interrupted_ids[0]
            _, state = engine._load_transaction(tx)
            self.assertEqual(state["state"], "CHECKED_OUT")
            self.assertTrue(engine.lease_path.exists())
            self.assertTrue(pending_path(engine, tx).exists())
            self.assertEqual(guardian.status(guardian_id, full=True)["patch_session"]["active_transaction"], tx)
            result = engine.guardian_reconcile(guardian_id)
            self.assertTrue(result["lease_retained"])
            engine.reconcile_transaction_document(tx)
            assert_projected(engine, tx)
            engine.abort(tx)
            self.assertFalse(engine.lease_path.exists())

    def test_direct_abort_first_links_pending_guardian_event_without_second_apply(self):
        from runtime_sync_workshop.tx_documents import assert_projected, pending_path

        fixture, engine, guardian, guardian_id, ticket = self._guarded_transaction_fixture()
        with patch.object(engine, "guardian", return_value=guardian), patch.object(engine, "_guardian_enabled", return_value=True):
            checkout = engine.checkout(["app.py"], purpose="Abort safely after an interrupted Guardian observation",
                                       guardian_id=guardian_id, guardian_state_ticket=ticket)
            tx = checkout["transaction_id"]
            (Path(checkout["work_directory"]) / "app.py").write_bytes(b"value = 2\n")
            observe = guardian.observe_transaction_state

            def interrupted(transaction_id, *, event, state, **kwargs):
                if state.get("state") == "AWAITING_METADATA_REVIEW":
                    raise OSError("isolated failure before the Guardian review observation")
                return observe(transaction_id, event=event, state=state, **kwargs)

            with patch.object(guardian, "observe_transaction_state", side_effect=interrupted):
                with self.assertRaises(OSError):
                    engine.prepare(tx)
            _, pending = engine._load_transaction(tx)
            pending_id = pending["tx_event_id"]
            self.assertTrue(pending_path(engine, tx).is_file())
            with patch.object(engine, "apply", side_effect=AssertionError("Abort must never repeat Apply")):
                engine.abort(tx)
            _, final = engine._load_transaction(tx)
            assert_projected(engine, tx)
            self.assertEqual(final["state"], "ABORTED")
            self.assertEqual(final["tx_event"]["previous_event_id"], pending_id)
            events = [e["details"] for e in guardian._load(guardian_id)["events"] if e.get("kind") == "TX_STATE"]
            self.assertEqual(sum(e.get("tx_event_id") == pending_id for e in events), 1)
            self.assertEqual(sum(e.get("tx_event_id") == final["tx_event_id"] for e in events), 1)
            self.assertFalse(pending_path(engine, tx).exists())
            self.assertFalse(engine.lease_path.exists())

    def test_direct_checkout_and_legacy_transaction_commands_fail_closed_when_guardian_is_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            source = project / "src" / "main.cpp"
            source.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=_compile_commands(project, source), project_spec=_spec())
            config = workspace / ".kairos" / "workshop.config.json"
            unguarded = WorkshopEngine(config)
            unguarded.seal()
            legacy = unguarded.checkout(["src/main.cpp"], purpose="Create a pre-Guardian transaction for boundary testing")
            memory, requirements = _memory_contract(root)
            env = {
                "WERKFADEN_GUARDIAN_REQUIRED": "1",
                "WERKFADEN_GUARDIAN_MEMORY_FILE": str(memory),
                "WERKFADEN_GUARDIAN_MEMORY_REQUIREMENTS_FILE": str(requirements),
            }
            with patch.dict(os.environ, env, clear=False):
                guarded = WorkshopEngine(config)
                with self.assertRaises(WorkshopError) as caught:
                    guarded.prepare(legacy["transaction_id"])
                self.assertEqual(caught.exception.code, "GUARDIAN_TRANSACTION_BINDING")
            unguarded.abort(legacy["transaction_id"])
            with patch.dict(os.environ, env, clear=False):
                guarded = WorkshopEngine(config)
                with self.assertRaises(WorkshopError) as caught:
                    guarded.checkout(["src/main.cpp"], purpose="Direct public API checkout must remain gated")
                self.assertEqual(caught.exception.code, "GUARDIAN_REQUIRED")

    def test_alternate_workshop_config_cannot_create_second_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            source = project / "src" / "main.cpp"
            source.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=_compile_commands(project, source), project_spec=_spec())
            canonical = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            alt_root = root / "alternate-control-plane"
            alt_root.mkdir()
            raw = json.loads(json.dumps(canonical.config.raw))
            raw.update({
                "codebase_root": str(canonical.config.codebase_root),
                "runtime_root": str(canonical.config.runtime_root),
                "cmake_file": str(canonical.config.cmake_file),
                "dataflow_index": str(canonical.config.dataflow_index),
                "blueprint_root": str(canonical.config.blueprint_root),
                "managed_blueprint_root": str(canonical.config.managed_blueprint_root),
                "kairos_workspace": str(canonical.config.kairos_workspace),
                "kairos_harness": str(canonical.config.kairos_harness),
                "kairos_database": str(canonical.config.kairos_database),
                "state_directory": ".state",
                "transaction_directory": "transactions",
                "machine_authority_files": ["workshop.config.json"],
            })
            alt = alt_root / "workshop.config.json"
            alt.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(WorkshopError) as caught:
                WorkshopEngine(alt)
            self.assertEqual(caught.exception.code, "CONFIG_CONTROL_PLANE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
