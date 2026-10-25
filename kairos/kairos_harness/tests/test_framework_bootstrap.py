from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kairos.cli import build_parser, execute
from kairos.framework import (
    FRAMEWORK_DATABASE_NAME,
    FRAMEWORK_DOCUMENT_PATHS,
    framework_database_path,
    framework_database_content_sha256,
    framework_manifest_path,
    verify_framework_bundle,
)
from kairos.references import iter_references
from kairos.sections import parse_sections


ROOT = Path(__file__).resolve().parents[3]


class FrameworkBootstrapTests(unittest.TestCase):
    def test_literal_markdown_examples_are_not_live_structure(self) -> None:
        text = '''
```text
[ref:missing.md|v:1|rel:NOT_A_PREDICATE|tags:x|src:example]
<a id="s-fake"></a>
## Fake heading
```
<a id="s-real"></a>
## Real heading

> Capsule: Real section.
'''
        self.assertEqual(list(iter_references(text)), [])
        sections = parse_sections(
            '# Doc\n\n## CONTEXT INDEX\n\n- s-real\n' + text,
            header_bytes=100,
        )
        self.assertEqual([section.section_id for section in sections], ['s-real'])

    def test_framework_bundle_is_hash_verified_and_source_bound(self) -> None:
        result = verify_framework_bundle()
        self.assertTrue(result['verified'])
        self.assertEqual(result['document_count'], len(FRAMEWORK_DOCUMENT_PATHS))
        self.assertEqual(result['source_documents_verified'], len(FRAMEWORK_DOCUMENT_PATHS))
        manifest = json.loads(framework_manifest_path().read_text(encoding='utf-8'))
        self.assertEqual(
            hashlib.sha256(framework_database_path().read_bytes()).hexdigest(),
            manifest['database_sha256'],
        )

    def test_search_without_workspace_uses_framework_corpus_and_writes_no_project_state(self) -> None:
        before_db = hashlib.sha256(framework_database_path().read_bytes()).hexdigest()
        args = build_parser().parse_args(['search', 'What owns translation-unit membership?', '--limit', '3'])
        payload, code = execute(args)
        after_db = hashlib.sha256(framework_database_path().read_bytes()).hexdigest()
        self.assertEqual(code, 0)
        self.assertEqual(payload['corpus'], 'framework')
        self.assertEqual(payload['primary']['artifact_id'], 'KAIROS_ARCHITECTURE')
        self.assertEqual(payload['primary']['section_id'], 's-overview')
        self.assertEqual(before_db, after_db)

    def test_framework_gold_queries_answer_bootstrap_rules_directly(self) -> None:
        cases = {
            'How should WERKFADEN perform evidence-based AI review?': ('WERKFADEN_EVIDENCE_BASED_AI_REVIEW', 's-method'),
            'How are proposition support and evidence sufficiency kept separate?': ('WERKFADEN_EVIDENCE_BASED_AI_REVIEW', 's-two-lanes'),
            'How do I query KAIROS rules before a project workspace exists?': ('KAIROS_FRAMEWORK_BOOTSTRAP', 's-before-project-intake'),
            'How do I start a real project with KAIROS?': ('KAIROS_README', 's-start-a-real-project'),
            'When is authority migration required?': ('KAIROS_WORKSHOP', 's-authority-migration'),
            'What does a successful KAIROS Workshop postcheck not prove?': ('KAIROS_KNOWN_LIMITATIONS', 's-verification-boundary'),
            'How should an LLM acquire context in KAIROS?': ('KAIROS_LLM_OPERATING_CONTRACT', 's-context-policy'),
            'How do I onboard an arbitrary existing codebase into KAIROS?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-cold-onboarding'),
            'After KAIROS seals a project, what is allowed to modify source code or Markdown?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-single-mutation-boundary'),
            'How do I create delete or rename governed source files after seal?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-source-set-transactions'),
            'How must I check metadata before applying a source patch?': ('KAIROS_WORKSHOP', 's-metadata-aftercheck'),
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                payload, code = execute(build_parser().parse_args(['search', query, '--limit', '3']))
                self.assertEqual(code, 0)
                self.assertEqual((payload['primary']['artifact_id'], payload['primary']['section_id']), expected)


    def test_zero_install_source_wrapper_serves_framework_search(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / 'kairos_cli.py'), 'search', 'When is authority migration required?', '--limit', '2'],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['corpus'], 'framework')
        self.assertEqual(payload['primary']['artifact_id'], 'KAIROS_WORKSHOP')
        self.assertEqual(payload['primary']['section_id'], 's-authority-migration')
        self.assertEqual(payload['framework']['source_documents_verified'], len(FRAMEWORK_DOCUMENT_PATHS))

    def test_framework_index_rebuild_is_content_equivalent_across_sqlite_builds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            output = temp / FRAMEWORK_DATABASE_NAME
            manifest = temp / 'framework_manifest.json'
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'build_framework_index.py'),
                    '--output',
                    str(output),
                    '--manifest',
                    str(manifest),
                    '--verify-determinism',
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(
                framework_database_content_sha256(output),
                framework_database_content_sha256(framework_database_path()),
            )
            generated_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                generated_manifest["database_content_sha256"],
                framework_database_content_sha256(output),
            )


if __name__ == '__main__':
    unittest.main()
