"""Canonical, evidence-limited Workshop transaction documents.

This renders the 18 required section anchors. It does not infer application
success, JEV authority or unrelated architecture from filenames or scores.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .templates import render_document, pointer
from .util import utc_now


SECTION_SPECS = (
    ("s-purpose", "PURPOSE", "Declared goal and limits"),
    ("s-mapping", "SOURCE MAPPING", "Source paths and baseline"),
    ("s-drift", "HISTORICAL DRIFT", "Measured drift or none"),
    ("s-ownership", "OWNERSHIP", "Owned state and authorities"),
    ("s-graph-metadata", "GRAPH METADATA", "Directly proven graph facts"),
    ("s-symbols", "PUBLIC SYMBOLS", "Selected sources, no inferred API"),
    ("s-inputs", "INPUTS", "Candidate and baseline inputs"),
    ("s-preconditions", "PRECONDITIONS", "Preconditions and open gaps"),
    ("s-structures", "STRUCTURES", "Evidence-bound structures"),
    ("s-constants", "CONSTANTS", "Proven constants or none"),
    ("s-primitives", "PRIMITIVES", "Proven primitives or none"),
    ("s-execution", "EXECUTION", "Recorded events and findings"),
    ("s-output", "OUTPUT", "Actual state and output evidence"),
    ("s-failures", "FAILURES", "Observed failures and gaps"),
    ("s-determinism", "DETERMINISM", "Bound candidate identifiers"),
    ("s-boundary", "AUTHORITY BOUNDARY", "Explicit claim boundary"),
    ("s-tests", "TESTS", "Recorded tests and their limits"),
    ("s-acceptance", "ACCEPTANCE", "Outcome and open obligations"),
)


def document_id(transaction_id: str) -> str:
    normalized = transaction_id.upper()
    if not re.fullmatch(r"TXN_[0-9A-F]{24}", normalized):
        raise ValueError("invalid Workshop transaction id")
    return "TRANSACTION_" + normalized


def document_path(transaction_id: str) -> str:
    return "reports/" + document_id(transaction_id) + ".md"


def _json(value: Any, limit: int = 8000) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, default=str)
    return raw if len(raw) <= limit else raw[:limit] + "\n... [TRUNCATED; full evidence remains in TX journal]"


def render_transaction_document(
    *,
    transaction: dict[str, Any],
    workspace_id: str,
    goal_id: str,
    milestone_id: str,
    task_id: str,
    loop: int,
    revision: int = 1,
    task_reference_exists: bool = False,
) -> str:
    transaction_id = str(transaction["transaction_id"])
    artifact_id = document_id(transaction_id)
    status = str(transaction.get("state", "UNKNOWN"))
    if status == "POSTCHECK_VERIFIED":
        document_state = "completed"
    elif status in {"ABORTED", "ABORTED_STALE_BASELINE", "ABORTED_ORPHANED_CHECKOUT", "ROLLED_BACK"}:
        document_state = "failed"
    elif status == "RECOVERY_REQUIRED":
        document_state = "blocked"
    else:
        document_state = "partial"
    purpose = str(transaction.get("purpose") or "No verified transaction purpose recorded")
    content = {
        "s-purpose": f"- Declared purpose: {purpose}\n- Actual observed state: `{status}`",
        "s-mapping": "- Selected source paths: `" + _json(transaction.get("sources", []), 1000) + "`\n- Baseline package SHA-256: `" + str(transaction.get("baseline_package_sha256") or "none") + "`",
        "s-drift": "none",
        "s-ownership": "The Workshop owns recorded transaction state. KAIROS owns the projected document index. A Guardian or JEV verdict is distinct from deployment evidence.",
        "s-graph-metadata": "none",
        "s-symbols": "Selected source paths (not a verified symbol inventory): `"+ _json(transaction.get("sources", []), 1000) + "`",
        "s-inputs": "- Selected dependent tests: `" + _json(transaction.get("dependent_tests", []), 1000) + "`\n- Selected context documents: `" + _json(transaction.get("documents", []), 1000) + "`",
        "s-preconditions": "Recorded prerequisites are limited to named baseline, lease, package and review evidence. Absence of a claim is not a pass.",
        "s-structures": "none; consult selected implementation blueprints for source structures",
        "s-constants": "none",
        "s-primitives": "none",
        "s-execution": "Declared findings and reuse annotations (not automatically verified):\n\n```json\n" + _json((transaction.get("transaction_notes") or [])[-40:], 10000) + "\n```\nMost recent machine events:\n\n```json\n" + _json((transaction.get("journal") or [])[-12:], 8000) + "\n```",
        "s-output": "- Transaction state: `" + status + "`\n- Postcheck corpus package: `" + str(transaction.get("postcheck_package_sha256") or "none") + "`\n- Heartbeat: `" + str((transaction.get("heartbeat") or {}).get("heartbeat_id") or "none") + "`\n- Compaction: `" + _json(transaction.get("compaction") or "none", 1400) + "`",
        "s-failures": "Recorded failure: `" + _json(transaction.get("failure") or "none", 2000) + "`",
        "s-determinism": "- Prepared candidate package: `" + str(transaction.get("prepared_work_package_sha256") or "none") + "`\n- Verified candidate package: `" + str(transaction.get("verified_work_package_sha256") or "none") + "`",
        "s-boundary": "This is a Workshop attempt record, not an independent proof of semantic correctness, JEV provider identity, scientific validity, production deployment or full code-to-binary correspondence.",
        "s-tests": "- Shadow result: `" + str((transaction.get("shadow") or {}).get("status") or "unknown") + "`\n- Scientific parity: `" + str((transaction.get("scientific_parity") or {}).get("status") or "not recorded") + "`",
        "s-acceptance": ("Recorded postcheck exists; deployment beyond the Workshop corpus is not established." if document_state == "completed" else "No successful Workshop postcheck is established by this record. Any retained candidate and its proof must be inventoried before cleanup."),
    }
    sections = [{"id": key, "title": title, "capsule": capsule, "content": content[key]}
                for key, title, capsule in SECTION_SPECS]
    metadata = {
        "schema": "kairos-context/v1",
        "id": artifact_id,
        "type": "transaction",
        "revision": revision,
        "state": document_state,
        "authority": "execution_evidence",
        "workspace": workspace_id,
        "route": f"{goal_id}/{milestone_id}/{task_id}/{artifact_id}",
        "loop": loop,
        "goal": goal_id,
        "milestone": milestone_id,
        "task": task_id,
        "updated_at": utc_now(),
        "capsule": f"Workshop attempt {transaction_id}: {purpose[:280]}; observed state {status}.",
        "claim_boundary": "Recorded Workshop state and selected evidence only; not JEV authenticity, semantic truth, deployment or criterion closure.",
        "entities": [artifact_id, task_id],
        "facets": ["workshop", "transaction-history", "candidate-lineage"],
        "criteria": [],
        "does_not_answer": ["production deployment status", "unobserved source correctness"],
        "transaction_id": transaction_id,
        "workshop_state": status,
        "baseline_package_sha256": str(transaction.get("baseline_package_sha256") or ""),
        "verified_work_package_sha256": str(transaction.get("verified_work_package_sha256") or ""),
        "answers": [
            {"intent": "implementation_location", "question": f"Which source paths are selected by {artifact_id}?", "target": "s-symbols"},
            {"intent": "failure_semantics", "question": f"What failures were recorded for {artifact_id}?", "target": "s-failures"},
            {"intent": "determinism", "question": f"Which package hashes are bound to {artifact_id}?", "target": "s-determinism"},
            {"intent": "boundary", "question": f"What does {artifact_id} not establish?", "target": "s-boundary"},
            {"intent": "transaction_outcome", "question": f"What was the observed Workshop outcome of {artifact_id}?", "target": "s-output"},
            {"intent": "transaction_lineage", "question": f"Which previous patches or transaction events does {artifact_id} record?", "target": "s-execution"},
        ],
        "refs": ({
            "task": pointer(f"tasks/task_{task_id}.md", section="s-objective", artifact_id=task_id,
                            version="dynamic", relation="belongs_to", tags=("task", "scope"))
        } if task_reference_exists else {}),
        "search_contract": [
            {"query": f"What was the observed Workshop outcome of {artifact_id}?", "expected": f"{artifact_id}#s-output", "required_top_k": 5},
            {"query": f"What failures were recorded for {artifact_id}?", "expected": f"{artifact_id}#s-failures", "required_top_k": 5},
        ],
    }
    return render_document(metadata, f"WORKSHOP TRANSACTION {transaction_id}", sections)
