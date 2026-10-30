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



HISTORY_METHOD_ID = "WERKFADEN_TRANSACTION_HISTORY_METHOD"
HISTORY_METHOD_PATH = "docs/HISTORICAL_TRANSACTION_SCOPE.md"
HISTORY_METHOD_INSTANCE_TIMESTAMP_POLICY = 'For a fixed workspace and method revision, updated_at is the only instance-owned header variance accepted by the canonical matcher. It must come from the existing native artifact metadata, and the whole document must equal the current renderer for that exact timestamp with a matching artifact path, content hash and reconciled projection. Unpublished timestamp edits, other metadata/body drift and older method revisions are refused. Timestamp variance never grants a new publication permit or rewrites the live method.'


def render_history_contract(workspace_id: str, *, updated_at: str | None = None) -> str:
    """One workspace-bound policy; only a proven native instance may supply its timestamp."""
    if updated_at is not None and (not isinstance(updated_at, str) or not 1 <= len(updated_at) <= 128):
        raise ValueError("canonical history method timestamp must be a bounded string")
    metadata = {
        "schema": "kairos-context/v1", "id": HISTORY_METHOD_ID, "type": "documentation",
        "revision": 4, "state": "active", "authority": "operating_contract", "workspace": workspace_id,
        "route": workspace_id + "/" + HISTORY_METHOD_ID, "updated_at": updated_at if updated_at is not None else "2026-10-29T00:00:00Z",
        "capsule": "Canonical historical intake with verified-origin or quarantine-only old-anchor admission, separate binding and exact recovery.",
        "claim_boundary": "Framework method only; no transaction provenance, project success, historical execution, provider attestation or deployment is certified by this document.",
        "entities": [HISTORY_METHOD_ID, "tx-scope-inspect", "tx-history-intake", "tx-scope-bind"],
        "facets": ["transaction-history", "legacy-intake", "scope-provenance"], "criteria": [],
        "does_not_answer": ["the true scope of an unobserved transaction", "missing historical facts"],
        "answers": [
            {"intent": "workflow", "question": "How is a legacy transaction explicitly admitted and later scope bound?", "target": "s-workflow"},
            {"intent": "evidence", "question": "Which historical scope evidence can authorize assignment?", "target": "s-evidence"},
            {"intent": "recovery", "question": "How is interrupted historical scope projection recovered?", "target": "s-recovery"}],
        "search_contract": [{"query": "How is a legacy transaction explicitly admitted and later scope bound?", "expected": HISTORY_METHOD_ID + "#s-workflow", "required_top_k": 5}],
    }
    sections = [
        {"id": "s-workflow", "title": "WORKFLOW", "capsule": "Explicit intake preserves the record before a separate verified assignment.",
         "content": "Use the canonical Workshop configuration. Run `tx-scope-inspect TX` and review its exact report hash. Run `tx-history-intake TX --expected-report-sha256 HASH --reason TEXT` to preserve the previous state and document in an immutable receipt and quarantine the record under `HISTORICAL_UNRESOLVED/TX`. The explanation requires 40-2000 characters. Inspect again after intake. Only a complete, consistent VERIFIED inspection permits `tx-scope-bind TX --expected-report-sha256 HASH --reason TEXT`. Binding is a new material event. Missing or conflicting records remain unassigned; current.json and active task state never supply their missing history."},
        {"id": "s-evidence", "title": "EVIDENCE", "capsule": "Byte identity and historical scope claims have different evidentiary limits.",
         "content": "Inspect controlled TX state and snapshots, Guardian evidence, pinned Task/Goal receipts, pinned loop archives and the associated document in order. Native complete-TX vault manifests and exact state objects can retain a lost original observation. Legacy Guardian fields or a document declaration alone cannot authorize assignment. A successful binding also checks the actual Task/Goal source ownership and preserves those bytes. Receipt hashes prove identity and recorded consistency, not independent historical truth. Scope receipts retain previous bytes and contradictory evidence; no API accepts arbitrary Goal/Task/Loop values as assignment arguments."},
        {"id": "s-recovery", "title": "RECOVERY", "capsule": "Reconcile the persisted event without applying code again or renewing mutation authority.",
         "content": "Intake and binding require a technically terminal, unleased TX and the retention writer lock. Outbox staging precedes the state commit. If publication fails after the state is saved, retry the exact operation with the same inspected hash and explanation, or use `tx-document-reconcile TX`. Do not repeat Apply. A committed operation has one event identity; replay does not create a new event or false document revision. Explicitly adopted history keeps its projection obligation for later annotations and compaction even under a legacy optional default. Pending mandatory documentation blocks new checkout. Conflict quarantine is retained until historical evidence is consistent; it never transfers ownership to the active Task."},
    ]
    metadata["entities"].extend(["werkfaden-tx-legacy-event-admission/v1", "werkfaden-tx-legacy-event-admission/v2"])
    metadata["facets"].extend(["legacy-event-format", "unattested-material-boundary"])
    metadata["answers"].append({"intent": "workflow", "question": "How are pre-material-epoch transaction event anchors migrated?", "target": "s-workflow"})
    metadata["search_contract"].append({"query": "How are pre-material-epoch transaction event anchors migrated?", "expected": HISTORY_METHOD_ID + "#s-workflow", "required_top_k": 5})
    sections[0]["content"] += "\n\nThe same explicit INTAKE admits two exact first-anchor field families without stored history: pre-material-epoch-anchor/v0 lacks old epoch, baseline, time and projection-obligation bindings; v1 records baseline/time/boolean obligation but still lacks the material epoch. Verified-origin admission/v1 requires complete VERIFIED recorded scope. Quarantine-only admission/v2 accepts an exact native MISSING or CONFLICT inspection, or witnessed origin loss, without assigning Task, Goal or Loop; an exact reviewed report hash is mandatory in both modes. The versioned werkfaden-tx-legacy-event-admission/v1 or /v2 declaration is stored in the immutable intake receipt, binding exact old state bytes and event identity. Original scope and old event bytes are preserved; separate BIND remains required. Partial, inconsistent or other old formats fail closed."
    sections[1]["content"] += "\n\nMissing old material epochs, history-prefix hashes and the v0 baseline/time/obligation fields remain explicitly UNATTESTED. The new INTAKE event proves only the newly observed current state and its projection. It does not manufacture old execution, an old baseline binding or a committed Apply. Generic current-event validation remains strict; a mere old event self-hash or scope inspection never grants deployment authority."
    sections[2]["content"] += "\n\nCanonical method revision4 and its renderer must be installed together through verified source/document migration. An existing nonmatching method is TX_HISTORY_METHOD_DRIFT, requiring verified document migration; INTAKE does not silently overwrite it. Publication grants bind exact canonical content bytes. Receipt admission declarations and raw preimages are revalidated during replay, retaining one current event identity without repeating Apply."
    sections[1]["content"] += "\n\nQuarantine-only admission preserves missing/conflicting claims and raw prior bytes but grants no BIND or success attribution. Historical origin and any unbound old scope-receipt reference remain explicitly UNATTESTED. Later binding still requires a fresh complete VERIFIED inspection and actual Task/Goal source proof; unknown field families and inconsistent event identities remain refused."
    sections[2]["content"] += "\n\n" + HISTORY_METHOD_INSTANCE_TIMESTAMP_POLICY
    return render_document(metadata, "Historical transaction scope: canonical intake and binding method", sections)


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


