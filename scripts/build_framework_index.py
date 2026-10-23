from __future__ import annotations

import argparse
import hashlib
import json
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
    FRAMEWORK_DOCUMENT_PATHS,
    FRAMEWORK_INDEX_WORKSPACE_ID,
    FRAMEWORK_MANIFEST_NAME,
    FRAMEWORK_VERSION,
    FRAMEWORK_WORKSPACE_ID,
    framework_database_content_sha256,
)
from kairos.promoter import promote_many  # noqa: E402
import kairos.promoter as promoter_module  # noqa: E402
from kairos.search import search_database  # noqa: E402

BUILD_EPOCH = "2000-01-01T00:00:00Z"
GOLD_QUERIES: tuple[tuple[str, tuple[str, ...], int], ...] = (
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


def materialize_sources(workspace: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in FRAMEWORK_DOCUMENT_PATHS:
        source = ROOT / relative
        if not source.is_file():
            raise SystemExit(f"framework source missing: {relative}")
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
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
    with tempfile.TemporaryDirectory(prefix="kairos-framework-index-") as temp:
        workspace = Path(temp)
        write_config(workspace)
        documents = materialize_sources(workspace)
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

    documents_manifest = []
    for relative in FRAMEWORK_DOCUMENT_PATHS:
        source = ROOT / relative
        # Header id is deliberately read with a tiny local parser avoidance: full validation already
        # occurred during promotion, and this keeps the generated manifest free of parser duplication.
        text = source.read_text(encoding="utf-8")
        marker = '\nid = "'
        start = text.find(marker)
        if start < 0:
            if text.startswith('id = "'):
                start = -1
            else:
                raise SystemExit(f"framework document lacks id header: {relative}")
        if start == -1:
            value_start = len('id = "')
        else:
            value_start = start + len(marker)
        value_end = text.find('"', value_start)
        artifact_id = text[value_start:value_end]
        documents_manifest.append({"path": relative, "artifact_id": artifact_id, "sha256": sha256(source)})

    return {
        "schema": "kairos-framework-index/v1",
        "version": FRAMEWORK_VERSION,
        "workspace_id": FRAMEWORK_WORKSPACE_ID,
        "database_file": FRAMEWORK_DATABASE_NAME,
        "database_sha256": sha256(output),
        "database_content_sha256": framework_database_content_sha256(output),
        "document_count": len(documents_manifest),
        "documents": documents_manifest,
        "gold_queries": gold_results,
        "build_epoch": BUILD_EPOCH,
        "derived": True,
        "authority_note": "Framework Markdown owns claims; this database is a hash-verified read-only retrieval projection.",
    }


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

    output = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = build_once(output)
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
    compared_database = None
    if args.compare_content_with:
        compared_database = Path(args.compare_content_with).resolve()
        compared_content_sha = framework_database_content_sha256(compared_database)
        if compared_content_sha != manifest["database_content_sha256"]:
            raise SystemExit(
                "framework DB logical content differs from bundle: "
                f"{manifest['database_content_sha256']} != {compared_content_sha}"
            )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
