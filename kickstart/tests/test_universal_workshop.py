from __future__ import annotations

import hashlib
import json
import os
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from kickstart.universal_init import initialize_universal_markdown
from kickstart.binding import initialize_project
from kickstart.errors import KickstartError


ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = ROOT / "workshop" / "src"
if str(WORKSHOP_SRC) not in sys.path:
    sys.path.insert(0, str(WORKSHOP_SRC))

from runtime_sync_workshop.corpus import build_corpus_manifest  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from workshop.tests.metadata_test_support import complete_metadata_audit  # noqa: E402


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_UW",
            "title": "Universal Workshop",
            "objective": "Govern source and Markdown as one verified transition.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_UW_01",
                "title": "Universal transaction",
                "objective": "Complete a verified universal Workshop mutation.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_UW_001",
                    "description": "Source and Markdown remain bit-exact after Workshop apply.",
                    "state": "active",
                    "evidence_required": "Workshop postcheck receipt.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_UW_001",
            "title": "Verify universal Workshop",
            "objective": "Run one source mutation through the sealed Workshop path.",
            "milestone": "MILESTONE_UW_01",
            "criteria": ["CRIT_UW_001"],
        },
    }


class InitialAuxiliaryAuthorityTests(unittest.TestCase):
    def _project(self, root, compiler):
        project = root / 'project'
        project.mkdir()
        source = project / ('unit.cpp' if compiler else 'app.py')
        source.write_bytes(b'int value = 1;\n' if compiler else b'value = 1\n')
        arguments = {'project_root': project, 'workspace': root / 'workspace',
                     'project_spec': _project_spec()}
        if compiler:
            commands = root / 'compile_commands.json'
            commands.write_bytes(json.dumps([{'directory': str(project),
                'file': str(source), 'arguments': ['c++', '-c', str(source)]}]).encode())
            arguments['compile_commands'] = commands
        return source, arguments, initialize_project if compiler else initialize_universal_markdown

    def _roundtrip(self, compiler):
        from runtime_sync_workshop.auxiliary import AuxiliaryDocumentTransaction
        from runtime_sync_workshop.tx_documents import assert_projected
        with tempfile.TemporaryDirectory(prefix='initial-auxiliary-') as temporary:
            root = Path(temporary)
            source, arguments, initialize = self._project(root, compiler)
            original_source = source.read_bytes()
            document = root / 'PROCESS_AUTHORITY.md'
            original = b'# Process authority\r\n\r\nExact initial authority.\r\n'
            document.write_bytes(original)
            result = initialize(**arguments, auxiliary_documents=(document,))
            from kairos.database import KnowledgeDatabase
            from kairos.search import search_database
            database = KnowledgeDatabase(arguments['workspace'] / '.kairos/kairos.db')
            question = "Which Auxiliary process documents were declared during this project's initial intake?"
            found = search_database(database, question, record_trace=False)
            self.assertEqual(found['primary']['artifact_id'], 'PROJECT_AUXILIARY_DECLARATION')
            self.assertEqual(found['primary']['section_id'], 's-initial-auxiliary-authority')
            guide = arguments['workspace'] / 'docs/PROJECT_AUXILIARY_DECLARATION.md'
            guide_before = guide.read_bytes()
            self.assertIn(hashlib.sha256(original).hexdigest().encode(), guide_before)
            config_path = (Path(result['manifest']).parent / 'workshop.config.json'
                           if compiler else Path(result['workshop_config']))
            engine = WorkshopEngine(config_path)
            self.assertEqual(engine.config.auxiliary_documents, (document.name,))
            self.assertIn(document.name, engine.config.blueprint_ignore)
            self.assertEqual((engine.config.blueprint_root / document.name).read_bytes(), original)
            native_manifest = build_corpus_manifest(engine.config)
            self.assertTrue(native_manifest['verified'], native_manifest['issues'])
            self.assertEqual(native_manifest['counts']['auxiliary_documents'], 1)
            self.assertEqual(len(native_manifest['records']), 1)
            engine.seal()
            owner = AuxiliaryDocumentTransaction(WorkshopEngine(engine.config.config_path))
            checkout = owner.checkout([document.name], purpose='Exercise genuinely declared initial Auxiliary authority and native projection.')
            tx = checkout['transaction_id']
            assert_projected(owner.engine, tx)
            (Path(checkout['work_directory']) / document.name).write_bytes(original + b'\r\nReviewed process update.\r\n')
            owner.verify(tx)
            assert_projected(owner.engine, tx)
            applied = owner.apply(tx)
            self.assertTrue(applied['bit_exact'])
            self.assertEqual(applied['state'], 'POSTCHECK_VERIFIED')
            assert_projected(owner.engine, tx)
            self.assertEqual(document.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original_source)
            self.assertEqual(guide.read_bytes(), guide_before)
            self.assertNotIn(hashlib.sha256(original + b'\r\nReviewed process update.\r\n').hexdigest().encode(), guide.read_bytes())
            # The same loaded binding cannot be repurposed by another init mode.
            for initializer in (initialize_project, initialize_universal_markdown):
                with self.assertRaises(KickstartError) as failure:
                    initializer(**arguments, auxiliary_documents=(document,))
                self.assertEqual(failure.exception.code, 'PROJECT_ALREADY_BOUND')

    def test_universal_auxiliary_declaration_loads_from_disk_and_applies_natively(self):
        self._roundtrip(False)

    def test_compiler_auxiliary_declaration_loads_from_disk_and_applies_natively(self):
        self._roundtrip(True)

    def test_invalid_auxiliary_inputs_refuse_before_workspace_creation(self):
        cases = [('missing', 'MISSING.md', 'AUXILIARY_DOCUMENT_MISSING'),
                 ('directory', 'DIRECTORY.md', 'AUXILIARY_DOCUMENT_MISSING'),
                 ('code', 'unreviewed.py', 'AUXILIARY_DOCUMENT_NAME_INVALID'),
                 ('generated', 'PROJECT_CODE_0123456789AB.md', 'AUXILIARY_DOCUMENT_NAME_INVALID'),
                 ('device', 'CON.md', 'AUXILIARY_DOCUMENT_NAME_INVALID'),
                 ('encoding', 'BINARY.md', 'AUXILIARY_DOCUMENT_INVALID')]
        for compiler in (False, True):
            for kind, name, code in cases:
                with self.subTest(compiler=compiler, kind=kind):
                    with tempfile.TemporaryDirectory(prefix='invalid-initial-auxiliary-') as temporary:
                        root = Path(temporary)
                        source, arguments, initialize = self._project(root, compiler)
                        before = source.read_bytes()
                        document = root / name
                        if kind == 'directory':
                            document.mkdir()
                        elif kind not in ('missing', 'device'):
                            document.write_bytes(b'\xff' if kind == 'encoding' else b'# Exact input\n')
                        with self.assertRaises(KickstartError) as failure:
                            initialize(**arguments, auxiliary_documents=(document,))
                        self.assertEqual(failure.exception.code, code)
                        self.assertFalse(arguments['workspace'].exists())
                        self.assertEqual(source.read_bytes(), before)

    def test_duplicate_portable_names_refuse_without_workspace(self):
        for compiler in (False, True):
            with self.subTest(compiler=compiler):
                with tempfile.TemporaryDirectory(prefix='duplicate-initial-auxiliary-') as temporary:
                    root = Path(temporary)
                    _, arguments, initialize = self._project(root, compiler)
                    first = root / 'first'; first.mkdir()
                    second = root / 'second'; second.mkdir()
                    documents = (first / 'PROCESS.md', second / 'process.MD')
                    for document in documents:
                        document.write_bytes(b'# Exact process input\n')
                    with self.assertRaises(KickstartError) as failure:
                        initialize(**arguments, auxiliary_documents=documents)
                    self.assertEqual(failure.exception.code, 'AUXILIARY_DOCUMENT_DUPLICATE')
                    self.assertFalse(arguments['workspace'].exists())

    def test_changing_auxiliary_input_is_refused_before_workspace_creation(self):
        original_read = Path.read_bytes
        for compiler in (False, True):
            with self.subTest(compiler=compiler):
                with tempfile.TemporaryDirectory(prefix='changing-initial-auxiliary-') as temporary:
                    root = Path(temporary)
                    _, arguments, initialize = self._project(root, compiler)
                    document = root / 'PROCESS.md'; document.write_bytes(b'# Before\n')
                    reads = []
                    def change_after_first_read(path):
                        data = original_read(path)
                        if path == document:
                            reads.append(data)
                            if len(reads) == 1:
                                document.write_bytes(b'# Changed by test actor\n')
                        return data
                    with patch.object(Path, 'read_bytes', change_after_first_read):
                        with self.assertRaises(KickstartError) as failure:
                            initialize(**arguments, auxiliary_documents=(document,))
                    self.assertEqual(failure.exception.code, 'AUXILIARY_DOCUMENT_CHANGED')
                    self.assertEqual(len(reads), 2)
                    self.assertFalse(arguments['workspace'].exists())
                    self.assertEqual(document.read_bytes(), b'# Changed by test actor\n')

    def test_intent_precedes_auxiliary_input_discovery(self):
        for compiler in (False, True):
            with self.subTest(compiler=compiler):
                with tempfile.TemporaryDirectory(prefix='intent-before-auxiliary-') as temporary:
                    root = Path(temporary)
                    _, arguments, initialize = self._project(root, compiler)
                    arguments['project_spec'] = None
                    with patch('kickstart.binding._read_auxiliary_documents', side_effect=AssertionError('input read before intent')):
                        with self.assertRaises(KickstartError) as failure:
                            initialize(**arguments, auxiliary_documents=(root / 'MISSING.md',))
                    self.assertEqual(failure.exception.code, 'PROJECT_INTENT_REQUIRED')
                    self.assertFalse(arguments['workspace'].exists())

    def test_public_init_cli_declares_multiple_auxiliary_inputs_in_both_modes(self):
        for compiler in (False, True):
            with self.subTest(compiler=compiler):
                with tempfile.TemporaryDirectory(prefix='initial auxiliary cli ') as temporary:
                    root = Path(temporary)
                    source, arguments, _ = self._project(root, compiler)
                    documents = (root / 'PROCESS.md', root / 'OPERATING_RULES.md')
                    for document in documents:
                        document.write_bytes(('# ' + document.stem + '\r\n').encode())
                    command = [sys.executable, '-m', 'kickstart', 'init',
                        '--project-root', str(arguments['project_root']),
                        '--workspace', str(arguments['workspace']),
                        '--spec-json', json.dumps(_project_spec())]
                    if compiler:
                        command.extend(['--compile-commands', str(arguments['compile_commands'])])
                    else:
                        command.append('--auto')
                    for document in documents:
                        command.extend(['--auxiliary-document', str(document)])
                    before = {path: path.read_bytes() for path in (*documents, source)}
                    completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                               text=True, encoding='utf-8')
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertTrue(result['verified'])
                    engine = WorkshopEngine(arguments['workspace'] / '.kairos/workshop.config.json')
                    self.assertEqual(engine.config.auxiliary_documents, tuple(sorted(p.name for p in documents)))
                    self.assertTrue(build_corpus_manifest(engine.config)['verified'])
                    for document in documents:
                        self.assertEqual((engine.config.blueprint_root / document.name).read_bytes(), before[document])
                    self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_linked_auxiliary_input_refuses_before_workspace_creation(self):
        for compiler in (False, True):
            with self.subTest(compiler=compiler):
                with tempfile.TemporaryDirectory(prefix='linked-initial-auxiliary-') as temporary:
                    root = Path(temporary)
                    _, arguments, initialize = self._project(root, compiler)
                    target = root / 'target'; target.mkdir()
                    document = target / 'PROCESS.md'; document.write_bytes(b'# Exact process input\n')
                    link = root / 'linked'
                    if os.name == 'nt':
                        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                                 capture_output=True)
                        if created.returncode:
                            self.skipTest('host does not permit isolated directory junction creation')
                    else:
                        try:
                            link.symlink_to(target, target_is_directory=True)
                        except OSError:
                            self.skipTest('host does not permit isolated directory link creation')
                    try:
                        with self.assertRaises(KickstartError) as failure:
                            initialize(**arguments, auxiliary_documents=(link / document.name,))
                        self.assertRegex(str(failure.exception).lower(), 'link|reparse')
                        self.assertFalse(arguments['workspace'].exists())
                        self.assertEqual(document.read_bytes(), b'# Exact process input\n')
                    finally:
                        if link.is_symlink():
                            link.unlink()
                        else:
                            link.rmdir()

    def test_declared_filename_is_literal_data_in_searchable_guidance(self):
        from kairos.database import KnowledgeDatabase
        from kairos.search import search_database
        with tempfile.TemporaryDirectory(prefix='literal-initial-auxiliary-') as temporary:
            root = Path(temporary)
            _, arguments, initialize = self._project(root, False)
            document = root / '[metadata](MISSING.md).md'
            document.write_bytes(b'# Exact process input\n')
            result = initialize(**arguments, auxiliary_documents=(document,))
            self.assertTrue(result['verified'])
            engine = WorkshopEngine(Path(result['workshop_config']))
            self.assertEqual(engine.config.auxiliary_documents, (document.name,))
            found = search_database(KnowledgeDatabase(engine.config.kairos_database),
                "Which Auxiliary process documents were declared during this project's initial intake?", record_trace=False)
            self.assertEqual(found['primary']['artifact_id'], 'PROJECT_AUXILIARY_DECLARATION')
            self.assertTrue(build_corpus_manifest(engine.config)['verified'])


