from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kickstart.cmake import configure_compile_commands
from kickstart.errors import KickstartError


class CMakeIsolationTests(unittest.TestCase):
    def test_project_local_cmake_wrapper_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kairos-cmake-tool-boundary-test-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\nproject(KairosIsolation LANGUAGES CXX)\n",
                encoding="utf-8",
            )
            tools = project / "tools"
            tools.mkdir()
            wrapper = tools / "cmake.exe"
            wrapper.write_text("not an executable", encoding="utf-8")
            with self.assertRaises(KickstartError) as caught:
                configure_compile_commands(
                    project,
                    project / "CMakeLists.txt",
                    cmake_executable=str(wrapper),
                )
            self.assertEqual(caught.exception.code, "CMAKE_EXECUTABLE_PROJECT_LOCAL")

    @unittest.skipUnless(shutil.which("cmake"), "cmake is required for isolation regression")
    def test_configure_time_source_write_hits_clone_not_governed_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kairos-cmake-isolation-test-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(KairosIsolation LANGUAGES CXX)\n"
                "file(WRITE \"${CMAKE_SOURCE_DIR}/MUTATED_BY_CMAKE.txt\" \"clone-only\")\n"
                "add_executable(kairos_isolation main.cpp)\n",
                encoding="utf-8",
            )
            before = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}

            configured = configure_compile_commands(project, project / "CMakeLists.txt")

            after = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((project / "MUTATED_BY_CMAKE.txt").exists())
            payload = json.loads(configured.compile_commands.read_text(encoding="utf-8"))
            self.assertTrue(payload)
            raw = configured.compile_commands.read_text(encoding="utf-8")
            self.assertIn(
                str((project / "main.cpp").resolve()).replace("\\", "/"),
                raw.replace("\\", "/"),
            )
            self.assertNotIn("kairos-cmake-source-", raw)
            if configured.temporary_build:
                shutil.rmtree(configured.build_directory, ignore_errors=True)

    @unittest.skipUnless(shutil.which("cmake"), "cmake is required for isolation regression")
    def test_build_directory_inside_governed_project_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kairos-cmake-live-build-test-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            (project / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(KairosIsolation LANGUAGES CXX)\n"
                "add_executable(kairos_isolation main.cpp)\n",
                encoding="utf-8",
            )
            with self.assertRaises(KickstartError) as caught:
                configure_compile_commands(
                    project,
                    project / "CMakeLists.txt",
                    build_directory=project / "build",
                )
            self.assertEqual(caught.exception.code, "CMAKE_BUILD_DIRECTORY_LIVE")
            self.assertFalse((project / "build").exists())


if __name__ == "__main__":
    unittest.main()
