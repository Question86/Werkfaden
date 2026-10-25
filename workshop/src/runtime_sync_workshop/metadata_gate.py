"""Evidence-bound metadata maintenance for ordinary Workshop source patches.

This gate checks actual header deltas and exact, deterministic code samples. The
editor supplies the explanation connecting those facts; the gate does not claim
to determine the truth of arbitrary natural-language statements.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from datetime import date, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, TYPE_CHECKING

from .blueprint import BlueprintDocument, parse_blueprint_text
from .util import WorkshopError, canonical_json, normalized_text, reject_link_components, sha256_bytes, sha256_text

if TYPE_CHECKING:
    from .corpus import WorkshopConfig


POLICY_SCHEMA = "runtime-sync-metadata-policy/v1"
AUDIT_SCHEMA = "runtime-sync-metadata-audit/v1"
HEADER_FIELDS = (
    "capsule", "claim_boundary", "answers", "refs", "entities", "facets",
    "does_not_answer", "search_contract",
)
GRAPH_FIELDS = ("relations", "contracts", "artifacts", "drift_records", "graph_metadata")
MAX_SAMPLES_PER_FILE = 3
MAX_SAMPLE_LINES = 12
_RFC3339 = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _semantic_value(value: Any) -> Any:
    """Ignore layout, key order and set/list ordering as evidence of maintenance."""
    value = _jsonable(value)
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {key: _semantic_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        items = [_semantic_value(item) for item in value]
        return sorted(items, key=canonical_json)
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _relative(value: Any) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/")
            or "\\" in value or ":" in value or "\x00" in value
            or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise WorkshopError("METADATA_SCOPE_INVALID", f"invalid transaction-relative path: {value!r}")
    return value


def _file(path: Path, *, code: str = "METADATA_SCOPE_INVALID") -> bytes:
    try:
        reject_link_components(path, label="metadata evidence")
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("expected a single-link regular file")
        return path.read_bytes()
    except (OSError, ValueError, WorkshopError) as exc:
        raise WorkshopError(code, f"unsafe or missing metadata evidence: {path}", details=str(exc)) from exc


def _read_json(path: Path, *, code: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        result = json.loads(_file(path, code=code), object_pairs_hook=unique_pairs)
        if not isinstance(result, dict):
            raise ValueError("expected a JSON object")
        return result
    except (ValueError, UnicodeError) as exc:
        raise WorkshopError(code, f"invalid metadata JSON: {path}", details=str(exc)) from exc


def _document(path: Path, raw: bytes) -> BlueprintDocument:
    try:
        return parse_blueprint_text(path, raw.decode("utf-8"))
    except UnicodeError as exc:
        raise WorkshopError("METADATA_SCOPE_INVALID", f"metadata document is not UTF-8: {path}") from exc


def _sections(document: BlueprintDocument) -> dict[str, str]:
    """Keep human section content while excluding generated mapping, ledger and owner rows."""
    excluded = [document.sections["s-mapping"], document.ledger_span]
    ranges: list[tuple[int, int]] = []
    for span in sorted(excluded, key=lambda item: item.start):
        if ranges and span.start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], span.end))
        else:
            ranges.append((span.start, span.end))

    def retained(start: int, end: int) -> str:
        cursor = start
        parts = []
        for left, right in ranges:
            if right <= start or left >= end:
                continue
            parts.append(document.text[cursor:max(cursor, left)])
            cursor = max(cursor, min(right, end))
        parts.append(document.text[cursor:end])
        return "".join(parts).strip()

    result = {}
    first = min((span.start for span in document.sections.values()), default=len(document.text))
    preamble = retained(document.body_start, first)
    if preamble:
        result["__preamble__"] = preamble
    for name, span in sorted(document.sections.items()):
        text = retained(span.start, span.end)
        if name == "s-ownership" and document.source_path is None:
            # prepare preserves this human heading and Capsule, replacing only
            # the generated owner list. Other prose must remain review-bound.
            prefix = re.match(r'(?s)(<a id="s-ownership"></a>\n##[^\n]*\n\n> Capsule:[^\n]*\n\n)', text)
            if prefix:
                tail = re.sub(
                    r"(?m)^- (?:`[^`\n]+`|none found in the static include closure)[ \t]*(?:\n|$)",
                    "", text[prefix.end():],
                )
                text = (prefix.group(1) + tail).strip()
        if text:
            result[name] = text
    return result


def _field_values(document: BlueprintDocument) -> dict[str, Any]:
    values = {name: _jsonable(document.metadata.get(name)) for name in HEADER_FIELDS}
    values["sections"] = _sections(document)
    if any(name in document.metadata for name in GRAPH_FIELDS):
        values["graph_metadata"] = {name: _jsonable(document.metadata.get(name)) for name in GRAPH_FIELDS}
    return values


def _window(lines: list[str], start: int, end: int) -> dict[str, Any]:
    end = min(end, start + MAX_SAMPLE_LINES)
    quote = "\n".join(lines[start:end])
    return {
        "start_line": start + 1 if end > start else None,
        "end_line": end if end > start else None,
        "anchor_line": start,
        "quote": quote,
        "sha256": sha256_text(quote),
    }


def _samples(path: str, role: str, before: bytes, after: bytes) -> list[dict[str, Any]]:
    try:
        old = normalized_text(before.decode("utf-8")).splitlines()
        new = normalized_text(after.decode("utf-8")).splitlines()
    except UnicodeError as exc:
        raise WorkshopError("METADATA_SCOPE_INVALID", f"code sample is not UTF-8: {path}") from exc
    changes = [item for item in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes() if item[0] != "equal"]
    if not changes:
        # Byte-only changes, including EOF/newline changes, remain changes.
        changes = [("bytes", 0, min(len(old), MAX_SAMPLE_LINES), 0, min(len(new), MAX_SAMPLE_LINES))]
    if len(changes) <= MAX_SAMPLES_PER_FILE:
        selected = range(len(changes))
    else:
        selected = sorted({0, len(changes) // 2, len(changes) - 1})
    result = []
    for index in selected:
        tag, old_start, old_end, new_start, new_end = changes[index]
        identity = {
            "path": path, "before_sha256": sha256_bytes(before), "after_sha256": sha256_bytes(after),
            "hunk": [tag, old_start, old_end, new_start, new_end],
        }
        result.append({
            "id": "S_" + sha256_text(canonical_json(identity))[:24],
            "path": path, "role": role, "change": tag,
            "hunk_index": index + 1, "changed_hunk_count": len(changes),
            "before": _window(old, old_start, old_end),
            "after": _window(new, new_start, new_end),
        })
    return result


class MetadataGate:
    def __init__(self, config: WorkshopConfig) -> None:
        self.config = config

    def plan(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        """Compute the review obligation from actual bytes, never from edited flags."""
        root = Path(root)
        transaction_id = state.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise WorkshopError("METADATA_SCOPE_INVALID", "transaction identity is missing")
        manifest = _read_json(root / "baseline" / "manifest.json", code="METADATA_SCOPE_INVALID")
        records = manifest.get("records")
        sources = state.get("sources", [])
        candidate_code = root / "work"
        if state.get("transaction_kind") == "authority_migration":
            candidates = state.get("candidate_sources"), state.get("candidate_headers")
            if not all(isinstance(values, list) for values in candidates) or not isinstance(records, dict):
                raise WorkshopError("METADATA_SCOPE_INVALID", "invalid authority migration scope")
            # Additions have no previous metadata to update. Every retained owner
            # is inspected, regardless of mutable content_changed declarations.
            scope_paths = [_relative(value) for values in candidates for value in values]
            if len(scope_paths) != len(set(scope_paths)):
                raise WorkshopError("METADATA_SCOPE_INVALID", "duplicate authority migration path")
            sources = [value for value in scope_paths if value in records]
            candidate_code = root / "work" / "candidate"
        if not isinstance(records, dict) or not isinstance(sources, list):
            raise WorkshopError("METADATA_SCOPE_INVALID", "invalid baseline records or transaction sources")
        normalized = [_relative(value) for value in sources]
        if len(normalized) != len(set(normalized)):
            raise WorkshopError("METADATA_SCOPE_INVALID", "duplicate transaction source")
        subjects = []
        for source in sorted(normalized):
            record = records.get(source)
            if not isinstance(record, dict) or not isinstance(record.get("blueprint"), dict):
                raise WorkshopError("METADATA_SCOPE_INVALID", f"source is absent from baseline: {source}")
            name = _relative(record["blueprint"].get("filename"))
            baseline_path = root / "baseline" / "blueprints" / name
            candidate_path = root / "work" / "blueprints" / name
            baseline_raw = _file(baseline_path)
            expected_doc_hash = record["blueprint"].get("raw_sha256")
            if not isinstance(expected_doc_hash, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_doc_hash):
                raise WorkshopError("METADATA_SCOPE_INVALID", f"baseline blueprint digest is missing or invalid: {name}")
            if sha256_bytes(baseline_raw) != expected_doc_hash.lower():
                raise WorkshopError("METADATA_BASELINE_CHANGED", f"baseline blueprint changed: {name}")
            baseline_doc = _document(baseline_path, baseline_raw)
            candidate_doc = _document(candidate_path, _file(candidate_path))
            if any(baseline_doc.metadata.get(key) != candidate_doc.metadata.get(key) for key in ("id", "workspace", "type")):
                raise WorkshopError("METADATA_SCOPE_INVALID", f"blueprint identity changed: {name}")
            paths: dict[str, tuple[str, dict[str, Any]]] = {}
            if baseline_doc.source_path is not None:
                paths[source] = ("source", record.get("source") or {})
            header = record.get("header")
            if isinstance(header, dict) and header.get("path"):
                live_header = Path(str(header["path"]))
                try:
                    reject_link_components(live_header, label="governed header")
                    relative = _relative(live_header.relative_to(self.config.codebase_root).as_posix())
                except (ValueError, WorkshopError) as exc:
                    raise WorkshopError("METADATA_SCOPE_INVALID", "header escapes codebase or traverses a link", details=str(exc)) from exc
                paths[relative] = ("header", header)
            files, samples = [], []
            for relative, (role, facts) in sorted(paths.items()):
                old = _file(root / "baseline" / relative)
                new = _file(candidate_code / relative)
                expected_hash = facts.get("sha256")
                if not isinstance(expected_hash, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_hash):
                    raise WorkshopError("METADATA_SCOPE_INVALID", f"baseline code digest is missing or invalid: {relative}")
                if sha256_bytes(old) != expected_hash.lower():
                    raise WorkshopError("METADATA_BASELINE_CHANGED", f"baseline code changed: {relative}")
                if old == new:
                    continue
                files.append({"path": relative, "role": role, "before_sha256": sha256_bytes(old), "after_sha256": sha256_bytes(new)})
                samples.extend(_samples(relative, role, old, new))
            if not files:
                continue
            before_values, after_values = _field_values(baseline_doc), _field_values(candidate_doc)
            if "graph_metadata" in before_values or "graph_metadata" in after_values:
                before_values.setdefault("graph_metadata", {key: None for key in GRAPH_FIELDS})
                after_values.setdefault("graph_metadata", {key: None for key in GRAPH_FIELDS})
            fields = {
                field: {"before": before_values[field], "after": after_values[field],
                        "changed": canonical_json(_semantic_value(before_values[field])) != canonical_json(_semantic_value(after_values[field]))}
                for field in before_values
            }
            subjects.append({
                "source": source, "blueprint": f"blueprints/{name}", "artifact_id": baseline_doc.artifact_id,
                "files": files, "samples": samples, "fields": fields,
                "changed_header_fields": [field for field in HEADER_FIELDS if fields[field]["changed"]],
                "baseline_semantic_sha256": sha256_text(canonical_json(_semantic_value(before_values))),
                "candidate_semantic_sha256": sha256_text(canonical_json(_semantic_value(after_values))),
            })
        plan = {
            "schema": POLICY_SCHEMA, "transaction_id": transaction_id, "required": bool(subjects),
            "header_fields": list(HEADER_FIELDS), "subjects": subjects,
            "sample_policy": {"max_samples_per_file": MAX_SAMPLES_PER_FILE, "max_lines_per_side": MAX_SAMPLE_LINES,
                              "selection": "first-middle-last changed hunks; exact byte change fallback"},
        }
        plan["plan_sha256"] = sha256_text(canonical_json(plan))
        return plan

    @staticmethod
    def draft(plan: dict[str, Any]) -> dict[str, Any]:
        """Supply facts and empty review slots, never prefilled passing assertions."""
        entries = []
        for subject in plan["subjects"]:
            entries.append({
                "source": subject["source"],
                "field_reviews": {field: {"action": "pending", "reason": "", "sample_ids": []} for field in subject["fields"]},
                "samples": [{**copy.deepcopy(sample), "verdict": "pending", "reason": "", "metadata_evidence": []}
                            for sample in subject["samples"]],
            })
        return {"schema": AUDIT_SCHEMA, "transaction_id": plan["transaction_id"], "plan_sha256": plan["plan_sha256"],
                "reviewer": "", "reviewed_at": "", "entries": entries}

    @staticmethod
    def _reason(value: Any) -> bool:
        return isinstance(value, str) and len(value.strip()) >= 12

    @staticmethod
    def _review_identity(audit: dict[str, Any]) -> None:
        reviewer, stamp = audit.get("reviewer"), audit.get("reviewed_at")
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(stamp, str) or not _RFC3339.fullmatch(stamp):
            raise WorkshopError("METADATA_AUDIT_INVALID", "reviewer and a timezone-aware RFC3339 reviewed_at are required")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise WorkshopError("METADATA_AUDIT_INVALID", "reviewed_at is not a valid RFC3339 date/time", details=str(exc)) from exc

    def validate(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        """Validate a completed report against a freshly derived, exact plan."""
        plan = self.plan(root, state)
        if not plan["required"]:
            return {"required": False, "verified": True, "plan_sha256": plan["plan_sha256"], "source_count": 0, "sample_count": 0}
        unchanged = [subject["source"] for subject in plan["subjects"] if not subject["changed_header_fields"]]
        if unchanged:
            raise WorkshopError("METADATA_HEADER_UNCHANGED", "changed code requires a real contextual header update; body text, layout and generated facts do not count", details=unchanged)
        path = Path(root) / "review" / "metadata_audit.json"
        if not path.exists() and not path.is_symlink():
            raise WorkshopError("METADATA_AUDIT_REQUIRED", "complete review/metadata_audit.json from the current metadata plan before verification")
        audit = _read_json(path, code="METADATA_AUDIT_INVALID")
        if audit.get("schema") != AUDIT_SCHEMA:
            raise WorkshopError("METADATA_AUDIT_INVALID", "metadata audit schema is invalid")
        if audit.get("transaction_id") != plan["transaction_id"] or audit.get("plan_sha256") != plan["plan_sha256"]:
            raise WorkshopError("METADATA_AUDIT_STALE", "metadata audit does not match the current code and semantic header plan; regenerate and review it")
        self._review_identity(audit)
        entries = audit.get("entries")
        if not isinstance(entries, list):
            raise WorkshopError("METADATA_AUDIT_INVALID", "metadata audit entries must be an array")
        by_source: dict[str, Any] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str) or entry["source"] in by_source:
                raise WorkshopError("METADATA_AUDIT_INVALID", "invalid or duplicate metadata audit source")
            by_source[entry["source"]] = entry
        if set(by_source) != {subject["source"] for subject in plan["subjects"]}:
            raise WorkshopError("METADATA_AUDIT_INVALID", "metadata audit must cover exactly every changed source owner")
        sample_count = 0
        for subject in plan["subjects"]:
            entry = by_source[subject["source"]]
            expected_samples = {sample["id"]: sample for sample in subject["samples"]}
            field_reviews = entry.get("field_reviews")
            if not isinstance(field_reviews, dict) or set(field_reviews) != set(subject["fields"]):
                raise WorkshopError("METADATA_FIELD_REVIEW_INVALID", f"review every contextual field and semantic sections for {subject['source']}")
            for field, expected in subject["fields"].items():
                review = field_reviews[field]
                if not isinstance(review, dict):
                    raise WorkshopError("METADATA_FIELD_REVIEW_INVALID", f"invalid field review: {field}")
                ids = review.get("sample_ids")
                if (review.get("action") != ("updated" if expected["changed"] else "unchanged")
                        or not self._reason(review.get("reason")) or not isinstance(ids, list) or not ids
                        or any(not isinstance(sample_id, str) for sample_id in ids)
                        or len(ids) != len(set(ids)) or not set(ids).issubset(expected_samples)):
                    raise WorkshopError("METADATA_FIELD_REVIEW_INVALID", f"field decision, reason or source evidence contradicts actual metadata: {subject['source']}:{field}")
            rows = entry.get("samples")
            if not isinstance(rows, list):
                raise WorkshopError("METADATA_SAMPLE_INVALID", "code samples must be an array")
            observed: set[str] = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in expected_samples or row["id"] in observed:
                    raise WorkshopError("METADATA_SAMPLE_INVALID", "unexpected or duplicate code sample")
                observed.add(row["id"])
                expected = expected_samples[row["id"]]
                if any(row.get(key) != value for key, value in expected.items()):
                    raise WorkshopError("METADATA_SAMPLE_INVALID", "sample paths, ranges, quotes and digests must match actual changed code")
                if row.get("verdict") != "pass":
                    raise WorkshopError("METADATA_SAMPLE_FAILED", "all required metadata/code samples must be reviewed and pass")
                if not self._reason(row.get("reason")):
                    raise WorkshopError("METADATA_SAMPLE_INVALID", "each sample needs a concrete explanation connecting code and metadata")
                evidence = row.get("metadata_evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise WorkshopError("METADATA_SAMPLE_INVALID", "each code sample needs an exact contextual metadata quotation")
                for quote in evidence:
                    if (not isinstance(quote, dict) or not isinstance(quote.get("field"), str)
                            or quote["field"] not in subject["fields"]):
                        raise WorkshopError("METADATA_SAMPLE_INVALID", "metadata evidence names an unreviewed field")
                    field, text = quote["field"], quote.get("quote")
                    if (not isinstance(text, str) or len(text.strip()) < 8
                            or not any(text in value for value in _strings(subject["fields"][field]["after"]))
                            or row["id"] not in field_reviews[field]["sample_ids"]):
                        raise WorkshopError("METADATA_SAMPLE_INVALID", "metadata quotation is absent from the reviewed candidate field or lacks its sample link")
                sample_count += 1
            if observed != set(expected_samples):
                raise WorkshopError("METADATA_SAMPLE_INVALID", "all deterministic code samples must be reviewed")
        return {
            "required": True, "verified": True, "plan_sha256": plan["plan_sha256"],
            "source_count": len(plan["subjects"]), "sample_count": sample_count,
            "changed_header_fields": {subject["source"]: subject["changed_header_fields"] for subject in plan["subjects"]},
            "reviewer": audit["reviewer"], "reviewed_at": audit["reviewed_at"],
            "claim_boundary": "Exact contextual header deltas and sample evidence checked; semantic conclusions remain the reviewer's responsibility.",
        }
