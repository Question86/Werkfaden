from __future__ import annotations

import json

from kairos.database import KnowledgeDatabase
from kairos.graph import (
    artifact_graph,
    asset_graph,
    integrity_report,
    vocabulary_report,
)
from kairos.promoter import promote_document
from kairos.query import compile_query
from kairos.search import search_database
from kairos.search_policy import record_routing_receipt
from kairos.util import atomic_write_text
from kairos.workspace import database_path

from support import WorkspaceTestCase


ARTIFACT_ID = "CODE_GRAPH_FIXTURE_L0001_V01"

# A graph-bearing document is written literally: KAIROS never renders one, because the
# renderer refuses a header it would have to drop the graph layer from. The fixture carries
# one deviating contract kind and one anchor that names no section, so both reporting paths
# have something to find.
GRAPH_DOCUMENT = f"""+++
schema = "kairos-context/v1"
id = "{ARTIFACT_ID}"
type = "code"
revision = 1
state = "ready"
authority = "implementation_documentation"
workspace = "KAIROS_TEST"
route = "GOAL_KAIROS_001/MILESTONE_KAIROS_01/TASK_0001/{ARTIFACT_ID}"
loop = 1
task = "TASK_0001"
goal = "GOAL_KAIROS_001"
milestone = "MILESTONE_KAIROS_01"
updated_at = "2026-01-01T00:00:00Z"
capsule = "Fixture module that defines one symbol and commits one artifact."
claim_boundary = "This fixture proves projection and reporting only; it describes no real implementation."
entities = ["{ARTIFACT_ID}", "fixture/module_v1.cpp"]
facets = ["fixture", "graph-projection"]
criteria = []
does_not_answer = ["real implementation behaviour"]

[[answers]]
intent = "implementation_location"
question = "Where is the fixture behaviour implemented?"
target = "s-symbols"

[[answers]]
intent = "graph_context"
question = "Which modules, contracts and artifacts connect to this fixture?"
target = "s-graph-metadata"

[refs]
parent = "[ref:tasks/task_TASK_0001.md#s-objective|id:TASK_0001|v:dynamic|rel:implements|tags:fixture,task|src:declared]"

[[search_contract]]
query = "Where is the fixture behaviour implemented?"
expected = "{ARTIFACT_ID}#s-symbols"
required_top_k = 5

[[relations]]
subject = "{ARTIFACT_ID}"
predicate = "defines"
object = "fixture/module_v1.cpp"
object_kind = "source"
scope = "fixture-owner"
evidence_target = "s-symbols"
evidence = "direct"

[[relations]]
subject = "fixture/module_v1.cpp"
predicate = "writes"
object = "fixture/output.json"
object_kind = "artifact"
scope = "fixture-output"
evidence_target = "s-nowhere"
evidence = "direct"

[[contracts]]
id = "fixture_ordering"
kind = "determinism"
subject = "fixture/module_v1.cpp"
statement = "Rows are emitted in declared order."
failure_or_effect = "FAIL_FIXTURE_ORDER"
evidence_target = "s-symbols"

[[artifacts]]
id = "fixture/output.json"
role = "output"
operation = "commit"
producer_or_consumer = "WriteFixtureOutputV1"
schema_or_type = "fixture_output_v1"
hash_bound = true
commit_bound = false
evidence_target = "s-symbols"
+++
# {ARTIFACT_ID}: fixture module

## CONTEXT INDEX

- [`s-symbols`](#s-symbols) - The single public entry point.
- [`s-graph-metadata`](#s-graph-metadata) - Normalized graph facts.

<a id="s-symbols"></a>
## PUBLIC SYMBOLS

> Capsule: The single public entry point of the fixture module.

WriteFixtureOutputV1 commits fixture/output.json.

<a id="s-graph-metadata"></a>
## GRAPH METADATA

> Capsule: Normalized graph facts for this fixture.

The normalized structures are declared in the header of this document.
"""


SEARCH_PRODUCER_ID = "CODE_GRAPH_SEARCH_PRODUCER_L0001_V01"
SEARCH_CONSUMER_ID = "CODE_GRAPH_SEARCH_CONSUMER_L0001_V01"
SEARCH_ASSET = "fixture/route_only_output.json"
SEARCH_DANGLING = "zzqdangling908172.datx"

