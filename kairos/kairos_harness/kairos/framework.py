from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .util import is_link_or_junction, reject_link_components

from .database import KnowledgeDatabase
from .search import search_database

FRAMEWORK_WORKSPACE_ID = "KAIROS_FRAMEWORK"
FRAMEWORK_INDEX_WORKSPACE_ID = "KAIROS_FRAMEWORK_INDEX"
FRAMEWORK_VERSION = "0.1.0-alpha.6"
FRAMEWORK_DATABASE_NAME = "framework.db"
FRAMEWORK_MANIFEST_NAME = "framework_manifest.json"
FRAMEWORK_MANIFEST_SCHEMA = "kairos-framework-index/v3"
TOOL_CATALOG_ID = "KAIROS_WORKSPACE_TOOLS"
TOOL_CATALOG_PATH = "docs/KAIROS_WORKSPACE_TOOLS.md"

CONTEXT_TOOL_CATALOG_ID = "KAIROS_CONTEXT_TOOLS"
CONTEXT_TOOL_CATALOG_PATH = "docs/KAIROS_CONTEXT_TOOLS.md"

# A second view of the same release routing definition separates context records
# from governed source mutation. Names are scoped by their actual CLI namespace.
HARNESS_TOOL_GROUPS = (
    ("s-records", "Tasks, reports, bugs and knowledge / Arbeitsdokumentation",
     "Create task contracts before work and attributable reports, bug records, implementation maps, decisions and research for its evidence.",
     "Aufgaben als Tasks anlegen, Arbeiten als Reports dokumentieren, gefundene Fehler als Bugs festhalten. new-task establishes the bounded goal/milestone/criteria route and active frontier. new-report records outcome, work, evidence, limitations and next action. new-bug records observation, cause and regression. new-code creates implementation documentation, not executable source. new-decision records tradeoffs; new-research records attributable source evidence. These constructors publish through a heartbeat. close-task requires demonstrated acceptance. They do not create Workshop transactions: use Workshop for governed code/paired Markdown and tx-document-reconcile for a missing TX lifecycle report. Owners: kairos.cli, templates, workspace, heartbeat and goals.",
     "new-task new-report new-bug new-code new-decision new-research close-task"),
    ("s-retrieval", "Retrieve prior work / Vorarbeit und Kontext finden",
     "Query the smallest authoritative context before action, then use permitted exact source inspection only when needed.",
     "Vorarbeit suchen: search routes a question to indexed sections; ranking is routing evidence, not truth. Before intake omit --workspace for the immutable framework; after intake explicitly bind the selected project workspace. graph exposes bounded inventories, exact identities and relations. Exact source bytes inside inspection_root follow search, source-permit, source-search; never widen the root or fabricate an empty-result conclusion. Files beyond that wall use filesystem tools under the owning contract. Owners: search, graph, search_policy and governance.",
     "search source-permit source-search graph"),
    ("s-orient", "State and coverage / Zustand und Abdeckung",
     "Inspect current state, reconcile source freshness, validate documents and assess goal evidence without inventing completion.",
     "Status, offene Goals, Dokumente und Abdeckung pruefen: status reports runtime/database/goal state; validate checks headers, anchors, answers and references. reconcile previews bounded document differences; health audits operational consistency. coverage assesses required evidence, not an automatic truth guarantee. goal-sync synchronizes authoritative goal contracts through the native lifecycle, not direct SQL or pointer editing. Owners: heartbeat, reconcile, health and goals.",
     "status validate reconcile coverage health goal-sync"),
    ("s-publication", "Publish documented work / Heartbeat und Datenbank",
     "A heartbeat publishes changed task/report/bug documents into the database and search, verifies the projection and checkpoints the frontier.",
     "Publish work reports and knowledge into database search / Berichte und Wissen in Datenbank und Suche verfuegbar machen: heartbeat is the normal work-document-promote-verify-checkpoint cycle. A saved report file becomes available to later retrieval only through verified promotion; creation on disk alone is not publication. Name every changed document and inspect verified/failures before proceeding. promote is diagnostic direct promotion, not a substitute for the bounded loop. rebuild-derived reconstructs absent derived state from authoritative tracked sources and explicit history; it is not permission to reset an inconvenient live database. Never hand-edit SQLite, receipts or current.json. Owners: heartbeat, promoter and recovery.",
     "heartbeat promote rebuild-derived"),
    ("s-governance", "Edit permits and blocked actions / Freigaben und Blockaden",
     "Use exact-path document permits and owner-specific recovery; no permit bypasses the Workshop source boundary.",
     "governance-status inspects phase, scope, permits and violations. action-permit authorizes an expiring exact-path external document edit; revoke-permit withdraws it. recover-action handles its proved orphaned action; recover-revisions restores artifact revision evidence. attribute-change reviews and attributes quarantined bytes under its explicit boundary; governance-enable requires a clean admissible migration state. Inspect failures before retrying. None of these deletes leases, rewrites seals, adopts arbitrary source drift or replaces Workshop recovery. Owners: governance and backup.",
     "governance-status action-permit revoke-permit recover-action recover-revisions attribute-change governance-enable"),
    ("s-lifecycle", "Backups and loop completion / Sicherung und Abschluss",
     "Verify backups, isolated restore evidence and complete coverage before finalization or a numbered loop transition.",
     "backup creates an attributable backup; backup-verify checks it; restore-drill tests recovery in isolation. finalize requires verified heartbeat, required coverage and closed goal/milestone/task state plus archive and verified backup. new-loop advances only the supported closed-loop boundary. project-kickoff uses reviewed bounded Human intent, never inferred product goals. These commands do not authorize Git or remote publication. Owners: backup, heartbeat and loops.",
     "backup backup-verify restore-drill finalize new-loop project-kickoff"),
    ("s-starter", "Prepare a fresh environment / Neue Umgebung vorbereiten",
     "Starter preparation is distinct from authorized project intake and source discovery.",
     "init creates a prepared starter workspace, not an inferred real-project contract. export-golden and starter-check manage the reusable starter boundary; goal-prompt renders a prompt and does not call an LLM. For a real project use the owning kickstart contract: reviewed kairos-project-kickoff/v1 intent, read-only detect, then explicitly authorized init. A fresh pull must not overwrite another instance or personal memory. Owners: workspace, golden and loops; Kickstart has a separate CLI.",
     "init export-golden starter-check goal-prompt"),
)


