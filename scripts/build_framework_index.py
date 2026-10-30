from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "kairos" / "kairos_harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from kairos.database import KnowledgeDatabase  # noqa: E402
from kairos.framework import (  # noqa: E402
    FRAMEWORK_DATABASE_NAME,
    framework_markdown_inventory,
    framework_markdown_coverage_sha256,
    FRAMEWORK_INDEX_WORKSPACE_ID,
    FRAMEWORK_MANIFEST_NAME,
    FRAMEWORK_MANIFEST_SCHEMA,
    FRAMEWORK_VERSION,
    FRAMEWORK_WORKSPACE_ID,
    framework_database_content_sha256,
    TOOL_CATALOG_ID,
    TOOL_CATALOG_PATH,
    tool_catalog_document,
    tool_catalog_source_paths,
    verify_tool_inventory,
    verify_context_tool_inventory,
    verify_framework_bundle,
    tool_catalog_definitions,
)
from kairos.promoter import promote_many  # noqa: E402
import kairos.promoter as promoter_module  # noqa: E402
from kairos.util import reject_link_components  # noqa: E402
from kairos.search import search_database  # noqa: E402

BUILD_EPOCH = "2000-01-01T00:00:00Z"
GOLD_QUERIES: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("How do I create tasks, reports and bug records?", ("KAIROS_CONTEXT_TOOLS#s-records",), 1),
    ("Which command publishes documented work into the database?", ("KAIROS_CONTEXT_TOOLS#s-publication",), 3),
    (
        "How can I recover a transaction whose report is missing from search without applying the code again?",
        ("KAIROS_WORKSPACE_TOOLS#s-recovery",),
        3,
    ),
    (
        "Welche Werkzeuge helfen bei einer blockierten Transaktion und beim Aufraeumen alter Arbeitskopien?",
        ("KAIROS_WORKSPACE_TOOLS#s-retention", "KAIROS_WORKSPACE_TOOLS#s-recovery"),
        3,
    ),
    (
        "How do I find which supported command handles a workspace management problem?",
        ("KAIROS_WORKSPACE_TOOLS#s-entry",),
        3,
    ),
    (
        "How do I query KAIROS rules before a project workspace exists?",
        ("KAIROS_FRAMEWORK_BOOTSTRAP#s-before-project-intake",),
        1,
    ),
    (
        "How do I start a real project with KAIROS?",
        ("KAIROS_README#s-start-a-real-project",),
        1,
    ),
    (
        "What owns translation-unit membership?",
        ("KAIROS_ARCHITECTURE#s-overview",),
        1,
    ),
    (
        "When is authority migration required?",
        ("KAIROS_WORKSHOP#s-authority-migration",),
        1,
    ),
    (
        "What does a successful KAIROS Workshop postcheck not prove?",
        ("KAIROS_KNOWN_LIMITATIONS#s-verification-boundary",),
        1,
    ),
    (
        "How do I onboard an arbitrary existing codebase into KAIROS?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding",),
        1,
    ),
    (
        "After KAIROS seals a project, what is allowed to modify source code or Markdown?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-single-mutation-boundary",),
        1,
    ),
    (
        "How do I create delete or rename governed source files after seal?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-source-set-transactions",),
        1,
    ),
    (
        "How should an LLM acquire context in KAIROS?",
        ("KAIROS_LLM_OPERATING_CONTRACT#s-context-policy",),
        1,
    ),
    (
        "How must I check metadata before applying a source patch?",
        ("KAIROS_WORKSHOP#s-metadata-aftercheck",),
        1,
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_config(workspace: Path) -> None:
    config = {
        "schema": "kairos-workspace-config/v1",
        "workspace_id": FRAMEWORK_INDEX_WORKSPACE_ID,
        "database": ".kairos/framework.db",
        "document_roots": [],
        "canonical_files": [],
        "reconcile_extensions": [".md"],
        "inspection_root": ".",
        "imported_workspace_ids": [FRAMEWORK_WORKSPACE_ID],
        "imported_search_contract_policies": {FRAMEWORK_WORKSPACE_ID: "host_enforced"},
        "fullscan_policy": "recovery_only",
        "governance_enforcement": "required",
        "created_at": BUILD_EPOCH,
    }
    path = workspace / ".kairos" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for directory in ("events", "receipts", "heartbeats"):
        (workspace / ".kairos" / directory).mkdir(parents=True, exist_ok=True)


def materialize_sources(workspace: Path, coverage: list[dict] | None = None) -> list[Path]:
    paths: list[Path] = []
    for row in coverage if coverage is not None else framework_markdown_inventory(ROOT):
        if row["role"] == "render_template":
            continue
        relative = row["path"]
        source = ROOT / relative
        if not source.is_file():
            raise SystemExit(f"framework source missing: {relative}")
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(source) != row["sha256"] or sha256(target) != row["sha256"]:
            raise SystemExit(f"framework source changed during materialization: {relative}")
        paths.append(target)
    # Introspect the public parser without constructing an engine or executing
    # operations. Refuse any command/module that has no reviewed routing entry.
    workshop_source = ROOT / "workshop" / "src"
    if str(workshop_source) not in sys.path:
        sys.path.insert(0, str(workshop_source))
    from runtime_sync_workshop.cli import _parser
    parser = _parser()
    commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    package = workshop_source / "runtime_sync_workshop"
    verify_tool_inventory(list(commands.choices), [path.relative_to(package).as_posix()
                                                for path in sorted(package.rglob("*.py"))])
    from kairos.cli import build_parser
    harness_parser = build_parser()
    harness_commands = next(action for action in harness_parser._actions
                            if isinstance(action, argparse._SubParsersAction))
    verify_context_tool_inventory(list(harness_commands.choices))
    for _, relative, renderer, _ in tool_catalog_definitions():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(renderer(FRAMEWORK_WORKSPACE_ID), encoding="utf-8", newline="\n")
        paths.append(target)
    return paths


def canonicalize_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        sibling = Path(str(path) + suffix)
        if sibling.exists():
            sibling.unlink()


def build_once(output: Path) -> dict:
    coverage = framework_markdown_inventory(ROOT)
    with tempfile.TemporaryDirectory(prefix="kairos-framework-index-") as temp:
        workspace = Path(temp)
        write_config(workspace)
        documents = materialize_sources(workspace, coverage)
        database_path = workspace / ".kairos" / "framework.db"
        database = KnowledgeDatabase(database_path)

        # Promotion receipts/events are retained in the derived DB for auditability. Pinning the
        # derived clock makes the binary reproducible from identical source bytes and tool code.
        original_clock = promoter_module.utc_now
        promoter_module.utc_now = lambda: BUILD_EPOCH
        try:
            promote_many(documents, workspace, database)
        finally:
            promoter_module.utc_now = original_clock

        gold_results = []
        for query, expected, required_top_k in GOLD_QUERIES:
            result = search_database(database, query, limit=max(5, required_top_k), mode="breadth", max_hops=2)
            actual = tuple(item["artifact_id"] + "#" + item["section_id"] for item in result["results"][:required_top_k])
            if not any(target in actual for target in expected):
                raise SystemExit(
                    f"framework gold query failed: {query!r}; expected one of {expected} in top {required_top_k}, got {actual}"
                )
            gold_results.append({
                "query": query,
                "expected": list(expected),
                "required_top_k": required_top_k,
                "primary": (result.get("primary") or {}).get("artifact_id", "") + "#" + (result.get("primary") or {}).get("section_id", ""),
            })

        canonicalize_database(database_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(database_path, output)

    if framework_markdown_inventory(ROOT) != coverage:
        raise SystemExit("framework Markdown membership or bytes changed during build")
    documents_manifest = [{k: row[k] for k in ("path", "artifact_id", "sha256", "content_sha256")}
                          for row in coverage if row["role"] != "render_template"]

    return {
        "schema": FRAMEWORK_MANIFEST_SCHEMA,
        "version": FRAMEWORK_VERSION,
        "workspace_id": FRAMEWORK_WORKSPACE_ID,
        "database_file": FRAMEWORK_DATABASE_NAME,
        "database_sha256": sha256(output),
        "database_content_sha256": framework_database_content_sha256(output),
        "document_count": len(documents_manifest) + len(tool_catalog_definitions()),
        "documents": documents_manifest,
        "markdown_coverage": coverage,
        "markdown_coverage_sha256": framework_markdown_coverage_sha256(coverage),
        "generated_documents": [{
            "path": relative,
            "artifact_id": artifact_id,
            "sha256": hashlib.sha256(renderer(FRAMEWORK_WORKSPACE_ID).encode("utf-8")).hexdigest(),
            "authority": "routing",
            "source_bindings": [{"path": source, "sha256": sha256(ROOT / source)}
                                for source in source_paths()],
        } for artifact_id, relative, renderer, source_paths in tool_catalog_definitions()],
        "gold_queries": gold_results,
        "build_epoch": BUILD_EPOCH,
        "derived": True,
        "authority_note": "Framework Markdown owns claims; this database is a hash-verified read-only retrieval projection.",
    }


def _checked_path(path: Path) -> Path:
    try:
        reject_link_components(path, label="framework builder path")
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    return path.resolve()


def _publish_file(source: Path, target: Path) -> None:
    """Replace one derived output atomically; native installation owns pair recovery."""
    _checked_path(source)
    _checked_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="." + target.name + "-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as destination, source.open("rb") as origin:
            shutil.copyfileobj(origin, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the immutable KAIROS framework retrieval projection")
    parser.add_argument("--output", default=str(HARNESS / "kairos" / FRAMEWORK_DATABASE_NAME))
    parser.add_argument("--manifest", default=str(HARNESS / "kairos" / FRAMEWORK_MANIFEST_NAME))
    parser.add_argument("--verify-determinism", action="store_true")
    parser.add_argument(
        "--compare-content-with",
        help="Require the rebuilt database to have the same logical content as this bundled database",
    )
    args = parser.parse_args()

    _checked_path(Path(__file__))
    output = _checked_path(Path(args.output))
    manifest_path = _checked_path(Path(args.manifest))
    compared_database = _checked_path(Path(args.compare_content_with)) if args.compare_content_with else None
    if output == manifest_path:
        raise SystemExit("framework database and manifest outputs must be distinct")
    with tempfile.TemporaryDirectory(prefix="kairos-framework-publication-") as staged:
        staged_output = Path(staged) / FRAMEWORK_DATABASE_NAME
        staged_manifest = Path(staged) / FRAMEWORK_MANIFEST_NAME
        manifest = build_once(staged_output)
        if args.verify_determinism:
            with tempfile.TemporaryDirectory(prefix="kairos-framework-determinism-") as temp:
                second = Path(temp) / FRAMEWORK_DATABASE_NAME
                second_manifest = build_once(second)
                if sha256(second) != manifest["database_sha256"]:
                    raise SystemExit(
                        f"framework DB is not deterministic: {manifest['database_sha256']} != {sha256(second)}"
                    )
                if framework_database_content_sha256(second) != manifest["database_content_sha256"]:
                    raise SystemExit("framework DB content is not deterministic")
                if [item["sha256"] for item in second_manifest["documents"]] != [item["sha256"] for item in manifest["documents"]]:
                    raise SystemExit("framework document inventory changed between deterministic builds")
                if second_manifest["generated_documents"] != manifest["generated_documents"]:
                    raise SystemExit("framework generated routing sources changed between deterministic builds")
        if compared_database is not None:
            compared_content_sha = framework_database_content_sha256(compared_database)
            if compared_content_sha != manifest["database_content_sha256"]:
                raise SystemExit(
                    "framework DB logical content differs from bundle: "
                    f"{manifest['database_content_sha256']} != {compared_content_sha}"
                )
        staged_manifest.write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        verify_framework_bundle(source_root=ROOT, database_path=staged_output, manifest_path=staged_manifest)
        _publish_file(staged_output, output)
        _publish_file(staged_manifest, manifest_path)
        verify_framework_bundle(source_root=ROOT, database_path=output, manifest_path=manifest_path)
    print(json.dumps({
        "verified": True,
        "database": str(output),
        "database_sha256": manifest["database_sha256"],
        "database_content_sha256": manifest["database_content_sha256"],
        "document_count": manifest["document_count"],
        "gold_queries": len(manifest["gold_queries"]),
        "determinism_checked": bool(args.verify_determinism),
        "content_compared_with": str(compared_database) if compared_database else None,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
