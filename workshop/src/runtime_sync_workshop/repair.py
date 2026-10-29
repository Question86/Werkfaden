from __future__ import annotations

import importlib
from dataclasses import replace
import re
import sqlite3
import sys
from collections import deque
from pathlib import Path
from typing import Any

from .blueprint import (
    complete_missing_mechanical_layers,
    inspect_blueprint_mapping,
    parse_blueprint,
    regenerate_mechanical_layers,
    verify_ledger,
)
from .corpus import (
    INCLUDE_RE,
    SOURCE_SUFFIXES,
    WorkshopConfig,
    _resolve_include,
    build_corpus_manifest,
    cmake_membership,
    header_closure,
)
from .header_blueprint import generate_header_only_blueprint
from .util import source_prefix
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    capture_rollback_preimage,
    copy_exact,
    file_facts,
    logical_lines,
    normalized_sha256,
    normalized_text,
    package_hash,
    read_json,
    restore_rollback_preimage,
    sha256_bytes,
    sqlite_snapshot,
    utc_now,
)


ALLOWED_BOOTSTRAP_ISSUES = {
    "BLUEPRINT_LEDGER_BLOCK_MISSING",
    "BLUEPRINT_LEDGER_LINE_INVALID",
    "BLUEPRINT_MEMBERSHIP_MISMATCH",
    "HEADER_COVERAGE_GAP",
    "HEADER_ONLY_BLUEPRINT_MISSING",
}


def _runtime_tree_manifest(config: WorkshopConfig) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(
        (value for value in config.runtime_root.rglob("*") if value.is_file()),
        key=lambda value: value.relative_to(config.codebase_root).as_posix(),
    ):
        relative = path.relative_to(config.codebase_root).as_posix()
        raw = path.read_bytes()
        rows.append({
            "path": relative,
            "sha256": sha256_bytes(raw),
            "bytes": len(raw),
        })
    return {
        "file_count": len(rows),
        "package_sha256": package_hash(rows),
        "files": rows,
    }


def _runtime_file(config: WorkshopConfig, relative: str | None) -> Path | None:
    if relative is None:
        return None
    path = (config.codebase_root / relative).resolve()
    try:
        path.relative_to(config.runtime_root)
    except ValueError as exc:
        raise WorkshopError(
            "RUNTIME_PATH_ESCAPE",
            f"mapped code file is outside the Runtime root: {relative}",
        ) from exc
    if not path.is_file():
        raise WorkshopError("RUNTIME_FILE_MISSING", f"mapped code file is missing: {relative}")
    return path


def _blueprint_candidates(config: WorkshopConfig) -> list[Path]:
    return [
        path
        for path in sorted(config.blueprint_root.glob("*.md"), key=lambda value: value.name.lower())
        if path.name not in config.blueprint_ignore
    ]


def _exact_sibling_header_from_ledger(
    config: WorkshopConfig,
    blueprint: Path,
    source_relative: str | None,
) -> str | None:
    if source_relative is None:
        return None
    rows = _prefixed_rows(blueprint.read_text(encoding="utf-8"), "H")
    if not rows:
        return None
    if [number for number, _ in rows] != list(range(1, len(rows) + 1)):
        raise WorkshopError(
            "HEADER_LEDGER_NUMBERING_INVALID",
            f"cannot infer a header from non-contiguous H rows: {blueprint.name}",
        )
    expected = [body for _, body in rows]
    source = (config.codebase_root / source_relative).resolve()
    candidates: list[Path] = []
    for suffix in sorted(config.header_extensions):
        candidate = source.with_suffix(suffix)
        if candidate.is_file() and logical_lines(candidate.read_bytes())[0] == expected:
            candidates.append(candidate.resolve())
    if len(candidates) > 1:
        raise WorkshopError(
            "HEADER_MAPPING_AMBIGUOUS",
            f"multiple sibling headers exactly equal the H ledger in {blueprint.name}",
            details=[str(value) for value in candidates],
        )
    if not candidates:
        return None
    return candidates[0].relative_to(config.codebase_root).as_posix()