def _json(value: Any, limit: int | None = 8000) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, default=str)
    truncated = limit is not None and len(raw) > limit
    if truncated:
        raw = raw[:limit] + "\n... [TRUNCATED; full evidence remains in TX journal]"
    # Machine-recorded paths/references are literal evidence, not live links.
    # A longer delimiter prevents data containing backticks from ending it.
    longest = max((len(match.group()) for match in re.finditer(r"`+", raw)), default=0)
    fence = "`" * max(3, longest + 1)
    return "\n" + fence + ("text" if truncated else "json") + "\n" + raw + "\n" + fence


def _literal_text(value: Any) -> str:
    """Preserve declared prose exactly without granting it Markdown authority."""
    raw = str(value)
    longest = max((len(match.group()) for match in re.finditer(r"`+", raw)), default=0)
    fence = "`" * max(3, longest + 1)
    return "\n" + fence + "text\n" + raw + "\n" + fence


def _event_obligations(transaction: dict[str, Any]) -> str:
    """Retain every identity without duplicating full replay endpoint payloads."""
    history = [{k: event.get(k) for k in ("sequence", "event_id", "event", "phase", "previous_event_id")}
               for event in transaction.get("tx_event_history", [])]
    resolutions = [{"event_id": item.get("event", {}).get("event_id"),
                    "sequence": item.get("event", {}).get("sequence"), "outcome": item.get("outcome"),
                    "intent_sha256": item.get("intent_sha256")}
                   for item in transaction.get("tx_event_resolutions", [])]
    # No truncation: losing a pending identity would defeat the recovery proof.
    return "\n\nPrior event identities:\n" + _json(history, None) + "\n\nResolved state-commit obligations:\n" + _json(resolutions, None)