def verify_context_tool_inventory(commands: list[str]) -> None:
    declared = [name for group in HARNESS_TOOL_GROUPS for name in group[4].split()]
    if (len(declared) != len(set(declared)) or len(commands) != len(set(commands))
            or set(commands) != set(declared)):
        raise ValueError("tool routing command inventory differs from the public Harness parser")


def context_tool_catalog_source_paths() -> tuple[str, ...]:
    return ("kairos/kairos_harness/kairos/framework.py",
            "kairos/kairos_harness/kairos/query.py",
            "kairos/kairos_harness/kairos/search.py",
            "kairos/kairos_harness/kairos/cli.py")


def context_tool_catalog_document(workspace_id: str, *, updated_at: str = "2000-01-01T00:00:00Z") -> str:
    from .templates import pointer, render_document
    framework = workspace_id == FRAMEWORK_WORKSPACE_ID
    reference = pointer(
        "kairos/kairos_harness/docs/OPERATIONS.md" if framework else "AGENTS.md",
        section="s-overview" if framework else "s-operating-loop",
        artifact_id="KAIROS_OPERATIONS" if framework else "KAIROS_OPERATING_CONTRACT",
        version="dynamic", relation="requires", tags=("operations", "authority"), source="system",
    )
    questions = (
        ("How do I document bounded work?", "s-records"),
        ("How do I retrieve prior context?", "s-retrieval"),
        ("How do I inspect goal coverage?", "s-orient"),
        ("How do I publish work evidence?", "s-publication"),
        ("How do I handle a document edit permit?", "s-governance"),
        ("How do I verify loop completion?", "s-lifecycle"),
        ("How do I prepare a starter?", "s-starter"),
    )
    metadata = {
        "schema": "kairos-context/v1", "id": CONTEXT_TOOL_CATALOG_ID, "type": "router",
        "revision": 1, "state": "active", "authority": "routing", "workspace": workspace_id,
        "route": f"{workspace_id}/{CONTEXT_TOOL_CATALOG_ID}", "updated_at": updated_at,
        "capsule": "Harness context tools: task/report/bug records, prior-work retrieval, heartbeat publication, governance, coverage and lifecycle.",
        "claim_boundary": "Derived release routing only. Operating contracts and native gates own authority; parser membership is not execution or source-admission proof.",
        "entities": [CONTEXT_TOOL_CATALOG_ID, "Werkfaden", "Harness"],
        "facets": ["documentation", "tasks", "reports", "bugs", "heartbeat", "tools"],
        "criteria": [], "does_not_answer": ["live state", "criterion acceptance", "source mutation permission"],
        "answers": [{"intent": "procedure", "question": q, "target": t} for q, t in questions],
        "refs": {"operating_contract": reference,
                 "workshop": pointer(TOOL_CATALOG_PATH, section="s-entry", artifact_id=TOOL_CATALOG_ID,
                                     version="dynamic", relation="references", tags=("tools",), source="system")},
        "search_contract": [{"query": questions[0][0], "expected": CONTEXT_TOOL_CATALOG_ID + "#s-records", "required_top_k": 5}],
    }
    sections = []
    for anchor, title, capsule, explanation, commands in HARNESS_TOOL_GROUPS:
        entry = ""
        if anchor == "s-records":
            entry = (f"Release routing for KAIROS {FRAMEWORK_VERSION}. Source checkout: "
                     "`python kairos_cli.py <command> --workspace <selected-workspace>`; installed: "
                     "`python -m kairos <command> --workspace <selected-workspace>`. "
                     "Use `<command> --help` for exact arguments. Harness --workspace is distinct from Workshop --config. "
                     "Bind the actual instance; no special directory name is required.\n\n")
        sections.append({"id": anchor, "title": title, "capsule": capsule,
                         "content": entry + explanation + "\n\nPublic Harness commands: "
                         + ", ".join("`" + name + "`" for name in commands.split()) + "."})
    return render_document(metadata, "Werkfaden context management / Dokumentationsverwaltung", sections)


def tool_catalog_definitions():
    """The two scoped release views share promotion and source verification."""
    return ((TOOL_CATALOG_ID, TOOL_CATALOG_PATH, tool_catalog_document, tool_catalog_source_paths),
            (CONTEXT_TOOL_CATALOG_ID, CONTEXT_TOOL_CATALOG_PATH,
             context_tool_catalog_document, context_tool_catalog_source_paths))


