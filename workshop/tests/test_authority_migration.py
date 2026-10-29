from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = PACKAGE_ROOT / "workshop" / "src"
for value in (PACKAGE_ROOT, WORKSHOP_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.binding import initialize_project  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from runtime_sync_workshop.migration import AuthorityMigration  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402
from runtime_sync_workshop.tx_documents import assert_projected  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402


def project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_MIGRATION",
            "title": "Migration project",
            "objective": "Maintain compiler-backed code and authority under synchronized migrations.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_MIGRATION_01",
                "title": "Authority continuity",
                "objective": "Keep structural source changes synchronized with KAIROS evidence.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_MIGRATION_001",
                    "description": "Source topology, documentation and database remain synchronized.",
                    "state": "active",
                    "evidence_required": "Migration shadow and postcheck receipts.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_MIGRATION_001",
            "title": "Maintain authority topology",
            "objective": "Apply one compiler-backed structural migration without stale code evidence.",
            "milestone": "MILESTONE_MIGRATION_01",
            "criteria": ["CRIT_MIGRATION_001"],
        },
    }


def write_compile_commands(project: Path, sources: list[Path]) -> Path:
    build = project / "build"
    build.mkdir(exist_ok=True)
    path = build / "compile_commands.json"
    include = project / "include"
    payload = []
    for source in sources:
        args = ["c++"]
        if include.is_dir():
            args.extend(["-I", str(include)])
        args.extend(["-c", str(source)])
        payload.append({"directory": str(build), "file": str(source), "arguments": args})
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def fill_review(path: Path, actions: dict[str, str]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        relative = entry["path"]
        impact = actions[relative]
        entry.update(
            metadata_impact=impact,
            reason=f"Explicit authority migration review for {relative} keeps project evidence synchronized",
            reviewer="migration-test",
            reviewed_at="2026-09-08T00:00:00Z",
        )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def edit_semantic_blueprint(work: Path, needle: str, replacement: str) -> None:
    candidates = list((work / "blueprints").glob("PROJECT_CODE_*.md"))
    target = next(path for path in candidates if needle in path.read_text(encoding="utf-8"))
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace(needle, replacement), encoding="utf-8")
    managed = work / "managed" / target.name
    managed.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")


