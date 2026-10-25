#!/usr/bin/env python3
"""
Jev Workshop Patch Attempt Gate — direct TypeSafe transport.

The transport fixes the TypeSafe endpoint. Credentials are resolved at runtime.
Project-owned questions must be supplied through JEV_QUESTIONS_FILE.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from jev_transport import JevTransportError, call_jev as direct_call_jev

DEFAULT_CONTEXT_LINES = 0
MAX_REVIEW_STATE_BYTES = 48 * 1024

TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".cu", ".cuh", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".json", ".md", ".toml", ".yaml", ".yml", ".txt", ".cmake",
}


class GateError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateError(f"expected JSON object: {path}")
    return value


def run_json(cmd: list[str], cwd: Path, *, allow_failure: bool = False) -> dict[str, Any] | None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        if allow_failure:
            return None
        raise GateError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    text = proc.stdout.strip()
    if not text:
        if allow_failure:
            return None
        raise GateError(f"command produced no JSON: {' '.join(cmd)}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        if allow_failure:
            return None
        raise GateError(f"command did not return JSON: {' '.join(cmd)}\n{text}") from exc
    return value if isinstance(value, dict) else {"value": value}


def locate_transaction_root(workshop_root: Path, tx: str) -> Path:
    root = workshop_root / "transactions" / tx
    if not root.is_dir():
        raise GateError(f"transaction directory not found: {root}")
    if not (root / "work").is_dir():
        raise GateError(f"transaction has no work directory: {root / 'work'}")
    return root


def load_transaction_state(workshop_root: Path, tx: str, config_path: Path | None = None) -> dict[str, Any]:
    args = [sys.executable, "workshop.py"]
    if config_path is not None:
        args.extend(["--config", str(config_path)])
    return run_json([*args, "transaction", tx], workshop_root) or {}


def load_workshop_config(config_path: Path) -> dict[str, Any]:
    config = read_json(config_path)
    for required in ("codebase_root", "kairos_workspace"):
        if not config.get(required):
            raise GateError(f"workshop config is missing required key: {required}")
    return config


def safe_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def enumerate_candidate_files(tx_root: Path) -> list[tuple[str, Path]]:
    work = tx_root / "work"
    roots = [
        ("runtime/", work / "runtime"),
        ("tests/", work / "tests"),
        ("blueprints/", work / "blueprints"),
    ]
    rows: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for prefix, base in roots:
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if path in seen:
                continue
            seen.add(path)
            rel = path.relative_to(base).as_posix()
            rows.append((prefix + rel, path))
    return rows


def live_path_for(candidate_key: str, config: dict[str, Any]) -> Path | None:
    if candidate_key.startswith("blueprints/"):
        root = config.get("blueprint_root")
        if not root:
            return None
        return Path(root) / candidate_key.removeprefix("blueprints/")
    return Path(config["codebase_root"]) / candidate_key


def changed_regions(before: list[str], after: list[str], context: int) -> list[dict[str, Any]]:
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    regions: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        bi1 = max(0, i1 - context)
        bi2 = min(len(before), i2 + context)
        aj1 = max(0, j1 - context)
        aj2 = min(len(after), j2 + context)
        regions.append({
            "change_kind": tag,
            "before_range": [bi1 + 1, bi2],
            "after_range": [aj1 + 1, aj2],
            "before": "".join(before[bi1:bi2]),
            "after": "".join(after[aj1:aj2]),
        })
    return regions


def materialize_change(candidate_key: str, candidate: Path, config: dict[str, Any], context: int) -> dict[str, Any] | None:
    live = live_path_for(candidate_key, config)
    if live is None or not live.is_file():
        raise GateError(f"no existing live counterpart for candidate {candidate_key}: {live}")
    if candidate.suffix.lower() not in TEXT_SUFFIXES and live.suffix.lower() not in TEXT_SUFFIXES:
        return None

    before_bytes = live.read_bytes()
    after_bytes = candidate.read_bytes()
    if before_bytes == after_bytes:
        return None

    before_text = safe_text(live)
    after_text = safe_text(candidate)
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"LIVE/{candidate_key}",
        tofile=f"TX_WORK/{candidate_key}",
        n=context,
        lineterm="\n",
    ))
    return {
        "path": candidate_key,
        "live_path": str(live),
        "candidate_path": str(candidate),
        "before_line_count": len(before_lines),
        "after_line_count": len(after_lines),
        "regions": changed_regions(before_lines, after_lines, context),
        "unified_diff": diff,
    }


def materialize_changes(tx_root: Path, config: dict[str, Any], context: int) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, candidate in enumerate_candidate_files(tx_root):
        row = materialize_change(key, candidate, config, context)
        if row is not None:
            changes.append(row)
    if not changes:
        raise GateError("transaction work tree contains no changed text source/test/blueprint files")
    return changes


def compact_review_change(change: dict[str, Any]) -> dict[str, Any]:
    """Keep exact changed regions while removing the redundant unified diff."""

    return {key: value for key, value in change.items() if key != "unified_diff"}


def kairos_graph_receipts(config: dict[str, Any], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    harness_raw = config.get("kairos_harness")
    workspace_raw = config.get("kairos_workspace")
    if not harness_raw or not workspace_raw:
        return []
    harness = Path(harness_raw)
    workspace = Path(workspace_raw)
    if not harness.is_dir() or not workspace.is_dir():
        return []

    receipts: list[dict[str, Any]] = []
    for change in changes:
        path = change["path"]
        if path.startswith("blueprints/"):
            continue
        resolve = run_json(
            [sys.executable, "-m", "kairos", "graph", "--workspace", str(workspace), "--resolve", path],
            harness,
            allow_failure=True,
        )
        entry: dict[str, Any] = {"query_path": path, "resolve": resolve}
        candidates = [] if not isinstance(resolve, dict) else resolve.get("candidates", [])
        if isinstance(candidates, list) and len(candidates) == 1 and isinstance(candidates[0], dict):
            identity = str(candidates[0].get("identity", "")).strip()
            if identity:
                node = run_json(
                    [sys.executable, "-m", "kairos", "graph", "--workspace", str(workspace), "--node", identity],
                    harness,
                    allow_failure=True,
                )
                entry["exact_identity"] = identity
                entry["node"] = node
        receipts.append(entry)
    return receipts


def current_workspace_state(config: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(config["kairos_workspace"]) / "current.json"
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except GateError:
        return None


def questions() -> dict[str, dict[str, str]]:
    """Load project-owned TypeSafe questions; no private local profile is shipped."""
    raw = os.environ.get("JEV_QUESTIONS_FILE")
    if not raw:
        raise GateError("JEV_QUESTIONS_FILE must name an explicit project-owned question profile")
    profile = read_json(Path(raw).expanduser())
    if not profile:
        raise GateError("question profile must not be empty")
    for name, question in profile.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(question, dict)
                or set(question) != {"type", "instructions"} or question["type"] != "noul"
                or not isinstance(question["instructions"], str) or not question["instructions"].strip()):
            raise GateError("each question requires a nonempty name, type=noul and nonempty instructions")
    return profile


def call_jev(state: dict[str, Any]) -> dict[str, Any]:
    try:
        return direct_call_jev(state, questions(), timeout=60)
    except JevTransportError as exc:
        raise GateError(str(exc)) from exc


def numeric_answer(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("noul", "score", "value"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review an existing Workshop candidate patch with Jev using sealed live code vs transaction work code. No Git. TypeSafe Direct API."
    )
    parser.add_argument("transaction_id", help="Workshop transaction id, e.g. TXN_...")
    parser.add_argument(
        "--workshop-root",
        default=os.environ.get("KAIROS_WORKSHOP_ROOT", "."),
        help="directory containing workshop.py and transactions/ (or KAIROS_WORKSHOP_ROOT)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("KAIROS_WORKSHOP_CONFIG"),
        help="authoritative workshop.config.json (or KAIROS_WORKSHOP_CONFIG)",
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=DEFAULT_CONTEXT_LINES,
        help="deterministic context around changed regions; auto-compacts to keep state below 48 KiB",
    )
    parser.add_argument("--out", default=None, help="write full review JSON here")
    parser.add_argument("--include-state", action="store_true", help="include the complete Jev state in output")
    args = parser.parse_args()

    try:
        workshop_root = Path(args.workshop_root).resolve()
        if not (workshop_root / "workshop.py").is_file():
            raise GateError(f"workshop.py not found under {workshop_root}")
        if not args.config:
            raise GateError("--config or KAIROS_WORKSHOP_CONFIG is required; the routine will not guess a workspace/config path")
        config_path = Path(args.config).resolve()
        config = load_workshop_config(config_path)
        tx_root = locate_transaction_root(workshop_root, args.transaction_id)
        tx_state = load_transaction_state(workshop_root, args.transaction_id, config_path)

        allowed_states = {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}
        state_name = str(tx_state.get("state", ""))
        if state_name not in allowed_states:
            raise GateError(f"transaction state {state_name!r} is not a pre-apply candidate state")

        requested_context = max(0, args.context_lines)
        changes = materialize_changes(tx_root, config, requested_context)
        # Filter out blueprint changes (too large, not code review)
        changes = [c for c in changes if not c["path"].startswith("blueprints/")]
        review_changes = [compact_review_change(change) for change in changes]
        # Skip graph receipts and current workspace state to keep payload under API limit
        graph = []
        current = {}

        state = {
            "review_contract": "WERKFADEN_JEV_WORKSHOP_PATCH_ATTEMPT_V1",
            "review_mode": "PRE_EXECUTION_PATCH_ATTEMPT",
            "semantics": {
                "question": (
                    "Is the concrete candidate edit in the Workshop transaction a technically and evidentially "
                    "justified patch attempt against the current sealed code, such that compiling/testing it is warranted?"
                ),
                "important": [
                    "This is NOT a closure review.",
                    "A successful post-fix run is NOT expected to exist yet.",
                    "Do not require artifacts that can only be produced after this candidate is compiled/tested/run.",
                    "Judge only the supplied current code, candidate code, exact diff, transaction purpose/state, and KAIROS evidence.",
                    "Do not invent project facts that are absent from the supplied state.",
                ],
            },
            "transaction": {
                "transaction_id": args.transaction_id,
                "state": tx_state.get("state"),
                "purpose": tx_state.get("purpose"),
                "selected_sources": tx_state.get("sources") or tx_state.get("selected_sources"),
                "dependent_tests": tx_state.get("dependent_tests"),
                "transaction_kind": tx_state.get("transaction_kind", "normal"),
            },
            "workspace_state": current,
            "system_materialized_code_changes": review_changes,
            "system_materialized_graph_receipts": graph,
        }

        state_bytes = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
        if state_bytes > MAX_REVIEW_STATE_BYTES and requested_context != 0:
            # Preserve every exact changed line, but discard optional context
            # before refusing the request.
            changes = materialize_changes(tx_root, config, 0)
            changes = [c for c in changes if not c["path"].startswith("blueprints/")]
            state["system_materialized_code_changes"] = [compact_review_change(change) for change in changes]
            state_bytes = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
        if state_bytes > MAX_REVIEW_STATE_BYTES:
            raise GateError(
                "review state exceeds the bounded 48 KiB TypeSafe envelope even with exact changed lines only; "
                "split the candidate through a separately scoped Workshop transaction"
            )

        # DEBUG: check payload size and state sizes
        debug_payload = json.dumps({"model": "jev-latest", "state": state, "questions": questions()}, ensure_ascii=False).encode("utf-8")
        state_json = json.dumps(state, ensure_ascii=False)
        print(f"DEBUG: state size = {len(state_json) / 1024:.1f} KB", file=sys.stderr)
        print(f"DEBUG: payload size = {len(debug_payload) / 1024:.1f} KB", file=sys.stderr)
        print(f"DEBUG: changes = {len(changes)} items", file=sys.stderr)
        for i, c in enumerate(changes):
            c_json = json.dumps(c, ensure_ascii=False)
            print(f"  change {i}: {c.get('path', 'N/A')} = {len(c_json) / 1024:.1f} KB", file=sys.stderr)
        tx_json = json.dumps(tx_state, ensure_ascii=False)
        print(f"DEBUG: tx_state size = {len(tx_json) / 1024:.1f} KB", file=sys.stderr)

        response = call_jev(state)

        answers = response.get("answers", {}) if isinstance(response, dict) else {}
        scores = {}
        for k, v in answers.items():
            if isinstance(v, dict) and "noul" in v:
                scores[k] = v["noul"]
            elif isinstance(v, (int, float)):
                scores[k] = float(v)

        result: dict[str, Any] = {
            "schema": "werkfaden-jev-workshop-patch-attempt-review/v1",
            "transaction_id": args.transaction_id,
            "transaction_state": state_name,
            "changed_files": [row["path"] for row in changes],
            "scores": scores,
            "jev_response": response,
            "interpretation": (
                "PRE-EXECUTION REVIEW ONLY: these scores judge whether the concrete candidate is a justified patch attempt. "
                "They do not prove compilation, runtime success, scientific correctness, or closure."
            ),
        }
        if args.include_state:
            result["jev_state"] = state

        text = json.dumps(result, indent=2, ensure_ascii=False)
        print(text)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        return 0
    except GateError as exc:
        print(json.dumps({"schema": "werkfaden-jev-workshop-patch-attempt-error/v1", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