# Reviewed release routing, not an alternative operating contract. Command/module
# coverage is checked against the public parser and exact package inventory at build.
WORKSHOP_TOOL_GROUPS = (
    ("s-entry", "Find workspace tools / Werkzeuge finden",
     "Choose a workspace tool by situation; bind the selected project config.",
     "Workspace Verwaltung: Werkzeuge, Befehle und Skripte nach Problem finden. First orient with status and manifest; inspect the transaction kind before choosing a writer. Status persists a local receipt; manifest is the corpus inspection surface. Seal creates a trusted baseline only from verified authority, never by adopting drift. acl-plan describes an OS boundary without applying ACLs.",
     "status manifest seal transaction source-set-transaction auxiliary-transaction bootstrap-transaction authority-transaction acl-plan"),
    ("s-patch", "Change code and metadata / Code und Metadaten aendern",
     "Ordinary source edits require checkout, semantic document review, preparation, metadata clearance, verification and Apply.",
     "Code aendern: checkout returns the only writable candidate. Include affected existing Markdown with include-documents. Review real header changes and exact samples before metadata-check. Preserve the verified package through verify and apply; abort closes an admissible pre-Apply candidate. Live source is never edited directly. Implementation owners: engine, _core_checkout, _core_metadata, _core_prepare, metadata_gate, _core_verify, _core_apply and context_documents.",
     "checkout include-documents metadata-plan metadata-check prepare verify apply abort"),
    ("s-recovery", "Missing reports and blocked recovery / Fehlende Berichte und Blockaden",
     "A missing transaction report in search after a completed operation needs document reconciliation, without applying code again.",
     "Fehlender TX-Bericht, Dokumentation nicht in der Datenbank oder Suche: inspect saved state, then tx-document-reconcile repairs the exact lifecycle document and SQL projection. Never repeat Apply for this publication failure. A blockierte Transaktion must use its owning recover command and exact error evidence: ordinary recover; auxiliary-recover; authority-migration-recover; bootstrap-recover. source-set-recover-orphan, stale-abort and authority-recover have distinct explicit proof/authorization boundaries. They are not interchangeable fallbacks. TX_OPERATION_RETRY_REQUIRED requires rereading recovered state and reevaluating a fresh operation. Owners: engine, tx_documents, tx_lifecycle, _core_recovery, auxiliary, migration, repair, universal_migration.",
     "tx-document-reconcile recover auxiliary-recover authority-migration-recover bootstrap-recover source-set-recover-orphan stale-abort authority-recover"),
    ("s-retention", "Old worktrees, space and restore / Alte Arbeitskopien aufraeumen",
     "Inspect disk bloat and old transaction worktrees, review every changed file, archive exact bytes, then compact or restore through native retention.",
     "Speicherplatz, Bloat, Recycling, alte Arbeitskopien aufraeumen / aufraumen, sicher archivieren und wiederherstellen: tx-space-audit measures storage; tx-pool-status and tx-recycle-status show policy and pool. For a terminal unleased TX use tx-recycle-inspect, fill its exact evidence-bound proposal, then tx-recycle-submit. Only reviewed, projected terminal worktrees may use tx-compact; tx-recycle-drain resumes admitted maintenance. tx-rehydrate restores verified archive bytes to a separate destination, never silently installs them. tx-recycle-retire-orphan requires a provable unleased pre-Apply orphan. Do not delete worktrees or leases by hand. Automatic enforcement depends on sealed policy. To inspect an exact monotone activation target use manifest --policy-profile mandatory-runtime/v1. This read-only stage1 plan binds a verified seal/config preimage and refuses unrelated changes; it neither activates policy nor supplies an Apply clearance. For supported activation, review the exact plan hash, run policy-checkout --profile mandatory-runtime/v1 --expected-plan-sha256 HASH --purpose TEXT, complete review/policy_review.json, then policy-prepare, policy-verify and policy-apply. The dedicated transaction forces searchable TX documentation before changing either flag. policy-abort closes an untouched candidate; policy-recover proves exact baseline/target or restores verified pre-heartbeat mixed preimages, without repeating Apply. Foreign bytes remain blocked. Active Guardian or JEV policies without a policy-profile proof route are explicitly unsupported. Never turn a verified plan into manual config/seal edits. vault garbage collection is not implemented by retention. Owners: tx_recycling and tx_retention. For more than512 changed files, inspect one bounded page with tx-recycle-inspect TX --chunk-index N; --chunk-size may reduce the next page below512 to fit the unchanged source byte budget. Submit each exact chunk proposal through tx-recycle-submit. Accepted chunks are immutable subordinate PARTIAL Markdown/SQL evidence, not review or compaction clearance. Inspect again without --chunk-index for a fresh global completion proposal; submit only after every ordered range and global File-Change ID is covered without gaps or overlap. Existing evidence, candidate protection, retention lock, outbox, archive and full SQL proof still apply. Query partial work using graph --inventory tx_chunks --field transaction_id --value TX. The parent retains eighteen anchors and hash-bound child references; legacy flat512 review and4000 projection limits are not widened. To find a particular file in accepted intermediate work use graph --inventory tx_chunk_atoms --field path --value RELATIVE_PATH. This is a SQL view over existing bounded chunk JSON, not another evidence copy. It exposes PARTIAL status and a separate parent_reviewed flag; neither an isolated chunk nor a search hit grants completion.",
     "tx-space-audit tx-pool-status tx-recycle-status tx-recycle-inspect tx-recycle-submit tx-recycle-drain tx-compact tx-rehydrate tx-recycle-retire-orphan policy-checkout policy-prepare policy-verify policy-apply policy-recover policy-abort"),
    ("s-history", "Historical ownership / Historische Zuordnung",
     "Inspect legacy transaction scope, preserve attributable history, and bind only proved existing evidence.",
     "Historische TX und alte Arbeiten zuordnen: tx-scope-inspect is inspection, tx-history-intake and tx-scope-bind require exact report hashes and reasons. tx-note records an evidenced observation or reuse annotation; it does not grant code correctness or authority. Never fabricate earlier scope or reopen a completed lease. Owners: engine, tx_documents, tx_lifecycle.",
     "tx-scope-inspect tx-history-intake tx-scope-bind tx-note"),
    ("s-membership", "Source topology and compiler authority / Dateien und Build-Autoritaet",
     "Create, remove or rename static sources through source-set migration; compiler/build authority uses its stronger migration owner.",
     "Dateien hinzufuegen, loeschen, umbenennen: source-set-checkout creates a static membership candidate. Existing source byte edits remain an ordinary metadata patch. source-set-prepare, source-set-verify and source-set-apply validate and install membership; source-set-abort abandons it. C-family/build authority uses authority-checkout, authority-prepare, authority-verify, authority-apply or authority-abort with compiler-produced proof. scientific-stage-runner-authority stages only explicitly bound runner authority. Never infer membership from imports or filenames. Owners: universal_migration, migration, corpus, scientific.",
     "source-set-checkout source-set-prepare source-set-verify source-set-apply source-set-abort authority-checkout authority-prepare authority-verify authority-apply authority-abort scientific-stage-runner-authority"),
    ("s-maintenance", "Document authorities and legacy repair / Dokumente und Altbestand",
     "Document-only auxiliary changes require configured authority; bootstrap repair is a bounded legacy maintenance route.",
     "Dokumente ohne Code aendern: auxiliary-checkout, auxiliary-verify and auxiliary-apply admit only configured process authorities; auxiliary-abort abandons their candidate. bootstrap-stage, bootstrap-apply and bootstrap-abort handle only the supported legacy document repair boundary, not arbitrary broken seals or unrestricted rule edits. Guardian enforcement can refuse unsupported maintenance. Owners: auxiliary, repair, blueprint, header_blueprint.",
     "auxiliary-checkout auxiliary-verify auxiliary-apply auxiliary-abort bootstrap-stage bootstrap-apply bootstrap-abort framework-documents-plan framework-documents-checkout"),
    ("s-guardian", "Guarded investigation / Bewachte Untersuchung",
     "Guardian sessions require canonical Memory, ordered state evidence and state-bound execution tickets.",
     "Guardian, Memory und Untersuchung: guard-start establishes the Human problem and Memory binding; guard-enter issues the required state ticket, guard-step records the evidenced transition, guard-status inspects it. guard-reframe is an admitted return to investigation, not history rewriting. guard-patch-close, guard-fresh-run and guard-reconcile require their exact receipt/state boundaries. A cancelled Guardian is not revived by generic recovery. Owners: guardian and the guardian_* implementation modules.",
     "guard-start guard-enter guard-step guard-status guard-reframe guard-patch-close guard-fresh-run guard-reconcile"),
    ("s-parity", "Project-supplied parity / Projektspezifische Pruefung",
     "Scientific or build parity is optional, project-supplied and explicitly bound; parser presence does not provision an adapter.",
     "Tests, Build und wissenschaftliche Paritaet: scientific-baseline establishes the explicitly configured reference, scientific-reproducibility checks the unchanged sealed build, scientific-verify checks the configured candidate comparison. The canonical package invents no scientific domain, build runner, fixture or result. JEV Apply admission is separately configured and candidate-bound. Owners: scientific, _core_scientific and jev_admission.",
     "scientific-baseline scientific-reproducibility scientific-verify"),
)

