from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_ROOTS = (
    ROOT / "kickstart",
    ROOT / "kairos" / "kairos_harness" / "kairos",
    ROOT / "workshop" / "src" / "runtime_sync_workshop",
)
# All active implementation is regular Python under IMPLEMENTATION_ROOTS.
# Historical quarantine text is retained as provenance and is not executed.
ACTIVE_EXECUTED_SOURCES: tuple[Path, ...] = ()
SEED_NAMES = {
    "project_root",
    "codebase_root",
    "runtime_root",
    "blueprint_root",
    "managed_blueprint_root",
}
METHOD_SINKS = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch", "rename"}
FUNCTION_DEST_INDEX = {
    "open": 0,
    "atomic_write_json": 0,
    "atomic_write_text": 0,
    "atomic_write_bytes": 0,
    "_atomic_bytes": 0,
    "_atomic_text": 0,
    "_atomic_json": 0,
    "copy_exact": 1,
    "shutil.copy": 1,
    "shutil.copy2": 1,
    "shutil.copyfile": 1,
    "shutil.copytree": 1,
    "shutil.move": 1,
    "os.replace": 1,
    "os.rename": 1,
    "os.remove": 0,
    "os.unlink": 0,
    "os.rmdir": 0,
    "shutil.rmtree": 0,
}
SOURCE_MUTATING_FUNCTIONS = {"shutil.move", "os.rename", "os.replace"}

# These two external-tool calls have stronger static proofs checked by require_contracts():
# the compiler literal probe is non-writing by construction, while CMake receives only a
# disposable clone and an external build directory. They are not generic exceptions.
SUBPROCESS_ALLOW = {
    ("kickstart/binding.py", "_compiler_selected_literal"),
    ("kickstart/cmake.py", "configure_compile_commands"),
}


def dotted(call: ast.AST) -> str:
    if isinstance(call, ast.Name):
        return call.id
    if isinstance(call, ast.Attribute):
        base = dotted(call.value)
        return f"{base}.{call.attr}" if base else call.attr
    return ""


def target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            result.update(target_names(item))
        return result
    return set()


def expression_tainted(node: ast.AST | None, tainted: set[str]) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        # A local variable merely named blueprint_root is not automatically the live
        # blueprint root. Function parameters with authority names are seeded below;
        # config.<authority_root> attributes remain intrinsically live-authority roots.
        if isinstance(child, ast.Name) and child.id in tainted:
            return True
        if isinstance(child, ast.Attribute) and child.attr in SEED_NAMES:
            return True
    return False


def call_write_targets(node: ast.Call) -> list[ast.AST]:
    name = dotted(node.func)
    if isinstance(node.func, ast.Attribute) and node.func.attr in METHOD_SINKS:
        if node.func.attr == "rename":
            return [node.func.value, *node.args[:1]]
        return [node.func.value]
    if name == "open":
        mode = "r"
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            mode = node.args[1].value
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                mode = keyword.value.value
        if not any(flag in mode for flag in "wax+"):
            return []
    index = FUNCTION_DEST_INDEX.get(name)
    if index is None:
        return []
    targets: list[ast.AST] = []
    if len(node.args) > index:
        targets.append(node.args[index])
    if name in SOURCE_MUTATING_FUNCTIONS and node.args:
        targets.append(node.args[0])
    return targets


def safe_nonlive_sink(relative: str, function_name: str, node: ast.Call, target: ast.AST) -> bool:
    """Recognize narrowly proved writes to disposable/non-live paths."""
    if relative == "kickstart/binding.py" and function_name == "initialize_project":
        if dotted(node.func) == "shutil.rmtree" and ast.unparse(target) == "cmake_configure.build_directory":
            return True
    return False


def audit_function(relative: str, function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, object]]:
    tainted = {
        arg.arg
        for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        if arg.arg in SEED_NAMES
    }
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            value = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]
            if value is not None and expression_tainted(value, tainted):
                for target in targets:
                    for name in target_names(target):
                        if name not in tainted:
                            tainted.add(name)
                            changed = True

    findings: list[dict[str, object]] = []
    workshop_module = relative.startswith("workshop/src/runtime_sync_workshop/")
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        for target in call_write_targets(node):
            if safe_nonlive_sink(relative, function.name, node, target):
                continue
            if expression_tainted(target, tainted) and not workshop_module:
                findings.append({
                    "file": relative,
                    "line": node.lineno,
                    "function": function.name,
                    "kind": "live_write_sink_outside_workshop",
                    "call": ast.unparse(node)[:300],
                })
        call_name = dotted(node.func)
        if call_name in {"subprocess.run", "subprocess.Popen", "subprocess.check_call", "subprocess.check_output"}:
            if (relative, function.name) in SUBPROCESS_ALLOW:
                continue
            exposed = any(expression_tainted(argument, tainted) for argument in node.args)
            exposed = exposed or any(expression_tainted(keyword.value, tainted) for keyword in node.keywords)
            if exposed and not workshop_module:
                findings.append({
                    "file": relative,
                    "line": node.lineno,
                    "function": function.name,
                    "kind": "mutation_capable_tool_exposed_to_live_root",
                    "call": ast.unparse(node)[:300],
                })
    return findings


def audit_python() -> tuple[int, list[dict[str, object]]]:
    files = 0
    findings: list[dict[str, object]] = []
    for base in IMPLEMENTATION_ROOTS:
        active = [p for p in ACTIVE_EXECUTED_SOURCES if p.is_relative_to(base)]
        for path in sorted(set(base.rglob("*.py")) | set(active)):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            files += 1
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    findings.extend(audit_function(relative, node))
                elif isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            findings.extend(audit_function(relative, child))
    return files, findings


def require_contracts() -> list[str]:
    failures: list[str] = []
    onboarding = (ROOT / "docs" / "UNIVERSAL_ONBOARDING.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    workshop = (ROOT / "docs" / "WORKSHOP.md").read_text(encoding="utf-8")
    cmake = (ROOT / "kickstart" / "cmake.py").read_text(encoding="utf-8")
    required = {
        "onboarding single mutation": (onboarding, "Workshop is the sole mutation boundary"),
        "onboarding direct live edits forbidden": (onboarding, "must never edit the configured live project root"),
        "onboarding clone boundary": (onboarding, "isolated clone or snapshot"),
        "agents Workshop writer": (agents, "Workshop is the only"),
        "Workshop source-set": (workshop, "source-set-checkout"),
        "CMake clone": (cmake, "shutil.copytree(project_root, clone_root"),
        "CMake clone cwd": (cmake, "cwd=clone_root"),
        "CMake live build refusal": (cmake, "CMAKE_BUILD_DIRECTORY_LIVE"),
        "CMake clone cleanup": (cmake, "shutil.rmtree(clone_parent"),
    }
    for label, (text, needle) in required.items():
        if needle not in text:
            failures.append(f"missing contract: {label}: {needle!r}")
    if "cwd=project_root" in cmake:
        failures.append("legacy CMake still executes a mutation-capable tool with cwd=project_root")
    return failures


def main() -> int:
    scanned, findings = audit_python()
    contract_failures = require_contracts()
    payload = {
        "schema": "kairos-v3-write-boundary-audit/v1",
        "verified": not findings and not contract_failures,
        "python_files_scanned": scanned,
        "write_findings": findings,
        "contract_failures": contract_failures,
        "policy": "Outside runtime_sync_workshop, live project/codebase/runtime/blueprint roots must not reach write sinks or mutation-capable subprocesses. Initial intake may derive KAIROS state but does not write the observed project. Clone-isolated tools are separately proven by static contracts and regression tests.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