def _baseline_documents(config: WorkshopConfig) -> list[dict[str, Any]]:
    candidates = _blueprint_candidates(config)
    if len(candidates) != config.expected_translation_units:
        raise WorkshopError(
            "BOOTSTRAP_FILE_COUNT_INVALID",
            f"bootstrap expects {config.expected_translation_units} pre-repair blueprint files, found {len(candidates)}",
        )
    connection = sqlite3.connect(
        f"file:{config.kairos_database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise WorkshopError(
                "DATABASE_INTEGRITY_FAILED",
                f"SQLite integrity_check returned {integrity[0] if integrity else None}",
            )
        for path in candidates:
            metadata, source, header, _ = inspect_blueprint_mapping(path)
            if header is None:
                header = _exact_sibling_header_from_ledger(
                    config,
                    path,
                    source,
                )
            artifact_id = metadata.get("id")
            revision = metadata.get("revision")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise WorkshopError("BLUEPRINT_ID_INVALID", f"missing artifact id in {path.name}")
            if artifact_id in seen_ids:
                raise WorkshopError("BLUEPRINT_ID_DUPLICATE", f"duplicate artifact id: {artifact_id}")
            seen_ids.add(artifact_id)
            if not isinstance(revision, int) or revision < 1:
                raise WorkshopError("BLUEPRINT_REVISION_INVALID", f"invalid revision in {path.name}")
            managed = config.managed_blueprint_root / path.name
            if not managed.is_file():
                raise WorkshopError("MANAGED_BLUEPRINT_MISSING", f"managed copy is missing: {path.name}")
            raw = path.read_bytes()
            managed_raw = managed.read_bytes()
            if raw != managed_raw:
                raise WorkshopError(
                    "BLUEPRINT_MANAGED_MISMATCH",
                    f"external and managed baseline differ: {path.name}",
                )
            digest = normalized_sha256(raw)
            database_row = connection.execute(
                "SELECT path,revision,content_sha256 FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if (
                not database_row
                or database_row["path"] != f"code/{path.name}"
                or int(database_row["revision"]) != revision
                or database_row["content_sha256"] != digest
            ):
                raise WorkshopError(
                    "DATABASE_BASELINE_MISMATCH",
                    f"database baseline differs for {artifact_id}",
                )
            revision_row = connection.execute(
                "SELECT content_sha256 FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (artifact_id, revision),
            ).fetchone()
            if not revision_row or revision_row["content_sha256"] != digest:
                raise WorkshopError(
                    "DATABASE_REVISION_ROW_MISMATCH",
                    f"database revision baseline differs for {artifact_id}",
                )
            rows.append({
                "filename": path.name,
                "artifact_id": artifact_id,
                "revision": revision,
                "source": source,
                "header": header,
                "raw_sha256": sha256_bytes(raw),
                "normalized_sha256": digest,
                "bytes": len(raw),
            })
    finally:
        connection.close()
    return rows


