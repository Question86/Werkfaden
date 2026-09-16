from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tomllib
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .blueprint import (
    advance_document_revision,
    cpp_token_signature,
    parse_blueprint,
    parse_blueprint_text,
    regenerate_mechanical_layers,
    semantic_document_hash,
    verify_ledger,
)
from .corpus import (
    CorpusEntry,
    WorkshopConfig,
    GRAPH_PROJECTIONS,
    blueprint_inventory,
    build_corpus_manifest,
    canonical_include_edges,
    direct_include_edges,
    header_ownership,
    include_topology_sha256,
    load_config,
    parsed_sections,
    verify_database_projection,
    verify_project_intake_binding,
)
from .scientific import ScientificParityGate, transaction_work_manifest
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    copy_exact,
    package_hash,
    read_json,
    sha256_bytes,
    sha256_text,
    sqlite_snapshot,
    utc_now,
)


TERMINAL_STATES = {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK"}
PRE_APPLY_STATES = {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}


class WorkshopEngine:
    def __init__(self, config_path: Path) -> None:
        self.config = load_config(config_path)
        self.scientific_gate = ScientificParityGate(self.config)
        self.config.state_directory.mkdir(parents=True, exist_ok=True)
        self.config.transaction_directory.mkdir(parents=True, exist_ok=True)
        (self.config.state_directory / "objects").mkdir(parents=True, exist_ok=True)
        (self.config.state_directory / "receipts").mkdir(parents=True, exist_ok=True)

    @property
    def lease_path(self) -> Path:
        return self.config.state_directory / "lease.json"

    @property
    def seal_path(self) -> Path:
        return self.config.state_directory / "seal.json"

    def _write_receipt(self, kind: str, payload: dict[str, Any]) -> Path:
        stamp = utc_now().replace("-", "").replace(":", "")
        receipt_id = f"{kind.upper()}_{sha256_bytes(canonical_json(payload).encode('utf-8'))[:16]}_{stamp}"
        destination = self.config.state_directory / "receipts" / f"{receipt_id}.json"
        atomic_write_json(destination, {"receipt_id": receipt_id, **payload})
        return destination

    def status(self, *, persist: bool = True) -> dict[str, Any]:
        manifest = build_corpus_manifest(self.config)
        scientific_preflight = self.scientific_gate.preflight()
        lease = read_json(self.lease_path) if self.lease_path.is_file() else None
        seal = read_json(self.seal_path) if self.seal_path.is_file() else None
        result = {
            "schema": "runtime-sync-status/v1",
            "created_at": utc_now(),
            "verified": manifest["verified"],
            "package_sha256": manifest["package_sha256"],
            "counts": manifest["counts"],
            "issue_counts": dict(sorted(Counter(issue["code"] for issue in manifest["issues"]).items())),
            "issues": manifest["issues"],
            "scientific_parity": scientific_preflight,
            "lease": lease,
            "seal": {
                "package_sha256": seal.get("package_sha256"),
                "created_at": seal.get("created_at"),
                "matches_current": seal.get("package_sha256") == manifest["package_sha256"],
            } if isinstance(seal, dict) else None,
        }
        if persist:
            path = self._write_receipt("status", result)
            result["receipt"] = str(path)
        return result

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise WorkshopError("OBJECT_HASH_INVALID", f"invalid object digest: {digest}")
        return self.config.state_directory / "objects" / digest[:2] / digest[2:]

    def _store_object(self, raw: bytes) -> str:
        digest = sha256_bytes(raw)
        target = self._object_path(digest)
        if target.is_file():
            if sha256_bytes(target.read_bytes()) != digest:
                raise WorkshopError("OBJECT_STORE_CORRUPT", f"object hash mismatch: {target}")
            return digest
        atomic_write_bytes(target, raw)
        return digest

    def _mirror(self, manifest: dict[str, Any]) -> dict[str, Any]:
        objects: dict[str, str] = {}
        for relative in manifest["authority"].get("dependent_tests", {}):
            objects[f"dependent-test:{relative}"] = self._store_object(
                (self.config.codebase_root / relative).read_bytes()
            )
        for relative, record in manifest["records"].items():
            source = record.get("source")
            if source:
                source_path = Path(str(source["path"]))
                objects[f"source:{relative}"] = self._store_object(source_path.read_bytes())
            header = record.get("header")
            if header:
                header_path = Path(str(header["path"]))
                objects[f"header:{header_path.as_posix()}"] = self._store_object(header_path.read_bytes())
            name = record["blueprint"]["filename"]
            objects[f"blueprint:{name}"] = self._store_object((self.config.blueprint_root / name).read_bytes())
            objects[f"managed:{name}"] = self._store_object((self.config.managed_blueprint_root / name).read_bytes())
        objects["authority:CMakeLists.txt"] = self._store_object(self.config.cmake_file.read_bytes())
        objects["authority:DATAFLOW_INDEX.md"] = self._store_object(self.config.dataflow_index.read_bytes())
        for filename in self.config.auxiliary_documents:
            objects[f"auxiliary:{filename}"] = self._store_object(
                (self.config.blueprint_root / filename).read_bytes()
            )
        for relative in self.config.machine_authority_files:
            objects[f"machine-authority:{relative}"] = self._store_object(
                (self.config.machine_root / relative).read_bytes()
            )
        snapshot = self.config.state_directory / f".snapshot-{manifest['package_sha256'][:16]}-{os.getpid()}.db"
        snapshot.unlink(missing_ok=True)
        try:
            sqlite_snapshot(self.config.kairos_database, snapshot)
            objects["database:kairos.db"] = self._store_object(snapshot.read_bytes())
        finally:
            snapshot.unlink(missing_ok=True)
        mirror = {
            "schema": "runtime-sync-mirror/v1",
            "created_at": utc_now(),
            "package_sha256": manifest["package_sha256"],
            "objects": dict(sorted(objects.items())),
        }
        mirror["mirror_sha256"] = package_hash({"package_sha256": mirror["package_sha256"], "objects": mirror["objects"]})
        destination = self.config.state_directory / "mirrors" / f"{manifest['package_sha256']}.json"
        atomic_write_json(destination, mirror)
        return mirror

    def seal(self) -> dict[str, Any]:
        if self.lease_path.exists():
            raise WorkshopError("LEASE_ACTIVE", "cannot seal while a transaction holds the global workshop lease", details=read_json(self.lease_path))
        manifest = build_corpus_manifest(self.config)
        if not manifest["verified"]:
            raise WorkshopError("CORPUS_NOT_VERIFIED", "the corpus cannot be sealed while synchronization issues exist", details=manifest["issues"])
        scientific_preflight = self.scientific_gate.preflight()
        mirror = self._mirror(manifest)
        seal = {
            "schema": "runtime-sync-seal/v1",
            "created_at": utc_now(),
            "package_sha256": manifest["package_sha256"],
            "mirror_sha256": mirror["mirror_sha256"],
            "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{manifest['package_sha256']}.json"),
            "counts": manifest["counts"],
            "scientific_parity": scientific_preflight,
        }
        atomic_write_json(self.seal_path, seal)
        self._write_receipt("seal", seal)
        return seal

    def _acquire_lease(self, transaction_id: str) -> dict[str, Any]:
        lease = {
            "schema": "runtime-sync-lease/v1",
            "transaction_id": transaction_id,
            "process_id": os.getpid(),
            "acquired_at": utc_now(),
            "state": "ACTIVE",
        }
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise WorkshopError("LEASE_ACTIVE", "another checkout already owns the global workshop lease", details=read_json(self.lease_path)) from exc
        try:
            os.write(descriptor, (json.dumps(lease, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return lease

    def _release_lease(self, transaction_id: str) -> None:
        if not self.lease_path.is_file():
            raise WorkshopError("LEASE_MISSING", "workshop lease is missing")
        lease = read_json(self.lease_path)
        if lease.get("transaction_id") != transaction_id:
            raise WorkshopError("LEASE_OWNER_MISMATCH", f"lease belongs to {lease.get('transaction_id')}, not {transaction_id}")
        self.lease_path.unlink()

    def _transaction_path(self, transaction_id: str) -> Path:
        if not re_fullmatch_transaction(transaction_id):
            raise WorkshopError("TRANSACTION_ID_INVALID", f"invalid transaction id: {transaction_id}")
        path = (self.config.transaction_directory / transaction_id).resolve()
        try:
            path.relative_to(self.config.transaction_directory)
        except ValueError as exc:
            raise WorkshopError("PATH_ESCAPE", f"transaction escapes transaction root: {transaction_id}") from exc
        return path

    def _load_transaction(self, transaction_id: str) -> tuple[Path, dict[str, Any]]:
        root = self._transaction_path(transaction_id)
        state_path = root / "state.json"
        if not state_path.is_file():
            raise WorkshopError("TRANSACTION_MISSING", f"transaction does not exist: {transaction_id}")
        return root, read_json(state_path)

    def _save_transaction(self, root: Path, state: dict[str, Any], *, event: str, details: Any = None) -> None:
        state["updated_at"] = utc_now()
        state.setdefault("journal", []).append({"event": event, "at": state["updated_at"], "details": details})
        atomic_write_json(root / "state.json", state)

    def _require_lease(self, transaction_id: str) -> None:
        if not self.lease_path.is_file():
            raise WorkshopError("LEASE_MISSING", f"transaction {transaction_id} has no active lease")
        lease = read_json(self.lease_path)
        if lease.get("transaction_id") != transaction_id:
            raise WorkshopError("LEASE_OWNER_MISMATCH", f"lease belongs to {lease.get('transaction_id')}")

    def checkout(self, sources: Iterable[str], *, purpose: str, tests: Iterable[str] = ()) -> dict[str, Any]:
        normalized_sources = sorted({str(value).replace("\\", "/") for value in sources})
        if not normalized_sources:
            raise WorkshopError("CHECKOUT_SCOPE_EMPTY", "checkout requires at least one --source")
        if len(purpose.strip()) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "checkout purpose must contain at least 12 characters")
        if not self.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "run seal after a fully verified status before checkout")
        seal = read_json(self.seal_path)
        manifest = build_corpus_manifest(self.config)
        if not manifest["verified"]:
            raise WorkshopError("CORPUS_NOT_VERIFIED", "checkout is blocked by corpus drift", details=manifest["issues"])
        if seal.get("package_sha256") != manifest["package_sha256"]:
            raise WorkshopError("SEAL_DRIFT", "live corpus differs from the trusted seal; inspect status and reseal only after resolution")
        missing = sorted(set(normalized_sources) - set(manifest["records"]))
        if missing:
            raise WorkshopError(
                "CHECKOUT_SOURCE_UNKNOWN",
                "checkout source is not in the configured translation-unit authority",
                details=missing,
            )
        selected_tests = sorted(set(tests))
        admitted_tests = manifest["authority"].get("dependent_tests", {})
        for relative in selected_tests:
            if relative not in admitted_tests or admitted_tests[relative]["owner"] not in normalized_sources:
                raise WorkshopError("CHECKOUT_TEST_NOT_ADMITTED", f"test requires its configured selected Runtime owner: {relative}")
        created_at = utc_now()
        transaction_id = "TXN_" + package_hash({
            "baseline": manifest["package_sha256"],
            "sources": normalized_sources,
            "dependent_tests": selected_tests,
            "purpose": purpose.strip(),
            "created_at": created_at,
        })[:24]
        root = self._transaction_path(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self._acquire_lease(transaction_id)
        try:
            (root / "baseline" / "runtime").mkdir(parents=True)
            (root / "baseline" / "blueprints").mkdir(parents=True)
            (root / "baseline" / "managed").mkdir(parents=True)
            (root / "work").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            atomic_write_json(root / "baseline" / "manifest.json", manifest)
            sqlite_snapshot(self.config.kairos_database, root / "baseline" / "kairos.db")
            review_entries: list[dict[str, Any]] = []
            for relative in selected_tests:
                copy_exact(self.config.codebase_root / relative, root / "baseline" / relative)
                copy_exact(self.config.codebase_root / relative, root / "work" / relative)
                review_entries.append({
                    "source": relative, "metadata_impact": "pending",
                    "reason": "", "reviewer": "", "reviewed_at": "",
                })
            for source in normalized_sources:
                record = manifest["records"][source]
                source_live = self.config.codebase_root / source
                source_baseline = root / "baseline" / source
                source_work = root / "work" / source
                copy_exact(source_live, source_baseline)
                copy_exact(source_live, source_work)
                header = record.get("header")
                if header:
                    header_live = Path(header["path"])
                    header_relative = header_live.resolve().relative_to(self.config.codebase_root).as_posix()
                    copy_exact(header_live, root / "baseline" / header_relative)
                    copy_exact(header_live, root / "work" / header_relative)
                name = record["blueprint"]["filename"]
                copy_exact(self.config.blueprint_root / name, root / "baseline" / "blueprints" / name)
                copy_exact(self.config.blueprint_root / name, root / "work" / "blueprints" / name)
                copy_exact(self.config.managed_blueprint_root / name, root / "baseline" / "managed" / name)
                copy_exact(self.config.managed_blueprint_root / name, root / "work" / "managed" / name)
                review_entries.append({
                    "source": source,
                    "metadata_impact": "pending",
                    "reason": "",
                    "reviewer": "",
                    "reviewed_at": "",
                })
            review = {
                "schema": "runtime-sync-metadata-review/v1",
                "transaction_id": transaction_id,
                "entries": review_entries,
            }
            atomic_write_json(root / "review" / "metadata_review.json", review)
            state = {
                "schema": "runtime-sync-transaction/v1",
                "transaction_id": transaction_id,
                "state": "CHECKED_OUT",
                "created_at": created_at,
                "updated_at": created_at,
                "deterministic_updated_at": created_at,
                "purpose": purpose.strip(),
                "sources": normalized_sources,
                "dependent_tests": selected_tests,
                "baseline_package_sha256": manifest["package_sha256"],
                "journal": [],
            }
            self._save_transaction(root, state, event="CHECKOUT_COMPLETED", details={"sources": normalized_sources})
        except Exception:
            if self.lease_path.exists():
                self._release_lease(transaction_id)
            raise
        return {
            "schema": "runtime-sync-checkout/v1",
            "transaction_id": transaction_id,
            "state": "CHECKED_OUT",
            "work_directory": str(root / "work"),
            "metadata_review": str(root / "review" / "metadata_review.json"),
            "sources": normalized_sources,
            "dependent_tests": selected_tests,
        }

    def _assert_live_baseline(self, state: dict[str, Any]) -> dict[str, Any]:
        current = build_corpus_manifest(self.config)
        if not current["verified"]:
            raise WorkshopError("LIVE_CORPUS_DRIFT", "live corpus failed verification while a transaction is open", details=current["issues"])
        if current["package_sha256"] != state["baseline_package_sha256"]:
            raise WorkshopError("LIVE_CORPUS_DRIFT", "live corpus package changed after checkout", details={"baseline": state["baseline_package_sha256"], "current": current["package_sha256"]})
        return current

    def _test_changes(self, root: Path, state: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
        admitted = manifest["authority"].get("dependent_tests", {})
        rows = []
        for relative in state.get("dependent_tests", []):
            if relative not in admitted or admitted[relative]["owner"] not in state["sources"]:
                raise WorkshopError("CHECKOUT_TEST_NOT_ADMITTED", f"test scope no longer admitted: {relative}")
            work = root / "work" / relative
            baseline = root / "baseline" / relative
            # Work paths may not redirect apply outside the transaction.
            for path in (work, baseline):
                current = path
                while current != root:
                    if current.is_symlink() or getattr(current.lstat(), "st_file_attributes", 0) & 0x400:
                        raise WorkshopError("WORK_FILE_INVALID", f"linked test path: {current}")
                    current = current.parent
                if not path.is_file() or path.stat().st_nlink != 1:
                    raise WorkshopError("WORK_FILE_INVALID", f"test is not a single-link regular file: {path}")
            if sha256_bytes(baseline.read_bytes()) != admitted[relative]["sha256"]:
                raise WorkshopError("TEST_BASELINE_CHANGED", f"immutable test baseline changed: {relative}")
            rows.append({"source": relative, "owner": admitted[relative]["owner"],
                         "changed": work.read_bytes() != baseline.read_bytes()})
        return rows

    def _render_include_ownership_authority(
        self,
        source_text: str,
        owners: dict[str, set[str]],
        *,
        updated_at: str,
        include_edges: list[dict[str, Any]] | None = None,
        topology_sha256: str | None = None,
    ) -> str:
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        frontmatter = importlib.import_module("kairos.frontmatter")
        parsed = frontmatter.split_frontmatter(source_text)
        metadata = dict(parsed.metadata)
        metadata["revision"] = int(metadata["revision"]) + 1
        metadata["updated_at"] = updated_at
        authority = self.config.raw.get("source_authority") or {}

        def replace_rows(body: str, section_id: str, rows: list[str]) -> str:
            anchor = f'<a id="{section_id}"></a>'
            start = body.find(anchor)
            if start < 0:
                raise WorkshopError("AUTHORITY_TOPOLOGY_INVALID", f"authority section is missing: {section_id}")
            next_anchor = body.find('<a id="', start + len(anchor))
            end = len(body) if next_anchor < 0 else next_anchor
            section = body[start:end]
            prefix_match = re.match(
                rf'(?s)(<a id="{re.escape(section_id)}"></a>\n##[^\n]*\n\n> Capsule:[^\n]*\n\n)',
                section,
            )
            if not prefix_match:
                raise WorkshopError("AUTHORITY_TOPOLOGY_INVALID", f"authority section lacks canonical heading/capsule: {section_id}")
            replacement = prefix_match.group(1) + "\n".join(rows) + "\n\n"
            return body[:start] + replacement + body[end:]

        section_id = str(authority.get("include_ownership_section_id", ""))
        if not section_id:
            raise WorkshopError("AUTHORITY_TOPOLOGY_UNCONFIGURED", "include ownership authority section is not configured")
        owner_rows = [
            f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in sorted(header_owners))
            for header, header_owners in sorted(owners.items())
        ] or ["- none"]
        body = replace_rows(parsed.body, section_id, owner_rows)
        if include_edges is not None:
            edge_section = str(authority.get("include_edges_section_id", ""))
            if not edge_section or not topology_sha256:
                raise WorkshopError("AUTHORITY_TOPOLOGY_UNCONFIGURED", "direct include-edge authority section/digest is not configured")
            canonical = canonical_include_edges(include_edges)
            edge_rows = [
                f"Edge count: `{len(canonical)}`",
                f"Topology SHA-256: `{topology_sha256}`",
                "",
                "Exact compiler-observed rows are retained in machine authority and the SQLite projection; this index binds them by count and digest.",
            ]
            body = replace_rows(body, edge_section, edge_rows)
        return frontmatter.render_frontmatter(metadata) + body


    def _render_header_ownership_blueprint(
        self,
        source_text: str,
        owners: set[str],
        *,
        updated_at: str,
        advance_revision: bool,
        path: Path,
    ) -> str:
        document = parse_blueprint_text(path, source_text)
        span = document.sections.get("s-ownership")
        if span is None:
            raise WorkshopError(
                "AUTHORITY_TOPOLOGY_INVALID",
                f"header blueprint lacks s-ownership: {path.name}",
            )
        section = document.text[span.start:span.end]
        prefix_match = re.match(
            r'(?s)(<a id="s-ownership"></a>\n##[^\n]*\n\n> Capsule:[^\n]*\n\n)',
            section,
        )
        if not prefix_match:
            raise WorkshopError(
                "AUTHORITY_TOPOLOGY_INVALID",
                f"header ownership section lacks the canonical heading/capsule shape: {path.name}",
            )
        rows = [f"- `{owner}`" for owner in sorted(owners)] or ["- none found in the static include closure"]
        replacement = prefix_match.group(1) + "\n".join(rows) + "\n\n"
        text = document.text[:span.start] + replacement + document.text[span.end:]
        reparsed = parse_blueprint_text(path, text, document.newline)
        if advance_revision:
            return advance_document_revision(
                reparsed,
                revision=document.revision + 1,
                updated_at=updated_at,
            )
        # A mechanically regenerated selected header already advanced exactly once.
        # Ownership is another derived section of the same logical change, not a
        # second document revision. Keep its revision and timestamp unchanged.
        return text.replace("\n", document.newline) if document.newline != "\n" else text

    def _prepare_topology_authority(
        self,
        root: Path,
        state: dict[str, Any],
        baseline_manifest: dict[str, Any],
        *,
        stage: bool = True,
    ) -> dict[str, Any] | None:
        authority = self.config.raw.get("source_authority") or {}
        if not authority.get("include_ownership_section_id"):
            state["authority_documents"] = []
            return None
        overrides: dict[str, Path] = {}
        for key in state["sources"]:
            record = baseline_manifest["records"][key]
            if record.get("source"):
                overrides[key] = root / "work" / key
            if record.get("header"):
                header_live = Path(str(record["header"]["path"]))
                header_relative = header_live.resolve().relative_to(self.config.codebase_root).as_posix()
                overrides[header_relative] = root / "work" / header_relative
        owners, issues = header_ownership(
            self.config,
            baseline_manifest["authority"]["translation_units"],
            overrides=overrides,
        )
        edge_file = authority.get("include_edges_file")
        work_edges: list[dict[str, Any]] | None = None
        baseline_edges: list[dict[str, Any]] = []
        edge_changed = False
        if edge_file:
            work_edges, edge_issues = direct_include_edges(
                self.config,
                baseline_manifest["authority"]["translation_units"],
                overrides=overrides,
            )
            issues.extend(edge_issues)
            baseline_edges = canonical_include_edges(baseline_manifest["authority"].get("include_edges", []))
            edge_changed = work_edges != baseline_edges
        if issues:
            raise WorkshopError(
                "AUTHORITY_TOPOLOGY_UNVERIFIED",
                "transaction work tree has an include graph that cannot be proven",
                details=issues,
            )
        baseline_owners = {
            str(header): set(value)
            for header, value in baseline_manifest["authority"].get("include_owners", {}).items()
        }
        baseline_headers = set(baseline_owners)
        current_headers = set(owners)
        if current_headers != baseline_headers:
            raise WorkshopError(
                "AUTHORITY_TOPOLOGY_MIGRATION_REQUIRED",
                "transaction changes the governed header set; use an explicit authority migration before apply",
                details={
                    "added_headers": sorted(current_headers - baseline_headers),
                    "removed_headers": sorted(baseline_headers - current_headers),
                },
            )
        if owners == baseline_owners and not edge_changed:
            if stage:
                state["authority_documents"] = []
                state["topology_blueprints"] = []
            return None
        if not stage:
            return {
                "baseline_owners": {key: sorted(value) for key, value in sorted(baseline_owners.items())},
                "work_owners": {key: sorted(value) for key, value in sorted(owners.items())},
            }
        changed_header_owners = sorted(
            header for header in baseline_headers
            if owners.get(header, set()) != baseline_owners.get(header, set())
        )
        topology_blueprints: list[str] = []
        for header in changed_header_owners:
            record = baseline_manifest["records"].get(header)
            if not isinstance(record, dict) or record.get("kind") != "header_only":
                continue
            blueprint = record.get("blueprint") or {}
            name = str(blueprint.get("filename", ""))
            if not name:
                raise WorkshopError("AUTHORITY_TOPOLOGY_INVALID", f"header record has no blueprint: {header}")
            baseline_blueprint = root / "baseline" / "blueprints" / name
            baseline_managed = root / "baseline" / "managed" / name
            if not baseline_blueprint.is_file():
                copy_exact(self.config.blueprint_root / name, baseline_blueprint)
            if not baseline_managed.is_file():
                copy_exact(self.config.managed_blueprint_root / name, baseline_managed)
            work_blueprint = root / "work" / "blueprints" / name
            work_managed = root / "work" / "managed" / name
            source_path = work_blueprint if work_blueprint.is_file() else baseline_blueprint
            source_text = source_path.read_text(encoding="utf-8")
            baseline_document = parse_blueprint(baseline_blueprint)
            current_document = parse_blueprint_text(source_path, source_text)
            already_advanced = current_document.revision == baseline_document.revision + 1
            if current_document.revision not in {baseline_document.revision, baseline_document.revision + 1}:
                raise WorkshopError(
                    "BLUEPRINT_REVISION_INVALID",
                    f"topology blueprint has unexpected work revision: {name}",
                )
            rendered_header = self._render_header_ownership_blueprint(
                source_text,
                owners.get(header, set()),
                updated_at=state["deterministic_updated_at"],
                advance_revision=not already_advanced,
                path=source_path,
            )
            atomic_write_bytes(work_blueprint, rendered_header.encode("utf-8"))
            atomic_write_bytes(work_managed, rendered_header.encode("utf-8"))
            topology_blueprints.append(header)
        state["topology_blueprints"] = topology_blueprints
        try:
            relative = self.config.dataflow_index.resolve().relative_to(self.config.kairos_workspace.resolve()).as_posix()
        except ValueError as exc:
            raise WorkshopError(
                "AUTHORITY_TOPOLOGY_UNCONFIGURED",
                "include ownership drift requires a KAIROS-managed dataflow index",
            ) from exc
        baseline_authority = root / "baseline" / "authority" / relative
        work_authority = root / "work" / "authority" / relative
        if not baseline_authority.is_file():
            copy_exact(self.config.dataflow_index, baseline_authority)
        rendered = self._render_include_ownership_authority(
            baseline_authority.read_text(encoding="utf-8"),
            owners,
            updated_at=state["deterministic_updated_at"],
            include_edges=work_edges,
            topology_sha256=include_topology_sha256(work_edges or []) if work_edges is not None else None,
        )
        atomic_write_bytes(work_authority, rendered.encode("utf-8"))
        state["authority_documents"] = [relative]
        if work_edges is not None and isinstance(edge_file, str):
            payload = {
                "schema": "kairos-compiler-include-edges/v1",
                "edge_count": len(work_edges),
                "topology_sha256": include_topology_sha256(work_edges),
                "claim_boundary": "Direct compiler-observed include consumers are distinct from byte ownership and transitive compiled-root reachability.",
                "edges": canonical_include_edges(work_edges),
            }
            atomic_write_json(root / "work" / "machine" / edge_file, payload)
            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])) | {edge_file})
            state["include_edge_authority"] = {
                "path": edge_file,
                "edge_count": payload["edge_count"],
                "topology_sha256": payload["topology_sha256"],
            }
        return {
            "document": relative,
            "baseline_owners": {key: sorted(value) for key, value in sorted(baseline_owners.items())},
            "work_owners": {key: sorted(value) for key, value in sorted(owners.items())},
            "updated_header_blueprints": topology_blueprints,
            "direct_edge_changed": edge_changed,
            "baseline_topology_sha256": include_topology_sha256(baseline_edges) if work_edges is not None else None,
            "work_topology_sha256": include_topology_sha256(work_edges or []) if work_edges is not None else None,
        }

    @staticmethod
    def _intake_fact(*, live_path: Path, staged_path: Path) -> dict[str, Any]:
        raw = staged_path.read_bytes()
        return {
            "path": live_path.as_posix(),
            "sha256": sha256_bytes(raw),
            "bytes": len(raw),
        }

    def _stage_project_intake_authority(
        self,
        root: Path,
        state: dict[str, Any],
        baseline_manifest: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Stage the current project-intake binding for an ordinary transaction.

        A project intake is machine authority, not a one-time historical receipt. When
        governed source/header bytes or include ownership change, its exact byte facts
        and ownership claims must advance in the same transaction. Otherwise the
        package could contain individually hashed files that contradict one another.
        """
        live_path = self.config.machine_root / "project-intake.json"
        if not live_path.is_file():
            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])))
            return None
        intake = read_json(live_path)
        if not isinstance(intake, dict) or intake.get("schema") != "kairos-project-intake/v1":
            raise WorkshopError(
                "PROJECT_INTAKE_INVALID",
                "ordinary Workshop synchronization requires a valid project-intake.json when one is present",
            )
        candidate = json.loads(json.dumps(intake))
        changes = {str(row["source"]): row for row in state.get("changes", [])}
        changed_fields: list[str] = []

        unit_rows = {
            str(row.get("relative_path", "")).replace("\\", "/"): row
            for row in candidate.get("translation_units", [])
            if isinstance(row, dict) and isinstance(row.get("relative_path"), str)
        }
        changed_header_paths: set[str] = set()
        for relative, change in changes.items():
            record = baseline_manifest["records"].get(relative) or {}
            if change.get("source_changed") and record.get("source"):
                row = unit_rows.get(relative)
                if not isinstance(row, dict):
                    raise WorkshopError(
                        "PROJECT_INTAKE_SOURCE_FACT_MISSING",
                        f"project intake has no translation-unit row for changed source: {relative}",
                    )
                row["facts"] = self._intake_fact(
                    live_path=self.config.codebase_root / relative,
                    staged_path=root / "work" / relative,
                )
                changed_fields.append(f"source:{relative}")
            if change.get("header_changed") and isinstance(record.get("header"), dict):
                header_live = Path(str(record["header"]["path"]))
                header_relative = header_live.resolve().relative_to(self.config.codebase_root.resolve()).as_posix()
                changed_header_paths.add(header_relative)

        topology = state.get("topology_authority")
        if isinstance(topology, dict):
            owners = {
                str(key): sorted(str(value) for value in values)
                for key, values in (topology.get("work_owners") or {}).items()
            }
        else:
            owners = {
                str(key): sorted(str(value) for value in values)
                for key, values in baseline_manifest["authority"].get("include_owners", {}).items()
            }

        closure = candidate.get("include_closure")
        if not isinstance(closure, dict):
            raise WorkshopError(
                "PROJECT_INTAKE_INCLUDE_CLOSURE_INVALID",
                "project intake include_closure must be an object",
            )
        for header, header_owners in owners.items():
            row = closure.get(header)
            if not isinstance(row, dict):
                raise WorkshopError(
                    "PROJECT_INTAKE_HEADER_FACT_MISSING",
                    f"project intake has no include-closure row for governed header: {header}",
                )
            normalized_owners = sorted(header_owners)
            current_owners = sorted(str(value) for value in row.get("owners", [])) if isinstance(row.get("owners"), list) else []
            if current_owners != normalized_owners:
                row["owners"] = normalized_owners
                changed_fields.append(f"owners:{header}")
            if header in changed_header_paths:
                row["facts"] = self._intake_fact(
                    live_path=self.config.codebase_root / header,
                    staged_path=root / "work" / header,
                )
                changed_fields.append(f"header:{header}")

        edge_stage = state.get("include_edge_authority")
        if isinstance(edge_stage, dict) and isinstance(candidate.get("include_edge_topology"), dict):
            candidate["include_edge_topology"] = {
                "edge_count": int(edge_stage["edge_count"]),
                "topology_sha256": str(edge_stage["topology_sha256"]),
            }
            changed_fields.append("include_edges")

        if not changed_fields:
            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])))
            return None

        baseline_machine = root / "baseline" / "machine" / "project-intake.json"
        work_machine = root / "work" / "machine" / "project-intake.json"
        copy_exact(live_path, baseline_machine)
        atomic_write_json(work_machine, candidate)
        state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])) | {"project-intake.json"})

        prospective_records = json.loads(json.dumps(baseline_manifest["records"]))
        for relative, change in changes.items():
            record = prospective_records.get(relative)
            if not isinstance(record, dict):
                continue
            if change.get("source_changed") and record.get("source"):
                raw = (root / "work" / relative).read_bytes()
                record["source"]["sha256"] = sha256_bytes(raw)
                record["source"]["byte_count"] = len(raw)
            if change.get("header_changed") and isinstance(record.get("header"), dict):
                header_live = Path(str(record["header"]["path"]))
                header_relative = header_live.resolve().relative_to(self.config.codebase_root.resolve()).as_posix()
                raw = (root / "work" / header_relative).read_bytes()
                record["header"]["sha256"] = sha256_bytes(raw)
                record["header"]["byte_count"] = len(raw)

        binding_issues = verify_project_intake_binding(
            self.config,
            translation_units=baseline_manifest["authority"]["translation_units"],
            include_owners={key: set(values) for key, values in owners.items()},
            records=prospective_records,
            intake_path=work_machine,
        )
        if binding_issues:
            raise WorkshopError(
                "PROJECT_INTAKE_STAGE_INVALID",
                "staged project intake does not describe the exact transaction result",
                details=binding_issues,
            )
        return {
            "path": "project-intake.json",
            "changed_bindings": sorted(set(changed_fields)),
            "sha256": sha256_bytes(work_machine.read_bytes()),
        }

    def _review_map(self, root: Path, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        review = read_json(root / "review" / "metadata_review.json")
        if review.get("schema") != "runtime-sync-metadata-review/v1" or review.get("transaction_id") != state["transaction_id"]:
            raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review schema or transaction binding is invalid")
        entries = review.get("entries")
        if not isinstance(entries, list):
            raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review entries must be an array")
        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                raise WorkshopError("METADATA_REVIEW_INVALID", "metadata review entry has no source")
            source = entry["source"].replace("\\", "/")
            if source in result:
                raise WorkshopError("METADATA_REVIEW_INVALID", f"duplicate metadata review entry: {source}")
            result[source] = entry
        return result

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"prepare is not allowed from {state['state']}")
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        self._assert_live_baseline(state)
        reviews = self._review_map(root, state)
        review_blockers: list[dict[str, Any]] = []
        change_rows: list[dict[str, Any]] = []
        generated: dict[str, bytes] = {}
        for source in state["sources"]:
            record = baseline_manifest["records"][source]
            name = record["blueprint"]["filename"]
            baseline_source = root / "baseline" / source
            work_source = root / "work" / source
            if not work_source.is_file():
                raise WorkshopError("WORK_FILE_MISSING", f"checked-out source is missing: {work_source}")
            baseline_header: Path | None = None
            work_header: Path | None = None
            if record.get("header"):
                live_header = Path(record["header"]["path"])
                header_relative = live_header.resolve().relative_to(self.config.codebase_root).as_posix()
                baseline_header = root / "baseline" / header_relative
                work_header = root / "work" / header_relative
                if not work_header.is_file():
                    raise WorkshopError("WORK_FILE_MISSING", f"checked-out header is missing: {work_header}")
            baseline_blueprint = parse_blueprint(root / "baseline" / "blueprints" / name)
            work_blueprint_path = root / "work" / "blueprints" / name
            if not work_blueprint_path.is_file():
                raise WorkshopError("WORK_FILE_MISSING", f"checked-out blueprint is missing: {work_blueprint_path}")
            work_blueprint = parse_blueprint(work_blueprint_path)
            if (
                work_blueprint.source_path != baseline_blueprint.source_path
                or work_blueprint.header_path != baseline_blueprint.header_path
            ):
                raise WorkshopError("MAPPING_CHANGE_FORBIDDEN", f"source/header mapping cannot change inside an existing transaction: {name}")
            mapped_work_source = work_source if work_blueprint.source_path is not None else None
            source_changed = bool(
                mapped_work_source
                and baseline_source.read_bytes() != mapped_work_source.read_bytes()
            )
            header_changed = bool(baseline_header and work_header and baseline_header.read_bytes() != work_header.read_bytes())
            semantic_changed = semantic_document_hash(baseline_blueprint) != semantic_document_hash(work_blueprint)
            code_changed = source_changed or header_changed
            ecosystem_map = self.config.raw.get("source_ecosystems") or {}
            ecosystem = str(ecosystem_map.get(source, "c_family")) if isinstance(ecosystem_map, dict) else "c_family"
            if ecosystem == "c_family":
                token_changed = bool(
                    mapped_work_source
                    and cpp_token_signature(baseline_source.read_bytes())
                    != cpp_token_signature(mapped_work_source.read_bytes())
                )
                if baseline_header and work_header:
                    token_changed = token_changed or cpp_token_signature(baseline_header.read_bytes()) != cpp_token_signature(work_header.read_bytes())
            else:
                # Universal static blueprints intentionally make no semantic claim
                # beyond exact bytes/ledger. A language-agnostic C++ lexer must not
                # manufacture a semantic-metadata obligation for Python/Rust/JS/etc.
                # Their exact mechanical mirror still advances on every byte change.
                token_changed = False
            changed = code_changed or semantic_changed
            review = reviews.get(source)
            if changed:
                if not review or review.get("metadata_impact") not in {"updated", "none"}:
                    review_blockers.append({"source": source, "reason": "metadata_impact must be updated or none"})
                else:
                    reason = str(review.get("reason", "")).strip()
                    reviewer = str(review.get("reviewer", "")).strip()
                    reviewed_at = str(review.get("reviewed_at", "")).strip()
                    if len(reason) < 12 or not reviewer or not reviewed_at:
                        review_blockers.append({"source": source, "reason": "review reason, reviewer, and reviewed_at are required"})
                    if token_changed and review.get("metadata_impact") != "updated":
                        review_blockers.append({"source": source, "reason": "C/C++ token stream changed; semantic metadata update is mandatory"})
                    if review.get("metadata_impact") == "updated" and not semantic_changed:
                        review_blockers.append({"source": source, "reason": "metadata_impact=updated but no non-mechanical blueprint content changed"})
                    if review.get("metadata_impact") == "none" and semantic_changed:
                        review_blockers.append({"source": source, "reason": "metadata_impact=none conflicts with changed semantic blueprint content"})
            change_rows.append({
                "source": source,
                "source_changed": source_changed,
                "header_changed": header_changed,
                "token_stream_changed": token_changed,
                "semantic_blueprint_changed": semantic_changed,
                "changed": changed,
            })
            if changed:
                if code_changed:
                    rendered = regenerate_mechanical_layers(
                        work_blueprint,
                        mapped_work_source,
                        work_header,
                        revision=baseline_blueprint.revision + 1,
                        updated_at=state["deterministic_updated_at"],
                    )
                else:
                    rendered = advance_document_revision(
                        work_blueprint,
                        revision=baseline_blueprint.revision + 1,
                        updated_at=state["deterministic_updated_at"],
                    )
                generated[source] = rendered.encode("utf-8")
        test_changes = self._test_changes(root, state, baseline_manifest)
        for row in test_changes:
            if not row["changed"]:
                continue
            review = reviews.get(row["source"], {})
            if (review.get("metadata_impact") != "updated"
                    or len(str(review.get("reason", "")).strip()) < 12
                    or not str(review.get("reviewer", "")).strip()
                    or not str(review.get("reviewed_at", "")).strip()):
                review_blockers.append({"source": row["source"], "reason": "test migration requires explicit updated review"})
            if not any(change["source"] == row["owner"] and change["token_stream_changed"] for change in change_rows):
                raise WorkshopError("TEST_OWNER_UNCHANGED", "test migration must accompany its changed Runtime interface")
        # Topology-set changes are a stronger authority boundary than metadata
        # review. Detect them before asking the operator to repair a review that
        # cannot make the transaction admissible. Staging happens only after
        # ordinary review succeeds.
        self._prepare_topology_authority(root, state, baseline_manifest, stage=False)
        if not any(row["changed"] for row in change_rows):
            raise WorkshopError("TRANSACTION_NO_CHANGES", "prepare found no source, header, or semantic blueprint changes")
        if review_blockers:
            state["state"] = "AWAITING_METADATA_REVIEW"
            self._save_transaction(root, state, event="METADATA_REVIEW_REQUIRED", details=review_blockers)
            raise WorkshopError("METADATA_REVIEW_REQUIRED", "prepare is blocked until metadata review is complete", details=review_blockers)
        for source, raw in generated.items():
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            atomic_write_bytes(root / "work" / "blueprints" / name, raw)
            atomic_write_bytes(root / "work" / "managed" / name, raw)
            document = parse_blueprint(root / "work" / "blueprints" / name)
            work_source = root / "work" / source
            mapped_work_source = work_source if document.source_path is not None else None
            work_header = None
            if baseline_manifest["records"][source].get("header"):
                header_live = Path(baseline_manifest["records"][source]["header"]["path"])
                work_header = root / "work" / header_live.resolve().relative_to(self.config.codebase_root)
            issues = verify_ledger(document, mapped_work_source, work_header)
            if issues:
                raise WorkshopError("GENERATED_LEDGER_INVALID", f"mechanical ledger postcheck failed for {name}", details=issues)
        state["changes"] = change_rows
        state["test_changes"] = test_changes
        topology_authority = self._prepare_topology_authority(root, state, baseline_manifest)
        state["topology_authority"] = topology_authority
        state["changed_sources"] = [row["source"] for row in change_rows if row["changed"]]
        state["prepared_blueprints"] = [baseline_manifest["records"][source]["blueprint"]["filename"] for source in generated]
        state["project_intake_authority"] = self._stage_project_intake_authority(
            root,
            state,
            baseline_manifest,
        )
        state["state"] = "PREPARED"
        self._save_transaction(root, state, event="PREPARE_COMPLETED", details=change_rows)
        return {"schema": "runtime-sync-prepare/v1", "transaction_id": transaction_id, "state": state["state"], "changes": change_rows}

    def _shadow_native(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        shadow = root / "shadow"
        if shadow.exists():
            shutil.rmtree(shadow)
        (shadow / ".kairos" / "events").mkdir(parents=True)
        (shadow / ".kairos" / "receipts").mkdir(parents=True)
        (shadow / "code").mkdir(parents=True)
        copy_exact(self.config.kairos_workspace / ".kairos" / "config.json", shadow / ".kairos" / "config.json")
        copy_exact(root / "baseline" / "kairos.db", shadow / ".kairos" / "kairos.db")
        # A native shadow is a document-validation workspace, not merely a code
        # directory. Changed implementation documents retain typed references to
        # tasks, method documents, source indexes and other authoritative sources;
        # those targets must exist in the shadow or a correct first project patch
        # fails for reasons unrelated to the proposed change. Copy authoritative
        # source documents only; derived state and receipts remain isolated.
        workspace_config = read_json(self.config.kairos_workspace / ".kairos" / "config.json")
        roots = workspace_config.get("document_roots", []) if isinstance(workspace_config, dict) else []
        for root_name in roots:
            source_root = self.config.kairos_workspace / str(root_name)
            if not source_root.is_dir():
                continue
            for source_path in source_root.rglob("*"):
                if source_path.is_file():
                    relative_path = source_path.relative_to(self.config.kairos_workspace)
                    copy_exact(source_path, shadow / relative_path)
        for source_path in (self.config.kairos_workspace / "goals").glob("*.json"):
            if source_path.is_file():
                copy_exact(source_path, shadow / source_path.relative_to(self.config.kairos_workspace))
        for name in workspace_config.get("canonical_files", []) if isinstance(workspace_config, dict) else []:
            source_path = self.config.kairos_workspace / str(name)
            if source_path.is_file():
                copy_exact(source_path, shadow / str(name))
        current_path = self.config.kairos_workspace / "current.json"
        if current_path.is_file():
            copy_exact(current_path, shadow / "current.json")
        for relative in state.get("authority_documents", []):
            copy_exact(root / "work" / "authority" / relative, shadow / relative)
        entries: list[CorpusEntry] = []
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            work = root / "work" / "blueprints" / name
            copy_exact(work, shadow / "code" / name)
        for header in state.get("topology_blueprints", []):
            name = baseline_manifest["records"][header]["blueprint"]["filename"]
            work = root / "work" / "blueprints" / name
            copy_exact(work, shadow / "code" / name)
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        database_module = importlib.import_module("kairos.database")
        promoter_module = importlib.import_module("kairos.promoter")
        database = database_module.KnowledgeDatabase(shadow / ".kairos" / "kairos.db")
        receipts: list[dict[str, Any]] = []
        for relative in state.get("authority_documents", []):
            receipt = promoter_module.promote_document(shadow / relative, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError(
                    "SHADOW_PROMOTION_FAILED",
                    f"shadow authority promotion was not verified: {relative}",
                    details=receipt,
                )
            receipts.append(receipt)
        inventory, inventory_issues = blueprint_inventory(self.config)
        if inventory_issues:
            raise WorkshopError("LIVE_INVENTORY_INVALID", "cannot construct shadow entries", details=inventory_issues)
        by_source = {entry.record_key: entry for entry in inventory}
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            shadow_document = parse_blueprint(shadow / "code" / name)
            live_entry = by_source[source]
            shadow_entry = replace(live_entry, blueprint_path=shadow / "code" / name, managed_path=shadow / "code" / name, document=shadow_document)
            receipt = promoter_module.promote_document(shadow_entry.managed_path, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"shadow promotion was not verified: {name}", details=receipt)
            receipts.append(receipt)
            entries.append(shadow_entry)
        for header in state.get("topology_blueprints", []):
            name = baseline_manifest["records"][header]["blueprint"]["filename"]
            shadow_document = parse_blueprint(shadow / "code" / name)
            live_entry = by_source[header]
            shadow_entry = replace(
                live_entry,
                blueprint_path=shadow / "code" / name,
                managed_path=shadow / "code" / name,
                document=shadow_document,
            )
            receipt = promoter_module.promote_document(shadow_entry.managed_path, shadow, database)
            if not receipt.get("verified"):
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"shadow topology promotion was not verified: {name}", details=receipt)
            receipts.append(receipt)
            entries.append(shadow_entry)
        include_edge_projection = None
        edge_stage = state.get("include_edge_authority")
        if isinstance(edge_stage, dict):
            include_module = importlib.import_module("kairos.include_edges")
            include_edge_projection = include_module.project_compiler_include_edges(
                shadow,
                database,
                authority_path=root / "work" / "machine" / str(edge_stage["path"]),
            )
            if not include_edge_projection.get("verified"):
                raise WorkshopError("SHADOW_INCLUDE_EDGE_PROJECTION_FAILED", "shadow include-edge projection was not verified", details=include_edge_projection)
        summary, issues = verify_database_projection(self.config, entries, database_path=shadow / ".kairos" / "kairos.db")
        if issues:
            raise WorkshopError("SHADOW_PROJECTION_MISMATCH", "shadow KAIROS projection is not exact", details=issues)
        return {"adapter": "native", "receipts": receipts, "database": summary, "compiler_include_edges": include_edge_projection, "shadow_workspace": str(shadow)}

    def _shadow_fixture(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        if not self.config.raw.get("test_mode"):
            raise WorkshopError("FIXTURE_ADAPTER_FORBIDDEN", "fixture shadow adapter is allowed only when test_mode=true")
        for source in state["changed_sources"]:
            name = baseline_manifest["records"][source]["blueprint"]["filename"]
            document = parse_blueprint(root / "work" / "blueprints" / name)
            if document.revision != baseline_manifest["records"][source]["blueprint"]["revision"] + 1:
                raise WorkshopError("SHADOW_REVISION_INVALID", f"fixture revision is not baseline+1: {name}")
        return {"adapter": "fixture", "verified": True}

    def _fixture_promote_live(self, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> None:
        if not self.config.raw.get("test_mode"):
            raise WorkshopError("FIXTURE_ADAPTER_FORBIDDEN", "fixture projection is allowed only when test_mode=true")
        connection = sqlite3.connect(self.config.kairos_database)
        try:
            for source in state["changed_sources"]:
                name = baseline_manifest["records"][source]["blueprint"]["filename"]
                document = parse_blueprint(self.config.managed_blueprint_root / name)
                artifact_id = document.artifact_id
                raw = (self.config.managed_blueprint_root / name).read_text(encoding="utf-8")
                digest = sha256_text(raw.replace("\r\n", "\n").replace("\r", "\n"))
                metadata_json = json.dumps(document.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                connection.execute(
                    "UPDATE artifacts SET path=?,revision=?,content_sha256=?,metadata_json=? WHERE artifact_id=?",
                    (f"code/{name}", document.revision, digest, metadata_json, artifact_id),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO artifact_revisions(artifact_id,revision,content_sha256,path,metadata_json,promoted_at) VALUES(?,?,?,?,?,?)",
                    (artifact_id, document.revision, digest, f"code/{name}", metadata_json, utc_now()),
                )
                connection.execute("DELETE FROM section_fts WHERE artifact_id=?", (artifact_id,))
                connection.execute("DELETE FROM sections WHERE artifact_id=?", (artifact_id,))
                for section in parsed_sections(document):
                    connection.execute(
                        "INSERT INTO sections(artifact_id,section_id,position,title,capsule,body,body_sha256) VALUES(?,?,?,?,?,?,?)",
                        (artifact_id, section["section_id"], section["position"], section["title"], section["capsule"], section["body"], section["body_sha256"]),
                    )
                    connection.execute(
                        "INSERT INTO section_fts(artifact_id,section_id,title,capsule,questions,entities,facets,body) VALUES(?,?,?,?,?,?,?,?)",
                        (artifact_id, section["section_id"], section["title"], section["capsule"], "", "", "", section["body"]),
                    )
                for header_key, table, columns in GRAPH_PROJECTIONS:
                    connection.execute(f"DELETE FROM {table} WHERE artifact_id=?", (artifact_id,))
                    for ordinal, row in enumerate(document.metadata.get(header_key, []), 1):
                        names = ["artifact_id", "ordinal", *(column for column, _ in columns)]
                        values: list[Any] = [artifact_id, ordinal]
                        for column, keys in columns:
                            value = next((row[key] for key in keys if key in row), None)
                            if column in {"hash_bound", "commit_bound"}:
                                value = 1 if value is True else 0
                            elif value is None:
                                value = ""
                            elif not isinstance(value, str):
                                value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                            values.append(value)
                        placeholders = ",".join("?" for _ in names)
                        connection.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({placeholders})", values)
            connection.commit()
        finally:
            connection.close()

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in {"PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"verify is not allowed from {state['state']}")
        self._assert_live_baseline(state)
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        intake_stage = state.get("project_intake_authority")
        if isinstance(intake_stage, dict):
            staged_intake = root / "work" / "machine" / str(intake_stage.get("path", ""))
            if (
                not staged_intake.is_file()
                or sha256_bytes(staged_intake.read_bytes()) != str(intake_stage.get("sha256", ""))
            ):
                raise WorkshopError(
                    "PROJECT_INTAKE_CHANGED_AFTER_PREPARE",
                    "staged project-intake authority changed after prepare",
                )
        if self._test_changes(root, state, baseline_manifest) != state.get("test_changes", []):
            raise WorkshopError("TEST_CHANGED_AFTER_PREPARE", "test change classification differs from prepared state")
        for source in state["changed_sources"]:
            record = baseline_manifest["records"][source]
            name = record["blueprint"]["filename"]
            blueprint = root / "work" / "blueprints" / name
            managed = root / "work" / "managed" / name
            if not blueprint.is_file() or not managed.is_file() or blueprint.read_bytes() != managed.read_bytes():
                raise WorkshopError("WORK_BLUEPRINT_PAIR_MISMATCH", f"work external/managed pair differs: {name}")
            document = parse_blueprint(blueprint)
            work_source = root / "work" / source
            mapped_work_source = work_source if document.source_path is not None else None
            work_header = None
            if record.get("header"):
                live_header = Path(record["header"]["path"])
                work_header = root / "work" / live_header.resolve().relative_to(self.config.codebase_root)
            ledger_issues = verify_ledger(document, mapped_work_source, work_header)
            if ledger_issues:
                raise WorkshopError("WORK_LEDGER_MISMATCH", f"work ledger differs for {name}", details=ledger_issues)
            if document.revision != record["blueprint"]["revision"] + 1:
                raise WorkshopError("WORK_REVISION_INVALID", f"revision must be baseline+1 for {name}")
        for header in state.get("topology_blueprints", []):
            record = baseline_manifest["records"][header]
            name = record["blueprint"]["filename"]
            blueprint = root / "work" / "blueprints" / name
            managed = root / "work" / "managed" / name
            if not blueprint.is_file() or not managed.is_file() or blueprint.read_bytes() != managed.read_bytes():
                raise WorkshopError("WORK_BLUEPRINT_PAIR_MISMATCH", f"topology work external/managed pair differs: {name}")
            document = parse_blueprint(blueprint)
            work_header = root / "work" / header
            if not work_header.is_file():
                work_header = self.config.codebase_root / header
            ledger_issues = verify_ledger(document, None, work_header)
            if ledger_issues:
                raise WorkshopError("WORK_LEDGER_MISMATCH", f"topology work ledger differs for {name}", details=ledger_issues)
            if document.revision != record["blueprint"]["revision"] + 1:
                raise WorkshopError("WORK_REVISION_INVALID", f"topology revision must be baseline+1 for {name}")
        adapter = str(self.config.raw.get("shadow_adapter", "native"))
        shadow = self._shadow_native(root, state, baseline_manifest) if adapter == "native" else self._shadow_fixture(root, state, baseline_manifest)
        work_package = transaction_work_manifest(self.config, root, state)
        state["state"] = "SHADOW_VERIFIED"
        state["shadow"] = shadow
        state["verified_work_package_sha256"] = work_package["package_sha256"]
        self._save_transaction(
            root,
            state,
            event="SHADOW_VERIFIED",
            details={"adapter": adapter, "work_package_sha256": work_package["package_sha256"]},
        )
        return {
            "schema": "runtime-sync-shadow-verification/v1",
            "transaction_id": transaction_id,
            "state": state["state"],
            "shadow": shadow,
            "work_package_sha256": work_package["package_sha256"],
        }

    def scientific_baseline(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        gate = self.scientific_gate
        try:
            result = gate.baseline(root, state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_BASELINE_FAILED", details=details)
            raise
        state["scientific_parity"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_BASELINE_VERIFIED", details=result)
        return result

    def scientific_reproducibility(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        gate = self.scientific_gate
        try:
            result = gate.reproducibility(root, state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_REPRODUCIBILITY_FAILED", details=details)
            raise
        state["scientific_reproducibility"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
            "runner_sha256": result["runner_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_REPRODUCIBILITY_VERIFIED", details=result)
        return result

    def scientific_stage_runner_authority(
        self,
        transaction_id: str,
        *,
        decision_id: str,
        expected_runner_sha256: str,
    ) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        reproducibility_state = state.get("scientific_reproducibility")
        if not isinstance(reproducibility_state, dict):
            raise WorkshopError(
                "SCIENTIFIC_REPRODUCIBILITY_REQUIRED",
                "run scientific-reproducibility before staging a runner authority",
            )
        try:
            result = self.scientific_gate.stage_runner_authority(
                root,
                state,
                reproducibility_state,
                decision_id=decision_id,
                expected_runner_sha256=expected_runner_sha256,
            )
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_RUNNER_AUTHORITY_STAGE_FAILED", details=details)
            raise
        state["scientific_runner_authority"] = {
            "status": result["status"],
            "decision_id": result["decision_id"],
            "runner_path": result["runner_path"],
            "runner_sha256": result["runner_sha256"],
            "authority_receipt_path": result["authority_receipt_path"],
            "authority_receipt_sha256": result["authority_receipt_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_RUNNER_AUTHORITY_STAGED", details=result)
        return result

    def scientific_verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        self._assert_live_baseline(state)
        baseline_state = state.get("scientific_parity")
        if not isinstance(baseline_state, dict):
            raise WorkshopError("SCIENTIFIC_BASELINE_REQUIRED", "run scientific-baseline before editing")
        gate = self.scientific_gate
        try:
            result = gate.verify_candidate(root, state, baseline_state)
            self._require_lease(transaction_id)
            self._assert_live_baseline(state)
        except Exception as exc:
            details = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="SCIENTIFIC_CANDIDATE_FAILED", details=details)
            raise
        state["scientific_parity"] = {
            **baseline_state,
            "status": result["status"],
            "decision_id": result["decision_id"],
            "receipt_path": result["receipt_path"],
            "receipt_sha256": result["receipt_sha256"],
            "verified_work_package_sha256": result["verified_work_package_sha256"],
        }
        self._save_transaction(root, state, event="SCIENTIFIC_PARITY_VERIFIED", details=result)
        return result

    def _import_kairos(self) -> tuple[Any, Any]:
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        governance = importlib.import_module("kairos.governance")
        heartbeat = importlib.import_module("kairos.heartbeat")
        return governance, heartbeat

    def _backup_apply_targets(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        changes = {row["source"]: row for row in state.get("changes", [])}
        for source in state["changed_sources"]:
            record = baseline_manifest["records"][source]
            change = changes.get(source)
            if not change:
                raise WorkshopError("TRANSACTION_CHANGESET_INVALID", f"missing change record for {source}")
            targets: list[tuple[str, Path]] = []
            if change.get("source_changed"):
                targets.append(("runtime", self.config.codebase_root / source))
            if change.get("header_changed") and record.get("header"):
                targets.append(("runtime", Path(record["header"]["path"])))
            name = record["blueprint"]["filename"]
            targets.extend((
                ("blueprint", self.config.blueprint_root / name),
                ("managed", self.config.managed_blueprint_root / name),
            ))
            for role, live in targets:
                key = f"{role}/{sha256_bytes(str(live).encode('utf-8'))[:16]}_{live.name}"
                backup = root / "rollback" / key
                copy_exact(live, backup)
                rows.append({"role": role, "live": str(live), "backup": str(backup), "sha256": sha256_bytes(live.read_bytes())})
        for change in state.get("test_changes", []):
            if change["changed"]:
                relative = change["source"]
                live = self.config.codebase_root / relative
                backup = root / "rollback" / relative
                copy_exact(live, backup)
                rows.append({"role": "dependent-test", "live": str(live),
                             "backup": str(backup), "sha256": sha256_bytes(live.read_bytes())})
        for header in state.get("topology_blueprints", []):
            record = baseline_manifest["records"][header]
            name = record["blueprint"]["filename"]
            for role, live in (
                ("blueprint", self.config.blueprint_root / name),
                ("managed", self.config.managed_blueprint_root / name),
            ):
                key = f"{role}/{sha256_bytes(str(live).encode('utf-8'))[:16]}_{live.name}"
                backup = root / "rollback" / key
                copy_exact(live, backup)
                rows.append({"role": role, "live": str(live), "backup": str(backup), "sha256": sha256_bytes(live.read_bytes())})
        for relative in state.get("authority_documents", []):
            live = self.config.kairos_workspace / relative
            backup = root / "rollback" / "authority" / relative
            copy_exact(live, backup)
            rows.append({
                "role": "authority",
                "live": str(live),
                "backup": str(backup),
                "sha256": sha256_bytes(live.read_bytes()),
            })
        for relative in state.get("machine_authority_files", []):
            live = (self.config.machine_root / relative).resolve()
            try:
                live.relative_to(self.config.machine_root.resolve())
            except ValueError as exc:
                raise WorkshopError("PATH_ESCAPE", f"machine authority escapes Workshop root: {relative}") from exc
            backup = root / "rollback" / "machine" / relative
            copy_exact(live, backup)
            rows.append({
                "role": "machine-authority",
                "live": str(live),
                "backup": str(backup),
                "sha256": sha256_bytes(live.read_bytes()),
            })
        atomic_write_json(root / "rollback" / "targets.json", rows)
        return rows

    def _rollback_files(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in reversed(rows):
            live = Path(row["live"])
            backup = Path(row["backup"])
            try:
                copy_exact(backup, live)
                valid = sha256_bytes(live.read_bytes()) == row["sha256"]
                results.append({"path": str(live), "restored": valid})
            except Exception as exc:
                results.append({"path": str(live), "restored": False, "error": str(exc)})
        return results

    def apply(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"apply requires SHADOW_VERIFIED, found {state['state']}")
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        self._assert_live_baseline(state)
        current_work = transaction_work_manifest(self.config, root, state)
        self._test_changes(root, state, baseline_manifest)
        if current_work["package_sha256"] != state.get("verified_work_package_sha256"):
            raise WorkshopError(
                "WORK_CHANGED_AFTER_VERIFY",
                "transaction work tree changed after KAIROS shadow verification",
                details={
                    "verified": state.get("verified_work_package_sha256"),
                    "current": current_work["package_sha256"],
                },
            )
        scientific_parity = self.scientific_gate.assert_apply_ready(root, state)
        adapter = str(self.config.raw.get("shadow_adapter", "native"))
        changed_managed = [f"code/{baseline_manifest['records'][source]['blueprint']['filename']}" for source in state["changed_sources"]]
        changed_managed.extend(
            f"code/{baseline_manifest['records'][header]['blueprint']['filename']}"
            for header in state.get("topology_blueprints", [])
        )
        changed_managed.extend(state.get("authority_documents", []))
        changed_managed = sorted(set(changed_managed))
        permit: dict[str, Any] | None = None
        if adapter == "native":
            runtime_state_path = self.config.kairos_workspace / ".kairos" / "runtime_state.json"
            runtime_state = read_json(runtime_state_path)
            if runtime_state.get("lifecycle") == "FINALIZED":
                raise WorkshopError("KAIROS_FINALIZED", "KAIROS workspace is FINALIZED; open a governed loop/task before workshop apply")
            governance, _ = self._import_kairos()
            permit = governance.issue_external_edit_permit(
                self.config.kairos_workspace,
                paths=changed_managed,
                reason=f"Runtime Sync Workshop {transaction_id}: {state['purpose']}",
                ttl_seconds=1800,
            )
        backups = self._backup_apply_targets(root, state, baseline_manifest)
        state["state"] = "APPLYING"
        state["permit"] = permit
        self._save_transaction(root, state, event="APPLY_STARTED", details={"managed_paths": changed_managed})
        heartbeat_started = False
        try:
            changes = {row["source"]: row for row in state.get("changes", [])}
            for source in state["changed_sources"]:
                record = baseline_manifest["records"][source]
                change = changes.get(source)
                if not change:
                    raise WorkshopError("TRANSACTION_CHANGESET_INVALID", f"missing change record for {source}")
                if change.get("source_changed"):
                    copy_exact(root / "work" / source, self.config.codebase_root / source)
                if change.get("header_changed") and record.get("header"):
                    live_header = Path(record["header"]["path"])
                    relative_header = live_header.resolve().relative_to(self.config.codebase_root)
                    copy_exact(root / "work" / relative_header, live_header)
                name = record["blueprint"]["filename"]
                copy_exact(root / "work" / "blueprints" / name, self.config.blueprint_root / name)
            for source in state["changed_sources"]:
                name = baseline_manifest["records"][source]["blueprint"]["filename"]
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for header in state.get("topology_blueprints", []):
                name = baseline_manifest["records"][header]["blueprint"]["filename"]
                copy_exact(root / "work" / "blueprints" / name, self.config.blueprint_root / name)
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for relative in state.get("authority_documents", []):
                copy_exact(root / "work" / "authority" / relative, self.config.kairos_workspace / relative)
            for relative in state.get("machine_authority_files", []):
                copy_exact(root / "work" / "machine" / relative, self.config.machine_root / relative)
            heartbeat_receipt: dict[str, Any]
            for change in state.get("test_changes", []):
                if change["changed"]:
                    relative = change["source"]
                    copy_exact(root / "work" / relative, self.config.codebase_root / relative)
            if adapter == "native":
                _, heartbeat = self._import_kairos()
                heartbeat_started = True
                heartbeat_receipt = heartbeat.run_heartbeat(
                    self.config.kairos_workspace,
                    requested_mode="verify",
                    changed_paths=changed_managed,
                    use_reconciliation=True,
                    trigger=f"runtime-sync-workshop:{transaction_id}",
                )
                if not heartbeat_receipt.get("verified"):
                    raise WorkshopError("HEARTBEAT_NOT_VERIFIED", "KAIROS heartbeat did not verify", details=heartbeat_receipt)
            else:
                self._fixture_promote_live(state, baseline_manifest)
                heartbeat_receipt = {"schema": "fixture-heartbeat/v1", "verified": True}
            post = build_corpus_manifest(self.config)
            if not post["verified"]:
                raise WorkshopError("POSTCHECK_FAILED", "post-apply corpus verification failed", details=post["issues"])
            for source in state["changed_sources"]:
                record = post["records"][source]
                baseline_record = baseline_manifest["records"][source]
                name = record["blueprint"]["filename"]
                if (self.config.codebase_root / source).read_bytes() != (root / "work" / source).read_bytes():
                    raise WorkshopError("POSTCHECK_SOURCE_MISMATCH", f"live source differs from work result: {source}")
                if (self.config.blueprint_root / name).read_bytes() != (root / "work" / "blueprints" / name).read_bytes():
                    raise WorkshopError("POSTCHECK_BLUEPRINT_MISMATCH", f"live blueprint differs from work result: {name}")
                if record["blueprint"]["revision"] != baseline_record["blueprint"]["revision"] + 1:
                    raise WorkshopError("POSTCHECK_REVISION_INVALID", f"live revision did not advance exactly once: {name}")
            for header in state.get("topology_blueprints", []):
                record = post["records"][header]
                baseline_record = baseline_manifest["records"][header]
                name = record["blueprint"]["filename"]
                if (self.config.blueprint_root / name).read_bytes() != (root / "work" / "blueprints" / name).read_bytes():
                    raise WorkshopError("POSTCHECK_BLUEPRINT_MISMATCH", f"live topology blueprint differs from work result: {name}")
                if record["blueprint"]["revision"] != baseline_record["blueprint"]["revision"] + 1:
                    raise WorkshopError("POSTCHECK_REVISION_INVALID", f"topology blueprint revision did not advance exactly once: {name}")
            for relative in state.get("dependent_tests", []):
                if (self.config.codebase_root / relative).read_bytes() != (root / "work" / relative).read_bytes():
                    raise WorkshopError("POSTCHECK_TEST_MISMATCH", f"live test differs from work: {relative}")
            for relative in state.get("authority_documents", []):
                if (self.config.kairos_workspace / relative).read_bytes() != (root / "work" / "authority" / relative).read_bytes():
                    raise WorkshopError("POSTCHECK_AUTHORITY_MISMATCH", f"live authority document differs from work: {relative}")
            for relative in state.get("machine_authority_files", []):
                if (self.config.machine_root / relative).read_bytes() != (root / "work" / "machine" / relative).read_bytes():
                    raise WorkshopError("POSTCHECK_MACHINE_AUTHORITY_MISMATCH", f"live machine authority differs from work: {relative}")
            mirror = self._mirror(post)
            seal = {
                "schema": "runtime-sync-seal/v1",
                "created_at": utc_now(),
                "package_sha256": post["package_sha256"],
                "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{post['package_sha256']}.json"),
                "counts": post["counts"],
                "transaction_id": transaction_id,
            }
            atomic_write_json(self.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["heartbeat"] = heartbeat_receipt
            self._save_transaction(root, state, event="POSTCHECK_VERIFIED", details={"package_sha256": post["package_sha256"]})
            self._release_lease(transaction_id)
            receipt = {
                "schema": "runtime-sync-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "baseline_package_sha256": state["baseline_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "heartbeat": heartbeat_receipt,
                "bit_exact": True,
                "scientific_parity": scientific_parity,
            }
            self._write_receipt("postcheck", receipt)
            return receipt
        except Exception as exc:
            if heartbeat_started:
                state["state"] = "RECOVERY_REQUIRED"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self._save_transaction(root, state, event="RECOVERY_REQUIRED", details=state["failure"])
                raise WorkshopError("RECOVERY_REQUIRED", "apply crossed the heartbeat boundary and failed; lease retained", details=state["failure"]) from exc
            rollback = self._rollback_files(backups)
            if all(row.get("restored") for row in rollback):
                state["state"] = "ROLLED_BACK"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self._save_transaction(root, state, event="APPLY_ROLLED_BACK", details=rollback)
                self._release_lease(transaction_id)
                raise WorkshopError("APPLY_ROLLED_BACK", "apply failed before heartbeat and all named files were restored", details={"cause": state["failure"], "rollback": rollback}) from exc
            state["state"] = "RECOVERY_REQUIRED"
            state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="ROLLBACK_INCOMPLETE", details=rollback)
            raise WorkshopError("RECOVERY_REQUIRED", "apply failed and rollback was incomplete; lease retained", details={"cause": state["failure"], "rollback": rollback}) from exc

    def abort(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in PRE_APPLY_STATES:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"abort is not allowed from {state['state']}")
        self._assert_live_baseline(state)
        state["state"] = "ABORTED"
        self._save_transaction(root, state, event="TRANSACTION_ABORTED")
        self._release_lease(transaction_id)
        return {"schema": "runtime-sync-abort/v1", "transaction_id": transaction_id, "state": "ABORTED", "live_corpus_unchanged": True}

    @staticmethod
    def _authority_sha256(value: str, label: str) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise WorkshopError("AUTHORITY_HASH_INVALID", f"{label} must be a lowercase hexadecimal SHA-256")
        return normalized

    def _load_authority_record(
        self,
        *,
        record_id: str,
        directory: str,
        expected_type: str,
        expected_states: set[str],
        expected_sha256: str,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(record_id, str) or not record_id.strip() or Path(record_id).name != record_id:
            raise WorkshopError("AUTHORITY_RECORD_ID_INVALID", f"{label} id is not a single document id")
        expected_hash = self._authority_sha256(expected_sha256, f"{label} SHA-256")
        root = (self.config.kairos_workspace / directory).resolve()
        if not root.is_dir():
            raise WorkshopError("AUTHORITY_RECORD_MISSING", f"KAIROS {label} directory is missing: {root}")
        candidates = sorted(
            path for path in root.glob("*.md")
            if path.is_file() and path.stem.casefold() == record_id.casefold()
        )
        if len(candidates) != 1:
            raise WorkshopError(
                "AUTHORITY_RECORD_MISSING" if not candidates else "AUTHORITY_RECORD_AMBIGUOUS",
                f"expected exactly one KAIROS {label} document for {record_id}",
                details=[str(path) for path in candidates],
            )
        path = candidates[0].resolve()
        raw = path.read_bytes()
        actual_hash = sha256_bytes(raw)
        if actual_hash != expected_hash:
            raise WorkshopError(
                "AUTHORITY_RECORD_HASH_MISMATCH",
                f"KAIROS {label} hash does not match the explicitly supplied authority hash",
                details={"id": record_id, "path": str(path), "expected": expected_hash, "actual": actual_hash},
            )
        try:
            text = raw.decode("utf-8")
            parts = text.split("+++", 2)
            if len(parts) != 3 or parts[0].strip():
                raise ValueError("missing kairos-context front matter")
            header = tomllib.loads(parts[1].strip())
        except (UnicodeDecodeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise WorkshopError(
                "AUTHORITY_RECORD_INVALID",
                f"KAIROS {label} has no valid kairos-context header: {path}",
            ) from exc
        if header.get("schema") != "kairos-context/v1":
            raise WorkshopError("AUTHORITY_RECORD_INVALID", f"KAIROS {label} schema is not kairos-context/v1: {path}")
        if header.get("id") != record_id or header.get("type") != expected_type:
            raise WorkshopError(
                "AUTHORITY_RECORD_INVALID",
                f"KAIROS {label} id or type does not match the requested authority record",
                details={"id": header.get("id"), "type": header.get("type"), "expected_id": record_id, "expected_type": expected_type},
            )
        state = header.get("state")
        if state not in expected_states:
            raise WorkshopError(
                "AUTHORITY_RECORD_STATE_INVALID",
                f"KAIROS {label} is not in an admissible state: {state}",
                details={"id": record_id, "allowed": sorted(expected_states)},
            )
        required_scope = ("goal", "milestone", "task", "route")
        if any(not isinstance(header.get(key), str) or not header[key].strip() for key in required_scope):
            raise WorkshopError("AUTHORITY_RECORD_INVALID", f"KAIROS {label} has incomplete route scope: {path}")
        return {
            "id": record_id,
            "type": expected_type,
            "state": state,
            "path": str(path),
            "sha256": actual_hash,
            "goal": header["goal"],
            "milestone": header["milestone"],
            "task": header["task"],
            "route": header["route"],
        }

    def _verify_authority_scope(
        self,
        *,
        decision_id: str,
        decision_sha256: str,
        exception_report_id: str,
        exception_report_sha256: str,
        blocker_report_id: str,
        blocker_report_sha256: str,
    ) -> dict[str, dict[str, Any]]:
        decision = self._load_authority_record(
            record_id=decision_id,
            directory="decisions",
            expected_type="decision",
            expected_states={"active", "completed"},
            expected_sha256=decision_sha256,
            label="decision",
        )
        exception_report = self._load_authority_record(
            record_id=exception_report_id,
            directory="reports",
            expected_type="report",
            expected_states={"partial", "success"},
            expected_sha256=exception_report_sha256,
            label="human-exception report",
        )
        blocker_report = self._load_authority_record(
            record_id=blocker_report_id,
            directory="reports",
            expected_type="report",
            expected_states={"blocked"},
            expected_sha256=blocker_report_sha256,
            label="blocker report",
        )
        scope = {(record["goal"], record["milestone"], record["task"]) for record in (decision, exception_report, blocker_report)}
        if len(scope) != 1:
            raise WorkshopError(
                "AUTHORITY_SCOPE_MISMATCH",
                "decision, human-exception report, and blocker report do not share one Goal/Milestone/Task scope",
                details={
                    "decision": decision,
                    "human_exception": exception_report,
                    "blocker": blocker_report,
                },
            )
        return {
            "decision": decision,
            "human_exception": exception_report,
            "blocker": blocker_report,
        }

    def _verify_checkout_live_scope(
        self,
        root: Path,
        state: dict[str, Any],
        baseline_manifest: dict[str, Any],
        *,
        error_code: str,
    ) -> list[str]:
        sources = state.get("sources")
        records = baseline_manifest.get("records")
        if not isinstance(sources, list) or not sources or not isinstance(records, dict):
            raise WorkshopError("TRANSACTION_SCOPE_INVALID", "transaction baseline has no valid source scope")
        checked: list[str] = []
        for value in sources:
            if not isinstance(value, str) or not value.strip():
                raise WorkshopError("TRANSACTION_SCOPE_INVALID", "transaction source scope contains an invalid entry")
            source = value.replace("\\", "/")
            record = records.get(source)
            if not isinstance(record, dict):
                raise WorkshopError("TRANSACTION_SCOPE_INVALID", f"transaction source is absent from its baseline manifest: {source}")
            try:
                baseline_source = (root / "baseline" / source).resolve()
                baseline_source.relative_to((root / "baseline").resolve())
                live_source = (self.config.codebase_root / source).resolve()
                live_source.relative_to(self.config.codebase_root.resolve())
            except (ValueError, OSError) as exc:
                raise WorkshopError("PATH_ESCAPE", f"transaction source escapes its configured root: {source}") from exc
            pairs: list[tuple[Path, Path]] = [(baseline_source, live_source)]
            header = record.get("header")
            if header:
                header_path = header.get("path") if isinstance(header, dict) else None
                if not isinstance(header_path, str) or not header_path.strip():
                    raise WorkshopError("TRANSACTION_SCOPE_INVALID", f"header mapping is invalid: {source}")
                live_header = Path(header_path).resolve()
                try:
                    relative_header = live_header.relative_to(self.config.codebase_root.resolve())
                except ValueError as exc:
                    raise WorkshopError("PATH_ESCAPE", f"transaction header escapes its configured root: {live_header}") from exc
                pairs.append((root / "baseline" / relative_header, live_header))
            blueprint = record.get("blueprint")
            name = blueprint.get("filename") if isinstance(blueprint, dict) else None
            if not isinstance(name, str) or not name.strip() or Path(name).name != name:
                raise WorkshopError("TRANSACTION_SCOPE_INVALID", f"blueprint mapping is invalid: {source}")
            pairs.append(
                (
                    (root / "baseline" / "blueprints" / name).resolve(),
                    (self.config.blueprint_root / name).resolve(),
                )
            )
            pairs.append(
                (
                    (root / "baseline" / "managed" / name).resolve(),
                    (self.config.managed_blueprint_root / name).resolve(),
                )
            )
            for baseline, live in pairs:
                if not baseline.is_file() or not live.is_file() or baseline.read_bytes() != live.read_bytes():
                    raise WorkshopError(error_code, f"checked-out live path differs from its immutable baseline: {live}")
                checked.append(str(live))
            work_pairs = [(root / "work" / source).resolve()]
            if header:
                work_pairs.append(root / "work" / Path(header["path"]).resolve().relative_to(self.config.codebase_root.resolve()))
            work_pairs.extend((root / "work" / "blueprints" / name, root / "work" / "managed" / name))
            if any(not path.is_file() for path in work_pairs):
                raise WorkshopError("AUTHORITY_RECOVERY_WORK_SCOPE_INVALID", f"preserved work scope is incomplete: {source}")
        self._test_changes(root, state, baseline_manifest)
        for relative in state.get("dependent_tests", []):
            live = self.config.codebase_root / relative
            if live.read_bytes() != (root / "baseline" / relative).read_bytes():
                raise WorkshopError(error_code, f"checked-out live test differs from baseline: {relative}")
            checked.append(str(live))
        return checked

    def authority_recover(
        self,
        transaction_id: str,
        *,
        decision_id: str,
        decision_sha256: str,
        exception_report_id: str,
        exception_report_sha256: str,
        blocker_report_id: str,
        blocker_report_sha256: str,
        expected_current_package_sha256: str,
        expected_sealed_package_sha256: str,
    ) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        if state.get("schema") != "runtime-sync-transaction/v1" or state.get("transaction_id") != transaction_id:
            raise WorkshopError("TRANSACTION_INVALID", f"transaction schema or binding is invalid: {transaction_id}")
        if self.lease_path.exists():
            raise WorkshopError("AUTHORITY_RECOVERY_LEASE_ACTIVE", "authority recovery requires the global lease to be absent")
        if state.get("state") not in PRE_APPLY_STATES:
            raise WorkshopError("AUTHORITY_RECOVERY_STATE_INVALID", f"authority recovery is not allowed from {state.get('state')}")
        expected_current = self._authority_sha256(expected_current_package_sha256, "current package SHA-256")
        expected_sealed = self._authority_sha256(expected_sealed_package_sha256, "sealed package SHA-256")
        if expected_current == expected_sealed:
            raise WorkshopError("AUTHORITY_RECOVERY_PACKAGE_INVALID", "current and sealed package hashes must differ")
        authority = self._verify_authority_scope(
            decision_id=decision_id,
            decision_sha256=decision_sha256,
            exception_report_id=exception_report_id,
            exception_report_sha256=exception_report_sha256,
            blocker_report_id=blocker_report_id,
            blocker_report_sha256=blocker_report_sha256,
        )
        seal = read_json(self.seal_path) if self.seal_path.is_file() else None
        if not isinstance(seal, dict) or seal.get("package_sha256") != expected_sealed:
            raise WorkshopError(
                "AUTHORITY_RECOVERY_SEAL_MISMATCH",
                "the existing seal is not the explicitly supplied historical sealed package",
                details={"expected": expected_sealed, "actual": seal.get("package_sha256") if isinstance(seal, dict) else None},
            )
        baseline_manifest = read_json(root / "baseline" / "manifest.json") if (root / "baseline" / "manifest.json").is_file() else None
        if not isinstance(baseline_manifest, dict) or baseline_manifest.get("package_sha256") != state.get("baseline_package_sha256"):
            raise WorkshopError("AUTHORITY_RECOVERY_BASELINE_INVALID", "transaction baseline manifest is missing or not bound to its state")
        current = build_corpus_manifest(self.config)
        if not current.get("verified") or current.get("issues"):
            raise WorkshopError("AUTHORITY_RECOVERY_CORPUS_INVALID", "current corpus is not fully verified", details=current.get("issues", []))
        if current.get("package_sha256") != expected_current:
            raise WorkshopError(
                "AUTHORITY_RECOVERY_PACKAGE_MISMATCH",
                "current live package differs from the operator-confirmed recovery package",
                details={"expected": expected_current, "actual": current.get("package_sha256")},
            )
        baseline_package = str(state.get("baseline_package_sha256", "")).lower()
        if expected_current == baseline_package:
            raise WorkshopError("AUTHORITY_RECOVERY_NOT_REQUIRED", "current package still equals the checkout baseline")
        checked = self._verify_checkout_live_scope(
            root,
            state,
            baseline_manifest,
            error_code="AUTHORITY_RECOVERY_LIVE_SCOPE_CHANGED",
        )
        authority_recheck = self._verify_authority_scope(
            decision_id=decision_id,
            decision_sha256=decision_sha256,
            exception_report_id=exception_report_id,
            exception_report_sha256=exception_report_sha256,
            blocker_report_id=blocker_report_id,
            blocker_report_sha256=blocker_report_sha256,
        )
        if authority_recheck != authority:
            raise WorkshopError("AUTHORITY_RECORD_CHANGED", "an authority record changed during recovery validation")
        current_recheck = build_corpus_manifest(self.config)
        if not current_recheck.get("verified") or current_recheck.get("issues"):
            raise WorkshopError("AUTHORITY_RECOVERY_CORPUS_INVALID", "current corpus changed during recovery validation", details=current_recheck.get("issues", []))
        if current_recheck.get("package_sha256") != expected_current:
            raise WorkshopError(
                "AUTHORITY_RECOVERY_PACKAGE_CHANGED",
                "current live package changed during recovery validation",
                details={"expected": expected_current, "actual": current_recheck.get("package_sha256")},
            )
        seal_recheck = read_json(self.seal_path) if self.seal_path.is_file() else None
        if not isinstance(seal_recheck, dict) or seal_recheck.get("package_sha256") != expected_sealed:
            raise WorkshopError("AUTHORITY_RECOVERY_SEAL_CHANGED", "the named historical seal changed during recovery validation")
        checked_recheck = self._verify_checkout_live_scope(
            root,
            state,
            baseline_manifest,
            error_code="AUTHORITY_RECOVERY_LIVE_SCOPE_CHANGED",
        )
        if checked_recheck != checked:
            raise WorkshopError("AUTHORITY_RECOVERY_LIVE_SCOPE_CHANGED", "the checked-out live scope changed during recovery validation")
        if self.lease_path.exists():
            raise WorkshopError("AUTHORITY_RECOVERY_LEASE_ACTIVE", "a global lease appeared during authority recovery")
        details = {
            "baseline_package_sha256": baseline_package,
            "current_package_sha256": expected_current,
            "sealed_package_sha256": expected_sealed,
            "checked_paths": checked_recheck,
            "live_scope_unchanged": True,
            "work_preserved": True,
            "lease_absent": True,
            "authority": authority,
        }
        state["state"] = "ABORTED_STALE_BASELINE"
        state["authority_recovery"] = {
            "schema": "runtime-sync-authority-recovery/v1",
            "transaction_id": transaction_id,
            **details,
        }
        self._save_transaction(root, state, event="AUTHORITY_RECOVERY_STALE_BASELINE_ABORTED", details=details)
        receipt = self._write_receipt(
            "authority-recovery",
            {
                "schema": "runtime-sync-authority-recovery/v1",
                "transaction_id": transaction_id,
                "state": state["state"],
                **details,
            },
        )
        return {
            "schema": "runtime-sync-authority-recovery/v1",
            "transaction_id": transaction_id,
            "state": state["state"],
            "receipt": str(receipt),
            **details,
        }

    def stale_abort(self, transaction_id: str, *, expected_current_package_sha256: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in PRE_APPLY_STATES:
            raise WorkshopError(
                "TRANSACTION_STATE_INVALID",
                f"stale-abort is not allowed from {state['state']}",
            )
        current = build_corpus_manifest(self.config)
        if not current["verified"]:
            raise WorkshopError(
                "STALE_ABORT_CORPUS_INVALID",
                "stale-abort requires a fully verified current live corpus",
                details=current["issues"],
            )
        expected = expected_current_package_sha256.lower()
        if current["package_sha256"] != expected:
            raise WorkshopError(
                "STALE_ABORT_PACKAGE_MISMATCH",
                "current live package differs from the operator-confirmed stale-abort package",
                details={"expected": expected, "actual": current["package_sha256"]},
            )
        if current["package_sha256"] == state["baseline_package_sha256"]:
            raise WorkshopError(
                "STALE_ABORT_NOT_REQUIRED",
                "current package still equals the checkout baseline; use normal abort",
            )
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        checked: list[str] = []
        for source in state["sources"]:
            record = baseline_manifest["records"][source]
            pairs: list[tuple[Path, Path]] = [
                (root / "baseline" / source, self.config.codebase_root / source),
            ]
            header = record.get("header")
            if header:
                live_header = Path(header["path"])
                relative = live_header.resolve().relative_to(self.config.codebase_root)
                pairs.append((root / "baseline" / relative, live_header))
            name = record["blueprint"]["filename"]
            pairs.extend(
                (
                    (root / "baseline" / "blueprints" / name, self.config.blueprint_root / name),
                    (root / "baseline" / "managed" / name, self.config.managed_blueprint_root / name),
                )
            )
            for baseline, live in pairs:
                if not baseline.is_file() or not live.is_file() or baseline.read_bytes() != live.read_bytes():
                    raise WorkshopError(
                        "STALE_ABORT_LIVE_SCOPE_CHANGED",
                        f"checked-out live path differs from its immutable baseline: {live}",
                    )
                checked.append(str(live))
        self._test_changes(root, state, baseline_manifest)
        for relative in state.get("dependent_tests", []):
            live = self.config.codebase_root / relative
            if live.read_bytes() != (root / "baseline" / relative).read_bytes():
                raise WorkshopError("STALE_ABORT_LIVE_SCOPE_CHANGED", f"checked-out live test differs from baseline: {relative}")
            checked.append(str(live))
        state["state"] = "ABORTED_STALE_BASELINE"
        details = {
            "baseline_package_sha256": state["baseline_package_sha256"],
            "current_package_sha256": current["package_sha256"],
            "live_scope_unchanged": True,
            "checked_paths": checked,
            "work_preserved": True,
        }
        self._save_transaction(root, state, event="STALE_BASELINE_ABORTED", details=details)
        self._release_lease(transaction_id)
        return {
            "schema": "runtime-sync-stale-abort/v1",
            "transaction_id": transaction_id,
            "state": state["state"],
            **details,
        }

    def recover(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] != "RECOVERY_REQUIRED":
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"recover requires RECOVERY_REQUIRED, found {state['state']}")
        post = build_corpus_manifest(self.config)
        if not post["verified"]:
            raise WorkshopError("RECOVERY_STILL_BLOCKED", "live corpus is not bit-exact; lease retained", details=post["issues"])
        if post["package_sha256"] == state["baseline_package_sha256"]:
            permit_revocation: dict[str, Any] | None = None
            permit = state.get("permit")
            if permit and str(self.config.raw.get("shadow_adapter", "native")) == "native":
                governance, _ = self._import_kairos()
                permit_revocation = governance.revoke_action_permit(
                    self.config.kairos_workspace,
                    permit_id=str(permit["permit_id"]),
                    reason=(
                        f"Runtime Sync Workshop {transaction_id} verified that the failed apply "
                        "left the complete live corpus at its sealed checkout baseline; the "
                        "authorized external edit will not be made by this transaction."
                    ),
                )
            state["state"] = "ROLLED_BACK"
            state["permit_revocation"] = permit_revocation
            details = {
                "package_sha256": post["package_sha256"],
                "live_corpus_unchanged": True,
                "permit_revocation": permit_revocation,
            }
            self._save_transaction(root, state, event="RECOVERY_BASELINE_ROLLED_BACK", details=details)
            self._release_lease(transaction_id)
            return {
                "schema": "runtime-sync-recovery/v1",
                "transaction_id": transaction_id,
                "state": "ROLLED_BACK",
                **details,
            }
        for source in state["changed_sources"]:
            record = post["records"].get(source)
            if not record:
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"post-recovery source is absent: {source}")
            name = record["blueprint"]["filename"]
            if (self.config.codebase_root / source).read_bytes() != (root / "work" / source).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live source does not equal transaction result: {source}")
            if (self.config.blueprint_root / name).read_bytes() != (root / "work" / "blueprints" / name).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live blueprint does not equal transaction result: {name}")
        for header in state.get("topology_blueprints", []):
            record = post["records"].get(header)
            if not record:
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"post-recovery topology header is absent: {header}")
            name = record["blueprint"]["filename"]
            if (self.config.blueprint_root / name).read_bytes() != (root / "work" / "blueprints" / name).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live topology blueprint differs from transaction result: {name}")
        for relative in state.get("dependent_tests", []):
            if (self.config.codebase_root / relative).read_bytes() != (root / "work" / relative).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live test differs from transaction result: {relative}")
        for relative in state.get("authority_documents", []):
            if (self.config.kairos_workspace / relative).read_bytes() != (root / "work" / "authority" / relative).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live authority document differs from transaction result: {relative}")
        for relative in state.get("machine_authority_files", []):
            if (self.config.machine_root / relative).read_bytes() != (root / "work" / "machine" / relative).read_bytes():
                raise WorkshopError("RECOVERY_STILL_BLOCKED", f"live machine authority differs from transaction result: {relative}")
        mirror = self._mirror(post)
        seal = {
            "schema": "runtime-sync-seal/v1",
            "created_at": utc_now(),
            "package_sha256": post["package_sha256"],
            "mirror_sha256": mirror["mirror_sha256"],
            "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{post['package_sha256']}.json"),
            "counts": post["counts"],
            "transaction_id": transaction_id,
            "recovered": True,
        }
        atomic_write_json(self.seal_path, seal)
        state["state"] = "POSTCHECK_VERIFIED"
        state["postcheck_package_sha256"] = post["package_sha256"]
        self._save_transaction(root, state, event="RECOVERY_POSTCHECK_VERIFIED")
        self._release_lease(transaction_id)
        return {"schema": "runtime-sync-recovery/v1", "transaction_id": transaction_id, "state": "POSTCHECK_VERIFIED", "package_sha256": post["package_sha256"]}

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        return {**state, "root": str(root), "lease_owned": self.lease_path.is_file() and read_json(self.lease_path).get("transaction_id") == transaction_id}


def re_fullmatch_transaction(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("TXN_") or len(value) != 28:
        return False
    return all(char in "0123456789abcdef" for char in value[4:].lower())
