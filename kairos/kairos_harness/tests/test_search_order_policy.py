from __future__ import annotations

import json
import unittest

from kairos.cli import build_parser, execute
from kairos.database import KnowledgeDatabase
from kairos.heartbeat import run_heartbeat
from kairos.search_policy import (
    SearchPolicyError,
    execute_source_search,
    issue_source_permit,
)
from kairos.util import atomic_write_json, atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


GRAPH_POLICY_ID = "CODE_GRAPH_POLICY_ROUTE_L0001_V01"
GRAPH_POLICY_ASSET = "policy/route_only.json"
GRAPH_POLICY_DOCUMENT = f"""+++
schema = "kairos-context/v1"
id = "{GRAPH_POLICY_ID}"
type = "code"
revision = 1
state = "ready"
authority = "implementation_documentation"
workspace = "KAIROS_TEST"
route = "GOAL_KAIROS_001/MILESTONE_KAIROS_01/TASK_0001/{GRAPH_POLICY_ID}"
loop = 1
task = "TASK_0001"
goal = "GOAL_KAIROS_001"
milestone = "MILESTONE_KAIROS_01"
updated_at = "2026-01-01T00:00:00Z"
capsule = "Graph policy fixture with an identity isolated to normalized metadata."
claim_boundary = "This fixture proves routed policy behavior only."
entities = ["{GRAPH_POLICY_ID}"]
facets = ["fixture", "graph-search-policy"]
criteria = []
does_not_answer = ["real implementation behaviour"]

[[answers]]
intent = "implementation_location"
question = "Where is graph policy evidence documented?"
target = "s-evidence"

[[artifacts]]
id = "{GRAPH_POLICY_ASSET}"
role = "output"
operation = "commit"
producer_or_consumer = "policy/producer_v1.cpp"
schema_or_type = "policy_output_v1"
hash_bound = true
commit_bound = true
evidence_target = "s-evidence"
+++
# {GRAPH_POLICY_ID}: graph policy fixture

<a id="s-evidence"></a>
## EVIDENCE

> Capsule: Graph policy evidence without searchable identity prose.

Graph policy evidence is available at this declared section.
"""


class SearchOrderPolicyTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))
        self.probe = self.workspace / "docs" / "implementation_probe.py"
        atomic_write_text(
            self.probe,
            "def governed_probe():\n    return 'metadata first source second'\n",
        )

    def _route(self, *, no_refresh: bool = False) -> dict:
        argv = [
            "search",
            "--workspace",
            str(self.workspace),
            "Which implementation verifies KAIROS metadata promotion?",
            "--mode",
            "depth",
        ]
        if no_refresh:
            argv.append("--no-refresh")
        payload, code = execute(build_parser().parse_args(argv))
        self.assertEqual(code, 0)
        return payload["routing_receipt"]

    def _normal_permit(self, receipt_id: str) -> dict:
        return issue_source_permit(
            self.workspace,
            self.database,
            routing_receipt_id=receipt_id,
            purpose="implementation_verification",
            paths=["docs/implementation_probe.py"],
            patterns=["metadata first"],
            reason="Verify the exact implementation phrase identified after metadata routing.",
        )

    def _install_graph_policy_fixture(self) -> str:
        relative = f"code/{GRAPH_POLICY_ID}.md"
        atomic_write_text(self.workspace / relative, GRAPH_POLICY_DOCUMENT)
        heartbeat = run_heartbeat(
            self.workspace,
            requested_mode="work",
            changed_paths=[relative],
        )
        self.assertTrue(heartbeat["verified"])
        return relative

    def _violation_count(self) -> int:
        connection = self.database.connect(read_only=True)
        try:
            return int(connection.execute("SELECT count(*) FROM search_policy_violations").fetchone()[0])
        finally:
            connection.close()

    def test_cli_metadata_search_records_scoped_durable_routing_receipt(self) -> None:
        receipt = self._route()
        self.assertEqual(receipt["status"], "ROUTED")
        self.assertEqual(receipt["task_id"], "TASK_0001")
        self.assertEqual(receipt["criterion_id"], "CRIT_KAIROS_001")
        self.assertTrue(receipt["freshness"]["checked"])
        self.assertIsNotNone(receipt["primary"])
        self.assertNotIn("graph_route", receipt)
        connection = self.database.connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT query_text,primary_path FROM search_routing_receipts WHERE routing_receipt_id=?",
                (receipt["routing_receipt_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row["query_text"], receipt["query"])
        self.assertEqual(row["primary_path"], receipt["primary"]["path"])

    def test_cli_graph_route_preserves_freshness_receipt_and_one_use_permit(self) -> None:
        relative = self._install_graph_policy_fixture()
        payload, code = execute(
            build_parser().parse_args(
                [
                    "search",
                    "--workspace",
                    str(self.workspace),
                    f"Who produces {GRAPH_POLICY_ASSET}?",
                    "--mode",
                    "work",
                ]
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["primary"]["artifact_id"], GRAPH_POLICY_ID)
        receipt = payload["routing_receipt"]
        self.assertEqual(receipt["status"], "ROUTED")
        self.assertTrue(receipt["freshness"]["checked"])
        self.assertEqual(receipt["graph_route"]["seed_total"], 1)
        self.assertEqual(len(receipt["graph_route"]["primary_anchors"]), 1)
        self.assertNotIn("facts", receipt["graph_route"])

        permit = issue_source_permit(
            self.workspace,
            self.database,
            routing_receipt_id=receipt["routing_receipt_id"],
            purpose="implementation_verification",
            paths=[relative],
            patterns=["Graph policy evidence"],
            reason="Verify the exact source section selected through normalized graph routing.",
        )
        inspected = execute_source_search(
            self.workspace,
            self.database,
            permit_id=permit["permit_id"],
        )
        self.assertEqual(inspected["status"], "VERIFIED")
        self.assertGreater(inspected["match_count"], 0)
        with self.assertRaises(SearchPolicyError):
            execute_source_search(
                self.workspace,
                self.database,
                permit_id=permit["permit_id"],
            )

    def test_no_refresh_graph_route_remains_diagnostic_and_cannot_authorize_source(self) -> None:
        relative = self._install_graph_policy_fixture()
        payload, code = execute(
            build_parser().parse_args(
                [
                    "search",
                    "--workspace",
                    str(self.workspace),
                    f"Who produces {GRAPH_POLICY_ASSET}?",
                    "--mode",
                    "work",
                    "--no-refresh",
                ]
            )
        )
        self.assertEqual(code, 0)
        receipt = payload["routing_receipt"]
        self.assertEqual(receipt["status"], "STALE_DIAGNOSTIC")
        self.assertEqual(receipt["graph_route"]["seed_total"], 1)
        with self.assertRaises(SearchPolicyError):
            issue_source_permit(
                self.workspace,
                self.database,
                routing_receipt_id=receipt["routing_receipt_id"],
                purpose="implementation_verification",
                paths=[relative],
                patterns=["Graph policy evidence"],
                reason="A stale diagnostic graph route must not authorize source inspection.",
            )

    def test_routed_permit_allows_one_bounded_source_search_and_rejects_replay(self) -> None:
        route = self._route()
        permit = self._normal_permit(route["routing_receipt_id"])
        result = execute_source_search(
            self.workspace,
            self.database,
            permit_id=permit["permit_id"],
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["matches"][0]["path"], "docs/implementation_probe.py")
        before = self._violation_count()
        with self.assertRaises(SearchPolicyError):
            execute_source_search(
                self.workspace,
                self.database,
                permit_id=permit["permit_id"],
            )
        self.assertEqual(self._violation_count(), before + 1)

    def test_broad_stale_foreign_and_reexchanged_routes_fail_closed(self) -> None:
        route = self._route()
        before = self._violation_count()
        with self.assertRaises(SearchPolicyError):
            issue_source_permit(
                self.workspace,
                self.database,
                routing_receipt_id=route["routing_receipt_id"],
                purpose="exact_location",
                paths=["."],
                patterns=["KAIROS"],
                reason="Attempt an intentionally broad project-root search for the negative test.",
            )
        self.assertEqual(self._violation_count(), before + 1)

        permit = self._normal_permit(route["routing_receipt_id"])
        with self.assertRaises(SearchPolicyError):
            self._normal_permit(route["routing_receipt_id"])

        second_route = self._route()
        old = "2000-01-01T00:00:00Z"
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT receipt_json FROM search_routing_receipts WHERE routing_receipt_id=?",
                (second_route["routing_receipt_id"],),
            ).fetchone()
            payload = json.loads(row["receipt_json"])
            payload["created_at"] = old
            connection.execute(
                "UPDATE search_routing_receipts SET created_at=?,receipt_json=? WHERE routing_receipt_id=?",
                (old, json.dumps(payload, sort_keys=True), second_route["routing_receipt_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SearchPolicyError):
            self._normal_permit(second_route["routing_receipt_id"])

        third_route = self._route()
        runtime_path = self.workspace / ".kairos" / "runtime_state.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["active_task"] = "TASK_FOREIGN"
        runtime["active_criterion"] = "CRIT_FOREIGN"
        atomic_write_json(runtime_path, runtime)
        with self.assertRaises(SearchPolicyError):
            self._normal_permit(third_route["routing_receipt_id"])
        self.assertEqual(permit["status"], "ACTIVE")

    def test_stale_diagnostic_route_cannot_authorize_source_escalation(self) -> None:
        route = self._route(no_refresh=True)
        self.assertEqual(route["status"], "STALE_DIAGNOSTIC")
        with self.assertRaises(SearchPolicyError):
            self._normal_permit(route["routing_receipt_id"])

    def test_changed_source_scope_revokes_permit_before_read(self) -> None:
        route = self._route()
        permit = self._normal_permit(route["routing_receipt_id"])
        atomic_write_text(
            self.probe,
            "def governed_probe():\n    return 'changed after permit'\n",
        )
        with self.assertRaises(SearchPolicyError):
            execute_source_search(
                self.workspace,
                self.database,
                permit_id=permit["permit_id"],
            )
        connection = self.database.connect(read_only=True)
        try:
            status = connection.execute(
                "SELECT status FROM source_inspection_permits WHERE permit_id=?",
                (permit["permit_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "REVOKED")

    def test_exact_file_and_metadata_repair_exceptions_are_narrow_and_recorded(self) -> None:
        exact = issue_source_permit(
            self.workspace,
            self.database,
            routing_receipt_id=None,
            purpose="exact_file_request",
            paths=["docs/implementation_probe.py"],
            patterns=["governed_probe"],
            reason="The user explicitly requested this exact file and symbol for direct inspection.",
        )
        self.assertEqual(exact["exception"], "exact_file_request")
        exact_result = execute_source_search(
            self.workspace,
            self.database,
            permit_id=exact["permit_id"],
        )
        self.assertEqual(exact_result["exception"], "exact_file_request")

        repair = issue_source_permit(
            self.workspace,
            self.database,
            routing_receipt_id=None,
            purpose="metadata_repair",
            paths=["docs/implementation_probe.py"],
            patterns=["metadata first"],
            reason="Metadata health identified this exact source as the bounded repair target.",
        )
        self.assertEqual(repair["exception"], "metadata_repair")
        with self.assertRaises(SearchPolicyError):
            issue_source_permit(
                self.workspace,
                self.database,
                routing_receipt_id=None,
                purpose="exact_file_request",
                paths=["docs"],
                patterns=["KAIROS"],
                reason="The negative test attempts to disguise a directory as an exact file request.",
            )

    def test_expired_permit_is_terminal_and_records_violation(self) -> None:
        route = self._route()
        permit = self._normal_permit(route["routing_receipt_id"])
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE source_inspection_permits SET expires_at=? WHERE permit_id=?",
                ("2000-01-01T00:00:00Z", permit["permit_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        before = self._violation_count()
        with self.assertRaises(SearchPolicyError):
            execute_source_search(
                self.workspace,
                self.database,
                permit_id=permit["permit_id"],
            )
        connection = self.database.connect(read_only=True)
        try:
            status = connection.execute(
                "SELECT status FROM source_inspection_permits WHERE permit_id=?",
                (permit["permit_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "EXPIRED")
        self.assertEqual(self._violation_count(), before + 1)

    def test_unscoped_runtime_cannot_issue_even_an_exception_permit(self) -> None:
        runtime_path = self.workspace / ".kairos" / "runtime_state.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["active_task"] = None
        runtime["active_criterion"] = None
        atomic_write_json(runtime_path, runtime)

        before = self._violation_count()
        with self.assertRaises(SearchPolicyError):
            issue_source_permit(
                self.workspace,
                self.database,
                routing_receipt_id=None,
                purpose="exact_file_request",
                paths=["docs/implementation_probe.py"],
                patterns=["governed_probe"],
                reason="An exception still requires a governed active task and criterion.",
            )
        self.assertEqual(self._violation_count(), before + 1)

    def test_source_scope_cannot_escape_inspection_root(self) -> None:
        route = self._route()
        before = self._violation_count()
        with self.assertRaises(SearchPolicyError):
            issue_source_permit(
                self.workspace,
                self.database,
                routing_receipt_id=route["routing_receipt_id"],
                purpose="negative_proof",
                paths=["../outside.py"],
                patterns=["secret"],
                reason="Adversarial path traversal must fail before any source read.",
            )
        self.assertEqual(self._violation_count(), before + 1)

    def test_missing_database_uses_explicit_bounded_file_backed_metadata_repair(self) -> None:
        database_file = database_path(self.workspace)
        database_file.unlink()
        permit_args = build_parser().parse_args(
            [
                "source-permit",
                "--workspace",
                str(self.workspace),
                "--purpose",
                "metadata_repair",
                "--path",
                "docs/implementation_probe.py",
                "--pattern",
                "metadata first",
                "--reason",
                "The metadata database is absent; inspect only this exact repair source.",
                "--metadata-error",
                "SQLite database file is missing",
            ]
        )
        permit, code = execute(permit_args)
        self.assertEqual(code, 0)
        self.assertTrue(permit["permit_id"].startswith("EMR_"))
        self.assertEqual(permit["storage"], "bounded-file-fallback")
        self.assertFalse(database_file.exists())
        result, code = execute(
            build_parser().parse_args(
                [
                    "source-search",
                    "--workspace",
                    str(self.workspace),
                    "--permit",
                    permit["permit_id"],
                ]
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["match_count"], 1)
        self.assertFalse(database_file.exists())


if __name__ == "__main__":
    unittest.main()
