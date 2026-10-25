from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kickstart.errors import KickstartError
from kickstart.universal import detect_project, render_markdown_corpus
from kickstart.universal_init import initialize_universal_markdown, survey_universal_sources


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_UNIVERSAL",
            "title": "Universal intake",
            "objective": "Establish a governed multi-language source baseline.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_UNIVERSAL_01",
                "title": "Initial universal baseline",
                "objective": "Materialize exact source evidence for the detected project.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_UNIVERSAL_001",
                    "description": "Initial source membership and Markdown evidence are exact.",
                    "state": "active",
                    "evidence_required": "Universal intake authority and source Markdown.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_UNIVERSAL_001",
            "title": "Materialize universal source baseline",
            "objective": "Create the exact initial multi-language Markdown mirror.",
            "milestone": "MILESTONE_UNIVERSAL_01",
            "criteria": ["CRIT_UNIVERSAL_001"],
        },
    }


class UniversalDetectionTests(unittest.TestCase):
    def test_detects_multiple_ecosystems_and_excludes_vendor_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "package.json").write_text("{}\n", encoding="utf-8")
            (root / "ui.ts").write_text("export const n = 1;\n", encoding="utf-8")
            (root / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text("pub fn x() {}\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "hidden.ts").write_text("bad\n", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "hidden.py").write_text("bad\n", encoding="utf-8")

            result = detect_project(root)
            names = {row["ecosystem"] for row in result["ecosystems"]}
            self.assertEqual(names, {"python", "javascript_typescript", "rust"})
            self.assertTrue(result["mixed"])
            counts = {row["ecosystem"]: row["source_files"] for row in result["ecosystems"]}
            self.assertEqual(counts["python"], 1)
            self.assertEqual(counts["javascript_typescript"], 1)
            self.assertEqual(counts["rust"], 1)

    def test_c_family_requires_compiler_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                survey_universal_sources(root)
            self.assertEqual(raised.exception.code, "C_FAMILY_COMPILER_AUTHORITY_REQUIRED")

    def test_mixed_cpp_python_uses_compiler_membership_only_for_cpp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            cpp = root / "src" / "main.cpp"
            py = root / "src" / "helper.py"
            header = root / "src" / "api.hpp"
            cpp.write_text('#include "api.hpp"\nint main(){return 0;}\n', encoding="utf-8")
            py.write_text("def helper():\n    return 1\n", encoding="utf-8")
            header.write_text("#pragma once\n", encoding="utf-8")
            compile_commands = root / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(root),
                "file": str(cpp),
                "arguments": ["c++", "-c", str(cpp)],
            }]), encoding="utf-8")

            survey = survey_universal_sources(root, compile_commands=compile_commands)
            paths = [source.relative_path for source in survey.sources]
            self.assertEqual(paths, ["src/helper.py", "src/main.cpp"])
            self.assertNotIn("src/api.hpp", paths)
            ecosystems = {source.relative_path: source.ecosystem for source in survey.sources}
            self.assertEqual(ecosystems["src/main.cpp"], "c_family")
            self.assertEqual(ecosystems["src/helper.py"], "python")


class UniversalMarkdownTests(unittest.TestCase):
    def test_exact_source_hash_and_ledger_are_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "app.py"
            source.write_text("x = 1\nprint(x)\n", encoding="utf-8")
            survey = survey_universal_sources(root)
            documents = render_markdown_corpus(
                survey,
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_UNIVERSAL",
                milestone_id="MILESTONE_UNIVERSAL_01",
                task_id="TASK_UNIVERSAL_001",
                updated_at="2026-09-09T00:00:00Z",
            )
            code_documents = [text for path, text in documents.items() if path.startswith("code/")]
            self.assertEqual(len(code_documents), 1)
            text = code_documents[0]
            self.assertIn('target_source_file = "app.py"', text)
            self.assertIn('ecosystem = "python"', text)
            self.assertIn("C:0001 x = 1", text)
            self.assertIn("C:0002 print(x)", text)
            self.assertIn(survey.sources[0].sha256.upper(), text)

    def test_initial_materialization_writes_workspace_not_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            workspace = root / "workspace"
            project.mkdir()
            source = project / "app.py"
            source.write_text("print('before')\n", encoding="utf-8")
            before = source.read_bytes()

            result = initialize_universal_markdown(
                project_root=project,
                workspace=workspace,
                project_spec=_project_spec(),
            )

            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(result["state"], "VERIFIED_PENDING_SEAL")
            self.assertTrue(result["corpus"]["verified"])
            self.assertTrue(Path(result["workshop_config"]).is_file())
            self.assertTrue((workspace / ".kairos" / "universal-intake.json").is_file())
            self.assertTrue((workspace / "docs" / "PROJECT_SOURCE_INDEX.md").is_file())
            self.assertEqual(len(list((workspace / "code").glob("PROJECT_CODE_*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
