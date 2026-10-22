"""Baseline-bound semantic Markdown inside ordinary Workshop transactions.

The editor owns semantic changes. This module checks their review and exact
projection; it never invents summaries, rewrites historical references or writes SQL.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .corpus import WorkshopConfig
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    read_json,
    reject_link_components,
    sha256_bytes,
    sha256_text,
)


CONTEXT_FIELDS = frozenset({"capsule", "answers", "refs", "claim_boundary", "sections"})


class ContextDocuments:
    def __init__(self, config: WorkshopConfig) -> None:
        self.config = config

    def _module(self, name: str) -> Any:
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        return importlib.import_module(f"kairos.{name}")

    @staticmethod
    def _regular(path: Path, *, code: str = "CONTEXT_DOCUMENT_INVALID") -> None:
        try:
            reject_link_components(path, label="context document")
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("expected a single-link regular file")
        except (OSError, ValueError, WorkshopError) as exc:
            raise WorkshopError(code, f"unsafe or missing context file: {path}", details=str(exc)) from exc

    @classmethod
    def _file(cls, path: Path, *, code: str = "CONTEXT_DOCUMENT_INVALID") -> bytes:
        cls._regular(path, code=code)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise WorkshopError(code, f"cannot read context file: {path}", details=str(exc)) from exc

    def normalize(self, documents: Iterable[str]) -> list[str]:
        selected: set[str] = set()
        for value in documents:
            if not isinstance(value, str):
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", "document paths must be strings")
            relative = value.replace("\\", "/")
            parts = relative.split("/")
            if (not relative or any(part in {"", ".", ".."} for part in parts)
                    or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
                    or ":" in relative or "\x00" in relative
                    or PurePosixPath(relative).suffix.lower() != ".md"):
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"expected an exact workspace-relative Markdown path: {value}")
            selected.add(relative)
        if not selected:
            return []
        if str(self.config.raw.get("shadow_adapter", "native")) != "native":
            raise WorkshopError("CONTEXT_DOCUMENT_INVALID", "semantic context documents require the native KAIROS adapter")
        workspace = self.config.kairos_workspace
        workspace_config = read_json(workspace / ".kairos" / "config.json")
        roots = [str(value).replace("\\", "/").rstrip("/") for value in workspace_config.get("document_roots", [])]
        canonical = set(workspace_config.get("canonical_files", []))
        dynamic = self._module("constants").DYNAMIC_CANONICAL_FILES
        protected = {self.config.dataflow_index, self.config.cmake_file}
        protected.update(self.config.machine_root / value for value in self.config.machine_authority_files)
        for relative in sorted(selected):
            path = workspace / relative
            managed = relative in canonical or any(relative.startswith(f"{root}/") for root in roots)
            if (not managed or relative in dynamic or relative.startswith((".kairos/", "code/"))
                    or path in protected or path.is_relative_to(self.config.managed_blueprint_root)):
                raise WorkshopError(
                    "CONTEXT_DOCUMENT_INVALID",
                    f"document is unmanaged, generated, or owned by a source/authority transaction: {relative}",
                )
            raw = self._file(path)
            parsed = self._parse(raw, relative)
            if parsed.metadata["type"] in {"router", "gate", "session"}:
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"generated routing documents cannot be edited here: {relative}")
        return sorted(selected)

    def _parse(self, raw: bytes, relative: str) -> Any:
        try:
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError("NUL byte")
            return self._module("frontmatter").split_frontmatter(text)
        except (UnicodeError, ValueError) as exc:
            raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"invalid context Markdown: {relative}", details=str(exc)) from exc

    @staticmethod
    def _semantic(parsed: Any) -> str:
        metadata = {key: value for key, value in parsed.metadata.items() if key not in {"revision", "updated_at"}}
        return sha256_text(canonical_json({"metadata": metadata, "body": parsed.body}))

    def _advance(self, parsed: Any, *, revision: int, updated_at: str) -> bytes:
        # Preserve every metadata table, including graph declarations that the
        # harness's generated-document renderer intentionally cannot emit.
        header, separator, tables = parsed.raw_header.partition("\n[")
        for key, value in (("revision", str(revision)), ("updated_at", json.dumps(updated_at))):
            header, count = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", header)
            if count != 1:
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"expected one top-level {key} field")
        raw = f"+++\n{header}{separator}{tables}\n+++\n{parsed.body}".encode("utf-8")
        rendered = self._parse(raw, str(parsed.metadata["id"]))
        if (self._semantic(rendered) != self._semantic(parsed) or rendered.metadata["revision"] != revision
                or rendered.metadata["updated_at"] != updated_at):
            raise WorkshopError("CONTEXT_DOCUMENT_INVALID", "revision update changed semantic Markdown content")
        return raw

    def checkout(self, root: Path, state: dict[str, Any], documents: Iterable[str]) -> None:
        selected = self.normalize(documents)
        existing = list(state.get("documents", []))
        added = sorted(set(selected) - set(existing))
        if not added:
            state.setdefault("documents", [])
            state.setdefault("baseline_documents", {})
            return
        self.verify_projection(self.config.kairos_workspace, added)
        snapshot = root / "baseline" / "kairos.db"
        if snapshot.is_file():
            self.verify_projection(self.config.kairos_workspace, added, database_path=snapshot)
        review_path = root / "review" / "context_review.json"
        if existing:
            review = self._review(root, state)
        else:
            review = {"schema": "runtime-sync-context-review/v1", "transaction_id": state["transaction_id"], "entries": []}
        baselines = dict(state.get("baseline_documents", {}))
        snapshots: dict[str, bytes] = {}
        for relative in added:
            live = self.config.kairos_workspace / relative
            raw = self._file(live)
            parsed = self._parse(raw, relative)
            baseline = root / "baseline" / "documents" / relative
            work = root / "work" / "documents" / relative
            # A failed expansion must not overwrite work on its retry.
            if baseline.exists() or work.exists():
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"untracked staged context document already exists: {relative}")
            baselines[relative] = {
                "sha256": sha256_bytes(raw),
                "artifact_id": parsed.metadata["id"],
                "revision": parsed.metadata["revision"],
            }
            snapshots[relative] = raw
        for relative in added:
            atomic_write_bytes(root / "baseline" / "documents" / relative, snapshots[relative])
            atomic_write_bytes(root / "work" / "documents" / relative, snapshots[relative])
            if self._file(self.config.kairos_workspace / relative) != snapshots[relative]:
                raise WorkshopError("CONTEXT_BASELINE_CHANGED", f"live context changed during checkout: {relative}")
            review["entries"].append({
                "document": relative,
                "metadata_impact": "pending",
                "reason": "", "reviewer": "", "reviewed_at": "", "reviewed_fields": [],
            })
        atomic_write_json(review_path, review)
        state["documents"] = sorted(set(existing) | set(added))
        state["baseline_documents"] = baselines

    def assert_baseline(self, root: Path, state: dict[str, Any]) -> None:
        documents = state.get("documents", [])
        if not documents:
            return
        try:
            self.normalize(documents)
            for relative in documents:
                bound = state["baseline_documents"][relative]
                for path in (root / "baseline" / "documents" / relative, self.config.kairos_workspace / relative):
                    raw = self._file(path, code="CONTEXT_BASELINE_CHANGED")
                    parsed = self._parse(raw, relative)
                    if (sha256_bytes(raw) != bound["sha256"] or parsed.metadata["id"] != bound["artifact_id"]
                            or parsed.metadata["revision"] != bound["revision"]):
                        raise WorkshopError("CONTEXT_BASELINE_CHANGED", f"context document changed since checkout: {relative}")
            self.verify_projection(self.config.kairos_workspace, documents)
        except (WorkshopError, KeyError, TypeError) as exc:
            if isinstance(exc, WorkshopError) and exc.code == "CONTEXT_BASELINE_CHANGED":
                raise
            raise WorkshopError("CONTEXT_BASELINE_CHANGED", "context baseline no longer matches its verified checkout", details=str(exc)) from exc

    def _review(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        path = root / "review" / "context_review.json"
        self._file(path, code="CONTEXT_REVIEW_REQUIRED")
        try:
            review = read_json(path)
        except WorkshopError as exc:
            raise WorkshopError("CONTEXT_REVIEW_REQUIRED", "context review must contain valid JSON", details=str(exc)) from exc
        if (not isinstance(review, dict) or review.get("schema") != "runtime-sync-context-review/v1"
                or review.get("transaction_id") != state["transaction_id"]
                or not isinstance(review.get("entries"), list)):
            raise WorkshopError("CONTEXT_REVIEW_REQUIRED", "context review has an invalid schema or transaction binding")
        names = [entry.get("document") for entry in review["entries"] if isinstance(entry, dict)]
        if (len(names) != len(review["entries"]) or any(not isinstance(name, str) for name in names)
                or len(names) != len(set(names)) or set(names) != set(state.get("documents", []))):
            raise WorkshopError("CONTEXT_REVIEW_REQUIRED", "context review must cover exactly the selected documents once each")
        return review

    def prepare(self, root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
        documents = state.get("documents", [])
        if not documents:
            state["changed_documents"] = []
            return []
        self.assert_baseline(root, state)
        review = {entry["document"]: entry for entry in self._review(root, state)["entries"]}
        blockers: list[dict[str, str]] = []
        rows: list[dict[str, Any]] = []
        generated: dict[str, bytes] = {}
        for relative in documents:
            baseline_raw = self._file(root / "baseline" / "documents" / relative)
            baseline = self._parse(baseline_raw, relative)
            work = self._parse(self._file(root / "work" / "documents" / relative), relative)
            for key in ("id", "workspace", "type"):
                if work.metadata[key] != baseline.metadata[key]:
                    raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"context identity {key} cannot change: {relative}")
            revision = baseline.metadata["revision"]
            if work.metadata["revision"] not in {revision, revision + 1}:
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", f"context revision must remain baseline or baseline+1: {relative}")
            changed = self._semantic(baseline) != self._semantic(work)
            entry = review[relative]
            fields = entry.get("reviewed_fields")
            impact = entry.get("metadata_impact")
            if (not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)
                    or not CONTEXT_FIELDS.issubset(fields)
                    or not isinstance(entry.get("reason"), str) or len(entry["reason"].strip()) < 12
                    or not isinstance(entry.get("reviewer"), str) or not entry["reviewer"].strip()
                    or not isinstance(entry.get("reviewed_at"), str) or not entry["reviewed_at"].strip()
                    or impact not in {"updated", "none"}
                    or (impact == "updated") != changed):
                blockers.append({"document": relative, "reason": "review all context fields; updated requires a semantic edit, none requires unchanged semantics"})
            rows.append({"document": relative, "changed": changed, "semantic_changed": changed})
            generated[relative] = self._advance(work, revision=revision + 1, updated_at=state["deterministic_updated_at"]) if changed else baseline_raw
        if blockers:
            raise WorkshopError("CONTEXT_REVIEW_REQUIRED", "context review is incomplete or contradicts staged Markdown", details=blockers)
        for relative, raw in generated.items():
            atomic_write_bytes(root / "work" / "documents" / relative, raw)
        state["changed_documents"] = [row["document"] for row in rows if row["changed"]]
        return rows

    def work_files(self, root: Path, state: dict[str, Any]) -> list[tuple[str, str, Path]]:
        files = []
        for relative in state.get("documents", []):
            # Validate the path independently of the live file: apply/recovery may
            # already have replaced live bytes by the verified candidate.
            if (not isinstance(relative, str) or any(part in {"", ".", ".."} for part in relative.split("/"))
                    or ":" in relative or "\\" in relative or relative.startswith("/")):
                raise WorkshopError("CONTEXT_DOCUMENT_INVALID", "invalid transaction document path")
            path = root / "work" / "documents" / relative
            self._file(path)
            files.append(("context-document", f"documents/{relative}", path))
        if files:
            path = root / "review" / "context_review.json"
            self._file(path, code="CONTEXT_REVIEW_REQUIRED")
            files.append(("context-review", "review/context_review.json", path))
        return files

    def postcheck(self, root: Path, state: dict[str, Any]) -> None:
        for relative in state.get("documents", []):
            live = self._file(self.config.kairos_workspace / relative)
            work = self._file(root / "work" / "documents" / relative)
            if live != work:
                raise WorkshopError("POSTCHECK_CONTEXT_MISMATCH", f"live context differs from verified work: {relative}")
        self.verify_projection(self.config.kairos_workspace, state.get("documents", []))

    def verify_projection(
        self, workspace: Path, documents: Iterable[str], database_path: Path | None = None,
    ) -> dict[str, Any]:
        """Check the source-derived search surfaces using read-only SQLite access."""
        selected = list(documents)
        if not selected:
            return {}
        promoter = self._module("promoter")
        path = database_path or (self.config.kairos_database if workspace == self.config.kairos_workspace else workspace / ".kairos" / "kairos.db")
        self._regular(path)
        result: dict[str, Any] = {}
        connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            for relative in selected:
                self._file(workspace / relative)
                document = promoter.prepare_document(workspace / relative, workspace)
                metadata = document.frontmatter.metadata
                artifact_id = metadata["id"]

                def expect(table: str, columns: tuple[str, ...], rows: Iterable[tuple[Any, ...]], *, key: str = "artifact_id") -> None:
                    actual = connection.execute(
                        f"SELECT {','.join(columns)} FROM {table} WHERE {key}=?", (artifact_id,),
                    ).fetchall()
                    if Counter(tuple(row) for row in actual) != Counter(rows):
                        raise WorkshopError("CONTEXT_PROJECTION_MISMATCH", f"{table} projection differs from context Markdown: {relative}")

                row = connection.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                scalar_fields = {
                    "document_type": metadata["type"], "state": metadata["state"],
                    "authority": metadata["authority"], "workspace_id": metadata["workspace"],
                    "route": metadata["route"], "updated_at": metadata["updated_at"],
                    "header_bytes": document.frontmatter.header_bytes,
                    "loop_id": str(metadata.get("loop", "")) or None,
                    "task_id": metadata.get("task") or None, "goal_id": metadata.get("goal") or None,
                    "milestone_id": metadata.get("milestone") or None,
                }
                if (not row or row["path"] != relative or row["revision"] != metadata["revision"]
                        or row["content_sha256"] != document.content_sha256
                        or row["capsule"] != metadata["capsule"] or row["claim_boundary"] != metadata["claim_boundary"]
                        or json.loads(row["metadata_json"]) != metadata
                        or any(row[key] != value for key, value in scalar_fields.items())):
                    raise WorkshopError("CONTEXT_PROJECTION_MISMATCH", f"artifact projection differs from context Markdown: {relative}")
                revision = connection.execute(
                    "SELECT content_sha256,path,metadata_json FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                    (artifact_id, metadata["revision"]),
                ).fetchone()
                if (not revision or revision["content_sha256"] != document.content_sha256 or revision["path"] != relative
                        or json.loads(revision["metadata_json"]) != metadata):
                    raise WorkshopError("CONTEXT_PROJECTION_MISMATCH", f"immutable context revision differs: {relative}")
                expect("sections", ("section_id", "position", "title", "capsule", "body", "body_sha256"), (
                    (section.section_id, section.position, section.title, section.capsule, section.body, sha256_text(section.body))
                    for section in document.sections
                ))
                expect("section_fts", ("section_id", "title", "capsule", "questions", "entities", "facets", "body"), (
                    (section.section_id, section.title, f"{metadata['capsule']} | {section.capsule} | {metadata['claim_boundary']}",
                     " | ".join(answer["question"] for answer in metadata.get("answers", []) if answer["target"] == section.section_id),
                     " | ".join(metadata.get("entities", [])), " | ".join(metadata.get("facets", [])), section.body)
                    for section in document.sections
                ))
                expect("query_handles", ("section_id", "intent", "question", "language", "weight"), (
                    (answer["target"], answer["intent"], answer["question"], answer.get("language", "en"), float(answer.get("weight", 1.0)))
                    for answer in metadata.get("answers", [])
                ))
                # INSERT OR REPLACE collapses duplicate non-null section keys;
                # SQLite permits duplicates for header references (NULL section).
                references: list[tuple[Any, ...]] = []
                section_refs: dict[tuple[Any, ...], tuple[Any, ...]] = {}
                for section, ref in document.references:
                    projected = (section, ref.relation, ref.target_id, ref.target_path, ref.target_section,
                                 ref.version, canonical_json(list(ref.tags)), ref.source, 1.0)
                    if section is None or ref.target_section is None:
                        references.append(projected)
                    else:
                        section_refs[(section, ref.relation, ref.target_path, ref.target_section)] = projected
                references.extend(section_refs.values())
                expect("relations", ("source_section_id", "predicate", "target_artifact_id", "target_path", "target_section_id", "version", "tags_json", "provenance", "confidence"), references, key="source_artifact_id")
                for header_key, table, columns in promoter.GRAPH_PROJECTIONS:
                    names = ("ordinal", *(column for column, _ in columns))
                    expected = []
                    for ordinal, declared in enumerate(metadata.get(header_key, []), 1):
                        expected.append((ordinal, *(promoter._graph_cell(column, next((declared[key] for key in keys if key in declared), None)) for column, keys in columns)))
                    expect(table, names, expected)
                if document.search_contract_policy == "host_enforced":
                    # Called only after all staged documents have been promoted;
                    # ranking must hold for the complete candidate corpus.
                    promoter._verify_contracts(connection, metadata)
                result[relative] = {"artifact_id": artifact_id, "revision": metadata["revision"], "content_sha256": document.content_sha256}
        except WorkshopError:
            raise
        except (OSError, ValueError, sqlite3.Error, promoter.PromotionError) as exc:
            raise WorkshopError("CONTEXT_PROJECTION_MISMATCH", "context projection could not be verified", details=str(exc)) from exc
        finally:
            connection.close()
        return result
