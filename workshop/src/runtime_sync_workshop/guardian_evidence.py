from __future__ import annotations

from .guardian_common import *


class GuardianEvidenceMixin:
    def _evidence(self, payload: dict[str, Any], step: str, evidence_refs: Iterable[str]) -> list[dict[str, Any]]:
        refs = [str(value).strip() for value in evidence_refs if str(value).strip()]
        if step in {"HYPOTHESIS", "PROVE"}:
            if step == "PROVE" and refs:
                raise WorkshopError("GUARDIAN_PROOF_OPAQUE_EVIDENCE", "PROVE does not accept opaque evidence; it binds the validated prior evidence chain")
            return []
        if not refs:
            raise WorkshopError("GUARDIAN_EVIDENCE_MISSING", f"{step} requires evidence")
        if len(refs) != len(set(refs)):
            raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"{step} contains duplicate evidence")
        already = {item["id"] for values in (payload.get("evidence") or {}).values() for item in values if isinstance(item, dict) and item.get("id")}
        if any(value in already for value in refs):
            raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"evidence was already consumed by an earlier state: {refs}")
        if step == "FALSIFIER_1":
            if len(refs) != 1 or not refs[0].startswith("SRR_"):
                raise WorkshopError("GUARDIAN_ROUTING_RECEIPT_REQUIRED", "FALSIFIER_1 requires exactly one SRR_ receipt")
            return [self._validate_srr(payload, refs[0], purpose="falsifier1")]
        if step == "FALSIFIER_2":
            if len(refs) != 1 or not refs[0].startswith("SRR_"):
                raise WorkshopError("GUARDIAN_GRAPH_EVIDENCE_REQUIRED", "FALSIFIER_2 requires exactly one graph-routed SRR_ receipt")
            row = self._validate_srr(payload, refs[0], purpose="falsifier2")
            f1 = (payload.get("evidence") or {}).get("FALSIFIER_1") or []
            if f1 and row.get("query_sha256") == f1[0].get("query_sha256"):
                raise WorkshopError("GUARDIAN_FALSIFIER_NOT_INDEPENDENT", "FALSIFIER_2 query must differ from FALSIFIER_1")
            return [row]
        if step == "MAP":
            if any(not value.startswith("SRR_") for value in refs):
                raise WorkshopError("GUARDIAN_GRAPH_EVIDENCE_REQUIRED", "MAP accepts only routing/graph SRR_ receipts")
            return [self._validate_srr(payload, value, purpose="map") for value in refs]
        if step == "COUNTERPROBE":
            rows: list[dict[str, Any]] = []
            for value in refs:
                if value.startswith("SIR_"):
                    rows.append(self._validate_sir(payload, value, positive=False, purpose="counterprobe"))
                elif value.startswith("SRR_"):
                    row = self._validate_srr(payload, value, purpose="counterprobe")
                    if row["status"] != "NO_MATCH":
                        raise WorkshopError("GUARDIAN_NEGATIVE_PROBE_REQUIRED", "SRR counterprobe must be an explicit NO_MATCH result")
                    rows.append(row)
                else:
                    raise WorkshopError("GUARDIAN_NEGATIVE_PROBE_REQUIRED", "counterprobe requires SRR_ or SIR_ negative evidence")
            mapped = set(payload.get("mapped_sources") or [])
            for row in rows:
                if row.get("kind") == "SIR" and not set(row.get("paths") or []).issubset(mapped):
                    raise WorkshopError("GUARDIAN_COUNTERPROBE_OUTSIDE_MAP", "source counterprobe escaped the mapped causal surface")
            return rows
        if step == "EXACT_SOURCE":
            if any(not value.startswith("SIR_") for value in refs):
                raise WorkshopError("GUARDIAN_SOURCE_RECEIPT_REQUIRED", "EXACT_SOURCE accepts only SIR_ receipts")
            rows = [self._validate_sir(payload, value, positive=True, purpose="exact_source") for value in refs]
            mapped = set(payload.get("mapped_sources") or [])
            if not mapped:
                raise WorkshopError("GUARDIAN_MAP_SCOPE_MISSING", "MAP did not freeze expected source owners")
            inspected = {path for row in rows for path in row["paths"]}
            if not inspected.issubset(mapped):
                raise WorkshopError("GUARDIAN_SOURCE_OUTSIDE_MAP", "exact-source receipt includes paths outside MAP", details={"mapped": sorted(mapped), "inspected": sorted(inspected)})
            return rows
        return []


    @staticmethod
    def _structured_summary(step: str, summary: str) -> dict[str, Any]:
        text = str(summary).strip()
        if step not in {"HYPOTHESIS", "PROVE"}:
            if len(text) < 12:
                raise WorkshopError("GUARDIAN_SUMMARY_TOO_SHORT", f"{step} summary must contain at least 12 characters")
            return {"text": text}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise WorkshopError("GUARDIAN_STRUCTURED_SUMMARY_REQUIRED", f"{step} summary must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise WorkshopError("GUARDIAN_STRUCTURED_SUMMARY_REQUIRED", f"{step} summary must be a JSON object")
        required = (
            {"observation", "invariant", "suspected_mechanism", "expected_owner", "refutation"}
            if step == "HYPOTHESIS"
            else {"observation", "violated_invariant", "causal_owner", "scope_reason", "general_fix_reason"}
        )
        missing = sorted(key for key in required if not isinstance(payload.get(key), str) or len(payload[key].strip()) < 4)
        if missing:
            raise WorkshopError("GUARDIAN_STRUCTURED_SUMMARY_REQUIRED", f"{step} missing fields: {', '.join(missing)}")
        return payload
