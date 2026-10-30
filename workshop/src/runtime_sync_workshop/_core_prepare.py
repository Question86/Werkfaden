from __future__ import annotations

from ._core_contract import EngineCoreContract

import importlib
import json
import re
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
from .corpus import (
    canonical_include_edges,
    direct_include_edges,
    header_ownership,
    include_topology_sha256,
    verify_project_intake_binding,
)
from .scientific import transaction_work_manifest
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    copy_exact,
    read_json,
    sha256_bytes,
)


from ._core_state import PRE_APPLY_STATES


class PrepareCore(EngineCoreContract):
    """Private prepare responsibility; composed only by the canonical engine base."""

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

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        self._require_lease(transaction_id)
        if state["state"] not in PRE_APPLY_STATES:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"prepare is not allowed from {state['state']}")
        # Preparation always invalidates earlier approvals, including when a new
        # candidate fails a later gate. Do not leave an old receipt usable.
        if state["state"] in {"PREPARED", "SHADOW_VERIFIED"} or "metadata_check" in state:
            state["state"] = "CHECKED_OUT"
            for key in ("prepared_work_package_sha256", "verified_work_package_sha256", "shadow", "metadata_check"):
                state.pop(key, None)
            self._save_transaction(root, state, event="VERIFICATION_REOPENED")
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        from .corpus import framework_projection_reopen
        framework_projection_reopen(root, state)
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
                # This legacy token-review rule is C-family-specific. The separate
                # MetadataGate below requires actual contextual header maintenance
                # and source evidence for byte changes in every supported language.
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
        if review_blockers:
            state["state"] = "AWAITING_METADATA_REVIEW"
            self._save_transaction(root, state, event="METADATA_REVIEW_REQUIRED", details=review_blockers)
            raise WorkshopError("METADATA_REVIEW_REQUIRED", "prepare is blocked until metadata review is complete", details=review_blockers)
        try:
            document_changes = self.context_documents.prepare(root, state)
        except WorkshopError as exc:
            if exc.code == "CONTEXT_REVIEW_REQUIRED":
                state["state"] = "AWAITING_METADATA_REVIEW"
                self._save_transaction(root, state, event="CONTEXT_REVIEW_REQUIRED", details=exc.details)
            raise
        try:
            metadata_aftercheck = self.metadata_gate.validate(root, state)
        except WorkshopError as exc:
            state["state"] = "AWAITING_METADATA_REVIEW"
            self._save_transaction(root, state, event="METADATA_AFTERCHECK_REQUIRED", details={
                "code": exc.code, "details": exc.details,
            })
            raise
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
        state["document_changes"] = document_changes
        from .corpus import framework_projection_prepare
        framework_projection_prepare(self.config, root, state)
        frame = state.get("framework_projection") or {}
        if (not any(row["changed"] for row in change_rows) and not state.get("changed_documents")
                and not frame.get("changes") and not frame.get("machine_targets")):
            raise WorkshopError("TRANSACTION_NO_CHANGES", "prepare found no source, header, or semantic document changes")
        state["prepared_work_package_sha256"] = transaction_work_manifest(self.config, root, state)["package_sha256"]
        state["state"] = "PREPARED"
        self._save_transaction(root, state, event="PREPARE_COMPLETED", details=change_rows)
        return {"schema": "runtime-sync-prepare/v1", "transaction_id": transaction_id, "state": state["state"], "changes": change_rows, "document_changes": document_changes, "metadata_aftercheck": metadata_aftercheck, "next": "metadata-check -> verify -> apply"}

