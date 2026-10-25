from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from v3_cold_bootstrap_gate import review_value_fixture

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

CASES = (
    ("python", "app.py", "VALUE = 1\n", "VALUE = 2\n"),
    ("javascript_typescript", "app.ts", "export const VALUE = 1;\n", "export const VALUE = 2;\n"),
    ("rust", "lib.rs", "pub const VALUE: i32 = 1;\n", "pub const VALUE: i32 = 2;\n"),
    ("go", "main.go", "package main\nconst Value = 1\n", "package main\nconst Value = 2\n"),
    ("jvm", "Main.java", "class Main { static final int VALUE = 1; }\n", "class Main { static final int VALUE = 2; }\n"),
    ("dotnet", "Program.cs", "class Program { static int Value = 1; }\n", "class Program { static int Value = 2; }\n"),
    ("ruby", "app.rb", "VALUE = 1\n", "VALUE = 2\n"),
    ("php", "app.php", "<?php\n$value = 1;\n", "<?php\n$value = 2;\n"),
)


def run_json(*args: str) -> dict:
    completed = subprocess.run(
        [PYTHON, *args], cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, timeout=180,
    )
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode}): {args}\n{completed.stdout}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"command did not return JSON: {args}\n{completed.stdout}") from exc


def spec(label: str) -> dict:
    slug = label.upper().replace("-", "_")
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": f"GOAL_COLD_{slug}",
            "title": f"Cold {label} validation",
            "objective": f"Prove release-artifact intake and Workshop mutation for {label}.",
            "state": "active",
            "milestones": [{
                "id": f"MILESTONE_{slug}_01",
                "title": "Verified release transaction",
                "objective": "Reach a bit-exact verified Workshop postcheck from a cold project.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": f"CRIT_{slug}_001",
                    "description": "Cold intake and one Workshop mutation reach POSTCHECK_VERIFIED.",
                    "state": "active",
                    "evidence_required": "Intake, seal and postcheck receipts.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": f"TASK_{slug}_001",
            "title": "Run cold ecosystem gate",
            "objective": "Exercise public KAIROS intake and Workshop CLI surfaces.",
            "milestone": f"MILESTONE_{slug}_01",
            "criteria": [f"CRIT_{slug}_001"],
        },
    }


def snapshot(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in sorted(project.rglob("*")) if path.is_file()
    }


def ordinary_roundtrip(label: str, filename: str, before: str, after: str) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"kairos-v3-matrix-{label}-") as temporary:
        root = Path(temporary)
        project = root / "project"
        workspace = root / "workspace"
        project.mkdir()
        source = project / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(before.encode("utf-8"))
        baseline = snapshot(project)
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(spec(label), indent=2) + "\n", encoding="utf-8")

        detected = run_json("-m", "kickstart", "detect", str(project))
        ecosystems = {row.get("ecosystem") for row in detected.get("ecosystems", [])}
        if ecosystems != {label}:
            raise SystemExit(f"{label}: detect mismatch: {detected}")
        if snapshot(project) != baseline:
            raise SystemExit(f"{label}: detect modified live project")

        intake = run_json(
            "-m", "kickstart", "init", "--auto",
            "--project-root", str(project), "--workspace", str(workspace),
            "--spec-file", str(spec_path),
        )
        if not intake.get("verified") or intake.get("state") != "VERIFIED_PENDING_SEAL":
            raise SystemExit(f"{label}: intake not verified: {intake}")
        if snapshot(project) != baseline:
            raise SystemExit(f"{label}: intake modified live project")

        config = workspace / ".kairos" / "workshop.config.json"
        run_json("workshop/workshop.py", "--config", str(config), "seal")
        checkout = run_json(
            "workshop/workshop.py", "--config", str(config), "checkout",
            "--source", filename, "--purpose", f"Cold {label} release mutation verification",
        )
        transaction_id = checkout["transaction_id"]
        work_source = Path(checkout["work_directory"]) / filename
        work_source.parent.mkdir(parents=True, exist_ok=True)
        work_source.write_bytes(after.encode("utf-8"))
        review_value_fixture(config, checkout, filename, before, after)
        prepared = run_json("workshop/workshop.py", "--config", str(config), "prepare", transaction_id)
        run_json("workshop/workshop.py", "--config", str(config), "metadata-check", transaction_id)
        verified = run_json("workshop/workshop.py", "--config", str(config), "verify", transaction_id)
        if prepared.get("state") != "PREPARED" or verified.get("state") != "SHADOW_VERIFIED":
            raise SystemExit(f"{label}: pre-apply transaction failed: {prepared} / {verified}")
        if snapshot(project) != baseline:
            raise SystemExit(f"{label}: project changed before verified apply")
        applied = run_json("workshop/workshop.py", "--config", str(config), "apply", transaction_id)
        if applied.get("state") != "POSTCHECK_VERIFIED" or not applied.get("bit_exact"):
            raise SystemExit(f"{label}: apply failed: {applied}")
        if source.read_text(encoding="utf-8") != after:
            raise SystemExit(f"{label}: live source does not equal verified candidate")
        return {"ecosystem": label, "state": applied["state"], "bit_exact": bool(applied.get("bit_exact"))}


