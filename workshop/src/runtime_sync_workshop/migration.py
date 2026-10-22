from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .blueprint import (
    advance_document_revision,
    cpp_token_signature,
    parse_blueprint,
    parse_blueprint_text,
    regenerate_mechanical_layers,
    semantic_document_hash,
    verify_ledger,
)
from .corpus import build_corpus_manifest, load_config
from .scientific import ScientificParityGate
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    copy_exact,
    package_hash,
    read_json,
    sha256_bytes,
    utc_now,
)


def _package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_kickstart():
    root = _package_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from kickstart.binding import _cmake_authority, _resolve_include_closure
    from kickstart.blueprints import filename_for, render_binding_documents, render_blueprints
    from kickstart.survey import HEADER_SUFFIXES, compiler_context_sha256, compiler_probe_argv, survey_compile_commands

    return (
        _cmake_authority,
        _resolve_include_closure,
        filename_for,
        render_binding_documents,
        render_blueprints,
        HEADER_SUFFIXES,
        compiler_context_sha256,
        compiler_probe_argv,
        survey_compile_commands,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _facts(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": path.as_posix(), "sha256": sha256_bytes(raw), "bytes": len(raw)}


def _same_file_fact(record: dict[str, Any], candidate: Path) -> bool:
    expected = record.get("source") or record.get("header")
    if not isinstance(expected, dict):
        return False
    return str(expected.get("sha256", "")).lower() == sha256_bytes(candidate.read_bytes()).lower()


def _safe_relative(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise WorkshopError("AUTHORITY_MIGRATION_PATH_INVALID", f"unsafe project-relative path: {value}")
    return normalized


def _copy_tree_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    copy_exact(source, destination)


def _replace_sections(
    current_text: str,
    candidate_text: str,
    section_ids: tuple[str, ...],
    *,
    current_path: Path,
    candidate_path: Path,
    revision: int,
    updated_at: str,
) -> str:
    current = parse_blueprint_text(current_path, current_text)
    candidate = parse_blueprint_text(candidate_path, candidate_text)
    text = current.text
    replacements: list[tuple[int, int, str]] = []
    for section_id in section_ids:
        current_span = current.sections.get(section_id)
        candidate_span = candidate.sections.get(section_id)
        if current_span is None or candidate_span is None:
            continue
        replacement = candidate.text[candidate_span.start:candidate_span.end]
        replacements.append((current_span.start, current_span.end, replacement))
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    reparsed = parse_blueprint_text(current_path, text, current.newline)
    if revision == current.revision:
        return text.replace("\n", current.newline) if current.newline != "\n" else text
    return advance_document_revision(reparsed, revision=revision, updated_at=updated_at)


def _retire_document(path: Path, *, updated_at: str) -> str:
    root = _package_root() / "kairos" / "kairos_harness"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    frontmatter = importlib.import_module("kairos.frontmatter")
    text = path.read_text(encoding="utf-8")
    parsed = frontmatter.split_frontmatter(text)
    metadata = dict(parsed.metadata)
    metadata["revision"] = int(metadata["revision"]) + 1
    metadata["updated_at"] = updated_at
    metadata["state"] = "superseded"
    old_capsule = str(metadata.get("capsule", "")).strip()
    metadata["capsule"] = (
        "Superseded implementation evidence retained after an explicit authority migration. "
        + old_capsule
    )[:1000]
    metadata["claim_boundary"] = (
        "Historical implementation snapshot only. Its mapped source/header is no longer part of the "
        "current compiler-backed Workshop corpus; use current source-index and active code artifacts for present-state claims."
    )
    facets = list(metadata.get("facets", []))
    if "superseded" not in facets:
        facets.append("superseded")
    metadata["facets"] = facets
    nonanswers = list(metadata.get("does_not_answer", []))
    if "current implementation state" not in nonanswers:
        nonanswers.append("current implementation state")
    metadata["does_not_answer"] = nonanswers
    return frontmatter.render_frontmatter(metadata) + parsed.body


def _advance_generated_document(candidate_text: str, live_text: str, *, updated_at: str, path: Path) -> str:
    """Use generated authority content while preserving the existing artifact revision line."""
    root = _package_root() / "kairos" / "kairos_harness"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    frontmatter = importlib.import_module("kairos.frontmatter")
    candidate = frontmatter.split_frontmatter(candidate_text)
    live = frontmatter.split_frontmatter(live_text)
    metadata = dict(candidate.metadata)
    metadata["revision"] = int(live.metadata["revision"]) + 1
    metadata["updated_at"] = updated_at
    # Stable route/scope values are owned by the existing workspace. The generated
    # document should agree already, but preserving them makes migration fail-safe
    # against accidental generator drift.
    for key in ("workspace", "goal", "milestone", "task", "route", "authority", "type", "id"):
        if key in live.metadata:
            metadata[key] = live.metadata[key]
    return frontmatter.render_frontmatter(metadata) + candidate.body


class AuthorityMigration:
    """Explicit header-topology migration for a sealed compiler-backed Workshop corpus.

    The generic path may add/remove governed local headers while translation-unit membership
    and normalized compiler context remain unchanged. Source membership or compiler-context
    changes require a separately configured build-authority migration because KAIROS cannot
    safely rewrite arbitrary CMake/Meson/Bazel authority from a compiler database alone.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    @property
    def config(self):
        return self.engine.config

    def _scope(self) -> tuple[str, str, str, str, str | None]:
        intake = read_json(self.config.machine_root / "project-intake.json")
        if not isinstance(intake, dict) or intake.get("schema") != "kairos-project-intake/v1":
            raise WorkshopError("AUTHORITY_MIGRATION_INTAKE_MISSING", "project-intake.json is required for authority migration")
        scope = intake.get("project_scope") or {}
        workspace_id = str(scope.get("workspace_id", ""))
        goal_id = str(scope.get("goal_id", ""))
        milestone_id = str(scope.get("milestone_id", ""))
        task_id = str(scope.get("task_id", ""))
        if not all((workspace_id, goal_id, milestone_id, task_id)):
            raise WorkshopError("AUTHORITY_MIGRATION_SCOPE_INVALID", "project intake does not carry complete project scope")
        return workspace_id, goal_id, milestone_id, task_id, scope.get("project_intent_sha256")

    def _candidate_paths(self, survey: Any, headers: dict[str, tuple[Path, list[str]]]) -> dict[str, Path]:
        paths = {unit.relative_path: unit.absolute_path for unit in survey.units}
        paths.update({relative: path for relative, (path, _) in headers.items()})
        return dict(sorted(paths.items()))

    def _map_root(self, root: Path, from_root: Path, to_root: Path) -> Path:
        try:
            relative = root.resolve().relative_to(from_root.resolve())
        except ValueError:
            return root.resolve()
        return (to_root / relative).resolve()

    def _serialized_include_sequences(self, survey: Any, from_root: Path, to_root: Path) -> dict[str, list[list[str]]]:
        result: dict[str, list[list[str]]] = {}
        for unit in survey.units:
            result[unit.relative_path] = [
                [self._map_root(root, from_root, to_root).as_posix() for root in sequence]
                for sequence in unit.include_root_sequences
            ]
        return result

    def _serialized_include_variants(self, survey: Any, from_root: Path, to_root: Path) -> dict[str, list[dict[str, Any]]]:
        _, _, _, _, _, _, _, compiler_probe_argv, _ = _load_kickstart()
        result: dict[str, list[dict[str, Any]]] = {}
        for unit in survey.units:
            rows: list[dict[str, Any]] = []
            for variant in unit.variants:
                probe = compiler_probe_argv(variant.argv, directory=variant.directory, project_root=from_root)
                row = {
                    "quote_roots": [
                        self._map_root(root, from_root, to_root).as_posix()
                        for root in variant.quote_include_roots
                    ],
                    "angle_roots": [
                        self._map_root(root, from_root, to_root).as_posix()
                        for root in variant.angle_include_roots
                    ],
                    "idirafter_roots": [
                        self._map_root(root, from_root, to_root).as_posix()
                        for root in variant.idirafter_roots
                    ],
                    "compiler_probe": (
                        {
                            "argv": [
                                token.replace(from_root.resolve().as_posix(), to_root.resolve().as_posix())
                                for token in probe
                            ],
                            "directory": to_root.resolve().as_posix(),
                        }
                        if probe else None
                    ),
                }
                if row not in rows:
                    rows.append(row)
            result[unit.relative_path] = rows
        return result

    def _serialized_compiler_probes(
        self, survey: Any, from_root: Path, to_root: Path
    ) -> dict[str, list[dict[str, Any]]]:
        _, _, _, _, _, _, _, compiler_probe_argv, _ = _load_kickstart()
        source_root = from_root.resolve()
        target_root = to_root.resolve()
        markers = (source_root.as_posix(), str(source_root))

        def remap_token(token: str) -> str:
            result = token
            for marker in sorted(set(markers), key=len, reverse=True):
                result = result.replace(marker, target_root.as_posix())
                result = result.replace(marker.replace("/", "\\"), str(target_root))
            return result

        result: dict[str, list[dict[str, Any]]] = {}
        for unit in survey.units:
            rows: list[dict[str, Any]] = []
            for variant in unit.variants:
                probe = compiler_probe_argv(variant.argv, directory=variant.directory, project_root=from_root)
                if not probe:
                    continue
                directory = target_root
                row = {
                    "argv": [remap_token(token) for token in probe],
                    "directory": directory.as_posix(),
                }
                if row not in rows:
                    rows.append(row)
            result[unit.relative_path] = rows
        return result

    def _infer_renames(
        self,
        removed: list[str],
        added: list[str],
        baseline_manifest: dict[str, Any],
        candidate_root: Path,
    ) -> dict[str, str]:
        removed_by_hash: dict[str, list[str]] = {}
        added_by_hash: dict[str, list[str]] = {}
        for relative in removed:
            record = baseline_manifest["records"][relative]
            fact = record.get("source") or record.get("header") or {}
            digest = str(fact.get("sha256", "")).lower()
            removed_by_hash.setdefault(digest, []).append(relative)
        for relative in added:
            digest = sha256_bytes((candidate_root / relative).read_bytes()).lower()
            added_by_hash.setdefault(digest, []).append(relative)
        result: dict[str, str] = {}
        for digest, old_paths in removed_by_hash.items():
            new_paths = added_by_hash.get(digest, [])
            if digest and len(old_paths) == 1 and len(new_paths) == 1:
                result[old_paths[0]] = new_paths[0]
        return result

    def checkout(
        self,
        *,
        candidate_root: Path,
        compile_commands: Path,
        purpose: str,
    ) -> dict[str, Any]:
        purpose = purpose.strip()
        if len(purpose) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "authority migration purpose must contain at least 12 characters")
        candidate_root = candidate_root.resolve()
        if not candidate_root.is_dir():
            raise WorkshopError("AUTHORITY_MIGRATION_CANDIDATE_INVALID", f"candidate root is not a directory: {candidate_root}")
        if candidate_root == self.config.codebase_root.resolve():
            raise WorkshopError(
                "AUTHORITY_MIGRATION_CANDIDATE_LIVE",
                "authority migration requires an isolated candidate tree; do not mutate the governed live root",
            )
        if self.engine.lease_path.exists():
            raise WorkshopError("LEASE_ACTIVE", "another Workshop transaction already owns the global lease")
        if not self.engine.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "seal the verified live corpus before authority migration")
        baseline_manifest = build_corpus_manifest(self.config)
        if not baseline_manifest.get("verified"):
            raise WorkshopError("CORPUS_NOT_VERIFIED", "authority migration requires a verified live corpus", details=baseline_manifest.get("issues"))
        seal = read_json(self.engine.seal_path)
        if seal.get("package_sha256") != baseline_manifest["package_sha256"]:
            raise WorkshopError("SEAL_DRIFT", "live corpus differs from the trusted seal")

        (
            cmake_authority,
            resolve_include_closure,
            filename_for,
            render_binding_documents,
            render_blueprints,
            header_suffixes,
            compiler_context_sha256,
            compiler_probe_argv,
            survey_compile_commands,
        ) = _load_kickstart()
        survey = survey_compile_commands(compile_commands.resolve(), candidate_root)
        headers, unresolved = resolve_include_closure(candidate_root, survey)
        if unresolved:
            dynamic = [item for item in unresolved if item.get("reason") == "dynamic_or_macro_include"]
            code = "DYNAMIC_INCLUDE_UNSUPPORTED" if dynamic else "QUOTED_INCLUDE_UNRESOLVED"
            raise WorkshopError(code, "candidate include closure is not fully provable", details=unresolved)
        from kickstart.include_edges import authority_payload, collect_include_edges, owners_from_edges
        candidate_include_edges, edge_issues = collect_include_edges(candidate_root, survey)
        if edge_issues:
            raise WorkshopError(
                "INCLUDE_EDGE_AUTHORITY_UNVERIFIED",
                "candidate direct compiler include-edge authority is not fully provable",
                details=edge_issues,
            )
        candidate_edge_payload = authority_payload(candidate_include_edges)
        candidate_closure_owners = {
            relative: sorted(owners) for relative, (_, owners) in sorted(headers.items())
        }
        if owners_from_edges(candidate_include_edges) != candidate_closure_owners:
            raise WorkshopError(
                "INCLUDE_EDGE_CLOSURE_MISMATCH",
                "candidate direct include edges disagree with transitive compiler header closure",
            )
        workspace_id, goal_id, milestone_id, task_id, project_intent_sha256 = self._scope()
        created_at = utc_now()
        generated = render_blueprints(
            survey,
            candidate_root,
            headers=headers,
            workspace_id=workspace_id,
            task_id=task_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            updated_at=created_at,
        )
        binding_documents = render_binding_documents(
            survey=survey,
            project_root=candidate_root,
            workspace_id=workspace_id,
            task_id=task_id,
            goal_id=goal_id,
            milestone_id=milestone_id,
            updated_at=created_at,
            header_closure={relative: owners for relative, (_, owners) in headers.items()},
            include_edges=candidate_include_edges,
            include_topology_sha256=candidate_edge_payload["topology_sha256"],
        )
        candidate_sources = sorted(unit.relative_path for unit in survey.units)
        candidate_headers = sorted(headers)
        baseline_sources = sorted(str(value) for value in baseline_manifest["authority"]["translation_units"])
        baseline_headers = sorted(str(value) for value in baseline_manifest["authority"]["include_headers"])
        added_sources = sorted(set(candidate_sources) - set(baseline_sources))
        removed_sources = sorted(set(baseline_sources) - set(candidate_sources))
        added_headers = sorted(set(candidate_headers) - set(baseline_headers))
        removed_headers = sorted(set(baseline_headers) - set(candidate_headers))
        common = sorted((set(candidate_sources) & set(baseline_sources)) | (set(candidate_headers) & set(baseline_headers)))
        candidate_paths = self._candidate_paths(survey, headers)
        content_changed = [
            relative for relative in common
            if not _same_file_fact(baseline_manifest["records"][relative], candidate_paths[relative])
        ]
        live_intake = read_json(self.config.machine_root / "project-intake.json")
        live_authority = (live_intake.get("authority") or {}) if isinstance(live_intake, dict) else {}
        candidate_compiler_context_sha256 = compiler_context_sha256(survey, candidate_root)
        baseline_compiler_context_sha256 = str(live_authority.get("compiler_context_sha256", ""))
        compile_changed = (
            candidate_compiler_context_sha256 != baseline_compiler_context_sha256
            if baseline_compiler_context_sha256
            else sha256_bytes(compile_commands.read_bytes()) != str((live_authority.get("compile_commands") or {}).get("sha256", ""))
        )
        if added_sources or removed_sources or compile_changed:
            raise WorkshopError(
                "BUILD_AUTHORITY_MIGRATION_REQUIRED",
                "translation-unit membership or compiler-context changes require a project-specific build-authority migration; "
                "the generic Workshop will not make its derived compiler database true by changing only KAIROS authority files",
                details={
                    "added_sources": added_sources,
                    "removed_sources": removed_sources,
                    "compiler_context_changed": compile_changed,
                    "boundary": "header-topology migration is supported only while compiler membership/context remain unchanged",
                },
            )
        authority_changed = any((added_headers, removed_headers))
        if not authority_changed:
            if content_changed:
                raise WorkshopError(
                    "AUTHORITY_MIGRATION_NOT_REQUIRED",
                    "candidate changes code bytes but not compiler/header authority; use a normal Workshop transaction",
                    details=content_changed,
                )
            raise WorkshopError("AUTHORITY_MIGRATION_NO_CHANGES", "candidate authority is identical to the sealed live corpus")

        transaction_id = "TXN_" + package_hash({
            "kind": "authority_migration",
            "baseline": baseline_manifest["package_sha256"],
            "candidate_compile_commands": sha256_bytes(compile_commands.read_bytes()),
            "purpose": purpose,
            "created_at": created_at,
        })[:24]
        root = self.engine._transaction_path(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self.engine._acquire_lease(transaction_id)
        try:
            (root / "baseline").mkdir(parents=True)
            (root / "work" / "candidate").mkdir(parents=True)
            (root / "work" / "blueprints").mkdir(parents=True)
            (root / "work" / "managed").mkdir(parents=True)
            (root / "work" / "generated").mkdir(parents=True)
            (root / "work" / "authority").mkdir(parents=True)
            (root / "work" / "machine").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            atomic_write_json(root / "baseline" / "manifest.json", baseline_manifest)
            copy_exact(self.config.kairos_database, root / "baseline" / "kairos.db")
            for relative, source_path in candidate_paths.items():
                _copy_tree_file(source_path, root / "work" / "candidate" / relative)
            for record in generated.records:
                atomic_write_bytes(root / "work" / "generated" / record.filename, record.text.encode("utf-8"))
            atomic_write_bytes(
                root / "work" / "generated" / "PROJECT_SOURCE_INDEX.md",
                binding_documents["docs/PROJECT_SOURCE_INDEX.md"].encode("utf-8"),
            )
            # Generic authority migration is deliberately limited to header topology.
            # The candidate compiler database is evidence that compiler membership and
            # context are unchanged; it must not replace the retained build authority,
            # whose absolute paths and provenance belong to the live project.
            copy_exact(
                self.config.machine_root / "project-intake" / "compile_commands.json",
                root / "work" / "machine" / "compile_commands.json",
            )
            copy_exact(
                self.config.machine_root / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake",
                root / "work" / "machine" / "PROJECT_BUILD_AUTHORITY.cmake",
            )

            affected_active = sorted(set(content_changed) | set(added_sources) | set(added_headers))
            for relative in affected_active:
                if relative in baseline_manifest["records"]:
                    name = baseline_manifest["records"][relative]["blueprint"]["filename"]
                    copy_exact(self.config.blueprint_root / name, root / "baseline" / "blueprints" / name)
                    copy_exact(self.config.managed_blueprint_root / name, root / "baseline" / "managed" / name)
                    copy_exact(self.config.blueprint_root / name, root / "work" / "blueprints" / name)
                    copy_exact(self.config.managed_blueprint_root / name, root / "work" / "managed" / name)
                else:
                    name = filename_for(relative, header_only=relative in set(candidate_headers))
                    copy_exact(root / "work" / "generated" / name, root / "work" / "blueprints" / name)
                    copy_exact(root / "work" / "generated" / name, root / "work" / "managed" / name)
            for relative in sorted(set(removed_sources) | set(removed_headers)):
                record = baseline_manifest["records"][relative]
                name = record["blueprint"]["filename"]
                copy_exact(self.config.blueprint_root / name, root / "baseline" / "blueprints" / name)
                copy_exact(self.config.managed_blueprint_root / name, root / "baseline" / "managed" / name)

            review_entries = []
            for relative in content_changed:
                review_entries.append({
                    "path": relative,
                    "action": "update",
                    "metadata_impact": "pending",
                    "reason": "",
                    "reviewer": "",
                    "reviewed_at": "",
                })
            for relative in sorted(set(added_sources) | set(added_headers)):
                review_entries.append({
                    "path": relative,
                    "action": "add",
                    "metadata_impact": "pending",
                    "reason": "",
                    "reviewer": "",
                    "reviewed_at": "",
                })
            for relative in sorted(set(removed_sources) | set(removed_headers)):
                review_entries.append({
                    "path": relative,
                    "action": "remove",
                    "metadata_impact": "pending",
                    "reason": "",
                    "reviewer": "",
                    "reviewed_at": "",
                })
            review = {
                "schema": "runtime-sync-authority-migration-review/v1",
                "transaction_id": transaction_id,
                "entries": review_entries,
            }
            atomic_write_json(root / "review" / "authority_review.json", review)

            renames = {
                **self._infer_renames(
                    removed_sources,
                    added_sources,
                    baseline_manifest,
                    root / "work" / "candidate",
                ),
                **self._infer_renames(
                    removed_headers,
                    added_headers,
                    baseline_manifest,
                    root / "work" / "candidate",
                ),
            }
            candidate_record_names = {
                record.relative_path: record.filename for record in generated.records
            }
            state = {
                "schema": "runtime-sync-authority-migration/v1",
                "transaction_kind": "authority_migration",
                "transaction_id": transaction_id,
                "state": "CHECKED_OUT",
                "created_at": created_at,
                "updated_at": created_at,
                "deterministic_updated_at": created_at,
                "purpose": purpose,
                "baseline_package_sha256": baseline_manifest["package_sha256"],
                "candidate_root_source": candidate_root.as_posix(),
                "candidate_compile_commands_sha256": sha256_bytes(compile_commands.read_bytes()),
                "candidate_compiler_context_sha256": candidate_compiler_context_sha256,
                "candidate_command_counts": {unit.relative_path: len(unit.commands) for unit in survey.units},
                "project_scope": {
                    "workspace_id": workspace_id,
                    "goal_id": goal_id,
                    "milestone_id": milestone_id,
                    "task_id": task_id,
                    "project_intent_sha256": project_intent_sha256,
                },
                "candidate_sources": candidate_sources,
                "candidate_headers": candidate_headers,
                "candidate_records": candidate_record_names,
                "candidate_include_owners": {
                    relative: sorted(owners) for relative, (_, owners) in sorted(headers.items())
                },
                "candidate_include_edges": candidate_edge_payload["edges"],
                "candidate_include_topology_sha256": candidate_edge_payload["topology_sha256"],
                "candidate_include_edge_count": candidate_edge_payload["edge_count"],
                "candidate_include_roots": [
                    self._map_root(path, candidate_root, root / "work" / "candidate").as_posix()
                    for path in survey.include_roots
                ],
                "candidate_live_include_roots": [
                    self._map_root(path, candidate_root, self.config.codebase_root).as_posix()
                    for path in survey.include_roots
                ],
                "candidate_include_sequences": self._serialized_include_sequences(
                    survey, candidate_root, root / "work" / "candidate"
                ),
                "candidate_live_include_sequences": self._serialized_include_sequences(
                    survey, candidate_root, self.config.codebase_root
                ),
                "candidate_include_variants": self._serialized_include_variants(
                    survey, candidate_root, root / "work" / "candidate"
                ),
                "candidate_live_include_variants": self._serialized_include_variants(
                    survey, candidate_root, self.config.codebase_root
                ),
                "candidate_compiler_probes": self._serialized_compiler_probes(
                    survey, candidate_root, root / "work" / "candidate"
                ),
                "candidate_live_compiler_probes": self._serialized_compiler_probes(
                    survey, candidate_root, self.config.codebase_root
                ),
                "candidate_external_include_roots": list(survey.ignored_external_include_roots),
                "added_sources": added_sources,
                "removed_sources": removed_sources,
                "added_headers": added_headers,
                "removed_headers": removed_headers,
                "content_changed": content_changed,
                "renames": renames,
                "journal": [],
            }
            self.engine._save_transaction(root, state, event="AUTHORITY_CHECKOUT_COMPLETED", details={
                "added_sources": added_sources,
                "removed_sources": removed_sources,
                "added_headers": added_headers,
                "removed_headers": removed_headers,
                "content_changed": content_changed,
                "renames": renames,
            })
        except Exception:
            if self.engine.lease_path.exists():
                self.engine._release_lease(transaction_id)
            raise
        return {
            "schema": "runtime-sync-authority-checkout/v1",
            "transaction_id": transaction_id,
            "state": "CHECKED_OUT",
            "work_directory": str(root / "work"),
            "review": str(root / "review" / "authority_review.json"),
            "added_sources": added_sources,
            "removed_sources": removed_sources,
            "added_headers": added_headers,
            "removed_headers": removed_headers,
            "content_changed": content_changed,
            "renames": renames,
        }

    def _review(self, root: Path, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        review = read_json(root / "review" / "authority_review.json")
        if review.get("schema") != "runtime-sync-authority-migration-review/v1" or review.get("transaction_id") != state["transaction_id"]:
            raise WorkshopError("AUTHORITY_MIGRATION_REVIEW_INVALID", "authority migration review schema or transaction binding is invalid")
        entries = review.get("entries")
        if not isinstance(entries, list):
            raise WorkshopError("AUTHORITY_MIGRATION_REVIEW_INVALID", "authority migration review entries must be an array")
        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise WorkshopError("AUTHORITY_MIGRATION_REVIEW_INVALID", "review entry has no path")
            relative = _safe_relative(entry["path"])
            if relative in result:
                raise WorkshopError("AUTHORITY_MIGRATION_REVIEW_INVALID", f"duplicate migration review entry: {relative}")
            result[relative] = entry
        return result

    def _review_ok(self, entry: dict[str, Any], expected_action: str) -> bool:
        impact = str(entry.get("metadata_impact", ""))
        reason = str(entry.get("reason", "")).strip()
        reviewer = str(entry.get("reviewer", "")).strip()
        reviewed_at = str(entry.get("reviewed_at", "")).strip()
        allowed = {
            "update": {"updated", "none"},
            "add": {"created"},
            "remove": {"removed"},
        }[expected_action]
        return impact in allowed and len(reason) >= 12 and bool(reviewer) and bool(reviewed_at)

    def _derived_sections_for(self, relative: str, state: dict[str, Any]) -> tuple[str, ...]:
        return ("s-ownership",) if relative in set(state["candidate_headers"]) else ("s-includes", "s-boundary")

    def _stage_final_config(self, root: Path, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        final = dict(self.config.raw)
        final["codebase_root"] = self.config.codebase_root.as_posix()
        final["runtime_root"] = self.config.runtime_root.as_posix()
        final["cmake_file"] = (self.config.machine_root / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake").as_posix()
        final["dataflow_index"] = self.config.dataflow_index.as_posix()
        final["blueprint_root"] = self.config.blueprint_root.as_posix()
        final["managed_blueprint_root"] = self.config.managed_blueprint_root.as_posix()
        final["kairos_workspace"] = self.config.kairos_workspace.as_posix()
        final["kairos_harness"] = self.config.kairos_harness.as_posix()
        final["kairos_database"] = self.config.kairos_database.as_posix()
        # Header-only authority migration does not own the compiler context. Preserve
        # the exact live build-derived routing data; candidate compiler evidence was
        # already required to prove it equivalent during checkout.
        final["include_roots"] = json.loads(json.dumps(self.config.raw.get("include_roots", [])))
        final["translation_unit_include_roots"] = json.loads(
            json.dumps(self.config.raw.get("translation_unit_include_roots", {}))
        )
        final["translation_unit_include_variants"] = json.loads(
            json.dumps(self.config.raw.get("translation_unit_include_variants", {}))
        )
        final["translation_unit_compiler_probes"] = json.loads(
            json.dumps(self.config.raw.get("translation_unit_compiler_probes", {}))
        )
        final["expected_translation_units"] = self.config.expected_translation_units
        _, _, filename_for, _, _, header_suffixes, _, _, _ = _load_kickstart()
        final["header_extensions"] = sorted(header_suffixes)
        final["header_only_blueprints"] = {
            relative: filename_for(relative, header_only=True)
            for relative in state["candidate_headers"]
        }
        final["additional_header_owners"] = {}

        verify = json.loads(json.dumps(final))
        candidate = root / "work" / "candidate"
        shadow = root / "shadow"
        machine = root / "work" / "machine-verify"
        external = root / "work" / "external-final"
        verify["codebase_root"] = candidate.as_posix()
        verify["runtime_root"] = candidate.as_posix()
        verify["cmake_file"] = (machine / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake").as_posix()
        verify["dataflow_index"] = (shadow / "docs" / "PROJECT_SOURCE_INDEX.md").as_posix()
        verify["blueprint_root"] = external.as_posix()
        verify["managed_blueprint_root"] = (shadow / "code").as_posix()
        verify["kairos_workspace"] = shadow.as_posix()
        verify["kairos_database"] = (shadow / ".kairos" / "kairos.db").as_posix()
        verify["include_roots"] = state["candidate_include_roots"]
        verify["translation_unit_include_roots"] = state["candidate_include_sequences"]
        verify["translation_unit_include_variants"] = state["candidate_include_variants"]
        verify["translation_unit_compiler_probes"] = state["candidate_compiler_probes"]
        return final, verify

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "authority_migration":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not an authority migration")
        if state["state"] not in {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"authority prepare is not allowed from {state['state']}")
        self.engine._assert_live_baseline(state)
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        review = self._review(root, state)
        blockers: list[dict[str, Any]] = []
        added = set(state["added_sources"]) | set(state["added_headers"])
        removed = set(state["removed_sources"]) | set(state["removed_headers"])
        changed = set(state["content_changed"])
        for relative in sorted(changed):
            entry = review.get(relative, {})
            if not self._review_ok(entry, "update"):
                blockers.append({"path": relative, "reason": "changed existing file requires reviewed metadata_impact updated/none"})
        for relative in sorted(added):
            if not self._review_ok(review.get(relative, {}), "add"):
                blockers.append({"path": relative, "reason": "added governed file requires metadata_impact=created review"})
        for relative in sorted(removed):
            if not self._review_ok(review.get(relative, {}), "remove"):
                blockers.append({"path": relative, "reason": "removed governed file requires metadata_impact=removed review"})

        candidate_root = root / "work" / "candidate"
        generated_root = root / "work" / "generated"
        prepared_active: dict[str, str] = {}
        updated_existing: list[str] = []
        created_active: list[str] = []
        for relative in state["candidate_sources"] + state["candidate_headers"]:
            name = state["candidate_records"][relative]
            generated_path = generated_root / name
            candidate_file = candidate_root / relative
            if relative in added:
                work_blueprint = root / "work" / "blueprints" / name
                if not work_blueprint.is_file():
                    copy_exact(generated_path, work_blueprint)
                # Additions have no previous semantic authority. The generated document
                # is deliberately conservative and may be enriched before prepare.
                document = parse_blueprint(work_blueprint)
                issues = verify_ledger(
                    document,
                    candidate_file if relative in set(state["candidate_sources"]) else None,
                    candidate_file if relative in set(state["candidate_headers"]) else None,
                )
                if issues:
                    raise WorkshopError("GENERATED_LEDGER_INVALID", f"added blueprint ledger is invalid: {name}", details=issues)
                # The external work document is the operator-editable semantic source;
                # managed projection must be byte-identical when prepare closes.
                atomic_write_bytes(root / "work" / "managed" / name, work_blueprint.read_bytes())
                prepared_active[relative] = name
                created_active.append(relative)
                continue

            record = baseline_manifest["records"][relative]
            baseline_name = record["blueprint"]["filename"]
            baseline_path = self.config.blueprint_root / baseline_name
            work_path = root / "work" / "blueprints" / baseline_name
            if not work_path.is_file():
                copy_exact(baseline_path, work_path)
            baseline_doc = parse_blueprint(baseline_path)
            work_doc = parse_blueprint(work_path)
            content_changed = relative in changed
            token_changed = False
            if content_changed:
                live_fact = record.get("source") or record.get("header")
                live_path = Path(str(live_fact["path"]))
                token_changed = cpp_token_signature(live_path.read_bytes()) != cpp_token_signature(candidate_file.read_bytes())
                entry = review.get(relative, {})
                semantic_changed = semantic_document_hash(baseline_doc) != semantic_document_hash(work_doc)
                if token_changed and entry.get("metadata_impact") != "updated":
                    blockers.append({"path": relative, "reason": "token stream changed; semantic metadata update is mandatory"})
                if entry.get("metadata_impact") == "updated" and not semantic_changed:
                    blockers.append({"path": relative, "reason": "metadata_impact=updated but semantic blueprint content is unchanged"})
                if entry.get("metadata_impact") == "none" and semantic_changed:
                    blockers.append({"path": relative, "reason": "metadata_impact=none conflicts with semantic blueprint change"})
                if blockers:
                    continue
                rendered = regenerate_mechanical_layers(
                    work_doc,
                    candidate_file if relative in set(state["candidate_sources"]) else None,
                    candidate_file if relative in set(state["candidate_headers"]) else None,
                    revision=baseline_doc.revision + 1,
                    updated_at=state["deterministic_updated_at"],
                )
            else:
                rendered = work_doc.text

            # Compiler/include ownership is derived authority and must follow the
            # candidate even if code bytes did not change.
            candidate_generated = generated_path.read_text(encoding="utf-8")
            current_for_sections = rendered
            current_doc = parse_blueprint_text(work_path, current_for_sections)
            candidate_doc = parse_blueprint(generated_path)
            derived_changed = any(
                section_id in current_doc.sections
                and section_id in candidate_doc.sections
                and current_doc.text[current_doc.sections[section_id].start:current_doc.sections[section_id].end]
                    != candidate_doc.text[candidate_doc.sections[section_id].start:candidate_doc.sections[section_id].end]
                for section_id in self._derived_sections_for(relative, state)
            )
            if derived_changed:
                revision = baseline_doc.revision + 1
                rendered = _replace_sections(
                    current_for_sections,
                    candidate_generated,
                    self._derived_sections_for(relative, state),
                    current_path=work_path,
                    candidate_path=generated_path,
                    revision=revision if current_doc.revision == baseline_doc.revision else current_doc.revision,
                    updated_at=state["deterministic_updated_at"],
                )
            final_doc = parse_blueprint_text(work_path, rendered)
            needs_update = content_changed or derived_changed
            if needs_update:
                if final_doc.revision != baseline_doc.revision + 1:
                    raise WorkshopError("WORK_REVISION_INVALID", f"migration blueprint revision must be baseline+1: {baseline_name}")
                atomic_write_bytes(work_path, rendered.encode("utf-8"))
                atomic_write_bytes(root / "work" / "managed" / baseline_name, rendered.encode("utf-8"))
                prepared_active[relative] = baseline_name
                updated_existing.append(relative)

        if blockers:
            state["state"] = "AWAITING_METADATA_REVIEW"
            self.engine._save_transaction(root, state, event="AUTHORITY_METADATA_REVIEW_REQUIRED", details=blockers)
            raise WorkshopError("METADATA_REVIEW_REQUIRED", "authority migration is blocked until review is complete", details=blockers)

        tombstones: dict[str, str] = {}
        for relative in sorted(removed):
            record = baseline_manifest["records"][relative]
            name = record["blueprint"]["filename"]
            tombstone = _retire_document(self.config.managed_blueprint_root / name, updated_at=state["deterministic_updated_at"])
            atomic_write_bytes(root / "work" / "managed" / name, tombstone.encode("utf-8"))
            tombstones[relative] = name

        # Source index is purely compiler/include authority. Replace it from the
        # candidate rendering but advance the existing artifact exactly once.
        generated_index = (generated_root / "PROJECT_SOURCE_INDEX.md").read_text(encoding="utf-8")
        live_index = self.config.dataflow_index.read_text(encoding="utf-8")
        prepared_index = _advance_generated_document(
            generated_index,
            live_index,
            updated_at=state["deterministic_updated_at"],
            path=self.config.dataflow_index,
        )
        relative_index = self.config.dataflow_index.resolve().relative_to(self.config.kairos_workspace.resolve()).as_posix()
        atomic_write_bytes(root / "work" / "authority" / relative_index, prepared_index.encode("utf-8"))

        # Assemble the active external blueprint set expected after migration.
        external_final = root / "work" / "external-final"
        if external_final.exists():
            shutil.rmtree(external_final)
        external_final.mkdir(parents=True)
        active_relatives = state["candidate_sources"] + state["candidate_headers"]
        for relative in active_relatives:
            name = state["candidate_records"][relative]
            if relative in prepared_active:
                source = root / "work" / "blueprints" / prepared_active[relative]
            else:
                # Unchanged path retains its existing semantic blueprint.
                baseline_name = baseline_manifest["records"][relative]["blueprint"]["filename"]
                source = self.config.blueprint_root / baseline_name
                name = baseline_name
            copy_exact(source, external_final / name)
            # Every active external blueprint must have a byte-identical managed source
            # in the candidate shadow. Copy unchanged documents into the migration work
            # managed set only for assembly; live apply touches only changed paths.
            managed_stage = root / "work" / "managed-final" / name
            if relative in prepared_active:
                copy_exact(root / "work" / "managed" / prepared_active[relative], managed_stage)
            else:
                copy_exact(self.config.managed_blueprint_root / name, managed_stage)

        final_config, verify_config = self._stage_final_config(root, state)
        machine = root / "work" / "machine"
        final_intake = read_json(self.config.machine_root / "project-intake.json")
        final_intake = json.loads(json.dumps(final_intake))
        final_intake["project_root"] = self.config.codebase_root.as_posix()
        final_intake["workspace"] = self.config.kairos_workspace.as_posix()
        # Preserve the retained compiler/build authority exactly. This generic
        # migration has already proved that translation-unit membership and compiler
        # context are unchanged and therefore has no authority to rewrite their
        # provenance merely because the candidate lived at another checkout path.
        final_intake["authority"] = json.loads(json.dumps(final_intake.get("authority") or {}))
        unit_rows = {
            str(row.get("relative_path", "")).replace("\\", "/"): row
            for row in final_intake.get("translation_units", [])
            if isinstance(row, dict) and isinstance(row.get("relative_path"), str)
        }
        if set(unit_rows) != set(state["candidate_sources"]):
            raise WorkshopError(
                "BUILD_AUTHORITY_MIGRATION_REQUIRED",
                "retained project-intake translation-unit authority differs from the candidate",
            )
        for relative in state["candidate_sources"]:
            path = candidate_root / relative
            unit_rows[relative]["facts"] = {
                "path": (self.config.codebase_root / relative).as_posix(),
                "sha256": sha256_bytes(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
        final_intake["translation_units"] = [unit_rows[relative] for relative in state["candidate_sources"]]
        final_intake["include_closure"] = {
            relative: {
                "owners": state["candidate_include_owners"].get(relative, []),
                "facts": {
                    "path": (self.config.codebase_root / relative).as_posix(),
                    "sha256": sha256_bytes((candidate_root / relative).read_bytes()),
                    "bytes": (candidate_root / relative).stat().st_size,
                },
            }
            for relative in state["candidate_headers"]
        }
        final_intake["include_edge_topology"] = {
            "edge_count": int(state["candidate_include_edge_count"]),
            "topology_sha256": str(state["candidate_include_topology_sha256"]),
        }
        final_intake["blueprints"] = [
            {
                "filename": state["candidate_records"][relative],
                "artifact_id": parse_blueprint(external_final / state["candidate_records"][relative]).artifact_id,
                "kind": "header_only" if relative in set(state["candidate_headers"]) else "translation_unit",
                "relative_path": relative,
            }
            for relative in active_relatives
        ]
        final_intake["migration"] = {
            "transaction_id": transaction_id,
            "candidate_compile_commands_sha256": state["candidate_compile_commands_sha256"],
            "added_sources": state["added_sources"],
            "removed_sources": state["removed_sources"],
            "added_headers": state["added_headers"],
            "removed_headers": state["removed_headers"],
            "renames": state["renames"],
            "updated_at": state["deterministic_updated_at"],
        }
        final_intake["workshop_config_sha256"] = _sha256_json(final_config)
        atomic_write_json(machine / "workshop.config.json", final_config)
        atomic_write_json(machine / "project-intake.json", final_intake)
        # Arrange final machine-authority layout.
        (machine / "project-intake").mkdir(parents=True, exist_ok=True)
        copy_exact(machine / "compile_commands.json", machine / "project-intake" / "compile_commands.json")
        copy_exact(machine / "PROJECT_BUILD_AUTHORITY.cmake", machine / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")
        atomic_write_json(
            machine / "project-intake" / "include-edges.json",
            {
                "schema": "kairos-compiler-include-edges/v1",
                "edge_count": int(state["candidate_include_edge_count"]),
                "topology_sha256": str(state["candidate_include_topology_sha256"]),
                "claim_boundary": (
                    "Direct compiler-observed include consumers are distinct from byte ownership "
                    "and transitive compiled-root reachability."
                ),
                "edges": state["candidate_include_edges"],
            },
        )

        state["state"] = "PREPARED"
        state["prepared_active"] = prepared_active
        state["updated_existing"] = updated_existing
        state["created_active"] = created_active
        state["tombstones"] = tombstones
        state["authority_documents"] = [relative_index]
        state["final_config_sha256"] = _sha256_json(final_config)
        self.engine._save_transaction(root, state, event="AUTHORITY_PREPARE_COMPLETED", details={
            "updated_existing": updated_existing,
            "created_active": created_active,
            "tombstones": tombstones,
        })
        return {
            "schema": "runtime-sync-authority-prepare/v1",
            "transaction_id": transaction_id,
            "state": "PREPARED",
            "updated_existing": updated_existing,
            "created_active": created_active,
            "retired": sorted(tombstones),
            "renames": state["renames"],
        }

    def _shadow_workspace(self, root: Path, state: dict[str, Any]) -> Path:
        shadow = root / "shadow"
        if shadow.exists():
            shutil.rmtree(shadow)
        (shadow / ".kairos" / "events").mkdir(parents=True)
        (shadow / ".kairos" / "receipts").mkdir(parents=True)
        copy_exact(self.config.kairos_workspace / ".kairos" / "config.json", shadow / ".kairos" / "config.json")
        copy_exact(root / "baseline" / "kairos.db", shadow / ".kairos" / "kairos.db")
        workspace_config = read_json(self.config.kairos_workspace / ".kairos" / "config.json")
        for root_name in workspace_config.get("document_roots", []):
            source_root = self.config.kairos_workspace / str(root_name)
            if source_root.is_dir():
                for source_path in source_root.rglob("*"):
                    if source_path.is_file():
                        copy_exact(source_path, shadow / source_path.relative_to(self.config.kairos_workspace))
        goal_root = self.config.kairos_workspace / "goals"
        if goal_root.is_dir():
            for source_path in goal_root.glob("*.json"):
                copy_exact(source_path, shadow / source_path.relative_to(self.config.kairos_workspace))
        for name in workspace_config.get("canonical_files", []):
            source_path = self.config.kairos_workspace / str(name)
            if source_path.is_file():
                copy_exact(source_path, shadow / str(name))
        current = self.config.kairos_workspace / "current.json"
        if current.is_file():
            copy_exact(current, shadow / "current.json")
        return shadow

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "authority_migration":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not an authority migration")
        if state["state"] not in {"PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"authority verify is not allowed from {state['state']}")
        self.engine._assert_live_baseline(state)
        shadow = self._shadow_workspace(root, state)
        changed_managed: list[str] = []
        for relative, name in state.get("prepared_active", {}).items():
            copy_exact(root / "work" / "managed" / name, shadow / "code" / name)
            changed_managed.append(f"code/{name}")
        for relative, name in state.get("tombstones", {}).items():
            copy_exact(root / "work" / "managed" / name, shadow / "code" / name)
            changed_managed.append(f"code/{name}")
        for relative in state.get("authority_documents", []):
            copy_exact(root / "work" / "authority" / relative, shadow / relative)
            changed_managed.append(relative)

        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        database_module = importlib.import_module("kairos.database")
        promoter_module = importlib.import_module("kairos.promoter")
        database = database_module.KnowledgeDatabase(shadow / ".kairos" / "kairos.db")
        receipts = []
        for relative in sorted(set(changed_managed)):
            receipt = promoter_module.promote_document(shadow / relative, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"migration shadow promotion failed: {relative}", details=receipt)
            receipts.append(receipt)

        # Build a candidate Workshop machine-root whose paths point only at the
        # staged candidate and shadow. This proves the exact final active corpus
        # before any live file is added, removed, or renamed.
        machine_verify = root / "work" / "machine-verify"
        if machine_verify.exists():
            shutil.rmtree(machine_verify)
        (machine_verify / "project-intake").mkdir(parents=True)
        _, verify_config = self._stage_final_config(root, state)
        atomic_write_json(machine_verify / "workshop.config.json", verify_config)
        copy_exact(root / "work" / "machine" / "project-intake" / "compile_commands.json", machine_verify / "project-intake" / "compile_commands.json")
        copy_exact(root / "work" / "machine" / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake", machine_verify / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")
        copy_exact(root / "work" / "machine" / "project-intake" / "include-edges.json", machine_verify / "project-intake" / "include-edges.json")
        verify_intake = read_json(root / "work" / "machine" / "project-intake.json")
        verify_intake = json.loads(json.dumps(verify_intake))
        candidate_root = root / "work" / "candidate"
        verify_intake["project_root"] = candidate_root.as_posix()
        verify_intake["workspace"] = shadow.as_posix()
        verify_intake["workshop_config_sha256"] = _sha256_json(verify_config)
        verify_intake["authority"] = dict(verify_intake.get("authority") or {})
        retained_compile = machine_verify / "project-intake" / "compile_commands.json"
        verify_intake["authority"]["compile_commands"] = {
            "path": retained_compile.as_posix(),
            "sha256": sha256_bytes(retained_compile.read_bytes()),
            "bytes": retained_compile.stat().st_size,
        }
        verify_intake["authority"]["cmake_file"] = None
        verify_intake["authority"]["cmake_build_directory"] = None
        verify_intake["include_roots"] = list(state["candidate_include_roots"])
        candidate_sequences = state.get("candidate_include_sequences", {})
        for row in verify_intake.get("translation_units", []):
            if not isinstance(row, dict) or not isinstance(row.get("relative_path"), str):
                continue
            relative = row["relative_path"].replace("\\", "/")
            sequences = candidate_sequences.get(relative, [])
            row["include_roots"] = sorted({
                str(value)
                for sequence in sequences
                if isinstance(sequence, list)
                for value in sequence
            })
            row["include_search_variants"] = [
                {
                    key: list(item.get(key, []))
                    for key in ("quote_roots", "angle_roots", "idirafter_roots")
                }
                for item in state.get("candidate_include_variants", {}).get(relative, [])
                if isinstance(item, dict)
            ]
            facts = row.get("facts")
            if isinstance(facts, dict):
                facts["path"] = (candidate_root / relative).as_posix()
        closure = verify_intake.get("include_closure")
        if isinstance(closure, dict):
            for relative, row in closure.items():
                if isinstance(row, dict) and isinstance(row.get("facts"), dict):
                    row["facts"]["path"] = (candidate_root / str(relative)).as_posix()
        atomic_write_json(machine_verify / "project-intake.json", verify_intake)

        # Project exact direct edges into the isolated candidate database before the
        # corpus postcheck. This is the same mechanical projection the live heartbeat
        # will perform after apply.
        include_module = importlib.import_module("kairos.include_edges")
        include_projection = include_module.project_compiler_include_edges(
            shadow,
            database,
            authority_path=machine_verify / "project-intake" / "include-edges.json",
        )
        if not include_projection.get("verified"):
            raise WorkshopError(
                "AUTHORITY_MIGRATION_INCLUDE_EDGE_SHADOW_FAILED",
                "candidate include-edge SQLite projection was not verified",
                details=include_projection,
            )

        # Assemble candidate managed active docs from shadow and external active docs.
        external_final = root / "work" / "external-final"
        candidate_config = load_config(machine_verify / "workshop.config.json")
        candidate_manifest = build_corpus_manifest(candidate_config, verify_database=True)
        if not candidate_manifest.get("verified"):
            raise WorkshopError("AUTHORITY_MIGRATION_SHADOW_INVALID", "candidate corpus failed Workshop verification", details=candidate_manifest.get("issues"))
        work_package = self._work_package(root, state)
        state["state"] = "SHADOW_VERIFIED"
        state["shadow"] = {
            "workspace": shadow.as_posix(),
            "receipts": receipts,
            "candidate_package_sha256": candidate_manifest["package_sha256"],
            "candidate_counts": candidate_manifest["counts"],
            "include_edge_projection": include_projection,
        }
        state["verified_work_package_sha256"] = work_package["package_sha256"]
        self.engine._save_transaction(root, state, event="AUTHORITY_SHADOW_VERIFIED", details=state["shadow"])
        return {
            "schema": "runtime-sync-authority-verify/v1",
            "transaction_id": transaction_id,
            "state": "SHADOW_VERIFIED",
            "candidate_package_sha256": candidate_manifest["package_sha256"],
            "counts": candidate_manifest["counts"],
            "work_package_sha256": work_package["package_sha256"],
        }

    def _work_package(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for base, directory in (
            ("candidate", root / "work" / "candidate"),
            ("blueprint", root / "work" / "blueprints"),
            ("managed", root / "work" / "managed"),
            ("authority", root / "work" / "authority"),
            ("machine", root / "work" / "machine"),
            ("external-final", root / "work" / "external-final"),
        ):
            if not directory.exists():
                continue
            for path in sorted(p for p in directory.rglob("*") if p.is_file()):
                relative = path.relative_to(directory).as_posix()
                raw = path.read_bytes()
                rows.append({"role": base, "path": relative, "sha256": sha256_bytes(raw), "size": len(raw)})
        return {
            "schema": "runtime-sync-authority-work-package/v1",
            "file_count": len(rows),
            "byte_count": sum(row["size"] for row in rows),
            "package_sha256": package_hash(rows),
            "files": rows,
        }

    def _backup_target(self, root: Path, live: Path, role: str, rows: list[dict[str, Any]]) -> None:
        exists = live.is_file()
        backup = root / "rollback" / role / f"{sha256_bytes(str(live).encode('utf-8'))[:16]}_{live.name}"
        if exists:
            copy_exact(live, backup)
        rows.append({
            "live": str(live),
            "backup": str(backup),
            "existed": exists,
            "sha256": sha256_bytes(live.read_bytes()) if exists else None,
        })

    def _rollback(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in reversed(rows):
            live = Path(row["live"])
            try:
                if row["existed"]:
                    copy_exact(Path(row["backup"]), live)
                    ok = sha256_bytes(live.read_bytes()) == row["sha256"]
                else:
                    if live.exists():
                        if live.is_dir():
                            raise RuntimeError("migration rollback refuses a created directory target")
                        live.unlink()
                    ok = not live.exists()
                result.append({"path": str(live), "restored": ok})
            except Exception as exc:
                result.append({"path": str(live), "restored": False, "error": str(exc)})
        return result

    def apply(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "authority_migration":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not an authority migration")
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"authority apply requires SHADOW_VERIFIED, found {state['state']}")
        self.engine._assert_live_baseline(state)
        work_package = self._work_package(root, state)
        if work_package["package_sha256"] != state.get("verified_work_package_sha256"):
            raise WorkshopError("WORK_CHANGED_AFTER_VERIFY", "authority migration work package changed after verification")

        changed_managed = sorted({
            *(f"code/{name}" for name in state.get("prepared_active", {}).values()),
            *(f"code/{name}" for name in state.get("tombstones", {}).values()),
            *state.get("authority_documents", []),
        })
        governance, heartbeat = self.engine._import_kairos()
        permit = governance.issue_external_edit_permit(
            self.config.kairos_workspace,
            paths=changed_managed,
            reason=f"Runtime Sync Workshop authority migration {transaction_id}: {state['purpose']}",
            ttl_seconds=1800,
        )

        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        active_candidate = set(state["candidate_sources"]) | set(state["candidate_headers"])
        removed = set(state["removed_sources"]) | set(state["removed_headers"])
        affected_runtime = sorted(set(state["content_changed"]) | set(state["added_sources"]) | set(state["added_headers"]) | removed)
        rollback_rows: list[dict[str, Any]] = []
        for relative in affected_runtime:
            self._backup_target(root, self.config.codebase_root / relative, "runtime", rollback_rows)
        affected_external = set(state.get("prepared_active", {}).values()) | {
            baseline_manifest["records"][relative]["blueprint"]["filename"] for relative in removed
        }
        for name in sorted(affected_external):
            self._backup_target(root, self.config.blueprint_root / name, "external", rollback_rows)
        affected_managed = set(state.get("prepared_active", {}).values()) | set(state.get("tombstones", {}).values())
        for name in sorted(affected_managed):
            self._backup_target(root, self.config.managed_blueprint_root / name, "managed", rollback_rows)
        for relative in state.get("authority_documents", []):
            self._backup_target(root, self.config.kairos_workspace / relative, "authority", rollback_rows)
        for relative in self.config.machine_authority_files:
            self._backup_target(root, self.config.machine_root / relative, "machine", rollback_rows)
        atomic_write_json(root / "rollback" / "targets.json", rollback_rows)

        state["state"] = "APPLYING"
        state["permit"] = permit
        self.engine._save_transaction(root, state, event="AUTHORITY_APPLY_STARTED", details={"managed_paths": changed_managed})
        heartbeat_started = False
        old_config_path = self.config.config_path
        try:
            # Runtime/code topology.
            for relative in state["candidate_sources"] + state["candidate_headers"]:
                if relative in set(state["content_changed"]) | set(state["added_sources"]) | set(state["added_headers"]):
                    destination = self.config.codebase_root / relative
                    if relative in set(state["added_sources"]) | set(state["added_headers"]):
                        if destination.exists() and relative not in baseline_manifest["records"]:
                            raise WorkshopError("AUTHORITY_MIGRATION_TARGET_EXISTS", f"added governed path already exists live: {relative}")
                    copy_exact(root / "work" / "candidate" / relative, destination)
            for relative in sorted(removed):
                target = self.config.codebase_root / relative
                if target.is_file():
                    target.unlink()

            # Active external blueprints: replace changed/add, remove retired.
            for relative, name in state.get("prepared_active", {}).items():
                copy_exact(root / "work" / "external-final" / name, self.config.blueprint_root / name)
            for relative in sorted(removed):
                name = baseline_manifest["records"][relative]["blueprint"]["filename"]
                path = self.config.blueprint_root / name
                if path.is_file():
                    path.unlink()

            # Managed KAIROS source: active docs + tombstones + source-index authority.
            for relative, name in state.get("prepared_active", {}).items():
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for relative, name in state.get("tombstones", {}).items():
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for relative in state.get("authority_documents", []):
                copy_exact(root / "work" / "authority" / relative, self.config.kairos_workspace / relative)

            # Machine authority becomes the candidate authority only after all file/doc
            # bytes are in place. The config is last so a mid-copy failure is rolled back
            # under the old engine configuration.
            machine = root / "work" / "machine"
            for relative in self.config.machine_authority_files:
                source = machine / relative
                if not source.is_file():
                    raise WorkshopError("AUTHORITY_MIGRATION_MACHINE_FILE_MISSING", f"prepared machine authority missing: {relative}")
                copy_exact(source, self.config.machine_root / relative)

            self.engine.config = load_config(old_config_path)
            self.engine.scientific_gate = ScientificParityGate(self.engine.config)
            heartbeat_started = True
            heartbeat_receipt = heartbeat.run_heartbeat(
                self.engine.config.kairos_workspace,
                requested_mode="verify",
                changed_paths=changed_managed,
                use_reconciliation=True,
                trigger=f"runtime-sync-authority-migration:{transaction_id}",
            )
            if not heartbeat_receipt.get("verified"):
                raise WorkshopError("HEARTBEAT_NOT_VERIFIED", "authority migration heartbeat did not verify", details=heartbeat_receipt)
            health_module = importlib.import_module("kairos.health")
            health = health_module.health_audit(self.engine.config.kairos_workspace, full=True)
            if health.get("verdict") != "PASS":
                raise WorkshopError("KAIROS_HEALTH_FAILED", "authority migration KAIROS health audit failed", details=health.get("failures"))
            post = build_corpus_manifest(self.engine.config)
            if not post.get("verified"):
                raise WorkshopError("POSTCHECK_FAILED", "authority migration postcheck failed", details=post.get("issues"))
            if set(post["authority"]["translation_units"]) != set(state["candidate_sources"]):
                raise WorkshopError("POSTCHECK_AUTHORITY_MISMATCH", "postcheck translation-unit authority differs from candidate")
            if set(post["authority"]["include_headers"]) != set(state["candidate_headers"]):
                raise WorkshopError("POSTCHECK_AUTHORITY_MISMATCH", "postcheck header authority differs from candidate")
            mirror = self.engine._mirror(post)
            seal = {
                "schema": "runtime-sync-seal/v1",
                "created_at": utc_now(),
                "package_sha256": post["package_sha256"],
                "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(self.engine.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"),
                "counts": post["counts"],
                "transaction_id": transaction_id,
                "authority_migration": True,
            }
            atomic_write_json(self.engine.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["heartbeat"] = heartbeat_receipt
            self.engine._save_transaction(root, state, event="AUTHORITY_POSTCHECK_VERIFIED", details={"package_sha256": post["package_sha256"]})
            self.engine._release_lease(transaction_id)
            receipt = {
                "schema": "runtime-sync-authority-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "baseline_package_sha256": state["baseline_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "heartbeat": heartbeat_receipt,
                "health": {"verdict": health.get("verdict"), "failure_count": len(health.get("failures", []))},
                "bit_exact": True,
                "renames": state["renames"],
                "added_sources": state["added_sources"],
                "removed_sources": state["removed_sources"],
                "added_headers": state["added_headers"],
                "removed_headers": state["removed_headers"],
            }
            self.engine._write_receipt("authority-postcheck", receipt)
            return receipt
        except Exception as exc:
            if heartbeat_started:
                state["state"] = "RECOVERY_REQUIRED"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self.engine._save_transaction(root, state, event="AUTHORITY_RECOVERY_REQUIRED", details=state["failure"])
                raise WorkshopError("RECOVERY_REQUIRED", "authority migration crossed the heartbeat boundary and failed; lease retained", details=state["failure"]) from exc
            rollback = self._rollback(rollback_rows)
            self.engine.config = load_config(old_config_path)
            self.engine.scientific_gate = ScientificParityGate(self.engine.config)
            if all(row.get("restored") for row in rollback):
                state["state"] = "ROLLED_BACK"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self.engine._save_transaction(root, state, event="AUTHORITY_APPLY_ROLLED_BACK", details=rollback)
                self.engine._release_lease(transaction_id)
                raise WorkshopError("APPLY_ROLLED_BACK", "authority migration failed before heartbeat and all targets were restored", details={"cause": state["failure"], "rollback": rollback}) from exc
            state["state"] = "RECOVERY_REQUIRED"
            self.engine._save_transaction(root, state, event="AUTHORITY_ROLLBACK_INCOMPLETE", details=rollback)
            raise WorkshopError("RECOVERY_REQUIRED", "authority migration rollback was incomplete; lease retained", details=rollback) from exc

    def abort(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "authority_migration":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not an authority migration")
        if state["state"] not in {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"authority abort is not allowed from {state['state']}")
        self.engine._assert_live_baseline(state)
        state["state"] = "ABORTED"
        self.engine._save_transaction(root, state, event="AUTHORITY_MIGRATION_ABORTED")
        self.engine._release_lease(transaction_id)
        return {"schema": "runtime-sync-authority-abort/v1", "transaction_id": transaction_id, "state": "ABORTED"}

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        _, state = self.engine._load_transaction(transaction_id)
        if state.get("transaction_kind") != "authority_migration":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not an authority migration")
        return state
