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
    ("s-purpose", "PURPOSE", "Goal and limits"),
    ("s-mapping", "SOURCE MAPPING", "Paths and baseline"),
    ("s-drift", "HISTORICAL DRIFT", "Drift or none"),
    ("s-ownership", "OWNERSHIP", "State authority"),
    ("s-graph-metadata", "GRAPH METADATA", "Proven graph facts"),
    ("s-symbols", "PUBLIC SYMBOLS", "Selected sources"),
    ("s-inputs", "INPUTS", "Inputs and baseline"),
    ("s-preconditions", "PRECONDITIONS", "Preconditions"),
    ("s-structures", "STRUCTURES", "Bound structures"),
    ("s-constants", "CONSTANTS", "Constants or none"),
    ("s-primitives", "PRIMITIVES", "Primitives or none"),
    ("s-execution", "EXECUTION", "Events and findings"),
    ("s-output", "OUTPUT", "State and outputs"),
    ("s-failures", "FAILURES", "Failures and gaps"),
    ("s-determinism", "DETERMINISM", "Candidate hashes"),
    ("s-boundary", "AUTHORITY BOUNDARY", "Claim boundary"),
    ("s-tests", "TESTS", "Tests and limits"),
    ("s-acceptance", "ACCEPTANCE", "Outcome and next work"),
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
    goal_id: str | None,
    milestone_id: str | None,
    task_id: str | None,
    loop: int | None,
    revision: int = 1,
    task_reference_exists: bool = False,
    updated_at: str | None = None,
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
    scope_bound = bool(goal_id and milestone_id and task_id and type(loop) is int and loop > 0)
    q = chr(96)
    recycle = transaction.get("recycling") if isinstance(transaction.get("recycling"), dict) else {}
    reviewed = recycle.get("status") == "REVIEWED"
    decisions = recycle.get("decisions", []) if reviewed else []
    if not isinstance(decisions, list):
        decisions = []
    dispositions = {
        name: sum(1 for row in decisions if row.get("disposition") == name)
        for name in ("RETAIN_FOR_REUSE", "RETAIN_EVIDENCE", "REJECT_FOR_REUSE")
    }
    graph_data = "none"
    if reviewed:
        graph_data = q * 3 + "json\n" + json.dumps({
            "schema": "werkfaden-tx-recycling-index/v1",
            "transaction_id": transaction_id,
            "analysis_sha256": recycle.get("analysis_sha256"),
            "inventory_sha256": recycle.get("inventory_sha256"),
            "decisions": decisions,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n" + q * 3
    summary = "\n".join(
        "- " + str(row.get("path")) + " [" + str(row.get("disposition")) + "] "
        + str(row.get("reason"))
        for row in decisions[:20]
    ) or "- No atomic decisions recorded."
    if len(decisions) > 20:
        summary += "\n- " + str(len(decisions) - 20) + " further decisions in s-graph-metadata."
    attempt = (
        "Declared plan: " + str(recycle.get("planned_approach") or purpose)
        + "\nActual attempted approach: " + str(recycle.get("attempted_approach") or "not yet evaluated")
        + "\nWhy this approach was retained or rejected: " + str(recycle.get("outcome_reason") or "not yet evaluated")
    )
    rejected = "\n".join(
        "- " + str(row.get("path")) + ": " + str(row.get("reason"))
        for row in decisions if row.get("disposition") == "REJECT_FOR_REUSE"
    )[:3000]
    content = {
        "s-purpose": "- Declared purpose: " + purpose + "\n- Actual state: " + status
                     + "\n- Recycling intent: " + str(recycle.get("planned_approach") or "unreviewed"),
        "s-mapping": "- Selected source paths: " + _json(transaction.get("sources", []), 1000)
                     + "\n- Baseline package SHA-256: " + str(transaction.get("baseline_package_sha256") or "none"),
        "s-drift": "none",
        "s-ownership": "The Workshop owns recorded transaction state; KAIROS projects its Markdown evidence. A JEV verdict is not a production deployment receipt. " + ("Historical scope is checkout-bound." if scope_bound else "Historical scope is unresolved; no current Task ownership is asserted."),
        "s-graph-metadata": graph_data,
        "s-symbols": "Selected files (not a verified symbol inventory): "
                     + _json(transaction.get("sources", []), 1000),
        "s-inputs": "- Dependent tests: " + _json(transaction.get("dependent_tests", []), 1000)
                    + "\n- Selected documents: " + _json(transaction.get("documents", []), 1000),
        "s-preconditions": "Only explicitly recorded preconditions and evidence may be cited. Missing proof is not a pass.",
        "s-structures": "none; consult the actual implementation blueprints",
        "s-constants": "none",
        "s-primitives": "none",
        "s-execution": attempt + "\n\nAtomic dispositions (overview):\n" + summary
                       + "\n\nOther declared notes:\n" + _json((transaction.get("transaction_notes") or [])[-30:], 6000)
                       + "\n\nRecent machine events:\n" + _json((transaction.get("journal") or [])[-8:], 4000),
        "s-output": "- Technical TX state: " + status
                    + "\n- Postcheck package: " + str(transaction.get("postcheck_package_sha256") or "none")
                    + "\n- Heartbeat: " + str((transaction.get("heartbeat") or {}).get("heartbeat_id") or "none")
                    + "\n- Recycling decisions: " + json.dumps(dispositions, sort_keys=True)
                    + "\n- Preserved vault manifest: " + str(recycle.get("vault_manifest_sha256") or "none")
                    + "\n- Compaction: " + _json(transaction.get("compaction") or "none", 1400),
        "s-failures": "- Recorded failure: " + _json(transaction.get("failure") or "none", 1400)
                      + "\n- Outcome rationale: " + str(recycle.get("outcome_reason") or "not yet reviewed")
                      + "\n- Rejected code or approaches:\n" + (rejected or "none"),
        "s-determinism": "- Prepared package: " + str(transaction.get("prepared_work_package_sha256") or "none")
                         + "\n- Verified package: " + str(transaction.get("verified_work_package_sha256") or "none")
                         + "\n- Recycling inventory SHA-256: " + str(recycle.get("inventory_sha256") or "none")
                         + "\n- Recycling review SHA-256: " + str(recycle.get("analysis_sha256") or "none")
                         + "\n- Original scope receipt SHA-256: " + str((transaction.get("original_scope") or {}).get("source_receipt_sha256") or "none"),
        "s-boundary": "This records a Workshop attempt and reviewed disposition coverage, not semantic correctness, provider authenticity, or production deployment.",
        "s-tests": "- Shadow verification: " + str((transaction.get("shadow") or {}).get("status") or "unknown")
                   + "\n- Scientific parity: " + str((transaction.get("scientific_parity") or {}).get("status") or "not recorded"),
        "s-acceptance": (
            "A mechanically checked recycling review exists. Cleanup still requires the exact current inventory and the verified vault archive."
            if reviewed else
            "Recycling is outstanding; there is no authority to discard the materialized worktree."
        ) + (" Workshop postcheck was recorded; deployment beyond the Workshop corpus remains unproven."
             if document_state == "completed" else " No successful Workshop postcheck is established."),
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
        "route": (f"{goal_id}/{milestone_id}/{task_id}/{artifact_id}"
                  if scope_bound else f"HISTORICAL_UNRESOLVED/{artifact_id}"),
        "scope_status": "BOUND" if scope_bound else "SCOPE_UNRESOLVED",
        **({"scope_evidence_boundary":
            "Original task/goal/loop unverified; current.json was not used"
        } if not scope_bound else {}),
        # Exact checkout scope bytes remain in TX state and are named below.

        "updated_at": updated_at or transaction.get("updated_at") or transaction.get("created_at") or utc_now(),
        "capsule": f"TX {transaction_id}: {purpose[:125]}; state {status}.",
        "claim_boundary": "Recorded Workshop state and selected evidence only; not JEV authenticity, semantic truth, deployment or criterion closure.",
        "entities": [artifact_id, task_id] if scope_bound else [artifact_id],
        "facets": ["workshop", "transaction-history", "candidate-lineage"],
        "criteria": [],
        "does_not_answer": ["production deployment status", "unobserved source correctness"],
        "transaction_id": transaction_id,
        "workshop_state": status,
        "baseline_package_sha256": str(transaction.get("baseline_package_sha256") or ""),
        "verified_work_package_sha256": str(transaction.get("verified_work_package_sha256") or ""),
        "recycling_analysis_sha256": str(recycle.get("analysis_sha256") or ""),
        "tx_event_id": str(transaction.get("tx_event_id") or ""),

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
        } if task_reference_exists and scope_bound else {}),
        **({"loop": loop, "goal": goal_id, "milestone": milestone_id, "task": task_id}
           if scope_bound else {}),
        "search_contract": [
            {"query": f"What was the observed Workshop outcome of {artifact_id}?", "expected": f"{artifact_id}#s-output", "required_top_k": 5},
            {"query": f"What failures were recorded for {artifact_id}?", "expected": f"{artifact_id}#s-failures", "required_top_k": 5},
        ],
    }
    return render_document(metadata, f"WORKSHOP TRANSACTION {transaction_id}", sections)
