from __future__ import annotations

import unittest

from kairos.constants import FIRST_WINDOW_BYTES, MAX_HEADER_BYTES
from kairos.frontmatter import HeaderError, split_frontmatter
from kairos.references import parse_reference
from kairos.sections import SectionError, parse_sections, validate_answer_targets
from kairos.templates import pointer, task_document


class FrontmatterSectionTests(unittest.TestCase):
    def test_capsule_and_index_truncation_never_split_typed_pointers(self):
        import re
        from kairos.references import collect_references
        from kairos.templates import bug_document
        ref = pointer("docs/KNOWN.md", section="s-proof", artifact_id="DOC_KNOWN",
                      version="dynamic", tags=("evidence",))
        for offset in (0, 60, 110, 440, 465):
            with self.subTest(offset=offset):
                content = ("x " * ((offset + 1) // 2))[:offset] + ref + " retained evidence"
                text = bug_document(bug_id="BUG_CAPSULE_BOUNDARY", task_id="TASK_CAPSULE_PARENT",
                    workspace_id="KAIROS_TEST", task_path="tasks/task_TASK_CAPSULE_PARENT.md",
                    goal_id="GOAL_TEST_001", milestone_id="MILESTONE_TEST_01",
                    observation=content, regression=content, loop=1)
                parsed = split_frontmatter(text)
                sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
                validate_answer_targets(parsed.metadata, sections)
                self.assertIn(content, parsed.body)
                summaries = [parsed.metadata["capsule"]]
                summaries += [line for line in text.splitlines()
                              if line.startswith("> Capsule:") or line.startswith("- [" + chr(96) + "s-")]
                for summary in summaries:
                    complete_removed = re.sub(r"\[ref:[^\]\r\n]+\]", "", summary)
                    self.assertNotIn("[ref:", complete_removed, summary)
                known = {"docs/KNOWN.md", "tasks/task_TASK_CAPSULE_PARENT.md",
                         "bugs/BUG_CAPSULE_BOUNDARY.md"}
                self.assertTrue(all(ref.target_path in known
                                    for _, ref in collect_references(parsed.metadata, sections)))

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


    def test_shared_renderer_flattens_capsules_without_creating_structure(self) -> None:
        from kairos.templates import render_document
        metadata = split_frontmatter(self._document()).metadata
        for capsule in (
            "Summary.\n\x60\x60\x60json\n{\n",
            'Summary.\r\n<a id="s-injected"></a>\r\n## Injected heading',
            "Summary.\u2028line\tvalue\rnext",
        ):
            with self.subTest(capsule=capsule):
                sections = [
                    {"id": section_id, "title": title, "capsule": capsule,
                     "content": "Original evidence stays intact."}
                    for section_id, title in (
                        ("s-objective", "Objective"),
                        ("s-dependencies", "Dependencies"),
                        ("s-acceptance", "Acceptance"),
                    )
                ]
                text = render_document(metadata, "Capsule boundary fixture", sections)
                parsed = split_frontmatter(text)
                indexed = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
                validate_answer_targets(parsed.metadata, indexed)
                self.assertEqual([item.section_id for item in indexed],
                                 ["s-objective", "s-dependencies", "s-acceptance"])
                self.assertEqual(text.count("Original evidence stays intact."), 3)
                for item in indexed:
                    self.assertEqual(item.capsule, " ".join(capsule.split()))

    def test_fenced_research_evidence_keeps_trailing_answer_sections(self) -> None:
        import json
        from kairos.templates import research_document
        fact = "Exact observed evidence.\n\n\x60\x60\x60json\n" + json.dumps(
            {"rows": [{"id": index, "value": "literal evidence"} for index in range(40)]},
            indent=2,
        ) + "\n\x60\x60\x60"
        self.assertGreater(len(fact), 470)
        text = research_document(
            research_id="RESEARCH_CAPSULE_9001", task_id="TASK_9001",
            title="Fenced evidence capsule", question="Which exact evidence was observed?",
            source_uri="research/fixture.json", source_kind="workspace",
            source_fact=fact, interpretation="Interpret only the supplied evidence.",
            workspace_id="KAIROS_TEST", task_path="tasks/task_TASK_9001.md",
            goal_id="GOAL_TEST_001", milestone_id="MILESTONE_TEST_01", loop=1,
            contradictions="Unexecuted branches remain open.",
            next_query="Which contrary branch requires inspection?",
        )
        parsed = split_frontmatter(text)
        indexed = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
        validate_answer_targets(parsed.metadata, indexed)
        self.assertIn(fact, parsed.body)
        self.assertEqual({item.section_id for item in indexed}, {
            "s-question", "s-source", "s-fact", "s-interpretation",
            "s-contradictions", "s-next-query",
        })


from support import WorkspaceTestCase


class CapsulePromotionTests(WorkspaceTestCase):
    def test_native_fenced_research_promotes_and_remains_searchable(self) -> None:
        import json
        from kairos.cli import build_parser, execute
        fact = "Native observed inventory.\n\n\x60\x60\x60json\n" + json.dumps(
            {"rows": [{"id": index, "value": "unchanged source fact"} for index in range(40)]},
            indent=2,
        ) + "\n\x60\x60\x60"
        args = build_parser().parse_args([
            "new-research", "--workspace", str(self.workspace),
            "--id", "RESEARCH_CAPSULE_PROMOTION_9001", "--task", "TASK_0001",
            "--goal", "GOAL_KAIROS_001", "--milestone", "MILESTONE_KAIROS_01",
            "--title", "Native fenced research evidence",
            "--question", "Which literal source inventory did the native writer preserve?",
            "--source", "research/fixture.json", "--source-kind", "workspace",
            "--fact", fact, "--interpretation", "Only supplied evidence is established.",
            "--contradictions", "No runtime or release acceptance is implied.",
            "--next-query", "Which contrary branch remains untested?",
            "--mode", "verify",
        ])
        result, code = execute(args)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["heartbeat"]["verified"], result)
        path = self.workspace / "research/RESEARCH_CAPSULE_PROMOTION_9001.md"
        self.assertIn(fact, path.read_text(encoding="utf-8"))
        payload, code = execute(build_parser().parse_args([
            "search", "--workspace", str(self.workspace),
            "Which next query should resolve uncertainty in RESEARCH_CAPSULE_PROMOTION_9001?",
            "--limit", "5",
        ]))
        self.assertEqual(code, 0, payload)
        self.assertTrue(any(item["artifact_id"] == "RESEARCH_CAPSULE_PROMOTION_9001"
                            for item in payload["results"]), payload)


if __name__ == "__main__":
    unittest.main()
