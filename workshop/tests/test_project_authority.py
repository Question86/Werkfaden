from __future__ import annotations

import shutil
import json
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from runtime_sync_workshop.corpus import cmake_membership, header_closure
from runtime_sync_workshop.util import set_source_prefix


class RuntimePolicyCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='policy-candidate-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'workshop.config.json'

    def fixture(self, values=None, *, intake=False):
        from runtime_sync_workshop.util import package_hash
        raw = dict(schema='runtime-sync-workshop-config/v1', codebase_root='../chosen project',
                   guardian_required=False, jev_apply_required=True, tx_recycle_trigger=9,
                   private_adapter={'secret': 'never emit this payload', 'paths': ['x', 'y']})
        raw.update(values or {})
        self.path.write_text(json.dumps(raw), encoding='utf-8')
        members = [self.path.name]
        if intake:
            data = dict(schema='kairos-project-intake/v1', workshop_config_sha256=package_hash(raw),
                        preserved={'compiler': ['exact', 'unmodified'], 'sources': ['main.cpp']})
            (self.root / 'project-intake.json').write_text(json.dumps(data), encoding='utf-8')
            members.append('project-intake.json')
        return SimpleNamespace(config_path=self.path, machine_root=self.root, raw=raw,
                               machine_authority_files=tuple(members))

    def test_monotone_targets_preserve_every_unrelated_value_and_write_nothing(self):
        from runtime_sync_workshop.corpus import runtime_policy_candidate, verify_runtime_policy_candidate, RUNTIME_POLICY_PROFILE
        from runtime_sync_workshop.util import sha256_bytes
        for flags in ({}, {'transaction_documents_required': False, 'tx_retention_required': False},
                      {'transaction_documents_required': True, 'tx_retention_required': False}):
            with self.subTest(flags=flags):
                config = self.fixture(flags); before = self.path.read_bytes()
                result = runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
                target = json.loads(result['candidate_files'][self.path.name])
                self.assertTrue(target.pop('transaction_documents_required'))
                self.assertTrue(target.pop('tx_retention_required'))
                old = {k: v for k, v in config.raw.items() if k not in ('transaction_documents_required', 'tx_retention_required')}
                self.assertEqual(target, old)
                proof = verify_runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE, result['candidate_files'],
                        expected_source_hashes={k: sha256_bytes(v) for k, v in result['source_files'].items()})
                self.assertTrue(proof['verified']); self.assertFalse(proof['mutation_authorized'])
                self.assertNotIn('never emit this payload', json.dumps(proof))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_intake_changes_only_its_existing_config_digest(self):
        from runtime_sync_workshop.corpus import runtime_policy_candidate, RUNTIME_POLICY_PROFILE
        from runtime_sync_workshop.util import package_hash
        config = self.fixture(intake=True)
        before = (self.root / 'project-intake.json').read_bytes()
        old = json.loads(before)
        result = runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
        target = json.loads(result['candidate_files'][self.path.name])
        updated = json.loads(result['candidate_files']['project-intake.json'])
        self.assertEqual(updated.pop('workshop_config_sha256'), package_hash(target))
        old.pop('workshop_config_sha256')
        self.assertEqual(updated, old)
        self.assertEqual((self.root / 'project-intake.json').read_bytes(), before)

    def test_refuses_noop_incompatible_invalid_or_ambiguous_sources(self):
        from runtime_sync_workshop.corpus import runtime_policy_candidate, RUNTIME_POLICY_PROFILE
        from runtime_sync_workshop.util import WorkshopError
        cases = [({'transaction_documents_required': True, 'tx_retention_required': True}, 'POLICY_ALREADY_ACTIVE'),
                 ({'tx_retention_required': True}, 'POLICY_BASELINE_INVALID')]
        cases += [({'transaction_documents_required': value}, 'POLICY_BASELINE_INVALID') for value in (None, 0, 1, 'true', [], {})]
        for values, code in cases:
            with self.subTest(values=values), self.assertRaises(WorkshopError) as caught:
                runtime_policy_candidate(self.fixture(values), RUNTIME_POLICY_PROFILE)
            self.assertEqual(caught.exception.code, code)
        config = self.fixture()
        for text in ('{"schema":"runtime-sync-workshop-config/v1","x":1,"x":2}',
                     '{"nested":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '[]'):
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(WorkshopError) as caught:
                runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
            self.assertEqual(caught.exception.code, 'POLICY_AUTHORITY_INVALID')
        with self.assertRaises(WorkshopError) as caught:
            runtime_policy_candidate(config, 'disable-gates')
        self.assertEqual(caught.exception.code, 'POLICY_PROFILE_UNSUPPORTED')

    def test_refuses_foreign_paths_payloads_and_stale_preimages(self):
        from runtime_sync_workshop.corpus import runtime_policy_candidate, verify_runtime_policy_candidate, RUNTIME_POLICY_PROFILE
        from runtime_sync_workshop.util import WorkshopError, sha256_bytes
        config = self.fixture()
        generated = runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
        hashes = {k: sha256_bytes(v) for k, v in generated['source_files'].items()}
        mutated = json.loads(generated['candidate_files'][self.path.name]); mutated['guardian_required'] = True
        cases = ({}, {**generated['candidate_files'], '../foreign.json': b'{}'},
                 {self.path.name: json.dumps(mutated).encode('utf-8')},
                 {self.path.name: generated['candidate_files'][self.path.name] + b' '},
                 {self.path.name: bytearray(generated['candidate_files'][self.path.name])})
        for files in cases:
            with self.subTest(files=list(files)), self.assertRaises(WorkshopError):
                verify_runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE, files, expected_source_hashes=hashes)
        self.path.write_text(json.dumps({**config.raw, 'tx_recycle_trigger': 8}), encoding='utf-8')
        with self.assertRaises(WorkshopError) as caught:
            verify_runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE, generated['candidate_files'], expected_source_hashes=hashes)
        self.assertEqual(caught.exception.code, 'POLICY_BASELINE_CHANGED')
        config.raw['tx_recycle_trigger'] = 8  # Even a freshly loaded config cannot erase frozen preimage evidence.
        with self.assertRaises(WorkshopError) as caught:
            verify_runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE, generated['candidate_files'], expected_source_hashes=hashes)
        self.assertEqual(caught.exception.code, 'POLICY_BASELINE_CHANGED')

    def test_intake_dependency_requires_governance_and_correct_existing_binding(self):
        from runtime_sync_workshop.corpus import runtime_policy_candidate, RUNTIME_POLICY_PROFILE
        from runtime_sync_workshop.util import WorkshopError
        config = self.fixture(intake=True)
        config.machine_authority_files = (self.path.name,)
        with self.assertRaises(WorkshopError) as caught:
            runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_INTAKE_NOT_GOVERNED')
        config.machine_authority_files += ('project-intake.json',)
        intake = self.root / 'project-intake.json'
        data = json.loads(intake.read_bytes()); data['workshop_config_sha256'] = '0' * 64
        intake.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(WorkshopError) as caught:
            runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_INTAKE_BINDING_INVALID')
        intake.unlink()
        with self.assertRaises(WorkshopError) as caught:
            runtime_policy_candidate(config, RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_AUTHORITY_MISSING')

    def test_authority_race_and_unadmitted_config_fail_closed(self):
        import runtime_sync_workshop.corpus as corpus
        from runtime_sync_workshop.util import WorkshopError
        config = self.fixture(); original = corpus._policy_authority_bytes
        calls = []
        def racing(path):
            result = original(path); calls.append(path)
            if len(calls) == 1:
                self.path.write_text(json.dumps({**config.raw, 'tx_recycle_trigger': 8}), encoding='utf-8')
            return result
        with patch.object(corpus, '_policy_authority_bytes', side_effect=racing):
            with self.assertRaises(WorkshopError) as caught:
                corpus.runtime_policy_candidate(config, corpus.RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_BASELINE_CHANGED')
        config = self.fixture(); config.machine_authority_files = ()
        with self.assertRaises(WorkshopError) as caught:
            corpus.runtime_policy_candidate(config, corpus.RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_CONFIG_NOT_GOVERNED')
        config = self.fixture()
        with patch.object(corpus, 'MAX_POLICY_AUTHORITY_BYTES', 8):
            with self.assertRaises(WorkshopError) as caught:
                corpus.runtime_policy_candidate(config, corpus.RUNTIME_POLICY_PROFILE)
        self.assertEqual(caught.exception.code, 'POLICY_AUTHORITY_INVALID')

    def test_public_parser_exposes_plan_option_without_new_mutation_command(self):
        from runtime_sync_workshop.cli import _parser
        from runtime_sync_workshop.corpus import RUNTIME_POLICY_PROFILE
        import argparse
        parser = _parser(); args = parser.parse_args(['manifest', '--policy-profile', RUNTIME_POLICY_PROFILE])
        self.assertEqual(args.policy_profile, RUNTIME_POLICY_PROFILE)
        self.assertIsNone(parser.parse_args(['manifest']).policy_profile)
        commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        # Policy planning remains a read-only manifest option. Unrelated,
        # reviewed commands do not change this exact policy workflow contract.
        self.assertEqual({name for name in commands.choices if name.startswith("policy-")},
                         {"policy-checkout", "policy-prepare", "policy-verify",
                          "policy-apply", "policy-recover", "policy-abort"})
        self.assertNotIn("policy-plan", commands.choices)


class ProjectAuthorityTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_source_prefix("")

    def test_cmake_membership_ignores_comments_and_keeps_quoted_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cmake = root / "authority.cmake"
            cmake.write_text(
                "set(KAIROS_TRANSLATION_UNITS\n"
                '  "src/space name.cpp"\n'
                "  # src/commented.cpp\n"
                ")\n",
                encoding="utf-8",
            )
            config = SimpleNamespace(
                cmake_file=cmake,
                cmake_source_sets=("KAIROS_TRANSLATION_UNITS",),
                cmake_extra_sources=(),
                raw={"source_authority": {"translation_unit_extensions": ["cpp"]}},
            )
            set_source_prefix("")
            self.assertEqual(cmake_membership(config), ["src/space name.cpp"])

    def test_include_closure_uses_compiler_include_roots_and_angle_includes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "include").mkdir()
            (root / "src" / "main.cpp").write_text("#include <api.hpp>\n", encoding="utf-8")
            (root / "include" / "api.hpp").write_text("#pragma once\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(root / "include",),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={},
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["src/main.cpp"])
            self.assertEqual(headers, {"include/api.hpp"})
            self.assertEqual(issues, [])

    def test_angle_include_uses_persisted_angle_roots_not_iquote_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "quote").mkdir(); (root / "user").mkdir()
            (root / "main.cpp").write_text('#include <choice.hpp>\n', encoding="utf-8")
            (root / "quote" / "choice.hpp").write_text("#define QUOTE_ONLY 1\n", encoding="utf-8")
            (root / "user" / "choice.hpp").write_text("#define USER 1\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(root / "quote", root / "user"),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(root / "quote"), str(root / "user")],
                            "angle_roots": [str(root / "user")],
                            "idirafter_roots": [],
                            "compiler_probe": None,
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, {"user/choice.hpp"})
            self.assertEqual(issues, [])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_idirafter_selection_uses_retained_compiler_probe_before_local_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            after = root / "after"; after.mkdir()
            (root / "main.cpp").write_text('#include <stddef.h>\n', encoding="utf-8")
            (after / "stddef.h").write_text("#error WRONG_HEADER\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(after,),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(after)],
                            "angle_roots": [str(after)],
                            "idirafter_roots": [str(after)],
                            "compiler_probe": {
                                "argv": [shutil.which("c++"), "-idirafter", str(after)],
                                "directory": str(root),
                            },
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual(issues, [])

    def test_idirafter_without_probe_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            after = root / "after"; after.mkdir()
            (root / "main.cpp").write_text('#include <choice.hpp>\n', encoding="utf-8")
            (after / "choice.hpp").write_text("#pragma once\n", encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(after,),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_include_variants": {
                        "main.cpp": [{
                            "quote_roots": [str(after)],
                            "angle_roots": [str(after)],
                            "idirafter_roots": [str(after)],
                            "compiler_probe": None,
                        }]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual([item["code"] for item in issues], ["INCLUDE_SELECTION_AMBIGUOUS"])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_quoted_implicit_system_header_uses_retained_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.cpp"
            source.write_text('#include "stddef.h"\n', encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_compiler_probes": {
                        "main.cpp": [{"argv": ["c++"], "directory": str(root)}]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual(issues, [])

    @unittest.skipUnless(shutil.which("c++"), "c++ is not installed")
    def test_missing_quote_remains_a_workshop_issue_after_compiler_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.cpp"
            source.write_text('#include "__kairos_missing_header_12345.h"\n', encoding="utf-8")
            config = SimpleNamespace(
                codebase_root=root,
                runtime_root=root,
                include_roots=(),
                header_extensions=frozenset({".h", ".hpp"}),
                raw={
                    "translation_unit_compiler_probes": {
                        "main.cpp": [{"argv": ["c++"], "directory": str(root)}]
                    }
                },
            )
            set_source_prefix("")
            headers, issues = header_closure(config, ["main.cpp"])
            self.assertEqual(headers, set())
            self.assertEqual([issue["code"] for issue in issues], ["RUNTIME_INCLUDE_UNRESOLVED"])



if __name__ == "__main__":
    unittest.main()
