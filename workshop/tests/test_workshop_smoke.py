from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from runtime_sync_workshop.scientific import ScientificParityGate  # noqa: E402
from runtime_sync_workshop.corpus import dependent_test_inventory  # noqa: E402
from runtime_sync_workshop.util import set_source_prefix, source_prefix  # noqa: E402


class WorkshopSmokeTests(unittest.TestCase):
    def test_unbound_parity_gate_is_explicitly_optional(self) -> None:
        gate = ScientificParityGate(SimpleNamespace(raw={}))
        self.assertEqual(
            gate.preflight(),
            {
                "schema": "runtime-sync-parity-preflight/v1",
                "configured": False,
                "verified": True,
            },
        )

    def test_source_prefix_is_process_local_and_normalized(self) -> None:
        set_source_prefix("src\\project")
        self.assertEqual(source_prefix(), "src/project/")
        set_source_prefix("")
        self.assertEqual(source_prefix(), "")

    def test_dependent_test_authority_is_configuration_driven(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "runtime").mkdir()
            (root / "tests" / "unit.cpp").write_text("int test();\n", encoding="utf-8")
            (root / "runtime" / "unit.cpp").write_text("int test() { return 0; }\n", encoding="utf-8")
            set_source_prefix("runtime")
            config = SimpleNamespace(
                raw={"dependent_tests": {"tests/unit.cpp": "runtime/unit.cpp"}},
                codebase_root=root,
                runtime_root=root / "runtime",
            )
            inventory = dependent_test_inventory(config)
            self.assertEqual(inventory["tests/unit.cpp"]["owner"], "runtime/unit.cpp")
            set_source_prefix("")


if __name__ == "__main__":
    unittest.main()
