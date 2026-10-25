"""The two surfaces an operator needs to get from a symptom to its cause.

A caller reads a name off a log or a stack trace, where it is bare, and asks the graph
about it. The graph stores qualified identities, so the bare name matches nothing. These
tests pin the way out of that: name the canonical forms instead of guessing one, and walk
the declared edges from there.
"""
from __future__ import annotations

from kairos.database import KnowledgeDatabase
from kairos.graph import (
    GraphError,
    asset_graph,
    graph_chase,
    inventory,
    node_graph,
    predicate_graph,
    resolve_identity,
)
from kairos.workspace import database_path

from support import WorkspaceTestCase


RELATIONS = (
    # artifact_id, ordinal, subject, predicate, object, object_kind, scope, evidence_target
    ("CODE_A", 0, "runtime/owner.cpp", "gates", "FAIL_STAGE_ORDER", "failure", "order", "s-failures"),
    ("CODE_A", 1, "runtime/owner.cpp", "depends_on", "runtime/intake/probe.h", "header", "", "s-deps"),
    ("CODE_A", 2, "app::runtime::owner::RunOwnerV1", "calls",
     "app::runtime::intake::BuildProbeV1", "symbol", "", "s-exec"),
    ("CODE_B", 0, "runtime/intake/probe.cpp", "defines", "runtime/intake/probe.h",
     "header", "", "s-symbols"),
    ("CODE_B", 1, "app::runtime::intake::BuildProbeV1", "reads", "canonical/seed.json",
     "artifact", "", "s-exec"),
)


class GraphNavigationTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database = KnowledgeDatabase(database_path(self.workspace))
        self.database.initialize()
        self.connection = self.database.connect()
        for artifact_id in ("CODE_A", "CODE_B", "CODE_C"):
            self.connection.execute(
                "INSERT INTO artifacts(artifact_id,path,document_type,state,authority,"
                "workspace_id,route,revision,updated_at,capsule,claim_boundary,"
                "content_sha256,header_bytes,metadata_json,promoted_at) "
                "VALUES(?,?,'code','ready','implementation_documentation','KAIROS_TEST',"
                "?,1,'2026-08-31T00:00:00Z','c','b','0',0,'{}','2026-08-31T00:00:00Z')",
                (artifact_id, f"code/{artifact_id.lower()}.md", f"R/{artifact_id}"),
            )
        for row in RELATIONS:
            self.connection.execute(
                "INSERT INTO graph_relations(artifact_id,ordinal,subject,predicate,object,"
                "object_kind,scope,evidence_target,evidence) VALUES(?,?,?,?,?,?,?,?,'')",
                row,
            )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()
        super().tearDown()

    def test_tx_recycling_atoms_are_queryable_through_supported_graph_inventory(self):
        self.connection.execute(
            "INSERT INTO tx_recycling_atoms("
            "artifact_id,unit_id,path,baseline_sha256,candidate_sha256,"
            "disposition,reason,evidence_json,source_transaction,successor_transaction,protected)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("CODE_A", "FILE_CHANGE_TEST", "runtime/parser.cpp", "a" * 64,
             "b" * 64, "REJECT_FOR_REUSE",
             "Old approach introduced a verified regression in the tested parser path",
             "[]", "TXN_" + "1" * 24, "", 0),
        )
        self.connection.commit()
        result = inventory(self.connection, "tx_atoms",
                           field="disposition", value="REJECT_FOR_REUSE", limit=10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["unit_id"], "FILE_CHANGE_TEST")

    def test_node_miss_on_a_bare_symbol_names_the_qualified_identity(self):
        """An empty lookup must not read like "the corpus does not know this"."""
        result = node_graph(self.connection, "BuildProbeV1", limit=10)
        self.assertEqual(0, result["outgoing"]["total"])
        hint = result["no_match"]
        self.assertIn("--resolve", hint["hint"])
        self.assertIn(
            "app::runtime::intake::BuildProbeV1",
            [c["identity"] for c in hint["candidates"]],
        )

    def test_node_miss_on_an_unknown_name_says_so_without_candidates(self):
        result = node_graph(self.connection, "NoSuchSymbolAnywhereV9", limit=10)
        hint = result["no_match"]
        self.assertEqual([], hint["candidates"])
        self.assertIn("does not mention", hint["hint"])

    def test_node_hit_carries_no_hint(self):
        result = node_graph(self.connection, "runtime/owner.cpp", limit=10)
        self.assertNotIn("no_match", result)

    def test_asset_miss_on_a_bare_file_name_names_the_declared_path(self):
        result = asset_graph(self.connection, "seed.json", limit=10)
        self.assertIn(
            "canonical/seed.json",
            [c["identity"] for c in result["no_match"]["candidates"]],
        )

    def test_predicate_miss_lists_the_closed_vocabulary(self):
        result = predicate_graph(self.connection, "invokes", limit=10)
        self.assertEqual(0, result["total"])
        self.assertIn("gates", result["no_match"]["declared"])
        self.assertNotIn("candidates", result["no_match"])

    def test_bare_symbol_resolves_to_its_qualified_identity(self):
        result = resolve_identity(self.connection, "BuildProbeV1")
        identities = [c["identity"] for c in result["candidates"]]
        self.assertIn("app::runtime::intake::BuildProbeV1", identities)
        # the caller asked with a name the graph does not store, and is told so
        self.assertIsNone(result["exact_identity"])

    def test_bare_file_name_resolves_to_its_path(self):
        result = resolve_identity(self.connection, "probe.h")
        identities = [c["identity"] for c in result["candidates"]]
        self.assertIn("runtime/intake/probe.h", identities)

    def test_resolution_reports_every_candidate_instead_of_choosing(self):
        self.connection.execute(
            "INSERT INTO graph_relations(artifact_id,ordinal,subject,predicate,object,"
            "object_kind,scope,evidence_target,evidence) VALUES(?,?,?,?,?,?,?,?,'')",
            ("CODE_C", 0, "app::runtime::worker::BuildProbeV1", "calls",
             "runtime/worker/x.h", "header", "", "s-exec"),
        )
        self.connection.commit()
        result = resolve_identity(self.connection, "BuildProbeV1")
        identities = {c["identity"] for c in result["candidates"]}
        self.assertEqual(
            identities,
            {"app::runtime::intake::BuildProbeV1", "app::runtime::worker::BuildProbeV1"},
        )
        self.assertEqual(result["total"], 2)

    def test_absent_name_resolves_to_nothing_rather_than_a_guess(self):
        result = resolve_identity(self.connection, "NoSuchSymbolV9")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["total"], 0)

    def test_chase_walks_from_a_failure_back_to_what_gates_it(self):
        chase = graph_chase(self.connection, ("FAIL_STAGE_ORDER",), max_hops=2, limit=50)
        first = chase["hops"][0]["edges"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["subject"], "runtime/owner.cpp")
        self.assertEqual(first[0]["predicate"], "gates")
        self.assertEqual(first[0]["direction"], "incoming")
        reached = {e["object"] for hop in chase["hops"] for e in hop["edges"]}
        self.assertIn("runtime/intake/probe.h", reached)

    def test_an_edge_belongs_to_one_hop_only(self):
        chase = graph_chase(self.connection, ("FAIL_STAGE_ORDER",), max_hops=4, limit=200)
        keys = [
            (e["artifact_id"], e["subject"], e["predicate"], e["object"])
            for hop in chase["hops"] for e in hop["edges"]
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_predicate_filter_narrows_the_walk(self):
        chase = graph_chase(self.connection, ("runtime/owner.cpp",), max_hops=2,
                            limit=50, predicates=("gates",))
        predicates = {e["predicate"] for hop in chase["hops"] for e in hop["edges"]}
        self.assertEqual(predicates, {"gates"})

    def test_a_truncated_hop_says_how_much_it_left_behind(self):
        chase = graph_chase(self.connection, ("runtime/owner.cpp",), max_hops=1, limit=1)
        hop = chase["hops"][0]
        self.assertTrue(hop["truncated"])
        self.assertGreater(hop["edge_total"], hop["edge_read"])
        self.assertTrue(chase["truncated"])

    def test_hop_bound_is_enforced(self):
        with self.assertRaises(GraphError):
            graph_chase(self.connection, ("FAIL_STAGE_ORDER",), max_hops=99)

    def test_no_seed_is_an_empty_walk_not_an_error(self):
        chase = graph_chase(self.connection, (), max_hops=2)
        self.assertEqual(chase["hops"], [])
        self.assertEqual(chase["edges_returned"], 0)

    def test_inventory_returns_a_whole_table_with_its_own_denominator(self):
        report = inventory(self.connection, "relations", limit=2)
        self.assertEqual(report["total"], len(RELATIONS))
        self.assertEqual(report["returned"], 2)
        self.assertTrue(report["truncated"])
        self.assertEqual(report["documents"], 2)
        self.assertEqual(report["grouped_by"], "predicate")
        self.assertEqual({g["value"] for g in report["groups"]},
                         {r[3] for r in RELATIONS})

    def test_inventory_narrows_to_one_column_value(self):
        report = inventory(self.connection, "relations", field="predicate",
                           value="depends_on", limit=10)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["rows"][0]["object"], "runtime/intake/probe.h")

    def test_inventory_names_the_columns_it_accepts(self):
        with self.assertRaises(GraphError) as caught:
            inventory(self.connection, "relations", field="nonsense", value="x")
        self.assertIn("predicate", str(caught.exception))

    def test_inventory_refuses_an_unknown_table(self):
        with self.assertRaises(GraphError):
            inventory(self.connection, "everything")
