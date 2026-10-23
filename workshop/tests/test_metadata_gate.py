from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for value in (ROOT, ROOT / "workshop" / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from kickstart.tests.test_universal_workshop import _project_spec  # noqa: E402
from kickstart.universal_init import initialize_universal_markdown  # noqa: E402
from runtime_sync_workshop.blueprint import parse_blueprint, semantic_document_hash  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from runtime_sync_workshop.metadata_gate import HEADER_FIELDS  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit, stage_source_metadata  # noqa: E402


class MetadataGateTests(unittest.TestCase):
    """Real sealed fixtures exercise the before-apply metadata authority boundary."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="metadata-gate-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture_number = 0

    def checkout(self, *, baseline: bytes = b"value = 1\n", selected: tuple[str, ...] = ("app.py",)) -> dict:
        self.fixture_number += 1
        fixture = self.root / str(self.fixture_number)
        self.project = fixture / "project"
        self.project.mkdir(parents=True)
        self.workspace = fixture / "workspace"
        (self.project / "app.py").write_bytes(baseline)
        (self.project / "helper.py").write_bytes(b"def helper():\n    return 7\n")
        intake = initialize_universal_markdown(
            project_root=self.project, workspace=self.workspace, project_spec=_project_spec(),
        )
        self.assertEqual(intake["state"], "VERIFIED_PENDING_SEAL")
        self.engine = WorkshopEngine(Path(intake["workshop_config"]))
        self.engine.seal()
        self.checked_out = self.engine.checkout(
            selected, purpose="Review actual source changes and contextual metadata before promotion",
        )
        self.transaction_id = self.checked_out["transaction_id"]
        self.work = Path(self.checked_out["work_directory"])
        self.transaction = self.work.parent
        self.manifest = json.loads((self.transaction / "baseline" / "manifest.json").read_text(encoding="utf-8"))
        self.blueprint = self.work / "blueprints" / self.manifest["records"]["app.py"]["blueprint"]["filename"]
        self.baseline_blueprint = self.transaction / "baseline" / "blueprints" / self.blueprint.name
        (self.work / "app.py").write_bytes(b"value = 2\n")
        self.complete_general_review()
        return self.checked_out

    def complete_general_review(self) -> None:
        """Describe actual old-gate impact so a different blocker cannot hide the new gate."""
        path = Path(self.checked_out["metadata_review"])
        review = json.loads(path.read_text(encoding="utf-8"))
        for entry in review["entries"]:
            filename = self.manifest["records"][entry["source"]]["blueprint"]["filename"]
            before = parse_blueprint(self.transaction / "baseline" / "blueprints" / filename)
            after = parse_blueprint(self.work / "blueprints" / filename)
            entry.update(
                metadata_impact="updated" if semantic_document_hash(before) != semantic_document_hash(after) else "none",
                reason="Checked the actual source and document differences for this isolated regression fixture.",
                reviewer="metadata-gate-regression", reviewed_at="2026-10-23T12:00:00Z",
            )
        self.write_json(path, review)

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.write_bytes((json.dumps(value, indent=2) + "\n").encode("utf-8"))

    def replace_header(self, field: str, value: object) -> None:
        text, count = re.subn(
            rf"(?m)^{re.escape(field)}\s*=.*$",
            lambda _match: f"{field} = " + json.dumps(value),
            self.blueprint.read_text(encoding="utf-8"), count=1,
        )
        self.assertEqual(count, 1)
        self.blueprint.write_bytes(text.encode("utf-8"))

    def assert_blocked(self, code: str, operation=None) -> WorkshopError:
        with self.assertRaises(WorkshopError) as raised:
            (operation or self.engine.prepare)(self.transaction_id)
        self.assertEqual(raised.exception.code, code, str(raised.exception))
        return raised.exception

    def complete_audit(self) -> tuple[dict, Path, dict]:
        plan, path = complete_metadata_audit(self.engine, self.checked_out)
        return plan, path, json.loads(path.read_text(encoding="utf-8"))

    def prepare_and_check(self) -> dict:
        self.complete_audit()
        self.assertEqual(self.engine.prepare(self.transaction_id)["state"], "PREPARED")
        return self.engine.metadata_check(self.transaction_id)

    def test_body_text_timestamp_and_header_formatting_do_not_satisfy_header_obligation(self) -> None:
        self.checkout()
        original = self.blueprint.read_bytes()
        capsule = parse_blueprint(self.blueprint).metadata["capsule"]
        for change in ("body", "timestamp", "revision", "whitespace"):
            with self.subTest(change=change):
                self.blueprint.write_bytes(original)
                if change == "body":
                    self.blueprint.write_bytes(original + b"\nBananas were mentioned during the review.\n")
                elif change == "timestamp":
                    self.replace_header("updated_at", "2026-10-23T12:00:00Z")
                elif change == "revision":
                    self.replace_header("revision", 99)
                else:
                    self.replace_header("capsule", "  " + capsule.replace(" ", "  ") + "  ")
                self.complete_general_review()
                self.assert_blocked("METADATA_HEADER_UNCHANGED")
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")

    def test_real_header_change_still_requires_completed_audit(self) -> None:
        self.checkout()
        stage_source_metadata(self.engine, self.checked_out)
        self.assert_blocked("METADATA_AUDIT_REQUIRED")
        result = self.engine.metadata_plan(self.transaction_id)
        draft = json.loads(Path(result["metadata_audit"]).read_text(encoding="utf-8"))
        self.assertEqual(draft["reviewer"], "")
        self.assertTrue(all(row["verdict"] == "pending" for entry in draft["entries"] for row in entry["samples"]))
        self.assert_blocked("METADATA_AUDIT_INVALID")

    def test_every_field_needs_a_correct_decision_and_actual_sample_reference(self) -> None:
        self.checkout()
        _plan, path, valid = self.complete_audit()
        mutations = {
            "missing field": lambda fields: fields.pop("claim_boundary"),
            "unchanged capsule": lambda fields: fields["capsule"].update(action="unchanged"),
            "unmodified field claimed updated": lambda fields: fields["claim_boundary"].update(action="updated"),
            "malformed field": lambda fields: fields.update(capsule=[]),
            "pending field": lambda fields: fields["refs"].update(action="pending"),
            "short reason": lambda fields: fields["entities"].update(reason="checked"),
            "invented sample": lambda fields: fields["answers"].update(sample_ids=["S_invented"]),
            "no sample": lambda fields: fields["sections"].update(sample_ids=[]),
        }
        self.assertEqual(set(valid["entries"][0]["field_reviews"]), set(HEADER_FIELDS) | {"sections"})
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                audit = copy.deepcopy(valid)
                mutate(audit["entries"][0]["field_reviews"])
                self.write_json(path, audit)
                self.assert_blocked("METADATA_FIELD_REVIEW_INVALID")

    def test_review_requires_real_timezone_aware_date(self) -> None:
        self.checkout()
        _plan, path, valid = self.complete_audit()
        for date in ("yesterday", "2026-02-30T12:00:00Z", "2026-10-23T12:00:00", "2026-10-23T12:00:00+01:99"):
            with self.subTest(date=date):
                audit = copy.deepcopy(valid)
                audit["reviewed_at"] = date
                self.write_json(path, audit)
                self.assert_blocked("METADATA_AUDIT_INVALID")

    def test_sample_quotes_hashes_ranges_and_metadata_citations_are_checked(self) -> None:
        self.checkout()
        _plan, path, valid = self.complete_audit()

        def forged_code(sample: dict) -> None:
            sample["after"].update(quote="value = 999", sha256=hashlib.sha256(b"value = 999").hexdigest())

        mutations = {
            "self-consistent fake code quote": forged_code,
            "wrong code hash": lambda row: row["after"].update(sha256="0" * 64),
            "wrong line range": lambda row: row["after"].update(start_line=999),
            "fake metadata quote": lambda row: row.update(metadata_evidence=[{"field": "capsule", "quote": "An invented assertion that is absent from the document."}]),
            "no metadata citation": lambda row: row.update(metadata_evidence=[]),
            "generated field cited": lambda row: row.update(metadata_evidence=[{"field": "updated_at", "quote": "2026-10-23"}]),
            "missing explanation": lambda row: row.update(reason=""),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                audit = copy.deepcopy(valid)
                mutate(audit["entries"][0]["samples"][0])
                self.write_json(path, audit)
                self.assert_blocked("METADATA_SAMPLE_INVALID")
        audit = copy.deepcopy(valid)
        audit["entries"][0]["samples"] = []
        self.write_json(path, audit)
        self.assert_blocked("METADATA_SAMPLE_INVALID")

    def test_partial_fail_and_unanswered_sample_verdicts_block_preparation(self) -> None:
        self.checkout()
        _plan, path, valid = self.complete_audit()
        for verdict in ("partial", "fail", "pending"):
            with self.subTest(verdict=verdict):
                audit = copy.deepcopy(valid)
                audit["entries"][0]["samples"][0]["verdict"] = verdict
                self.write_json(path, audit)
                self.assert_blocked("METADATA_SAMPLE_FAILED")

    def test_report_belongs_to_exact_transaction_and_candidate(self) -> None:
        self.checkout()
        _plan, path, valid = self.complete_audit()
        for key, value in (("transaction_id", "unrelated-transaction"), ("plan_sha256", "0" * 64)):
            with self.subTest(key=key):
                audit = copy.deepcopy(valid)
                audit[key] = value
                self.write_json(path, audit)
                self.assert_blocked("METADATA_AUDIT_STALE")
        self.write_json(path, valid)
        original_source = (self.work / "app.py").read_bytes()
        (self.work / "app.py").write_bytes(b"value = 3\n")
        self.assert_blocked("METADATA_AUDIT_STALE")
        (self.work / "app.py").write_bytes(original_source)
        self.replace_header("capsule", "The application assigns value 2 and defines no callable functions.")
        self.assert_blocked("METADATA_AUDIT_STALE")

    def test_altered_baseline_evidence_is_rejected_before_preparation(self) -> None:
        self.checkout()
        self.complete_audit()
        for path in (self.transaction / "baseline" / "app.py", self.baseline_blueprint):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.assert_blocked("METADATA_BASELINE_CHANGED")
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
                path.write_bytes(original)

    def test_planning_preserves_review_until_explicit_refresh_for_changed_candidate(self) -> None:
        self.checkout()
        plan, path, _audit = self.complete_audit()
        reviewed_bytes = path.read_bytes()
        same = self.engine.metadata_plan(self.transaction_id)
        self.assertTrue(same["existing_review_preserved"])
        self.assertEqual(same["plan"]["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(path.read_bytes(), reviewed_bytes)
        (self.work / "app.py").write_bytes(b"value = 3\n")
        self.assert_blocked("METADATA_PLAN_STALE", self.engine.metadata_plan)
        refreshed = self.engine.metadata_plan(self.transaction_id, refresh=True)
        self.assertFalse(refreshed["existing_review_preserved"])
        self.assertNotEqual(refreshed["plan"]["plan_sha256"], plan["plan_sha256"])
        draft = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(draft["reviewer"], "")
        self.assertEqual(draft["entries"][0]["samples"][0]["verdict"], "pending")

    def test_verify_requires_separate_aftercheck_and_receipt_reaches_apply(self) -> None:
        self.checkout()
        plan, _path, _audit = self.complete_audit()
        prepared = self.engine.prepare(self.transaction_id)
        self.assertEqual(prepared["metadata_aftercheck"]["changed_header_fields"], {"app.py": ["capsule"]})
        self.assert_blocked("METADATA_CHECK_REQUIRED", self.engine.verify)
        checked = self.engine.metadata_check(self.transaction_id)
        self.assertEqual(checked["review"]["plan_sha256"], plan["plan_sha256"])
        self.assertTrue(checked["verified"])
        self.assertEqual(self.engine.verify(self.transaction_id)["state"], "SHADOW_VERIFIED")
        receipt = self.engine.apply(self.transaction_id)
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        self.assertEqual(receipt["metadata_aftercheck"], checked["review"])
        self.assertEqual((self.project / "app.py").read_bytes(), b"value = 2\n")

    def test_apply_rechecks_aftercheck_presence_and_binding(self) -> None:
        self.checkout()
        self.prepare_and_check()
        self.engine.verify(self.transaction_id)
        path = self.transaction / "state.json"
        verified = json.loads(path.read_text(encoding="utf-8"))
        # Simulate corruption/loss of an already genuine receipt. No successful
        # test path fabricates approval state or bypasses the metadata gate.
        for field in (None, "policy", "work_package_sha256", "review"):
            with self.subTest(field=field):
                state = copy.deepcopy(verified)
                if field is None:
                    state.pop("metadata_check")
                else:
                    state["metadata_check"][field] = "corrupted"
                self.write_json(path, state)
                expected = "METADATA_CHECK_REQUIRED" if field is None else "METADATA_CHECK_STALE"
                self.assert_blocked(expected, self.engine.apply)
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
        self.write_json(path, verified)

    def test_code_doc_and_audit_edits_after_check_require_new_preparation(self) -> None:
        self.checkout()
        self.prepare_and_check()
        paths = (self.work / "app.py", self.blueprint, self.transaction / "review" / "metadata_audit.json")
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.assert_blocked("WORK_CHANGED_AFTER_PREPARE", self.engine.verify)
                self.assert_blocked("WORK_CHANGED_AFTER_PREPARE", self.engine.metadata_check)
                path.write_bytes(original)

    def test_code_doc_and_audit_edits_after_verify_cannot_be_applied(self) -> None:
        self.checkout()
        self.prepare_and_check()
        self.engine.verify(self.transaction_id)
        paths = (self.work / "app.py", self.blueprint, self.transaction / "review" / "metadata_audit.json")
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.assert_blocked("WORK_CHANGED_AFTER_VERIFY", self.engine.apply)
                self.assertEqual((self.project / "app.py").read_bytes(), b"value = 1\n")
                path.write_bytes(original)

    def test_reprepare_invalidates_receipt_even_when_new_review_fails(self) -> None:
        self.checkout()
        self.prepare_and_check()
        self.engine.prepare(self.transaction_id)
        self.assertNotIn("metadata_check", self.engine.transaction_status(self.transaction_id))
        self.assert_blocked("METADATA_CHECK_REQUIRED", self.engine.verify)
        self.engine.metadata_check(self.transaction_id)
        self.engine.verify(self.transaction_id)
        path = self.transaction / "review" / "metadata_audit.json"
        audit = json.loads(path.read_text(encoding="utf-8"))
        audit["entries"][0]["samples"][0]["verdict"] = "fail"
        self.write_json(path, audit)
        self.assert_blocked("METADATA_SAMPLE_FAILED")
        state = self.engine.transaction_status(self.transaction_id)
        for key in ("metadata_check", "prepared_work_package_sha256", "verified_work_package_sha256", "shadow"):
            self.assertNotIn(key, state)
        self.assert_blocked("TRANSACTION_STATE_INVALID", self.engine.apply)

    def test_unchanged_selected_sources_need_no_artificial_header_edit(self) -> None:
        self.checkout(selected=("app.py", "helper.py"))
        other_name = self.manifest["records"]["helper.py"]["blueprint"]["filename"]
        original = (self.work / "blueprints" / other_name).read_bytes()
        plan, _path, audit = self.complete_audit()
        self.assertEqual([subject["source"] for subject in plan["subjects"]], ["app.py"])
        self.assertEqual([entry["source"] for entry in audit["entries"]], ["app.py"])
        self.engine.prepare(self.transaction_id)
        self.engine.metadata_check(self.transaction_id)
        self.engine.verify(self.transaction_id)
        self.engine.apply(self.transaction_id)
        self.assertEqual((self.work / "blueprints" / other_name).read_bytes(), original)
        self.assertEqual((self.project / "helper.py").read_bytes(), b"def helper():\n    return 7\n")

    def test_empty_source_deletion_has_real_before_evidence_and_no_invented_after_lines(self) -> None:
        self.checkout()
        (self.work / "app.py").write_bytes(b"")
        plan, _path, _audit = self.complete_audit()
        sample = plan["subjects"][0]["samples"][0]
        self.assertEqual(sample["change"], "delete")
        self.assertEqual(sample["before"]["quote"], "value = 1")
        self.assertEqual(sample["after"]["quote"], "")
        self.assertIsNone(sample["after"]["start_line"])
        self.assertIsNone(sample["after"]["end_line"])
        self.engine.prepare(self.transaction_id)
        self.engine.metadata_check(self.transaction_id)
        self.engine.verify(self.transaction_id)
        self.assertEqual(self.engine.apply(self.transaction_id)["state"], "POSTCHECK_VERIFIED")
        self.assertEqual((self.project / "app.py").read_bytes(), b"")

    def test_stichproben_cover_first_middle_and_last_hunks_deterministically(self) -> None:
        baseline = "".join(f"value_{number} = 1\n# unchanged boundary {number}\n" for number in range(1, 8))
        self.checkout(baseline=baseline.encode("utf-8"))
        (self.work / "app.py").write_bytes(baseline.replace(" = 1", " = 2").encode("utf-8"))
        plan, _path, _audit = self.complete_audit()
        samples = plan["subjects"][0]["samples"]
        self.assertEqual([sample["hunk_index"] for sample in samples], [1, 4, 7])
        self.assertEqual({sample["changed_hunk_count"] for sample in samples}, {7})
        self.assertEqual([sample["after"]["quote"] for sample in samples], ["value_1 = 2", "value_4 = 2", "value_7 = 2"])
        again = self.engine.metadata_plan(self.transaction_id)
        self.assertEqual(again["plan"], plan)
        self.assertEqual(self.engine.prepare(self.transaction_id)["state"], "PREPARED")
        self.assertEqual(self.engine.metadata_check(self.transaction_id)["review"]["sample_count"], 3)


if __name__ == "__main__":
    unittest.main()
