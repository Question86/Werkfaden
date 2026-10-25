from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .constants import DATABASE_SCHEMA_VERSION


class DatabaseError(RuntimeError):
    pass


COMPATIBLE_SCHEMA_VERSIONS = {"kairos-db/v1", DATABASE_SCHEMA_VERSION}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    document_type TEXT NOT NULL,
    state TEXT NOT NULL,
    authority TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    route TEXT NOT NULL,
    loop_id TEXT,
    task_id TEXT,
    goal_id TEXT,
    milestone_id TEXT,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    capsule TEXT NOT NULL,
    claim_boundary TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    header_bytes INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    promoted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_type_state ON artifacts(document_type, state);
CREATE INDEX IF NOT EXISTS idx_artifacts_goal ON artifacts(goal_id, milestone_id, task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(content_sha256);

CREATE TABLE IF NOT EXISTS artifact_revisions (
    artifact_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    PRIMARY KEY (artifact_id, revision),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sections (
    artifact_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    capsule TEXT NOT NULL,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    PRIMARY KEY (artifact_id, section_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sections_title ON sections(title);

CREATE VIRTUAL TABLE IF NOT EXISTS section_fts USING fts5(
    artifact_id UNINDEXED,
    section_id UNINDEXED,
    title,
    capsule,
    questions,
    entities,
    facets,
    body,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS artifact_path_fts USING fts5(
    artifact_id UNINDEXED,
    path,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS query_handles (
    artifact_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    question TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (artifact_id, section_id, intent, question),
    FOREIGN KEY (artifact_id, section_id) REFERENCES sections(artifact_id, section_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_query_handles_intent ON query_handles(intent);

CREATE TABLE IF NOT EXISTS relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_artifact_id TEXT NOT NULL,
    source_section_id TEXT,
    predicate TEXT NOT NULL,
    target_artifact_id TEXT,
    target_path TEXT NOT NULL,
    target_section_id TEXT,
    version TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    UNIQUE (
        source_artifact_id,
        source_section_id,
        predicate,
        target_path,
        target_section_id
    ),
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_artifact_id, source_section_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_artifact_id, target_path, target_section_id);
CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate);

CREATE TABLE IF NOT EXISTS promotion_events (
    event_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    path TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_promotion_events_status ON promotion_events(status, created_at);

CREATE TABLE IF NOT EXISTS promotion_receipts (
    receipt_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    artifact_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    section_count INTEGER NOT NULL,
    relation_count INTEGER NOT NULL,
    query_handle_count INTEGER NOT NULL,
    search_contract_count INTEGER NOT NULL,
    verified INTEGER NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES promotion_events(event_id)
);

CREATE TABLE IF NOT EXISTS goals (
    goal_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    milestone_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    depends_on_json TEXT NOT NULL,
    FOREIGN KEY (goal_id) REFERENCES goals(goal_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_milestones_goal ON milestones(goal_id, sequence);

CREATE TABLE IF NOT EXISTS criteria (
    criterion_id TEXT PRIMARY KEY,
    milestone_id TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    evidence_required TEXT NOT NULL,
    required_artifact_types_json TEXT NOT NULL,
    FOREIGN KEY (milestone_id) REFERENCES milestones(milestone_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_criteria_milestone ON criteria(milestone_id, state);

CREATE TABLE IF NOT EXISTS goal_coverage (
    criterion_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    section_id TEXT,
    coverage_state TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (criterion_id, artifact_id, section_id),
    FOREIGN KEY (criterion_id) REFERENCES criteria(criterion_id) ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS action_journal (
    journal_id TEXT PRIMARY KEY,
    heartbeat_sequence INTEGER,
    action_type TEXT NOT NULL,
    mode TEXT,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    artifact_id TEXT,
    summary TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_journal_heartbeat ON action_journal(heartbeat_sequence, created_at);

CREATE TABLE IF NOT EXISTS heartbeat_receipts (
    heartbeat_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL UNIQUE,
    lifecycle_before TEXT NOT NULL,
    lifecycle_after TEXT NOT NULL,
    mode TEXT NOT NULL,
    promoted_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    verified INTEGER NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finalization_receipts (
    finalization_id TEXT PRIMARY KEY,
    loop INTEGER NOT NULL UNIQUE,
    heartbeat_id TEXT NOT NULL,
    archive_id TEXT NOT NULL UNIQUE,
    archive_receipt_id TEXT NOT NULL,
    backup_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (heartbeat_id) REFERENCES heartbeat_receipts(heartbeat_id)
);

CREATE TABLE IF NOT EXISTS loop_transition_receipts (
    transition_id TEXT PRIMARY KEY,
    predecessor_finalization_id TEXT NOT NULL UNIQUE,
    source_loop INTEGER NOT NULL,
    target_loop INTEGER NOT NULL UNIQUE,
    goal_id TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','VERIFIED')),
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (predecessor_finalization_id) REFERENCES finalization_receipts(finalization_id)
);

CREATE TABLE IF NOT EXISTS retrieval_traces (
    trace_id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    query_frame_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_routing_receipts (
    routing_receipt_id TEXT PRIMARY KEY,
    task_id TEXT,
    criterion_id TEXT,
    query_text TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ROUTED','NO_MATCH','STALE_DIAGNOSTIC','UNSCOPED')),
    primary_artifact_id TEXT,
    primary_section_id TEXT,
    primary_path TEXT,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_routing_scope
ON search_routing_receipts(task_id,criterion_id,created_at);

CREATE TABLE IF NOT EXISTS source_inspection_permits (
    permit_id TEXT PRIMARY KEY,
    routing_receipt_id TEXT UNIQUE,
    task_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    root_relative TEXT NOT NULL,
    paths_json TEXT NOT NULL,
    patterns_json TEXT NOT NULL,
    scope_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','CONSUMED','EXPIRED','REVOKED')),
    receipt_json TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY (routing_receipt_id) REFERENCES search_routing_receipts(routing_receipt_id)
);

CREATE TABLE IF NOT EXISTS source_inspection_receipts (
    inspection_id TEXT PRIMARY KEY,
    permit_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('VERIFIED','BLOCKED')),
    match_count INTEGER NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (permit_id) REFERENCES source_inspection_permits(permit_id)
);

CREATE TABLE IF NOT EXISTS action_permits (
    permit_id TEXT PRIMARY KEY,
    action_id TEXT,
    phase TEXT NOT NULL,
    command_name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    criterion_id TEXT,
    scope_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_permits_scope
ON action_permits(task_id,criterion_id,status,expires_at);

CREATE TABLE IF NOT EXISTS governed_action_receipts (
    action_id TEXT PRIMARY KEY,
    permit_id TEXT NOT NULL UNIQUE,
    phase TEXT NOT NULL,
    command_name TEXT NOT NULL,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    criterion_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result_json TEXT NOT NULL,
    FOREIGN KEY (permit_id) REFERENCES action_permits(permit_id)
);
CREATE INDEX IF NOT EXISTS idx_governed_actions_scope
ON governed_action_receipts(task_id,criterion_id,status,started_at);

CREATE TABLE IF NOT EXISTS governance_state (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    enforcement_mode TEXT NOT NULL,
    current_phase TEXT,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    criterion_id TEXT,
    last_action_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS governance_violations (
    violation_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    command_name TEXT,
    phase TEXT,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    criterion_id TEXT,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_violations_scope
ON governance_violations(task_id,criterion_id,created_at);

CREATE TABLE IF NOT EXISTS quarantine_entries (
    quarantine_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    observed_sha256 TEXT NOT NULL,
    observed_size INTEGER,
    observed_mtime_ns INTEGER,
    reason TEXT NOT NULL,
    goal_id TEXT,
    milestone_id TEXT,
    task_id TEXT,
    criterion_id TEXT,
    status TEXT NOT NULL,
    attributed_action_id TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quarantine_status
ON quarantine_entries(status,created_at,path);

-- Normalized graph layer.
--
-- A graph-bearing document declares typed facts about entities that are not documents:
-- sources, headers, symbols, types, produced assets, schemas, contracts and failure
-- literals. These four tables hold those facts verbatim. Column names mirror the header
-- keys so the projection stays mechanical and no interpretation happens on the way in.
--
-- artifact_id is always the declaring document. The controlled vocabularies live in
-- constants.py and are not enforced by CHECK constraints on purpose: a value outside the
-- declared set is stored as written and found by query, so a single deviating row never
-- costs a whole document. ordinal preserves the declared order and keeps a repeated local
-- identifier from colliding.

CREATE TABLE IF NOT EXISTS graph_relations (
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    evidence_target TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (artifact_id, ordinal),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_graph_relations_object ON graph_relations(object, predicate);
CREATE INDEX IF NOT EXISTS idx_graph_relations_subject ON graph_relations(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_graph_relations_predicate ON graph_relations(predicate, object_kind);

CREATE TABLE IF NOT EXISTS graph_contracts (
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    contract_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    statement TEXT NOT NULL,
    failure_or_effect TEXT NOT NULL,
    evidence_target TEXT NOT NULL,
    PRIMARY KEY (artifact_id, ordinal),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_graph_contracts_kind ON graph_contracts(kind);
CREATE INDEX IF NOT EXISTS idx_graph_contracts_subject ON graph_contracts(subject);
CREATE INDEX IF NOT EXISTS idx_graph_contracts_failure ON graph_contracts(failure_or_effect);

-- asset_id is the produced or consumed thing, not a KAIROS document id.
CREATE TABLE IF NOT EXISTS graph_artifacts (
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    asset_id TEXT NOT NULL,
    role TEXT NOT NULL,
    operation TEXT NOT NULL,
    producer_or_consumer TEXT NOT NULL,
    schema_or_type TEXT NOT NULL,
    hash_bound INTEGER NOT NULL,
    commit_bound INTEGER NOT NULL,
    evidence_target TEXT NOT NULL,
    PRIMARY KEY (artifact_id, ordinal),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_graph_artifacts_asset ON graph_artifacts(asset_id);
CREATE INDEX IF NOT EXISTS idx_graph_artifacts_party ON graph_artifacts(producer_or_consumer);
CREATE INDEX IF NOT EXISTS idx_graph_artifacts_schema ON graph_artifacts(schema_or_type);
CREATE INDEX IF NOT EXISTS idx_graph_artifacts_binding ON graph_artifacts(hash_bound, commit_bound);

CREATE TABLE IF NOT EXISTS graph_drift (
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    drift_id TEXT NOT NULL,
    historical TEXT NOT NULL,
    subject TEXT NOT NULL,
    classification TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_target TEXT NOT NULL,
    PRIMARY KEY (artifact_id, ordinal),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_graph_drift_class ON graph_drift(classification, status);
CREATE INDEX IF NOT EXISTS idx_graph_drift_subject ON graph_drift(subject);

CREATE TABLE IF NOT EXISTS compiler_include_edges (
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    compiled_root TEXT NOT NULL,
    including_file TEXT NOT NULL,
    include_line INTEGER NOT NULL,
    include_token TEXT NOT NULL,
    delimiter TEXT NOT NULL,
    resolved_target TEXT NOT NULL,
    resolution_class TEXT NOT NULL,
    compiler_variant INTEGER NOT NULL,
    resolution_path TEXT NOT NULL,
    evidence_target TEXT NOT NULL,
    PRIMARY KEY (artifact_id, ordinal),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_compiler_include_edges_including
ON compiler_include_edges(including_file, resolved_target);
CREATE INDEX IF NOT EXISTS idx_compiler_include_edges_target
ON compiler_include_edges(resolved_target, compiled_root);
CREATE INDEX IF NOT EXISTS idx_compiler_include_edges_root
ON compiler_include_edges(compiled_root, including_file);

-- Derived only from mechanically validated recycling fields of a promoted
-- transaction Markdown document; no second mutable TX authority in SQLite.
CREATE TABLE IF NOT EXISTS tx_recycling_reviews (
    artifact_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE,
    inventory_sha256 TEXT NOT NULL,
    analysis_sha256 TEXT NOT NULL,
    decision_count INTEGER NOT NULL,
    document_revision INTEGER NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tx_recycling_reviews_analysis ON tx_recycling_reviews(analysis_sha256);

CREATE TABLE IF NOT EXISTS tx_recycling_atoms (
    artifact_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    path TEXT NOT NULL,
    baseline_sha256 TEXT,
    candidate_sha256 TEXT,
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    source_transaction TEXT NOT NULL,
    successor_transaction TEXT NOT NULL,
    protected INTEGER NOT NULL,
    PRIMARY KEY (artifact_id, unit_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tx_recycling_atoms_disposition ON tx_recycling_atoms(disposition, protected);
CREATE INDEX IF NOT EXISTS idx_tx_recycling_atoms_path ON tx_recycling_atoms(path);
CREATE INDEX IF NOT EXISTS idx_tx_recycling_atoms_successor ON tx_recycling_atoms(successor_transaction);

CREATE TABLE IF NOT EXISTS search_policy_violations (
    violation_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    task_id TEXT,
    criterion_id TEXT,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class KnowledgeDatabase:
    def __init__(self, path: Path):
        self.path = path

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA trusted_schema=OFF")
        # Keep transient SQLite B-trees inside the governed connection.  Promotion
        # must not depend on a host TEMP directory or its ACLs, and there is no
        # file-backed fallback for this invariant.
        connection.execute("PRAGMA temp_store=MEMORY")
        if not read_only:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA wal_autocheckpoint=1000")
            connection.execute("PRAGMA journal_size_limit=16777216")
        else:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(SCHEMA_SQL)
            row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            # The graph layer is purely additive: every table it introduces is created by
            # CREATE TABLE IF NOT EXISTS above, so an earlier database is carried forward
            # without a data migration. Only an unknown version fails closed.
            if row and row["value"] not in COMPATIBLE_SCHEMA_VERSIONS:
                raise DatabaseError(
                    f"database schema is {row['value']!r}; expected one of "
                    f"{sorted(COMPATIBLE_SCHEMA_VERSIONS)}"
                )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (DATABASE_SCHEMA_VERSION,),
            )
            # Existing workspaces created before path FTS are migrated once.
            # Thereafter the promoter maintains the derived index atomically.
            path_index = connection.execute(
                "SELECT value FROM schema_meta WHERE key='artifact_path_fts_bootstrapped'"
            ).fetchone()
            if path_index is None:
                connection.execute("DELETE FROM artifact_path_fts")
                connection.execute(
                    "INSERT INTO artifact_path_fts(artifact_id,path) "
                    "SELECT artifact_id,path FROM artifacts"
                )
                connection.execute(
                    "INSERT INTO schema_meta(key,value) "
                    "VALUES('artifact_path_fts_bootstrapped','1')"
                )
            connection.commit()
            connection.execute("PRAGMA optimize")
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending_counts(self) -> tuple[int, int]:
        connection = self.connect(read_only=True)
        try:
            pending = connection.execute(
                "SELECT count(*) FROM promotion_events WHERE status='pending'"
            ).fetchone()[0]
            failed = connection.execute(
                "SELECT count(*) FROM promotion_events WHERE status='failed'"
            ).fetchone()[0]
            return int(pending), int(failed)
        finally:
            connection.close()