def mixed_cpp_python_roundtrip() -> dict:
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise SystemExit("mixed C++ release gate requires a system C++ compiler")
    with tempfile.TemporaryDirectory(prefix="kairos-v3-matrix-mixed-") as temporary:
        root = Path(temporary)
        project = root / "project"
        workspace = root / "workspace"
        project.mkdir()
        cpp = project / "main.cpp"
        py = project / "helper.py"
        cpp.write_text("int main(){return 0;}\n", encoding="utf-8")
        py.write_bytes(b"VALUE = 1\n")
        compile_commands = root / "compile_commands.json"
        compile_commands.write_text(json.dumps([{
            "directory": str(project.resolve()),
            "arguments": [compiler, "-c", str(cpp.resolve()), "-o", str((root / "main.o").resolve())],
            "file": str(cpp.resolve()),
        }], indent=2) + "\n", encoding="utf-8")
        baseline = snapshot(project)
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(spec("mixed_cpp_python"), indent=2) + "\n", encoding="utf-8")

        detected = run_json("-m", "kickstart", "detect", str(project))
        ecosystems = {row.get("ecosystem") for row in detected.get("ecosystems", [])}
        if ecosystems != {"c_family", "python"}:
            raise SystemExit(f"mixed: detect mismatch: {detected}")
        intake = run_json(
            "-m", "kickstart", "init", "--auto",
            "--project-root", str(project), "--workspace", str(workspace),
            "--spec-file", str(spec_path), "--auto-compile-commands", str(compile_commands),
        )
        if not intake.get("verified"):
            raise SystemExit(f"mixed: intake not verified: {intake}")
        if snapshot(project) != baseline:
            raise SystemExit("mixed: intake modified live project")

        config = workspace / ".kairos" / "workshop.config.json"
        run_json("workshop/workshop.py", "--config", str(config), "seal")
        checkout = run_json(
            "workshop/workshop.py", "--config", str(config), "checkout",
            "--source", "helper.py", "--purpose", "Cold mixed C++ Python release mutation verification",
        )
        transaction_id = checkout["transaction_id"]
        (Path(checkout["work_directory"]) / "helper.py").write_bytes(b"VALUE = 2\n")
        review_value_fixture(config, checkout, "helper.py", "VALUE = 1\n", "VALUE = 2\n")
        run_json("workshop/workshop.py", "--config", str(config), "prepare", transaction_id)
        run_json("workshop/workshop.py", "--config", str(config), "metadata-check", transaction_id)
        verified = run_json("workshop/workshop.py", "--config", str(config), "verify", transaction_id)
        if verified.get("state") != "SHADOW_VERIFIED" or snapshot(project) != baseline:
            raise SystemExit("mixed: verification failed or changed live project")
        applied = run_json("workshop/workshop.py", "--config", str(config), "apply", transaction_id)
        if applied.get("state") != "POSTCHECK_VERIFIED" or py.read_text(encoding="utf-8") != "VALUE = 2\n":
            raise SystemExit(f"mixed: apply failed: {applied}")
        return {"ecosystem": "mixed_c_family_python", "state": applied["state"], "bit_exact": bool(applied.get("bit_exact"))}


def python_source_set_roundtrip() -> dict:
    with tempfile.TemporaryDirectory(prefix="kairos-v3-matrix-source-set-") as temporary:
        root = Path(temporary)
        project = root / "project"
        workspace = root / "workspace"
        project.mkdir()
        (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "rename_me.py").write_text("RENAMED = True\n", encoding="utf-8")
        (project / "delete_me.py").write_text("DELETE = True\n", encoding="utf-8")
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(spec("source_set"), indent=2) + "\n", encoding="utf-8")
        intake = run_json(
            "-m", "kickstart", "init", "--auto", "--project-root", str(project),
            "--workspace", str(workspace), "--spec-file", str(spec_path),
        )
        if not intake.get("verified"):
            raise SystemExit("source-set: initial intake failed")
        config = workspace / ".kairos" / "workshop.config.json"
        run_json("workshop/workshop.py", "--config", str(config), "seal")
        live_before = snapshot(project)
        checkout = run_json(
            "workshop/workshop.py", "--config", str(config), "source-set-checkout",
            "--purpose", "Cold release create delete rename source-set verification",
        )
        candidate = Path(checkout["candidate_root"])
        (candidate / "rename_me.py").rename(candidate / "renamed.py")
        (candidate / "delete_me.py").unlink()
        (candidate / "new.py").write_text("NEW = True\n", encoding="utf-8")
        transaction_id = checkout["transaction_id"]
        prepared = run_json("workshop/workshop.py", "--config", str(config), "source-set-prepare", transaction_id)
        verified = run_json("workshop/workshop.py", "--config", str(config), "source-set-verify", transaction_id)
        if prepared.get("state") != "PREPARED" or verified.get("state") != "SHADOW_VERIFIED":
            raise SystemExit(f"source-set: pre-apply failed: {prepared} / {verified}")
        if snapshot(project) != live_before:
            raise SystemExit("source-set: live project changed before verified apply")
        applied = run_json("workshop/workshop.py", "--config", str(config), "source-set-apply", transaction_id)
        if applied.get("state") != "POSTCHECK_VERIFIED":
            raise SystemExit(f"source-set: apply failed: {applied}")
        if (project / "rename_me.py").exists() or (project / "delete_me.py").exists():
            raise SystemExit("source-set: removed/renamed live paths remain")
        if not (project / "renamed.py").is_file() or not (project / "new.py").is_file():
            raise SystemExit("source-set: created/renamed live paths missing")
        return {"ecosystem": "python_source_set", "state": applied["state"], "bit_exact": bool(applied.get("bit_exact", True))}


def main() -> int:
    results = [ordinary_roundtrip(*case) for case in CASES]
    results.append(mixed_cpp_python_roundtrip())
    results.append(python_source_set_roundtrip())
    print(json.dumps({
        "schema": "kairos-v3-cold-ecosystem-matrix/v1",
        "verified": True,
        "case_count": len(results),
        "cases": results,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