def recycling_chunk_identity(transaction_id: str, inventory_sha256: str, chunk_index: int) -> tuple[str, str]:
    if (not re.fullmatch(r"TXN_[0-9a-f]{24}", transaction_id)
            or not re.fullmatch(r"[0-9a-f]{64}", inventory_sha256)
            or type(chunk_index) is not int or chunk_index < 0):
        raise ValueError("invalid recycling chunk source identity")
    identity = f"RECYCLING_CHUNK_{transaction_id.upper()}_{inventory_sha256[:16].upper()}_{chunk_index:08d}"
    return identity, "reports/" + identity + ".md"


def render_recycling_chunk(*, transaction_id: str, workspace_id: str,
                            packet: dict[str, Any], receipt_sha256: str) -> str:
    """Immutable subordinate source; only the parent TX can grant clearance."""
    identity, relative = recycling_chunk_identity(transaction_id, packet["inventory_sha256"], packet["chunk_index"])
    payload = {"schema": "werkfaden-tx-recycling-chunk-source/v1",
               "transaction_id": transaction_id, "receipt_sha256": receipt_sha256, "packet": packet}
    scope = packet["scope"]
    metadata = {
        "schema": "kairos-context/v1", "id": identity, "type": "report",
        "revision": 1, "state": "partial", "authority": "execution_evidence",
        "workspace": workspace_id, "route": "/".join((scope["goal"], scope["milestone"], scope["task"], identity)),
        "goal": scope["goal"], "milestone": scope["milestone"], "task": scope["task"], "loop": scope["loop"],
        "updated_at": packet["created_at"],
        "capsule": f"Immutable partial recycling chunk {packet['chunk_index']} for {transaction_id}; no compaction clearance.",
        "claim_boundary": "Recorded per-file declarations and observed evidence hashes only; global acceptance belongs exclusively to the parent transaction review.",
        "entities": [identity, transaction_id], "facets": ["recycling-chunk", "partial-review"],
        "criteria": [], "does_not_answer": ["global review clearance", "semantic correctness", "deployment approval"],
        "recycling_chunk_schema": payload["schema"], "transaction_id": transaction_id,
        "answers": [{"intent": "evidence", "question": f"What partial review does {identity} record?", "target": "s-outcome"},
                    {"intent": "implementation_location", "question": f"Where are the exact decisions for {identity}?", "target": "s-graph-metadata"}],
        "refs": {"parent": pointer(document_path(transaction_id), section="s-graph-metadata",
                                    artifact_id=document_id(transaction_id), version="dynamic",
                                    relation="documents", tags=("recycling", "partial"))},
    }
    fence = chr(96) * 3
    return render_document(metadata, "Partial recycling evidence " + identity, [
        {"id": "s-outcome", "title": "PARTIAL REVIEW",
         "capsule": "This immutable chunk records bounded intermediate work and grants no final review.",
         "content": f"Global inventory: {packet['inventory_sha256']}\nChunk: {packet['chunk_index']}\nHalf-open range: {packet['range']}\nReceipt: {receipt_sha256}\nParent transaction: {transaction_id}\nSource: {relative}\nLifetime: retain as the parent transaction's exact evidence; only a separately verified history/tiering operation may retire this source."},
        {"id": "s-graph-metadata", "title": "EXACT CHUNK EVIDENCE",
         "capsule": "The normalized SQL chunk is derived exclusively from this exact source payload.",
         "content": fence + "json\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":"), allow_nan=False) + "\n" + fence},
    ])


def _recycling_chunk_refs(chunks: list[dict[str, Any]], transaction_id: str,
                          inventory_sha256: str) -> list[dict[str, Any]]:
    result = []
    for chunk in chunks:
        identity, path = recycling_chunk_identity(transaction_id, inventory_sha256, chunk["chunk_index"])
        result.append({key: chunk[key] for key in ("chunk_index", "range", "chunk_sha256", "receipt_sha256", "document_sha256")})
        result[-1].update(artifact_id=identity, path=path, decision_count=len(chunk["decisions"]))
    return result


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
    # Budget the serialized UTF-8 excerpt, including TOML/JSON escaping.
    # The complete declared purpose remains in s-purpose and s-execution.
    purpose_excerpt = purpose[:64]
    if re.search(r"(?i)(?:file://|(?<![A-Za-z0-9_])[A-Z]:[\\/]|\[ref:|\]\(|[`\r\n])", purpose_excerpt):
        purpose_excerpt = "Declared purpose retained in literal evidence"
    while len(json.dumps(purpose_excerpt, ensure_ascii=False).encode("utf-8")) > 66:
        purpose_excerpt = purpose_excerpt[:-1]
    history = transaction.get("historical_scope") or {}
    if history.get("scope_status") == "SCOPE_UNRESOLVED":
        document_state = "blocked"
    scope_bound = bool(goal_id and milestone_id and task_id and type(loop) is int and loop > 0)
    q = chr(96)
    recycle = transaction.get("recycling") if isinstance(transaction.get("recycling"), dict) else {}
    reviewed = recycle.get("status") == "REVIEWED"
    progress = transaction.get("recycling_progress") or {}
    paged = bool(recycle.get("chunks")) if reviewed else bool(progress)
    decisions = recycle.get("decisions", []) if reviewed else []
    if not isinstance(decisions, list):
        decisions = []
    dispositions = {
        name: sum(1 for row in decisions if row.get("disposition") == name)
        for name in ("RETAIN_FOR_REUSE", "RETAIN_EVIDENCE", "REJECT_FOR_REUSE")
    }
    graph_data = "none"
    if paged:
        owner = recycle if reviewed else progress
        graph_data = q * 3 + "json\n" + json.dumps({
            "schema": "werkfaden-tx-recycling-index/v2", "transaction_id": transaction_id,
            "status": "REVIEWED" if reviewed else "PARTIAL",
            "analysis_sha256": owner.get("analysis_sha256") if reviewed else owner.get("progress_sha256"),
            "inventory_sha256": owner["inventory_sha256"], "global_unit_count": owner["global_unit_count"],
            "chunks": _recycling_chunk_refs(owner["chunks"], transaction_id, owner["inventory_sha256"]),
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n" + q * 3
    elif reviewed:
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
        "s-purpose": "- Declared purpose: " + _literal_text(purpose) + "\n- Actual state: " + status
                     + "\n- Recycling intent: " + _literal_text(recycle.get("planned_approach") or "unreviewed"),
        "s-mapping": "- Selected source paths: " + _json(transaction.get("sources", []), 1000)
                     + "\n- Baseline package SHA-256: " + str(transaction.get("baseline_package_sha256") or "none"),
        "s-drift": ("Historical lookup: HISTORICAL_UNRESOLVED/" + transaction_id
                    + "\nHistorical scope transition (full prior bytes remain in the immutable receipt):\n" + _json(history, 3000)) if history else "none",
        "s-ownership": "The Workshop owns recorded transaction state; KAIROS projects its Markdown evidence. A JEV verdict is not a production deployment receipt. " + ("Historical scope is explicitly receipt-bound; prior scope claims remain in the immutable transition receipts."
                     if scope_bound and history else "Historical scope is checkout-bound." if scope_bound
                     else "Historical scope is unresolved; no current Task ownership is asserted."),
        "s-graph-metadata": graph_data,
        "s-symbols": "Selected files (not a verified symbol inventory): "
                     + _json(transaction.get("sources", []), 1000),
        "s-inputs": "- Dependent tests: " + _json(transaction.get("dependent_tests", []), 1000)
                    + "\n- Selected documents: " + _json(transaction.get("documents", []), 1000)
                    + "\n- Framework namespace: " + ("KAIROS_FRAMEWORK (separate projection)" if transaction.get("framework_projection") else "none")
                    + "\n- Framework document/index changes: " + _json((transaction.get("framework_projection") or {}).get("changes", []), 6000),
        "s-preconditions": "Only explicitly recorded preconditions and evidence may be cited. Missing proof is not a pass.",
        "s-structures": "none; consult the actual implementation blueprints",
        "s-constants": "none",
        "s-primitives": "none",
        "s-execution": _literal_text(attempt) + "\n\nAtomic dispositions (overview):\n" + _literal_text(summary)
                       + "\n\nOther declared notes:\n" + _json((transaction.get("transaction_notes") or [])[-30:], 6000)
                       + "\n\nRecent machine events:\n" + _json((transaction.get("journal") or [])[-8:], 4000)
                       + _event_obligations(transaction),
        "s-output": "- Technical TX state: " + status
                    + "\n- Postcheck package: " + str(transaction.get("postcheck_package_sha256") or "none")
                    + "\n- Heartbeat: " + str((transaction.get("heartbeat") or {}).get("heartbeat_id") or "none")
                    + "\n- Recycling decisions: " + json.dumps(dispositions, sort_keys=True)
                    + "\n- Preserved vault manifest: " + str(recycle.get("vault_manifest_sha256") or "none")
                    + "\n- Compaction: " + _json(transaction.get("compaction") or "none", 1400)
                    + "\n- Terminal receipt: " + _json({key: value for key, value in (transaction.get("terminal_receipt") or {}).items() if key != "payload"} or "none", 1400),
        "s-failures": "- Recorded failure: " + _json(transaction.get("failure") or "none", 1400)
                      + "\n- Outcome rationale: " + _literal_text(recycle.get("outcome_reason") or "not yet reviewed")
                      + "\n- Rejected code or approaches:\n" + _literal_text(rejected or "none"),
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
    if paged:
        owner = recycle if reviewed else progress
        covered = sum(len(chunk["decisions"]) for chunk in owner["chunks"])
        content["s-output"] += (f"\n- Subordinate PARTIAL chunks: {len(owner['chunks'])}"
                                f"\n- Recorded file decisions: {covered}/{owner['global_unit_count']}"
                                "\n- A chunk is intermediate evidence; parent clearance is separate.")
        content["s-determinism"] += "\n- Partial frontier SHA-256: " + str(progress.get("progress_sha256") or "none")
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
        "capsule": f"TX {transaction_id}: {purpose_excerpt}; state {status}.",
        "claim_boundary": "Recorded Workshop state and selected evidence only; not JEV authenticity, semantic truth, deployment or criterion closure.",
        "entities": [artifact_id, task_id] if scope_bound else ([artifact_id, "HISTORICAL_UNRESOLVED/" + transaction_id] if history else [artifact_id]),
        "facets": ["workshop", "transaction-history", "candidate-lineage"],
        "criteria": [],
        "does_not_answer": ["production deployment status", "unobserved source correctness"],
        "transaction_id": transaction_id,
        "workshop_state": status,
        "baseline_package_sha256": str(transaction.get("baseline_package_sha256") or ""),
        "verified_work_package_sha256": str(transaction.get("verified_work_package_sha256") or ""),
        "recycling_analysis_sha256": str(recycle.get("analysis_sha256") or ""),
        "recycling_progress_sha256": str(progress.get("progress_sha256") or ""),
        "tx_event_id": str(transaction.get("tx_event_id") or ""),

        "answers": [
            {"intent": "implementation_location", "question": f"{artifact_id}: selected sources?", "target": "s-symbols"},
            {"intent": "failure_semantics", "question": f"{artifact_id}: failures?", "target": "s-failures"},
            {"intent": "determinism", "question": f"{artifact_id}: package hashes?", "target": "s-determinism"},
            {"intent": "boundary", "question": f"{artifact_id}: evidence limits?", "target": "s-boundary"},
            {"intent": "transaction_outcome", "question": f"{artifact_id}: observed outcome?", "target": "s-output"},
            {"intent": "transaction_lineage", "question": f"{artifact_id}: prior patches/events?", "target": "s-execution"},
        ],
        "refs": {
            **({"task": pointer(f"tasks/task_{task_id}.md", section="s-objective", artifact_id=task_id,
                                version="dynamic", relation="belongs_to", tags=("task", "scope"))}
               if task_reference_exists and scope_bound else {}),
            **({"history_method": pointer(HISTORY_METHOD_PATH, section="s-workflow", artifact_id=HISTORY_METHOD_ID,
                                          version="1", relation="requires", tags=("history", "method"))} if history else {}),
        },
        **({"loop": loop, "goal": goal_id, "milestone": milestone_id, "task": task_id}
           if scope_bound else {}),
        "search_contract": [
            {"query": f"{artifact_id}: observed outcome?", "expected": f"{artifact_id}#s-output", "required_top_k": 5},
            {"query": f"{artifact_id}: failures?", "expected": f"{artifact_id}#s-failures", "required_top_k": 5},
        ],
    }
    return render_document(metadata, f"WORKSHOP TRANSACTION {transaction_id}", sections)
