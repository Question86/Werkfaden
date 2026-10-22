from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .blueprint import parse_blueprint, regenerate_mechanical_layers
from .corpus import build_corpus_manifest, load_config
from .migration import _advance_generated_document, _retire_document
from .scientific import ScientificParityGate
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    copy_exact,
    package_hash,
    read_json,
    sha256_bytes,
    utc_now,
)


C_TRANSLATION_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".cu"})
C_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".cuh"})


def _package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_universal():
    root = _package_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from kickstart.universal import (
        EXCLUDED_DIRECTORIES,
        UniversalSource,
        UniversalSurvey,
        _SUFFIX_TO_SPEC,
        _iter_project_files,
        _sha256,
        filename_for,
        render_markdown_corpus,
    )
    from kickstart.universal_workshop import _membership_authority, _render_source_index
    return (
        EXCLUDED_DIRECTORIES,
        UniversalSource,
        UniversalSurvey,
        _SUFFIX_TO_SPEC,
        _iter_project_files,
        _sha256,
        filename_for,
        render_markdown_corpus,
        _membership_authority,
        _render_source_index,
    )


def _safe_relative(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise WorkshopError("UNIVERSAL_SOURCE_SET_PATH_INVALID", f"unsafe project-relative path: {value}")
    return normalized


def _copy_tree_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    copy_exact(source, destination)


def _file_inventory(root: Path) -> dict[str, dict[str, Any]]:
    _, _, _, _, iter_project_files, _, _, _, _, _ = _load_universal()
    rows: dict[str, dict[str, Any]] = {}
    for path in iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        rows[relative] = {"sha256": sha256_bytes(raw), "bytes": len(raw)}
    return dict(sorted(rows.items()))


def _infer_renames(
    removed: list[str],
    added: list[str],
    baseline_files: dict[str, dict[str, Any]],
    candidate_root: Path,
) -> dict[str, str]:
    removed_by_hash: dict[str, list[str]] = {}
    added_by_hash: dict[str, list[str]] = {}
    for relative in removed:
        digest = str((baseline_files.get(relative) or {}).get("sha256", ""))
        removed_by_hash.setdefault(digest, []).append(relative)
    for relative in added:
        path = candidate_root / relative
        if path.is_file():
            added_by_hash.setdefault(sha256_bytes(path.read_bytes()), []).append(relative)
    result: dict[str, str] = {}
    for digest, old_paths in removed_by_hash.items():
        new_paths = added_by_hash.get(digest, [])
        if digest and len(old_paths) == 1 and len(new_paths) == 1:
            result[old_paths[0]] = new_paths[0]
    return result


def _remap_path(value: str, live_root: Path, candidate_root: Path) -> str:
    live = live_root.resolve()
    candidate = candidate_root.resolve()
    try:
        relative = Path(value).resolve().relative_to(live)
    except (ValueError, OSError):
        return value
    return (candidate / relative).as_posix()


def _remap_token(value: str, live_root: Path, candidate_root: Path) -> str:
    live_posix = live_root.resolve().as_posix()
    live_native = str(live_root.resolve())
    target_posix = candidate_root.resolve().as_posix()
    target_native = str(candidate_root.resolve())
    result = str(value)
    for source, target in (
        (live_posix, target_posix),
        (live_native, target_native),
        (live_native.replace("\\", "/"), target_posix),
    ):
        result = result.replace(source, target)
    return result


def _candidate_config(
    raw: dict[str, Any],
    *,
    live_root: Path,
    candidate_root: Path,
    machine_root: Path,
    shadow: Path,
    external_root: Path,
    source_paths: list[str],
    ecosystems: dict[str, str],
) -> dict[str, Any]:
    config = json.loads(json.dumps(raw))
    config["codebase_root"] = candidate_root.as_posix()
    config["runtime_root"] = candidate_root.as_posix()
    config["cmake_file"] = (machine_root / "project-intake" / "PROJECT_SOURCE_AUTHORITY.cmake").as_posix()
    config["dataflow_index"] = (shadow / "docs" / "PROJECT_SOURCE_INDEX.md").as_posix()
    config["blueprint_root"] = external_root.as_posix()
    config["managed_blueprint_root"] = (shadow / "code").as_posix()
    config["kairos_workspace"] = shadow.as_posix()
    config["kairos_database"] = (shadow / ".kairos" / "kairos.db").as_posix()
    config["expected_translation_units"] = len(source_paths)
    config["source_ecosystems"] = dict(sorted(ecosystems.items()))
    extensions = sorted({Path(value).suffix.lower().lstrip(".") for value in source_paths})
    source_authority = dict(config.get("source_authority") or {})
    source_authority["translation_unit_extensions"] = extensions
    config["source_authority"] = source_authority
    configured_test_suffixes = {
        str(value).lower() for value in config.get("dependent_test_suffixes", []) if isinstance(value, str)
    }
    configured_test_suffixes.update("." + value for value in extensions)
    config["dependent_test_suffixes"] = sorted(configured_test_suffixes)

    config["include_roots"] = [
        _remap_path(str(value), live_root, candidate_root)
        for value in config.get("include_roots", [])
    ]
    sequences = config.get("translation_unit_include_roots") or {}
    if isinstance(sequences, dict):
        config["translation_unit_include_roots"] = {
            str(source): [
                [_remap_path(str(value), live_root, candidate_root) for value in sequence]
                for sequence in rows
                if isinstance(sequence, list)
            ]
            for source, rows in sequences.items()
            if isinstance(rows, list)
        }
    variants = config.get("translation_unit_include_variants") or {}
    if isinstance(variants, dict):
        mapped_variants: dict[str, list[dict[str, Any]]] = {}
        for source, rows in variants.items():
            if not isinstance(rows, list):
                continue
            mapped: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                for key in ("quote_roots", "angle_roots", "idirafter_roots"):
                    item[key] = [
                        _remap_path(str(value), live_root, candidate_root)
                        for value in row.get(key, [])
                    ]
                probe = row.get("compiler_probe")
                if isinstance(probe, dict):
                    item["compiler_probe"] = {
                        "argv": [
                            _remap_token(str(value), live_root, candidate_root)
                            for value in probe.get("argv", [])
                        ],
                        "directory": _remap_path(str(probe.get("directory", live_root)), live_root, candidate_root),
                    }
                mapped.append(item)
            mapped_variants[str(source)] = mapped
        config["translation_unit_include_variants"] = mapped_variants
    probes = config.get("translation_unit_compiler_probes") or {}
    if isinstance(probes, dict):
        mapped_probes: dict[str, list[dict[str, Any]]] = {}
        for source, rows in probes.items():
            if not isinstance(rows, list):
                continue
            mapped_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mapped_rows.append({
                    "argv": [
                        _remap_token(str(value), live_root, candidate_root)
                        for value in row.get("argv", [])
                    ],
                    "directory": _remap_path(str(row.get("directory", live_root)), live_root, candidate_root),
                })
            mapped_probes[str(source)] = mapped_rows
        config["translation_unit_compiler_probes"] = mapped_probes
    return config


class UniversalSourceSetMigration:
    """Workshop-owned add/delete/rename transaction for static source ecosystems.

    The candidate project tree lives inside the Workshop transaction directory. Agents
    never create topology directly in the governed live root. C-family topology and any
    build/config mutation fail closed because those require stronger external authority.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    @property
    def config(self):
        return self.engine.config

    def checkout(self, *, purpose: str) -> dict[str, Any]:
        purpose = purpose.strip()
        if len(purpose) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "source-set migration purpose must contain at least 12 characters")
        if self.engine.lease_path.exists():
            raise WorkshopError("LEASE_ACTIVE", "another Workshop transaction already owns the global lease")
        if not self.engine.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "seal the verified corpus before source-set migration")
        baseline_manifest = build_corpus_manifest(self.config)
        if not baseline_manifest.get("verified"):
            raise WorkshopError("CORPUS_NOT_VERIFIED", "source-set migration requires a verified live corpus", details=baseline_manifest.get("issues"))
        seal = read_json(self.engine.seal_path)
        if seal.get("package_sha256") != baseline_manifest["package_sha256"]:
            raise WorkshopError("SEAL_DRIFT", "live corpus differs from the trusted seal")
        created_at = utc_now()
        transaction_id = "TXN_" + package_hash({
            "kind": "universal_source_set",
            "baseline": baseline_manifest["package_sha256"],
            "purpose": purpose,
            "created_at": created_at,
        })[:24]
        root = self.engine._transaction_path(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self.engine._acquire_lease(transaction_id)
        root.mkdir(parents=True)
        state = {
            "schema": "runtime-sync-universal-source-set/v1",
            "transaction_kind": "universal_source_set",
            "transaction_id": transaction_id,
            "state": "CHECKOUT_STAGING",
            "created_at": created_at,
            "updated_at": created_at,
            "deterministic_updated_at": created_at,
            "purpose": purpose,
            "baseline_package_sha256": baseline_manifest["package_sha256"],
            "journal": [],
        }
        self.engine._save_transaction(
            root,
            state,
            event="UNIVERSAL_SOURCE_SET_CHECKOUT_STAGING",
            details={"live_project_unchanged": True},
        )
        try:
            candidate = root / "work" / "candidate"
            candidate.mkdir(parents=True)
            (root / "baseline").mkdir(parents=True)
            (root / "work" / "blueprints").mkdir(parents=True)
            (root / "work" / "managed").mkdir(parents=True)
            (root / "work" / "authority").mkdir(parents=True)
            (root / "work" / "machine").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            atomic_write_json(root / "baseline" / "manifest.json", baseline_manifest)
            copy_exact(self.config.kairos_database, root / "baseline" / "kairos.db")
            _, _, _, _, iter_project_files, _, _, _, _, _ = _load_universal()
            tool_owned_roots = (
                self.config.kairos_workspace,
                self.config.machine_root,
                self.config.state_directory,
                self.config.transaction_directory,
                self.config.blueprint_root,
                self.config.managed_blueprint_root,
            )
            for source in iter_project_files(
                self.config.codebase_root,
                excluded_roots=tool_owned_roots,
            ):
                relative = source.relative_to(self.config.codebase_root).as_posix()
                _copy_tree_file(source, candidate / relative)
            baseline_files = _file_inventory(candidate)
            atomic_write_json(root / "baseline" / "files.json", baseline_files)
            state["state"] = "CHECKED_OUT"
            self.engine._save_transaction(
                root,
                state,
                event="UNIVERSAL_SOURCE_SET_CHECKOUT",
                details={
                    "candidate": candidate.as_posix(),
                    "excluded_tool_roots": [value.resolve().as_posix() for value in tool_owned_roots],
                },
            )
        except BaseException as exc:
            state["state"] = "CHECKOUT_FAILED"
            state["failure"] = {"code": type(exc).__name__, "message": str(exc)}
            try:
                self.engine._save_transaction(
                    root, state, event="UNIVERSAL_SOURCE_SET_CHECKOUT_FAILED", details=state["failure"]
                )
            finally:
                if self.engine.lease_path.exists():
                    self.engine._release_lease(transaction_id)
            raise
        return {
            "schema": "runtime-sync-universal-source-set-checkout/v1",
            "transaction_id": transaction_id,
            "state": "CHECKED_OUT",
            "candidate_root": str(root / "work" / "candidate"),
            "boundary": "Only this candidate tree may be edited; the governed live project remains sealed and read-only until verified apply.",
        }

    def recover_orphan_checkout(
        self,
        transaction_id: str,
        *,
        expected_sealed_package_sha256: str,
    ) -> dict[str, Any]:
        """Release only a proven pre-apply orphan created during source-set checkout."""
        root = self.engine._transaction_path(transaction_id)
        self.engine._require_lease(transaction_id)
        state_path = root / "state.json"
        state = read_json(state_path) if state_path.is_file() else None
        if state is not None:
            if state.get("transaction_kind") != "universal_source_set":
                raise WorkshopError("TRANSACTION_KIND_INVALID", "orphan lease is not a universal source-set checkout")
            if state.get("state") not in {"CHECKOUT_STAGING", "CHECKOUT_FAILED"}:
                raise WorkshopError(
                    "ORPHAN_RECOVERY_STATE_INVALID",
                    f"orphan checkout recovery is not allowed from {state.get('state')}",
                )
        if (root / "rollback" / "targets.json").exists():
            raise WorkshopError(
                "ORPHAN_RECOVERY_APPLY_EVIDENCE",
                "rollback targets exist; use normal recovery because apply may have begun",
            )
        expected = expected_sealed_package_sha256.strip().lower()
        if len(expected) != 64 or any(value not in "0123456789abcdef" for value in expected):
            raise WorkshopError("PACKAGE_HASH_INVALID", "expected sealed package SHA-256 must be 64 hexadecimal characters")
        seal = read_json(self.engine.seal_path)
        current = build_corpus_manifest(self.config)
        if not current.get("verified"):
            raise WorkshopError("ORPHAN_RECOVERY_CORPUS_INVALID", "live corpus must verify before orphan lease recovery", details=current.get("issues"))
        if str(seal.get("package_sha256", "")).lower() != expected:
            raise WorkshopError("ORPHAN_RECOVERY_SEAL_MISMATCH", "operator-confirmed package differs from Workshop seal")
        if str(current.get("package_sha256", "")).lower() != expected:
            raise WorkshopError(
                "ORPHAN_RECOVERY_LIVE_DRIFT",
                "live corpus differs from the sealed package; orphan checkout recovery refuses to release the lease",
                details={"expected": expected, "actual": current.get("package_sha256")},
            )
        root.mkdir(parents=True, exist_ok=True)
        recovered = state if isinstance(state, dict) else {
            "schema": "runtime-sync-universal-source-set/v1",
            "transaction_kind": "universal_source_set",
            "transaction_id": transaction_id,
            "created_at": utc_now(),
            "purpose": "Recovered historical orphan created before source-set state materialization",
            "baseline_package_sha256": expected,
            "journal": [],
        }
        recovered["state"] = "ABORTED_ORPHANED_CHECKOUT"
        recovered["orphan_recovery"] = {
            "expected_sealed_package_sha256": expected,
            "live_package_sha256": current["package_sha256"],
            "live_corpus_verified": True,
            "rollback_targets_absent": True,
            "candidate_preserved": (root / "work" / "candidate").exists(),
        }
        self.engine._save_transaction(
            root,
            recovered,
            event="UNIVERSAL_SOURCE_SET_ORPHAN_RECOVERED",
            details=recovered["orphan_recovery"],
        )
        self.engine._release_lease(transaction_id)
        return {
            "schema": "runtime-sync-universal-source-set-orphan-recovery/v1",
            "transaction_id": transaction_id,
            "state": recovered["state"],
            **recovered["orphan_recovery"],
        }

    def _candidate_survey(self, candidate_root: Path, baseline_manifest: dict[str, Any]):
        (
            excluded,
            UniversalSource,
            UniversalSurvey,
            suffix_to_spec,
            iter_project_files,
            source_sha,
            _,
            _,
            _,
            _,
        ) = _load_universal()
        ecosystem_map = self.config.raw.get("source_ecosystems") or {}
        if not isinstance(ecosystem_map, dict) or not ecosystem_map:
            raise WorkshopError("UNIVERSAL_SOURCE_ECOSYSTEMS_MISSING", "source-set migration requires universal source_ecosystems authority")
        c_sources = {str(path) for path, ecosystem in ecosystem_map.items() if str(ecosystem) == "c_family"}
        sources: list[Any] = []
        markers: dict[str, set[str]] = {}
        marker_specs = {spec.name: spec for spec in suffix_to_spec.values()}

        for path in iter_project_files(candidate_root):
            relative = path.relative_to(candidate_root).as_posix()
            suffix = path.suffix.casefold()
            spec = suffix_to_spec.get(suffix)
            name = path.name.casefold()
            for marker_spec in marker_specs.values():
                if any(name == marker.casefold() for marker in marker_spec.markers if marker != "gemspec"):
                    markers.setdefault(marker_spec.name, set()).add(relative)
            if name.endswith(".gemspec"):
                markers.setdefault("ruby", set()).add(relative)
            if suffix in {".csproj", ".fsproj", ".vbproj", ".sln"}:
                markers.setdefault("dotnet", set()).add(relative)
            if spec is None or spec.name == "c_family":
                continue
            raw = path.read_bytes()
            sources.append(UniversalSource(relative, path, spec.name, source_sha(raw), len(raw)))

        for relative in sorted(c_sources):
            candidate = candidate_root / relative
            if not candidate.is_file():
                raise WorkshopError("C_FAMILY_TOPOLOGY_MIGRATION_REQUIRED", f"source-set migration may not remove C-family source: {relative}")
            baseline = baseline_manifest["records"].get(relative) or {}
            expected = str((baseline.get("source") or {}).get("sha256", ""))
            actual = sha256_bytes(candidate.read_bytes())
            if actual != expected:
                raise WorkshopError("C_FAMILY_NORMAL_TRANSACTION_REQUIRED", f"C-family byte changes belong to an ordinary Workshop transaction: {relative}")
            raw = candidate.read_bytes()
            sources.append(UniversalSource(relative, candidate, "c_family", source_sha(raw), len(raw)))

        if not sources:
            raise WorkshopError("SOURCE_MEMBERSHIP_EMPTY", "candidate contains no supported governed source files")
        return UniversalSurvey(
            project_root=candidate_root,
            sources=tuple(sorted(sources, key=lambda item: item.relative_path.casefold())),
            ecosystems=tuple(sorted({item.ecosystem for item in sources})),
            markers={key: tuple(sorted(value)) for key, value in markers.items()},
            excluded_directories=tuple(sorted(excluded)),
        )

    def prepare(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "universal_source_set":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not a universal source-set migration")
        if state["state"] not in {"CHECKED_OUT", "PREPARED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"source-set prepare is not allowed from {state['state']}")
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        self.engine._assert_live_baseline(state)
        candidate_root = root / "work" / "candidate"
        baseline_files = read_json(root / "baseline" / "files.json")
        candidate_files = _file_inventory(candidate_root)
        baseline_paths = set(baseline_files)
        candidate_paths_all = set(candidate_files)
        added_files = sorted(candidate_paths_all - baseline_paths)
        removed_files = sorted(baseline_paths - candidate_paths_all)
        modified_files = sorted(
            relative for relative in baseline_paths & candidate_paths_all
            if baseline_files[relative]["sha256"] != candidate_files[relative]["sha256"]
        )
        _, _, _, suffix_to_spec, _, _, filename_for, render_markdown_corpus, membership_authority, render_source_index = _load_universal()
        forbidden: list[dict[str, str]] = []
        for relative in sorted(set(added_files) | set(removed_files) | set(modified_files)):
            spec = suffix_to_spec.get(Path(relative).suffix.casefold())
            if spec is None:
                forbidden.append({"path": relative, "reason": "non-source/build/config mutation requires a dedicated Workshop authority adapter"})
            elif spec.name == "c_family":
                forbidden.append({"path": relative, "reason": "C-family topology/content requires compiler-backed authority or ordinary C-family Workshop flow"})
        if forbidden:
            raise WorkshopError(
                "UNIVERSAL_SOURCE_SET_FORBIDDEN_MUTATION",
                "source-set candidate changed files outside the static-source authority boundary",
                details=forbidden,
            )

        survey = self._candidate_survey(candidate_root, baseline_manifest)
        candidate_sources = [source.relative_path for source in survey.sources]
        baseline_sources = [str(value) for value in baseline_manifest["authority"]["translation_units"]]
        added_sources = sorted(set(candidate_sources) - set(baseline_sources))
        removed_sources = sorted(set(baseline_sources) - set(candidate_sources))
        if not added_sources and not removed_sources:
            raise WorkshopError("SOURCE_SET_MIGRATION_NOT_REQUIRED", "candidate has no source membership change; use an ordinary Workshop transaction")
        c_membership = {
            path for path, ecosystem in (self.config.raw.get("source_ecosystems") or {}).items()
            if str(ecosystem) == "c_family"
        }
        if (set(added_sources) | set(removed_sources)) & c_membership:
            raise WorkshopError("C_FAMILY_TOPOLOGY_MIGRATION_REQUIRED", "universal source-set migration cannot alter C-family membership")

        updated_existing = sorted(
            relative for relative in set(candidate_sources) & set(baseline_sources)
            if relative in modified_files
        )
        generated_docs = render_markdown_corpus(
            survey,
            workspace_id=str(read_json(self.config.kairos_workspace / ".kairos" / "config.json").get("workspace_id", "")),
            goal_id=str(read_json(self.config.kairos_workspace / "current.json").get("active_goal", "")),
            milestone_id=str(read_json(self.config.kairos_workspace / "current.json").get("active_milestone", "")),
            task_id=str(read_json(self.config.kairos_workspace / "current.json").get("active_task", "")),
            updated_at=state["deterministic_updated_at"],
        )
        prepared_active: dict[str, str] = {}
        for relative in updated_existing:
            baseline_record = baseline_manifest["records"][relative]
            name = baseline_record["blueprint"]["filename"]
            baseline_doc = parse_blueprint(self.config.blueprint_root / name)
            candidate_file = candidate_root / relative
            rendered = regenerate_mechanical_layers(
                baseline_doc,
                candidate_file,
                None,
                revision=baseline_doc.revision + 1,
                updated_at=state["deterministic_updated_at"],
            )
            atomic_write_bytes(root / "work" / "blueprints" / name, rendered.encode("utf-8"))
            atomic_write_bytes(root / "work" / "managed" / name, rendered.encode("utf-8"))
            prepared_active[relative] = name
        for relative in added_sources:
            name = filename_for(relative)
            raw = generated_docs[f"code/{name}"].encode("utf-8")
            atomic_write_bytes(root / "work" / "blueprints" / name, raw)
            atomic_write_bytes(root / "work" / "managed" / name, raw)
            prepared_active[relative] = name

        tombstones: dict[str, str] = {}
        for relative in removed_sources:
            record = baseline_manifest["records"][relative]
            if str((self.config.raw.get("source_ecosystems") or {}).get(relative, "")) == "c_family":
                raise WorkshopError("C_FAMILY_TOPOLOGY_MIGRATION_REQUIRED", f"cannot remove C-family source through static migration: {relative}")
            name = record["blueprint"]["filename"]
            tombstone = _retire_document(
                self.config.managed_blueprint_root / name,
                updated_at=state["deterministic_updated_at"],
            )
            atomic_write_bytes(root / "work" / "managed" / name, tombstone.encode("utf-8"))
            tombstones[relative] = name

        include_owners = {
            str(key): [str(value) for value in values]
            for key, values in baseline_manifest["authority"].get("include_owners", {}).items()
        }
        generated_index = render_source_index(
            survey,
            header_owners=include_owners,
            workspace_id=str(read_json(self.config.kairos_workspace / ".kairos" / "config.json").get("workspace_id", "")),
            updated_at=state["deterministic_updated_at"],
        )
        live_index = self.config.dataflow_index.read_text(encoding="utf-8")
        prepared_index = _advance_generated_document(
            generated_index,
            live_index,
            updated_at=state["deterministic_updated_at"],
            path=self.config.dataflow_index,
        )
        relative_index = self.config.dataflow_index.resolve().relative_to(self.config.kairos_workspace.resolve()).as_posix()
        atomic_write_bytes(root / "work" / "authority" / relative_index, prepared_index.encode("utf-8"))

        external_final = root / "work" / "external-final"
        if external_final.exists():
            shutil.rmtree(external_final)
        external_final.mkdir(parents=True)
        for relative in candidate_sources:
            if relative in prepared_active:
                source = root / "work" / "blueprints" / prepared_active[relative]
                name = prepared_active[relative]
            else:
                record = baseline_manifest["records"][relative]
                name = record["blueprint"]["filename"]
                source = self.config.blueprint_root / name
            copy_exact(source, external_final / name)
        for relative in baseline_manifest["authority"].get("include_headers", []):
            record = baseline_manifest["records"][relative]
            name = record["blueprint"]["filename"]
            copy_exact(self.config.blueprint_root / name, external_final / name)

        source_ecosystems = {source.relative_path: source.ecosystem for source in survey.sources}
        final_raw = json.loads(json.dumps(self.config.raw))
        final_raw["expected_translation_units"] = len(candidate_sources)
        final_raw["source_ecosystems"] = dict(sorted(source_ecosystems.items()))
        extensions = sorted({Path(value).suffix.lower().lstrip(".") for value in candidate_sources})
        final_raw["source_authority"] = dict(final_raw.get("source_authority") or {})
        final_raw["source_authority"]["translation_unit_extensions"] = extensions
        test_suffixes = {str(value).lower() for value in final_raw.get("dependent_test_suffixes", [])}
        test_suffixes.update("." + value for value in extensions)
        final_raw["dependent_test_suffixes"] = sorted(test_suffixes)

        machine = root / "work" / "machine"
        machine.mkdir(parents=True, exist_ok=True)
        for relative in self.config.machine_authority_files:
            live = self.config.machine_root / relative
            if live.is_file():
                copy_exact(live, machine / relative)
        atomic_write_json(machine / "workshop.config.json", final_raw)
        membership_relative = "project-intake/PROJECT_SOURCE_AUTHORITY.cmake"
        atomic_write_bytes(
            machine / membership_relative,
            membership_authority(candidate_sources).encode("utf-8"),
        )

        state.update({
            "state": "PREPARED",
            "candidate_sources": candidate_sources,
            "source_ecosystems": source_ecosystems,
            "added_sources": added_sources,
            "removed_sources": removed_sources,
            "updated_existing": updated_existing,
            "renames": _infer_renames(removed_sources, added_sources, baseline_files, candidate_root),
            "prepared_active": prepared_active,
            "tombstones": tombstones,
            "authority_documents": [relative_index],
            "membership_authority": membership_relative,
        })
        self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_PREPARED", details={
            "added": added_sources,
            "removed": removed_sources,
            "updated_existing": updated_existing,
            "renames": state["renames"],
        })
        return {
            "schema": "runtime-sync-universal-source-set-prepare/v1",
            "transaction_id": transaction_id,
            "state": "PREPARED",
            "added_sources": added_sources,
            "removed_sources": removed_sources,
            "updated_existing": updated_existing,
            "renames": state["renames"],
        }

    def _shadow_workspace(self, root: Path) -> Path:
        shadow = root / "shadow"
        if shadow.exists():
            shutil.rmtree(shadow)
        (shadow / ".kairos" / "events").mkdir(parents=True)
        (shadow / ".kairos" / "receipts").mkdir(parents=True)
        workspace_config = read_json(self.config.kairos_workspace / ".kairos" / "config.json")
        copy_exact(self.config.kairos_workspace / ".kairos" / "config.json", shadow / ".kairos" / "config.json")
        copy_exact(root / "baseline" / "kairos.db", shadow / ".kairos" / "kairos.db")
        for root_name in workspace_config.get("document_roots", []):
            source_root = self.config.kairos_workspace / str(root_name)
            if source_root.is_dir():
                for source in source_root.rglob("*"):
                    if source.is_file():
                        copy_exact(source, shadow / source.relative_to(self.config.kairos_workspace))
        goal_root = self.config.kairos_workspace / "goals"
        if goal_root.is_dir():
            for source in goal_root.glob("*.json"):
                copy_exact(source, shadow / source.relative_to(self.config.kairos_workspace))
        for name in workspace_config.get("canonical_files", []):
            source = self.config.kairos_workspace / str(name)
            if source.is_file():
                copy_exact(source, shadow / str(name))
        current = self.config.kairos_workspace / "current.json"
        if current.is_file():
            copy_exact(current, shadow / "current.json")
        return shadow

    def _work_package(self, root: Path) -> dict[str, Any]:
        rows = []
        for role, directory in (
            ("candidate", root / "work" / "candidate"),
            ("blueprints", root / "work" / "blueprints"),
            ("managed", root / "work" / "managed"),
            ("authority", root / "work" / "authority"),
            ("machine", root / "work" / "machine"),
            ("external-final", root / "work" / "external-final"),
        ):
            if not directory.exists():
                continue
            for path in sorted(value for value in directory.rglob("*") if value.is_file()):
                raw = path.read_bytes()
                rows.append({
                    "role": role,
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_bytes(raw),
                    "size": len(raw),
                })
        return {
            "schema": "runtime-sync-universal-source-set-work-package/v1",
            "file_count": len(rows),
            "byte_count": sum(row["size"] for row in rows),
            "package_sha256": package_hash(rows),
            "files": rows,
        }

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "universal_source_set":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not a universal source-set migration")
        if state["state"] not in {"PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"source-set verify is not allowed from {state['state']}")
        self.engine._assert_live_baseline(state)
        shadow = self._shadow_workspace(root)
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
                raise WorkshopError("SHADOW_PROMOTION_FAILED", f"source-set shadow promotion failed: {relative}", details=receipt)
            receipts.append(receipt)

        machine_verify = root / "work" / "machine-verify"
        if machine_verify.exists():
            shutil.rmtree(machine_verify)
        machine_verify.mkdir(parents=True)
        for relative in self.config.machine_authority_files:
            prepared = root / "work" / "machine" / relative
            if prepared.is_file():
                copy_exact(prepared, machine_verify / relative)
        candidate_root = root / "work" / "candidate"
        external_final = root / "work" / "external-final"
        verify_config = _candidate_config(
            read_json(root / "work" / "machine" / "workshop.config.json"),
            live_root=self.config.codebase_root,
            candidate_root=candidate_root,
            machine_root=machine_verify,
            shadow=shadow,
            external_root=external_final,
            source_paths=state["candidate_sources"],
            ecosystems=state["source_ecosystems"],
        )
        atomic_write_json(machine_verify / "workshop.config.json", verify_config)
        candidate_manifest = build_corpus_manifest(load_config(machine_verify / "workshop.config.json"), verify_database=True)
        if not candidate_manifest.get("verified"):
            raise WorkshopError("UNIVERSAL_SOURCE_SET_SHADOW_INVALID", "candidate source-set corpus failed Workshop verification", details=candidate_manifest.get("issues"))
        work_package = self._work_package(root)
        state["state"] = "SHADOW_VERIFIED"
        state["verified_work_package_sha256"] = work_package["package_sha256"]
        state["shadow"] = {
            "workspace": shadow.as_posix(),
            "candidate_package_sha256": candidate_manifest["package_sha256"],
            "receipts": receipts,
        }
        self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_SHADOW_VERIFIED", details=state["shadow"])
        return {
            "schema": "runtime-sync-universal-source-set-verify/v1",
            "transaction_id": transaction_id,
            "state": "SHADOW_VERIFIED",
            "candidate_package_sha256": candidate_manifest["package_sha256"],
            "work_package_sha256": work_package["package_sha256"],
        }

    def _backup(self, root: Path, live: Path, role: str, rows: list[dict[str, Any]]) -> None:
        existed = live.is_file()
        backup = root / "rollback" / role / f"{sha256_bytes(str(live).encode())[:16]}_{live.name}"
        if existed:
            copy_exact(live, backup)
        rows.append({
            "live": live.as_posix(),
            "backup": backup.as_posix(),
            "existed": existed,
            "sha256": sha256_bytes(live.read_bytes()) if existed else None,
        })

    def _rollback(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in reversed(rows):
            live = Path(row["live"])
            try:
                if row["existed"]:
                    copy_exact(Path(row["backup"]), live)
                    restored = sha256_bytes(live.read_bytes()) == row["sha256"]
                else:
                    if live.is_file():
                        live.unlink()
                    restored = not live.exists()
                result.append({"path": live.as_posix(), "restored": restored})
            except Exception as exc:
                result.append({"path": live.as_posix(), "restored": False, "error": str(exc)})
        return result

    def apply(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "universal_source_set":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not a universal source-set migration")
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"source-set apply requires SHADOW_VERIFIED, found {state['state']}")
        self.engine._assert_live_baseline(state)
        work_package = self._work_package(root)
        if work_package["package_sha256"] != state.get("verified_work_package_sha256"):
            raise WorkshopError("WORK_CHANGED_AFTER_VERIFY", "source-set work package changed after shadow verification")
        baseline_manifest = read_json(root / "baseline" / "manifest.json")
        changed_managed = sorted({
            *(f"code/{name}" for name in state.get("prepared_active", {}).values()),
            *(f"code/{name}" for name in state.get("tombstones", {}).values()),
            *state.get("authority_documents", []),
        })
        governance, heartbeat = self.engine._import_kairos()
        permit = governance.issue_external_edit_permit(
            self.config.kairos_workspace,
            paths=changed_managed,
            reason=f"Universal source-set Workshop transaction {transaction_id}: {state['purpose']}",
            ttl_seconds=1800,
        )

        affected_sources = sorted(set(state["added_sources"]) | set(state["removed_sources"]) | set(state["updated_existing"]))
        rollback_rows: list[dict[str, Any]] = []
        for relative in affected_sources:
            self._backup(root, self.config.codebase_root / relative, "runtime", rollback_rows)
        for relative, name in state.get("prepared_active", {}).items():
            self._backup(root, self.config.blueprint_root / name, "external", rollback_rows)
            self._backup(root, self.config.managed_blueprint_root / name, "managed", rollback_rows)
        for relative in state.get("removed_sources", []):
            name = baseline_manifest["records"][relative]["blueprint"]["filename"]
            self._backup(root, self.config.blueprint_root / name, "external", rollback_rows)
            self._backup(root, self.config.managed_blueprint_root / name, "managed", rollback_rows)
        for relative in state.get("authority_documents", []):
            self._backup(root, self.config.kairos_workspace / relative, "authority", rollback_rows)
        for relative in self.config.machine_authority_files:
            self._backup(root, self.config.machine_root / relative, "machine", rollback_rows)
        atomic_write_json(root / "rollback" / "targets.json", rollback_rows)

        state["state"] = "APPLYING"
        state["permit"] = permit
        self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_APPLY_STARTED")
        heartbeat_started = False
        old_config_path = self.config.config_path
        try:
            candidate_root = root / "work" / "candidate"
            for relative in sorted(set(state["added_sources"]) | set(state["updated_existing"])):
                copy_exact(candidate_root / relative, self.config.codebase_root / relative)
            for relative in state["removed_sources"]:
                live = self.config.codebase_root / relative
                if live.is_file():
                    live.unlink()

            for relative, name in state.get("prepared_active", {}).items():
                copy_exact(root / "work" / "blueprints" / name, self.config.blueprint_root / name)
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for relative in state["removed_sources"]:
                name = baseline_manifest["records"][relative]["blueprint"]["filename"]
                external = self.config.blueprint_root / name
                if external.is_file():
                    external.unlink()
                copy_exact(root / "work" / "managed" / name, self.config.managed_blueprint_root / name)
            for relative in state.get("authority_documents", []):
                copy_exact(root / "work" / "authority" / relative, self.config.kairos_workspace / relative)
            for relative in self.config.machine_authority_files:
                prepared = root / "work" / "machine" / relative
                if not prepared.is_file():
                    raise WorkshopError("UNIVERSAL_SOURCE_SET_MACHINE_FILE_MISSING", f"prepared machine authority missing: {relative}")
                copy_exact(prepared, self.config.machine_root / relative)

            self.engine.config = load_config(old_config_path)
            self.engine.scientific_gate = ScientificParityGate(self.engine.config)
            heartbeat_started = True
            heartbeat_receipt = heartbeat.run_heartbeat(
                self.engine.config.kairos_workspace,
                requested_mode="verify",
                changed_paths=changed_managed,
                use_reconciliation=True,
                trigger=f"runtime-sync-universal-source-set:{transaction_id}",
            )
            if not heartbeat_receipt.get("verified"):
                raise WorkshopError("HEARTBEAT_NOT_VERIFIED", "source-set heartbeat did not verify", details=heartbeat_receipt)
            post = build_corpus_manifest(self.engine.config)
            if not post.get("verified"):
                raise WorkshopError("POSTCHECK_FAILED", "source-set postcheck failed", details=post.get("issues"))
            if set(post["authority"]["translation_units"]) != set(state["candidate_sources"]):
                raise WorkshopError("POSTCHECK_AUTHORITY_MISMATCH", "postcheck source membership differs from verified candidate")
            for relative in state["candidate_sources"]:
                if (self.engine.config.codebase_root / relative).read_bytes() != (candidate_root / relative).read_bytes():
                    raise WorkshopError("POSTCHECK_SOURCE_MISMATCH", f"live source differs from verified candidate: {relative}")
            mirror = self.engine._mirror(post)
            seal = {
                "schema": "runtime-sync-seal/v1",
                "created_at": utc_now(),
                "package_sha256": post["package_sha256"],
                "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(self.engine.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"),
                "counts": post["counts"],
                "transaction_id": transaction_id,
                "universal_source_set_migration": True,
            }
            atomic_write_json(self.engine.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["heartbeat"] = heartbeat_receipt
            self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_POSTCHECK_VERIFIED", details={"package_sha256": post["package_sha256"]})
            self.engine._release_lease(transaction_id)
            receipt = {
                "schema": "runtime-sync-universal-source-set-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "baseline_package_sha256": state["baseline_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "heartbeat": heartbeat_receipt,
                "bit_exact": True,
                "added_sources": state["added_sources"],
                "removed_sources": state["removed_sources"],
                "renames": state["renames"],
            }
            self.engine._write_receipt("universal-source-set-postcheck", receipt)
            return receipt
        except Exception as exc:
            if heartbeat_started:
                state["state"] = "RECOVERY_REQUIRED"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_RECOVERY_REQUIRED", details=state["failure"])
                raise WorkshopError("RECOVERY_REQUIRED", "source-set apply crossed the heartbeat boundary and failed; lease retained", details=state["failure"]) from exc
            rollback = self._rollback(rollback_rows)
            self.engine.config = load_config(old_config_path)
            self.engine.scientific_gate = ScientificParityGate(self.engine.config)
            if all(row.get("restored") for row in rollback):
                state["state"] = "ROLLED_BACK"
                self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_APPLY_ROLLED_BACK", details=rollback)
                self.engine._release_lease(transaction_id)
                raise WorkshopError("APPLY_ROLLED_BACK", "source-set apply failed before heartbeat and all targets were restored", details=rollback) from exc
            state["state"] = "RECOVERY_REQUIRED"
            self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_ROLLBACK_INCOMPLETE", details=rollback)
            raise WorkshopError("RECOVERY_REQUIRED", "source-set rollback was incomplete; lease retained", details=rollback) from exc

    def abort(self, transaction_id: str) -> dict[str, Any]:
        root, state = self.engine._load_transaction(transaction_id)
        self.engine._require_lease(transaction_id)
        if state.get("transaction_kind") != "universal_source_set":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not a universal source-set migration")
        if state["state"] not in {"CHECKED_OUT", "PREPARED", "SHADOW_VERIFIED"}:
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"source-set abort is not allowed from {state['state']}")
        self.engine._assert_live_baseline(state)
        state["state"] = "ABORTED"
        self.engine._save_transaction(root, state, event="UNIVERSAL_SOURCE_SET_ABORTED")
        self.engine._release_lease(transaction_id)
        return {"schema": "runtime-sync-universal-source-set-abort/v1", "transaction_id": transaction_id, "state": "ABORTED"}

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        _, state = self.engine._load_transaction(transaction_id)
        if state.get("transaction_kind") != "universal_source_set":
            raise WorkshopError("TRANSACTION_KIND_INVALID", "transaction is not a universal source-set migration")
        return state