class AuthorityMigrationTests(unittest.TestCase):
    def test_compiler_partial_managed_write_restores_freshness_and_projects(self):
        from unittest.mock import patch
        from runtime_sync_workshop import migration as module
        from runtime_sync_workshop.tx_documents import pending_path
        original_apply = AuthorityMigration.apply
        original_copy = module.copy_exact
        observed = []

        class RollbackObserved(Exception):
            pass

        def inject(owner, tx):
            root, state = owner.engine._load_transaction(tx)
            if owner._work_package(root, state)["package_sha256"] != state["verified_work_package_sha256"]:
                return original_apply(owner, tx)
            workspace = owner.engine.config.kairos_workspace
            paths = list((workspace / "code").glob("*.md")) + [owner.engine.config.dataflow_index]
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths if path.is_file()}
            seal = owner.engine.seal_path.read_bytes()
            faulted = []

            def fail_after_write(source, target):
                result = original_copy(source, target)
                if not faulted and Path(target).parent == owner.engine.config.managed_blueprint_root:
                    faulted.append(str(target))
                    raise OSError("isolated compiler fault after managed document write")
                return result

            with patch.object(module, "copy_exact", side_effect=fail_after_write):
                with self.assertRaises(WorkshopError) as caught:
                    original_apply(owner, tx)
            self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK", caught.exception.details)
            self.assertEqual(len(faulted), 1)
            assert_projected(owner.engine, tx)
            _, final = owner.engine._load_transaction(tx)
            self.assertEqual(final["state"], "ROLLED_BACK")
            for path, signature in before.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), signature, str(path))
            self.assertEqual(owner.engine.seal_path.read_bytes(), seal)
            self.assertFalse(pending_path(owner.engine, tx).exists())
            self.assertFalse(owner.engine.lease_path.exists())
            observed.append(tx)
            raise RollbackObserved()

        with patch.object(AuthorityMigration, "apply", new=inject):
            with self.assertRaises(RollbackObserved):
                self.test_header_add_remove_and_source_change_migrate_atomically()
        self.assertEqual(len(observed), 1)

    def _prove_direct_compiler_abort(self, target_state, *, pending=False):
        from unittest.mock import patch
        from contextlib import closing
        import sqlite3
        from kairos.frontmatter import split_frontmatter
        from kairos.transaction_document import document_id, document_path
        from runtime_sync_workshop.tx_documents import pending_path
        checkout, prepare, verify = AuthorityMigration.checkout, AuthorityMigration.prepare, AuthorityMigration.verify
        observed = []

        class AbortObserved(Exception):
            pass

        def stop_with_verified_abort(owner, tx):
            _, state = owner.engine._load_transaction(tx)
            self.assertEqual(state["state"], target_state)
            assert_projected(owner.engine, tx)
            baseline = state["baseline_package_sha256"]
            if pending:
                with patch("runtime_sync_workshop.tx_documents.publish",
                           side_effect=WorkshopError("INJECTED_PROJECTION_OUTAGE", "Isolated compiler abort publication outage")):
                    with self.assertRaises(WorkshopError):
                        owner.engine.annotate_transaction(
                            tx, kind="observation",
                            summary="Compiler abort remains available with its projection pending",
                            evidence="isolated compiler authority abort matrix fixture",
                        )
                _, state = owner.engine._load_transaction(tx)
                marker = json.loads(pending_path(owner.engine, tx).read_text(encoding="utf-8"))
                self.assertEqual(marker["event_id"], state["tx_event_id"])
                with self.assertRaises(WorkshopError) as caught:
                    owner.engine.checkout(["src/main.cpp"], purpose="Reject new compiler work while exact projection is pending")
                self.assertEqual(caught.exception.code, "TX_DOCUMENT_SYNC_PENDING")
                self.assertEqual(caught.exception.outcome, "TX_DOCUMENT_PROJECTION_PENDING")
                self.assertEqual(caught.exception.references["items"][0]["event_id"], marker["event_id"])
            _, before = owner.engine._load_transaction(tx)
            result = owner.abort(tx)
            self.assertEqual(result["state"], "ABORTED")
            assert_projected(owner.engine, tx)
            _, after = owner.engine._load_transaction(tx)
            metadata = split_frontmatter((owner.engine.config.kairos_workspace / document_path(tx)).read_text(encoding="utf-8")).metadata
            with closing(sqlite3.connect(owner.engine.config.kairos_database)) as connection:
                row = connection.execute("SELECT metadata_json FROM artifacts WHERE artifact_id=?", (document_id(tx),)).fetchone()
            sql = json.loads(row[0])
            self.assertEqual(after["state"], "ABORTED")
            self.assertEqual(after["tx_event"]["event"], "AUTHORITY_MIGRATION_ABORTED")
            self.assertEqual(after["tx_event_id"], metadata["tx_event_id"])
            self.assertEqual(after["tx_event_id"], sql["tx_event_id"])
            self.assertEqual(sql["workshop_state"], "ABORTED")
            self.assertGreater(after["tx_event_sequence"], before["tx_event_sequence"])
            self.assertEqual(after["tx_event"]["previous_event_id"], before["tx_event_id"])
            self.assertFalse(pending_path(owner.engine, tx).exists())
            self.assertFalse(owner.engine.lease_path.exists())
            status = owner.engine.status()
            self.assertTrue(status["verified"], status)
            self.assertEqual(status["package_sha256"], baseline)
            self.assertTrue(status["seal"]["matches_current"])
            observed.append((target_state, pending))
            raise AbortObserved()

        def checked(owner, *args, **kwargs):
            result = checkout(owner, *args, **kwargs)
            if target_state == "CHECKED_OUT":
                stop_with_verified_abort(owner, result["transaction_id"])
            return result

        def prepared(owner, tx):
            try:
                result = prepare(owner, tx)
            except WorkshopError:
                _, state = owner.engine._load_transaction(tx)
                if target_state == "AWAITING_METADATA_REVIEW" and state["state"] == target_state:
                    stop_with_verified_abort(owner, tx)
                raise
            if target_state == "PREPARED":
                stop_with_verified_abort(owner, tx)
            return result

        def verified(owner, tx):
            result = verify(owner, tx)
            if target_state == "SHADOW_VERIFIED":
                stop_with_verified_abort(owner, tx)
            return result

        # Reuse the compiler-authoritative fixture and its real public lifecycle.
        # The sentinel ends that fixture only after the native Abort is proved.
        with patch.object(AuthorityMigration, "checkout", new=checked), \
             patch.object(AuthorityMigration, "prepare", new=prepared), \
             patch.object(AuthorityMigration, "verify", new=verified):
            with self.assertRaises(AbortObserved):
                self.test_header_add_remove_and_source_change_migrate_atomically()
        self.assertEqual(observed, [(target_state, pending)])

    def test_direct_compiler_abort_from_every_supported_preapply_state(self):
        for state in ("CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"):
            with self.subTest(state=state):
                self._prove_direct_compiler_abort(state)

    def test_direct_compiler_abort_is_available_while_projection_is_pending(self):
        self._prove_direct_compiler_abort("CHECKED_OUT", pending=True)

    def compiler_sealed_interruption(self, *, point, fault=None):
        from unittest.mock import patch
        from runtime_sync_workshop import migration as module
        original = AuthorityMigration.apply
        seen = []
        def interrupted(owner, tx):
            root, state = owner.engine._load_transaction(tx)
            if owner._work_package(root, state)["package_sha256"] != state["verified_work_package_sha256"]:
                return original(owner, tx)
            write, save = module.atomic_write_json, owner.engine._save_transaction
            def interrupt_seal(path, payload):
                write(path, payload)
                if path == owner.engine.seal_path:
                    raise OSError("isolated interruption after compiler postcheck seal")
            def interrupt_source(root, state, *, event, details=None):
                if event == "AUTHORITY_POSTCHECK_VERIFIED":
                    raise OSError("isolated interruption before compiler postcheck source commit")
                return save(root, state, event=event, details=details)
            context = patch.object(module, "atomic_write_json", side_effect=interrupt_seal) if point == "seal" else patch.object(owner.engine, "_save_transaction", side_effect=interrupt_source)
            with context:
                with self.assertRaises(WorkshopError) as caught:
                    original(owner, tx)
            self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
            _, failed = owner.engine._load_transaction(tx)
            self.assertEqual(failed["state"], "RECOVERY_REQUIRED")
            self.assertTrue(owner.engine.lease_path.exists())
            assert_projected(owner.engine, tx)
            if point == "seal":
                self.assertNotIn("postcheck_package_sha256", failed)
            if fault:
                source = root / "review" / "metadata_audit.json" if fault == "work" else owner.engine.seal_path
                before = source.read_bytes()
                saved_state = (root / "state.json").read_bytes()
                if fault == "work":
                    source.write_bytes(before + b"\n")
                else:
                    wrong = json.loads(before)
                    wrong["transaction_id"] = "TXN_" + "f" * 24
                    source.write_text(json.dumps(wrong), encoding="utf-8")
                try:
                    with self.assertRaises(WorkshopError) as caught:
                        owner.engine.recover(tx)
                    expected = "AUTHORITY_RECOVERY_WORK_DRIFT" if fault == "work" else "AUTHORITY_RECOVERY_SEAL_MISMATCH"
                    self.assertEqual(caught.exception.code, expected)
                    self.assertEqual((root / "state.json").read_bytes(), saved_state)
                    self.assertTrue(owner.engine.lease_path.exists())
                finally:
                    source.write_bytes(before)  # Restore only the isolated fault fixture.
            with patch.object(module, "copy_exact", side_effect=AssertionError("Recovery cannot recopy Apply targets")):
                with patch.object(owner, "apply", side_effect=AssertionError("Recovery cannot Apply")):
                    result = owner.engine.recover(tx)
            _, final = owner.engine._load_transaction(tx)
            self.assertEqual(final["state"], "POSTCHECK_VERIFIED")
            self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
            self.assertEqual(final["recovery_verification"]["seal_transaction_id"], tx)
            self.assertTrue(final["heartbeat"]["verified"])
            self.assertFalse(owner.engine.lease_path.exists())
            assert_projected(owner.engine, tx)
            seen.append(tx)
            return result
        case = AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
        with patch.object(AuthorityMigration, "apply", interrupted):
            case.test_header_add_remove_and_source_change_migrate_atomically()
        self.assertEqual(len(seen), 1)

    def compiler_unsealed_interruption(self, boundary="mirror", fault=None):
        from unittest.mock import patch
        from runtime_sync_workshop import migration as module
        from runtime_sync_workshop.cli import _parser, execute
        class Finished(BaseException):
            pass
        original, save = AuthorityMigration.apply, WorkshopEngine._save_transaction
        seen = []
        def legacy_save(engine, root, state, *, event, details=None):
            if fault == "missing_pin" and event == "AUTHORITY_CHECKOUT_COMPLETED":
                state.pop("baseline_seal_sha256", None)
            return save(engine, root, state, event=event, details=details)
        def interrupted(owner, tx):
            root, state = owner.engine._load_transaction(tx)
            if owner._work_package(root, state)["package_sha256"] != state["verified_work_package_sha256"]:
                return original(owner, tx)
            seal = owner.engine.seal_path.read_bytes()
            _, heartbeat = owner.engine._import_kairos()
            copy, run = module.copy_exact, heartbeat.run_heartbeat
            def cut_copy(source, target):
                if target == owner.config.codebase_root / "src/main.cpp":
                    raise SystemExit("isolated cut before first compiler target")
                return copy(source, target)
            def cut_heartbeat(*args, **kwargs):
                if kwargs.get("trigger") == f"runtime-sync-authority-migration:{tx}":
                    raise SystemExit("isolated cut before compiler projection")
                return run(*args, **kwargs)
            context = (patch.object(owner.engine, "_mirror", side_effect=OSError("isolated unsealed compiler result"))
                       if boundary == "mirror" else patch.object(heartbeat, "run_heartbeat", side_effect=cut_heartbeat)
                       if boundary == "heartbeat" else patch.object(module, "copy_exact", side_effect=cut_copy))
            with context:
                if boundary == "mirror":
                    with self.assertRaises(WorkshopError) as caught:
                        original(owner, tx)
                    self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
                else:
                    with self.assertRaises(SystemExit):
                        original(owner, tx)
            assert_projected(owner.engine, tx)
            before = (root / "state.json").read_bytes()
            sources = {p.relative_to(owner.config.codebase_root).as_posix():p.read_bytes()
                       for p in owner.config.codebase_root.rglob("*") if p.suffix in {".cpp", ".hpp"}}
            if fault == "seal":
                data=json.loads(seal);data["created_at"]="isolated changed original seal"
                owner.engine.seal_path.write_text(json.dumps(data), encoding="utf-8")
            elif fault == "work":
                path=root / "review/metadata_audit.json"
                path.write_bytes(path.read_bytes()+b"\n")
            elif fault == "source":
                target=owner.config.codebase_root/"src/main.cpp"
                target.write_bytes(target.read_bytes()+b"\n")
            elif fault == "retired":
                (owner.config.codebase_root/"include/a.hpp").write_bytes(b"#pragma once\n")
            elif fault == "permit":
                governance,_=owner.engine._import_kairos()
                governance.revoke_action_permit(owner.config.kairos_workspace,
                    permit_id=owner.engine._load_transaction(tx)[1]["permit"]["permit_id"], reason="Isolated recovery must not renew revoked authority")
            elif fault and fault.startswith("role:"):
                role=fault.split(":",1)[1]
                row=next(r for r in owner._work_package(root,state)["files"] if r["role"]==role)
                roots={"candidate":owner.config.codebase_root,"blueprint":owner.config.blueprint_root,
                       "managed":owner.config.managed_blueprint_root,"authority":owner.config.kairos_workspace,
                       "machine":owner.config.machine_root,"external-final":owner.config.blueprint_root}
                relative=row["path"]
                if role=="machine":
                    relative={"compile_commands.json":"project-intake/compile_commands.json",
                              "PROJECT_BUILD_AUTHORITY.cmake":"project-intake/PROJECT_BUILD_AUTHORITY.cmake"}.get(relative,relative)
                target=roots[role]/relative
                target.write_bytes(target.read_bytes()+b"\n")
            observed_seal=owner.engine.seal_path.read_bytes()
            health=__import__("importlib").import_module("kairos.health")
            health_context=(patch.object(health,"health_audit",return_value={"verdict":"FAIL","failures":["isolated health refusal"]})
                            if fault=="health" else patch.object(health,"health_audit",wraps=health.health_audit))
            with health_context, patch.object(module,"copy_exact",side_effect=AssertionError("Recovery must not repeat target copies")):
                if fault:
                    governance,_=owner.engine._import_kairos()
                    errors=(WorkshopError,governance.GovernanceError) if fault=="permit" else WorkshopError
                    with self.assertRaises(errors) as caught:
                        owner.engine.recover(tx)
                    expected=("AUTHORITY_RECOVERY_TARGET_DRIFT" if fault.startswith("role:") or fault in {"source","retired"} else
                              {"seal":"AUTHORITY_RECOVERY_SEAL_MISMATCH","work":"AUTHORITY_RECOVERY_WORK_DRIFT",
                               "missing_pin":"AUTHORITY_RECOVERY_BASELINE_UNBOUND","health":"AUTHORITY_RECOVERY_STILL_BLOCKED","permit":None}[fault])
                    if fault!="permit":
                        self.assertEqual(caught.exception.code,expected)
                    self.assertEqual((root/"state.json").read_bytes(),before)
                    self.assertEqual(owner.engine.seal_path.read_bytes(),observed_seal)
                    self.assertTrue(owner.engine.lease_path.exists())
                else:
                    args=_parser().parse_args(["--config",str(owner.config.config_path),"recover",tx])
                    result=execute(args)
                    _,final=owner.engine._load_transaction(tx)
                    expected="ROLLED_BACK" if boundary=="before_copy" else "POSTCHECK_VERIFIED"
                    self.assertEqual(final["state"],expected)
                    assert_projected(owner.engine,tx)
                    self.assertFalse(owner.engine.lease_path.exists())
                    if boundary=="before_copy":
                        self.assertFalse(result["runtime_write_performed"])
                        self.assertEqual(owner.engine.seal_path.read_bytes(),seal)
                    else:
                        self.assertTrue(final["recovery_verification"]["seal_created"])
                        self.assertEqual(final["terminal_receipt"]["status"],"COMMITTED")
                    self.assertEqual({p.relative_to(owner.config.codebase_root).as_posix():p.read_bytes()
                                      for p in owner.config.codebase_root.rglob("*") if p.suffix in {".cpp",".hpp"}},sources)
            seen.append(tx)
            raise Finished()
        case=AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
        try:
            with patch.object(AuthorityMigration,"apply",interrupted), patch.object(WorkshopEngine,"_save_transaction",legacy_save):
                case.test_header_add_remove_and_source_change_migrate_atomically()
        except Finished:
            pass
        self.assertEqual(len(seen),1)

    def test_unsealed_compiler_result_recovers_before_or_after_heartbeat(self):
        for boundary in ("mirror","heartbeat"):
            with self.subTest(boundary=boundary):
                self.compiler_unsealed_interruption(boundary)

    def test_unsealed_compiler_untouched_baseline_retires_without_apply(self):
        self.compiler_unsealed_interruption("before_copy")

    def test_unsealed_compiler_recovery_refuses_all_changed_target_roles(self):
        for role in ("candidate","blueprint","managed","authority","machine","external-final"):
            with self.subTest(role=role):
                self.compiler_unsealed_interruption(fault="role:"+role)

    def test_unsealed_compiler_recovery_refuses_unbound_seal_work_or_failed_health(self):
        for fault in ("missing_pin","seal","work","health","source","retired"):
            with self.subTest(fault=fault):
                self.compiler_unsealed_interruption(fault=fault)

    def test_unsealed_compiler_recovery_refuses_revoked_original_permit(self):
        self.compiler_unsealed_interruption("heartbeat", fault="permit")

    def test_sealed_compiler_result_recovers_after_seal_or_source_interruption(self):
        for point in ("seal", "source"):
            with self.subTest(point=point):
                self.compiler_sealed_interruption(point=point)

    def test_sealed_compiler_recovery_refuses_changed_verified_work(self):
        self.compiler_sealed_interruption(point="seal", fault="work")

    def test_sealed_compiler_recovery_refuses_foreign_transaction_seal(self):
        self.compiler_sealed_interruption(point="seal", fault="seal")

    def test_terminal_receipt_outage_recovers_exact_postcheck_without_second_migration(self):
        from unittest.mock import patch
        from runtime_sync_workshop import engine as engine_module
        original = AuthorityMigration.apply
        observed = []
        def receipt_outage(owner, tx):
            root, state = owner.engine._load_transaction(tx)
            if owner._work_package(root, state)["package_sha256"] != state["verified_work_package_sha256"]:
                return original(owner, tx)  # Preserve the original candidate-tamper refusal.
            writer = engine_module.atomic_write_json
            def fail_receipt(path, payload):
                if path.name.startswith("AUTHORITY-POSTCHECK_"):
                    raise OSError("isolated compiler terminal receipt outage")
                return writer(path, payload)
            with patch.object(engine_module, "atomic_write_json", side_effect=fail_receipt):
                with self.assertRaises(WorkshopError) as caught:
                    original(owner, tx)
            self.assertEqual(caught.exception.code, "TX_TERMINAL_RECEIPT_PENDING")
            _, after = owner.engine._load_transaction(tx)
            self.assertEqual(after["state"], "POSTCHECK_VERIFIED")
            self.assertEqual(after["terminal_receipt"]["status"], "PENDING")
            self.assertTrue(owner.engine.lease_path.exists())
            assert_projected(owner.engine, tx)
            with patch.object(owner, "apply", side_effect=AssertionError("Recovery cannot migrate twice")):
                result = owner.engine.recover(tx)
            _, final = owner.engine._load_transaction(tx)
            self.assertEqual(final["terminal_receipt"]["status"], "COMMITTED")
            self.assertFalse(owner.engine.lease_path.exists())
            assert_projected(owner.engine, tx)
            observed.append(result["terminal_receipt_id"])
            return result
        case = AuthorityMigrationTests("test_header_add_remove_and_source_change_migrate_atomically")
        with patch.object(AuthorityMigration, "apply", receipt_outage):
            case.test_header_add_remove_and_source_change_migrate_atomically()
        self.assertEqual(len(observed), 1)

    def test_header_add_remove_and_source_change_migrate_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            (live / "src").mkdir(parents=True)
            (live / "include").mkdir()
            main = live / "src" / "main.cpp"
            old_header = live / "include" / "a.hpp"
            main.write_text('#include <a.hpp>\nint main(){return a();}\n', encoding="utf-8")
            old_header.write_text('#pragma once\ninline int a(){return 1;}\n', encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(
                project_root=live,
                workspace=workspace,
                compile_commands=write_compile_commands(live, [main]),
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            self.assertIs(engine.config.raw["transaction_documents_required"], True)
            engine.seal()

            candidate = root / "candidate"
            shutil.copytree(live, candidate)
            candidate_main = candidate / "src" / "main.cpp"
            candidate_main.write_text('#include <c.hpp>\nint main(){return c();}\n', encoding="utf-8")
            (candidate / "include" / "a.hpp").unlink()
            new_header = candidate / "include" / "c.hpp"
            new_header.write_text('#pragma once\ninline int c(){return 3;}\n', encoding="utf-8")
            compile_commands = write_compile_commands(candidate, [candidate_main])

            migration = AuthorityMigration(engine)
            checkout = migration.checkout(
                candidate_root=candidate,
                compile_commands=compile_commands,
                purpose="Replace one governed header through explicit authority migration",
            )
            self.assertEqual(checkout["added_headers"], ["include/c.hpp"])
            self.assertEqual(checkout["removed_headers"], ["include/a.hpp"])
            self.assertEqual(checkout["content_changed"], ["src/main.cpp"])
            assert_projected(engine, checkout["transaction_id"])
            work = Path(checkout["work_directory"])
            edit_semantic_blueprint(
                work,
                "The authoritative translation unit is `src/main.cpp`.",
                "The authoritative translation unit is `src/main.cpp`; its migrated dependency is explicitly reviewed.",
            )
            fill_review(
                Path(checkout["review"]),
                {
                    "src/main.cpp": "updated",
                    "include/c.hpp": "created",
                    "include/a.hpp": "removed",
                },
            )
            # A body-only review sentence must not turn a topology migration
            # into an escape from the ordinary metadata maintenance duty.
            with self.assertRaises(WorkshopError) as raised:
                migration.prepare(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "METADATA_HEADER_UNCHANGED")
            self.assertEqual(main.read_text(encoding="utf-8"), '#include <a.hpp>\nint main(){return a();}\n')
            source_blueprint = next(
                path for path in (work / "blueprints").glob("PROJECT_CODE_*.md")
                if 'target_source_file = "src/main.cpp"' in path.read_text(encoding="utf-8")
            )
            reviewed_text, count = re.subn(
                r"(?m)^capsule\s*=.*$",
                lambda _match: "capsule = " + json.dumps("The src/main.cpp translation unit includes c.hpp and returns c(), implemented by the new header as 3."),
                source_blueprint.read_text(encoding="utf-8"), count=1,
            )
            self.assertEqual(count, 1)
            source_blueprint.write_bytes(reviewed_text.encode("utf-8"))
            _metadata_plan, audit_path = complete_metadata_audit(engine, checkout, update_capsules=False)
            prepared = migration.prepare(checkout["transaction_id"])
            self.assertEqual(prepared["state"], "PREPARED")
            assert_projected(engine, checkout["transaction_id"])
            with self.assertRaises(WorkshopError) as raised:
                migration.verify(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "METADATA_CHECK_REQUIRED")
            aftercheck = engine.metadata_check(checkout["transaction_id"])
            self.assertTrue(aftercheck["verified"])
            verified = migration.verify(checkout["transaction_id"])
            self.assertEqual(verified["state"], "SHADOW_VERIFIED")
            assert_projected(engine, checkout["transaction_id"])
            reviewed_audit = audit_path.read_bytes()
            audit_path.write_bytes(reviewed_audit + b"\n")
            with self.assertRaises(WorkshopError) as raised:
                migration.apply(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "WORK_CHANGED_AFTER_VERIFY")
            audit_path.write_bytes(reviewed_audit)
            machine_verify = work.parent / "work" / "machine-verify"
            verify_config = json.loads((machine_verify / "workshop.config.json").read_text(encoding="utf-8"))
            verify_intake = json.loads((machine_verify / "project-intake.json").read_text(encoding="utf-8"))
            import hashlib
            canonical = json.dumps(verify_config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(
                verify_intake["workshop_config_sha256"],
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
            applied = migration.apply(checkout["transaction_id"])
            self.assertEqual(applied["state"], "POSTCHECK_VERIFIED")
            assert_projected(engine, checkout["transaction_id"])
            self.assertTrue(applied["bit_exact"])
            self.assertFalse((live / "include" / "a.hpp").exists())
            self.assertEqual((live / "include" / "c.hpp").read_bytes(), new_header.read_bytes())
            self.assertEqual((live / "src" / "main.cpp").read_bytes(), candidate_main.read_bytes())
            source_index = (workspace / "docs" / "PROJECT_SOURCE_INDEX.md").read_text(encoding="utf-8")
            self.assertIn("include/c.hpp", source_index)
            self.assertNotIn("include/a.hpp` ←", source_index)
            old_docs = [
                path for path in (workspace / "code").glob("PROJECT_HEADER_*.md")
                if 'target_header_file = "include/a.hpp"' in path.read_text(encoding="utf-8")
            ]
            self.assertEqual(len(old_docs), 1)
            self.assertIn('state = "superseded"', old_docs[0].read_text(encoding="utf-8"))
            self.assertTrue(engine.status()["verified"])
            live_config = json.loads((workspace / ".kairos" / "workshop.config.json").read_text(encoding="utf-8"))
            live_intake = json.loads((workspace / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            canonical = json.dumps(live_config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(
                live_intake["workshop_config_sha256"],
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )

    def test_translation_unit_rename_requires_project_build_authority_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            (live / "src").mkdir(parents=True)
            main = live / "src" / "main.cpp"
            helper = live / "src" / "helper.cpp"
            main.write_text("int helper();\nint main(){return helper();}\n", encoding="utf-8")
            helper.write_text("int helper(){return 7;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(
                project_root=live, workspace=workspace,
                compile_commands=write_compile_commands(live, [main, helper]),
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()
            candidate = root / "candidate"
            shutil.copytree(live, candidate)
            old = candidate / "src" / "helper.cpp"
            worker = candidate / "src" / "worker.cpp"
            old.rename(worker)
            compile_commands = write_compile_commands(candidate, [candidate / "src" / "main.cpp", worker])
            migration = AuthorityMigration(engine)
            with self.assertRaises(WorkshopError) as raised:
                migration.checkout(
                    candidate_root=candidate, compile_commands=compile_commands,
                    purpose="Rename one compiler-recorded translation unit with explicit authority migration",
                )
            self.assertEqual(raised.exception.code, "BUILD_AUTHORITY_MIGRATION_REQUIRED")
            self.assertTrue((live / "src" / "helper.cpp").is_file())
            self.assertFalse((live / "src" / "worker.cpp").exists())
            self.assertTrue(engine.status()["verified"])

    def test_candidate_checkout_path_does_not_create_false_compiler_authority_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            (live / "src").mkdir(parents=True)
            main = live / "src" / "main.cpp"
            main.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(
                project_root=live,
                workspace=workspace,
                compile_commands=write_compile_commands(live, [main]),
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()
            candidate = root / "candidate-different-absolute-root"
            shutil.copytree(live, candidate)
            candidate_main = candidate / "src" / "main.cpp"
            candidate_main.write_text("// comment only\nint main(){return 0;}\n", encoding="utf-8")
            compile_commands = write_compile_commands(candidate, [candidate_main])
            migration = AuthorityMigration(engine)
            with self.assertRaises(WorkshopError) as raised:
                migration.checkout(
                    candidate_root=candidate,
                    compile_commands=compile_commands,
                    purpose="Reject a normal code-only change from authority migration",
                )
            self.assertEqual(raised.exception.code, "AUTHORITY_MIGRATION_NOT_REQUIRED")

    def test_compiler_flag_change_requires_project_build_authority_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            (live / "src").mkdir(parents=True)
            main = live / "src" / "main.cpp"
            main.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            baseline_commands = write_compile_commands(live, [main])
            initialize_project(
                project_root=live, workspace=workspace, compile_commands=baseline_commands,
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()
            candidate = root / "candidate"
            shutil.copytree(live, candidate)
            candidate_main = candidate / "src" / "main.cpp"
            build = candidate / "build"
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(candidate_main),
                "arguments": ["c++", "-DPORTABLE_MODE=1", "-c", str(candidate_main)],
            }]), encoding="utf-8")
            migration = AuthorityMigration(engine)
            with self.assertRaises(WorkshopError) as raised:
                migration.checkout(
                    candidate_root=candidate, compile_commands=compile_commands,
                    purpose="Change one compiler flag through explicit authority migration",
                )
            self.assertEqual(raised.exception.code, "BUILD_AUTHORITY_MIGRATION_REQUIRED")
            self.assertTrue(engine.status()["verified"])


if __name__ == "__main__":
    unittest.main()