SEARCH_PRODUCER_DOCUMENT = f"""+++
schema = "kairos-context/v1"
id = "{SEARCH_PRODUCER_ID}"
type = "code"
revision = 1
state = "ready"
authority = "implementation_documentation"
workspace = "KAIROS_TEST"
route = "GOAL_KAIROS_001/MILESTONE_KAIROS_01/TASK_0001/{SEARCH_PRODUCER_ID}"
loop = 1
task = "TASK_0001"
goal = "GOAL_KAIROS_001"
milestone = "MILESTONE_KAIROS_01"
updated_at = "2026-01-01T00:00:00Z"
capsule = "Graph-search producer fixture with isolated metadata-only identities."
claim_boundary = "This fixture proves graph-aware retrieval only."
entities = ["{SEARCH_PRODUCER_ID}"]
facets = ["fixture", "graph-search"]
criteria = []
does_not_answer = ["real implementation behaviour"]

[[answers]]
intent = "implementation_location"
question = "Where is the isolated producer fixture documented?"
target = "s-output"

[[relations]]
subject = "fixture/producer_v1.cpp"
predicate = "commits"
object = "{SEARCH_ASSET}"
object_kind = "artifact"
scope = "isolated-output"
evidence_target = "s-output"
evidence = "direct"

[[relations]]
subject = "fixture/producer_v1.cpp"
predicate = "produces"
object = "PASS"
object_kind = "concept"
scope = "isolated-terminal-node"
evidence_target = "s-output"
evidence = "direct"

[[contracts]]
id = "fixture_failure_pair"
kind = "invariant"
subject = "fixture/producer_v1.cpp"
statement = "The isolated fixture carries three exact failure literals."
failure_or_effect = "FAIL_FIXTURE_ALPHA or FAIL_FIXTURE_BETA or E_X"
evidence_target = "s-contracts"

[[contracts]]
id = "fixture_extended_failure_ids"
kind = "invariant"
subject = "fixture/producer_v1.cpp"
statement = "Extended failure identifiers must preserve their complete token boundaries."
failure_or_effect = "FAIL_EXTENDED-BAR or FAIL_DOTTED.VALUE or FAIL_SCOPED:VALUE"
evidence_target = "s-contracts"

[[artifacts]]
id = "{SEARCH_ASSET}"
role = "output"
operation = "commit"
producer_or_consumer = "fixture/producer_v1.cpp"
schema_or_type = "fixture_output_v1"
hash_bound = true
commit_bound = true
evidence_target = "s-output"

[[drift_records]]
id = "D1"
historical = "HIST_GRAPH_SEARCH_PRODUCER"
subject = "fixture/producer_v1.cpp"
classification = "implementation_drift"
summary = "The fixture retains one measured unresolved difference."
current_evidence_target = "s-drift"
status = "unresolved"
+++
# {SEARCH_PRODUCER_ID}: graph-search producer fixture

<a id="s-output"></a>
## OUTPUT

> Capsule: Producer evidence without searchable body literals.

This module reads and verifies unrelated generic inputs; the normalized header owns the isolated output fact.

<a id="s-contracts"></a>
## CONTRACTS

> Capsule: Contract evidence without searchable body literals.

The normalized header owns the isolated failure contract.

<a id="s-drift"></a>
## DRIFT

> Capsule: Drift evidence without searchable body literals.

The normalized header owns the isolated drift record.
"""