WORKSHOP_MODULE_ROLES = {
    "__init__.py": "package identity; no standalone management command",
    "__main__.py": "installed module entrypoint delegates to public CLI",
    "_cli_core_v1.py": "restricts legacy CLI loading to the public wrapper",
    "_cli_implementation.py": "base parser and command dispatch behind the public wrapper",
    "_core_apply.py": "ordinary candidate installation, rollback and postcheck",
    "_core_checkout.py": "ordinary source checkout and baseline capture",
    "_core_contract.py": "typed contract shared by engine core mixins",
    "_core_metadata.py": "source/context metadata review integration",
    "_core_prepare.py": "ordinary package preparation and mechanical metadata",
    "_core_recovery.py": "ordinary abort, stale checkout and recovery boundaries",
    "_core_scientific.py": "engine integration of explicitly configured parity",
    "_core_state.py": "lease, seal and atomic transaction state primitives",
    "_core_verify.py": "ordinary candidate shadow verification",
    "_engine_core_v1.py": "restricts access to the composed private engine",
    "_engine_implementation.py": "composes the private engine mixins",
    "auxiliary.py": "configured document-only transaction owner",
    "blueprint.py": "blueprint parsing, exact ledgers and mechanical regeneration",
    "cli.py": "supported public parser, Guardian gates and dispatch",
    "context_documents.py": "baseline-bound semantic Markdown review and projection",
    "corpus.py": "configuration, exact membership, include authority and corpus checks",
    "engine.py": "public Workshop operations and owner composition",
    "guardian.py": "composes the investigation Guardian",
    "guardian_close.py": "Guardian close and fresh-run completion operations",
    "guardian_common.py": "shared Guardian policy, Memory, scope and locking primitives",
    "guardian_evidence.py": "Guardian evidence validation",
    "guardian_ledger.py": "Guardian ledger persistence and observations",
    "guardian_patch.py": "Guardian patch-session control",
    "guardian_receipts.py": "Guardian receipt binding",
    "guardian_state.py": "Guardian state entry and advancement",
    "guardian_transaction.py": "Guardian transaction-event ownership and acknowledgement",
    "header_blueprint.py": "header-only source blueprint generation",
    "jev_admission.py": "candidate-bound optional JEV Apply clearance",
    "metadata_gate.py": "actual semantic header deltas and deterministic code sample audit",
    "migration.py": "compiler/build authority migration owner",
    "repair.py": "bounded legacy bootstrap repair owner",
    "scientific.py": "optional project-supplied parity adapter boundary",
    "tx_documents.py": "governed TX Markdown/SQL publication and pending acknowledgement",
    "tx_lifecycle.py": "event identities, native intents and exact replay provenance",
    "tx_recycling.py": "evidence-bound per-file classification before compaction",
    "tx_retention.py": "exact-byte vault, compaction and separate-target restore; no vault GC",
    "universal_migration.py": "static source membership create/delete/rename owner",
    "util.py": "hashes, confined paths, atomic I/O, preimages and SQLite snapshots",
}


def verify_tool_inventory(commands: list[str], modules: list[str]) -> None:
    """Refuse unclassified public commands, package files and duplicate routes."""
    declared = [name for group in WORKSHOP_TOOL_GROUPS for name in group[4].split()]
    if (len(declared) != len(set(declared)) or len(commands) != len(set(commands))
            or set(commands) != set(declared)):
        raise ValueError("tool routing command inventory differs from the public Workshop parser")
    if len(modules) != len(set(modules)) or set(modules) != set(WORKSHOP_MODULE_ROLES):
        raise ValueError("tool routing module inventory differs from the Workshop package")


def tool_catalog_source_paths() -> tuple[str, ...]:
    return ("kairos/kairos_harness/kairos/framework.py",
            "kairos/kairos_harness/kairos/query.py",
            "kairos/kairos_harness/kairos/search.py") + tuple(
        "workshop/src/runtime_sync_workshop/" + name for name in sorted(WORKSHOP_MODULE_ROLES)
    )


