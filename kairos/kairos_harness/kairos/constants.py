from __future__ import annotations

SCHEMA_VERSION = "kairos-context/v1"
DATABASE_SCHEMA_VERSION = "kairos-db/v2"
RUNTIME_SCHEMA_VERSION = "kairos-runtime/v1"
GOAL_SCHEMA_VERSION = "kairos-goal/v1"

MAX_HEADER_BYTES = 3_072
FIRST_WINDOW_BYTES = 4_096

# A graph-bearing document carries a normalized codebase graph in its header. Its header
# is not a first read; it is the calibration surface a query is sharpened against, so the
# reader reaches a named anchor through SQL instead of scanning the file. The first-window
# budget therefore buys nothing here and is replaced by the bounds below.
GRAPH_HEADER_KEYS = ("relations", "contracts", "artifacts", "drift_records")
MAX_GRAPH_HEADER_BYTES = 256 * 1024
MAX_GRAPH_ENTITIES = 128

MAX_CAPSULE_CHARS = 480
# The context index is a map, not a second copy of the capsules. Restating them in full
# spends the first-window budget twice and pushes substantive documents over it.
MAX_INDEX_CAPSULE_CHARS = 140
MAX_CLAIM_BOUNDARY_CHARS = 480
MAX_PRIMARY_REFS = 8
MAX_ANSWER_HANDLES = 10
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
# An ingested document may carry a line-numbered copy of its own source as one section.
# That is what a source ledger is for, and it scales with the translation unit rather than
# with prose, so the bound is set by the largest unit in the corpus rather than by taste.
MAX_SECTION_BYTES = 1024 * 1024
MAX_SECTIONS = 128
MAX_REFERENCES = 256
MAX_QUERY_CHARS = 4096
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_CANDIDATES = 500
MAX_CONTEXT_HOPS = 4
MAX_CONTEXT_CHASE_RESULTS = 50
MAX_RETRIEVAL_TRACES = 1000
MAX_GOVERNED_ACTION_RECEIPTS = 10_000
MAX_GOVERNANCE_VIOLATIONS = 2_000
MAX_QUARANTINE_HISTORY = 2_000
MAX_TERMINAL_ACTION_PERMITS = 12_000
MAX_DYNAMIC_TASK_POINTERS = 8
MAX_DYNAMIC_CRITERION_POINTERS = 12
MAX_GENERATED_RECEIPT_FILES = 200
MAX_MANAGED_DOCUMENTS = 10_000
MAX_MANAGED_SOURCE_BYTES = 256 * 1024 * 1024
MAX_GOAL_FILES = 1024
MAX_GOAL_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_GOAL_BYTES = 64 * 1024 * 1024
MAX_MILESTONES_PER_GOAL = 256
MAX_CRITERIA_PER_GOAL = 4096
MAX_GOAL_TEXT_CHARS = 8192
MAX_BACKUP_SOURCE_FILES = MAX_MANAGED_DOCUMENTS + MAX_GOAL_FILES + 16
MAX_BACKUP_SOURCE_BYTES = MAX_MANAGED_SOURCE_BYTES + MAX_TOTAL_GOAL_BYTES + 32 * 1024 * 1024
MAX_BACKUP_MANIFEST_BYTES = 16 * 1024 * 1024

EVIDENCE_DOCUMENT_TYPES = {
    "report",
    "bug",
    "code",
    "decision",
    "research",
    "archive",
    "documentation",
}

DOCUMENT_TYPES = {
    "task",
    "report",
    "bug",
    "code",
    "decision",
    "research",
    "archive",
    "router",
    "gate",
    "session",
    "documentation",
    "transaction",
}

LIFECYCLE_STATES = {
    "new",
    "ready",
    "active",
    "in_progress",
    "partial",
    "success",
    "failed",
    "blocked",
    "resolved",
    "completed",
    "superseded",
    "finalized",
}

RELATION_TYPES = {
    # Document-level predicates.
    "about",
    "belongs_to",
    "caused_by",
    "contradicts",
    "depends_on",
    "derived_from",
    "documents",
    "evidenced_by",
    "fixes",
    "implemented_by",
    "implements",
    "informs",
    "next",
    "parent",
    "produces",
    "references",
    "requires",
    "resolves",
    "satisfies",
    "supports",
    "supersedes",
    "unblocks",
    "validated_by",
    "validates",
    # Code-graph predicates carried by ingested documents. Five names above are reused;
    # the table a row lands in already says which meaning applies, so one list is enough.
    "authenticates",
    "binds",
    "calls",
    "commits",
    "constrained_by",
    "consumes",
    "declares",
    "defines",
    "delegates_to",
    "drifts_from",
    "gates",
    "owns",
    "precedes",
    "reads",
    "tracks",
    "writes",
    # Pointer predicates observed in [refs] of ingested corpora that the code-graph
    # vocabulary does not list. Harvested from real documents rather than assumed.
    "constrains",
    "indexes",
    "proves",
    "supersedes_candidate",
}

GRAPH_OBJECT_KINDS = {
    "module", "source", "header", "symbol", "type",
    "artifact", "schema", "contract", "failure", "concept",
}

GRAPH_CONTRACT_KINDS = {
    "invariant", "precondition", "postcondition", "authority_boundary",
    "identity_binding", "hash_binding", "ordering", "fail_closed",
    "compatibility", "admission",
}

GRAPH_ARTIFACT_ROLES = {
    "input", "output", "snapshot", "sidecar", "commit_marker",
    "manifest", "report", "registry", "config",
}

GRAPH_ARTIFACT_OPERATIONS = {"read", "write", "commit", "embed", "hash", "verify"}

GRAPH_DRIFT_CLASSIFICATIONS = {
    "retained", "superseded", "missing_surface", "implementation_drift",
    "route_drift", "provenance_boundary_deviation", "other_measured",
}

GRAPH_DRIFT_STATES = {"aligned", "intentional_supersession", "unresolved"}

ACQUISITION_MODES = {"auto", "work", "depth", "breadth", "breathe", "verify"}

AUTHORITIES = {
    "architecture_authority",
    "diagnostic_record",
    "execution_evidence",
    "goal_authority",
    "implementation_documentation",
    "loop_archive",
    "operating_contract",
    "research_evidence",
    "routing",
    "state_authority",
    "task_contract",
    "validation_evidence",
}

DOCUMENT_TYPE_AUTHORITIES = {
    "task": {"task_contract"},
    "report": {"execution_evidence", "validation_evidence"},
    "bug": {"diagnostic_record"},
    "code": {"implementation_documentation"},
    "decision": {"architecture_authority"},
    "research": {"research_evidence"},
    "archive": {"loop_archive"},
    "router": {"routing"},
    "gate": {"state_authority"},
    "session": {"routing"},
    "documentation": {"architecture_authority", "operating_contract"},
    "transaction": {"execution_evidence"},
}

DEFAULT_DOCUMENT_ROOTS = [
    "tasks",
    "reports",
    "bugs",
    "code",
    "decisions",
    "research",
    "docs",
    "archive",
]

DEFAULT_CANONICAL_FILES = [
    "AGENTS.md",
    "NEURAL_CORTEX.md",
    "ACTIVE.md",
    "CLOSED.md",
    "_LOOP_GATE.md",
    "_SESSION.md",
]

DYNAMIC_CANONICAL_FILES = {
    "NEURAL_CORTEX.md",
    "ACTIVE.md",
    "CLOSED.md",
    "_LOOP_GATE.md",
    "_SESSION.md",
}
