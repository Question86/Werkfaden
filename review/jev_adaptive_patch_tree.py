from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

# Reuse the already-correct Workshop materialization logic.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import jev_workshop_attempt_gate as gate
    from jev_transport import JevTransportError, call_jev as direct_call_jev
except Exception as exc:  # pragma: no cover - local integration guard
    raise SystemExit(
        "jev_adaptive_patch_tree.py must live beside jev_workshop_attempt_gate.py "
        f"or that module must be importable: {exc}"
    )

DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_FOCUS_ROOTS = 3
SUBPROCESS_TIMEOUT_SECONDS = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS

COMMON_JEV_PREFIX = (
    "Considering only the supplied state and current evidence, answer the proposition as a yes-probability. "
    "Do not invent missing project facts. Search rank is not authority. A missing fact is uncertainty, not permission to infer. "
)


class TreeError(RuntimeError):
    pass


@dataclass
class EvidenceItem:
    evidence_id: str
    kind: str
    label: str
    payload: dict[str, Any]
    fingerprint: str
    materiality_score: float | None = None


@dataclass
class QuestionNode:
    node_id: str
    parent_id: str | None
    depth: int
    kind: str
    proposition: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    evidence_score: float | None = None
    initial_score: float | None = None
    initial_evidence_score: float | None = None
    recheck_score: float | None = None
    recheck_evidence_score: float | None = None
    status: str = "PENDING"
    retrieval_query: str | None = None
    retrieval_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


LEVEL0: list[tuple[str, str]] = [
    ("goal_alignment", "Does the candidate directly advance the supplied task/transaction goal?"),
    ("architecture_consistency", "Is the candidate consistent with the supplied current architecture authorities and contracts?"),
    ("owning_mechanism", "Does the candidate modify the abstraction or mechanism that owns the evidenced problem rather than only the visible symptom?"),
    ("causal_repair", "Does the candidate address the evidenced causal failure mechanism?"),
    ("scope_complete", "Does the candidate include every implementation owner that the supplied evidence requires for this repair?"),
    ("scope_bounded", "Is the candidate limited to behavior justified by the supplied task and evidence?"),
    ("upstream_contracts", "Does the candidate preserve all applicable upstream producer and input contracts represented in the supplied evidence?"),
    ("downstream_contracts", "Does the candidate preserve all applicable downstream consumer and output contracts represented in the supplied evidence?"),
    ("state_identity_propagation", "Does the candidate correctly propagate every required state, identity, capability, or binding represented in the supplied evidence?"),
    ("artifact_provenance_binding", "Does the candidate preserve required artifact, hash, provenance, and evidence bindings represented in the supplied evidence?"),
    ("validation_gating", "Does the candidate preserve or correctly implement all applicable validation, admission, and gate semantics represented in the supplied evidence?"),
    ("fail_closed", "Does the candidate preserve required fail-closed behavior when authority, calibration, state, or evidence is missing or invalid?"),
    ("general_solution", "Does the candidate repair the general mechanism rather than only the observed dataset, lane, profile, fixture, test, or customer case?"),
    ("runtime_agnostic", "Is the candidate behavior derived from governed inputs, contracts, and state instead of hard-coded scenario or environment identity?"),
    ("no_fallback", "Does the candidate avoid introducing a fallback, default semantic substitution, bypass, silent downgrade, or reduced-scenario success path?"),
    ("no_residual_path", "Does the candidate remove the same failure mechanism from every supplied reachable path rather than leaving an alternate path that still violates the invariant?"),
    ("scientific_semantic_fidelity", "Does the candidate preserve all applicable scientific semantics, calibration meaning, determinism, provenance, and claim boundaries represented in the supplied evidence?"),
    ("persistence_recovery", "Does the candidate remain coherent with applicable persistence, state-transition, recovery, and replay semantics represented in the supplied evidence?"),
    ("internal_patch_coherence", "Are the concrete candidate edits mutually coherent and non-contradictory?"),
    ("attempt_evidence_sufficient", "Is there enough supplied evidence to justify compiling and testing this exact candidate without filling missing project facts by assumption?"),
]

