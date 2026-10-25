from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from kairos.frontmatter import split_frontmatter
from kairos.sections import parse_sections, validate_answer_targets


class EnglishContractTests(unittest.TestCase):
    def test_source_documents_and_templates_are_english_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        roots = [root / "kairos", root / "docs", root / "schemas", root / "templates", root / "scripts"]
        non_english_codepoints = {0x00E4, 0x00F6, 0x00FC, 0x00C4, 0x00D6, 0x00DC, 0x00DF}
        legacy_active = "".join(chr(value) for value in (78, 69, 85))
        legacy_closed = "".join(chr(value) for value in (65, 108, 116)) + ".md"
        legacy_markers = {
            legacy_active + ".md",
            "KAIROS_" + legacy_active,
            legacy_closed,
            "KAIROS_" + legacy_closed.removesuffix(".md").upper(),
        }
        violations: list[str] = []
        for base in roots:
            for path in base.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix.casefold() not in {".py", ".md", ".json", ".toml", ".ps1"}:
                    continue
                text = path.read_text(encoding="utf-8")
                bad_character = next((char for char in text if ord(char) in non_english_codepoints), None)
                bad_marker = next(
                    (
                        marker
                        for marker in legacy_markers
                        if re.search(re.escape(marker) + r"(?![A-Z0-9_])", text)
                    ),
                    None,
                )
                if bad_character or bad_marker:
                    violations.append(f"{path.relative_to(root)}: English-only contract violation")
        self.assertEqual(violations, [])

    def test_json_schemas_are_valid_json(self) -> None:
        root = Path(__file__).resolve().parents[1] / "schemas"
        for path in root.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_static_document_templates_obey_the_context_grammar(self) -> None:
        root = Path(__file__).resolve().parents[1] / "templates"
        for path in root.glob("*.md"):
            parsed = split_frontmatter(path.read_text(encoding="utf-8"))
            sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes)
            validate_answer_targets(parsed.metadata, sections)


if __name__ == "__main__":
    unittest.main()
