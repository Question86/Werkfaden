from __future__ import annotations

import unittest

from kairos.constants import FIRST_WINDOW_BYTES, MAX_HEADER_BYTES
from kairos.frontmatter import HeaderError, split_frontmatter
from kairos.references import parse_reference
from kairos.sections import SectionError, parse_sections, validate_answer_targets
from kairos.templates import pointer, task_document


class FrontmatterSectionTests(unittest.TestCase):
    def _document(self) -> str:
        return task_document(
            task_id="TASK_9001",
            title="Validate context grammar",
            objective="Prove that KAIROS headers and stable answer sections fit inside the first model-read window.",
            workspace_id="KAIROS_TEST",
            goal_id="GOAL_TEST_001",
            milestone_id="MILESTONE_TEST_01",
            criteria=[
                {
                    "id": "CRIT_TEST_001",
                    "description": "The context header and section anchors validate.",
                    "evidence_required": "A deterministic parser test.",
                }
            ],
            loop=1,
        )

    def test_generated_document_respects_first_window_contract(self) -> None:
        parsed = split_frontmatter(self._document())
        sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
        validate_answer_targets(parsed.metadata, sections)
        first_anchor = parsed.body.index('<a id="s-objective"></a>')
        first_section_byte = parsed.header_bytes + len(parsed.body[:first_anchor].encode("utf-8"))
        self.assertLessEqual(parsed.header_bytes, MAX_HEADER_BYTES)
        self.assertLessEqual(first_section_byte, FIRST_WINDOW_BYTES)
        self.assertEqual({answer["target"] for answer in parsed.metadata["answers"]}, {"s-objective", "s-dependencies", "s-acceptance"})

    def test_missing_answer_target_fails(self) -> None:
        parsed = split_frontmatter(self._document())
        sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
        parsed.metadata["answers"][0]["target"] = "s-missing"
        with self.assertRaises(SectionError):
            validate_answer_targets(parsed.metadata, sections)

    def test_non_english_answer_handle_fails(self) -> None:
        text = self._document().replace('target = "s-objective"', 'target = "s-objective"\nlanguage = "fr"', 1)
        with self.assertRaises(HeaderError):
            split_frontmatter(text)

    def test_future_updated_at_fails(self) -> None:
        text = self._document().replace(
            next(line for line in self._document().splitlines() if line.startswith('updated_at = ')),
            'updated_at = "2999-01-01T00:00:00Z"',
            1,
        )
        with self.assertRaisesRegex(HeaderError, "more than five minutes in the future"):
            split_frontmatter(text)

    def test_typed_pointer_round_trip(self) -> None:
        raw = pointer(
            "bugs/BUG_9001.md",
            section="s-root-cause",
            artifact_id="BUG_9001",
            version="4",
            relation="caused_by",
            tags=("causal-chain", "diagnostic"),
            source="test",
        )
        parsed = parse_reference(raw[5:-1])
        self.assertEqual(parsed.target_path, "bugs/BUG_9001.md")
        self.assertEqual(parsed.target_section, "s-root-cause")
        self.assertEqual(parsed.target_id, "BUG_9001")
        self.assertEqual(parsed.relation, "caused_by")

    def test_success_report_requires_explicit_evidence(self) -> None:
        from kairos.templates import report_document

        with self.assertRaises(ValueError):
            report_document(
                report_id="REPORT_TASK_9001_L001_V01",
                task_id="TASK_9001",
                title="Invalid success report",
                workspace_id="KAIROS_TEST",
                goal_id="GOAL_TEST_001",
                milestone_id="MILESTONE_TEST_01",
                criteria=["CRIT_TEST_001"],
                task_path="tasks/task_TASK_9001.md",
                loop=1,
                state="success",
            )


if __name__ == "__main__":
    unittest.main()
