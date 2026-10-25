from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kickstart.binding import initialize_project
from kickstart.errors import KickstartError
from kickstart.survey import survey_compile_commands


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_DEMO",
            "title": "Demo project",
            "objective": "Maintain and improve the demo application under compiler-backed evidence.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_DEMO_01",
                "title": "Initial project baseline",
                "objective": "Establish a correct governed source baseline and complete one verified change.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_DEMO_001",
                    "description": "The compiler-backed project baseline and first governed change are verified.",
                    "state": "active",
                    "evidence_required": "Compiler intake and Workshop postcheck receipts.",
                    "required_artifact_types": ["report", "code"],
                }],
            }],
        },
        "task": {
            "id": "TASK_DEMO_001",
            "title": "Establish the governed project baseline",
            "objective": "Bind the real project codebase and prepare the first synchronized Workshop change.",
            "milestone": "MILESTONE_DEMO_01",
            "criteria": ["CRIT_DEMO_001"],
        },
    }


class KickstartSurveyTests(unittest.TestCase):
    def test_duplicate_compile_records_coalesce_and_include_roots_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src" / "main.cpp"
            include = root / "include"
            source.parent.mkdir()
            include.mkdir()
            source.write_text('#include "api.hpp"\nint main() { return 0; }\n', encoding="utf-8")
            (include / "api.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = root / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(
                json.dumps([
                    {"directory": str(build), "file": str(source), "arguments": ["c++", "-I", str(include), "-c", str(source)]},
                    {"directory": str(build), "file": str(source), "arguments": ["c++", "-I" + str(include), "-c", str(source)]},
                ]),
                encoding="utf-8",
            )
            survey = survey_compile_commands(compile_commands, root)
            self.assertEqual([unit.relative_path for unit in survey.units], ["src/main.cpp"])
            self.assertEqual(len(survey.units[0].commands), 2)
            self.assertEqual([path.relative_to(root).as_posix() for path in survey.include_roots], ["include"])

    def test_windows_command_string_preserves_quoted_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kairos project ") as temporary:
            root = Path(temporary)
            source = root / "src" / "main.cpp"
            include = root / "include files"
            source.parent.mkdir()
            include.mkdir()
            source.write_text('#include "api.hpp"\n', encoding="utf-8")
            (include / "api.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = root / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            command = f'c++ -I"{include}" -c "{source}"'
            compile_commands.write_text(
                json.dumps([{"directory": str(build), "file": str(source), "command": command}]),
                encoding="utf-8",
            )
            survey = survey_compile_commands(compile_commands, root)
            self.assertEqual(survey.units[0].relative_path, "src/main.cpp")
            self.assertEqual([path.relative_to(root).as_posix() for path in survey.include_roots], ["include files"])

    def test_source_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            outside = Path(temporary) / "outside.cpp"
            outside.write_text("int x;\n", encoding="utf-8")
            build = root / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(
                json.dumps([{"directory": str(build), "file": str(outside), "arguments": ["c++", "-c", str(outside)]}]),
                encoding="utf-8",
            )
            with self.assertRaises(KickstartError) as raised:
                survey_compile_commands(compile_commands, root)
            self.assertEqual(raised.exception.code, "SOURCE_OUTSIDE_PROJECT")


class KickstartIntegrationTests(unittest.TestCase):
    def test_compiler_backed_project_is_promoted_and_workshop_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "include").mkdir()
            (project / "src" / "main.cpp").write_text('#include "api.hpp"\nint main() { return api(); }\n', encoding="utf-8")
            (project / "include" / "api.hpp").write_text("#pragma once\ninline int api() { return 0; }\n", encoding="utf-8")
            build = project / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(
                json.dumps([{"directory": str(build), "file": str(project / "src" / "main.cpp"), "arguments": ["c++", "-I", str(project / "include"), "-c", str(project / "src" / "main.cpp")]}]),
                encoding="utf-8",
            )
            result = initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertTrue(result["verified"])
            self.assertTrue(result["corpus"]["verified"])
            self.assertEqual(result["corpus"]["counts"]["issues"], 0)
            self.assertEqual(result["health"]["verdict"], "PASS")
            self.assertEqual(result["translation_units"], 1)
            self.assertEqual(result["include_headers"], 1)
            self.assertTrue((root / "workspace" / ".kairos" / "project-intake.json").is_file())
            current = json.loads((root / "workspace" / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["active_goal"], "GOAL_DEMO")
            self.assertEqual(current["active_milestone"], "MILESTONE_DEMO_01")
            self.assertEqual(current["active_task"], "TASK_DEMO_001")
            self.assertFalse((root / "workspace" / "goals" / "GOAL_KAIROS_001.json").exists())
            intake_manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertEqual(intake_manifest["project_scope"]["goal_id"], "GOAL_DEMO")
            self.assertEqual(intake_manifest["project_scope"]["task_id"], "TASK_DEMO_001")
            self.assertRegex(intake_manifest["project_scope"]["project_intent_sha256"], r"^[0-9a-f]{64}$")

    def test_root_level_translation_unit_is_represented_without_invented_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            build = project / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(
                json.dumps([{"directory": str(build), "file": str(source), "arguments": ["c++", "-c", str(source)]}]),
                encoding="utf-8",
            )
            result = initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertTrue(result["verified"])
            self.assertEqual(result["corpus"]["authority"]["translation_units"], ["main.cpp"])

    def test_fresh_project_refuses_compiler_discovery_without_project_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            build = project / "build"
            build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-c", str(source)],
            }]), encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands)
            self.assertEqual(raised.exception.code, "PROJECT_INTENT_REQUIRED")


    def test_include_resolution_preserves_compiler_root_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "z").mkdir()
            (project / "a").mkdir()
            source = project / "src" / "main.cpp"
            source.write_text('#include <api.hpp>\nint main(){return API_VALUE;}\n', encoding="utf-8")
            (project / "z" / "api.hpp").write_text("#pragma once\n#define API_VALUE 7\n", encoding="utf-8")
            (project / "a" / "api.hpp").write_text("#pragma once\n#define API_VALUE 3\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-I", str(project / "z"), "-I", str(project / "a"), "-c", str(source)],
            }]), encoding="utf-8")
            result = initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertTrue(result["verified"])
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertIn("z/api.hpp", manifest["include_closure"])
            self.assertNotIn("a/api.hpp", manifest["include_closure"])

    def test_include_resolution_keeps_per_translation_unit_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "inc_a").mkdir(); (project / "inc_b").mkdir()
            a = project / "src" / "a.cpp"; b = project / "src" / "b.cpp"
            a.write_text('#include <api.hpp>\nint a(){return A;}\n', encoding="utf-8")
            b.write_text('#include <api.hpp>\nint b(){return B;}\n', encoding="utf-8")
            (project / "inc_a" / "api.hpp").write_text("#pragma once\n#define A 1\n", encoding="utf-8")
            (project / "inc_b" / "api.hpp").write_text("#pragma once\n#define B 2\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([
                {"directory": str(build), "file": str(a), "arguments": ["c++", "-I", str(project / "inc_a"), "-c", str(a)]},
                {"directory": str(build), "file": str(b), "arguments": ["c++", "-I", str(project / "inc_b"), "-c", str(b)]},
            ]), encoding="utf-8")
            initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["include_closure"]["inc_a/api.hpp"]["owners"], ["src/a.cpp"])
            self.assertEqual(manifest["include_closure"]["inc_b/api.hpp"]["owners"], ["src/b.cpp"])

    def test_transitive_shared_header_retains_all_translation_unit_owners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True); (project / "include").mkdir()
            a = project / "src" / "a.cpp"; b = project / "src" / "b.cpp"
            for source in (a, b): source.write_text('#include "shared.hpp"\n', encoding="utf-8")
            (project / "include" / "shared.hpp").write_text('#pragma once\n#include "leaf.hpp"\n', encoding="utf-8")
            (project / "include" / "leaf.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([
                {"directory": str(build), "file": str(a), "arguments": ["c++", "-I", str(project / "include"), "-c", str(a)]},
                {"directory": str(build), "file": str(b), "arguments": ["c++", "-I", str(project / "include"), "-c", str(b)]},
            ]), encoding="utf-8")
            initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["include_closure"]["include/leaf.hpp"]["owners"], ["src/a.cpp", "src/b.cpp"])

    def test_dynamic_include_fails_closed_instead_of_creating_partial_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            source = project / "main.cpp"
            source.write_text('#define HDR "api.hpp"\n#include HDR\nint main(){return 0;}\n', encoding="utf-8")
            (project / "api.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source), "arguments": ["c++", "-I", str(project), "-c", str(source)]
            }]), encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertEqual(raised.exception.code, "DYNAMIC_INCLUDE_UNSUPPORTED")

    def test_gnu_include_categories_override_raw_token_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "systemish").mkdir(); (project / "user").mkdir()
            source = project / "src" / "main.cpp"
            source.write_text('#include <choice.hpp>\nint main(){return KAIROS_CHOICE;}\n', encoding="utf-8")
            (project / "systemish" / "choice.hpp").write_text("#define KAIROS_CHOICE 1\n", encoding="utf-8")
            (project / "user" / "choice.hpp").write_text("#define KAIROS_CHOICE 2\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            # GCC/Clang search -I before -isystem regardless of this raw token order.
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-isystem", str(project / "systemish"), "-I", str(project / "user"), "-c", str(source)],
            }]), encoding="utf-8")
            result = initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertTrue(result["verified"])
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertIn("user/choice.hpp", manifest["include_closure"])
            self.assertNotIn("systemish/choice.hpp", manifest["include_closure"])

    def test_angle_include_does_not_search_iquote_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "quote").mkdir(); (project / "user").mkdir()
            source = project / "src" / "main.cpp"
            source.write_text('#include <choice.hpp>\n', encoding="utf-8")
            (project / "quote" / "choice.hpp").write_text("#define FROM_QUOTE 1\n", encoding="utf-8")
            (project / "user" / "choice.hpp").write_text("#define FROM_USER 1\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-iquote", str(project / "quote"), "-I", str(project / "user"), "-c", str(source)],
            }]), encoding="utf-8")
            initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertIn("user/choice.hpp", manifest["include_closure"])
            self.assertNotIn("quote/choice.hpp", manifest["include_closure"])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_idirafter_project_header_does_not_override_implicit_system_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            after = project / "after"; after.mkdir()
            source = project / "main.cpp"
            source.write_text('#include <stddef.h>\nint main(){return 0;}\n', encoding="utf-8")
            (after / "stddef.h").write_text("#error KAIROS_WRONG_HEADER\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-idirafter", str(after), "-c", str(source)],
            }]), encoding="utf-8")
            result = initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertTrue(result["verified"])
            manifest = json.loads((root / "workspace" / ".kairos" / "project-intake.json").read_text(encoding="utf-8"))
            self.assertNotIn("after/stddef.h", manifest["include_closure"])

    def test_unsupported_compiler_with_idirafter_candidate_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            after = project / "after"; after.mkdir()
            source = project / "main.cpp"
            source.write_text('#include <choice.hpp>\n', encoding="utf-8")
            (after / "choice.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["unknown-cxx", "-idirafter", str(after), "-c", str(source)],
            }]), encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertEqual(raised.exception.code, "INCLUDE_SELECTION_AMBIGUOUS")

    def test_project_supplied_compiler_probe_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            after = project / "after"; after.mkdir()
            tools = project / "tools"; tools.mkdir()
            marker_file = project / "probe-ran.txt"
            compiler = tools / "g++"
            compiler.write_text(f'#!/bin/sh\ntouch "{marker_file}"\nexit 0\n', encoding="utf-8")
            compiler.chmod(0o755)
            source = project / "main.cpp"
            source.write_text('#include <choice.hpp>\n', encoding="utf-8")
            (after / "choice.hpp").write_text("#pragma once\n", encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": [str(compiler), "-idirafter", str(after), "-c", str(source)],
            }]), encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                initialize_project(project_root=project, workspace=root / "workspace", compile_commands=compile_commands, project_spec=_project_spec())
            self.assertEqual(raised.exception.code, "INCLUDE_SELECTION_AMBIGUOUS")
            self.assertFalse(marker_file.exists())

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_quoted_implicit_system_header_is_external_not_missing_project_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            source = project / "main.cpp"
            source.write_text('#include "stddef.h"\nint main(){return 0;}\n', encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-c", str(source)],
            }]), encoding="utf-8")
            result = initialize_project(
                project_root=project, workspace=root / "workspace",
                compile_commands=compile_commands, project_spec=_project_spec(),
            )
            self.assertTrue(result["verified"])
            self.assertEqual(result["include_headers"], 0)
            config = json.loads((root / "workspace" / ".kairos" / "workshop.config.json").read_text(encoding="utf-8"))
            self.assertTrue(config["translation_unit_compiler_probes"]["main.cpp"])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_missing_quoted_header_still_fails_closed_after_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            source = project / "main.cpp"
            source.write_text('#include "__kairos_missing_header_12345.h"\n', encoding="utf-8")
            build = project / "build"; build.mkdir()
            compile_commands = build / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(build), "file": str(source),
                "arguments": ["c++", "-c", str(source)],
            }]), encoding="utf-8")
            with self.assertRaises(KickstartError) as raised:
                initialize_project(
                    project_root=project, workspace=root / "workspace",
                    compile_commands=compile_commands, project_spec=_project_spec(),
                )
            self.assertEqual(raised.exception.code, "QUOTED_INCLUDE_UNRESOLVED")

    @unittest.skipUnless(shutil.which("cmake"), "cmake is not installed")
    def test_cmake_input_uses_compiler_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.15)\nproject(kickstart_test LANGUAGES CXX)\n"
                "set(CMAKE_CXX_STANDARD 11)\nadd_executable(kickstart_test src/main.cpp)\n",
                encoding="utf-8",
            )
            result = initialize_project(project_root=project, workspace=root / "workspace", cmake_file=project / "CMakeLists.txt", project_spec=_project_spec())
            self.assertTrue(result["verified"])
            self.assertEqual(result["corpus"]["counts"]["issues"], 0)
            self.assertTrue(result["manifest"].endswith("project-intake.json"))


if __name__ == "__main__":
    unittest.main()
