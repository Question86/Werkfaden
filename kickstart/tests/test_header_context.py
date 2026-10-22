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

from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_HEADER_CONTEXT",
            "title": "Current header context",
            "objective": "Keep header byte evidence current after Workshop patches.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_HEADER_01",
                "title": "Verified header update",
                "objective": "Apply a header patch with consistent document evidence.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_HEADER_001",
                    "description": "Header documents contain no obsolete byte hash after apply.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck and document inspection.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_HEADER_001",
            "title": "Verify header context",
            "objective": "Update header bytes without leaving duplicated stale facts.",
            "milestone": "MILESTONE_HEADER_01",
            "criteria": ["CRIT_HEADER_001"],
        },
    }


class HeaderContextTests(unittest.TestCase):
    def test_comment_only_header_patch_leaves_no_obsolete_semantic_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "main.cpp"
            header = project / "api.hpp"
            source.write_bytes(b'#include "api.hpp"\nint main() { return api(); }\n')
            header.write_bytes(b"#pragma once\ninline int api() { return 0; }\n")
            before_hash = hashlib.sha256(header.read_bytes()).hexdigest().upper()
            commands = project / "compile_commands.json"
            commands.write_text(json.dumps([{
                "directory": str(project),
                "file": str(source),
                "arguments": ["c++", "-I", str(project), "-c", str(source)],
            }]), encoding="utf-8")
            workspace = root / "workspace"
            intake = initialize_universal_markdown(
                project_root=project,
                workspace=workspace,
                compile_commands=commands,
                project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.seal()
            checkout = engine.checkout(["api.hpp"], purpose="Verify current header hashes after a comment-only patch")
            work_header = Path(checkout["work_directory"]) / "api.hpp"
            work_header.write_bytes(b"// reviewed comment\n" + work_header.read_bytes())
            review_path = Path(checkout["metadata_review"])
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["entries"][0].update({
                "metadata_impact": "none",
                "reason": "Comment-only change preserves header purpose, answers, references and claim boundary.",
                "reviewer": "header-context-test",
                "reviewed_at": "2026-10-22T00:00:00Z",
                "reviewed_fields": ["capsule", "answers", "refs", "claim_boundary", "sections"],
            })
            review_path.write_text(json.dumps(review) + "\n", encoding="utf-8")
            transaction_id = checkout["transaction_id"]
            engine.prepare(transaction_id)
            engine.verify(transaction_id)
            receipt = engine.apply(transaction_id)
            self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
            self.assertTrue(receipt["bit_exact"])

            document = next((workspace / "code").glob("PROJECT_HEADER_*.md")).read_text(encoding="utf-8")
            current_hash = hashlib.sha256(header.read_bytes()).hexdigest().upper()
            self.assertIn(f'header_sha256 = "{current_hash}"', document)
            self.assertNotIn(before_hash, document)
            boundary = document.split('<a id="s-boundary"></a>', 1)[1].split('<a id=', 1)[0]
            self.assertIn("[SOURCE MAPPING](#s-mapping)", boundary)
            self.assertNotIn(current_hash, boundary)


if __name__ == "__main__":
    unittest.main()
