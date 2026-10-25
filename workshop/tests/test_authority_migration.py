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
            with self.assertRaises(WorkshopError) as raised:
                migration.verify(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "METADATA_CHECK_REQUIRED")
            aftercheck = engine.metadata_check(checkout["transaction_id"])
            self.assertTrue(aftercheck["verified"])
            verified = migration.verify(checkout["transaction_id"])
            self.assertEqual(verified["state"], "SHADOW_VERIFIED")
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
