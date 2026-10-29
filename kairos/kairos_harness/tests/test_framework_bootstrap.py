from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kairos.cli import build_parser, execute
from kairos.framework import (
    FRAMEWORK_DATABASE_NAME,
    FRAMEWORK_DOCUMENT_PATHS,
    framework_database_path,
    framework_database_content_sha256,
    framework_manifest_path,
    verify_framework_bundle,
    TOOL_CATALOG_ID,
    TOOL_CATALOG_PATH,
    WORKSHOP_MODULE_ROLES,
    WORKSHOP_TOOL_GROUPS,
    tool_catalog_document,
    verify_tool_inventory,
    CONTEXT_TOOL_CATALOG_ID, CONTEXT_TOOL_CATALOG_PATH, HARNESS_TOOL_GROUPS,
    context_tool_catalog_document, verify_context_tool_inventory,
)
from kairos.database import KnowledgeDatabase
from kairos.promoter import prepare_document
from kairos.search import search_database
from kairos.query import compile_query
from kairos.search import _score_row
from kairos.workspace import initialize_workspace, initialize_project_workspace, database_path, _seed_goal
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
        self.assertEqual(result['document_count'], len(FRAMEWORK_DOCUMENT_PATHS) + 2)
        self.assertEqual(result['source_documents_verified'], len(FRAMEWORK_DOCUMENT_PATHS))
        self.assertEqual(result['generated_source_files_verified'], len(WORKSHOP_MODULE_ROLES) + 4)
        manifest = json.loads(framework_manifest_path().read_text(encoding='utf-8'))
        self.assertEqual(
            hashlib.sha256(framework_database_path().read_bytes()).hexdigest(),
            manifest['database_sha256'],
        )

    def test_tool_router_covers_actual_public_parser_and_package(self) -> None:
        workshop_source = ROOT / 'workshop' / 'src'
        with mock.patch.object(sys, 'path', [str(workshop_source), *sys.path]):
            from runtime_sync_workshop.cli import _parser
            parser = _parser()
        import argparse
        sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        package = workshop_source / 'runtime_sync_workshop'
        modules = [path.relative_to(package).as_posix() for path in package.rglob('*.py')]
        verify_tool_inventory(list(sub.choices), modules)
        self.assertEqual(len(sub.choices), 73)
        self.assertEqual(len(modules), 42)

    def test_tool_router_refuses_unclassified_missing_or_duplicate_inventory(self) -> None:
        commands = [name for group in WORKSHOP_TOOL_GROUPS for name in group[4].split()]
        modules = list(WORKSHOP_MODULE_ROLES)
        for command_case in (commands + ['unclassified-command'], commands[1:], commands + [commands[0]]):
            with self.subTest(commands=command_case[-1]), self.assertRaisesRegex(ValueError, 'command inventory'):
                verify_tool_inventory(command_case, modules)
        for module_case in (modules + ['nested/unclassified.py'], modules[1:], modules + [modules[0]]):
            with self.subTest(modules=module_case[-1]), self.assertRaisesRegex(ValueError, 'module inventory'):
                verify_tool_inventory(commands, module_case)

    def test_context_router_exact_public_parser_inventory_and_refusals(self) -> None:
        import argparse
        parser = build_parser()
        sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        commands = list(sub.choices)
        verify_context_tool_inventory(commands)
        self.assertEqual(len(commands), 37)
        for altered in (commands[1:], commands + [commands[0]], commands + ['unclassified']):
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, 'Harness parser'):
                verify_context_tool_inventory(altered)

    def test_context_router_independent_situations_reach_framework(self) -> None:
        cases = {
            'How do I create tasks, reports and bug records?': 's-records',
            'Welche Befehle dokumentieren Aufgaben und gefundene Fehler?': 's-records',
            'How can I make a work report available in database search?': 's-publication',
            'Which command checks my backup before a restore drill?': 's-lifecycle',
        }
        for query, section in cases.items():
            with self.subTest(query=query):
                result, code = execute(build_parser().parse_args(['search', query, '--limit', '3']))
                self.assertEqual(code, 0)
                self.assertIn((CONTEXT_TOOL_CATALOG_ID, section),
                              [(row['artifact_id'], row['section_id']) for row in result['results']])
        result, _ = execute(build_parser().parse_args(['search', 'How do I create tasks, reports and bug records?']))
        self.assertEqual(result['primary']['artifact_id'], CONTEXT_TOOL_CATALOG_ID)
        self.assertEqual(result['primary']['section_id'], 's-records')

    def test_procedure_intent_is_generic_and_does_not_replace_chronology_or_authority(self) -> None:
        for query in ('Which tool can migrate a deployment?', 'How can I inspect storage usage?',
                      'Wie kann ich einen Bericht erneut veröffentlichen?'):
            with self.subTest(query=query):
                self.assertIn('procedure', compile_query(query).intents)
        for query in ('Did deployment A happen before deployment B?', 'What authority approves deletion?'):
            with self.subTest(query=query):
                self.assertNotIn('procedure', compile_query(query).intents)
        frame = compile_query('Which tool can migrate a deployment?')
        common = dict(artifact_id='DEPLOYMENT_GUIDE', section_id='s-migration', task_id=None,
                      path='docs/deployment.md', title='Deployment migration', state='active')
        router = dict(common, authority='routing', document_type='router')
        rules = dict(common, artifact_id='DEPLOYMENT_RULES', authority='operating_contract',
                     document_type='documentation')
        # Generic relevance rank0 and a declared procedure answer must not be
        # hidden by the statement-authority bonus of a weaker rank1 rules match.
        handle = {('DEPLOYMENT_GUIDE', 's-migration'): {'procedure'}}
        route_score, _ = _score_row(router, frame, 0, handle, {}, set())
        rule_score, _ = _score_row(rules, frame, 1, handle, {}, set())
        self.assertGreater(route_score, rule_score)
        # Neither document identity nor authority constants are spoofed; without
        # the relevant declared answer, the same router does not win this test.
        unbound_score, _ = _score_row(router, frame, 0, {}, {}, set())
        self.assertLess(unbound_score, rule_score)

    def test_tool_router_held_out_situations_reach_framework_sections(self) -> None:
        # These are neither declared answer strings nor builder gold queries.
        cases = {
            'Where can I check disk usage before removing old transaction copies?': 's-retention',
            'How do I safely enable automatic transaction recycling in an existing workspace?': 's-retention',
            'Which command retries publishing documentation after a successful code change?': 's-recovery',
            'Wie kann ich alte Transaktions-Arbeitskopien sicher archivieren und wiederherstellen?': 's-retention',
            'Wie kann ich den Workspace aufräumen?': 's-retention',
        }
        for query, section in cases.items():
            with self.subTest(query=query):
                result, code = execute(build_parser().parse_args(['search', query, '--limit', '3']))
                self.assertEqual(code, 0)
                self.assertIn((TOOL_CATALOG_ID, section),
                              [(item['artifact_id'], item['section_id']) for item in result['results']])

    def test_tool_router_is_promoted_in_both_fresh_initialization_paths(self) -> None:
        cases = (
            ('How can I recover a transaction whose report is missing from search without applying the code again?', 's-recovery'),
            ('Where can I check disk usage before removing old transaction copies?', 's-retention'),
            ('Wie kann ich alte Transaktions-Arbeitskopien sicher archivieren und wiederherstellen?', 's-retention'),
            ('Wie kann ich den Workspace aufräumen?', 's-retention'),
        )
        for project in (False, True):
            with self.subTest(project=project), tempfile.TemporaryDirectory(prefix='unrelated name ') as temp:
                workspace = Path(temp) / 'outside-source' / 'context'
                if project:
                    goal = _seed_goal()
                    milestone = goal['milestones'][0]
                    initialize_project_workspace(
                        workspace, workspace_id='X' * 128, goal=goal, milestone=milestone,
                        task={'id': 'TASK_ROUTER_FIXTURE', 'title': 'Synthetic routing fixture',
                              'objective': 'Verify initial tool discovery without a real project intake.'},
                        selected_criteria=milestone['criteria'],
                    )
                else:
                    initialize_workspace(workspace, workspace_id='ROUTER_STARTER')
                path = workspace / TOOL_CATALOG_PATH
                prepared = prepare_document(path, workspace)
                self.assertEqual(prepared.frontmatter.metadata['id'], TOOL_CATALOG_ID)
                text = path.read_text(encoding='utf-8')
                self.assertNotIn(str(ROOT), text)
                self.assertIn('Derived release routing only', text)
                self.assertIn('vault garbage collection is not implemented', text)
                context_path = workspace / CONTEXT_TOOL_CATALOG_PATH
                context = prepare_document(context_path, workspace)
                self.assertEqual(context.frontmatter.metadata['id'], CONTEXT_TOOL_CATALOG_ID)
                context_text = context_path.read_text(encoding='utf-8')
                self.assertNotIn(str(ROOT), context_text)
                self.assertIn('new-code creates implementation documentation, not executable source', context_text)
                architecture = (workspace / 'docs/KAIROS_STARTER_ARCHITECTURE.md').read_text(encoding='utf-8')
                self.assertIn(TOOL_CATALOG_PATH + '#s-entry', architecture)
                database = KnowledgeDatabase(database_path(workspace))
                for query, expected in (
                    ('How do I create tasks, reports and bug records?', 's-records'),
                    ('Welche Befehle dokumentieren Aufgaben und gefundene Fehler?', 's-records'),
                    ('How can I make a work report available in database search?', 's-publication'),
                ):
                    result = search_database(database, query, limit=3)
                    self.assertIn((CONTEXT_TOOL_CATALOG_ID, expected),
                                  [(row['artifact_id'], row['section_id']) for row in result['results']])
                for query, section in cases:
                    result = search_database(database, query, limit=3)
                    self.assertIn((TOOL_CATALOG_ID, section),
                                  [(item['artifact_id'], item['section_id']) for item in result['results']])

    def test_tool_router_rejects_forged_generated_binding(self) -> None:
        original = json.loads(framework_manifest_path().read_text(encoding='utf-8'))
        for index in (0, 1):
            for field, value in (('sha256', '0' * 64), ('artifact_id', 'FORGED_ROUTER'),
                                 ('source_bindings', []), ('authority', 'operating_contract')):
                forged = json.loads(json.dumps(original))
                forged['generated_documents'][index][field] = value
                with self.subTest(index=index, field=field), mock.patch('kairos.framework.load_framework_manifest', return_value=forged):
                    with self.assertRaisesRegex(RuntimeError, 'router'):
                        verify_framework_bundle()
        for rows in (original['generated_documents'][:1], list(reversed(original['generated_documents']))):
            forged = dict(original, generated_documents=rows)
            with self.subTest(rows=rows), mock.patch('kairos.framework.load_framework_manifest', return_value=forged):
                with self.assertRaisesRegex(RuntimeError, 'router'):
                    verify_framework_bundle()

    def test_installed_layout_routes_without_source_tree_or_workshop_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix='installed-router-') as temp:
            root = Path(temp)
            shutil.copytree(ROOT / 'kairos/kairos_harness/kairos', root / 'site/kairos',
                            ignore=shutil.ignore_patterns('__pycache__'))
            env = dict(os.environ, PYTHONPATH=str(root / 'site'), PYTHONDONTWRITEBYTECODE='1')
            completed = subprocess.run(
                [sys.executable, '-c',
                 'import json; from kairos.framework import search_framework; '
                 'r=search_framework("Where can I check disk usage before removing old transaction copies?",limit=3); '
                 'c=search_framework("How do I create tasks, reports and bug records?",limit=3); '
                 'print(json.dumps({"ids":[x["artifact_id"] for x in r["results"]],"proof":r["framework"],"context":c["primary"]["artifact_id"]}))'],
                cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            result = json.loads(completed.stdout)
            self.assertIn(TOOL_CATALOG_ID, result['ids'])
            self.assertEqual(result['context'], CONTEXT_TOOL_CATALOG_ID)
            self.assertEqual(result['proof']['source_documents_verified'], 0)
            self.assertEqual(result['proof']['generated_source_files_verified'], 4)

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
