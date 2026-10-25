from __future__ import annotations

import re
import unittest
from pathlib import Path

from kairos.framework import FRAMEWORK_DOCUMENT_PATHS, source_distribution_root


class OperatingContractDriftTests(unittest.TestCase):
    def test_indexed_rules_agree_with_workshop_commands_and_journal_authority(self):
        root = source_distribution_root()
        self.assertIsNotNone(root)
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        workshop = (root / "docs/WORKSHOP.md").read_text(encoding="utf-8")
        operations = (root / "workshop/OPERATIONS.md").read_text(encoding="utf-8")
        cli = (root / "workshop/src/runtime_sync_workshop/cli.py").read_text(encoding="utf-8")
        self.assertIn("workshop/OPERATIONS.md", FRAMEWORK_DOCUMENT_PATHS)
        self.assertNotIn("does not yet provide automatic transaction pruning", operations)
        self.assertNotIn("No supported Apply path consumes a JEV approval", workshop)
        self.assertIn("jev_apply_required=true", workshop)
        self.assertIn("SCOPE_UNRESOLVED", operations)
        for command in ("tx-recycle-status", "tx-recycle-inspect",
                        "tx-recycle-submit", "tx-recycle-drain", "tx-document-reconcile"):
            self.assertIn(command, cli)
            self.assertIn(command, operations)
        revision = re.search(r"(?m)^revision = (\d+)$", workshop)
        self.assertIsNotNone(revision)
        match = re.search(r"\[ref:docs/WORKSHOP\.md#s-overview\|id:KAIROS_WORKSHOP\|v:(\d+)\|", agents)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), int(revision.group(1)))

if __name__ == "__main__":
    unittest.main()
