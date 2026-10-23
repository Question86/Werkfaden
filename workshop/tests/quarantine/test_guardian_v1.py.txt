from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from runtime_sync_workshop.guardian import InvestigationGuardian  # noqa: E402
from runtime_sync_workshop.util import WorkshopError  # noqa: E402


class InvestigationGuardianTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[InvestigationGuardian, Path, Path]:
        state = root / ".state"
        state.mkdir()
        memory = root / "MEMORY.md"
        memory.write_text(
            "# Memory\n\nMEM.LOOP\nMEM.RULE.NO_GUESS\nMEM.RULE.LIVE_AUTH\nMEM.WORKSHOP\n",
            encoding="utf-8",
        )
        database = root / "kairos.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE source_inspection_receipts(
                    inspection_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                )
                """
            )
            receipt = {
                "schema": "kairos-source-inspection-receipt/v1",
                "inspection_id": "SIR_verified",
                "status": "VERIFIED",
                "scope_stable": True,
                "paths": ["runtime/example.cpp"],
                "match_count": 1,
                "created_at": "2026-09-16T00:00:00Z",
            }
            connection.execute(
                "INSERT INTO source_inspection_receipts VALUES(?,?,?)",
                ("SIR_verified", "VERIFIED", json.dumps(receipt)),
            )
            connection.commit()
        finally:
            connection.close()
        return (
            InvestigationGuardian(state_directory=state, kairos_database=database),
            memory,
            database,
        )

    def _advance_to_prove(
        self,
        guard: InvestigationGuardian,
        guardian_id: str,
        package: str,
    ) -> None:
        guard.advance(
            guardian_id,
            step="HYPOTHESIS",
            summary="The observed behavior violates one explicit project invariant.",
            memory_refs=["MEM.LOOP", "MEM.RULE.NO_GUESS"],
            evidence_refs=[],
            package_sha256=package,
        )
        for step, evidence in (
            ("FALSIFIER_1", "SRR_semantic"),
            ("FALSIFIER_2", "graph:structural-owner-path"),
            ("MAP", "graph:bounded-causal-surface"),
            ("COUNTERPROBE", "graph:no-competing-owner"),
        ):
            guard.advance(
                guardian_id,
                step=step,
                summary=f"{step} completed against the bounded project authority.",
                memory_refs=["MEM.LOOP", "MEM.RULE.LIVE_AUTH"],
                evidence_refs=[evidence],
                package_sha256=package,
            )
        guard.advance(
            guardian_id,
            step="EXACT_SOURCE",
            summary="Exact predicted source lines were inspected through a verified permit.",
            memory_refs=["MEM.LOOP", "MEM.RULE.NO_GUESS"],
            evidence_refs=["SIR_verified"],
            package_sha256=package,
        )
        guard.advance(
            guardian_id,
            step="PROVE",
            summary="Observed defect, violated invariant, causal owner and exact scope are evidenced.",
            memory_refs=["MEM.LOOP", "MEM.RULE.LIVE_AUTH"],
            evidence_refs=["proof:causal-chain"],
            package_sha256=package,
            sources=["runtime/example.cpp"],
        )

    def test_guardian_enforces_order_and_freezes_patch_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guard, memory, _ = self._fixture(root)
            package = "a" * 64
            started = guard.start(
                problem="Human observed a reproducible implementation mismatch.",
                memory_file=memory,
                memory_refs=["MEM.LOOP"],
                package_sha256=package,
            )
            guardian_id = started["guardian_id"]

            with self.assertRaises(WorkshopError) as caught:
                guard.advance(
                    guardian_id,
                    step="MAP",
                    summary="This must not be accepted because earlier states were skipped.",
                    memory_refs=["MEM.LOOP"],
                    evidence_refs=["graph:any"],
                    package_sha256=package,
                )
            self.assertEqual(caught.exception.code, "GUARDIAN_STEP_ORDER")

            self._advance_to_prove(guard, guardian_id, package)

            with self.assertRaises(WorkshopError) as caught:
                guard.assert_checkout(
                    guardian_id,
                    sources=["runtime/other.cpp"],
                    package_sha256=package,
                )
            self.assertEqual(caught.exception.code, "GUARDIAN_SCOPE_MISMATCH")

            authorized = guard.assert_checkout(
                guardian_id,
                sources=["runtime/example.cpp"],
                package_sha256=package,
            )
            self.assertEqual(authorized["next_step"], "PATCH")

            bound = guard.bind_checkout(
                guardian_id,
                transaction_id="TXN_0123456789abcdef01234567",
                package_sha256=package,
                memory_refs=["MEM.LOOP", "MEM.WORKSHOP"],
                purpose="Repair the proven general mechanism only.",
            )
            self.assertEqual(bound["current_step"], "PATCH")
            self.assertEqual(bound["next_step"], "WORKSHOP")

            changed_package = "d" * 64
            workshop = guard.advance(
                guardian_id,
                step="WORKSHOP",
                summary="The bound Workshop transaction completed its governed verification path.",
                memory_refs=["MEM.WORKSHOP"],
                evidence_refs=["TXN_0123456789abcdef01234567"],
                package_sha256=changed_package,
            )
            self.assertEqual(workshop["next_step"], "HEARTBEAT")
            heartbeat = guard.advance(
                guardian_id,
                step="HEARTBEAT",
                summary="The post-patch heartbeat verified the promoted project state.",
                memory_refs=["MEM.LOOP"],
                evidence_refs=["HB_verified"],
                package_sha256=changed_package,
            )
            self.assertEqual(heartbeat["next_step"], "FRESH_RUN")
            closed = guard.advance(
                guardian_id,
                step="FRESH_RUN",
                summary="A fresh Runtime run produced the next evidence set and closes this investigation.",
                memory_refs=["MEM.LOOP"],
                evidence_refs=["RUN_fresh"],
                package_sha256=changed_package,
            )
            self.assertTrue(closed["closed"])

    def test_exact_source_requires_real_verified_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guard, memory, _ = self._fixture(root)
            package = "b" * 64
            guardian_id = guard.start(
                problem="Human observed a source-level mismatch that must be localized.",
                memory_file=memory,
                memory_refs=["MEM.LOOP"],
                package_sha256=package,
            )["guardian_id"]

            guard.advance(
                guardian_id,
                step="HYPOTHESIS",
                summary="One falsifiable mechanism explains the observed mismatch.",
                memory_refs=["MEM.LOOP"],
                evidence_refs=[],
                package_sha256=package,
            )
            for step in ("FALSIFIER_1", "FALSIFIER_2", "MAP", "COUNTERPROBE"):
                guard.advance(
                    guardian_id,
                    step=step,
                    summary=f"{step} produced bounded evidence for the same hypothesis.",
                    memory_refs=["MEM.LOOP"],
                    evidence_refs=[f"evidence:{step}"],
                    package_sha256=package,
                )

            with self.assertRaises(WorkshopError) as caught:
                guard.advance(
                    guardian_id,
                    step="EXACT_SOURCE",
                    summary="A made-up source receipt must not authorize exact source.",
                    memory_refs=["MEM.LOOP"],
                    evidence_refs=["SIR_missing"],
                    package_sha256=package,
                )
            self.assertEqual(caught.exception.code, "GUARDIAN_SOURCE_RECEIPT_MISSING")

    def test_memory_or_package_drift_forces_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guard, memory, _ = self._fixture(root)
            package = "c" * 64
            guardian_id = guard.start(
                problem="Human observed a stable mismatch under the current sealed package.",
                memory_file=memory,
                memory_refs=["MEM.LOOP"],
                package_sha256=package,
            )["guardian_id"]

            memory.write_text(memory.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            with self.assertRaises(WorkshopError) as caught:
                guard.status(guardian_id)
            self.assertEqual(caught.exception.code, "GUARDIAN_MEMORY_DRIFT")


if __name__ == "__main__":
    unittest.main()