def tool_catalog_document(workspace_id: str, *, updated_at: str = "2000-01-01T00:00:00Z") -> str:
    """Render one derived release router; existing operating contracts own rules."""
    from .templates import pointer, render_document
    framework = workspace_id == FRAMEWORK_WORKSPACE_ID
    reference = pointer(
        "workshop/OPERATIONS.md" if framework else "AGENTS.md",
        section="s-overview" if framework else "s-operating-loop",
        artifact_id="KAIROS_WORKSHOP_OPERATIONS" if framework else "KAIROS_OPERATING_CONTRACT",
        version="dynamic", relation="requires", tags=("operations", "authority"), source="system",
    )
    questions = (
        ("How do I choose a workspace tool?", "s-entry", "procedure"),
        ("What is the workspace tool entry point?", "s-entry", "orientation"),
        ("How do I change governed source?", "s-patch", "procedure"),
        ("How do I repair TX documentation?", "s-recovery", "procedure"),
        ("How do I safely enable automatic transaction recycling and archive old worktrees?", "s-retention", "procedure"),
        ("How do I inspect historical ownership?", "s-history", "procedure"),
        ("How do I change source membership?", "s-membership", "procedure"),
        ("How do I repair legacy documents?", "s-maintenance", "procedure"),
        ("How do I run a guarded investigation?", "s-guardian", "procedure"),
        ("Which tools check project parity?", "s-parity", "procedure"),
    )
    metadata = {
        "schema": "kairos-context/v1", "id": TOOL_CATALOG_ID, "type": "router",
        "revision": 1, "state": "active", "authority": "routing", "workspace": workspace_id,
        "route": f"{workspace_id}/{TOOL_CATALOG_ID}", "updated_at": updated_at,
        "capsule": "Situation-based Werkfaden workspace tool routing: transaction recovery, missing reports, metadata, recycling, source authority and Guardian.",
        "claim_boundary": "Derived release routing only. Operating contracts and configured native gates own authority; command presence does not prove live availability or execution success.",
        "entities": [TOOL_CATALOG_ID, "Werkfaden", "Workshop"],
        "facets": ["workspace-management", "tools", "recovery", "recycling", "Werkzeuge"],
        "criteria": [], "does_not_answer": ["live project state", "criterion acceptance", "permission to bypass a gate"],
        "answers": [{"intent": intent, "question": question, "target": target}
                    for question, target, intent in questions],
        "refs": {"operating_contract": reference},
        "search_contract": [{"query": q, "expected": TOOL_CATALOG_ID + "#" + t, "required_top_k": 5}
                            for q, t, _ in questions[:2]],
    }
    sections = []
    for anchor, title, capsule, explanation, commands in WORKSHOP_TOOL_GROUPS:
        entry = ""
        if anchor == "s-entry":
            entry = (
                f"Release routing for KAIROS {FRAMEWORK_VERSION}. From a source checkout use "
                "`python workshop/workshop.py --config <selected-project-config> <command>`; "
                "with Workshop installed use `python -m runtime_sync_workshop --config <selected-project-config> <command>`. "
                "Use `<command> --help` for exact arguments. Paths bind to the selected project/config root, "
                "not a specially named checkout. Internal Python modules are implementation owners, not scripts to execute. "
                "For tasks, reports, bugs and heartbeat publication use "
                "[ref:docs/KAIROS_CONTEXT_TOOLS.md#s-records|id:KAIROS_CONTEXT_TOOLS|v:dynamic|rel:references|tags:tools|src:system]. "
                "If the package/adapter is unavailable, establish the documented installation/binding first.\n\n"
            )
        sections.append({"id": anchor, "title": title, "capsule": capsule,
                         "content": entry + explanation + "\n\nPublic commands: "
                         + ", ".join("`" + name + "`" for name in commands.split()) + "."})
    sections.append({
        "id": "s-owners", "title": "Implementation routing / Implementierungsbesitzer",
        "capsule": "Workshop module roles; private helpers are not public commands.",
        "content": "Module prefix: `runtime_sync_workshop`. These roles route source inspection, not project source membership. "
                   "Build-time parser/package coverage and source bindings belong to the immutable bundle manifest.\n\n"
                   + "\n".join("- `" + name + "`: " + role + "." for name, role in sorted(WORKSHOP_MODULE_ROLES.items())),
    })
    return render_document(metadata, "Werkfaden workspace tools / Werkzeuge zur Workspace-Verwaltung", sections)

# These are the authored framework sources admitted to the bootstrap corpus. Templates, project
# self-model history and generated release/runtime state are deliberately outside this surface.
FRAMEWORK_DOCUMENT_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    "workspace/docs/EVIDENCE_BASED_AI_REVIEW.md",
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/CANONICAL_DOCUMENTS.md",
    "docs/DATABASE_AND_DYNAMICS.md",
    "docs/EXTERNAL_AUDIT_SCOPE.md",
    "docs/FRAMEWORK_BOOTSTRAP.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/LOOP_PROCEDURE.md",
    "docs/OPERATING_MODEL.md",
    "docs/PORTABILITY_SCOPE.md",
    "docs/PROJECT_KICKSTART.md",
    "docs/UNIVERSAL_ONBOARDING.md",
    "docs/WORKSHOP.md",
    "kairos/kairos_harness/docs/CONTEXT_HEADER_SPEC.md",
    "kairos/kairos_harness/docs/INGESTED_DOCUMENT_SPEC.md",
    "kairos/kairos_harness/docs/LLM_OPERATING_CONTRACT.md",
    "kairos/kairos_harness/docs/OPERATIONS.md",
    "kairos/kairos_harness/docs/RETRIEVAL_METHOD.md",
    "kairos/kairos_harness/docs/SEARCH_INDEX.md",
    "kairos/kairos_harness/docs/SYSTEM_SYNTHESIS.md",
    "workshop/README.md",
    "workshop/OPERATIONS.md",
    "workshop/SECURITY_BOUNDARY.md",
)



# This tuple is a required core floor, never the complete distributed corpus.
# Coverage is derived from the same bounded product surfaces as the exporter.
FRAMEWORK_SOURCE_ROLES = frozenset({
    "current_authority", "navigation", "reference", "historical_evidence",
    "publication_view", "quarantine_notice",
})
FRAMEWORK_RENDER_TEMPLATES = tuple(
    "kairos/kairos_harness/templates/" + name + "_TEMPLATE.md"
    for name in ("BUG", "CODE", "DECISION", "REPORT", "RESEARCH", "ROUTER", "TASK")
)
_FRAMEWORK_HEADER_FIELDS = frozenset({
    "schema", "id", "type", "revision", "state", "authority", "workspace", "route",
    "updated_at", "capsule", "claim_boundary", "entities", "facets", "criteria",
    "does_not_answer", "answers", "refs", "search_contract", "distribution_role",
})


def framework_markdown_inventory(root: Path) -> list[dict[str, Any]]:
    """Classify every exported Markdown input; no silent extension or SQL omission."""
    from .frontmatter import split_frontmatter, validate_metadata
    from .sections import parse_sections, validate_answer_targets
    from .golden import _framework_source_entries

    rows = []
    ids = set()
    for entry in _framework_source_entries(root):
        relative = entry["path"]
        if Path(relative).suffix.lower() != ".md":
            continue
        if relative in FRAMEWORK_RENDER_TEMPLATES:
            rows.append({"path": relative, "sha256": entry["sha256"],
                         "role": "render_template",
                         "catalog": "KAIROS_CANONICAL_DOCUMENTS#s-render-templates"})
            continue
        raw = (root / relative).read_bytes()
        if _sha256_bytes(raw) != entry["sha256"]:
            raise RuntimeError(f"framework Markdown changed during inventory: {relative}")
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        parsed = split_frontmatter(text)
        metadata = parsed.metadata
        missing = sorted(_FRAMEWORK_HEADER_FIELDS - metadata.keys())
        if missing:
            raise RuntimeError(f"framework Markdown has incomplete metadata: {relative}: {missing}")
        validate_metadata(metadata)
        if parsed.header_bytes > 3072:
            raise RuntimeError(f"framework Markdown header exceeds 3072 bytes: {relative}")
        if metadata["workspace"] != FRAMEWORK_WORKSPACE_ID or metadata["route"] != (
                FRAMEWORK_WORKSPACE_ID + "/" + metadata["id"]):
            raise RuntimeError(f"framework Markdown has foreign namespace: {relative}")
        if metadata["distribution_role"] not in FRAMEWORK_SOURCE_ROLES:
            raise RuntimeError(f"framework Markdown has unclassified role: {relative}")
        sections = parse_sections(parsed.body, header_bytes=parsed.header_bytes, ingested=False)
        validate_answer_targets(metadata, sections)
        if not metadata["answers"] or not metadata["search_contract"]:
            raise RuntimeError(f"framework Markdown lacks retrieval contract: {relative}")
        if metadata["id"] in ids:
            raise RuntimeError(f"duplicate framework Markdown identity: {metadata['id']}")
        ids.add(metadata["id"])
        rows.append({"path": relative, "sha256": entry["sha256"],
                     "content_sha256": _sha256_bytes(text.encode("utf-8")),
                     "role": metadata["distribution_role"], "artifact_id": metadata["id"]})
    if not set(FRAMEWORK_DOCUMENT_PATHS).issubset(
            row["path"] for row in rows if row["role"] != "render_template"):
        raise RuntimeError("framework Markdown is missing required core sources")
    if not set(FRAMEWORK_RENDER_TEMPLATES).issubset(row["path"] for row in rows):
        raise RuntimeError("framework Markdown is missing required render templates")
    return rows


