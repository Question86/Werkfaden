from __future__ import annotations

import json
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
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


def project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_PORTABLE",
            "title": "Portable project",
            "objective": "Maintain a small C++ project with synchronized implementation evidence.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_PORTABLE_01",
                "title": "Verified baseline",
                "objective": "Create the baseline and complete one governed change.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_PORTABLE_001",
                    "description": "Code, documentation and projection remain synchronized.",
                    "state": "active",
                    "evidence_required": "Compiler intake and Workshop postcheck receipts.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_PORTABLE_001",
            "title": "Establish portable baseline",
            "objective": "Bind the real codebase and execute a synchronized Workshop change.",
            "milestone": "MILESTONE_PORTABLE_01",
            "criteria": ["CRIT_PORTABLE_001"],
        },
    }


def write_compile_commands(project: Path, sources: list[Path]) -> Path:
    build = project / "build"
    build.mkdir(exist_ok=True)
    path = build / "compile_commands.json"
    path.write_text(json.dumps([
        {
            "directory": str(build),
            "file": str(source),
            "arguments": ["c++", "-I", str(project / "include"), "-c", str(source)],
        }
        for source in sources
    ]), encoding="utf-8")
    return path


def set_review(path: Path, source: str, *, impact: str, reason: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        if entry["source"] == source:
            entry.update(
                metadata_impact=impact,
                reason=reason,
                reviewer="integration-test",
                reviewed_at="2026-09-07T20:00:00Z",
            )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class KickstartWorkshopIntegrationTests(unittest.TestCase):
    def test_first_translation_unit_and_header_patches_reach_bit_exact_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            source = project / "src" / "main.cpp"
            header = project / "include" / "api.hpp"
            source.write_text('#include <api.hpp>\nint main(){return api();}\n', encoding="utf-8")
            header.write_text('#pragma once\ninline int api(){return 0;}\n', encoding="utf-8")
            compile_commands = write_compile_commands(project, [source])
            workspace = root / "workspace"
            intake = initialize_project(
                project_root=project,
                workspace=workspace,
                compile_commands=compile_commands,
                project_spec=project_spec(),
            )
            self.assertTrue(intake["verified"])
            current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["active_goal"], "GOAL_PORTABLE")
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()

            checkout = engine.checkout(["src/main.cpp"], purpose="First synchronized source patch")
            work = Path(checkout["work_directory"])
            (work / "src" / "main.cpp").write_text(
                "// comment-only audit change\n" + (work / "src" / "main.cpp").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            set_review(
                Path(checkout["metadata_review"]),
                "src/main.cpp",
                impact="none",
                reason="Comment-only source edit leaves semantic implementation metadata unchanged",
            )
            complete_metadata_audit(engine, checkout)
            engine.prepare(checkout["transaction_id"])
            engine.metadata_check(checkout["transaction_id"])
            engine.verify(checkout["transaction_id"])
            source_apply = engine.apply(checkout["transaction_id"])
            self.assertTrue(source_apply["bit_exact"])
            self.assertEqual(source_apply["state"], "POSTCHECK_VERIFIED")
            project_intake = json.loads((workspace / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            source_row = next(
                row for row in project_intake["translation_units"]
                if row["relative_path"] == "src/main.cpp"
            )
            import hashlib
            self.assertEqual(
                source_row["facts"]["sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(source_row["facts"]["bytes"], source.stat().st_size)

            checkout = engine.checkout(["include/api.hpp"], purpose="First synchronized header patch")
            work = Path(checkout["work_directory"])
            (work / "include" / "api.hpp").write_text(
                "// header comment\n" + (work / "include" / "api.hpp").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            set_review(
                Path(checkout["metadata_review"]),
                "include/api.hpp",
                impact="none",
                reason="Comment-only header edit leaves semantic implementation metadata unchanged",
            )
            complete_metadata_audit(engine, checkout)
            engine.prepare(checkout["transaction_id"])
            engine.metadata_check(checkout["transaction_id"])
            engine.verify(checkout["transaction_id"])
            header_apply = engine.apply(checkout["transaction_id"])
            self.assertTrue(header_apply["bit_exact"])
            self.assertTrue(engine.status()["verified"])
            project_intake = json.loads((workspace / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertEqual(
                project_intake["include_closure"]["include/api.hpp"]["facts"]["sha256"],
                hashlib.sha256(header.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                project_intake["include_closure"]["include/api.hpp"]["facts"]["bytes"],
                header.stat().st_size,
            )

    def test_owner_only_include_change_updates_source_index_in_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            main = project / "src" / "main.cpp"
            keeper = project / "src" / "keeper.cpp"
            helper = project / "src" / "helper.cpp"
            main.write_text('#include <a.hpp>\nint main(){return a();}\n', encoding="utf-8")
            keeper.write_text('#include <a.hpp>\nint keeper(){return a();}\n', encoding="utf-8")
            helper.write_text('#include <b.hpp>\nint helper(){return b();}\n', encoding="utf-8")
            (project / "include" / "a.hpp").write_text('#pragma once\ninline int a(){return 1;}\n', encoding="utf-8")
            (project / "include" / "b.hpp").write_text('#pragma once\ninline int b(){return 2;}\n', encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(
                project_root=project,
                workspace=workspace,
                compile_commands=write_compile_commands(project, [main, keeper, helper]),
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()
            before = (workspace / "docs" / "PROJECT_SOURCE_INDEX.md").read_text(encoding="utf-8")
            checkout = engine.checkout(["src/main.cpp"], purpose="Move one existing include owner")
            work = Path(checkout["work_directory"])
            (work / "src" / "main.cpp").write_text('#include <b.hpp>\nint main(){return b();}\n', encoding="utf-8")
            baseline = json.loads((work.parent / "baseline" / "manifest.json").read_text(encoding="utf-8"))
            name = baseline["records"]["src/main.cpp"]["blueprint"]["filename"]
            blueprint = work / "blueprints" / name
            blueprint.write_text(
                blueprint.read_text(encoding="utf-8").replace(
                    "The authoritative translation unit is `src/main.cpp`.",
                    "The authoritative translation unit is `src/main.cpp`; its include responsibility is reviewed with semantic dependency changes.",
                ),
                encoding="utf-8",
            )
            set_review(
                Path(checkout["metadata_review"]),
                "src/main.cpp",
                impact="updated",
                reason="The semantic include dependency changed and its implementation document was reviewed",
            )
            complete_metadata_audit(engine, checkout)
            engine.prepare(checkout["transaction_id"])
            state = json.loads((work.parent / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["authority_documents"], ["docs/PROJECT_SOURCE_INDEX.md"])
            engine.metadata_check(checkout["transaction_id"])
            engine.verify(checkout["transaction_id"])
            receipt = engine.apply(checkout["transaction_id"])
            after = (workspace / "docs" / "PROJECT_SOURCE_INDEX.md").read_text(encoding="utf-8")
            self.assertNotEqual(before, after)
            self.assertIn("PROJECT_SOURCE_INDEX", receipt["heartbeat"]["promoted"])
            self.assertIn('`include/a.hpp` ← `src/keeper.cpp`', after)
            self.assertIn('`include/b.hpp` ← `src/helper.cpp`, `src/main.cpp`', after)
            project_intake = json.loads((workspace / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertEqual(
                project_intake["include_closure"]["include/a.hpp"]["owners"],
                ["src/keeper.cpp"],
            )
            self.assertEqual(
                project_intake["include_closure"]["include/b.hpp"]["owners"],
                ["src/helper.cpp", "src/main.cpp"],
            )
            header_docs = list((workspace / "code").glob("PROJECT_HEADER_*.md"))
            a_doc = next(path for path in header_docs if 'target_header_file = "include/a.hpp"' in path.read_text(encoding="utf-8"))
            b_doc = next(path for path in header_docs if 'target_header_file = "include/b.hpp"' in path.read_text(encoding="utf-8"))
            a_text = a_doc.read_text(encoding="utf-8")
            b_text = b_doc.read_text(encoding="utf-8")
            self.assertIn("- `src/keeper.cpp`", a_text)
            self.assertNotIn("- `src/main.cpp`", a_text[a_text.index('<a id="s-ownership">'):a_text.index('<a id="s-boundary">')])
            self.assertIn("- `src/helper.cpp`", b_text)
            self.assertIn("- `src/main.cpp`", b_text)
            self.assertTrue(engine.status()["verified"])

    def test_changed_governed_header_set_fails_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            main = project / "src" / "main.cpp"
            helper = project / "src" / "helper.cpp"
            main.write_text('#include <a.hpp>\nint main(){return a();}\n', encoding="utf-8")
            helper.write_text('#include <b.hpp>\nint helper(){return b();}\n', encoding="utf-8")
            (project / "include" / "a.hpp").write_text('#pragma once\ninline int a(){return 1;}\n', encoding="utf-8")
            (project / "include" / "b.hpp").write_text('#pragma once\ninline int b(){return 2;}\n', encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(
                project_root=project,
                workspace=workspace,
                compile_commands=write_compile_commands(project, [main, helper]),
                project_spec=project_spec(),
            )
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            engine.seal()
            checkout = engine.checkout(["src/main.cpp"], purpose="Attempt a governed header-set change")
            work = Path(checkout["work_directory"])
            (work / "src" / "main.cpp").write_text('#include <b.hpp>\nint main(){return b();}\n', encoding="utf-8")
            with self.assertRaises(WorkshopError) as raised:
                engine.prepare(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "AUTHORITY_TOPOLOGY_MIGRATION_REQUIRED")
            self.assertEqual((project / "src" / "main.cpp").read_text(encoding="utf-8"), '#include <a.hpp>\nint main(){return a();}\n')


if __name__ == "__main__":
    unittest.main()