def _prefixed_rows(text: str, prefix: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    pattern = re.compile(rf"^{prefix}:(\d+) ?(.*)$")
    for line in normalized_text(text).splitlines():
        match = pattern.match(line)
        if match:
            result.append((int(match.group(1)), match.group(2)))
    return result


def _assert_malformed_payload_is_only_blank_noise(
    document_path: Path,
    source: Path | None,
    header: Path | None,
) -> dict[str, Any]:
    text = document_path.read_text(encoding="utf-8")
    expected = {
        "H": logical_lines(header.read_bytes())[0] if header else [],
        "C": logical_lines(source.read_bytes())[0] if source else [],
    }
    blank_noise: dict[str, list[int]] = {}
    for prefix in ("H", "C"):
        rows = _prefixed_rows(text, prefix)
        numbers = [number for number, _ in rows]
        bodies = [body for _, body in rows]
        if numbers != list(range(1, len(rows) + 1)) or bodies != expected[prefix]:
            raise WorkshopError(
                "MALFORMED_LEDGER_NOT_EXACT",
                f"prefixed {prefix} rows do not already equal live code in {document_path.name}",
            )
        all_lines = normalized_text(text).splitlines()
        matching = [
            index
            for index, line in enumerate(all_lines)
            if re.match(rf"^{prefix}:\d+", line)
        ]
        noise: list[int] = []
        if matching:
            for index in range(matching[0], matching[-1] + 1):
                if not re.match(rf"^{prefix}:\d+", all_lines[index]):
                    if all_lines[index] != "":
                        raise WorkshopError(
                            "MALFORMED_LEDGER_NONBLANK_NOISE",
                            f"nonblank unprefixed content appears inside {prefix} ledger in {document_path.name}",
                            details={"document_line": index + 1, "text": all_lines[index][:160]},
                        )
                    noise.append(index + 1)
        blank_noise[prefix] = noise
    return blank_noise


def _include_graph(
    config: WorkshopConfig,
) -> tuple[dict[str, set[str]], list[str]]:
    extensions = set(config.header_extensions) | {".c", ".cc", ".cpp", ".cxx", ".cu"}
    files = [
        path
        for path in config.runtime_root.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    ]
    edges: dict[str, set[str]] = {}
    for path in files:
        relative = path.resolve().relative_to(config.codebase_root).as_posix()
        targets: set[str] = set()
        text = path.read_text(encoding="utf-8")
        for include in INCLUDE_RE.findall(text):
            resolved = _resolve_include(config, path, include)
            if resolved is None:
                continue
            try:
                target = resolved.relative_to(config.codebase_root).as_posix()
            except ValueError:
                continue
            targets.add(target)
        edges[relative] = targets
    return edges, cmake_membership(config)


def _compiled_roots_for_header(
    edges: dict[str, set[str]],
    translation_units: list[str],
    header: str,
) -> list[str]:
    roots: list[str] = []
    for source in translation_units:
        queue: deque[str] = deque([source])
        seen = {source}
        found = False
        while queue and not found:
            current = queue.popleft()
            for target in sorted(edges.get(current, set())):
                if target == header:
                    found = True
                    break
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        if found:
            roots.append(source)
    return roots


def _header_artifact_id(relative: str) -> str:
    without_runtime = relative.removeprefix(source_prefix())
    stem = str(Path(without_runtime).with_suffix("")).replace("\\", "/")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").upper()
    artifact_id = f"CODE_RT_{normalized}_L0001_V01"
    if len(artifact_id) > 128:
        raise WorkshopError("BLUEPRINT_ID_INVALID", f"generated header artifact id is too long: {artifact_id}")
    return artifact_id


def _mechanical_header_blueprint_name(relative: str) -> str:
    return str(Path(relative).with_suffix("")).replace("\\", "_").replace("/", "_") + ".md"


def _shadow_config(
    config: WorkshopConfig,
    *,
    blueprint_root: Path,
    managed_root: Path,
    workspace: Path,
    database: Path,
) -> WorkshopConfig:
    """Read-only verification view retaining the loaded machine/source authority.

    Only document and SQL projection locations differ. Never synthesize another
    config writer or reinterpret relative source/authority paths below a TX root.
    """
    return replace(config, blueprint_root=blueprint_root,
                   managed_blueprint_root=managed_root,
                   kairos_workspace=workspace, kairos_database=database)


class BootstrapRepair:
    """One fail-closed document-only repair path for an unsealable legacy corpus."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.config: WorkshopConfig = engine.config

    def _transaction_root(self, transaction_id: str) -> Path:
        return self.engine._transaction_path(transaction_id)

    def stage(self) -> dict[str, Any]:
        if self.engine.lease_path.exists():
            raise WorkshopError(
                "LEASE_ACTIVE",
                "cannot stage bootstrap repair while another workshop transaction is active",
                details=read_json(self.engine.lease_path),
            )
        live_manifest = build_corpus_manifest(self.config)
        issue_codes = {issue["code"] for issue in live_manifest["issues"]}
        unexpected = sorted(issue_codes - ALLOWED_BOOTSTRAP_ISSUES)
        if unexpected:
            raise WorkshopError(
                "BOOTSTRAP_SCOPE_UNSAFE",
                "bootstrap repair refuses unrelated live issues",
                details=unexpected,
            )
        baseline_documents = _baseline_documents(self.config)
        runtime_before = _runtime_tree_manifest(self.config)
        created_at = utc_now()
        transaction_id = "TXN_" + package_hash({
            "mode": "document-only-bootstrap-repair",
            "runtime": runtime_before["package_sha256"],
            "blueprints": [
                (row["filename"], row["raw_sha256"])
                for row in baseline_documents
            ],
            "created_at": created_at,
        })[:24]
        root = self._transaction_root(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self.engine._acquire_lease(transaction_id)
        try:
            work_blueprints = root / "work" / "blueprints"
            work_managed = root / "work" / "managed"
            shadow = root / "shadow"
            for directory in (
                work_blueprints,
                work_managed,
                shadow / "code",
                shadow / ".kairos" / "events",
                shadow / ".kairos" / "receipts",
                root / "baseline",
                root / "rollback",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(root / "baseline" / "runtime_tree.json", runtime_before)
            atomic_write_json(root / "baseline" / "documents.json", baseline_documents)
            sqlite_snapshot(self.config.kairos_database, root / "baseline" / "kairos.db")
            copy_exact(
                self.config.kairos_workspace / ".kairos" / "config.json",
                shadow / ".kairos" / "config.json",
            )
            copy_exact(root / "baseline" / "kairos.db", shadow / ".kairos" / "kairos.db")
            for row in baseline_documents:
                name = row["filename"]
                copy_exact(self.config.blueprint_root / name, work_blueprints / name)
                copy_exact(self.config.managed_blueprint_root / name, work_managed / name)

            changed: list[dict[str, Any]] = []
            missing_count = 0
            malformed_count = 0
            for row in baseline_documents:
                name = row["filename"]
                source = _runtime_file(self.config, row["source"])
                header = _runtime_file(self.config, row["header"])
                path = work_blueprints / name
                rendered: str | None = None
                repair_kind = ""
                noise: dict[str, Any] | None = None
                try:
                    document = parse_blueprint(path)
                except WorkshopError as exc:
                    if exc.code != "BLUEPRINT_LEDGER_BLOCK_MISSING":
                        raise
                    if re.search(r"(?m)^[HC]:\d+", path.read_text(encoding="utf-8")):
                        raise WorkshopError(
                            "MISSING_LEDGER_HAS_PREFIXED_ROWS",
                            f"missing-ledger repair found prefixed code rows in {name}",
                        )
                    rendered = complete_missing_mechanical_layers(
                        path,
                        source,
                        header,
                        revision=int(row["revision"]) + 1,
                        updated_at=created_at,
                    )
                    repair_kind = "missing_ledger"
                    missing_count += 1
                else:
                    ledger_issues = verify_ledger(document, source, header)
                    if ledger_issues:
                        if {
                            issue["code"] for issue in ledger_issues
                        } != {"BLUEPRINT_LEDGER_LINE_INVALID"}:
                            raise WorkshopError(
                                "BOOTSTRAP_LEDGER_SCOPE_UNSAFE",
                                f"repair refuses any ledger defect beyond the verified blank-line case in {name}",
                                details=ledger_issues,
                            )
                        noise = _assert_malformed_payload_is_only_blank_noise(path, source, header)
                        rendered = regenerate_mechanical_layers(
                            document,
                            source,
                            header,
                            revision=int(row["revision"]) + 1,
                            updated_at=created_at,
                        )
                        repair_kind = "blank_ledger_noise"
                        malformed_count += 1
                if rendered is None:
                    continue
                raw = rendered.encode("utf-8")
                atomic_write_bytes(path, raw)
                atomic_write_bytes(work_managed / name, raw)
                repaired = parse_blueprint(path)
                issues = verify_ledger(repaired, source, header)
                if issues:
                    raise WorkshopError(
                        "GENERATED_LEDGER_INVALID",
                        f"repaired ledger does not equal live code: {name}",
                        details=issues,
                    )
                changed.append({
                    "filename": name,
                    "artifact_id": repaired.artifact_id,
                    "repair_kind": repair_kind,
                    "new_file": False,
                    "baseline_revision": int(row["revision"]),
                    "revision": repaired.revision,
                    "source": row["source"],
                    "header": row["header"],
                    "source_facts": file_facts(source) if source else None,
                    "header_facts": file_facts(header) if header else None,
                    "baseline_sha256": row["raw_sha256"],
                    "staged_sha256": sha256_bytes(raw),
                    "removed_blank_document_lines": noise,
                })
            include_edges, translation_units = _include_graph(self.config)
            include_headers, include_issues = header_closure(
                self.config,
                translation_units,
            )
            if include_issues:
                raise WorkshopError(
                    "BOOTSTRAP_INCLUDE_GRAPH_INVALID",
                    "compiled include closure has unresolved Runtime inputs",
                    details=include_issues,
                )
            existing_ids = {row["artifact_id"] for row in baseline_documents}
            header_only_count = 0
            for header_relative, filename in sorted(
                self.config.header_only_blueprints.items()
            ):
                expected_name = _mechanical_header_blueprint_name(header_relative)
                if filename != expected_name:
                    raise WorkshopError(
                        "HEADER_ONLY_FILENAME_NONMECHANICAL",
                        f"configured header-only filename is not mechanically derived: {filename}",
                        details={"expected": expected_name, "header": header_relative},
                    )
                if header_relative not in include_headers:
                    raise WorkshopError(
                        "HEADER_ONLY_NOT_COMPILED",
                        f"header-only input is absent from the recursive compiled closure: {header_relative}",
                    )
                if header_relative in translation_units:
                    raise WorkshopError(
                        "HEADER_ONLY_IS_TRANSLATION_UNIT",
                        f"header-only input is listed as a translation unit: {header_relative}",
                    )
                header = _runtime_file(self.config, header_relative)
                assert header is not None
                sibling_stem = header.with_suffix("")
                paired_sources = sorted(
                    str(sibling_stem.with_suffix(suffix))
                    for suffix in SOURCE_SUFFIXES
                    if sibling_stem.with_suffix(suffix).is_file()
                )
                if paired_sources:
                    raise WorkshopError(
                        "HEADER_ONLY_PAIRED_SOURCE_EXISTS",
                        f"header-only repair found a same-stem source: {header_relative}",
                        details=paired_sources,
                    )
                artifact_id = _header_artifact_id(header_relative)
                if artifact_id in existing_ids:
                    raise WorkshopError(
                        "HEADER_ONLY_ID_COLLISION",
                        f"mechanical header-only artifact id already exists: {artifact_id}",
                    )
                compiled_roots = _compiled_roots_for_header(
                    include_edges,
                    translation_units,
                    header_relative,
                )
                if not compiled_roots:
                    raise WorkshopError(
                        "HEADER_ONLY_NO_COMPILED_ROOT",
                        f"no authoritative translation unit reaches {header_relative}",
                    )
                direct_dependencies = sorted(
                    target
                    for target in include_edges.get(header_relative, set())
                    if target.startswith(source_prefix())
                )
                rendered = generate_header_only_blueprint(
                    artifact_id=artifact_id,
                    header_relative=header_relative,
                    header=header,
                    direct_runtime_includes=direct_dependencies,
                    compiled_include_roots=compiled_roots,
                    updated_at=created_at,
                )
                raw = rendered.encode("utf-8")
                path = work_blueprints / filename
                atomic_write_bytes(path, raw)
                atomic_write_bytes(work_managed / filename, raw)
                generated = parse_blueprint(path)
                if generated.source_path is not None or generated.header_path != header_relative:
                    raise WorkshopError(
                        "HEADER_ONLY_GENERATED_MAPPING_INVALID",
                        f"generated source-less mapping differs for {filename}",
                    )
                ledger_issues = verify_ledger(generated, None, header)
                if ledger_issues:
                    raise WorkshopError(
                        "HEADER_ONLY_GENERATED_LEDGER_INVALID",
                        f"generated header-only ledger differs from live code: {filename}",
                        details=ledger_issues,
                    )
                existing_ids.add(artifact_id)
                header_only_count += 1
                changed.append({
                    "filename": filename,
                    "artifact_id": artifact_id,
                    "repair_kind": "header_only_blueprint",
                    "new_file": True,
                    "baseline_revision": None,
                    "revision": 1,
                    "source": None,
                    "header": header_relative,
                    "source_facts": None,
                    "header_facts": file_facts(header),
                    "baseline_sha256": None,
                    "staged_sha256": sha256_bytes(raw),
                    "compiled_include_roots": compiled_roots,
                    "direct_runtime_includes": direct_dependencies,
                })
            for path in sorted(work_managed.glob("*.md"), key=lambda value: value.name):
                copy_exact(path, shadow / "code" / path.name)
            harness = str(self.config.kairos_harness)
            if harness not in sys.path:
                sys.path.insert(0, harness)
            database_module = importlib.import_module("kairos.database")
            promoter_module = importlib.import_module("kairos.promoter")
            database = database_module.KnowledgeDatabase(shadow / ".kairos" / "kairos.db")
            promotion_receipts: list[dict[str, Any]] = []
            for row in sorted(changed, key=lambda value: value["filename"]):
                receipt = promoter_module.promote_document(
                    shadow / "code" / row["filename"],
                    shadow,
                    database,
                )
                if not receipt.get("verified"):
                    raise WorkshopError(
                        "BOOTSTRAP_SHADOW_PROMOTION_FAILED",
                        f"shadow promotion did not verify: {row['filename']}",
                        details=receipt,
                    )
                promotion_receipts.append(receipt)
            staged_config = _shadow_config(
                self.config,
                blueprint_root=work_blueprints,
                managed_root=shadow / "code",
                workspace=shadow,
                database=shadow / ".kairos" / "kairos.db",
            )
            staged_manifest = build_corpus_manifest(staged_config)
            if not staged_manifest["verified"] or staged_manifest["issues"]:
                raise WorkshopError(
                    "BOOTSTRAP_SHADOW_CORPUS_INVALID",
                    "the staged corpus did not pass exact configured corpus verification",
                    details={
                        "counts": staged_manifest["counts"],
                        "issues": staged_manifest["issues"],
                    },
                )
            runtime_after = _runtime_tree_manifest(self.config)
            if runtime_after != runtime_before:
                raise WorkshopError(
                    "RUNTIME_MUTATED_DURING_STAGE",
                    "Runtime changed during document-only staging",
                    details={
                        "before": runtime_before["package_sha256"],
                        "after": runtime_after["package_sha256"],
                    },
                )
            state = {
                "schema": "runtime-sync-bootstrap-repair/v1",
                "transaction_id": transaction_id,
                "mode": "document_only",
                "runtime_write_allowed": False,
                "state": "SHADOW_VERIFIED",
                "created_at": created_at,
                "updated_at": created_at,
                "baseline_runtime": runtime_before,
                "baseline_package_sha256": live_manifest["package_sha256"],
                "baseline_documents": baseline_documents,
                "changed_documents": sorted(changed, key=lambda value: value["filename"]),
                "staged_package_sha256": staged_manifest["package_sha256"],
                "staged_counts": staged_manifest["counts"],
                "shadow_promotions": promotion_receipts,
                "journal": [],
            }
            self.engine._save_transaction(root, state, event="BOOTSTRAP_SHADOW_VERIFIED",
                                          details={"changed_documents": len(changed)})
            from .tx_lifecycle import requires_projection
            if requires_projection(self.engine, state):
                from .tx_documents import assert_projected
                assert_projected(self.engine, transaction_id)
            receipt_payload = {
                "schema": "runtime-sync-bootstrap-stage/v1",
                "transaction_id": transaction_id,
                "state": "SHADOW_VERIFIED",
                "runtime_write_allowed": False,
                "runtime_package_sha256": runtime_before["package_sha256"],
                "staged_package_sha256": staged_manifest["package_sha256"],
                "counts": staged_manifest["counts"],
                "changed_documents": state["changed_documents"],
            }
            receipt_path = self.engine._write_receipt("bootstrap-stage", receipt_payload)
            return {**receipt_payload, "receipt": str(receipt_path)}
        except Exception:
            # Once state is durable, keep its lease for native abort/recovery.
            # A projection failure must not strand an unowned verified TX.
            if not (root / "state.json").is_file() and self.engine.lease_path.exists():
                lease = read_json(self.engine.lease_path)
                if lease.get("transaction_id") == transaction_id:
                    self.engine._release_lease(transaction_id)
            raise

    def _load(self, transaction_id: str) -> tuple[Path, dict[str, Any]]:
        root = self._transaction_root(transaction_id)
        state_path = root / "state.json"
        if not state_path.is_file():
            raise WorkshopError(
                "BOOTSTRAP_TRANSACTION_MISSING",
                f"bootstrap repair transaction does not exist: {transaction_id}",
            )
        state = read_json(state_path)
        if (
            state.get("schema") != "runtime-sync-bootstrap-repair/v1"
            or state.get("transaction_id") != transaction_id
            or state.get("mode") != "document_only"
            or state.get("runtime_write_allowed") is not False
        ):
            raise WorkshopError(
                "BOOTSTRAP_TRANSACTION_INVALID",
                f"transaction is not a document-only bootstrap repair: {transaction_id}",
            )
        return root, state

    def _assert_live_baseline(self, state: dict[str, Any]) -> None:
        runtime = _runtime_tree_manifest(self.config)
        if runtime != state["baseline_runtime"]:
            raise WorkshopError(
                "RUNTIME_BASELINE_CHANGED",
                "Runtime differs from the full-tree checkout baseline",
                details={
                    "baseline": state["baseline_runtime"]["package_sha256"],
                    "current": runtime["package_sha256"],
                },
            )
        current_documents = _baseline_documents(self.config)
        if current_documents != state["baseline_documents"]:
            raise WorkshopError(
                "DOCUMENT_BASELINE_CHANGED",
                "the external/managed/database baseline changed after staging",
            )
        for row in state["changed_documents"]:
            if not row["new_file"]:
                continue
            filename = row["filename"]
            if filename != Path(filename).name:
                raise WorkshopError(
                    "BOOTSTRAP_FILENAME_INVALID",
                    f"new blueprint filename is not a basename: {filename}",
                )
            external = self.config.blueprint_root / filename
            managed = self.config.managed_blueprint_root / filename
            if external.exists() or managed.exists():
                raise WorkshopError(
                    "NEW_DOCUMENT_COLLISION",
                    f"header-only target appeared after staging: {filename}",
                    details={
                        "external_exists": external.exists(),
                        "managed_exists": managed.exists(),
                    },
                )

    def _backup_document_targets(
        self,
        root: Path,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for changed in state["changed_documents"]:
            filename = changed["filename"]
            if filename != Path(filename).name:
                raise WorkshopError(
                    "BOOTSTRAP_FILENAME_INVALID",
                    f"changed blueprint filename is not a basename: {filename}",
                )
            for role, live in (
                ("external", self.config.blueprint_root / filename),
                ("managed", self.config.managed_blueprint_root / filename),
            ):
                existed = live.is_file()
                expected_existence = not changed["new_file"]
                if existed != expected_existence:
                    raise WorkshopError(
                        "BOOTSTRAP_TARGET_BASELINE_INVALID",
                        f"target existence differs before apply: {live}",
                    )
                row: dict[str, Any] = {
                    "role": role,
                    "live": str(live),
                    "filename": filename,
                    "existed": existed,
                    "staged_sha256": changed["staged_sha256"],
                }
                if existed:
                    backup = root / "rollback" / role / filename
                    try:
                        preimage = capture_rollback_preimage(live, backup, copier=copy_exact)
                    except WorkshopError as exc:
                        if exc.code == "ROLLBACK_PREIMAGE_CHANGED":
                            raise WorkshopError("BOOTSTRAP_PREIMAGE_CHANGED", str(exc)) from exc
                        raise
                    row.update({
                        "backup": str(backup),
                        "baseline_sha256": preimage["sha256"],
                        "baseline_mtime_ns": preimage["mtime_ns"],
                    })
                rows.append(row)
        atomic_write_json(root / "rollback" / "document_targets.json", rows)
        return rows

    def _rollback_document_targets(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in reversed(rows):
            live = Path(row["live"])
            expected_root = (
                self.config.blueprint_root
                if row["role"] == "external"
                else self.config.managed_blueprint_root
            ).resolve()
            try:
                if live.resolve().parent != expected_root or live.name != row["filename"]:
                    raise WorkshopError(
                        "ROLLBACK_PATH_INVALID",
                        f"rollback target escapes its exact document root: {live}",
                    )
                if row["existed"]:
                    restored = restore_rollback_preimage(
                        Path(row["backup"]), live, row["baseline_sha256"],
                        row.get("baseline_mtime_ns"), copier=copy_exact,
                    )
                else:
                    if live.exists():
                        if not live.is_file():
                            raise WorkshopError(
                                "ROLLBACK_TARGET_NOT_FILE",
                                f"new document rollback target is not a file: {live}",
                            )
                        live.unlink()
                    restored = not live.exists()
                results.append({"path": str(live), "restored": restored})
            except Exception as exc:
                results.append({
                    "path": str(live),
                    "restored": False,
                    "error": str(exc),
                })
        return results

    def _runtime_must_equal_baseline(
        self,
        state: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        runtime = _runtime_tree_manifest(self.config)
        if runtime != state["baseline_runtime"]:
            raise WorkshopError(
                "RUNTIME_WRITE_INVARIANT_FAILED",
                f"Runtime full-tree bytes changed during document-only phase {phase}",
                details={
                    "phase": phase,
                    "baseline": state["baseline_runtime"]["package_sha256"],
                    "current": runtime["package_sha256"],
                },
            )
        return runtime

    def _verify_live_result(
        self,
        root: Path,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        self._runtime_must_equal_baseline(state, phase="post-heartbeat")
        manifest = build_corpus_manifest(self.config)
        if (
            not manifest["verified"]
            or manifest["package_sha256"] != state["staged_package_sha256"]
            or manifest["counts"] != state["staged_counts"]
        ):
            raise WorkshopError(
                "BOOTSTRAP_POSTCHECK_FAILED",
                "live Runtime/document/database corpus differs from the verified shadow result",
                details={
                    "verified": manifest["verified"],
                    "live_package": manifest["package_sha256"],
                    "staged_package": state["staged_package_sha256"],
                    "counts": manifest["counts"],
                    "expected_counts": state["staged_counts"],
                    "issues": manifest["issues"],
                },
            )
        for row in state["changed_documents"]:
            filename = row["filename"]
            staged = root / "work" / "blueprints" / filename
            external = self.config.blueprint_root / filename
            managed = self.config.managed_blueprint_root / filename
            expected = staged.read_bytes()
            if external.read_bytes() != expected or managed.read_bytes() != expected:
                raise WorkshopError(
                    "BOOTSTRAP_DOCUMENT_PAIR_MISMATCH",
                    f"live document pair differs from verified staged bytes: {filename}",
                )
        return manifest

    def apply(self, transaction_id: str) -> dict[str, Any]:
        from .jev_admission import require_supported_apply_route
        require_supported_apply_route(self.config, "repair")
        root, state = self._load(transaction_id)
        self.engine._require_lease(transaction_id)
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError(
                "BOOTSTRAP_STATE_INVALID",
                f"bootstrap apply requires SHADOW_VERIFIED, found {state['state']}",
            )
        self._assert_live_baseline(state)
        runtime_state = read_json(
            self.config.kairos_workspace / ".kairos" / "runtime_state.json"
        )
        if runtime_state.get("lifecycle") == "FINALIZED":
            raise WorkshopError(
                "KAIROS_FINALIZED",
                "KAIROS workspace is FINALIZED; a governed task must be active before apply",
            )
        changed_managed = [
            f"code/{row['filename']}"
            for row in state["changed_documents"]
        ]
        governance, heartbeat = self.engine._import_kairos()
        permit = governance.issue_external_edit_permit(
            self.config.kairos_workspace,
            paths=changed_managed,
            reason=(
                f"Runtime Sync Workshop {transaction_id}: exact document-only repair of "
                "source/header ledgers; Runtime writes are prohibited"
            ),
            ttl_seconds=1800,
        )
        try:
            backups = self._backup_document_targets(root, state)
        except Exception as exc:
            # No live apply target has been written yet. Do not leave an unused
            # edit grant outstanding if capturing its exact preimage fails.
            try:
                governance.revoke_action_permit(
                    self.config.kairos_workspace,
                    permit_id=permit["permit_id"],
                    reason=f"Bootstrap {transaction_id} failed before any apply target write",
                )
            except Exception as revoke_exc:
                raise WorkshopError(
                    "BOOTSTRAP_PREIMAGE_PERMIT_PENDING",
                    "preimage capture failed and the unused edit permit could not be revoked",
                    details={"cause": str(exc), "revocation": str(revoke_exc)},
                ) from exc
            raise
        state["state"] = "APPLYING"
        state["permit"] = permit
        self.engine._save_transaction(
            root,
            state,
            event="BOOTSTRAP_APPLY_STARTED",
            details={
                "managed_paths": changed_managed,
                "runtime_write_allowed": False,
            },
        )
        heartbeat_started = False
        try:
            for row in state["changed_documents"]:
                filename = row["filename"]
                staged = root / "work" / "blueprints" / filename
                copy_exact(staged, self.config.blueprint_root / filename)
            for row in state["changed_documents"]:
                filename = row["filename"]
                staged = root / "work" / "managed" / filename
                copy_exact(staged, self.config.managed_blueprint_root / filename)
            self._runtime_must_equal_baseline(state, phase="before-heartbeat")
            heartbeat_started = True
            heartbeat_receipt = heartbeat.run_heartbeat(
                self.config.kairos_workspace,
                requested_mode="verify",
                changed_paths=changed_managed,
                use_reconciliation=True,
                trigger=f"runtime-sync-bootstrap:{transaction_id}",
            )
            if not heartbeat_receipt.get("verified"):
                raise WorkshopError(
                    "HEARTBEAT_NOT_VERIFIED",
                    "KAIROS heartbeat did not verify the complete managed document set",
                    details=heartbeat_receipt,
                )
            post = self._verify_live_result(root, state)
            mirror = self.engine._mirror(post)
            seal = {
                "schema": "runtime-sync-seal/v1",
                "created_at": utc_now(),
                "package_sha256": post["package_sha256"],
                "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(
                    self.config.state_directory
                    / "mirrors"
                    / f"{mirror['mirror_sha256']}.json"
                ),
                "counts": post["counts"],
                "transaction_id": transaction_id,
                "document_only": True,
                "runtime_package_sha256": state["baseline_runtime"]["package_sha256"],
            }
            atomic_write_json(self.engine.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["heartbeat"] = heartbeat_receipt
            state["runtime_postcheck"] = self._runtime_must_equal_baseline(
                state,
                phase="after-mirror",
            )
            receipt = {
                "schema": "runtime-sync-bootstrap-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "document_only": True,
                "runtime_write_allowed": False,
                "runtime_file_count": state["baseline_runtime"]["file_count"],
                "runtime_package_sha256_before": state["baseline_runtime"]["package_sha256"],
                "runtime_package_sha256_after": state["runtime_postcheck"]["package_sha256"],
                "staged_package_sha256": state["staged_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "counts": post["counts"],
                "heartbeat_id": heartbeat_receipt.get("heartbeat_id"),
                "changed_documents": state["changed_documents"],
                "bit_exact": True,
            }
            state["terminal_receipt_payload"] = receipt
            self.engine._save_transaction(
                root,
                state,
                event="BOOTSTRAP_POSTCHECK_VERIFIED",
                details={
                    "package_sha256": post["package_sha256"],
                    "runtime_package_sha256": state["baseline_runtime"]["package_sha256"],
                },
            )
        except Exception as exc:
            failure = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(exc),
            }
            if heartbeat_started:
                state["state"] = "RECOVERY_REQUIRED"
                state["failure"] = failure
                self.engine._save_transaction(
                    root,
                    state,
                    event="BOOTSTRAP_RECOVERY_REQUIRED",
                    details=failure,
                )
                raise WorkshopError(
                    "RECOVERY_REQUIRED",
                    "document apply crossed the KAIROS heartbeat boundary and failed; lease retained",
                    details=failure,
                ) from exc
            rollback = self._rollback_document_targets(backups)
            revocation: dict[str, Any] | None = None
            revocation_error: str | None = None
            try:
                revocation = governance.revoke_action_permit(
                    self.config.kairos_workspace,
                    permit_id=permit["permit_id"],
                    reason=(
                        f"Bootstrap document apply {transaction_id} failed before heartbeat; "
                        "all exact document targets are being restored"
                    ),
                )
            except Exception as revoke_exc:
                revocation_error = str(revoke_exc)
            runtime_ok = False
            try:
                self._runtime_must_equal_baseline(state, phase="pre-heartbeat-rollback")
                runtime_ok = True
            except WorkshopError:
                runtime_ok = False
            rollback_ok = all(row.get("restored") for row in rollback)
            if rollback_ok and runtime_ok and revocation_error is None:
                state["state"] = "ROLLED_BACK"
                state["failure"] = failure
                self.engine._save_transaction(
                    root,
                    state,
                    event="BOOTSTRAP_APPLY_ROLLED_BACK",
                    details={"rollback": rollback, "permit": revocation},
                )
                self.engine._release_lease(transaction_id)
                raise WorkshopError(
                    "APPLY_ROLLED_BACK",
                    "document apply failed before heartbeat and exact baseline bytes were restored",
                    details={"cause": failure, "rollback": rollback},
                ) from exc
            state["state"] = "RECOVERY_REQUIRED"
            state["failure"] = failure
            self.engine._save_transaction(
                root,
                state,
                event="BOOTSTRAP_ROLLBACK_INCOMPLETE",
                details={
                    "rollback": rollback,
                    "runtime_unchanged": runtime_ok,
                    "permit_revocation_error": revocation_error,
                },
            )
            raise WorkshopError(
                "RECOVERY_REQUIRED",
                "pre-heartbeat document rollback could not prove complete restoration; lease retained",
                details={
                    "cause": failure,
                    "rollback": rollback,
                    "runtime_unchanged": runtime_ok,
                    "permit_revocation_error": revocation_error,
                },
            ) from exc

        return self.engine._finish_transaction_receipt(root, state, kind="bootstrap-postcheck", payload=receipt)

    def abort(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        self.engine._require_lease(transaction_id)
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError(
                "BOOTSTRAP_STATE_INVALID",
                f"bootstrap abort requires SHADOW_VERIFIED, found {state['state']}",
            )
        self._assert_live_baseline(state)
        state["state"] = "ABORTED"
        self.engine._save_transaction(
            root,
            state,
            event="BOOTSTRAP_ABORTED",
            details={"live_documents_unchanged": True, "runtime_unchanged": True},
        )
        self.engine._release_lease(transaction_id)
        return {
            "schema": "runtime-sync-bootstrap-abort/v1",
            "transaction_id": transaction_id,
            "state": "ABORTED",
            "live_documents_unchanged": True,
            "runtime_unchanged": True,
        }

    def recover(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        if state.get("state") == "POSTCHECK_VERIFIED":
            entry = state.get("terminal_receipt")
            payload = entry.get("payload") if isinstance(entry, dict) else state.get("terminal_receipt_payload")
            if not isinstance(payload, dict):
                raise WorkshopError("TX_TERMINAL_SEED_MISSING", "bootstrap postcheck has no exact preserved terminal payload")
            lease = read_json(self.engine.lease_path) if self.engine.lease_path.exists() else None
            if isinstance(lease, dict) and lease.get("transaction_id") == transaction_id:
                self._runtime_must_equal_baseline(state, phase="terminal-receipt-recovery")
            return self.engine._finish_transaction_receipt(root, state, kind="bootstrap-postcheck", payload=payload)
        self.engine._require_lease(transaction_id)
        if state["state"] != "RECOVERY_REQUIRED":
            raise WorkshopError(
                "BOOTSTRAP_STATE_INVALID",
                f"bootstrap recover requires RECOVERY_REQUIRED, found {state['state']}",
            )
        post = self._verify_live_result(root, state)
        mirror = self.engine._mirror(post)
        seal = {
            "schema": "runtime-sync-seal/v1",
            "created_at": utc_now(),
            "package_sha256": post["package_sha256"],
            "mirror_sha256": mirror["mirror_sha256"],
            "mirror_manifest": str(
                self.config.state_directory
                / "mirrors"
                / f"{mirror['mirror_sha256']}.json"
            ),
            "counts": post["counts"],
            "transaction_id": transaction_id,
            "document_only": True,
            "recovered": True,
            "runtime_package_sha256": state["baseline_runtime"]["package_sha256"],
        }
        atomic_write_json(self.engine.seal_path, seal)
        state["state"] = "POSTCHECK_VERIFIED"
        state["postcheck_package_sha256"] = post["package_sha256"]
        state["runtime_postcheck"] = self._runtime_must_equal_baseline(
            state,
            phase="recovery",
        )
        receipt = {
            "schema": "runtime-sync-bootstrap-recovery/v1",
            "transaction_id": transaction_id,
            "state": "POSTCHECK_VERIFIED",
            "package_sha256": post["package_sha256"],
            "postcheck_package_sha256": post["package_sha256"],
            "runtime_unchanged": True,
        }
        state["terminal_receipt_payload"] = receipt
        self.engine._save_transaction(root, state, event="BOOTSTRAP_RECOVERY_POSTCHECK_VERIFIED")
        return self.engine._finish_transaction_receipt(root, state, kind="bootstrap-postcheck", payload=receipt)

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        lease_owned = False
        if self.engine.lease_path.is_file():
            lease_owned = (
                read_json(self.engine.lease_path).get("transaction_id")
                == transaction_id
            )
        return {**state, "root": str(root), "lease_owned": lease_owned}