SEARCH_CONSUMER_DOCUMENT = f"""+++
schema = "kairos-context/v1"
id = "{SEARCH_CONSUMER_ID}"
type = "code"
revision = 1
state = "ready"
authority = "implementation_documentation"
workspace = "KAIROS_TEST"
route = "GOAL_KAIROS_001/MILESTONE_KAIROS_01/TASK_0001/{SEARCH_CONSUMER_ID}"
loop = 1
task = "TASK_0001"
goal = "GOAL_KAIROS_001"
milestone = "MILESTONE_KAIROS_01"
updated_at = "2026-01-01T00:00:00Z"
capsule = "Graph-search consumer fixture with isolated metadata-only identities."
claim_boundary = "This fixture proves graph-aware retrieval only."
entities = ["{SEARCH_CONSUMER_ID}"]
facets = ["fixture", "graph-search"]
criteria = []
does_not_answer = ["real implementation behaviour"]

[[answers]]
intent = "implementation_location"
question = "Where is the isolated consumer fixture documented?"
target = "s-input"

[[relations]]
subject = "fixture/consumer_v1.cpp"
predicate = "reads"
object = "{SEARCH_ASSET}"
object_kind = "artifact"
scope = "isolated-input"
evidence_target = "s-input"
evidence = "direct"

[[relations]]
subject = "fixture/consumer_v1.cpp"
predicate = "reads"
object = "{SEARCH_ASSET}"
object_kind = "artifact"
scope = "isolated-input-verification"
evidence_target = "s-input"
evidence = "direct"

[[relations]]
subject = "fixture/consumer_v1.cpp"
predicate = "reads"
object = "{SEARCH_DANGLING}"
object_kind = "artifact"
scope = "dangling-only"
evidence_target = "s-nowhere"
evidence = "direct"

[[artifacts]]
id = "{SEARCH_ASSET}"
role = "input"
operation = "read"
producer_or_consumer = "fixture/consumer_v1.cpp"
schema_or_type = "fixture_input_v1"
hash_bound = true
commit_bound = false
evidence_target = "s-input"

[[drift_records]]
id = "D1"
historical = "HIST_GRAPH_SEARCH_CONSUMER"
subject = "fixture/consumer_v1.cpp"
classification = "implementation_drift"
summary = "A second D1 record exercises multi-identifier specificity before SQL limits."
current_evidence_target = "s-input"
status = "unresolved"
+++
# {SEARCH_CONSUMER_ID}: graph-search consumer fixture

<a id="s-input"></a>
## INPUT

> Capsule: Consumer evidence without searchable body literals.

The normalized header owns the isolated input facts.
"""