# These are the undesired-YES propositions in the canonical attempt gate.  A
# focused tomography pass reverses their wording so every tree proposition
# again has YES = desired state.
NEGATIVE_GATE_QUESTIONS = {
    "unsupported_assumption_present",
    "scope_widening_present",
    "scenario_specific_workaround_present",
    "fallback_or_semantic_substitution_present",
    "more_source_or_authority_evidence_required",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def numeric_answer(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("noul", "score", "value"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
    return None


def run_json(cmd: list[str], cwd: Path, *, allow_failure: bool = False) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        if allow_failure:
            return None
        raise TreeError(
            f"command exceeded its {SUBPROCESS_TIMEOUT_SECONDS:.1f}s timeout: {' '.join(cmd)}"
        ) from exc
    if proc.returncode != 0:
        if allow_failure:
            return None
        raise TreeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    text = proc.stdout.strip()
    if not text:
        if allow_failure:
            return None
        raise TreeError(f"command produced no JSON: {' '.join(cmd)}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        if allow_failure:
            return None
        raise TreeError(f"command did not return JSON: {' '.join(cmd)}\n{text}") from exc
    return value if isinstance(value, dict) else {"value": value}


def call_jev(state: dict[str, Any], question_map: dict[str, dict[str, str]]) -> dict[str, Any]:
    try:
        return direct_call_jev(state, question_map, timeout=90)
    except JevTransportError as exc:
        raise TreeError(str(exc)) from exc


def compact_change(change: dict[str, Any]) -> dict[str, Any]:
    # Keep exact BEFORE/AFTER changed-region bytes. unified_diff is redundant here and can be very large.
    return {
        "path": change.get("path"),
        "live_path": change.get("live_path"),
        "candidate_path": change.get("candidate_path"),
        "before_line_count": change.get("before_line_count"),
        "after_line_count": change.get("after_line_count"),
        "regions": change.get("regions", []),
    }


GRAPH_RELEVANCE_RE = re.compile(
    r"pdm|channel|null|claim|metric|calibrat|search|binding|worker|target",
    re.IGNORECASE,
)


def compact_graph_section(section: Any, *, section_name: str, max_rows: int) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {"total": 0, "rows": []}
    rows = section.get("rows", [])
    if not isinstance(rows, list):
        rows = []
    selected: list[dict[str, Any]] = []
    if section_name == "contracts":
        allowed = ("contract_id", "kind", "evidence_target", "failure_or_effect", "statement")
    elif section_name == "incoming":
        allowed = ("subject", "predicate", "object", "object_kind", "evidence_target", "scope")
    else:
        allowed = ("object", "predicate", "object_kind", "evidence_target", "scope")
    for row in rows:
        if not isinstance(row, dict):
            continue
        if GRAPH_RELEVANCE_RE.search(json.dumps(row, ensure_ascii=False, sort_keys=True)):
            selected.append({key: row[key] for key in allowed if key in row})
    if not selected:
        selected = [
            {key: row[key] for key in allowed if key in row}
            for row in rows
            if isinstance(row, dict)
        ][:4]
    return {"total": section.get("total", len(rows)), "rows": selected[:max_rows]}


def compact_graph_receipt(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        return {"query_path": None, "graph": None}
    resolve = receipt.get("resolve") if isinstance(receipt.get("resolve"), dict) else {}
    candidates = resolve.get("candidates", [])
    compact_candidates = []
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                compact_candidates.append(
                    {
                        "identity": candidate.get("identity"),
                        "hits": candidate.get("hits"),
                        "where": candidate.get("where", []),
                    }
                )
    node = receipt.get("node") if isinstance(receipt.get("node"), dict) else {}
    governance = node.get("governance_action") if isinstance(node.get("governance_action"), dict) else {}
    resolve_governance = resolve.get("governance_action")
    if not isinstance(resolve_governance, dict):
        resolve_governance = {}
    return {
        "query_path": receipt.get("query_path"),
        "exact_identity": receipt.get("exact_identity"),
        "resolve": {
            "schema": resolve.get("schema"),
            "exact_identity": resolve.get("exact_identity"),
            "total": resolve.get("total"),
            "returned": resolve.get("returned"),
            "truncated": resolve.get("truncated"),
            "candidates": compact_candidates,
            "governance_status": resolve_governance.get("status"),
        },
        "node": {
            "identity": node.get("node"),
            "governance": {"phase": governance.get("phase"), "status": governance.get("status")},
            "contracts": compact_graph_section(node.get("contracts"), section_name="contracts", max_rows=0),
            "incoming": compact_graph_section(node.get("incoming"), section_name="incoming", max_rows=0),
            "outgoing": compact_graph_section(node.get("outgoing"), section_name="outgoing", max_rows=0),
        },
    }


def compact_base_state(base: dict[str, Any]) -> dict[str, Any]:
    raw_changes = base.get("system_materialized_code_changes", [])
    # Filter out blueprints to keep state size minimal
    filtered_changes = [c for c in raw_changes if not c.get("path", "").startswith("blueprints/")]
    return {
        "review_contract": "WERKFADEN_JEV_ADAPTIVE_PATCH_TOMOGRAPHY_V1",
        "review_mode": "PRE_EXECUTION_PATCH_TOMOGRAPHY",
        "semantics": {
            "important": [
                "This is a pre-execution patch-attempt analysis, not closure proof.",
                "Post-fix run artifacts are not prerequisites for judging whether a patch attempt is justified.",
                "All propositions are phrased so YES means the desired/satisfied state.",
                "A score below policy threshold means EXPAND, not percentage completion.",
                "Do not invent missing project facts.",
            ]
        },
        "transaction": base.get("transaction"),
        "workspace_state": base.get("workspace_state"),
        "system_materialized_code_changes": [
            compact_change(row) for row in filtered_changes
        ],
        "system_materialized_graph_receipts": [
            compact_graph_receipt(row)
            for row in base.get("system_materialized_graph_receipts", [])
        ],
    }


def question_map(nodes: Iterable[QuestionNode]) -> dict[str, dict[str, str]]:
    return {
        node.node_id: {
            "type": "noul",
            "instructions": COMMON_JEV_PREFIX + node.proposition,
        }
        for node in nodes
    }


def ask_nodes(
    nodes: list[QuestionNode],
    state: dict[str, Any],
    *,
    score_cache: dict[tuple[str, str], float],
) -> dict[str, float]:
    if not nodes:
        return {}
    evidence_digest = sha(state)
    uncached: list[QuestionNode] = []
    scores: dict[str, float] = {}
    for node in nodes:
        key = (normalize_question(node.proposition), evidence_digest)
        if key in score_cache:
            scores[node.node_id] = score_cache[key]
        else:
            uncached.append(node)
    if uncached:
        response = call_jev(state, question_map(uncached))
        answers = response.get("answers", {}) if isinstance(response, dict) else {}
        for node in uncached:
            score = numeric_answer(answers.get(node.node_id)) if isinstance(answers, dict) else None
            if score is None:
                raise TreeError(f"Jev returned no numeric Noul for {node.node_id}: {answers.get(node.node_id)!r}")
            score = max(0.0, min(1.0, float(score)))
            scores[node.node_id] = score
            score_cache[(normalize_question(node.proposition), evidence_digest)] = score
    return scores




def ask_evidence_sufficiency(
    nodes: list[QuestionNode],
    state: dict[str, Any],
    *,
    score_cache: dict[tuple[str, str], float],
) -> dict[str, float]:
    proxies: list[QuestionNode] = []
    original_by_proxy: dict[str, str] = {}
    for node in nodes:
        proxy_id = node.node_id + "_E"
        proxy = QuestionNode(
            node_id=proxy_id,
            parent_id=node.parent_id,
            depth=node.depth,
            kind="evidence_sufficiency",
            proposition=(
                "Is the supplied state and evidence sufficient to decide the following proposition without "
                "filling any missing project fact by assumption: " + node.proposition
            ),
        )
        proxies.append(proxy)
        original_by_proxy[proxy_id] = node.node_id
    proxy_scores = ask_nodes(proxies, state, score_cache=score_cache)
    return {original_by_proxy[proxy_id]: score for proxy_id, score in proxy_scores.items()}


def ask_nodes_and_evidence(
    nodes: list[QuestionNode],
    state: dict[str, Any],
    *,
    score_cache: dict[tuple[str, str], float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Score propositions and evidence sufficiency in one Jev request."""

    proxies: list[QuestionNode] = []
    original_by_proxy: dict[str, str] = {}
    for node in nodes:
        proxy_id = node.node_id + "_E"
        proxies.append(
            QuestionNode(
                node_id=proxy_id,
                parent_id=node.parent_id,
                depth=node.depth,
                kind="evidence_sufficiency",
                proposition=(
                    "Is the supplied state and evidence sufficient to decide the following proposition without "
                    "filling any missing project fact by assumption: " + node.proposition
                ),
            )
        )
        original_by_proxy[proxy_id] = node.node_id

    scores = ask_nodes(nodes + proxies, state, score_cache=score_cache)
    evidence_scores = {
        original_by_proxy[proxy_id]: score
        for proxy_id, score in ((proxy_id, scores[proxy_id]) for proxy_id in original_by_proxy)
    }
    return (
        {node.node_id: scores[node.node_id] for node in nodes},
        evidence_scores,
    )

def level0_nodes() -> list[QuestionNode]:
    nodes: list[QuestionNode] = []
    for i, (name, proposition) in enumerate(LEVEL0, start=1):
        nodes.append(
            QuestionNode(
                node_id=f"N{i:06d}",
                parent_id=None,
                depth=0,
                kind="root",
                proposition=proposition,
                metadata={"level0_name": name},
            )
        )
    return nodes


def review_scores(review: dict[str, Any]) -> dict[str, float]:
    raw_scores = review.get("scores")
    if not isinstance(raw_scores, dict):
        response = review.get("jev_response")
        answers = response.get("answers") if isinstance(response, dict) else None
        raw_scores = answers if isinstance(answers, dict) else {}
    scores: dict[str, float] = {}
    for key, value in raw_scores.items():
        score = numeric_answer(value)
        if score is not None:
            scores[str(key)] = max(0.0, min(1.0, float(score)))
    return scores


def focused_nodes(
    review: dict[str, Any],
    threshold: float,
    max_roots: int,
) -> list[QuestionNode]:
    """Turn the failed attempt-gate dimensions into KAIROS-driven roots.

    The old tree always started from its generic 20-root matrix.  That is a
    useful cold-start diagnostic, but it discards the information already
    returned by the attempt gate.  On a low attempt we rank failed gate
    questions by severity and retain only the bounded highest-value roots, so
    each retrieval query is explicitly aimed at an observed weakness without
    creating an unbounded serial database walk.
    """
    scores = review_scores(review)
    definitions = gate.questions()
    candidates: list[tuple[float, int, QuestionNode]] = []
    for index, (question_id, definition) in enumerate(definitions.items(), start=1):
        score = scores.get(question_id)
        negative = question_id in NEGATIVE_GATE_QUESTIONS
        failed = score is None or (
            score > 1.0 - threshold if negative else score < threshold
        )
        if not failed:
            continue
        instruction = str(definition.get("instructions", "")).strip()
        if negative:
            proposition = (
                f"Does the candidate avoid the failure condition assessed by Jev gate question "
                f"'{question_id}'? The original Jev criterion was: {instruction}"
            )
            polarity = "undesired_yes_reversed_to_desired_avoidance"
        else:
            proposition = (
                f"Does the candidate satisfy the requirement assessed by Jev gate question "
                f"'{question_id}'? The original Jev criterion was: {instruction}"
            )
            polarity = "desired_yes"
        node = QuestionNode(
            node_id="",
            parent_id=None,
            depth=0,
            kind="jev_feedback",
            proposition=proposition,
            metadata={
                "focus_source": "jev_attempt_gate",
                "jev_question_id": question_id,
                "jev_score": score,
                "jev_threshold": threshold,
                "jev_gate_polarity": polarity,
                "jev_instruction": instruction,
            },
        )
        if score is None:
            severity = 1.0
        elif negative:
            severity = max(0.0, float(score) - (1.0 - threshold))
        else:
            severity = max(0.0, threshold - float(score))
        candidates.append((severity, index, node))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[:max_roots]
    roots: list[QuestionNode] = []
    for rank, (severity, _definition_index, node) in enumerate(selected, start=1):
        node.node_id = f"N{rank:06d}"
        node.metadata["focus_rank"] = rank
        node.metadata["focus_severity"] = round(severity, 6)
        node.metadata["focus_candidates_total"] = len(candidates)
        node.metadata["focus_candidates_omitted"] = max(0, len(candidates) - len(selected))
        roots.append(node)
    return roots


def make_retrieval_query(node: QuestionNode) -> str:
    if node.kind == "jev_feedback":
        md = node.metadata or {}
        question_id = str(md.get("jev_question_id", "")).strip()
        score = md.get("jev_score")
        return (
            "Jev returned a low score for the concrete Workshop candidate on the following gate dimension. "
            "Which current project contracts, architecture relations, owners, artifacts, exact source authorities, "
            "and retrieval evidence determine the correct next candidate revision? Preserve contradictions, missing "
            "authority, and graph-only routing signals explicitly.\n\n"
            f"Jev question: {question_id}\n"
            f"Jev score: {score}\n"
            + node.proposition
        )
    return (
        "Which current project contracts, architecture relations, owners, artifacts, evidence anchors, "
        "or implementation authorities determine whether the following candidate-patch proposition is true "
        "for the active task? Preserve contradictions and missing authority explicitly.\n\n"
        + node.proposition
    )


def kairos_search(config: dict[str, Any], query: str, limit: int) -> dict[str, Any] | None:
    harness_raw = config.get("kairos_harness")
    workspace_raw = config.get("kairos_workspace")
    if not harness_raw or not workspace_raw:
        return None
    harness = Path(harness_raw)
    workspace = Path(workspace_raw)
    if not harness.is_dir() or not workspace.is_dir():
        return None
    return run_json(
        [
            sys.executable,
            "-m",
            "kairos",
            "search",
            "--workspace",
            str(workspace),
            "--mode",
            "breadth",
            "--limit",
            str(limit),
            "--max-hops",
            "2",
            query,
        ],
        harness,
        allow_failure=True,
    )


def graph_node(config: dict[str, Any], identity: str) -> dict[str, Any] | None:
    harness_raw = config.get("kairos_harness")
    workspace_raw = config.get("kairos_workspace")
    if not harness_raw or not workspace_raw:
        return None
    harness = Path(harness_raw)
    workspace = Path(workspace_raw)
    return run_json(
        [sys.executable, "-m", "kairos", "graph", "--workspace", str(workspace), "--node", identity],
        harness,
        allow_failure=True,
    )


def graph_asset(config: dict[str, Any], identity: str) -> dict[str, Any] | None:
    harness_raw = config.get("kairos_harness")
    workspace_raw = config.get("kairos_workspace")
    if not harness_raw or not workspace_raw:
        return None
    harness = Path(harness_raw)
    workspace = Path(workspace_raw)
    return run_json(
        [sys.executable, "-m", "kairos", "graph", "--workspace", str(workspace), "--asset", identity],
        harness,
        allow_failure=True,
    )


def graph_resolve(config: dict[str, Any], partial: str) -> dict[str, Any] | None:
    harness_raw = config.get("kairos_harness")
    workspace_raw = config.get("kairos_workspace")
    if not harness_raw or not workspace_raw:
        return None
    harness = Path(harness_raw)
    workspace = Path(workspace_raw)
    return run_json(
        [sys.executable, "-m", "kairos", "graph", "--workspace", str(workspace), "--resolve", partial],
        harness,
        allow_failure=True,
    )


def supplemental_graph(config: dict[str, Any], node: QuestionNode) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    md = node.metadata or {}
    if node.kind == "relation":
        for field_name in ("subject", "object"):
            value = str(md.get(field_name, "")).strip()
            if value:
                data = graph_node(config, value)
                if data:
                    out.append({"surface": "graph_node", "identity": value, "result": data})
    elif node.kind == "contract":
        value = str(md.get("subject", "")).strip()
        if value:
            data = graph_node(config, value)
            if data:
                out.append({"surface": "graph_node", "identity": value, "result": data})
    elif node.kind == "artifact":
        value = str(md.get("artifact_id", "")).strip()
        if value:
            data = graph_asset(config, value)
            if data:
                out.append({"surface": "graph_asset", "identity": value, "result": data})
    elif node.kind in {"file", "hunk"}:
        path = str(md.get("path", "")).strip()
        if path:
            resolved = graph_resolve(config, path)
            entry: dict[str, Any] = {"surface": "graph_resolve", "identity": path, "result": resolved}
            candidates = resolved.get("candidates", []) if isinstance(resolved, dict) else []
            if isinstance(candidates, list) and len(candidates) == 1 and isinstance(candidates[0], dict):
                exact = str(candidates[0].get("identity", "")).strip()
                if exact:
                    entry["exact_identity"] = exact
                    entry["node"] = graph_node(config, exact)
            out.append(entry)
    return out


def retrieve_for_node(config: dict[str, Any], node: QuestionNode, limit: int) -> dict[str, Any]:
    query = make_retrieval_query(node)
    return {
        "query": query,
        "search": kairos_search(config, query, limit),
        "supplemental_graph": supplemental_graph(config, node),
    }


def clean_text_clause(text: str) -> str:
    text = re.sub(r"^\s*[-*+>]\s*", "", text.strip())
    text = re.sub(r"^\s*\d+[.)]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_clauses(text: str, max_clauses: int = 6) -> list[str]:
    if not text:
        return []
    raw_lines: list[str] = []
    for line in text.splitlines():
        line = clean_text_clause(line)
        if not line or line.startswith("#") or line.startswith("```") or line in {"{", "}", "[", "]"}:
            continue
        if 25 <= len(line) <= 700:
            raw_lines.append(line)
    # Prefer lines that express a rule/contract/ownership statement.
    normative = re.compile(
        r"\b(must|shall|required|requires|never|only|cannot|may not|do not|owns|produces|consumes|"
        r"gates|binds|validates|fail[- ]closed|preserve|authority|invariant|contract)\b",
        re.IGNORECASE,
    )
    preferred = [line for line in raw_lines if normative.search(line)]
    chosen = preferred if preferred else raw_lines
    out: list[str] = []
    seen: set[str] = set()
    for line in chosen:
        # A long prose line can contain several explicit sentences. Split conservatively.
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`])", line)
        for part in parts:
            part = clean_text_clause(part)
            key = normalize_question(part)
            if 25 <= len(part) <= 700 and key not in seen:
                seen.add(key)
                out.append(part)
                if len(out) >= max_clauses:
                    return out
    return out


def evidence_item(kind: str, label: str, payload: dict[str, Any]) -> EvidenceItem:
    fp = sha({"kind": kind, "payload": payload})
    return EvidenceItem(
        evidence_id="E_" + fp[:12],
        kind=kind,
        label=label,
        payload=payload,
        fingerprint=fp,
    )


def extract_evidence_items(payload: Any, *, max_items: int) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()

    def add(item: EvidenceItem) -> None:
        if item.fingerprint in seen or len(items) >= max_items:
            return
        seen.add(item.fingerprint)
        items.append(item)

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if len(items) >= max_items:
            return
        if isinstance(value, dict):
            keys = set(value)
            # Normalized KAIROS graph relation.
            if {"subject", "predicate", "object"}.issubset(keys):
                p = {
                    "subject": value.get("subject"),
                    "predicate": value.get("predicate"),
                    "object": value.get("object"),
                    "object_kind": value.get("object_kind"),
                    "scope": value.get("scope"),
                    "evidence_target": value.get("evidence_target"),
                    "evidence": value.get("evidence"),
                }
                add(evidence_item("relation", f"relation {p['subject']} --{p['predicate']}--> {p['object']}", p))

            # Normalized KAIROS contract.
            if {"kind", "subject", "statement"}.issubset(keys):
                p = {
                    "id": value.get("id"),
                    "kind": value.get("kind"),
                    "subject": value.get("subject"),
                    "statement": value.get("statement"),
                    "failure_or_effect": value.get("failure_or_effect"),
                    "evidence_target": value.get("evidence_target"),
                }
                add(evidence_item("contract", f"contract {p['kind']} on {p['subject']}", p))

            # Normalized KAIROS artifact row.
            if {"id", "role", "operation", "producer_or_consumer"}.issubset(keys):
                p = {
                    "id": value.get("id"),
                    "role": value.get("role"),
                    "operation": value.get("operation"),
                    "producer_or_consumer": value.get("producer_or_consumer"),
                    "schema_or_type": value.get("schema_or_type"),
                    "hash_bound": value.get("hash_bound"),
                    "commit_bound": value.get("commit_bound"),
                    "evidence_target": value.get("evidence_target"),
                }
                add(evidence_item("artifact", f"artifact {p['id']} ({p['operation']})", p))

            # Normalized drift record.
            if {"classification", "summary", "status", "subject"}.issubset(keys):
                p = {
                    "id": value.get("id"),
                    "subject": value.get("subject"),
                    "classification": value.get("classification"),
                    "summary": value.get("summary"),
                    "status": value.get("status"),
                    "evidence_target": value.get("evidence_target"),
                }
                add(evidence_item("drift", f"drift {p['classification']} on {p['subject']}", p))

            # Bounded search-section/snippet fallback. We only turn literal returned prose into clauses;
            # the script never writes a new architectural claim itself.
            text = None
            for text_key in ("snippet", "body", "content", "text", "capsule"):
                if isinstance(value.get(text_key), str) and len(value[text_key].strip()) >= 25:
                    text = value[text_key]
                    break
            if text:
                source_bits = []
                for source_key in ("path", "document_path", "artifact_id", "id", "section_id", "target", "anchor"):
                    if isinstance(value.get(source_key), str) and value[source_key].strip():
                        source_bits.append(value[source_key].strip())
                source = "#".join(source_bits[:2]) or "/".join(path[-3:]) or "retrieved-section"
                for clause in split_clauses(text):
                    add(
                        evidence_item(
                            "clause",
                            f"clause from {source}",
                            {"source": source, "clause": clause},
                        )
                    )

            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    walk(child, path + (str(key),))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                if len(items) >= max_items:
                    break
                walk(child, path + (str(i),))

    walk(payload)
    return items


def materiality_scores(
    parent: QuestionNode,
    items: list[EvidenceItem],
    *,
    batch_size: int,
    score_cache: dict[tuple[str, str], float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        state = {
            "review_contract": "WERKFADEN_JEV_EVIDENCE_MATERIALITY_V1",
            "parent_proposition": parent.proposition,
            "evidence_catalog": [
                {"evidence_id": item.evidence_id, "kind": item.kind, "label": item.label, "payload": item.payload}
                for item in batch
            ],
        }
        nodes = [
            QuestionNode(
                node_id=f"M_{item.evidence_id}",
                parent_id=parent.node_id,
                depth=parent.depth,
                kind="materiality",
                proposition=(
                    f"Is evidence item {item.evidence_id} materially relevant to deciding the parent proposition: "
                    f"{parent.proposition}"
                ),
            )
            for item in batch
        ]
        scores = ask_nodes(nodes, state, score_cache=score_cache)
        for item, node in zip(batch, nodes):
            score = scores[node.node_id]
            item.materiality_score = score
            result[item.evidence_id] = score
    return result


def child_from_evidence(parent: QuestionNode, item: EvidenceItem, node_id: str) -> QuestionNode | None:
    p = item.payload
    if item.kind == "contract":
        subject = str(p.get("subject", "")).strip()
        statement = str(p.get("statement", "")).strip()
        if not statement:
            return None
        obligation = f"contract for {subject}: {statement}" if subject else statement
        proposition = f"Does the candidate satisfy this current project {obligation}?"
        metadata = {
            "evidence_id": item.evidence_id,
            "obligation": obligation,
            "subject": subject,
            "contract_kind": p.get("kind"),
            "evidence_target": p.get("evidence_target"),
        }
        kind = "contract"
    elif item.kind == "relation":
        subject = str(p.get("subject", "")).strip()
        pred = str(p.get("predicate", "")).strip()
        obj = str(p.get("object", "")).strip()
        if not subject or not pred or not obj:
            return None
        obligation = f"required relation {subject} --{pred}--> {obj}"
        proposition = f"Is the candidate consistent with the current {obligation}?"
        metadata = {
            "evidence_id": item.evidence_id,
            "obligation": obligation,
            "subject": subject,
            "predicate": pred,
            "object": obj,
            "evidence_target": p.get("evidence_target"),
        }
        kind = "relation"
    elif item.kind == "artifact":
        aid = str(p.get("id", "")).strip()
        op = str(p.get("operation", "")).strip()
        owner = str(p.get("producer_or_consumer", "")).strip()
        if not aid or not op or not owner:
            return None
        obligation = f"{owner}'s required {op} relationship with artifact {aid}"
        proposition = f"Does the candidate preserve or correctly implement {obligation}?"
        metadata = {
            "evidence_id": item.evidence_id,
            "obligation": obligation,
            "artifact_id": aid,
            "operation": op,
            "producer_or_consumer": owner,
            "evidence_target": p.get("evidence_target"),
        }
        kind = "artifact"
    elif item.kind == "drift":
        summary = str(p.get("summary", "")).strip()
        if not summary:
            return None
        obligation = f"avoid preserving or reintroducing unresolved drift: {summary}"
        proposition = f"Does the candidate {obligation}?"
        metadata = {
            "evidence_id": item.evidence_id,
            "obligation": obligation,
            "subject": p.get("subject"),
            "classification": p.get("classification"),
            "drift_status": p.get("status"),
            "evidence_target": p.get("evidence_target"),
        }
        kind = "drift"
    elif item.kind == "clause":
        clause = str(p.get("clause", "")).strip()
        if not clause:
            return None
        obligation = f"evidence clause: {clause}"
        proposition = f"Does the candidate satisfy the requirement or constraint expressed by this exact {obligation}?"
        metadata = {
            "evidence_id": item.evidence_id,
            "obligation": obligation,
            "source": p.get("source"),
        }
        kind = "clause"
    else:
        return None
    return QuestionNode(
        node_id=node_id,
        parent_id=parent.node_id,
        depth=parent.depth + 1,
        kind=kind,
        proposition=proposition,
        metadata=metadata,
    )


def relevant_changed_files(parent: QuestionNode, retrieval: dict[str, Any], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blob = canonical_json(retrieval).lower()
    matched: list[dict[str, Any]] = []
    for change in changes:
        path = str(change.get("path", ""))
        if not path:
            continue
        basename = Path(path).name.lower()
        if path.lower() in blob or (basename and basename in blob):
            matched.append(change)
    if not matched and parent.depth == 0:
        matched = changes[:]
    return matched


def file_children(parent: QuestionNode, retrieval: dict[str, Any], changes: list[dict[str, Any]], next_id) -> list[QuestionNode]:
    out: list[QuestionNode] = []
    for change in relevant_changed_files(parent, retrieval, changes):
        path = str(change.get("path", ""))
        out.append(
            QuestionNode(
                node_id=next_id(),
                parent_id=parent.node_id,
                depth=parent.depth + 1,
                kind="file",
                proposition=(
                    f"Is the actual candidate change in {path} correct and necessary for satisfying the parent proposition: "
                    f"{parent.proposition}"
                ),
                metadata={"path": path, "obligation": parent.proposition},
            )
        )
    return out


def hunk_children(parent: QuestionNode, changes: list[dict[str, Any]], next_id) -> list[QuestionNode]:
    if parent.kind != "file":
        return []
    path = str(parent.metadata.get("path", ""))
    target = next((row for row in changes if str(row.get("path", "")) == path), None)
    if not target:
        return []
    out: list[QuestionNode] = []
    for idx, region in enumerate(target.get("regions", []), start=1):
        after_range = region.get("after_range")
        out.append(
            QuestionNode(
                node_id=next_id(),
                parent_id=parent.node_id,
                depth=parent.depth + 1,
                kind="hunk",
                proposition=(
                    f"Does the concrete candidate change in {path}, candidate region {after_range}, correctly satisfy "
                    f"the parent proposition without violating the supplied project evidence: {parent.proposition}"
                ),
                metadata={
                    "path": path,
                    "region_index": idx,
                    "after_range": after_range,
                    "before_range": region.get("before_range"),
                    "obligation": parent.proposition,
                },
            )
        )
    return out


def diagnostic_children(parent: QuestionNode, next_id) -> list[QuestionNode]:
    if parent.kind not in {"contract", "relation", "artifact", "drift", "clause"}:
        return []
    obligation = str(parent.metadata.get("obligation", "")).strip()
    if not obligation:
        return []
    return [
        QuestionNode(
            node_id=next_id(),
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            kind="diagnostic",
            proposition=f"Does the sealed BEFORE implementation satisfy the following obligation: {obligation}?",
            metadata={"diagnostic": "before", "obligation": obligation},
        ),
        QuestionNode(
            node_id=next_id(),
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            kind="diagnostic",
            proposition=f"Does the CANDIDATE AFTER implementation satisfy the following obligation: {obligation}?",
            metadata={"diagnostic": "after", "obligation": obligation},
        ),
        QuestionNode(
            node_id=next_id(),
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            kind="diagnostic",
            proposition=f"Does the concrete candidate change directly repair the BEFORE violation of the following obligation: {obligation}?",
            metadata={"diagnostic": "delta", "obligation": obligation},
        ),
        QuestionNode(
            node_id=next_id(),
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            kind="diagnostic",
            proposition=f"Does the CANDIDATE eliminate the supplied reachable violation of the following obligation without leaving a residual path: {obligation}?",
            metadata={"diagnostic": "complete", "obligation": obligation},
        ),
    ]


def build_branch_state(
    base_state: dict[str, Any],
    node: QuestionNode,
    relevant_items: list[EvidenceItem],
    retrieval_digest: str,
) -> dict[str, Any]:
    return {
        "review_contract": "WERKFADEN_JEV_ADAPTIVE_PATCH_BRANCH_V1",
        "base_patch_state": base_state,
        "branch": {
            "node_id": node.node_id,
            "depth": node.depth,
            "kind": node.kind,
            "proposition": node.proposition,
            "retrieval_digest": retrieval_digest,
        },
        "retrieved_relevant_evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "label": item.label,
                "materiality_score": item.materiality_score,
                "payload": item.payload,
            }
            for item in relevant_items
        ],
    }


def build_child_state(
    base_state: dict[str, Any],
    parent: QuestionNode,
    relevant_items: list[EvidenceItem],
) -> dict[str, Any]:
    return {
        "review_contract": "WERKFADEN_JEV_ADAPTIVE_PATCH_CHILDREN_V1",
        "base_patch_state": base_state,
        "parent_branch": {
            "node_id": parent.node_id,
            "kind": parent.kind,
            "proposition": parent.proposition,
        },
        "evidence_catalog": [
            {"evidence_id": item.evidence_id, "kind": item.kind, "payload": item.payload}
            for item in relevant_items
        ],
    }


def dedupe_children(children: list[QuestionNode], global_questions: set[str]) -> list[QuestionNode]:
    out: list[QuestionNode] = []
    local: set[str] = set()
    for child in children:
        key = normalize_question(child.proposition)
        if key in local:
            continue
        # Global duplicates are still useful under another parent only if they carry a different exact file/hunk.
        meta_key = sha(child.metadata)[:12]
        global_key = key + "|" + meta_key
        if global_key in global_questions:
            continue
        local.add(key)
        global_questions.add(global_key)
        out.append(child)
    return out


def markdown_report(result: dict[str, Any]) -> str:
    nodes = result["nodes"]
    threshold = result["policy"]["pass_threshold"]
    unresolved = [
        n for n in nodes.values()
        if (
            n.get("score") is not None
            and n.get("evidence_score") is not None
            and (float(n["score"]) < threshold or float(n["evidence_score"]) < threshold)
            and not n.get("children")
        )
    ]
    unresolved.sort(key=lambda n: (float(n.get("score", 1.0)), -int(n.get("depth", 0))))
    lines = [
        "# Jev Adaptive Patch Tomography Report",
        "",
        f"- Transaction: `{result['transaction_id']}`",
        f"- Pass threshold: `{threshold:.2f}`",
        f"- Nodes: `{result['summary']['node_count']}`",
        f"- Passed: `{result['summary']['pass_count']}`",
        f"- Open leaves: `{len(unresolved)}`",
        f"- Max depth reached: `{result['summary']['max_depth_reached']}`",
        "",
        "> Scores are yes-probabilities for individual propositions. They are not percentages of implementation completion.",
        "",
        "## Open diagnostic leaves",
        "",
    ]
    if not unresolved:
        lines.append("No unresolved leaf remained under the configured limits. This is not post-run closure proof.")
    for node in unresolved:
        lines.extend([
            f"### {node['node_id']} — {node['kind']}",
            "",
            f"- Proposition score: `{float(node['score']):.3f}`",
            f"- Evidence-sufficiency score: `{float(node['evidence_score']):.3f}`",
            f"- Status: `{node['status']}`",
            f"- Parent: `{node.get('parent_id') or 'ROOT'}`",
            f"- Proposition: {node['proposition']}",
        ])
        md = node.get("metadata") or {}
        if md.get("path"):
            lines.append(f"- Path: `{md['path']}`")
        if md.get("after_range"):
            lines.append(f"- Candidate region: `{md['after_range']}`")
        if node.get("evidence_ids"):
            lines.append("- Evidence: " + ", ".join(f"`{e}`" for e in node["evidence_ids"]))
        if node.get("retrieval_query"):
            lines.append(f"- Retrieval query: {node['retrieval_query'].replace(chr(10), ' ')}")
        lines.append("")
    lines.extend([
        "## Coding-LLM interpretation rule",
        "",
        "Use the lowest-scoring, deepest leaves as the highest-resolution unresolved assumptions.",
        "Do not translate a score into percentage completion. Re-query KAIROS or revise the Workshop candidate, then run a fresh tree.",
        "Passing Jev branches do not replace Workshop verification, tests, scientific evidence, fresh-run evidence, or Human authority.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively decompose a Workshop candidate patch into evidence-backed Jev yes/no questions. "
            "Failed/open propositions query KAIROS and expand into atomic children."
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
        help="authoritative workshop.config.json",
    )
    parser.add_argument("--pass-threshold", type=float, default=0.90)
    parser.add_argument("--materiality-threshold", type=float, default=0.80)
    parser.add_argument("--search-limit", type=int, default=30)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--max-nodes", type=int, default=1000)
    parser.add_argument("--max-evidence-candidates", type=int, default=60)
    parser.add_argument("--max-children-per-node", type=int, default=30)
    parser.add_argument("--max-focus-roots", type=int, default=DEFAULT_MAX_FOCUS_ROOTS)
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--subprocess-timeout", type=float, default=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS)
    parser.add_argument(
        "--context-lines",
        type=int,
        default=0,
        help="context around changed regions; exact changed lines are the bounded default",
    )
    parser.add_argument(
        "--focus-review",
        default=None,
        help=(
            "prior Jev attempt-review JSON; failed gate dimensions become targeted KAIROS retrieval roots "
            "instead of using only the generic tomography matrix"
        ),
    )
    parser.add_argument("--out-json", default="jev_patch_tree.json")
    parser.add_argument("--out-md", default="jev_patch_tree.md")
    parser.add_argument("--include-retrieval-payloads", action="store_true")
    args = parser.parse_args()

    if not (0.0 < args.pass_threshold <= 1.0):
        raise SystemExit("--pass-threshold must be in (0,1]")
    if not (0.0 <= args.materiality_threshold <= 1.0):
        raise SystemExit("--materiality-threshold must be in [0,1]")
    if args.subprocess_timeout <= 0.0:
        raise SystemExit("--subprocess-timeout must be positive")
    if args.max_focus_roots <= 0:
        raise SystemExit("--max-focus-roots must be positive")

    global SUBPROCESS_TIMEOUT_SECONDS
    SUBPROCESS_TIMEOUT_SECONDS = args.subprocess_timeout

    try:
        workshop_root = Path(args.workshop_root).resolve()
        if not (workshop_root / "workshop.py").is_file():
            raise TreeError(f"workshop.py not found under {workshop_root}")
        if not args.config:
            raise TreeError("--config or KAIROS_WORKSHOP_CONFIG is required; no path is guessed")
        config_path = Path(args.config).resolve()
        config = gate.load_workshop_config(config_path)
        tx_root = gate.locate_transaction_root(workshop_root, args.transaction_id)
        tx_state = gate.load_transaction_state(workshop_root, args.transaction_id, config_path)
        allowed_states = {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}
        state_name = str(tx_state.get("state", ""))
        if state_name not in allowed_states:
            raise TreeError(f"transaction state {state_name!r} is not a pre-apply candidate state")

        focus_review: dict[str, Any] | None = None
        focus_review_path: Path | None = None
        if args.focus_review:
            focus_review_path = Path(args.focus_review).resolve()
            try:
                focus_review = gate.read_json(focus_review_path)
            except Exception as exc:
                raise TreeError(f"cannot read --focus-review {focus_review_path}: {exc}") from exc
            review_transaction_id = str(focus_review.get("transaction_id", "")).strip()
            if review_transaction_id != args.transaction_id:
                raise TreeError(
                    "focus review transaction binding does not match the requested transaction: "
                    f"{review_transaction_id!r} != {args.transaction_id!r}"
                )

        changes = gate.materialize_changes(tx_root, config, max(0, args.context_lines))
        # Filter out blueprint changes (too large for API payload)
        changes = [c for c in changes if not c["path"].startswith("blueprints/")]
        graph = gate.kairos_graph_receipts(config, changes)
        current = gate.current_workspace_state(config)
        original_base = gate.build_state(args.transaction_id, tx_state, current, changes, graph)
        base_state = compact_base_state(original_base)
        compact_changes = base_state["system_materialized_code_changes"]

        score_cache: dict[tuple[str, str], float] = {}
        nodes: dict[str, QuestionNode] = {}
        retrieval_records: dict[str, dict[str, Any]] = {}
        evidence_registry: dict[str, EvidenceItem] = {}
        global_questions: set[str] = set()
        next_num = 1

        def next_id() -> str:
            nonlocal next_num
            while True:
                nid = f"N{next_num:06d}"
                next_num += 1
                if nid not in nodes:
                    return nid

        roots = (
            focused_nodes(focus_review, args.pass_threshold, args.max_focus_roots)
            if focus_review is not None
            else level0_nodes()
        )
        if not roots:
            raise TreeError("--focus-review contains no failed attempt-gate dimensions to investigate")
        # Reserve root IDs and start subsequent generated IDs after them.
        next_num = len(roots) + 1
        for node in roots:
            nodes[node.node_id] = node
            global_questions.add(normalize_question(node.proposition) + "|" + sha(node.metadata)[:12])

        focus_retrieval_only = focus_review is not None
        if focus_retrieval_only:
            # The attempt receipt already contains the Jev score. Repeating
            # that same proposition here only burns another API round and does
            # not add evidence. Force every failed dimension through KAIROS.
            for node in roots:
                raw_score = float(node.metadata.get("jev_score", 0.0))
                desired_score = (
                    1.0 - raw_score
                    if str(node.metadata.get("jev_gate_polarity", "")).startswith("undesired_yes")
                    else raw_score
                )
                node.initial_score = desired_score
                node.initial_evidence_score = 0.0
                node.score = desired_score
                node.evidence_score = 0.0
                node.status = "EXPAND"
        else:
            for start in range(0, len(roots), args.batch_size):
                batch = roots[start : start + args.batch_size]
                scores, evidence_scores = ask_nodes_and_evidence(batch, base_state, score_cache=score_cache)
                for node in batch:
                    node.initial_score = scores[node.node_id]
                    node.initial_evidence_score = evidence_scores[node.node_id]
                    node.score = scores[node.node_id]
                    node.evidence_score = evidence_scores[node.node_id]
                    node.status = (
                        "PASS"
                        if node.score >= args.pass_threshold and node.evidence_score >= args.pass_threshold
                        else "EXPAND"
                    )

        queue: list[str] = [n.node_id for n in roots if n.status == "EXPAND"]
        query_count = 0
        max_depth_reached = 0

        while queue and len(nodes) < args.max_nodes:
            # Lowest score first = highest-value diagnostic branch first.
            queue.sort(
                key=lambda nid: (
                    min(
                        nodes[nid].score if nodes[nid].score is not None else 1.0,
                        nodes[nid].evidence_score if nodes[nid].evidence_score is not None else 1.0,
                    ),
                    -nodes[nid].depth,
                )
            )
            node_id = queue.pop(0)
            node = nodes[node_id]
            max_depth_reached = max(max_depth_reached, node.depth)

            if (
                node.score is not None
                and node.evidence_score is not None
                and node.score >= args.pass_threshold
                and node.evidence_score >= args.pass_threshold
            ):
                node.status = "PASS"
                continue
            if node.depth >= args.max_depth:
                node.status = "LIMIT_REACHED"
                continue
            if len(nodes) >= args.max_nodes:
                node.status = "LIMIT_REACHED"
                break

            retrieval = retrieve_for_node(config, node, args.search_limit)
            query_count += 1
            retrieval_digest = sha(retrieval)
            retrieval_id = "R_" + retrieval_digest[:12]
            node.retrieval_query = retrieval["query"]
            node.retrieval_id = retrieval_id

            if args.include_retrieval_payloads:
                retrieval_records[retrieval_id] = retrieval
            else:
                retrieval_records[retrieval_id] = {
                    "query": retrieval["query"],
                    "digest": retrieval_digest,
                    "search_present": retrieval.get("search") is not None,
                    "supplemental_graph_count": len(retrieval.get("supplemental_graph", [])),
                }

            items = extract_evidence_items(retrieval, max_items=args.max_evidence_candidates)
            for item in items:
                evidence_registry[item.evidence_id] = item

            relevant: list[EvidenceItem] = []
            if items:
                if focus_retrieval_only:
                    # Search ranking remains routing evidence, not truth. The
                    # bounded handoff preserves the first atomic candidates;
                    # Codex must inspect them before changing the work tree.
                    relevant = items[: args.max_children_per_node]
                else:
                    mat = materiality_scores(
                        node,
                        items,
                        batch_size=args.batch_size,
                        score_cache=score_cache,
                    )
                    relevant = [item for item in items if mat.get(item.evidence_id, 0.0) >= args.materiality_threshold]
                    # Keep deterministic ordering from retrieval, then cap.
                    relevant = relevant[: args.max_children_per_node]
                node.evidence_ids = [item.evidence_id for item in relevant]

            # Re-ask the same proposition only after material new evidence.
            if relevant and not focus_retrieval_only:
                recheck_state = build_branch_state(base_state, node, relevant, retrieval_digest)
                recheck_scores, recheck_evidence_scores = ask_nodes_and_evidence(
                    [node], recheck_state, score_cache=score_cache
                )
                recheck = recheck_scores[node.node_id]
                recheck_evidence = recheck_evidence_scores[node.node_id]
                node.recheck_score = recheck
                node.recheck_evidence_score = recheck_evidence
                node.score = recheck
                node.evidence_score = recheck_evidence
                if recheck >= args.pass_threshold and recheck_evidence >= args.pass_threshold:
                    node.status = "PASS_AFTER_RETRIEVAL"
                    continue

            if focus_retrieval_only:
                node.status = "KAIROS_EVIDENCE_COLLECTED" if items else "KAIROS_RETRIEVAL_EMPTY"
                continue

            children: list[QuestionNode] = []
            for item in relevant:
                child = child_from_evidence(node, item, next_id())
                if child:
                    children.append(child)

            # Localize a failed/open branch against actual changed files.
            if node.kind == "file":
                children.extend(hunk_children(node, compact_changes, next_id))
            else:
                children.extend(file_children(node, retrieval, compact_changes, next_id))

            # For already-atomic evidence obligations, add BEFORE/DELTA/COMPLETENESS diagnostics.
            children.extend(diagnostic_children(node, next_id))
            children = dedupe_children(children, global_questions)
            children = children[: args.max_children_per_node]

            if not children:
                node.status = "STALLED_NO_NEW_ATOMIC_EVIDENCE"
                continue

            child_state = build_child_state(base_state, node, relevant)
            for start in range(0, len(children), args.batch_size):
                batch = children[start : start + args.batch_size]
                scores, evidence_scores = ask_nodes_and_evidence(
                    batch, child_state, score_cache=score_cache
                )
                for child in batch:
                    child.initial_score = scores[child.node_id]
                    child.initial_evidence_score = evidence_scores[child.node_id]
                    child.score = scores[child.node_id]
                    child.evidence_score = evidence_scores[child.node_id]
                    child.status = (
                        "PASS"
                        if child.score >= args.pass_threshold and child.evidence_score >= args.pass_threshold
                        else "EXPAND"
                    )
                    nodes[child.node_id] = child
                    node.children.append(child.node_id)
                    if child.status == "EXPAND" and len(nodes) < args.max_nodes:
                        queue.append(child.node_id)

            node.status = "EXPANDED"

        # Anything still queued was cut off by node limit.
        for nid in queue:
            if nid in nodes and nodes[nid].status == "EXPAND":
                nodes[nid].status = "LIMIT_REACHED"

        node_dict = {nid: asdict(node) for nid, node in nodes.items()}
        pass_count = sum(1 for n in nodes.values() if n.status in {"PASS", "PASS_AFTER_RETRIEVAL"})
        open_leaf_count = sum(
            1 for n in nodes.values()
            if (
                n.score is not None
                and n.evidence_score is not None
                and (n.score < args.pass_threshold or n.evidence_score < args.pass_threshold)
                and not n.children
            )
        )
        result: dict[str, Any] = {
            "schema": "werkfaden-jev-adaptive-patch-tree/v1",
            "transaction_id": args.transaction_id,
            "transaction_state": state_name,
            "policy": {
                "pass_threshold": args.pass_threshold,
                "materiality_threshold": args.materiality_threshold,
                "search_limit": args.search_limit,
                "max_depth": args.max_depth,
                "max_nodes": args.max_nodes,
                "max_children_per_node": args.max_children_per_node,
                "max_focus_roots": args.max_focus_roots,
                "batch_size": args.batch_size,
                "focus_review": str(focus_review_path) if focus_review_path else None,
                "focus_mode": (
                    "FAILED_JEV_GATE_DIMENSIONS_KAIROS_RETRIEVAL_ONLY"
                    if focus_review is not None
                    else "GENERIC_LEVEL0_MATRIX"
                ),
                "score_semantics": (
                    "Each proposition has two Noul gates: P(YES) for the proposition and P(YES) that supplied evidence is sufficient to decide it. "
                    "Both must pass; neither is percentage implementation completion."
                ),
            },
            "changed_files": [row.get("path") for row in compact_changes],
            "summary": {
                "node_count": len(nodes),
                "pass_count": pass_count,
                "open_leaf_count": open_leaf_count,
                "query_count": query_count,
                "max_depth_reached": max_depth_reached,
            },
            "root_nodes": [node.node_id for node in roots],
            "nodes": node_dict,
            "evidence_registry": {
                eid: {
                    "kind": item.kind,
                    "label": item.label,
                    "materiality_score": item.materiality_score,
                    "payload": item.payload,
                }
                for eid, item in evidence_registry.items()
            },
            "retrievals": retrieval_records,
            "interpretation_boundary": (
                "This tree is a pre-execution semantic diagnostic of the Workshop candidate. "
                "It does not prove compilation, runtime success, scientific correctness, criterion closure, or release."
            ),
        }

        out_json = Path(args.out_json)
        out_md = Path(args.out_md)
        out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out_md.write_text(markdown_report(result), encoding="utf-8")

        print(json.dumps({
            "schema": result["schema"],
            "transaction_id": args.transaction_id,
            "node_count": len(nodes),
            "pass_count": pass_count,
            "open_leaf_count": open_leaf_count,
            "query_count": query_count,
            "json": str(out_json),
            "markdown": str(out_md),
        }, indent=2))
        return 0

    except (TreeError, gate.GateError) as exc:
        print(json.dumps({"schema": "werkfaden-jev-adaptive-patch-tree-error/v1", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
