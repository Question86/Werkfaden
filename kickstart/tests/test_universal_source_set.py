from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from kickstart.universal_init import initialize_universal_markdown


ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_SRC = ROOT / "workshop" / "src"
if str(WORKSHOP_SRC) not in sys.path:
    sys.path.insert(0, str(WORKSHOP_SRC))

from runtime_sync_workshop.corpus import build_corpus_manifest  # noqa: E402
from runtime_sync_workshop.engine import WorkshopEngine  # noqa: E402
from runtime_sync_workshop.universal_migration import UniversalSourceSetMigration  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


def _project_spec() -> dict:
    return {
        "schema": "kairos-project-kickoff/v1",
        "goal": {
            "schema": "kairos-goal/v1",
            "id": "GOAL_SET",
            "title": "Source set",
            "objective": "Govern source-set topology only through Workshop transactions.",
            "state": "active",
            "milestones": [{
                "id": "MILESTONE_SET_01",
                "title": "Source-set transaction",
                "objective": "Verify create delete rename and related edits atomically.",
                "state": "active",
                "depends_on": [],
                "criteria": [{
                    "id": "CRIT_SET_001",
                    "description": "Live source membership changes only after verified apply.",
                    "state": "active",
                    "evidence_required": "Source-set Workshop postcheck.",
                    "required_artifact_types": ["code", "report"],
                }],
            }],
        },
        "task": {
            "id": "TASK_SET_001",
            "title": "Verify source-set mutation",
            "objective": "Advance source and Markdown authority as one transition.",
            "milestone": "MILESTONE_SET_01",
            "criteria": ["CRIT_SET_001"],
        },
    }


