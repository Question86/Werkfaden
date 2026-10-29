from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "workshop/src"):
    sys.path.insert(0, str(path))
from runtime_sync_workshop import cli
from runtime_sync_workshop._engine_core_v1 import load_engine_core
from runtime_sync_workshop.util import WorkshopError
import workshop.tests.test_metadata_gate as metadata_fixture


class ReviewIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = metadata_fixture.MetadataGateTests("test_verify_requires_separate_aftercheck_and_receipt_reaches_apply")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_merged_cli_retains_guardian_and_metadata_commands(self):
        parser = cli._parser()
        args = parser.parse_args(["checkout", "--source", "example.py", "--purpose", "fixture", "--document", "docs/example.md", "--guardian", "GRD2_fixture", "--guardian-state-ticket", "ticket"])
        self.assertEqual(args.document, ["docs/example.md"])
        self.assertEqual(args.guardian, "GRD2_fixture")
        self.assertEqual(parser.parse_args(["metadata-check", "TXN_fixture"]).command, "metadata-check")
        self.assertEqual(parser.parse_args(["guard-status", "GRD2_fixture"]).command, "guard-status")

    def test_core_loader_cannot_be_called_directly(self):
        with self.assertRaises(WorkshopError) as caught:
            load_engine_core()
        self.assertEqual(caught.exception.code, "QUARANTINED_ENGINE_DIRECT_USE")

    def test_regular_implementation_is_the_actual_guarded_engine_base(self):
        from runtime_sync_workshop import _engine_implementation as implementation
        from runtime_sync_workshop.engine import WorkshopEngine
        self.assertIn(implementation.WorkshopEngine, WorkshopEngine.__mro__)
        self.assertEqual(Path(implementation.WorkshopEngine.apply.__code__.co_filename).resolve(),
                         Path(implementation.__file__).resolve().parent / "_core_apply.py")
        with self.assertRaises(WorkshopError) as caught:
            implementation.WorkshopEngine(Path("must-not-open-missing-config.json"))
        self.assertEqual(caught.exception.code, "PRIVATE_ENGINE_DIRECT_USE")

    def test_composition_has_unique_method_owners_and_preserves_public_overrides(self):
        import importlib
        from runtime_sync_workshop import _engine_implementation as implementation
        from runtime_sync_workshop.engine import WorkshopEngine
        groups = ("state", "checkout", "metadata", "prepare", "verify", "apply", "recovery", "scientific")
        owners = {}
        for group in groups:
            module = importlib.import_module("runtime_sync_workshop._core_" + group)
            classes = [value for value in vars(module).values()
                       if isinstance(value, type) and value.__module__ == module.__name__]
            self.assertEqual(len(classes), 1)
            self.assertIn(classes[0], implementation.WorkshopEngine.__mro__)
            for name, value in vars(classes[0]).items():
                if callable(value) or isinstance(value, (property, staticmethod, classmethod)):
                    self.assertNotIn(name, owners, "Duplicate base method owner")
                    owners[name] = module.__name__
        self.assertEqual(len(owners), 51)
        # These public overrides preserve the declared guards over core owners,
        # including both projection-enforcing technical recovery exits.
        self.assertEqual(set(owners) & set(vars(WorkshopEngine)), {
            "__init__", "_acquire_lease", "_release_lease", "_require_lease",
            "_save_transaction", "abort", "apply", "authority_recover", "checkout",
            "include_documents", "prepare", "recover", "seal", "stale_abort", "verify",
        })
        self.assertEqual(owners["apply"], "runtime_sync_workshop._core_apply")
        self.assertEqual(owners["_save_transaction"], "runtime_sync_workshop._core_state")

    def test_typed_contract_preserves_concrete_canonical_engine(self):
        import inspect
        from runtime_sync_workshop._core_contract import EngineCoreContract
        from runtime_sync_workshop._engine_implementation import WorkshopEngine as Base
        from runtime_sync_workshop.engine import WorkshopEngine
        self.assertTrue(inspect.isabstract(EngineCoreContract))
        self.assertEqual(len(EngineCoreContract.__abstractmethods__), 51)
        self.assertFalse(inspect.isabstract(Base))
        self.assertFalse(inspect.isabstract(WorkshopEngine))
        self.assertIn(EngineCoreContract, Base.__mro__)
        with self.assertRaises(WorkshopError) as caught:
            Base(Path("must-not-open-missing-config.json"))
        self.assertEqual(caught.exception.code, "PRIVATE_ENGINE_DIRECT_USE")

    def test_frame_unavailable_refuses_all_legacy_or_private_bridges(self):
        import argparse
        from runtime_sync_workshop import _engine_core_v1, _cli_core_v1, _cli_implementation
        cases = [(_engine_core_v1.load_engine_core, (), "QUARANTINED_ENGINE_DIRECT_USE"),
                 (_cli_core_v1.load_cli_core, (), "QUARANTINED_CLI_DIRECT_USE"),
                 (_cli_implementation.execute, (argparse.Namespace(command="status"),), "PRIVATE_CLI_DIRECT_USE")]
        for function, args, code in cases:
            with self.subTest(code=code):
                with patch("inspect.currentframe", return_value=None):
                    with self.assertRaises(WorkshopError) as caught:
                        function(*args)
                self.assertEqual(caught.exception.code, code)

    def test_regular_cli_refuses_dispatch_outside_public_guard(self):
        import argparse
        from runtime_sync_workshop import _cli_implementation as implementation
        with self.assertRaises(WorkshopError) as caught:
            implementation.execute(argparse.Namespace(command="status"))
        self.assertEqual(caught.exception.code, "PRIVATE_CLI_DIRECT_USE")

    def test_manual_seal_refuses_unreadable_or_corrupt_guardian_state_without_writes(self):
        import json
        from runtime_sync_workshop.guardian_common import SCHEMA
        fixture = self.fixture()
        fixture.checkout()
        fixture.engine.abort(fixture.transaction_id)
        directory = fixture.engine.config.state_directory / "guardian-v2"
        directory.mkdir(parents=True, exist_ok=True)
        ident = "GRD2_" + "a" * 24
        path = directory / (ident + ".json")
        valid = {"schema": SCHEMA, "guardian_id": ident, "closed": False, "patch_session": None}
        cases = [(b"{invalid", "GUARDIAN_SESSION_UNREADABLE"),
                 (b"[]", "GUARDIAN_SESSION_CORRUPT"),
                 (json.dumps({**valid, "closed": "false"}).encode(), "GUARDIAN_SESSION_CORRUPT"),
                 (json.dumps({**valid, "guardian_id": "wrong"}).encode(), "GUARDIAN_SESSION_CORRUPT"),
                 (json.dumps({**valid, "schema": "wrong"}).encode(), "GUARDIAN_SESSION_CORRUPT"),
                 (json.dumps({**valid, "patch_session": []}).encode(), "GUARDIAN_SESSION_CORRUPT")]
        original_seal = fixture.engine.seal_path.read_bytes()
        for raw, code in cases:
            with self.subTest(raw=raw):
                path.write_bytes(raw)  # Fault confined to disposable fixture.
                with patch.object(fixture.engine, "_guardian_enabled", return_value=True):
                    with self.assertRaises(WorkshopError) as caught:
                        fixture.engine.seal()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(fixture.engine.seal_path.read_bytes(), original_seal)
                self.assertFalse(fixture.engine.lease_path.exists())
                self.assertEqual(path.read_bytes(), raw)

    def test_manual_seal_distinguishes_open_and_closed_valid_guardian_state(self):
        import json
        from runtime_sync_workshop.guardian_common import SCHEMA
        fixture = self.fixture()
        fixture.checkout()
        fixture.engine.abort(fixture.transaction_id)
        directory = fixture.engine.config.state_directory / "guardian-v2"
        directory.mkdir(parents=True, exist_ok=True)
        ident = "GRD2_" + "b" * 24
        path = directory / (ident + ".json")
        payload = {"schema": SCHEMA, "guardian_id": ident, "closed": False,
                   "patch_session": {"status": "OPEN"}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        original_seal = fixture.engine.seal_path.read_bytes()
        with patch.object(fixture.engine, "_guardian_enabled", return_value=True):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.seal()
            self.assertEqual(caught.exception.code, "GUARDIAN_SESSION_ACTIVE")
            self.assertEqual(fixture.engine.seal_path.read_bytes(), original_seal)
            payload["closed"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(fixture.engine.seal()["schema"], "runtime-sync-seal/v1")
        self.assertFalse(fixture.engine.lease_path.exists())

    def test_guardian_context_documents_fail_closed(self):
        fixture = self.fixture()
        fixture.checkout()
        fixture.engine.abort(fixture.transaction_id)
        with patch.dict(os.environ, {"WERKFADEN_GUARDIAN_REQUIRED": "1"}):
            with self.assertRaises(WorkshopError) as caught:
                fixture.engine.checkout(["app.py"], purpose="unproven context", documents=["docs/example.md"])
            self.assertEqual(caught.exception.code, "GUARDIAN_CONTEXT_DOCUMENTS_UNSUPPORTED")

    def test_characterization_real_workshop_apply_does_not_require_ai(self):
        # This passing native fixture proves the missing AI boundary, not AI approval.
        # All existing metadata/shadow/postcheck gates run normally.
        fixture = self.fixture()
        fixture.checkout()
        fixture.prepare_and_check()
        self.assertEqual(fixture.engine.verify(fixture.transaction_id)["state"], "SHADOW_VERIFIED")
        self.assertFalse(any("jev" in p.name.lower() for p in fixture.transaction.rglob("*.json")))
        result = fixture.engine.apply(fixture.transaction_id)
        self.assertEqual(result["state"], "POSTCHECK_VERIFIED")
        self.assertEqual((fixture.project / "app.py").read_bytes(), b"value = 2\n")


if __name__ == "__main__":
    unittest.main()
