from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from kickstart.universal_init import initialize_universal_markdown


ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = ROOT / "workshop" / "src"
if str(WORKSHOP_SRC) not in sys.path:
    sys.path.insert(0, str(WORKSHOP_SRC))

from runtime_sync_workshop.corpus import build_corpus_manifest  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_UW",
            "title": "Universal Workshop",
            "objective": "Govern source and Markdown as one verified transition.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_UW_01",
                "title": "Universal transaction",
                "objective": "Complete a verified universal Workshop mutation.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_UW_001",
                    "description": "Source and Markdown remain bit-exact after Workshop apply.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck receipt.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_UW_001",
            "title": "Verify universal Workshop",
            "objective": "Run one source mutation through the sealed Workshop path.",
            "milestone": "MILESTONE_UW_01",
            "criteria": ["CRIT_UW_001"],
        },
    }


class UniversalWorkshopRoundtripTests(unittest.TestCase):
    def _roundtrip(
        self,
        *,
        project: Path,
        workspace: Path,
        source_relative: str,
        changed_text: str,
        compile_commands: Path | None = None,
    ) -> dict:
        result = initialize_universal_markdown(
            project_root=project,
            workspace=workspace,
            compile_commands=compile_commands,
            project_spec=_project_spec(),
        )
        self.assertEqual(result["state"], "VERIFIED_PENDING_SEAL")
        self.assertTrue(result["corpus"]["verified"])

        engine = WorkshopEngine(Path(result["workshop_config"]))
        from runtime_sync_workshop.tx_documents import assert_projected
        self.assertIs(engine.config.raw["transaction_documents_required"], True)
        seal = engine.seal()
        self.assertIn("package_sha256", seal)
        checkout = engine.checkout(
            [source_relative],
            purpose=f"Update {source_relative} through universal Workshop verification",
        )
        transaction_id = checkout["transaction_id"]
        assert_projected(engine, transaction_id)
        work_source = Path(checkout["work_directory"]) / source_relative
        # Keep the test input byte-exact on Windows.  Path.write_text's default
        # newline translation would otherwise turn the requested LF payload
        # into CRLF before Workshop can prove its exact copy semantics.
        work_source.write_bytes(changed_text.encode("utf-8"))

        review_path = Path(checkout["metadata_review"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        selected = False
        for entry in review["entries"]:
            if entry["source"] == source_relative:
                entry.update({
                    "metadata_impact": "none",
                    "reason": "Mechanical exact-mirror update; no semantic metadata claim exists.",
                    "reviewer": "KAIROS_TEST",
                    "reviewed_at": "2026-09-09T00:00:00Z",
                })
                selected = True
        self.assertTrue(selected)
        review_path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        complete_metadata_audit(engine, checkout)
        prepared = engine.prepare(transaction_id)
        self.assertEqual(prepared["state"], "PREPARED")
        assert_projected(engine, transaction_id)
        engine.metadata_check(transaction_id)
        verified = engine.verify(transaction_id)
        self.assertEqual(verified["state"], "SHADOW_VERIFIED")
        assert_projected(engine, transaction_id)
        receipt = engine.apply(transaction_id)
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        assert_projected(engine, transaction_id)
        self.assertTrue(receipt["bit_exact"])

        live_raw = (project / source_relative).read_bytes()
        self.assertEqual(live_raw, changed_text.encode("utf-8"))
        manifest = build_corpus_manifest(engine.config)
        self.assertTrue(manifest["verified"], manifest["issues"])
        record = manifest["records"][source_relative]
        self.assertEqual(record["source"]["sha256"], hashlib.sha256(live_raw).hexdigest())
        blueprint = workspace / "code" / record["blueprint"]["filename"]
        self.assertTrue(blueprint.is_file())
        self.assertIn(hashlib.sha256(live_raw).hexdigest().upper(), blueprint.read_text(encoding="utf-8"))
        return result

    def test_python_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("value = 1\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="app.py",
                changed_text="value = 2\nprint(value)\n",
            )

    def test_typescript_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="app.ts",
                changed_text="export const value = 2;\nexport const next = value + 1;\n",
            )

    def test_rust_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "lib.rs").write_text("pub fn value() -> i32 { 1 }\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="src/lib.rs",
                changed_text="pub fn value() -> i32 { 2 }\npub fn next() -> i32 { value() + 1 }\n",
            )

    def test_mixed_cpp_python_keeps_compiler_header_authority_and_edits_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            cpp = project / "src" / "main.cpp"
            header = project / "src" / "api.hpp"
            python = project / "src" / "helper.py"
            cpp.write_text('#include "api.hpp"\nint main() { return api(); }\n', encoding="utf-8")
            header.write_text("#pragma once\ninline int api() { return 0; }\n", encoding="utf-8")
            python.write_text("def helper():\n    return 1\n", encoding="utf-8")
            compile_commands = project / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(project),
                "file": str(cpp),
                "arguments": ["c++", "-I", str(project / "src"), "-c", str(cpp)],
            }]), encoding="utf-8")

            result = self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="src/helper.py",
                changed_text="def helper():\n    return 2\n",
                compile_commands=compile_commands,
            )
            self.assertEqual(result["headers"], 1)
            self.assertEqual(set(result["ecosystems"]), {"c_family", "python"})
            header_docs = list((root / "workspace" / "code").glob("PROJECT_HEADER_*.md"))
            self.assertEqual(len(header_docs), 1)
            self.assertIn("src/api.hpp", header_docs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
