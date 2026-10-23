from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = PACKAGE_ROOT / "workshop" / "src"
for value in (PACKAGE_ROOT, WORKSHOP_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.binding import initialize_project  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


def _spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_GUARD_BOUNDARY",
            "title": "Guardian boundary",
            "objective": "Verify that mutation authority cannot bypass the deterministic Guardian.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_GUARD_BOUNDARY_01",
                "title": "Guarded mutation",
                "objective": "Bind one source and prove the engine-level mutation gate.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_GUARD_BOUNDARY_001",
                    "description": "All Workshop mutation commands remain Guardian-bound.",
                    "state": "active",
                    "evidence_required": "Guardian refusal and transaction ownership evidence.",
                    "required_artifact_types": ["code"],
                }],
            }],
        },
        "task": {
            "id": "TASK_GUARD_BOUNDARY_001",
            "title": "Exercise Guardian boundary",
            "objective": "Create one synchronized baseline and test direct engine mutation paths.",
            "milestone": "MILESTONE_GUARD_BOUNDARY_01",
            "criteria": ["CRIT_GUARD_BOUNDARY_001"],
        },
    }


def _compile_commands(project: Path, source: Path) -> Path:
    build = project / "build"
    build.mkdir()
    path = build / "compile_commands.json"
    path.write_text(json.dumps([{
        "directory": str(build),
        "file": str(source),
        "arguments": ["c++", "-c", str(source)],
    }]), encoding="utf-8")
    return path


def _memory_contract(root: Path) -> tuple[Path, Path]:
    memory = root / "MEMORY.md"
    codes = [
        "MEM.HUMAN", "MEM.HYP", "MEM.F1", "MEM.F2", "MEM.MAP", "MEM.COUNTER",
        "MEM.SOURCE", "MEM.PROVE", "MEM.PATCH", "MEM.WORKSHOP", "MEM.HEARTBEAT", "MEM.FRESH",
    ]
    memory.write_text("\n\n".join(f"# {code}\n\n{code}\nRule for {code}." for code in codes) + "\n", encoding="utf-8")
    requirements = root / "memory-requirements.json"
    mapping = dict(zip(
        ["HUMAN_PROBLEM", "HYPOTHESIS", "FALSIFIER_1", "FALSIFIER_2", "MAP", "COUNTERPROBE", "EXACT_SOURCE", "PROVE", "PATCH", "WORKSHOP", "HEARTBEAT", "FRESH_RUN"],
        ([code] for code in codes),
    ))
    requirements.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    return memory, requirements


class GuardianEngineBoundaryTests(unittest.TestCase):
    def test_direct_checkout_and_legacy_transaction_commands_fail_closed_when_guardian_is_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            source = project / "src" / "main.cpp"
            source.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=_compile_commands(project, source), project_spec=_spec())
            config = workspace / ".kairos" / "workshop.config.json"
            unguarded = WorkshopEngine(config)
            unguarded.seal()
            legacy = unguarded.checkout(["src/main.cpp"], purpose="Create a pre-Guardian transaction for boundary testing")
            memory, requirements = _memory_contract(root)
            env = {
                "WERKFADEN_GUARDIAN_REQUIRED": "1",
                "WERKFADEN_GUARDIAN_MEMORY_FILE": str(memory),
                "WERKFADEN_GUARDIAN_MEMORY_REQUIREMENTS_FILE": str(requirements),
            }
            with patch.dict(os.environ, env, clear=False):
                guarded = WorkshopEngine(config)
                with self.assertRaises(WorkshopError) as caught:
                    guarded.prepare(legacy["transaction_id"])
                self.assertEqual(caught.exception.code, "GUARDIAN_TRANSACTION_BINDING")
            unguarded.abort(legacy["transaction_id"])
            with patch.dict(os.environ, env, clear=False):
                guarded = WorkshopEngine(config)
                with self.assertRaises(WorkshopError) as caught:
                    guarded.checkout(["src/main.cpp"], purpose="Direct public API checkout must remain gated")
                self.assertEqual(caught.exception.code, "GUARDIAN_REQUIRED")

    def test_alternate_workshop_config_cannot_create_second_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            source = project / "src" / "main.cpp"
            source.write_text("int main(){return 0;}\n", encoding="utf-8")
            workspace = root / "workspace"
            initialize_project(project_root=project, workspace=workspace, compile_commands=_compile_commands(project, source), project_spec=_spec())
            canonical = WorkshopEngine(workspace / ".kairos" / "workshop.config.json")
            alt_root = root / "alternate-control-plane"
            alt_root.mkdir()
            raw = json.loads(json.dumps(canonical.config.raw))
            raw.update({
                "codebase_root": str(canonical.config.codebase_root),
                "runtime_root": str(canonical.config.runtime_root),
                "cmake_file": str(canonical.config.cmake_file),
                "dataflow_index": str(canonical.config.dataflow_index),
                "blueprint_root": str(canonical.config.blueprint_root),
                "managed_blueprint_root": str(canonical.config.managed_blueprint_root),
                "kairos_workspace": str(canonical.config.kairos_workspace),
                "kairos_harness": str(canonical.config.kairos_harness),
                "kairos_database": str(canonical.config.kairos_database),
                "state_directory": ".state",
                "transaction_directory": "transactions",
                "machine_authority_files": ["workshop.config.json"],
            })
            alt = alt_root / "workshop.config.json"
            alt.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(WorkshopError) as caught:
                WorkshopEngine(alt)
            self.assertEqual(caught.exception.code, "CONFIG_CONTROL_PLANE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
