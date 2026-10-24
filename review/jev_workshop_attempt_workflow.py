from __future__ import annotations

"""Run the governed Jev attempt/revision loop.

This controller deliberately does not edit Runtime files and does not invoke
Workshop ``apply``.  It evaluates one concrete candidate, stops on a low Jev
vector, produces the atomic-QM revision handoff, and requires a changed
transaction candidate before a fresh Jev review can be started.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ATTEMPT_SCRIPT = SCRIPT_DIR / "jev_workshop_attempt_gate_typesafe.py"
DEFAULT_TREE_SCRIPT = SCRIPT_DIR / "jev_adaptive_patch_tree.py"
DEFAULT_THRESHOLD = 0.90
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 180.0
DEFAULT_TREE_TIMEOUT_SECONDS = 180.0
DEFAULT_TREE_SUBPROCESS_TIMEOUT_SECONDS = 30.0
DEFAULT_TREE_SEARCH_LIMIT = 10
DEFAULT_TREE_MAX_DEPTH = 1
DEFAULT_TREE_MAX_NODES = 21
DEFAULT_TREE_MAX_EVIDENCE_CANDIDATES = 6
DEFAULT_TREE_MAX_CHILDREN_PER_NODE = 1
DEFAULT_TREE_MAX_FOCUS_ROOTS = 3
DEFAULT_TREE_BATCH_SIZE = 15
DEFAULT_CONTEXT_LINES = 0
# The TypeSafe gate materializes blueprints only to discard them before sending
# the review state.  The re-entry guard must hash the same reviewable surface;
# a blueprint-only edit must not masquerade as a revised code attempt.
CANDIDATE_ROOTS = ("runtime", "tests", "blueprints", "managed", "documents", "authority", "machine")

DEFAULT_POSITIVE_QUESTIONS = ()
DEFAULT_NEGATIVE_QUESTIONS = ()

EXIT_RECONSIDERATION_REQUIRED = 3


class WorkflowError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise WorkflowError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"expected JSON object: {path}")
    return value


def run_script_entry(
    script: Path,
    script_args: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    """Run every route stage in a killable child process with a real timeout."""

    proc = subprocess.run(
        [sys.executable, str(script), *script_args],
        cwd=script.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return proc.returncode, proc.stdout, proc.stderr


def numeric_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        score = float(value)
        return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None
    if isinstance(value, dict):
        for key in ("noul", "score", "value"):
            candidate = value.get(key)
            if isinstance(candidate, bool):
                return None
            if isinstance(candidate, (int, float)):
                score = float(candidate)
                return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Receipts are evidence. Never overwrite a previous review iteration.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def candidate_fingerprint(workshop_root: Path, transaction_id: str) -> dict[str, Any]:
    """Hash the actual candidate work tree used by the attempt gate.

    The fingerprint is deliberately based on candidate bytes, not on a human
    supplied file list or on the Jev response.  It is the re-entry guard that
    prevents a low attempt from being submitted again unchanged.
    """
    work_root = workshop_root / "transactions" / transaction_id / "work"
    if not work_root.is_dir():
        raise WorkflowError(f"transaction candidate work tree not found: {work_root}")

    files: list[dict[str, Any]] = []
    # Bind the whole Workshop candidate, not only source/test roots; documents and
    # generated implementation mirrors can change after a review. Nothing in this
    # fingerprint implies that Jev has semantically assessed every captured file.
    for path in sorted(p for p in work_root.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise WorkflowError(f"candidate contains a linked file: {path}")
        relative = path.relative_to(work_root).as_posix()
        data = path.read_bytes()
        files.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if not files:
        raise WorkflowError(f"transaction candidate work tree has no supported files: {work_root}")

    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def emit_result(output_path: Path, result: dict[str, Any], exit_code: int) -> int:
    write_json(output_path, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code


def revision_feedback(
    *,
    vector: dict[str, Any],
    attempt_review: dict[str, Any],
    tree: dict[str, Any] | None,
) -> dict[str, Any]:
    positive = vector.get("positive", {})
    negative = vector.get("negative", {})
    low_positive = [
        {"question": question, "score": row.get("score"), "required": row.get("threshold")}
        for question, row in positive.items()
        if not row.get("passed", False)
    ]
    high_negative = [
        {"question": question, "score": row.get("score"), "maximum": row.get("threshold")}
        for question, row in negative.items()
        if not row.get("passed", False)
    ]
    feedback: dict[str, Any] = {
        "status": "REVISION_REQUIRED",
        "candidate_must_be_revised": True,
        "fresh_jev_review_required": True,
        "implementation_allowed": False,
        "low_positive_questions": low_positive,
        "high_negative_questions": high_negative,
        "missing_questions": vector.get("missing_questions", []),
        "instructions": [
            "Reason over the Jev/QM feedback and the atomic evidence before changing the candidate.",
            "Revise the existing Workshop transaction work tree; do not edit the sealed live codebase.",
            "Do not compile, apply, or start approved implementation from this receipt.",
            "After the candidate bytes change, rerun this controller with --previous-workflow and a revision note.",
        ],
    }
    response = attempt_review.get("jev_response")
    if isinstance(response, dict):
        for key in ("feedback", "rationale", "summary", "message"):
            value = response.get(key)
            if isinstance(value, (str, list, dict)) and value:
                feedback[f"jev_{key}"] = value
    if tree is not None:
        feedback["atomic_qm_feedback"] = {
            "summary": tree.get("summary"),
            "open_leaf_count": (tree.get("summary") or {}).get("open_leaf_count")
            if isinstance(tree.get("summary"), dict)
            else None,
        }
    return feedback


def resolve_path(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve()


def review_scores(review: dict[str, Any]) -> dict[str, float]:
    raw_scores = review.get("scores")
    if not isinstance(raw_scores, dict):
        response = review.get("jev_response")
        answers = response.get("answers") if isinstance(response, dict) else None
        raw_scores = answers if isinstance(answers, dict) else {}
    scores: dict[str, float] = {}
    for key, value in raw_scores.items():
        score = numeric_score(value)
        if score is not None:
            scores[str(key)] = max(0.0, min(1.0, float(score)))
    return scores


def evaluate_attempt_vector(
    scores: dict[str, float],
    *,
    positive_questions: tuple[str, ...],
    negative_questions: tuple[str, ...],
    threshold: float,
) -> dict[str, Any]:
    negative_ceiling = 1.0 - threshold
    positive: dict[str, dict[str, Any]] = {}
    negative: dict[str, dict[str, Any]] = {}

    for question in positive_questions:
        score = scores.get(question)
        positive[question] = {
            "score": score,
            "threshold": threshold,
            "passed": score is not None and score >= threshold,
            "polarity": "desired_yes",
        }
    for question in negative_questions:
        score = scores.get(question)
        negative[question] = {
            "score": score,
            "threshold": negative_ceiling,
            "passed": score is not None and score <= negative_ceiling,
            "polarity": "undesired_yes",
        }

    missing = [
        question
        for question, row in [*positive.items(), *negative.items()]
        if row["score"] is None
    ]
    passed = all(row["passed"] for row in [*positive.values(), *negative.values()])
    return {
        "threshold": threshold,
        "negative_ceiling": negative_ceiling,
        "passed": passed,
        "positive": positive,
        "negative": negative,
        "missing_questions": missing,
        "semantics": (
            "Positive questions must be >= threshold. Negative questions are undesirable propositions and must be <= "
            "1-threshold. Missing scores fail closed."
        ),
    }


def run_attempt_gate(
    *,
    attempt_script: Path,
    transaction_id: str,
    workshop_root: Path,
    config_path: Path,
    context_lines: int,
    attempt_out: Path,
    include_state: bool,
    timeout_seconds: float,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not attempt_script.is_file():
        raise WorkflowError(f"attempt-gate script not found: {attempt_script}")
    attempt_out.parent.mkdir(parents=True, exist_ok=True)
    if attempt_out.exists():
        raise WorkflowError(f"refusing to overwrite existing Jev evidence: {attempt_out}")
    script_args = [
        transaction_id,
        "--workshop-root",
        str(workshop_root),
        "--config",
        str(config_path),
        "--context-lines",
        str(max(0, context_lines)),
        "--out",
        str(attempt_out),
    ]
    if include_state:
        script_args.append("--include-state")
    try:
        returncode, stdout, stderr = run_script_entry(
            attempt_script,
            script_args,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError(
            f"attempt-gate exceeded its {timeout_seconds:.1f}s process timeout"
        ) from exc
    if returncode != 0:
        details = (stderr or stdout).strip()
        raise WorkflowError(f"attempt-gate failed with exit code {returncode}: {details}")
    if attempt_out.is_file():
        review = read_json(attempt_out)
    else:
        try:
            review = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise WorkflowError("attempt-gate produced no readable review JSON") from exc
        if not isinstance(review, dict):
            raise WorkflowError("attempt-gate output is not a JSON object")
        attempt_out.parent.mkdir(parents=True, exist_ok=True)
        attempt_out.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return review, attempt_out, {"mode": "executed", "exit_code": returncode}


def run_tomography(
    *,
    tree_script: Path,
    transaction_id: str,
    workshop_root: Path,
    config_path: Path,
    context_lines: int,
    threshold: float,
    tree_json: Path,
    tree_md: Path,
    focus_review: Path,
    search_limit: int,
    max_depth: int,
    max_nodes: int,
    max_evidence_candidates: int,
    max_children_per_node: int,
    max_focus_roots: int,
    batch_size: int,
    subprocess_timeout_seconds: float,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not tree_script.is_file():
        raise WorkflowError(f"adaptive tomography script not found: {tree_script}")
    tree_json.parent.mkdir(parents=True, exist_ok=True)
    tree_md.parent.mkdir(parents=True, exist_ok=True)
    if tree_json.exists() or tree_md.exists():
        raise WorkflowError("refusing to overwrite an existing Jev tomography result")
    script_args = [
        transaction_id,
        "--workshop-root",
        str(workshop_root),
        "--config",
        str(config_path),
        "--pass-threshold",
        str(threshold),
        "--context-lines",
        str(max(0, context_lines)),
        "--search-limit",
        str(search_limit),
        "--max-depth",
        str(max_depth),
        "--max-nodes",
        str(max_nodes),
        "--max-evidence-candidates",
        str(max_evidence_candidates),
        "--max-children-per-node",
        str(max_children_per_node),
        "--max-focus-roots",
        str(max_focus_roots),
        "--batch-size",
        str(batch_size),
        "--subprocess-timeout",
        str(subprocess_timeout_seconds),
        "--focus-review",
        str(focus_review),
        "--out-json",
        str(tree_json),
        "--out-md",
        str(tree_md),
    ]
    try:
        returncode, stdout, stderr = run_script_entry(
            tree_script,
            script_args,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError(
            f"adaptive tomography exceeded its {timeout_seconds:.1f}s process timeout"
        ) from exc
    if returncode != 0:
        details = (stderr or stdout).strip()
        raise WorkflowError(f"adaptive tomography failed with exit code {returncode}: {details}")
    if not tree_json.is_file() or not tree_md.is_file():
        raise WorkflowError("adaptive tomography completed without both tree artifacts")
    tree = read_json(tree_json)
    if str(tree.get("transaction_id", "")).strip() != transaction_id:
        raise WorkflowError("adaptive tomography transaction binding does not match the requested transaction")
    return tree, {"mode": "executed", "exit_code": returncode, "stdout": stdout.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one Workshop patch attempt, stop on low Noul for candidate revision, "
            "and route only a fresh approved attempt to the next action. No live write or apply is performed."
        )
    )
    parser.add_argument("transaction_id", help="Workshop transaction id, e.g. TXN_...")
    parser.add_argument(
        "--workshop-root",
        default=os.environ.get("KAIROS_WORKSHOP_ROOT", "."),
        help="directory containing workshop.py and transactions/",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("KAIROS_WORKSHOP_CONFIG"),
        help="authoritative workshop.config.json (or KAIROS_WORKSHOP_CONFIG)",
    )
    parser.add_argument("--attempt-script", default=str(DEFAULT_ATTEMPT_SCRIPT))
    parser.add_argument("--tree-script", default=str(DEFAULT_TREE_SCRIPT))
    parser.add_argument("--attempt-review", default=None, help="use an existing attempt-review JSON instead of calling the gate")
    parser.add_argument(
        "--previous-workflow",
        default=None,
        help=(
            "previous low/waiting workflow receipt; a fresh Jev call is allowed only after its candidate fingerprint changes"
        ),
    )
    parser.add_argument(
        "--revision-note",
        default="",
        help="short record of the Codex reasoning/action that revised the transaction candidate",
    )
    parser.add_argument("--attempt-out", default="jev_attempt_review.json")
    parser.add_argument("--tree-json", default="jev_patch_tree.json")
    parser.add_argument("--tree-md", default="jev_patch_tree.md")
    parser.add_argument("--out", default="jev_attempt_workflow.json")
    parser.add_argument(
        "--context-lines",
        type=int,
        default=DEFAULT_CONTEXT_LINES,
        help="context around changed regions; gate auto-compacts to keep state below 48 KiB",
    )
    parser.add_argument("--pass-threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--attempt-timeout", type=float, default=DEFAULT_ATTEMPT_TIMEOUT_SECONDS)
    parser.add_argument("--tree-timeout", type=float, default=DEFAULT_TREE_TIMEOUT_SECONDS)
    parser.add_argument("--tree-subprocess-timeout", type=float, default=DEFAULT_TREE_SUBPROCESS_TIMEOUT_SECONDS)
    parser.add_argument("--tree-search-limit", type=int, default=DEFAULT_TREE_SEARCH_LIMIT)
    parser.add_argument("--tree-max-depth", type=int, default=DEFAULT_TREE_MAX_DEPTH)
    parser.add_argument("--tree-max-nodes", type=int, default=DEFAULT_TREE_MAX_NODES)
    parser.add_argument(
        "--tree-max-evidence-candidates",
        type=int,
        default=DEFAULT_TREE_MAX_EVIDENCE_CANDIDATES,
    )
    parser.add_argument(
        "--tree-max-children-per-node",
        type=int,
        default=DEFAULT_TREE_MAX_CHILDREN_PER_NODE,
    )
    parser.add_argument(
        "--tree-max-focus-roots",
        type=int,
        default=DEFAULT_TREE_MAX_FOCUS_ROOTS,
        help="maximum number of lowest Jev gate dimensions sent to KAIROS in one bounded focus pass",
    )
    parser.add_argument("--tree-batch-size", type=int, default=DEFAULT_TREE_BATCH_SIZE)
    parser.add_argument("--include-state", action="store_true")
    parser.add_argument(
        "--require-positive",
        action="append",
        dest="positive_questions",
        help="desired-YES attempt question; repeatable; replaces the generic default set",
    )
    parser.add_argument(
        "--reject-positive",
        action="append",
        dest="negative_questions",
        help="undesired-YES attempt question; repeatable; replaces the generic default set",
    )
    parser.add_argument(
        "--no-tomography",
        action="store_true",
        help="do not run tomography on a low vector; the result remains blocked",
    )
    args = parser.parse_args()

    if not args.positive_questions or not args.negative_questions:
        parser.error("explicit --require-positive and --reject-positive policy is required; no private project defaults are shipped")
    if not (0.0 < args.pass_threshold <= 1.0):
        parser.error("--pass-threshold must be in (0,1]")
    for name in (
        "attempt_timeout",
        "tree_timeout",
        "tree_subprocess_timeout",
    ):
        if getattr(args, name) <= 0.0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    for name in (
        "tree_search_limit",
        "tree_max_depth",
        "tree_max_nodes",
        "tree_max_evidence_candidates",
        "tree_max_children_per_node",
        "tree_max_focus_roots",
        "tree_batch_size",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.previous_workflow and args.attempt_review:
        parser.error("--previous-workflow requires a fresh Jev gate call; do not combine it with --attempt-review")
    if not args.config:
        print(json.dumps({"schema": "werkfaden-jev-workshop-attempt-workflow-error/v1", "error": "--config or KAIROS_WORKSHOP_CONFIG is required"}, indent=2), file=sys.stderr)
        return 2

    positive_questions = tuple(args.positive_questions or DEFAULT_POSITIVE_QUESTIONS)
    negative_questions = tuple(args.negative_questions or DEFAULT_NEGATIVE_QUESTIONS)
    output_path = resolve_path(args.out)
    attempt_out = resolve_path(args.attempt_out)
    tree_json = resolve_path(args.tree_json)
    tree_md = resolve_path(args.tree_md)
    workshop_root = resolve_path(args.workshop_root)
    config_path = resolve_path(args.config)
    attempt_script = resolve_path(args.attempt_script)
    tree_script = resolve_path(args.tree_script)

    try:
        current_candidate = candidate_fingerprint(workshop_root, args.transaction_id)
        previous_workflow_path = resolve_path(args.previous_workflow) if args.previous_workflow else None

        # Reusing the normal output path is safe continuation syntax after this
        # controller has emitted a low/waiting receipt.  Old receipts without a
        # candidate fingerprint are ignored because they cannot prove whether
        # the transaction work tree changed.
        if previous_workflow_path is None and not args.attempt_review and output_path.is_file():
            try:
                existing = read_json(output_path)
            except WorkflowError:
                existing = {}
            if (
                str(existing.get("transaction_id", "")).strip() == args.transaction_id
                and existing.get("decision")
                in {"RECONSIDER_AND_VERIFY_ATOMIC_ACTIONS", "WAITING_FOR_CANDIDATE_REVISION"}
                and isinstance(existing.get("candidate_fingerprint"), dict)
                and str(existing["candidate_fingerprint"].get("sha256", "")).strip()
            ):
                previous_workflow_path = output_path

        continuation: dict[str, Any] = {"mode": "initial", "candidate_changed": False}
        iteration = 1
        if previous_workflow_path is not None:
            previous = read_json(previous_workflow_path)
            previous_transaction_id = str(previous.get("transaction_id", "")).strip()
            if previous_transaction_id != args.transaction_id:
                raise WorkflowError(
                    "previous workflow transaction binding does not match the requested transaction: "
                    f"{previous_transaction_id!r} != {args.transaction_id!r}"
                )
            if previous.get("decision") not in {
                "RECONSIDER_AND_VERIFY_ATOMIC_ACTIONS",
                "WAITING_FOR_CANDIDATE_REVISION",
            }:
                raise WorkflowError(
                    "--previous-workflow must point to a low or waiting receipt; "
                    "an approved receipt cannot be used to bypass a new attempt"
                )
            previous_fingerprint = previous.get("candidate_fingerprint")
            previous_sha256 = (
                previous_fingerprint.get("sha256")
                if isinstance(previous_fingerprint, dict)
                else None
            )
            if not isinstance(previous_sha256, str) or not previous_sha256.strip():
                raise WorkflowError(
                    "previous workflow receipt has no candidate fingerprint; "
                    "a fresh revision cannot be proven from it"
                )
            try:
                previous_iteration = int(previous.get("iteration", 1))
            except (TypeError, ValueError) as exc:
                raise WorkflowError("previous workflow receipt has an invalid iteration number") from exc
            if previous_iteration < 1:
                raise WorkflowError("previous workflow receipt has an invalid iteration number")
            if previous_sha256 == current_candidate["sha256"]:
                waiting_result = {
                    "schema": "werkfaden-jev-workshop-attempt-workflow/v1",
                    "transaction_id": args.transaction_id,
                    "iteration": previous_iteration,
                    "decision": "WAITING_FOR_CANDIDATE_REVISION",
                    "next_step": "EDIT_TRANSACTION_CANDIDATE",
                    "implementation_routing_allowed": False,
                    "candidate_fingerprint": current_candidate,
                    "previous_workflow": {
                        "path": str(previous_workflow_path),
                        "sha256": sha256_file(previous_workflow_path),
                        "decision": previous.get("decision"),
                        "candidate_sha256": previous_sha256,
                    },
                    "revision_handoff": {
                        "status": "WAITING_FOR_CANDIDATE_REVISION",
                        "candidate_must_be_revised": True,
                        "fresh_jev_review_required": True,
                        "instructions": [
                            "The transaction candidate bytes are unchanged since the last low Jev review.",
                            "Do not send the same candidate to Jev again.",
                            "Revise the transaction work tree, then rerun with --previous-workflow and --revision-note.",
                        ],
                    },
                    "action_boundary": (
                        "No Jev call, implementation, live edit, compile, or apply is allowed from this unchanged candidate."
                    ),
                }
                return emit_result(output_path, waiting_result, EXIT_RECONSIDERATION_REQUIRED)
            if not args.revision_note.strip():
                raise WorkflowError(
                    "--revision-note is required after a low Jev review so the candidate revision is auditable"
                )
            iteration = previous_iteration + 1
            continuation = {
                "mode": "fresh_after_candidate_revision",
                "candidate_changed": True,
                "previous_workflow": str(previous_workflow_path),
                "previous_workflow_sha256": sha256_file(previous_workflow_path),
                "previous_candidate_sha256": previous_sha256,
                "revision_note": args.revision_note.strip(),
            }

        if args.attempt_review:
            # An externally supplied response has no provider-validated binding to
            # this candidate. Keep that historical diagnostic file, but refuse to
            # convert it into approval. Only a fresh direct request is admissible.
            raise WorkflowError("--attempt-review cannot authorize a candidate; use a fresh direct Jev call")
        else:
            attempt_review, attempt_review_path, attempt_source = run_attempt_gate(
                attempt_script=attempt_script,
                transaction_id=args.transaction_id,
                workshop_root=workshop_root,
                config_path=config_path,
                context_lines=args.context_lines,
                attempt_out=attempt_out,
                include_state=args.include_state,
                timeout_seconds=args.attempt_timeout,
            )

        after_review = candidate_fingerprint(workshop_root, args.transaction_id)
        if after_review["sha256"] != current_candidate["sha256"]:
            raise WorkflowError("candidate changed while Jev was reviewing; the response is not valid for the current work tree")
        review_transaction_id = str(attempt_review.get("transaction_id", "")).strip()
        if review_transaction_id != args.transaction_id:
            raise WorkflowError(
                "attempt review transaction binding does not match the requested transaction: "
                f"{review_transaction_id!r} != {args.transaction_id!r}"
            )

        scores = review_scores(attempt_review)
        vector = evaluate_attempt_vector(
            scores,
            positive_questions=positive_questions,
            negative_questions=negative_questions,
            threshold=args.pass_threshold,
        )
        result: dict[str, Any] = {
            "schema": "werkfaden-jev-workshop-attempt-workflow/v1",
            "transaction_id": args.transaction_id,
            "iteration": iteration,
            "candidate_fingerprint": current_candidate,
            "continuation": continuation,
            "attempt_review": {
                "path": str(attempt_review_path),
                "sha256": sha256_file(attempt_review_path),
                "schema": attempt_review.get("schema"),
                **attempt_source,
            },
            "attempt_vector": vector,
            "action_boundary": (
                "This receipt routes the next coding/Workshop decision only. It never authorizes direct live edits, "
                "does not replace prepare/verify/scientific gates, and does not invoke apply."
            ),
        }

        if vector["passed"]:
            result.update(
                {
                    "decision": "APPROVED_FOR_NEXT_ACTION",
                    "next_step": "START_APPROVED_IMPLEMENTATION",
                    "implementation_routing_allowed": True,
                    "live_apply_authorized": False,
                    "tomography": {"status": "NOT_REQUIRED", "reason": "expected attempt vector passed"},
                }
            )
            return emit_result(output_path, result, 0)

        result.update(
            {
                "decision": "RECONSIDER_AND_VERIFY_ATOMIC_ACTIONS",
                "next_step": "REVISE_CANDIDATE_AFTER_JEV_QM_FEEDBACK",
                "implementation_routing_allowed": False,
                "live_apply_authorized": False,
                "tomography": {"status": "PENDING"},
            }
        )
        if args.no_tomography:
            result["tomography"] = {"status": "NOT_RUN", "reason": "--no-tomography"}
            result["revision_handoff"] = revision_feedback(
                vector=vector,
                attempt_review=attempt_review,
                tree=None,
            )
            return emit_result(output_path, result, EXIT_RECONSIDERATION_REQUIRED)

        tree, tree_run = run_tomography(
            tree_script=tree_script,
            transaction_id=args.transaction_id,
            workshop_root=workshop_root,
            config_path=config_path,
            context_lines=args.context_lines,
            threshold=args.pass_threshold,
            tree_json=tree_json,
            tree_md=tree_md,
            focus_review=attempt_review_path,
            search_limit=args.tree_search_limit,
            max_depth=args.tree_max_depth,
            max_nodes=args.tree_max_nodes,
            max_evidence_candidates=args.tree_max_evidence_candidates,
            max_children_per_node=args.tree_max_children_per_node,
            max_focus_roots=args.tree_max_focus_roots,
            batch_size=args.tree_batch_size,
            subprocess_timeout_seconds=args.tree_subprocess_timeout,
            timeout_seconds=args.tree_timeout,
        )
        result["tomography"] = {
            "status": "COMPLETED_FOR_RECONSIDERATION",
            "json": str(tree_json),
            "json_sha256": sha256_file(tree_json),
            "markdown": str(tree_md),
            "markdown_sha256": sha256_file(tree_md),
            "summary": tree.get("summary"),
            "run": tree_run,
        }
        result["revision_handoff"] = revision_feedback(
            vector=vector,
            attempt_review=attempt_review,
            tree=tree,
        )
        return emit_result(output_path, result, EXIT_RECONSIDERATION_REQUIRED)
    except WorkflowError as exc:
        error = {"schema": "werkfaden-jev-workshop-attempt-workflow-error/v1", "error": str(exc)}
        print(json.dumps(error, indent=2, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