def framework_document_authority_snapshot(root: Path, declaration: dict[str, Any]) -> dict[str, Any]:
    """Verify one complete physical framework product without importing project state."""
    before = _framework_plan_files(root)
    coverage = framework_markdown_inventory(root)
    if sorted(row["path"] for row in coverage) != declaration["documents"]:
        raise RuntimeError("FRAMEWORK_DOCUMENT_MEMBERSHIP_DRIFT: declaration differs from complete Markdown inventory")
    bundle = verify_framework_bundle(source_root=root)
    if _framework_plan_files(root) != before:
        raise RuntimeError("FRAMEWORK_DOCUMENT_SNAPSHOT_CHANGED: product changed during verification")
    files = {row["path"]: row for row in before}
    prefix = "kairos/kairos_harness/kairos/"
    return dict(schema=declaration["schema"], root=".", namespace=declaration["namespace"],
                documents=coverage, database_content_sha256=bundle["database_content_sha256"],
                projection_files={name: files[prefix + name] for name in
                                  (FRAMEWORK_DATABASE_NAME, FRAMEWORK_MANIFEST_NAME)})


def framework_markdown_coverage_sha256(rows: list[dict[str, Any]]) -> str:
    return _sha256_bytes(json.dumps(rows, ensure_ascii=True, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8"))


def _framework_manifest_documents(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the source/template partition even without a source tree installed."""
    schema = manifest.get("schema")
    if schema not in {"kairos-framework-index/v2", FRAMEWORK_MANIFEST_SCHEMA}:
        raise RuntimeError("unsupported framework manifest identity schema")
    content_fields = ("content_sha256",) if schema == FRAMEWORK_MANIFEST_SCHEMA else ()
    rows = manifest.get("markdown_coverage")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("framework manifest lacks complete Markdown coverage")
    paths = []
    docs = []
    templates = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("invalid framework Markdown coverage entry")
        path = row.get("path")
        digest = row.get("sha256")
        if (not isinstance(path, str) or not path.endswith(".md") or "\\" in path
                or path.startswith("/") or ":" in path or ".." in Path(path).parts
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise RuntimeError("invalid framework Markdown coverage path or hash")
        paths.append(path)
        if row.get("role") == "render_template":
            if (path not in FRAMEWORK_RENDER_TEMPLATES or set(row) != {
                    "path", "sha256", "role", "catalog"} or row["catalog"] != (
                    "KAIROS_CANONICAL_DOCUMENTS#s-render-templates")):
                raise RuntimeError("unclassified framework render template")
            templates.append(path)
        elif row.get("role") in FRAMEWORK_SOURCE_ROLES and set(row) == {
                "path", "sha256", "role", "artifact_id", *content_fields}:
            if content_fields:
                content_digest = row["content_sha256"]
                if (not isinstance(content_digest, str) or len(content_digest) != 64
                        or any(c not in "0123456789abcdef" for c in content_digest)):
                    raise RuntimeError("invalid framework canonical content hash")
            docs.append({k: row[k] for k in ("path", "artifact_id", "sha256", *content_fields)})
        else:
            raise RuntimeError("unclassified framework Markdown coverage entry")
    if (paths != sorted(set(paths)) or set(templates) != set(FRAMEWORK_RENDER_TEMPLATES)
            or not set(FRAMEWORK_DOCUMENT_PATHS).issubset(row["path"] for row in docs)
            or manifest.get("markdown_coverage_sha256") != framework_markdown_coverage_sha256(rows)
            or manifest.get("documents") != docs):
        raise RuntimeError("framework Markdown coverage and index admission mismatch")
    return docs


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sqlite_value(value: object) -> list[object]:
    """Encode SQLite values without depending on the host SQLite file format."""
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, str):
        return ["text", value]
    return [type(value).__name__, repr(value)]


def framework_database_content_sha256(path: Path | str | None = None) -> str:
    """Hash framework database content independently of SQLite build metadata.

    SQLite may change the file header, schema cookie, statistics tables, and page layout
    across operating systems or SQLite versions.  Those bytes remain useful for bundle
    integrity, but they are not a portable rebuild identity.  This digest covers the
    executable schema and row content while excluding SQLite's derived ``sqlite_stat*``
    tables.
    """
    database_path = Path(path) if path is not None else framework_database_path()
    resolved = database_path.resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_stat%' ORDER BY type, name"
        ).fetchall()
        canonical: list[object] = []
        for object_type, name, table_name, sql in objects:
            canonical.append(["schema", object_type, name, table_name, sql])
            if object_type != "table":
                continue
            identifier = '"' + str(name).replace('"', '""') + '"'
            try:
                rows = connection.execute(f"SELECT rowid, * FROM {identifier}").fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute(f"SELECT * FROM {identifier}").fetchall()
            encoded_rows = [
                [_canonical_sqlite_value(value) for value in row]
                for row in rows
            ]
            encoded_rows.sort(
                key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            )
            canonical.append(["rows", name, encoded_rows])
        payload = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return _sha256_bytes(payload)
    finally:
        connection.close()


def package_directory() -> Path:
    return Path(__file__).resolve().parent


def source_distribution_root() -> Path | None:
    """Return the source-release root when the package is running from the KAIROS tree."""
    candidate = package_directory().parents[2]
    if (candidate / "AGENTS.md").is_file() and (candidate / "docs").is_dir():
        return candidate
    return None


def framework_database_path() -> Path:
    return package_directory() / FRAMEWORK_DATABASE_NAME


def framework_manifest_path() -> Path:
    return package_directory() / FRAMEWORK_MANIFEST_NAME


def load_framework_manifest(path: Path | None = None) -> dict[str, Any]:
    path = path if path is not None else framework_manifest_path()
    if not path.is_file():
        raise RuntimeError(
            f"KAIROS framework manifest is missing: {path}. Reinstall the release or rebuild the framework index."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") not in {"kairos-framework-index/v2", FRAMEWORK_MANIFEST_SCHEMA}:
        raise RuntimeError(f"unsupported framework manifest schema: {payload.get('schema')!r}")
    if payload.get("workspace_id") != FRAMEWORK_WORKSPACE_ID:
        raise RuntimeError("framework manifest workspace identity mismatch")
    return payload


def verify_framework_bundle(*, verify_sources: bool = True,
                            source_root: Path | None = None,
                            database_path: Path | None = None,
                            manifest_path: Path | None = None) -> dict[str, Any]:
    package = source_root / "kairos/kairos_harness/kairos" if source_root is not None else package_directory()
    manifest = load_framework_manifest(manifest_path if manifest_path is not None else package / FRAMEWORK_MANIFEST_NAME)
    database_path = database_path if database_path is not None else package / FRAMEWORK_DATABASE_NAME
    if not database_path.is_file():
        raise RuntimeError(
            f"KAIROS framework database is missing: {database_path}. Reinstall the release or rebuild the framework index."
        )
    database_sha = _sha256_bytes(database_path.read_bytes())
    expected_database_sha = str(manifest.get("database_sha256", ""))
    if database_sha != expected_database_sha:
        raise RuntimeError(
            f"framework database SHA-256 mismatch: {database_sha} != {expected_database_sha}"
        )
    database_content_sha = framework_database_content_sha256(database_path)
    expected_database_content_sha = manifest.get("database_content_sha256")
    if expected_database_content_sha and database_content_sha != expected_database_content_sha:
        raise RuntimeError(
            "framework database content SHA-256 mismatch: "
            f"{database_content_sha} != {expected_database_content_sha}"
        )

    entries = _framework_manifest_documents(manifest)
    generated = manifest.get("generated_documents", [])
    definitions = tool_catalog_definitions()
    if not isinstance(generated, list) or len(generated) != len(definitions):
        raise RuntimeError("framework generated tool router inventory mismatch")
    expected_routers = {}
    binding_map = {}
    for row, (artifact_id, relative, renderer, source_paths) in zip(generated, definitions):
        expected_sha = _sha256_bytes(renderer(FRAMEWORK_WORKSPACE_ID).encode("utf-8"))
        if (not isinstance(row, dict) or row.get("artifact_id") != artifact_id
                or row.get("path") != relative or row.get("sha256") != expected_sha
                or row.get("authority") != "routing"):
            raise RuntimeError("framework generated tool router does not match its executable definition")
        rows = row.get("source_bindings", [])
        if (not isinstance(rows, list) or tuple(
                item.get("path") for item in rows if isinstance(item, dict)) != source_paths()):
            raise RuntimeError("framework tool router source inventory mismatch")
        for binding in rows:
            previous = binding_map.setdefault(binding["path"], binding)
            if previous != binding:
                raise RuntimeError("framework tool router source binding disagreement")
        expected_routers[artifact_id] = (relative, expected_sha)
    bindings = list(binding_map.values())
    expected_ids = {entry.get("artifact_id") for entry in entries} | set(expected_routers)
    connection = sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        actual_ids = {row[0] for row in connection.execute("SELECT artifact_id FROM artifacts")}
        source_rows = {row[0]: (row[1], row[2]) for row in connection.execute(
            "SELECT artifact_id, path, content_sha256 FROM artifacts")
            if row[0] not in expected_routers}
        template_catalog_present = connection.execute(
            "SELECT COUNT(*) FROM sections WHERE artifact_id = ? AND section_id = ?",
            ("KAIROS_CANONICAL_DOCUMENTS", "s-render-templates")).fetchone()[0] == 1
        router_rows = {artifact_id: connection.execute(
            "SELECT path, content_sha256 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone() for artifact_id in expected_routers}
    finally:
        connection.close()
    if (len(expected_ids) != len(entries) + len(definitions) or actual_ids != expected_ids
            or source_rows != {row["artifact_id"]: (row["path"], row.get("content_sha256", row["sha256"]))
                               for row in entries}
            or not template_catalog_present
            or router_rows != expected_routers or manifest.get("document_count") != len(expected_ids)):
        raise RuntimeError("framework database artifact inventory or tool router binding mismatch")

    verified_sources = 0
    verified_generated_sources = 0
    root = (source_root if source_root is not None else source_distribution_root()) if verify_sources else None
    if root is not None:
        live_coverage = framework_markdown_inventory(root)
        if manifest["schema"] == "kairos-framework-index/v2":
            live_coverage = [{k: v for k, v in row.items() if k != "content_sha256"}
                             for row in live_coverage]
        if live_coverage != manifest["markdown_coverage"]:
            raise RuntimeError("framework distributed Markdown coverage differs from the index")
        for entry in entries:
            relative = str(entry["path"])
            source = root / relative
            if not source.is_file():
                raise RuntimeError(f"framework source is missing: {relative}")
            actual = _sha256_bytes(source.read_bytes())
            if actual != entry.get("sha256"):
                raise RuntimeError(
                    f"framework source SHA-256 mismatch for {relative}: {actual} != {entry.get('sha256')}"
                )
            verified_sources += 1
        for binding in bindings:
            path = root / binding["path"]
            if not path.is_file() or _sha256_bytes(path.read_bytes()) != binding.get("sha256"):
                raise RuntimeError(f"framework tool router source SHA-256 mismatch: {binding['path']}")
            verified_generated_sources += 1
    elif verify_sources:
        # Installed packages do not contain source-release Markdown. Their local
        # generator must still match the source-bound release router; Workshop
        # capabilities remain release routing, not a claim about installed adapters.
        for binding in bindings:
            if not binding["path"].startswith("kairos/kairos_harness/kairos/"):
                continue
            path = package_directory() / Path(binding["path"]).name
            if not path.is_file() or _sha256_bytes(path.read_bytes()) != binding.get("sha256"):
                raise RuntimeError("installed framework tool router implementation SHA-256 mismatch")
            verified_generated_sources += 1

    return {
        "verified": True,
        "schema": manifest["schema"],
        "version": manifest.get("version", FRAMEWORK_VERSION),
        "database": str(database_path),
        "database_sha256": database_sha,
        "database_content_sha256": database_content_sha,
        "document_count": int(manifest.get("document_count", 0)),
        "source_documents_verified": verified_sources,
        "generated_source_files_verified": verified_generated_sources,
    }


def _framework_plan_path(path: Path) -> None:
    try:
        reject_link_components(path, label="framework plan path")
    except (ValueError, OSError) as exc:
        raise RuntimeError(str(exc)) from exc


def _framework_plan_files(root: Path) -> list[dict[str, Any]]:
    """Bound and stabilize product reads before producing a document-only plan."""
    import stat
    from .golden import (
        _FRAMEWORK_ROOT_FILES, _FRAMEWORK_ROOT_DIRS, _FRAMEWORK_PRUNED,
        _framework_source_entries,
    )

    def facts() -> dict[str, tuple[int, ...]]:
        result = {}
        total = 0
        _framework_plan_path(root)

        def inspect(path: Path) -> None:
            nonlocal total
            if is_link_or_junction(path):
                raise RuntimeError("framework plan refuses links or junctions")
            if path.name in _FRAMEWORK_PRUNED:
                return
            try:
                value = path.stat()
            except FileNotFoundError as exc:
                raise RuntimeError("framework plan required product input missing: " +
                                   path.relative_to(root).as_posix()) from exc
            if stat.S_ISDIR(value.st_mode):
                for child in sorted(path.iterdir()):
                    inspect(child)
                return
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                raise RuntimeError("framework plan requires singly linked regular files")
            if value.st_size > 32 * 1024 * 1024:
                raise RuntimeError("framework plan file exceeds 32 MiB read budget")
            total += value.st_size
            if len(result) >= 4096 or total > 256 * 1024 * 1024:
                raise RuntimeError("framework plan exceeds product read budget")
            relative = path.relative_to(root).as_posix()
            result[relative] = (value.st_size, value.st_mtime_ns, value.st_ino,
                                value.st_dev, value.st_mode, value.st_nlink)

        for relative in (*_FRAMEWORK_ROOT_FILES, *_FRAMEWORK_ROOT_DIRS):
            path = root / relative
            _framework_plan_path(path)
            inspect(path)
        return result

    before = facts()
    entries = _framework_source_entries(root)
    if facts() != before:
        raise RuntimeError("framework plan product changed during inspection")
    portable = [row["path"].casefold() for row in entries]
    if len(portable) != len(set(portable)):
        raise RuntimeError("framework plan product paths collide portably")
    return entries


def framework_document_change_plan(source_root: Path, candidate_root: Path) -> dict[str, Any]:
    """Read-only exact change proof; never grants admission, mutation or a seal."""
    roots = []
    for supplied in (source_root, candidate_root):
        path = Path(supplied).absolute()
        _framework_plan_path(path)
        if not path.is_dir():
            raise RuntimeError("framework plan requires explicit source directories")
        roots.append(path.resolve(strict=True))
    source, candidate = roots
    if source.is_relative_to(candidate) or candidate.is_relative_to(source):
        raise RuntimeError("framework plan roots must be disjoint")
    baseline = _framework_plan_files(source)
    target = _framework_plan_files(candidate)
    derived = {
        "kairos/kairos_harness/kairos/" + FRAMEWORK_DATABASE_NAME,
        "kairos/kairos_harness/kairos/" + FRAMEWORK_MANIFEST_NAME,
    }
    before = {row["path"]: row for row in baseline}
    after = {row["path"]: row for row in target}
    for relative in sorted(before.keys() | after.keys()):
        if Path(relative).suffix.lower() != ".md" and relative not in derived:
            if before.get(relative) != after.get(relative):
                raise RuntimeError("framework document plan changes non-Markdown product input: " + relative)
    # The old tree may have incomplete headers or a legacy index. Only the
    # candidate may supply current retrieval authority, through the real verifier.
    coverage = framework_markdown_inventory(candidate)
    bundle = verify_framework_bundle(source_root=candidate)
    if _framework_plan_files(source) != baseline or _framework_plan_files(candidate) != target:
        raise RuntimeError("framework plan product changed during verification")
    changes = []
    for relative in sorted(before.keys() | after.keys()):
        if Path(relative).suffix.lower() != ".md" or before.get(relative) == after.get(relative):
            continue
        old, new = before.get(relative), after.get(relative)
        changes.append({"path": relative,
                        "operation": "add" if old is None else "delete" if new is None else "replace",
                        "before": old, "after": new})
    plan = {
        "schema": "werkfaden-framework-document-change-plan/v1",
        "namespace": FRAMEWORK_WORKSPACE_ID,
        "read_only": True, "mutation_authorized": False,
        "authority_admitted": False, "transaction_required": True,
        "baseline_product": baseline, "candidate_product": target,
        "markdown_coverage": coverage, "changes": changes,
        "candidate_bundle": {key: bundle[key] for key in (
            "schema", "database_sha256", "database_content_sha256", "document_count",
            "source_documents_verified", "generated_source_files_verified")},
    }
    plan["plan_sha256"] = _sha256_bytes(json.dumps(
        plan, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    return plan


def search_framework(
    query: str,
    *,
    limit: int = 10,
    candidate_limit: int = 100,
    mode: str = "breadth",
    max_hops: int = 2,
) -> dict[str, Any]:
    verification = verify_framework_bundle()
    database = KnowledgeDatabase(framework_database_path())
    result = search_database(
        database,
        query,
        limit=limit,
        candidate_limit=candidate_limit,
        mode=mode,
        max_hops=max_hops,
        record_trace=False,
    )
    result["corpus"] = "framework"
    result["framework"] = verification
    result["freshness"] = {
        "checked": True,
        "refreshed": False,
        "reason": "immutable hash-verified framework projection",
    }
    for item in result.get("results", []):
        item["corpus"] = "framework"
    if isinstance(result.get("primary"), dict):
        result["primary"]["corpus"] = "framework"
    for item in result.get("context_chase", []):
        item["corpus"] = "framework"
    return result