class UniversalSourceSetTests(unittest.TestCase):
    def test_create_delete_and_rename_apply_only_after_shadow_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "rename_me.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "delete_me.py").write_text("DELETE = True\n", encoding="utf-8")
            (project / "keep.py").write_text("KEEP = 1\n", encoding="utf-8")
            initial = {
                path.name: path.read_bytes()
                for path in project.glob("*.py")
            }
            intake = initialize_universal_markdown(
                project_root=project,
                workspace=root / "workspace",
                project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.seal()
            migration = UniversalSourceSetMigration(engine)
            checkout = migration.checkout(purpose="Create delete and rename static Python sources atomically")
            candidate = Path(checkout["candidate_root"])

            # All changes happen only in Workshop-owned candidate state.
            (candidate / "rename_me.py").rename(candidate / "renamed.py")
            (candidate / "delete_me.py").unlink()
            (candidate / "new_file.py").write_text("NEW = 3\n", encoding="utf-8")

            # Governed live project is bit-identical before verified apply.
            self.assertEqual({path.name: path.read_bytes() for path in project.glob("*.py")}, initial)

            prepared = migration.prepare(checkout["transaction_id"])
            self.assertEqual(prepared["state"], "PREPARED")
            self.assertEqual(set(prepared["added_sources"]), {"new_file.py", "renamed.py"})
            self.assertEqual(set(prepared["removed_sources"]), {"delete_me.py", "rename_me.py"})
            self.assertEqual(prepared["renames"], {"rename_me.py": "renamed.py"})
            self.assertEqual(prepared["updated_existing"], [])
            self.assertEqual({path.name: path.read_bytes() for path in project.glob("*.py")}, initial)

            verified = migration.verify(checkout["transaction_id"])
            self.assertEqual(verified["state"], "SHADOW_VERIFIED")
            self.assertEqual({path.name: path.read_bytes() for path in project.glob("*.py")}, initial)

            receipt = migration.apply(checkout["transaction_id"])
            self.assertEqual(receipt["state"], "POSTCHECK_VERIFIED")
            self.assertTrue(receipt["bit_exact"])
            self.assertEqual(receipt["renames"], {"rename_me.py": "renamed.py"})

            self.assertFalse((project / "rename_me.py").exists())
            self.assertFalse((project / "delete_me.py").exists())
            self.assertEqual((project / "renamed.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertEqual((project / "new_file.py").read_text(encoding="utf-8"), "NEW = 3\n")
            self.assertEqual((project / "keep.py").read_text(encoding="utf-8"), "KEEP = 1\n")

            post = build_corpus_manifest(engine.config)
            self.assertTrue(post["verified"], post["issues"])
            self.assertEqual(
                set(post["authority"]["translation_units"]),
                {"keep.py", "new_file.py", "renamed.py"},
            )
            active_ids = {post["records"][path]["blueprint"]["filename"] for path in post["authority"]["translation_units"]}
            self.assertEqual({path.name for path in engine.config.blueprint_root.glob("*.md")}, active_ids)
            retired_text = "\n".join(path.read_text(encoding="utf-8") for path in engine.config.managed_blueprint_root.glob("*.md"))
            self.assertIn('state = "superseded"', retired_text)

    def test_source_addition_cannot_bypass_metadata_review_for_existing_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "app.py"
            source.write_bytes(b"VALUE = 1\n")
            intake = initialize_universal_markdown(
                project_root=project, workspace=root / "workspace", project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.seal()
            migration = UniversalSourceSetMigration(engine)
            checkout = migration.checkout(purpose="Add a source while attempting an unreviewed existing-source patch")
            candidate = Path(checkout["candidate_root"])
            (candidate / "app.py").write_bytes(b"VALUE = 2\n")
            (candidate / "new_file.py").write_bytes(b"NEW = 3\n")

            with self.assertRaises(WorkshopError) as raised:
                migration.prepare(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "ORDINARY_METADATA_PATCH_REQUIRED")
            self.assertIn("app.py", str(raised.exception.details))
            self.assertEqual(source.read_bytes(), b"VALUE = 1\n")
            self.assertFalse((project / "new_file.py").exists())
            migration.abort(checkout["transaction_id"])

    def test_candidate_build_or_config_mutation_fails_closed_without_touching_live(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            before = (project / "pyproject.toml").read_bytes()
            intake = initialize_universal_markdown(
                project_root=project,
                workspace=root / "workspace",
                project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.seal()
            migration = UniversalSourceSetMigration(engine)
            checkout = migration.checkout(purpose="Attempt an unauthorized project configuration mutation")
            candidate = Path(checkout["candidate_root"])
            (candidate / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
            with self.assertRaises(WorkshopError) as raised:
                migration.prepare(checkout["transaction_id"])
            self.assertEqual(raised.exception.code, "UNIVERSAL_SOURCE_SET_FORBIDDEN_MUTATION")
            self.assertEqual((project / "pyproject.toml").read_bytes(), before)
            migration.abort(checkout["transaction_id"])

    def test_nested_kairos_workspace_is_pruned_from_source_set_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            workspace = project / "kairos_workspace"
            intake = initialize_universal_markdown(
                project_root=project,
                workspace=workspace,
                project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            engine.seal()
            migration = UniversalSourceSetMigration(engine)
            checkout = migration.checkout(purpose="Prove nested KAIROS state is excluded from candidate copy")
            candidate = Path(checkout["candidate_root"])
            self.assertTrue((candidate / "app.py").is_file())
            self.assertFalse((candidate / "kairos_workspace").exists())
            self.assertFalse(any("transactions" in path.relative_to(candidate).parts for path in candidate.rglob("*")))
            migration.abort(checkout["transaction_id"])

    def test_orphan_checkout_lease_recovery_requires_exact_unchanged_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            intake = initialize_universal_markdown(
                project_root=project,
                workspace=root / "workspace",
                project_spec=_project_spec(),
            )
            engine = WorkshopEngine(Path(intake["workshop_config"]))
            seal = engine.seal()
            migration = UniversalSourceSetMigration(engine)
            transaction_id = "TXN_" + "a" * 24
            engine._acquire_lease(transaction_id)
            tx_root = engine._transaction_path(transaction_id)
            tx_root.mkdir(parents=True)
            before = (project / "app.py").read_bytes()
            recovered = migration.recover_orphan_checkout(
                transaction_id,
                expected_sealed_package_sha256=seal["package_sha256"],
            )
            self.assertEqual(recovered["state"], "ABORTED_ORPHANED_CHECKOUT")
            self.assertFalse(engine.lease_path.exists())
            self.assertEqual((project / "app.py").read_bytes(), before)
            state = __import__("json").loads((tx_root / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["orphan_recovery"]["live_corpus_verified"])


    def test_orphan_recovery_preserves_witness_and_never_invents_current_actor_origin(self) -> None:
        import io
        import json
        import sqlite3
        from contextlib import closing, redirect_stdout
        from kairos.cli import main
        from kairos.frontmatter import split_frontmatter
        from kairos.transaction_document import document_id, document_path
        from runtime_sync_workshop.tx_documents import assert_projected
        from unittest.mock import patch

        for with_checkout_witness in (False, True):
            with self.subTest(with_checkout_witness=with_checkout_witness):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    project = root / "project"
                    project.mkdir()
                    source = project / "app.py"
                    source.write_bytes(b"VALUE = 1\n")
                    before = source.read_bytes()
                    intake = initialize_universal_markdown(
                        project_root=project, workspace=root / "workspace", project_spec=_project_spec(),
                    )
                    engine = WorkshopEngine(Path(intake["workshop_config"]))
                    seal = engine.seal()
                    migration = UniversalSourceSetMigration(engine)
                    expected_scope = None
                    if with_checkout_witness:
                        class SimulatedProcessExit(BaseException):
                            pass
                        saved = {}
                        native_save = engine._save_transaction
                        def stop_after_staging(root_path, state, **kwargs):
                            native_save(root_path, state, **kwargs)
                            saved["tx"] = state["transaction_id"]
                            raise SimulatedProcessExit()
                        with patch.object(engine, "_save_transaction", side_effect=stop_after_staging):
                            with self.assertRaises(SimulatedProcessExit):
                                migration.checkout(purpose="Preserve real checkout provenance across interrupted staging")
                        tx = saved["tx"]
                        tx_root, staged = engine._load_transaction(tx)
                        self.assertEqual(staged["state"], "CHECKOUT_STAGING")
                        expected_scope = staged["original_scope"].copy()
                        self.assertEqual(expected_scope["task_id"], "TASK_SET_001")
                        self.assertTrue(engine.lease_path.exists())
                    else:
                        tx = "TXN_" + "a" * 24
                        engine._acquire_lease(tx)
                        tx_root = engine._transaction_path(tx)
                        tx_root.mkdir(parents=True)
                        self.assertFalse((tx_root / "state.json").exists())
                    current_path = engine.config.kairos_workspace / "current.json"
                    original_actor = json.loads(current_path.read_text(encoding="utf-8"))
                    self.assertEqual(original_actor["active_task"], "TASK_SET_001")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = main([
                            "search", "Which task constrains the next recovery actor?",
                            "--workspace", str(engine.config.kairos_workspace), "--limit", "1",
                        ])
                    self.assertEqual(code, 0, output.getvalue())
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = main([
                            "new-task", "--workspace", str(engine.config.kairos_workspace),
                            "--id", "TASK_RECOVERY_ACTOR", "--goal", original_actor["active_goal"],
                            "--milestone", original_actor["active_milestone"],
                            "--title", "Separate native recovery actor",
                            "--objective", "Recover missing-state orphan without inventing original checkout scope.",
                        ])
                    self.assertEqual(code, 0, output.getvalue())
                    recovery_actor = json.loads(current_path.read_text(encoding="utf-8"))
                    self.assertEqual(recovery_actor["active_task"], "TASK_RECOVERY_ACTOR")
                    recovered = migration.recover_orphan_checkout(
                        tx, expected_sealed_package_sha256=seal["package_sha256"],
                    )
                    self.assertEqual(recovered["state"], "ABORTED_ORPHANED_CHECKOUT")
                    _, state = engine._load_transaction(tx)
                    self.assertIn("original_scope", state)
                    self.assertEqual(state["original_scope"], expected_scope)
                    self.assertIsNone(state.get("historical_scope"))
                    assert_projected(engine, tx)
                    metadata = split_frontmatter(
                        (engine.config.kairos_workspace / document_path(tx)).read_text(encoding="utf-8")
                    ).metadata
                    with closing(sqlite3.connect(engine.config.kairos_database)) as connection:
                        row = connection.execute(
                            "SELECT metadata_json FROM artifacts WHERE artifact_id=?", (document_id(tx),),
                        ).fetchone()
                    self.assertIsNotNone(row)
                    projected = json.loads(row[0])
                    self.assertEqual(metadata["scope_status"], "BOUND" if with_checkout_witness else "SCOPE_UNRESOLVED")
                    expected_task = "TASK_SET_001" if with_checkout_witness else None
                    self.assertEqual(metadata.get("task"), expected_task)
                    self.assertEqual(projected.get("task"), expected_task)
                    self.assertEqual(state["tx_event_id"], metadata["tx_event_id"])
                    self.assertEqual(state["tx_event_id"], projected["tx_event_id"])
                    self.assertFalse(engine.lease_path.exists())
                    self.assertFalse((tx_root / "rollback" / "targets.json").exists())
                    self.assertEqual(source.read_bytes(), before)



if __name__ == "__main__":
    unittest.main()
