from __future__ import annotations

import hashlib
import json
import os
import re
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
    load_framework_manifest,
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


def _checkout_path_pattern(root: str) -> str:
    """Match a checkout path token, excluding longer or relative path prefixes."""
    return r"(?<![\w])" + re.escape(root) + r"(?![\w.-])"


class FrameworkDocumentPlanTests(unittest.TestCase):
    """Use complete real release trees; never mock successful bundle admission."""

    def setUp(self) -> None:
        from kairos.golden import _framework_source_entries
        self.temporary = tempfile.TemporaryDirectory(prefix="framework-document-plan-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.source, self.candidate = self.parent / "source", self.parent / "candidate"
        for root in (self.source, self.candidate):
            for entry in _framework_source_entries(ROOT):
                destination = root / entry["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / entry["path"], destination)
        # Legacy input is evidence, never current retrieval authority.
        (self.source / "README.md").write_text("# Legacy headerless readme\n", encoding="utf-8")
        (self.source / "kairos/kairos_harness/kairos/framework_manifest.json").write_text(
            '{"schema":"kairos-framework-index/v1"}\n', encoding="utf-8")

    def plan(self):
        from kairos.framework import framework_document_change_plan
        return framework_document_change_plan(self.source, self.candidate)

    def framework_authority(self, root=None):
        # Import the public Workshop owner from this complete product fixture.
        from types import SimpleNamespace
        workshop = str(ROOT / "workshop" / "src")
        if workshop not in sys.path:
            sys.path.insert(0, workshop)
        from runtime_sync_workshop import corpus
        from kairos.framework import framework_markdown_inventory
        root = self.candidate if root is None else root
        declaration = dict(schema=corpus.FRAMEWORK_DOCUMENT_AUTHORITY_SCHEMA,
                           root=".", namespace="KAIROS_FRAMEWORK",
                           documents=sorted(row["path"] for row in framework_markdown_inventory(root)))
        return corpus, SimpleNamespace(codebase_root=root, kairos_harness=ROOT / "kairos/kairos_harness",
                                       raw={"framework_documents": declaration})

    def test_framework_authority_native_status_without_ambient_python_paths(self):
        from kickstart.universal_init import initialize_universal_markdown
        from workshop.tests.test_context_transactions import project_spec
        corpus, declaration = self.framework_authority()
        workspace = self.parent / "separate-project-memory"
        intake = initialize_universal_markdown(project_root=self.candidate, workspace=workspace,
            workspace_id="KAIROS_CONTEXT_TEST", project_spec=project_spec(), auxiliary_documents=())
        self.assertEqual(intake["state"], "VERIFIED_PENDING_SEAL")
        config = Path(intake["workshop_config"])
        raw = json.loads(config.read_text(encoding="utf-8"))
        # Synthetic initial configuration BEFORE the first seal; no live migration.
        raw.update(declaration.raw)
        config.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        command = [sys.executable, "-I", "-S", "-B", str(self.candidate / "workshop/workshop.py"),
                   "--config", str(config), "status"]
        result = subprocess.run(command, cwd=self.parent, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["verified"])
        readme = self.candidate / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nActual unprojected native CLI drift.\n")
        result = subprocess.run(command, cwd=self.parent, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FRAMEWORK_DOCUMENT_AUTHORITY_INVALID", result.stdout + result.stderr)

    def test_framework_authority_refuses_a_different_configured_harness(self):
        corpus, config = self.framework_authority()
        foreign = self.parent / "foreign-harness" / "kairos"
        foreign.mkdir(parents=True)
        (foreign / "framework.py").write_bytes(b"# A different configured authority\n")
        config.kairos_harness = foreign.parent
        with self.assertRaises(corpus.WorkshopError) as caught:
            corpus.framework_document_authority(config)
        self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_AUTHORITY_INVALID")

    def test_framework_authority_absence_does_not_inspect_or_admit_a_consumer_tree(self):
        from types import SimpleNamespace
        corpus, _ = self.framework_authority()
        # Deliberately no codebase_root: absent declaration must not discover files.
        self.assertIsNone(corpus.framework_document_authority(SimpleNamespace(raw={})))

    def test_framework_authority_exact_coverage_projection_and_relocation_are_read_only(self):
        corpus, config = self.framework_authority()
        before = self.plan()["candidate_product"]
        first = corpus.framework_document_authority(config)
        moved = self.parent / "arbitrarily named framework"
        shutil.copytree(self.candidate, moved)
        _, relocated = self.framework_authority(moved)
        self.assertEqual(first, corpus.framework_document_authority(relocated))
        self.assertEqual(before, self.plan()["candidate_product"])
        self.assertEqual(config.raw["framework_documents"]["documents"],
                         sorted(row["path"] for row in first["documents"]))
        self.assertEqual(sum(row["role"] == "render_template" for row in first["documents"]), 7)
        self.assertEqual(len(first["projection_files"]), 2)
        self.assertEqual(first["namespace"], "KAIROS_FRAMEWORK")
        self.assertEqual(first["database_content_sha256"], framework_database_content_sha256(
            self.candidate / "kairos/kairos_harness/kairos/framework.db"))
        self.assertFalse(any((root / ".kairos").exists() for root in (self.candidate, moved)))

    def test_framework_authority_declaration_rejects_ambiguous_or_foreign_scope(self):
        import copy
        corpus, config = self.framework_authority()
        good = config.raw["framework_documents"]
        bad = [None, False, {}, {**good, "extra": True}, {**good, "root": "../other"},
               {**good, "namespace": "KAIROS_PROJECT"}, {**good, "schema": "unknown"}]
        bad += [{**good, "documents": value} for value in
                ([], "README.md", ["README.md", "AGENTS.md"], ["README.md", "README.md"],
                 ["README.md", "readme.md"], ["../README.md"], ["/README.md"],
                 ["C:README.md"], ["docs\\README.md"], ["docs//README.md"],
                 ["docs/./README.md"], ["docs/CON.md"], ["docs/trailing./README.md"],
                 ["docs/x:stream.md"], [False], ["a.md"] * 4097)]
        for declaration in bad:
            with self.subTest(declaration=repr(declaration)[:120]):
                config.raw = {"framework_documents": copy.deepcopy(declaration)}
                with self.assertRaises(corpus.WorkshopError) as caught:
                    corpus.framework_document_authority(config)
                self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_DECLARATION_INVALID")

    def test_framework_authority_refuses_unadmitted_or_missing_members(self):
        corpus, config = self.framework_authority()
        declared = config.raw["framework_documents"]["documents"]
        for paths in (declared[:-1], sorted([*declared, "docs/NOT_DISTRIBUTED.md"])):
            config.raw["framework_documents"]["documents"] = paths
            with self.assertRaises(corpus.WorkshopError) as caught:
                corpus.framework_document_authority(config)
            self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_MEMBERSHIP_DRIFT")

    def test_framework_authority_refuses_incomplete_metadata_and_stale_projection(self):
        corpus, config = self.framework_authority()
        readme = self.candidate / "README.md"
        original = readme.read_bytes()
        for changed in (b"# Headerless authority\n", original + b"\nUnprojected actual change.\n"):
            with self.subTest(changed=changed[:25]):
                readme.write_bytes(changed)
                with self.assertRaises(corpus.WorkshopError) as caught:
                    corpus.framework_document_authority(config)
                self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_AUTHORITY_INVALID")
        readme.write_bytes(original)
        self.assertIsNotNone(corpus.framework_document_authority(config))

    def test_framework_authority_refuses_actual_sql_mutation(self):
        import sqlite3
        from contextlib import closing
        corpus, config = self.framework_authority()
        database = self.candidate / "kairos/kairos_harness/kairos/framework.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE artifacts SET capsule='foreign projection' WHERE artifact_id='KAIROS_FRAMEWORK_RULES'")
            connection.commit()
        with self.assertRaises(corpus.WorkshopError) as caught:
            corpus.framework_document_authority(config)
        self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_AUTHORITY_INVALID")

    def test_framework_authority_rechecks_product_after_real_bundle_verification(self):
        import kairos.framework as framework
        corpus, config = self.framework_authority()
        actual = framework.verify_framework_bundle
        def changed(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.candidate / "docs" / "after-verification.md").write_text("# New drift\n", encoding="utf-8")
            return result
        with mock.patch.object(framework, "verify_framework_bundle", side_effect=changed):
            with self.assertRaises(corpus.WorkshopError) as caught:
                corpus._framework_document_authority_in_process(config)
        self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_SNAPSHOT_CHANGED")

    def test_framework_authority_uses_physical_configured_verifier_not_parent_cache(self):
        import kairos.framework as framework
        corpus, config = self.framework_authority()
        with mock.patch.object(framework, "framework_document_authority_snapshot",
                               side_effect=AssertionError("parent module cache is not authority")):
            self.assertEqual(corpus.framework_document_authority(config)["namespace"], "KAIROS_FRAMEWORK")

    @unittest.skipUnless(os.name == "nt", "real Windows junction test")
    def test_framework_authority_rejects_raw_junction_root_before_resolving(self):
        corpus, config = self.framework_authority()
        alias = self.parent / "framework-alias"
        self.make_junction(alias, self.candidate)
        config.codebase_root = alias
        with self.assertRaises(corpus.WorkshopError) as caught:
            corpus.framework_document_authority(config)
        self.assertEqual(caught.exception.code, "FRAMEWORK_DOCUMENT_AUTHORITY_INVALID")

    def add_valid_document(self, newline="\n"):
        path = self.candidate / "docs" / "PLAN_FIXTURE.md"
        path.write_text('''+++
schema = "kairos-context/v1"
id = "KAIROS_DOCUMENT_PLAN_FIXTURE"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_DOCUMENT_PLAN_FIXTURE"
updated_at = "2026-10-30T00:00:00Z"
capsule = "Synthetic document used only for exact admission-plan tests."
claim_boundary = "Owns synthetic fixture statements only."
entities = ["KAIROS_DOCUMENT_PLAN_FIXTURE"]
facets = ["synthetic-fixture"]
criteria = []
does_not_answer = ["production readiness"]
distribution_role = "reference"
[[answers]]
intent = "implementation"
question = "What synthetic document tests exact plan additions?"
target = "s-overview"
[[search_contract]]
query = "What synthetic document tests exact plan additions?"
expected = "KAIROS_DOCUMENT_PLAN_FIXTURE#s-overview"
required_top_k = 1
[refs]
+++
# Synthetic document plan fixture

## CONTEXT INDEX

- [Overview](#s-overview)

<a id="s-overview"></a>
## Overview

> Capsule: Synthetic document used only for exact admission-plan tests.

This fixture tests explicit indexed Markdown additions.
''', encoding="utf-8", newline=newline)
        return path

    def make_junction(self, alias: Path, target: Path) -> None:
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.addCleanup(alias.rmdir)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_windows_root_and_ancestor_junctions_fail_before_snapshot(self) -> None:
        from kairos.framework import framework_document_change_plan
        root_alias, parent_alias = self.parent / "root-alias", self.parent / "parent-alias"
        self.make_junction(root_alias, self.source)
        self.make_junction(parent_alias, self.parent)
        original_lstat = Path.lstat
        forbidden = parent_alias / "source"
        def observed_lstat(path, *args, **kwargs):
            if path == forbidden:
                raise AssertionError("inspected child behind a junction")
            return original_lstat(path, *args, **kwargs)
        with mock.patch("kairos.framework._framework_plan_files", side_effect=AssertionError("snapshot started")):
            with mock.patch.object(Path, "lstat", observed_lstat):
                for root in (root_alias, forbidden):
                    with self.subTest(root=root), self.assertRaisesRegex(RuntimeError, "link or junction"):
                        framework_document_change_plan(root, self.candidate)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_windows_intermediate_product_root_refuses_junction_before_hashing(self) -> None:
        from kairos.framework import _framework_plan_files
        original = self.source / "kairos"
        moved = self.parent / "moved-kairos"
        original.rename(moved)
        self.make_junction(original, moved)
        with mock.patch("kairos.golden._framework_source_entries", side_effect=AssertionError("hashing started")):
            with self.assertRaisesRegex(RuntimeError, "link or junction"):
                _framework_plan_files(self.source)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_windows_builder_junction_paths_are_refused_before_outputs(self) -> None:
        outside = self.parent / "outside"
        outside.mkdir()
        alias = self.parent / "output-alias"
        self.make_junction(alias, outside)
        package = self.candidate / "kairos/kairos_harness/kairos"
        before = {name: (package / name).read_bytes() for name in ("framework.db", "framework_manifest.json")}
        for option in ("--output", "--manifest", "--compare-content-with"):
            result = self.build_candidate(option, str(alias / "artifact"))
            with self.subTest(option=option):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("link or junction", result.stdout)
                self.assertEqual(list(outside.iterdir()), [])
                self.assertEqual(before, {name: (package / name).read_bytes() for name in before})
        source_alias = self.parent / "builder-source-alias"
        self.make_junction(source_alias, self.candidate)
        result = subprocess.run(
            [sys.executable, "-B", str(source_alias / "scripts/build_framework_index.py")],
            cwd=self.candidate, env={**os.environ, "PYTHONPATH": str(self.candidate / "kairos/kairos_harness")},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("link or junction", result.stdout)
        self.assertEqual(before, {name: (package / name).read_bytes() for name in before})

    @unittest.skipUnless(sys.platform == "win32", "Windows reparse attributes")
    def test_missing_windows_reparse_attributes_fail_closed(self) -> None:
        import stat
        from types import SimpleNamespace
        from kairos.util import is_link_or_junction
        with mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)):
            with self.assertRaisesRegex(OSError, "attributes are unavailable"):
                is_link_or_junction(self.source)

    def test_exact_legacy_to_current_plan_is_deterministic_and_read_only(self) -> None:
        def snapshot():
            return {str(root): {p.relative_to(root).as_posix(): p.read_bytes()
                               for p in root.rglob("*") if p.is_file()}
                    for root in (self.source, self.candidate)}
        before = snapshot()
        first = self.plan()
        self.assertEqual(first, self.plan())
        self.assertEqual(before, snapshot())
        self.assertTrue(first["read_only"])
        self.assertFalse(first["authority_admitted"])
        self.assertFalse(first["mutation_authorized"])
        self.assertTrue(first["transaction_required"])
        self.assertEqual(first["namespace"], "KAIROS_FRAMEWORK")
        self.assertEqual([(r["path"], r["operation"]) for r in first["changes"]],
                         [("README.md", "replace")])
        change = first["changes"][0]
        for root, key in ((self.source, "before"), (self.candidate, "after")):
            data = (root / "README.md").read_bytes()
            self.assertEqual(change[key]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(change[key]["size"], len(data))
        hashed = {k: v for k, v in first.items() if k != "plan_sha256"}
        self.assertEqual(first["plan_sha256"], hashlib.sha256(json.dumps(
            hashed, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest())
        moved = self.parent / "relocated"
        self.source.rename(moved)
        self.source = moved
        self.assertEqual(first, self.plan())

    def test_legacy_only_document_is_an_explicit_deletion(self) -> None:
        path = self.source / "docs" / "legacy-checkpoint.md"
        path.write_text("Historical source requiring reviewed archival before deletion.\n", encoding="utf-8")
        deleted = [r for r in self.plan()["changes"] if r["operation"] == "delete"]
        self.assertEqual([r["path"] for r in deleted], ["docs/legacy-checkpoint.md"])
        self.assertIsNone(deleted[0]["after"])
        self.assertEqual(deleted[0]["before"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(path.exists())

    def test_indexed_add_replace_delete_are_all_explicit(self) -> None:
        added = self.add_valid_document()
        (self.source / "docs" / "legacy-checkpoint.md").write_text("# Retired fixture\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(self.candidate / "scripts/build_framework_index.py")],
            cwd=self.candidate, env={**os.environ, "PYTHONPATH": str(self.candidate / "kairos/kairos_harness")},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        plan = self.plan()
        self.assertEqual([(r["path"], r["operation"]) for r in plan["changes"]], [
            ("README.md", "replace"), ("docs/PLAN_FIXTURE.md", "add"),
            ("docs/legacy-checkpoint.md", "delete"),
        ])
        change = next(r for r in plan["changes"] if r["operation"] == "add")
        self.assertIsNone(change["before"])
        self.assertEqual(change["after"]["sha256"], hashlib.sha256(added.read_bytes()).hexdigest())

    def build_candidate(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(self.candidate / "scripts/build_framework_index.py"), *arguments],
            cwd=self.candidate, env={**os.environ, "PYTHONPATH": str(self.candidate / "kairos/kairos_harness")},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

    def test_lf_and_crlf_have_distinct_raw_identity_and_equal_sql_identity(self) -> None:
        from kairos.framework import framework_database_content_sha256
        path = self.add_valid_document()
        result = self.build_candidate()
        self.assertEqual(result.returncode, 0, result.stdout)
        package = self.candidate / "kairos/kairos_harness/kairos"
        lf = json.loads((package / "framework_manifest.json").read_text(encoding="utf-8"))
        digest = framework_database_content_sha256(package / "framework.db")
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"\n", b"\r\n"))
        result = self.build_candidate()
        self.assertEqual(result.returncode, 0, result.stdout)
        crlf = json.loads((package / "framework_manifest.json").read_text(encoding="utf-8"))
        left = next(r for r in lf["documents"] if r["artifact_id"] == "KAIROS_DOCUMENT_PLAN_FIXTURE")
        right = next(r for r in crlf["documents"] if r["artifact_id"] == left["artifact_id"])
        self.assertNotEqual(left["sha256"], right["sha256"])
        self.assertEqual(right["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(left["content_sha256"], right["content_sha256"])
        self.assertEqual(right["content_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(digest, framework_database_content_sha256(package / "framework.db"))
        self.assertEqual(verify_framework_bundle(source_root=self.candidate)["schema"],
                         "kairos-framework-index/v3")
        self.assertFalse(self.plan()["mutation_authorized"])

    def test_v3_raw_and_content_hash_tampering_and_missing_field_are_refused(self) -> None:
        from kairos.framework import framework_markdown_coverage_sha256
        package = self.candidate / "kairos/kairos_harness/kairos"
        original = json.loads((package / "framework_manifest.json").read_text(encoding="utf-8"))
        forged_path = self.parent / "forged.json"
        for field in ("sha256", "content_sha256", "missing-content"):
            forged = json.loads(json.dumps(original))
            row = next(r for r in forged["markdown_coverage"] if r["role"] != "render_template")
            entry = next(r for r in forged["documents"] if r["artifact_id"] == row["artifact_id"])
            if field == "missing-content":
                row.pop("content_sha256"); entry.pop("content_sha256")
            else:
                row[field] = "0" * 64; entry[field] = "0" * 64
            forged["markdown_coverage_sha256"] = framework_markdown_coverage_sha256(forged["markdown_coverage"])
            forged_path.write_bytes(json.dumps(forged).encode("utf-8"))
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                verify_framework_bundle(source_root=self.candidate, manifest_path=forged_path)

    def test_genuine_v2_remains_strictly_verifiable_without_v3_fields(self) -> None:
        from kairos.framework import framework_markdown_coverage_sha256
        package = self.candidate / "kairos/kairos_harness/kairos"
        manifest = json.loads((package / "framework_manifest.json").read_text(encoding="utf-8"))
        manifest["schema"] = "kairos-framework-index/v2"
        for row in [*manifest["documents"], *manifest["markdown_coverage"]]:
            row.pop("content_sha256", None)
        manifest["markdown_coverage_sha256"] = framework_markdown_coverage_sha256(manifest["markdown_coverage"])
        old_path = self.parent / "legacy-v2.json"
        old_path.write_bytes(json.dumps(manifest).encode("utf-8"))
        self.assertEqual(verify_framework_bundle(source_root=self.candidate, manifest_path=old_path)["schema"],
                         "kairos-framework-index/v2")
        row = next(r for r in manifest["markdown_coverage"] if r["role"] != "render_template")
        row["content_sha256"] = row["sha256"]
        manifest["markdown_coverage_sha256"] = framework_markdown_coverage_sha256(manifest["markdown_coverage"])
        old_path.write_bytes(json.dumps(manifest).encode("utf-8"))
        with self.assertRaises(RuntimeError):
            verify_framework_bundle(source_root=self.candidate, manifest_path=old_path)

    def test_failed_staged_native_verification_preserves_previous_outputs(self) -> None:
        package = self.candidate / "kairos/kairos_harness/kairos"
        before = {name: (package / name).read_bytes() for name in ("framework.db", "framework_manifest.json")}
        code = '''import importlib.util,sys
from unittest import mock
spec=importlib.util.spec_from_file_location("fixture_builder",sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.argv=[sys.argv[1]]
with mock.patch.object(module,"verify_framework_bundle",side_effect=RuntimeError("injected staged native refusal")):
    module.main()
'''
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(self.candidate / "scripts/build_framework_index.py")],
            cwd=self.candidate, env={**os.environ, "PYTHONPATH": str(self.candidate / "kairos/kairos_harness")},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("injected staged native refusal", result.stdout)
        self.assertEqual(before, {name: (package / name).read_bytes() for name in before})

    def test_comparison_reads_previous_output_before_replacement(self) -> None:
        self.add_valid_document()
        package = self.candidate / "kairos/kairos_harness/kairos"
        before = {name: (package / name).read_bytes() for name in ("framework.db", "framework_manifest.json")}
        result = self.build_candidate("--compare-content-with", str(package / "framework.db"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("logical content differs", result.stdout)
        self.assertEqual(before, {name: (package / name).read_bytes() for name in before})

    def test_colliding_database_manifest_outputs_are_refused_before_write(self) -> None:
        target = self.parent / "collision.db"
        target.write_bytes(b"existing output")
        result = self.build_candidate("--output", str(target), "--manifest", str(target))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("outputs must be distinct", result.stdout)
        self.assertEqual(target.read_bytes(), b"existing output")

    def test_non_markdown_edit_addition_and_deletion_are_refused(self) -> None:
        path = self.candidate / "kairos_cli.py"
        original = path.read_bytes()
        path.write_bytes(original + b"\n# unauthorized coupled code change\n")
        with self.assertRaisesRegex(RuntimeError, "non-Markdown"):
            self.plan()
        path.write_bytes(original)
        extra = self.candidate / "docs" / "unadmitted.json"
        extra.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "non-Markdown"):
            self.plan()
        extra.unlink()
        (self.candidate / "LICENSE").unlink()
        with self.assertRaisesRegex(RuntimeError, "required product input missing"):
            self.plan()

    def test_incomplete_candidate_metadata_is_refused(self) -> None:
        (self.candidate / "README.md").write_text("# Missing metadata\n", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "metadata|frontmatter|front matter"):
            self.plan()

    def test_unknown_candidate_markdown_cannot_be_hidden_from_index(self) -> None:
        (self.candidate / "docs" / "hidden.md").write_text("# Not indexed\n", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "metadata|frontmatter|front matter"):
            self.plan()

    def test_valid_markdown_missing_from_actual_index_is_refused(self) -> None:
        self.add_valid_document()
        with self.assertRaisesRegex(RuntimeError, "coverage differs from the index"):
            self.plan()

    def test_foreign_candidate_namespace_is_refused(self) -> None:
        path = self.add_valid_document()
        path.write_text(path.read_text(encoding="utf-8").replace(
            'workspace = "KAIROS_FRAMEWORK"', 'workspace = "FOREIGN_PROJECT"'), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "foreign namespace"):
            self.plan()

    def test_candidate_bundle_is_really_verified(self) -> None:
        path = self.candidate / "kairos/kairos_harness/kairos/framework.db"
        path.write_bytes(path.read_bytes() + b"\x00")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            self.plan()

    def test_equal_or_nested_roots_are_refused(self) -> None:
        from kairos.framework import framework_document_change_plan
        for target in (self.source, self.source / "docs"):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "disjoint"):
                framework_document_change_plan(self.source, target)

    def test_hardlinked_input_is_refused(self) -> None:
        os.link(self.source / "README.md", self.source / "docs" / "alias.md")
        with self.assertRaisesRegex(RuntimeError, "singly linked"):
            self.plan()

    def test_linked_root_ancestor_is_refused(self) -> None:
        from kairos.framework import framework_document_change_plan
        alias = self.parent / "alias"
        try:
            os.symlink(self.source, alias, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink creation privilege unavailable")
            raise
        with self.assertRaisesRegex(RuntimeError, "link or junction"):
            framework_document_change_plan(alias, self.candidate)

    def test_oversized_input_is_refused_before_product_hashing(self) -> None:
        path = self.source / "docs" / "oversized.md"
        with path.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
        with mock.patch("kairos.golden._framework_source_entries", side_effect=AssertionError("unbounded hash read")):
            with self.assertRaisesRegex(RuntimeError, "32 MiB"):
                self.plan()

    def test_changed_preimage_during_verification_is_refused(self) -> None:
        from kairos.framework import _framework_plan_files
        count = 0
        def observed(root):
            nonlocal count
            result = _framework_plan_files(root)
            count += 1
            if count == 2:
                (self.source / "README.md").write_text("# Concurrent replacement\n", encoding="utf-8")
            return result
        with mock.patch("kairos.framework._framework_plan_files", side_effect=observed):
            with self.assertRaisesRegex(RuntimeError, "changed during verification"):
                self.plan()


class FrameworkDocumentTransactionTests(unittest.TestCase):
    """Real full-product admission and native ordinary source maintenance."""

    def setUp(self):
        from kairos.golden import _framework_source_entries
        from kickstart.universal_init import initialize_universal_markdown
        from workshop.tests.test_context_transactions import project_spec
        from runtime_sync_workshop.engine import WorkshopEngine
        self.temporary = tempfile.TemporaryDirectory(prefix="framework-native-writer-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.project = self.parent / "arbitrary-framework-name"
        self.workspace = self.parent / "separate-memory"
        for row in _framework_source_entries(ROOT):
            target = self.project / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / row["path"], target)
        (self.project / "README.md").write_text("# Historical headerless input\n", encoding="utf-8")
        (self.project / "docs/OLD_LOCAL_CHECKPOINT.md").write_text("# Obsolete synthetic checkpoint\n", encoding="utf-8")
        (self.project / "kairos/kairos_harness/kairos/framework_manifest.json").write_text(
            '{"schema":"kairos-framework-index/v1"}\n', encoding="utf-8")
        intake = initialize_universal_markdown(project_root=self.project, workspace=self.workspace,
            workspace_id="KAIROS_CONTEXT_TEST", project_spec=project_spec(), auxiliary_documents=())
        self.assertEqual(intake["state"], "VERIFIED_PENDING_SEAL")
        self.config = Path(intake["workshop_config"])
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        raw.update(transaction_documents_required=True, tx_retention_required=True)
        self.config.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        self.engine = WorkshopEngine(self.config)
        self.engine.seal()
        self.original_seal = self.engine.seal_path.read_bytes()
        self.original_config = self.config.read_bytes()

    def checkout(self):
        plan = self.engine.framework_documents_plan(ROOT)
        checkout = self.engine.framework_documents_checkout(ROOT, expected_plan_sha256=plan["plan_sha256"],
            purpose="Admit exact complete framework metadata and separate immutable retrieval projection")
        self.review(checkout)
        return checkout

    def review(self, checkout):
        from runtime_sync_workshop.corpus import FRAMEWORK_REVIEW_FIELDS, _framework_scope, _framework_inspect
        work = Path(checkout["work_directory"])
        root = work.parent
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        scope = _framework_scope(root, state)
        before = {r["path"]: r for r in scope["baseline_product"]}
        after = {r["path"]: r for r in _framework_inspect(self.engine.config, work / "framework", "product")}
        changed = sorted(p for p in before.keys() | after.keys() if p.lower().endswith(".md")
                         and before.get(p) != after.get(p))
        path = root / "review/framework_review.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        review.update(reviewer="native-framework-test", reason="Reviewed real product scope, changed document semantics, exact membership and separate projection; source-only probes do not change public behavioral contracts.",
            entries=[dict(path=p, reviewed_fields=list(FRAMEWORK_REVIEW_FIELDS),
                reason="Synthetic fixture change has an explicit role, valid current metadata and retrieval target, or removes the declared obsolete checkpoint without a current authority claim.") for p in changed])
        path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        path = root / "review/metadata_review.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        for entry in metadata["entries"]:
            entry.update(metadata_impact="none", reason="This document-only admission leaves the selected source and exact implementation map unchanged.",
                         reviewer="native-framework-test", reviewed_at="2026-10-30T00:00:00Z")
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def verified(self):
        result = self.checkout()
        tx = result["transaction_id"]
        self.engine.prepare(tx)
        self.engine.metadata_check(tx)
        self.engine.verify(tx)
        return result

    def test_native_admission_replaces_deletes_projects_and_preserves_namespace(self):
        import sqlite3
        from contextlib import closing
        result = self.verified()
        applied = self.engine.apply(result["transaction_id"])
        self.assertEqual(applied["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(applied["heartbeat"]["verified"])
        self.assertFalse((self.project / "docs/OLD_LOCAL_CHECKPOINT.md").exists())
        self.assertTrue((self.project / "README.md").read_bytes().startswith(b"+++"))
        self.assertEqual(self.engine.config.raw["framework_documents"]["namespace"], "KAIROS_FRAMEWORK")
        seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        mirror = json.loads(Path(seal["mirror_manifest"]).read_text(encoding="utf-8"))
        expected = [*self.engine.config.raw["framework_documents"]["documents"],
                    "kairos/kairos_harness/kairos/framework.db",
                    "kairos/kairos_harness/kairos/framework_manifest.json"]
        for relative in expected:
            digest = mirror["objects"]["framework:" + relative]
            self.assertEqual(self.engine._object_path(digest).read_bytes(), (self.project / relative).read_bytes())
        self.assertTrue(self.engine.status(persist=False)["verified"])
        with closing(sqlite3.connect(self.workspace / ".kairos/kairos.db")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM artifacts WHERE artifact_id='KAIROS_FRAMEWORK_RULES'").fetchone()[0], 0)
        self.assertFalse(self.engine.lease_path.exists())
        replay = self.engine.recover(result["transaction_id"])
        self.assertEqual(replay["state"], "POSTCHECK_VERIFIED")

    def test_admission_abort_preserves_original_legacy_documents_config_and_seal(self):
        result = self.checkout()
        self.assertEqual(self.engine.abort(result["transaction_id"])["state"], "ABORTED")
        self.assertEqual(self.config.read_bytes(), self.original_config)
        self.assertEqual(self.engine.seal_path.read_bytes(), self.original_seal)
        self.assertEqual((self.project / "README.md").read_text(encoding="utf-8"), "# Historical headerless input\n")

    def add_document_candidate(self):
        from types import SimpleNamespace
        result = self.checkout()
        work = Path(result["work_directory"])
        FrameworkDocumentPlanTests.add_valid_document(SimpleNamespace(candidate=work / "framework"))
        self.review(result)
        self.engine.prepare(result["transaction_id"])
        self.engine.metadata_check(result["transaction_id"])
        self.engine.verify(result["transaction_id"])
        return result

    def test_new_document_is_created_indexed_and_captured_in_mirror(self):
        import sqlite3
        from contextlib import closing
        result = self.add_document_candidate()
        self.engine.apply(result["transaction_id"])
        relative = "docs/PLAN_FIXTURE.md"
        self.assertIn(relative, self.engine.config.raw["framework_documents"]["documents"])
        self.assertTrue((self.project / relative).is_file())
        with closing(sqlite3.connect(self.project / "kairos/kairos_harness/kairos/framework.db")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM artifacts WHERE path=?", (relative,)).fetchone()[0], 1)
        seal = json.loads(self.engine.seal_path.read_text(encoding="utf-8"))
        mirror = json.loads(Path(seal["mirror_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(self.engine._object_path(mirror["objects"]["framework:" + relative]).read_bytes(),
                         (self.project / relative).read_bytes())

    def test_new_document_is_removed_when_admission_rolls_back(self):
        from runtime_sync_workshop import _core_apply
        from runtime_sync_workshop.util import WorkshopError
        result = self.add_document_candidate()
        actual = _core_apply.copy_exact
        failed = False
        def refused(source, target):
            nonlocal failed
            if Path(target) == self.config and not failed:
                failed = True
                raise OSError("synthetic failure after creation")
            return actual(source, target)
        with mock.patch.object(_core_apply, "copy_exact", side_effect=refused):
            with self.assertRaises(WorkshopError) as caught:
                self.engine.apply(result["transaction_id"])
        self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK")
        self.assertFalse((self.project / "docs/PLAN_FIXTURE.md").exists())
        self.assertEqual(self.config.read_bytes(), self.original_config)
        self.assertEqual(self.engine.seal_path.read_bytes(), self.original_seal)

    def test_document_change_after_verify_is_refused_before_any_live_write(self):
        from runtime_sync_workshop.util import WorkshopError
        result = self.verified()
        readme = Path(result["work_directory"]) / "framework/README.md"
        readme.write_bytes(readme.read_bytes() + b"\nUnverified document edit.\n")
        with self.assertRaises(WorkshopError) as caught:
            self.engine.apply(result["transaction_id"])
        # Mandatory retention checks the verified archive before core Apply.
        self.assertEqual(caught.exception.code, "TX_ARCHIVE_VERIFICATION_MISSING")
        self.assertEqual(self.config.read_bytes(), self.original_config)
        self.assertEqual(self.engine.seal_path.read_bytes(), self.original_seal)
        self.assertEqual((self.project / "README.md").read_text(encoding="utf-8"),
                         "# Historical headerless input\n")

    def test_stale_plan_and_incomplete_review_refuse_before_live_writes(self):
        from runtime_sync_workshop.util import WorkshopError
        with self.assertRaises(WorkshopError) as caught:
            self.engine.framework_documents_checkout(ROOT, expected_plan_sha256="0" * 64, purpose="Reject a stale reviewed admission plan")
        self.assertEqual(caught.exception.code, "FRAMEWORK_PLAN_CHANGED")
        self.assertFalse(self.engine.lease_path.exists())
        result = self.checkout()
        path = Path(result["work_directory"]).parent / "review/framework_review.json"
        review = json.loads(path.read_text(encoding="utf-8")); review["entries"] = []
        path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaises(WorkshopError) as caught:
            self.engine.prepare(result["transaction_id"])
        self.assertEqual(caught.exception.code, "FRAMEWORK_REVIEW_REQUIRED")
        self.assertEqual(self.config.read_bytes(), self.original_config)

    def test_source_edits_inside_document_product_are_refused(self):
        from runtime_sync_workshop.util import WorkshopError
        result = self.checkout()
        path = Path(result["work_directory"]) / "framework/kairos_cli.py"
        path.write_bytes(path.read_bytes() + b"\nFOREIGN_SOURCE_CHANGE = True\n")
        with self.assertRaises(WorkshopError) as caught:
            self.engine.prepare(result["transaction_id"])
        self.assertEqual(caught.exception.code, "FRAMEWORK_SOURCE_SCOPE_INVALID")
        self.assertEqual(self.config.read_bytes(), self.original_config)

    def test_partial_write_exception_rolls_back_exact_documents_projection_and_config(self):
        from runtime_sync_workshop import _core_apply
        from runtime_sync_workshop.util import WorkshopError
        result = self.verified()
        before = {p: (self.project / p).read_bytes() for p in
                  ("README.md", "docs/OLD_LOCAL_CHECKPOINT.md", "kairos/kairos_harness/kairos/framework.db",
                   "kairos/kairos_harness/kairos/framework_manifest.json")}
        actual = _core_apply.copy_exact
        failed = False
        def refused(source, target):
            nonlocal failed
            if Path(target) == self.config and not failed:
                failed = True
                raise OSError("synthetic config replacement failure")
            return actual(source, target)
        with mock.patch.object(_core_apply, "copy_exact", side_effect=refused):
            with self.assertRaises(WorkshopError) as caught:
                self.engine.apply(result["transaction_id"])
        self.assertEqual(caught.exception.code, "APPLY_ROLLED_BACK")
        for path, raw in before.items():
            self.assertEqual((self.project / path).read_bytes(), raw)
        self.assertEqual(self.config.read_bytes(), self.original_config)
        self.assertEqual(self.engine.seal_path.read_bytes(), self.original_seal)
        self.assertFalse(self.engine.lease_path.exists())

    def test_process_cut_during_first_document_recovers_exact_baseline(self):
        from runtime_sync_workshop import util
        from runtime_sync_workshop.engine import WorkshopEngine
        result = self.verified()
        class Interrupted(BaseException):
            pass
        actual = util.atomic_write_bytes
        def cut(path, data):
            value = actual(path, data)
            if Path(path) == self.project / "README.md":
                raise Interrupted()
            return value
        with mock.patch.object(util, "atomic_write_bytes", side_effect=cut):
            with self.assertRaises(Interrupted):
                self.engine.apply(result["transaction_id"])
        recovered = WorkshopEngine(self.config).recover(result["transaction_id"])
        self.assertEqual(recovered["state"], "ROLLED_BACK")
        self.assertEqual(self.config.read_bytes(), self.original_config)
        self.assertEqual(self.engine.seal_path.read_bytes(), self.original_seal)
        self.assertFalse(self.engine.lease_path.exists())

    def test_process_cut_after_all_targets_finishes_forward_without_second_apply(self):
        from runtime_sync_workshop import corpus
        from runtime_sync_workshop.engine import WorkshopEngine
        result = self.verified()
        class Interrupted(BaseException):
            pass
        with mock.patch.object(corpus, "framework_projection_reload", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                self.engine.apply(result["transaction_id"])
        fresh = WorkshopEngine(self.config)
        recovered = fresh.recover(result["transaction_id"])
        self.assertEqual(recovered["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(fresh.status(persist=False)["verified"])
        self.assertFalse(fresh.lease_path.exists())
        self.assertEqual(fresh.recover(result["transaction_id"])["state"], "POSTCHECK_VERIFIED")

    def test_foreign_partial_apply_bytes_are_never_overwritten_by_recovery(self):
        from runtime_sync_workshop import util
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.util import WorkshopError
        result = self.verified()
        class Interrupted(BaseException):
            pass
        actual = util.atomic_write_bytes
        def cut(path, data):
            value = actual(path, data)
            if Path(path) == self.project / "README.md":
                raise Interrupted()
            return value
        with mock.patch.object(util, "atomic_write_bytes", side_effect=cut):
            with self.assertRaises(Interrupted):
                self.engine.apply(result["transaction_id"])
        foreign = b"# Foreign bytes outside the admitted before/after set\n"
        (self.project / "README.md").write_bytes(foreign)
        with self.assertRaises(WorkshopError) as caught:
            WorkshopEngine(self.config).recover(result["transaction_id"])
        self.assertEqual(caught.exception.code, "FRAMEWORK_ROLLBACK_FOREIGN_BYTES")
        self.assertEqual((self.project / "README.md").read_bytes(), foreign)
        self.assertTrue(self.engine.lease_path.exists())

    def test_heartbeat_failure_finishes_forward_with_verified_heartbeat(self):
        from runtime_sync_workshop.engine import WorkshopEngine
        from runtime_sync_workshop.util import WorkshopError
        import kairos.heartbeat as heartbeat
        result = self.verified()
        actual = heartbeat.run_heartbeat
        def refused(*args, **kwargs):
            if kwargs.get("trigger") == "runtime-sync-workshop:" + result["transaction_id"]:
                raise OSError("synthetic result heartbeat outage")
            return actual(*args, **kwargs)
        with mock.patch.object(heartbeat, "run_heartbeat", side_effect=refused):
            with self.assertRaises(WorkshopError) as caught:
                self.engine.apply(result["transaction_id"])
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
        fresh = WorkshopEngine(self.config)
        recovered = fresh.recover(result["transaction_id"])
        self.assertEqual(recovered["state"], "POSTCHECK_VERIFIED")
        self.assertTrue(recovered["framework_heartbeat"]["verified"])
        self.assertFalse(fresh.lease_path.exists())

    def test_ordinary_source_patch_updates_framework_projection_in_same_native_apply(self):
        from workshop.tests.metadata_test_support import complete_metadata_audit
        from runtime_sync_workshop.corpus import build_corpus_manifest
        self.engine.apply(self.verified()["transaction_id"])
        result = self.engine.checkout(["kairos_cli.py"], purpose="Exercise native source plus framework projection maintenance")
        work = Path(result["work_directory"])
        source = work / "kairos_cli.py"
        source.write_bytes(source.read_bytes() + b"\nFRAMEWORK_MAINTENANCE_PROBE = True\n")
        self.review(result)
        complete_metadata_audit(self.engine, result)
        tx = result["transaction_id"]
        self.engine.prepare(tx)
        self.engine.metadata_check(tx)
        self.engine.verify(tx)
        applied = self.engine.apply(tx)
        self.assertTrue(applied["heartbeat"]["verified"])
        self.assertIn(b"FRAMEWORK_MAINTENANCE_PROBE = True", (self.project / "kairos_cli.py").read_bytes())
        manifest = build_corpus_manifest(self.engine.config)
        self.assertTrue(manifest["verified"])
        self.assertIn("framework_documents", manifest["authority"])
        self.assertFalse(self.engine.lease_path.exists())


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
        self.assertEqual(result['document_count'], len(load_framework_manifest()['documents']) + 2)
        self.assertEqual(result['source_documents_verified'], len(load_framework_manifest()['documents']))
        self.assertEqual(result['generated_source_files_verified'], len(WORKSHOP_MODULE_ROLES) + 4)
        manifest = json.loads(framework_manifest_path().read_text(encoding='utf-8'))
        self.assertEqual(
            hashlib.sha256(framework_database_path().read_bytes()).hexdigest(),
            manifest['database_sha256'],
        )


    def test_distributed_markdown_has_complete_source_or_template_coverage(self) -> None:
        from kairos.framework import (
            FRAMEWORK_RENDER_TEMPLATES, framework_markdown_inventory,
            load_framework_manifest, source_distribution_root,
        )
        coverage = framework_markdown_inventory(source_distribution_root())
        manifest = load_framework_manifest()
        self.assertEqual(coverage, manifest["markdown_coverage"])
        sources = [row for row in coverage if row["role"] != "render_template"]
        templates = [row for row in coverage if row["role"] == "render_template"]
        self.assertEqual({row["path"] for row in templates}, set(FRAMEWORK_RENDER_TEMPLATES))
        self.assertEqual(
            [{k: row[k] for k in ("path", "artifact_id", "sha256", "content_sha256")} for row in sources],
            manifest["documents"],
        )
        for required in ("workshop/RECYCLING.md", "docs/growth/START_HERE.md",
                         "docs/ARCHIVED_RELEASE_HISTORY.md"):
            self.assertIn(required, {row["path"] for row in sources})

    def test_complete_coverage_rejects_self_consistent_omission_and_template_relabel(self) -> None:
        from kairos.framework import (
            load_framework_manifest, verify_framework_bundle,
            framework_markdown_coverage_sha256,
        )
        import copy
        original = load_framework_manifest()
        for kind in ("omit", "relabel"):
            with self.subTest(kind=kind):
                forged = copy.deepcopy(original)
                row = next(x for x in forged["markdown_coverage"]
                           if x["path"] == "workshop/RECYCLING.md")
                if kind == "omit":
                    forged["markdown_coverage"].remove(row)
                    forged["documents"] = [x for x in forged["documents"]
                                           if x["path"] != row["path"]]
                    forged["document_count"] -= 1
                else:
                    row["role"] = "render_template"
                    row.pop("artifact_id")
                    row["catalog"] = "KAIROS_CANONICAL_DOCUMENTS#s-render-templates"
                forged["markdown_coverage_sha256"] = framework_markdown_coverage_sha256(
                    forged["markdown_coverage"])
                with mock.patch("kairos.framework.load_framework_manifest", return_value=forged):
                    with self.assertRaises(RuntimeError):
                        verify_framework_bundle(verify_sources=False)

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
        self.assertEqual(len(sub.choices), 75)
        self.assertIn('framework-documents-plan', sub.choices)
        self.assertIn('framework-documents-checkout', sub.choices)
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
        handle = {('DEPLOYMENT_GUIDE', 's-migration'): {'procedure'},
                  ('DEPLOYMENT_RULES', 's-migration'): {'procedure'}}
        route_score, _ = _score_row(router, frame, 0, handle, {}, set())
        rule_score, _ = _score_row(rules, frame, 1, handle, {}, set())
        self.assertGreater(route_score, rule_score)
        _, reasons = _score_row(router, frame, 0, handle, {}, set())
        self.assertTrue(any(reason.startswith("procedure_route_balance") for reason in reasons))
        for question in ("Did deployment A happen before deployment B?",
                         "What authority approves deletion?"):
            nonprocedure = compile_query(question)
            _, reasons = _score_row(router, nonprocedure, 0, handle, {}, set())
            self.assertFalse(any(reason.startswith("procedure_route_balance") for reason in reasons))
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

    def test_checkout_path_guard_distinguishes_absolute_and_relative_prefixes(self) -> None:
        for root in ("/work", r"C:\work"):
            with self.subTest(root=root):
                pattern = _checkout_path_pattern(root)
                for text in (root, "`" + root + "`", root + "/child.py", root + r"\child.py"):
                    self.assertRegex(text, pattern)
                for text in (root + "shop/README.md", root + "space", root + "-copy", root + ".md"):
                    self.assertNotRegex(text, pattern)
        self.assertNotRegex("docs/work/guide.md", _checkout_path_pattern("/work"))
        self.assertNotRegex("docs/workshop/README.md", _checkout_path_pattern("/work"))

    def test_tool_router_is_promoted_in_both_fresh_initialization_paths(self) -> None:
        cases = (
            ('How can I recover a transaction whose report is missing from search without applying the code again?', 's-recovery'),
            ('Where can I check disk usage before removing old transaction copies?', 's-retention'),
            ('Wie kann ich alte Transaktions-Arbeitskopien sicher archivieren und wiederherstellen?', 's-retention'),
            ('Wie kann ich den Workspace aufräumen?', 's-retention'),
        )
        checkout_path = _checkout_path_pattern(str(ROOT))
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
                self.assertNotRegex(text, checkout_path)
                self.assertIn('Derived release routing only', text)
                self.assertIn('vault garbage collection is not implemented', text)
                context_path = workspace / CONTEXT_TOOL_CATALOG_PATH
                context = prepare_document(context_path, workspace)
                self.assertEqual(context.frontmatter.metadata['id'], CONTEXT_TOOL_CATALOG_ID)
                context_text = context_path.read_text(encoding='utf-8')
                self.assertNotRegex(context_text, checkout_path)
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
        self.assertEqual(payload['framework']['source_documents_verified'], len(load_framework_manifest()['documents']))

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
