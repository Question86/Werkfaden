from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from kairos.util import atomic_write_json, read_json
from kairos.workspace import initialize_workspace


# Test artifacts must never be created below the installed/source package.  A
# checkout may be read-only, protected by Windows Defender/Application Control,
# or shared by another user.  tempfile.TemporaryDirectory also gives each test
# process an isolated root without relying on repository ACLs.
_TEST_ROOT = tempfile.TemporaryDirectory(prefix="kairos-harness-tests-")
TEST_ROOT = Path(_TEST_ROOT.name)


class WorkspaceTestCase(unittest.TestCase):
    workspace: Path

    def setUp(self) -> None:
        self.workspace = TEST_ROOT / f"kairos-test-{uuid.uuid4().hex}"
        self.workspace.mkdir()
        initialize_workspace(self.workspace, workspace_id="KAIROS_TEST")
        config_path = self.workspace / ".kairos" / "config.json"
        config = read_json(config_path)
        config["governance_enforcement"] = "audit"
        config["governance_audit_allows_finalization"] = True
        atomic_write_json(config_path, config)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)