class BindingReinitializationTests(unittest.TestCase):
    _project = InitialAuxiliaryAuthorityTests._project

    def _snapshot(self, root):
        # Compare the entire fixture, not just the source or selected authority.
        # Reads may alter access times; modification times must stay unchanged.
        return {
            path.relative_to(root).as_posix(): (
                path.is_dir(), path.stat().st_mtime_ns,
                None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in (root, *sorted(root.rglob('*')))
        }

    def _refuse(self, arguments, compiler, documents, cli):
        if cli:
            command = [sys.executable, '-m', 'kickstart', 'init',
                '--project-root', str(arguments['project_root']),
                '--workspace', str(arguments['workspace']),
                '--spec-json', json.dumps(_project_spec())]
            if compiler:
                command.extend(['--compile-commands', str(arguments['compile_commands'])])
            else:
                command.append('--auto')
            for document in documents:
                command.extend(['--auxiliary-document', str(document)])
            completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                       text=True, encoding='utf-8')
            self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn('PROJECT_ALREADY_BOUND', completed.stdout + completed.stderr)
        else:
            initialize = initialize_project if compiler else initialize_universal_markdown
            with self.assertRaises(KickstartError) as failure:
                initialize(**arguments, auxiliary_documents=documents)
            self.assertEqual(failure.exception.code, 'PROJECT_ALREADY_BOUND')

    def _bound_matrix(self, cli):
        for first_compiler in (False, True):
            for sealed in (False, True):
                with tempfile.TemporaryDirectory(prefix='bound-intake-no-write-') as temporary:
                    root = Path(temporary)
                    # Both entry points observe the same compiler-authoritative fixture.
                    _, arguments, _ = self._project(root, True)
                    initialize = initialize_project if first_compiler else initialize_universal_markdown
                    initialize(**arguments)
                    if sealed:
                        WorkshopEngine(arguments['workspace'] / '.kairos/workshop.config.json').seal()
                    document = root / 'PROCESS.md'
                    document.write_bytes(b'# Existing process input\n')
                    for second_compiler in (False, True):
                        for documents in ((), (document,)):
                            with self.subTest(first_compiler=first_compiler, sealed=sealed,
                                              second_compiler=second_compiler, auxiliary=bool(documents), cli=cli):
                                before = self._snapshot(root)
                                self._refuse(arguments, second_compiler, documents, cli)
                                self.assertEqual(self._snapshot(root), before)

    def test_registered_binding_api_matrix_refuses_without_any_fixture_mutation(self):
        self._bound_matrix(False)

    def test_registered_binding_actual_cli_matrix_refuses_without_any_fixture_mutation(self):
        self._bound_matrix(True)

    def test_each_partial_registration_refuses_before_input_read_or_workspace_initialization(self):
        for marker in ('workshop.config.json', 'project-intake.json', 'universal-intake.json'):
            with tempfile.TemporaryDirectory(prefix='partial-intake-no-write-') as temporary:
                root = Path(temporary)
                _, arguments, _ = self._project(root, True)
                authority = arguments['workspace'] / '.kairos' / marker
                authority.parent.mkdir(parents=True)
                authority.write_bytes(b'Incomplete registration must not be parsed or repaired.\n')
                for compiler in (False, True):
                    for cli in (False, True):
                        with self.subTest(marker=marker, compiler=compiler, cli=cli):
                            before = self._snapshot(root)
                            # A missing explicit input proves binding refusal precedes input reads.
                            self._refuse(arguments, compiler, (root / 'MISSING.md',), cli)
                            self.assertEqual(self._snapshot(root), before)

    def test_legitimate_unbound_workspace_still_initializes_in_both_modes(self):
        from kairos.workspace import initialize_project_workspace
        from kairos.loops import validate_project_kickoff_contract
        for compiler in (False, True):
            for declared in (False, True):
                with self.subTest(compiler=compiler, declared=declared):
                    with tempfile.TemporaryDirectory(prefix='unbound-first-intake-') as temporary:
                        root = Path(temporary)
                        source, arguments, initialize = self._project(root, True)
                        goal, milestone, task, criteria, _ = validate_project_kickoff_contract(_project_spec())
                        initialize_project_workspace(arguments['workspace'], workspace_id='KAIROS_UNBOUND_TEST',
                            goal=goal, milestone=milestone, task=task, selected_criteria=criteria)
                        document = root / 'PROCESS.md'
                        document.write_bytes(b'# Initial process input\n')
                        before = source.read_bytes(), document.read_bytes()
                        initialize = initialize_project if compiler else initialize_universal_markdown
                        result = initialize(**arguments, auxiliary_documents=(document,) if declared else ())
                        self.assertTrue(result['verified'])
                        self.assertEqual((source.read_bytes(), document.read_bytes()), before)
                        engine = WorkshopEngine(arguments['workspace'] / '.kairos/workshop.config.json')
                        self.assertEqual(engine.config.auxiliary_documents, (document.name,) if declared else ())
                        self.assertTrue(build_corpus_manifest(engine.config)['verified'])


