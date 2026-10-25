from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
HARNESS = PACKAGE_ROOT / "kairos" / "kairos_harness"
WORKSHOP_SRC = PACKAGE_ROOT / "workshop" / "src"
for value in (PACKAGE_ROOT, HARNESS, WORKSHOP_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.binding import initialize_project  # noqa: E402
from kairos.graph import query_graph_context  # noqa: E402
from kairos.query import compile_query  # noqa: E402
from runtime_sync_workshop.corpus import build_corpus_manifest  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


def project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_EDGE",
            "title": "Include edge authority",
            "objective": "Keep direct compiler include topology synchronized with source and KAIROS search.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_EDGE_01",
                "title": "Verified include graph",
                "objective": "Prove direct include topology survives intake and Workshop changes.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_EDGE_001",
                    "description": "Direct include edges, digest, Markdown and SQLite projection stay identical.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck and database edge projection.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_EDGE_001",
            "title": "Verify direct include topology",
            "objective": "Exercise a topology change invisible to transitive owner sets.",
            "milestone": "MILESTONE_EDGE_01",
            "criteria": ["CRIT_EDGE_001"],
        },
    }


def compile_commands(project: Path, source: Path) -> Path:
    build = project / "build"
    build.mkdir()
    path = build / "compile_commands.json"
    path.write_text(json.dumps([{
        "directory": str(build),
        "file": str(source),
        "arguments": ["c++", "-I", str(project / "include"), "-c", str(source)],
    }]), encoding="utf-8")
    return path


