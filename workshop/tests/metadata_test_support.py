"""Concrete metadata-review fixtures for native Workshop regression tests.

This helper edits only the isolated test transaction. It does not replace a gate,
fake a receipt, or alter engine state. Claims quote actual candidate source lines.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any


def _source_claim(relative: str, baseline: bytes, candidate: bytes) -> str:
    before = baseline.decode("utf-8").splitlines()
    after = candidate.decode("utf-8").splitlines()
    changed_lines: list[str] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag != "equal":
            changed_lines.extend(line.strip() for line in after[j1:j2] if line.strip())
    if not after:
        return f"The current source `{relative}` is empty after the selected deletion."
    evidence = " ".join(changed_lines or [line.strip() for line in after if line.strip()])
    evidence = evidence[: min(300, 420 - len(relative))]
    return f"The current source `{relative}` contains `{evidence}`."


def stage_source_metadata(engine: Any, checkout: dict[str, Any], *, capsules: dict[str, str] | None = None) -> None:
    """Give each byte-changed fixture owner an actual, source-backed header edit."""
    work = Path(checkout["work_directory"])
    root = work.parent
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "baseline" / "manifest.json").read_text(encoding="utf-8"))
    reviewed: set[str] = set()
    for source in state["sources"]:
        record = manifest["records"][source]
        relatives = [source]
        header = record.get("header")
        if header:
            relative = Path(header["path"]).resolve().relative_to(engine.config.codebase_root.resolve()).as_posix()
            if relative not in relatives:
                relatives.append(relative)
        changed: list[tuple[str, bytes, bytes]] = []
        for relative in relatives:
            old = root / "baseline" / relative
            new = work / relative
            if old.is_file() and new.is_file() and old.read_bytes() != new.read_bytes():
                changed.append((relative, old.read_bytes(), new.read_bytes()))
        if not changed:
            continue
        capsule = (capsules or {}).get(source)
        if capsule is None:
            capsule = " ".join(_source_claim(*row) for row in changed)
        if len(capsule) > 480:
            raise AssertionError("Fixture capsule must stay within the real metadata schema limit")
        blueprint = work / "blueprints" / record["blueprint"]["filename"]
        text = blueprint.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^capsule\s*=.*$",
            lambda _match: "capsule = " + json.dumps(capsule, ensure_ascii=False),
            text,
            count=1,
        )
        if count != 1:
            raise AssertionError(f"Fixture blueprint has no capsule: {source}")
        blueprint.write_bytes(updated.encode("utf-8"))
        reviewed.add(source)
    review_path = Path(checkout["metadata_review"])
    review = json.loads(review_path.read_text(encoding="utf-8"))
    for entry in review["entries"]:
        if entry["source"] in reviewed:
            entry.update(
                metadata_impact="updated",
                reason="The semantic capsule describes the actual changed fixture source lines and all context fields were reviewed.",
                reviewer=entry.get("reviewer") or "metadata-regression-test",
                reviewed_at="2026-10-23T12:00:00Z",
            )
    review_path.write_bytes((json.dumps(review, indent=2) + "\n").encode("utf-8"))


def complete_metadata_audit(engine: Any, checkout: dict[str, Any], *, update_capsules: bool = True,
                            capsules: dict[str, str] | None = None) -> tuple[dict[str, Any], Path]:
    """Perform the public planning step and fill evidence for small known fixtures."""
    if update_capsules:
        stage_source_metadata(engine, checkout, capsules=capsules)
    result = engine.metadata_plan(checkout["transaction_id"], refresh=True)
    plan = result["plan"]
    path = Path(result["metadata_audit"])
    audit = json.loads(path.read_text(encoding="utf-8"))
    audit["reviewer"] = "metadata-regression-test"
    audit["reviewed_at"] = "2026-10-23T12:00:00Z"
    subjects = {subject["source"]: subject for subject in plan["subjects"]}
    for entry in audit["entries"]:
        subject = subjects[entry["source"]]
        sample_ids = [sample["id"] for sample in subject["samples"]]
        for field, decision in entry["field_reviews"].items():
            changed = subject["fields"][field]["changed"]
            decision.update(
                action="updated" if changed else "unchanged",
                reason=f"Compared {field} against the candidate fixture and its quoted source evidence; the recorded field difference is exact.",
                sample_ids=sample_ids,
            )
        capsule = subject["fields"]["capsule"]["after"]
        for sample in entry["samples"]:
            sample.update(
                verdict="pass",
                reason="The quoted candidate lines support the fixture capsule; removed baseline lines were checked against the replacement.",
                metadata_evidence=[{"field": "capsule", "quote": capsule}],
            )
    path.write_bytes((json.dumps(audit, indent=2) + "\n").encode("utf-8"))
    return plan, path