class GraphLayerTests(WorkspaceTestCase):
    def _promote_fixture(self) -> dict:
        path = self.workspace / "code" / f"{ARTIFACT_ID}.md"
        atomic_write_text(path, GRAPH_DOCUMENT)
        return promote_document(
            path,
            self.workspace,
            KnowledgeDatabase(database_path(self.workspace)),
        )

    def _promote_search_fixtures(self) -> KnowledgeDatabase:
        database = KnowledgeDatabase(database_path(self.workspace))
        for artifact_id, document in (
            (SEARCH_PRODUCER_ID, SEARCH_PRODUCER_DOCUMENT),
            (SEARCH_CONSUMER_ID, SEARCH_CONSUMER_DOCUMENT),
        ):
            path = self.workspace / "code" / f"{artifact_id}.md"
            atomic_write_text(path, document)
            receipt = promote_document(path, self.workspace, database)
            self.assertTrue(receipt["verified"])
        return database

    def test_graph_structures_round_trip_at_declared_counts(self) -> None:
        receipt = self._promote_fixture()
        self.assertTrue(receipt["verified"])
        self.assertEqual(
            receipt["graph_counts"],
            {
                "graph_relations": 2,
                "graph_contracts": 1,
                "graph_artifacts": 1,
                "graph_drift": 0,
            },
        )
        connection = KnowledgeDatabase(database_path(self.workspace)).connect(read_only=True)
        try:
            recovered = artifact_graph(connection, ARTIFACT_ID, limit=50)
            self.assertEqual(recovered["tables"]["graph_relations"]["total"], 2)
            self.assertEqual(recovered["tables"]["graph_contracts"]["total"], 1)
            self.assertEqual(recovered["tables"]["graph_artifacts"]["total"], 1)
            self.assertFalse(recovered["tables"]["graph_relations"]["truncated"])
            first = recovered["tables"]["graph_relations"]["rows"][0]
            self.assertEqual(first["predicate"], "defines")
            self.assertEqual(first["object"], "fixture/module_v1.cpp")
        finally:
            connection.close()

    def test_asset_resolves_to_its_producer_and_bindings(self) -> None:
        self._promote_fixture()
        connection = KnowledgeDatabase(database_path(self.workspace)).connect(read_only=True)
        try:
            result = asset_graph(connection, "fixture/output.json", limit=50)
            self.assertEqual(result["declarations"]["total"], 1)
            declaration = result["declarations"]["rows"][0]
            self.assertEqual(declaration["producer_or_consumer"], "WriteFixtureOutputV1")
            self.assertEqual(declaration["schema_or_type"], "fixture_output_v1")
            self.assertEqual(declaration["hash_bound"], 1)
            self.assertEqual(declaration["commit_bound"], 0)
            # The writing edge is reachable from the asset side as well.
            self.assertEqual(result["edges"]["total"], 1)
            self.assertEqual(result["edges"]["rows"][0]["subject"], "fixture/module_v1.cpp")
        finally:
            connection.close()

    def test_deviating_vocabulary_is_reported_and_not_dropped(self) -> None:
        self._promote_fixture()
        connection = KnowledgeDatabase(database_path(self.workspace)).connect(read_only=True)
        try:
            report = vocabulary_report(connection)
            kinds = [
                item
                for item in report["deviations"]
                if item["table"] == "graph_contracts" and item["column"] == "kind"
            ]
            self.assertEqual([item["value"] for item in kinds], ["determinism"])
            self.assertEqual(kinds[0]["first_artifact"], ARTIFACT_ID)
            # The row survived: reporting a deviation must not remove it.
            self.assertEqual(report["row_totals"]["graph_contracts"], 1)
        finally:
            connection.close()

    def test_unresolvable_evidence_anchor_is_reported(self) -> None:
        self._promote_fixture()
        connection = KnowledgeDatabase(database_path(self.workspace)).connect(read_only=True)
        try:
            report = integrity_report(connection, limit=50)
            self.assertEqual(report["dangling_count"], 1)
            dangling = report["dangling"][0]
            self.assertEqual(dangling["table"], "graph_relations")
            self.assertEqual(dangling["evidence_target"], "s-nowhere")
            # Anchors that do resolve are not reported.
            self.assertEqual(report["anchored_rows_checked"], 4)
        finally:
            connection.close()

    def test_query_frame_extracts_bounded_graph_identities_and_role_intent(self) -> None:
        frame = compile_query(
            r'Who produces fixture\route_only_output.json through ns::WriterV1 '
            r'with FAIL_FIXTURE_BETA and schema "fixture_output_v1"?'
        )
        self.assertIn(SEARCH_ASSET, frame.graph_identifiers)
        self.assertIn("ns::WriterV1", frame.graph_identifiers)
        self.assertIn("FAIL_FIXTURE_BETA", frame.graph_identifiers)
        self.assertIn("fixture_output_v1", frame.graph_identifiers)
        self.assertIn("producer", frame.intents)
        self.assertLessEqual(len(frame.graph_identifiers), 16)

        self.assertIn("D1", compile_query("What drift is recorded as D1?").graph_identifiers)
        self.assertIn("PASS", compile_query("Which modules produce PASS?").graph_identifiers)
        for verb in ("produce", "write", "commit", "emit"):
            self.assertIn("producer", compile_query(f"What does the node {verb}?").intents)
        for verb in ("read", "consume", "verify"):
            self.assertIn("consumer", compile_query(f"What does the node {verb}?").intents)
        unrelated = compile_query("Paragraph threads and spreads remain ordinary prose.")
        self.assertNotIn("graph_context", unrelated.intents)
        self.assertNotIn("consumer", unrelated.intents)
        legacy_placement = compile_query("Where should I write future content?")
        self.assertIn("producer", legacy_placement.intents)
        self.assertNotIn("writes", legacy_placement.preferred_relations)
        self.assertNotIn("commits", legacy_placement.preferred_relations)

    def test_question_search_routes_exact_asset_by_producer_and_consumer_role(self) -> None:
        database = self._promote_search_fixtures()
        producer = search_database(
            database,
            f"Who produces {SEARCH_ASSET}?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(producer["primary"]["artifact_id"], SEARCH_PRODUCER_ID)
        self.assertEqual(producer["primary"]["section_id"], "s-output")
        self.assertIn("normalized_graph", producer["retrieval_surfaces"])
        self.assertGreater(producer["primary"]["graph_match_count"], 0)
        self.assertEqual(
            producer["ranking_basis"],
            [
                "graph_intent_role_tier ASC",
                "graph_identifier_specificity DESC",
                "score DESC",
                "path ASC",
            ],
        )
        self.assertEqual(producer["primary"]["rank_basis"]["graph_intent_role_tier"], 0)
        normalized_reasons = [
            reason
            for reason in producer["primary"]["reasons"]
            if reason.startswith("normalized_graph=")
        ]
        self.assertEqual(len(normalized_reasons), 1)
        self.assertNotIn(" +", normalized_reasons[0])
        self.assertTrue(
            any(
                fact["artifact_id"] == SEARCH_CONSUMER_ID
                for fact in producer["graph_context"]["facts"]
            )
        )

        consumer = search_database(
            database,
            f"Who reads {SEARCH_ASSET}?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(consumer["primary"]["artifact_id"], SEARCH_CONSUMER_ID)
        self.assertEqual(consumer["primary"]["section_id"], "s-input")

    def test_failure_schema_node_and_drift_literals_route_to_declared_evidence(self) -> None:
        database = self._promote_search_fixtures()
        cases = (
            ("Which contract names FAIL_FIXTURE_BETA?", "s-contracts", "graph_contracts"),
            ('Which module produces schema "fixture_output_v1"?', "s-output", "graph_artifacts"),
            ("What unresolved drift exists for fixture/producer_v1.cpp?", "s-drift", "graph_drift"),
        )
        for query, section_id, table in cases:
            with self.subTest(query=query):
                result = search_database(database, query, mode="work", record_trace=False)
                self.assertEqual(result["primary"]["artifact_id"], SEARCH_PRODUCER_ID)
                self.assertEqual(result["primary"]["section_id"], section_id)
                self.assertTrue(
                    any(fact["table"] == table for fact in result["graph_context"]["facts"])
                )
                fact = next(
                    fact
                    for fact in result["graph_context"]["facts"]
                    if fact["table"] == table
                )
                if table == "graph_contracts":
                    self.assertIn("statement", fact["row"])
                if table == "graph_drift":
                    self.assertIn("summary", fact["row"])

        prefix = search_database(
            database,
            "Which contract names FAIL_FIXTURE?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(prefix["graph_context"]["table_totals"]["graph_contracts"], 0)
        for extended_prefix in ("FAIL_EXTENDED", "FAIL_DOTTED", "FAIL_SCOPED"):
            with self.subTest(extended_prefix=extended_prefix):
                result = search_database(
                    database,
                    extended_prefix,
                    mode="work",
                    record_trace=False,
                )
                self.assertEqual(
                    result["graph_context"]["table_totals"]["graph_contracts"],
                    0,
                )

        short_failure = search_database(
            database,
            "E_X",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(short_failure["primary"]["section_id"], "s-contracts")
        self.assertEqual(short_failure["query_frame"]["tokens"], ())

        drift_id = search_database(
            database,
            "What drift is recorded as D1?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(drift_id["graph_context"]["table_totals"]["graph_drift"], 2)
        self.assertTrue(
            any(
                fact["row"]["drift_id"] == "D1"
                for fact in drift_id["graph_context"]["facts"]
                if fact["table"] == "graph_drift"
            )
        )

    def test_exact_node_uses_declared_relation_and_party_evidence(self) -> None:
        database = self._promote_search_fixtures()
        result = search_database(
            database,
            "What does fixture/producer_v1.cpp produce?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(result["primary"]["artifact_id"], SEARCH_PRODUCER_ID)
        self.assertEqual(result["primary"]["section_id"], "s-output")
        self.assertTrue(result["primary"]["graph_intent_role_match"])
        self.assertTrue(
            any(
                set(fact["matched_fields"]) & {"subject", "producer_or_consumer"}
                for fact in result["graph_context"]["facts"]
            )
        )

        terminal = search_database(
            database,
            "Which modules produce PASS?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(terminal["primary"]["artifact_id"], SEARCH_PRODUCER_ID)
        self.assertEqual(terminal["primary"]["section_id"], "s-output")

        case_mismatch = search_database(
            database,
            'Which modules produce "pass"?',
            mode="work",
            record_trace=False,
        )
        self.assertEqual(case_mismatch["query_frame"]["graph_identifiers"], ("pass",))
        self.assertEqual(case_mismatch["graph_context"]["seed_total"], 0)

    def test_multi_identifier_specificity_is_applied_before_graph_limit(self) -> None:
        database = self._promote_search_fixtures()
        result = search_database(
            database,
            "What drift D1 concerns fixture/producer_v1.cpp?",
            limit=1,
            candidate_limit=1,
            mode="work",
            record_trace=False,
        )
        self.assertEqual(result["graph_context"]["table_totals"]["graph_drift"], 2)
        self.assertEqual(result["graph_context"]["returned"], 1)
        self.assertEqual(result["primary"]["artifact_id"], SEARCH_PRODUCER_ID)
        self.assertEqual(result["primary"]["section_id"], "s-drift")
        fact = result["graph_context"]["facts"][0]
        self.assertEqual(fact["matched_identifier_count"], 2)
        self.assertEqual(
            set(fact["matched_identifiers"]),
            {"D1", "fixture/producer_v1.cpp"},
        )
        self.assertEqual(result["primary"]["graph_identifier_specificity"], 2)

    def test_dangling_graph_anchor_is_visible_but_never_routable(self) -> None:
        database = self._promote_search_fixtures()
        result = search_database(
            database,
            f"What produces {SEARCH_DANGLING}?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(result["graph_context"]["seed_total"], 1)
        self.assertEqual(result["graph_context"]["unresolved_anchor_total"], 1)
        self.assertFalse(result["graph_context"]["facts"][0]["evidence_resolved"])
        self.assertIsNone(result["graph_context"]["facts"][0]["evidence_path"])
        self.assertIsNone(result["primary"])

        fts_overlap = search_database(
            database,
            f"Which fixture produces {SEARCH_DANGLING}?",
            mode="work",
            record_trace=False,
        )
        self.assertEqual(fts_overlap["graph_context"]["seed_total"], 1)
        self.assertEqual(fts_overlap["graph_context"]["resolved_anchor_total"], 0)
        self.assertEqual(fts_overlap["graph_route_status"], "UNRESOLVED_EVIDENCE")
        self.assertEqual(fts_overlap["candidate_count"], 0)
        self.assertIsNone(fts_overlap["primary"])

        receipt = record_routing_receipt(
            self.workspace,
            database,
            fts_overlap,
            {"checked": True, "refreshed": False, "reason": "dangling graph test"},
        )
        self.assertEqual(receipt["status"], "NO_MATCH")
        self.assertEqual(receipt["graph_route"]["route_status"], "UNRESOLVED_EVIDENCE")
        self.assertEqual(receipt["graph_route"]["resolved_anchor_total"], 0)
        self.assertEqual(receipt["graph_route"]["unresolved_anchor_total"], 1)

        specific_dangling = search_database(
            database,
            f"Which fixture produces {SEARCH_DANGLING} from fixture/consumer_v1.cpp?",
            limit=1,
            candidate_limit=1,
            mode="work",
            record_trace=False,
        )
        self.assertGreater(specific_dangling["graph_context"]["resolved_anchor_total"], 0)
        self.assertEqual(
            specific_dangling["graph_context"]["max_returned_identifier_specificity"],
            2,
        )
        self.assertEqual(
            specific_dangling["graph_context"]["resolved_at_max_specificity_total"],
            0,
        )
        self.assertEqual(specific_dangling["graph_route_status"], "UNRESOLVED_EVIDENCE")
        self.assertEqual(specific_dangling["candidate_count"], 0)
        self.assertIsNone(specific_dangling["primary"])

    def test_graph_context_is_bounded_deterministic_and_summarized_in_receipt(self) -> None:
        database = self._promote_search_fixtures()
        query = "What graph context exists for fixture/producer_v1.cpp?"
        first = search_database(
            database,
            query,
            limit=1,
            candidate_limit=1,
            mode="work",
            record_trace=True,
        )
        second = search_database(
            database,
            query,
            limit=1,
            candidate_limit=1,
            mode="work",
            record_trace=False,
        )
        self.assertGreater(first["graph_context"]["seed_total"], 1)
        self.assertEqual(first["graph_context"]["returned"], 1)
        self.assertTrue(first["graph_context"]["truncated"])
        self.assertLessEqual(first["candidate_count"], 1)
        self.assertEqual(first["graph_context"]["facts"], second["graph_context"]["facts"])
        self.assertLessEqual(len(first["primary"]["graph_evidence_anchors"]), 5)

        connection = database.connect(read_only=True)
        try:
            trace = connection.execute(
                "SELECT result_json FROM retrieval_traces WHERE trace_id=?",
                (first["trace_id"],),
            ).fetchone()
        finally:
            connection.close()
        traced = json.loads(trace["result_json"])
        self.assertEqual(traced["graph_context"]["returned"], 1)
        self.assertEqual(len(traced["graph_context"]["facts"]), 1)

        receipt = record_routing_receipt(
            self.workspace,
            database,
            first,
            {"checked": True, "refreshed": False, "reason": "graph route test"},
        )
        self.assertEqual(receipt["status"], "ROUTED")
        self.assertEqual(receipt["graph_route"]["seed_total"], first["graph_context"]["seed_total"])
        self.assertTrue(receipt["graph_route"]["truncated"])
        self.assertLessEqual(len(receipt["graph_route"]["primary_anchors"]), 8)
        self.assertNotIn("facts", receipt["graph_route"])