def set_review(path: Path, source: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        if entry["source"] == source:
            entry.update(
                metadata_impact="updated",
                reason="Direct include topology changed and the implementation dependency metadata was reviewed",
                reviewer="include-edge-regression",
                reviewed_at="2026-09-10T10:15:00Z",
            )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class IncludeEdgeAuthorityTests(unittest.TestCase):
    def make_project(self, root: Path) -> tuple[Path, Path, Path]:
        project = root / "project"
        (project / "src").mkdir(parents=True)
        (project / "include").mkdir()
        main = project / "src" / "main.cpp"
        a = project / "include" / "a.hpp"
        b = project / "include" / "b.hpp"
        main.write_text('#include <a.hpp>\nint main(){return a();}\n', encoding="utf-8")
        a.write_text('#pragma once\n#include <b.hpp>\ninline int a(){return b();}\n', encoding="utf-8")
        b.write_text('#pragma once\ninline int b(){return 1;}\n', encoding="utf-8")
        return project, main, compile_commands(project, main)

    def test_initial_intake_projects_exact_direct_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, _, commands = self.make_project(root)
            workspace = root / "workspace"
            result = initialize_project(
                project_root=project,
                workspace=workspace,
                compile_commands=commands,
                project_spec=project_spec(),
            )
            self.assertTrue(result["verified"])
            authority = json.loads((workspace / ".kairos" / "project-intake" / "include-edges.json").read_text(encoding="utf-8"))
            self.assertEqual(authority["edge_count"], 2)
            self.assertRegex(authority["topology_sha256"], r"^[0-9a-f]{64}$")
            pairs = {(row["including_file"], row["resolved_target"]) for row in authority["edges"]}
            self.assertEqual(pairs, {("src/main.cpp", "include/a.hpp"), ("include/a.hpp", "include/b.hpp")})
            self.assertTrue(all(row["compiled_root"] == "src/main.cpp" for row in authority["edges"]))
            index = (workspace / "docs" / "PROJECT_SOURCE_INDEX.md").read_text(encoding="utf-8")
            self.assertIn('<a id="s-include-edges"></a>', index)
            self.assertIn(authority["topology_sha256"], index)
            connection = sqlite3.connect(workspace / ".kairos" / "kairos.db")
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    "SELECT including_file,resolved_target FROM compiler_include_edges ORDER BY ordinal"
                ).fetchall()
                self.assertEqual([(row["including_file"], row["resolved_target"]) for row in rows], [
                    ("include/a.hpp", "include/b.hpp"),
                    ("src/main.cpp", "include/a.hpp"),
                ])
                frame = compile_query('graph "src/main.cpp" "include/a.hpp"')
                context = query_graph_context(connection, frame, limit=20)
                self.assertTrue(any(fact["table"] == "compiler_include_edges" for fact in context["facts"]))
            finally:
                connection.close()

    def test_direct_edge_change_with_same_transitive_owners_is_transactional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, main, commands = self.make_project(root)
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=commands, project_spec=project_spec())
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            baseline = build_corpus_manifest(engine.config)
            self.assertTrue(baseline["verified"], baseline["issues"])
            baseline_digest = baseline["authority"]["include_topology_sha256"]
            baseline_owners = baseline["authority"]["include_owners"]
            engine.seal()

            checkout = engine.checkout(["src/main.cpp"], purpose="Add a redundant direct edge while preserving transitive reachability")
            work = Path(checkout["work_directory"])
            (work / "src" / "main.cpp").write_text(
                '#include <a.hpp>\n#include <b.hpp>\nint main(){return a();}\n', encoding="utf-8"
            )
            baseline_manifest = json.loads((work.parent / "baseline" / "manifest.json").read_text(encoding="utf-8"))
            name = baseline_manifest["records"]["src/main.cpp"]["blueprint"]["filename"]
            blueprint = work / "blueprints" / name
            blueprint.write_text(
                blueprint.read_text(encoding="utf-8").replace(
                    "The authoritative translation unit is `src/main.cpp`.",
                    "The authoritative translation unit is `src/main.cpp`; direct include topology is reviewed as compiler-observed dependency evidence.",
                ),
                encoding="utf-8",
            )
            set_review(Path(checkout["metadata_review"]), "src/main.cpp")
            complete_metadata_audit(engine, checkout)
            prepared = engine.prepare(checkout["transaction_id"])
            self.assertEqual(prepared["state"], "PREPARED")
            state = json.loads((work.parent / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["authority_documents"], ["docs/PROJECT_SOURCE_INDEX.md"])
            self.assertIn("project-intake/include-edges.json", state["machine_authority_files"])
            self.assertTrue(state["topology_authority"]["direct_edge_changed"])
            self.assertEqual(state["topology_authority"]["updated_header_blueprints"], [])
            self.assertNotEqual(
                state["topology_authority"]["baseline_topology_sha256"],
                state["topology_authority"]["work_topology_sha256"],
            )
            self.assertEqual(main.read_text(encoding="utf-8"), '#include <a.hpp>\nint main(){return a();}\n')

            engine.metadata_check(checkout["transaction_id"])
            verified = engine.verify(checkout["transaction_id"])
            self.assertEqual(verified["state"], "SHADOW_VERIFIED")
            self.assertEqual(main.read_text(encoding="utf-8"), '#include <a.hpp>\nint main(){return a();}\n')
            receipt = engine.apply(checkout["transaction_id"])
            self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
            self.assertTrue(receipt["bit_exact"])

            post = build_corpus_manifest(engine.config)
            self.assertTrue(post["verified"], post["issues"])
            self.assertEqual(post["authority"]["include_owners"], baseline_owners)
            self.assertNotEqual(post["authority"]["include_topology_sha256"], baseline_digest)
            self.assertEqual(len(post["authority"]["include_edges"]), 3)
            self.assertTrue(any(
                row["including_file"] == "src/main.cpp" and row["resolved_target"] == "include/b.hpp"
                for row in post["authority"]["include_edges"]
            ))
            connection = sqlite3.connect(workspace / ".kairos" / "kairos.db")
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT include_line,compiled_root FROM compiler_include_edges WHERE including_file=? AND resolved_target=?",
                    ("src/main.cpp", "include/b.hpp"),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(int(row["include_line"]), 2)
                self.assertEqual(row["compiled_root"], "src/main.cpp")
                frame = compile_query('graph "src/main.cpp" "include/b.hpp"')
                context = query_graph_context(connection, frame, limit=20)
                edge_facts = [fact for fact in context["facts"] if fact["table"] == "compiler_include_edges"]
                self.assertTrue(edge_facts)
                self.assertTrue(any(fact["row"]["resolved_target"] == "include/b.hpp" for fact in edge_facts))
            finally:
                connection.close()

    def test_machine_edge_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, _, commands = self.make_project(root)
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=commands, project_spec=project_spec())
            engine = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            path = workspace / ".kairos" / "project-intake" / "include-edges.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["topology_sha256"] = "0" * 64
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaises(WorkshopError) as raised:
                build_corpus_manifest(engine.config)
            self.assertEqual(raised.exception.code, "INCLUDE_EDGE_AUTHORITY_DIGEST_MISMATCH")


if __name__ == "__main__":
    unittest.main()