class UniversalWorkshopRoundtripTests(unittest.TestCase):
    def _roundtrip(
        self,
        *,
        project: Path,
        workspace: Path,
        source_relative: str,
        changed_text: str,
        compile_commands: Path | None = None,
    ) -> dict:
        result = initialize_universal_markdown(
            project_root=project,
            workspace=workspace,
            compile_commands=compile_commands,
            project_spec=_project_spec(),
        )
        self.assertEqual(result["state"], "VERIFIED_PENDING_SEAL")
        self.assertTrue(result["corpus"]["verified"])

        engine = WorkshopEngine(Path(result["workshop_config"]))
        from runtime_sync_workshop.tx_documents import assert_projected
        self.assertIs(engine.config.raw["transaction_documents_required"], True)
        seal = engine.seal()
        self.assertIn("package_sha256", seal)
        checkout = engine.checkout(
            [source_relative],
            purpose=f"Update {source_relative} through universal Workshop verification",
        )
        transaction_id = checkout["transaction_id"]
        assert_projected(engine, transaction_id)
        work_source = Path(checkout["work_directory"]) / source_relative
        # Keep the test input byte-exact on Windows.  Path.write_text's default
        # newline translation would otherwise turn the requested LF payload
        # into CRLF before Workshop can prove its exact copy semantics.
        work_source.write_bytes(changed_text.encode("utf-8"))

        review_path = Path(checkout["metadata_review"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        selected = False
        for entry in review["entries"]:
            if entry["source"] == source_relative:
                entry.update({
                    "metadata_impact": "none",
                    "reason": "Mechanical exact-mirror update; no semantic metadata claim exists.",
                    "reviewer": "KAIROS_TEST",
                    "reviewed_at": "2026-09-09T00:00:00Z",
                })
                selected = True
        self.assertTrue(selected)
        review_path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        complete_metadata_audit(engine, checkout)
        prepared = engine.prepare(transaction_id)
        self.assertEqual(prepared["state"], "PREPARED")
        assert_projected(engine, transaction_id)
        engine.metadata_check(transaction_id)
        verified = engine.verify(transaction_id)
        self.assertEqual(verified["state"], "SHADOW_VERIFIED")
        assert_projected(engine, transaction_id)
        receipt = engine.apply(transaction_id)
        self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
        assert_projected(engine, transaction_id)
        self.assertTrue(receipt["bit_exact"])

        live_raw = (project / source_relative).read_bytes()
        self.assertEqual(live_raw, changed_text.encode("utf-8"))
        manifest = build_corpus_manifest(engine.config)
        self.assertTrue(manifest["verified"], manifest["issues"])
        record = manifest["records"][source_relative]
        self.assertEqual(record["source"]["sha256"], hashlib.sha256(live_raw).hexdigest())
        blueprint = workspace / "code" / record["blueprint"]["filename"]
        self.assertTrue(blueprint.is_file())
        self.assertIn(hashlib.sha256(live_raw).hexdigest().upper(), blueprint.read_text(encoding="utf-8"))
        return result

    def test_python_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("value = 1\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="app.py",
                changed_text="value = 2\nprint(value)\n",
            )

    def test_typescript_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="app.ts",
                changed_text="export const value = 2;\nexport const next = value + 1;\n",
            )

    def test_rust_roundtrip_reaches_postcheck_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "lib.rs").write_text("pub fn value() -> i32 { 1 }\n", encoding="utf-8")
            self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="src/lib.rs",
                changed_text="pub fn value() -> i32 { 2 }\npub fn next() -> i32 { value() + 1 }\n",
            )

    def test_mixed_cpp_python_keeps_compiler_header_authority_and_edits_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            cpp = project / "src" / "main.cpp"
            header = project / "src" / "api.hpp"
            python = project / "src" / "helper.py"
            cpp.write_text('#include "api.hpp"\nint main() { return api(); }\n', encoding="utf-8")
            header.write_text("#pragma once\ninline int api() { return 0; }\n", encoding="utf-8")
            python.write_text("def helper():\n    return 1\n", encoding="utf-8")
            compile_commands = project / "compile_commands.json"
            compile_commands.write_text(json.dumps([{
                "directory": str(project),
                "file": str(cpp),
                "arguments": ["c++", "-I", str(project / "src"), "-c", str(cpp)],
            }]), encoding="utf-8")

            result = self._roundtrip(
                project=project,
                workspace=root / "workspace",
                source_relative="src/helper.py",
                changed_text="def helper():\n    return 2\n",
                compile_commands=compile_commands,
            )
            self.assertEqual(result["headers"], 1)
            self.assertEqual(set(result["ecosystems"]), {"c_family", "python"})
            header_docs = list((root / "workspace" / "code").glob("PROJECT_HEADER_*.md"))
            self.assertEqual(len(header_docs), 1)
            self.assertIn("src/api.hpp", header_docs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
