from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kairos.workspace import WorkspaceError, initialize_workspace
from kairos.cli import _require_id, build_parser, execute

from support import WorkspaceTestCase


class WorkspaceSafetyTests(WorkspaceTestCase):
    def test_initialization_refuses_non_empty_target(self) -> None:
        with self.assertRaises(WorkspaceError):
            initialize_workspace(self.workspace, workspace_id="SHOULD_NOT_OVERWRITE")

    def test_artifact_identifiers_cannot_escape_workspace_paths(self) -> None:
        with self.assertRaises(ValueError):
            _require_id("TASK_../ESCAPE", "TASK")

    def test_refused_governed_command_does_not_materialize_database_without_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "source-only"
            (workspace / ".kairos").mkdir(parents=True)
            (workspace / ".kairos" / "config.json").write_text(json.dumps({
                "schema": "kairos-workspace-config/v1",
                "workspace_id": "SOURCE_ONLY_TEST",
                "database": ".kairos/kairos.db",
                "document_roots": [],
                "canonical_files": [],
                "reconcile_extensions": [".md"],
                "inspection_root": ".",
                "imported_workspace_ids": [],
                "imported_search_contract_policies": {},
                "fullscan_policy": "recovery_only",
                "governance_enforcement": "required",
                "created_at": "2026-09-07T20:00:00Z",
            }) + "\n", encoding="utf-8")
            args = build_parser().parse_args(["status", "--workspace", str(workspace)])
            with self.assertRaises(Exception):
                execute(args)
            self.assertFalse((workspace / ".kairos" / "kairos.db").exists())


if __name__ == "__main__":
    unittest.main()
