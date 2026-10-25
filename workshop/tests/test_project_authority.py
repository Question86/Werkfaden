from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from runtime_sync_workshop.corpus import cmake_membership, header_closure
from runtime_sync_workshop.util import set_source_prefix


class ProjectAuthorityTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_source_prefix("")

    def test_cmake_membership_ignores_comments_and_keeps_quoted_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cmake = root / "authority.cmake"
            cmake.write_text(
                "set(KAIROS_TRANSLATION_UNITS\n"
                '  "src/space name.cpp"\n'
                "  # src/commented.cpp\n"
                ")\n",
                encoding="utf-8",
            )
            config = SimpleNamespace(
                cmake_file=cmake,
                cmake_source_sets=("KAIROS_TRANSLATION_UNITS",),
                cmake_extra_sources=(),
                raw={"source_authority": {"translation_unit_extensions": ["cpp"]}},
            )
            set_source_prefix("")
            self.assertEqual(cmake_membership(config), ["src/space name.cpp"])

    def test_include_closure_uses_compiler_include_roots_and_angle_includes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "include").mkdir()
            (root / "src" / "main.cpp").write_text("#include <api.hpp>\n", encoding="utf-8")
            (root / "include" / "api.hpp").write_text("#pragma once\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(root / "include",),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={},
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["src/main.cpp"])
            self.assertEqual(headers, {"include/api.hpp"})
            self.assertEqual(issues, [])

    def test_angle_include_uses_persisted_angle_roots_not_iquote_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "quote").mkdir(); (root / "user").mkdir()
            (root / "main.cpp").write_text('#include <choice.hpp>\n', encoding="utf-8")
            (root / "quote" / "choice.hpp").write_text("#define QUOTE_ONLY 1\n", encoding="utf-8")
            (root / "user" / "choice.hpp").write_text("#define USER 1\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(root / "quote", root / "user"),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(root / "quote"), str(root / "user")],
                            "angle_roots": [str(root / "user")],
                            "idirafter_roots": [],
                            "compiler_probe": None,
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, {"user/choice.hpp"})
            self.assertEqual(issues, [])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_idirafter_selection_uses_retained_compiler_probe_before_local_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            after = root / "after"; after.mkdir()
            (root / "main.cpp").write_text('#include <stddef.h>\n', encoding="utf-8")
            (after / "stddef.h").write_text("#error WRONG_HEADER\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(after,),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(after)],
                            "angle_roots": [str(after)],
                            "idirafter_roots": [str(after)],
                            "compiler_probe": {
                                "argv": [shutil.which("c++"), "-idirafter", str(after)],
                                "directory": str(root),
                            },
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual(issues, [])

    def test_idirafter_without_probe_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            after = root / "after"; after.mkdir()
            (root / "main.cpp").write_text('#include <choice.hpp>\n', encoding="utf-8")
            (after / "choice.hpp").write_text("#pragma once\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(after,),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(after)],
                            "angle_roots": [str(after)],
                            "idirafter_roots": [str(after)],
                            "compiler_probe": None,
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual([item["code"] for item in issues], ["INCLUDE_SELECTION_AMBIGUOUS"])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_quoted_implicit_system_header_uses_retained_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.cpp"
            source.write_text('#include "stddef.h"\n', encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_compiler_probes": {
                        "main.cpp": [{"argv": ["c++"], "directory": str(root)}]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual(issues, [])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_missing_quote_remains_a_workshop_issue_after_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.cpp"
            source.write_text('#include "__kairos_missing_header_12345.h"\n', encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_compiler_probes": {
                        "main.cpp": [{"argv": ["c++"], "directory": str(root)}]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual([issue["code"] for issue in issues], ["RUNTIME_INCLUDE_UNRESOLVED"])



if __name__ == "__main__":
    unittest.main()
