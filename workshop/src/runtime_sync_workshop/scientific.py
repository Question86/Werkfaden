"""Optional, project-supplied parity extension for the Workshop.

The canonical Workshop deliberately ships without a scientific or build policy.  A
project may provide an adapter later, but an unbound framework must remain usable for
mechanical corpus checks and must never invent a domain, fixture, runner, or result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .corpus import WorkshopConfig
from .context_documents import ContextDocuments
from .util import WorkshopError, package_hash, read_json, sha256_bytes


PARITY_SCHEMA = "runtime-sync-parity-config/v1"


def transaction_work_manifest(config: WorkshopConfig, root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic hash of the exact files selected for a transaction."""

    baseline_manifest = read_json(root / "baseline" / "manifest.json")
    rows: dict[str, dict[str, Any]] = {}
    for source in state.get("sources", []):
        record = baseline_manifest.get("records", {}).get(source)
        if not isinstance(record, dict):
            raise WorkshopError("WORK_SCOPE_INVALID", f"transaction source is absent from its baseline: {source}")
        paths: list[tuple[str, str, Path]] = [("source", source, root / "work" / source)]
        header = record.get("header")
        if isinstance(header, dict) and header.get("path"):
            header_live = Path(str(header["path"]))
            try:
                header_relative = header_live.resolve().relative_to(config.codebase_root.resolve()).as_posix()
            except ValueError as exc:
                raise WorkshopError("PATH_ESCAPE", f"transaction header escapes the configured codebase: {header_live}") from exc
            paths.append(("header", header_relative, root / "work" / header_relative))
        blueprint = record.get("blueprint")
        if not isinstance(blueprint, dict) or not isinstance(blueprint.get("filename"), str):
            raise WorkshopError("WORK_SCOPE_INVALID", f"transaction blueprint mapping is invalid: {source}")
        name = str(blueprint["filename"])
        paths.extend(
            (
                ("blueprint", f"blueprints/{name}", root / "work" / "blueprints" / name),
                ("managed", f"managed/{name}", root / "work" / "managed" / name),
            )
        )
        for role, relative, path in paths:
            if not path.is_file():
                raise WorkshopError("WORK_FILE_MISSING", f"transaction work file is missing: {path}")
            raw = path.read_bytes()
            rows[f"{role}:{relative}"] = {
                "role": role,
                "path": relative,
                "sha256": sha256_bytes(raw),
                "size": len(raw),
            }
    for header in state.get("topology_blueprints", []):
        record = baseline_manifest.get("records", {}).get(header)
        if not isinstance(record, dict):
            raise WorkshopError("WORK_SCOPE_INVALID", f"topology header is absent from its baseline: {header}")
        blueprint = record.get("blueprint") or {}
        name = str(blueprint.get("filename", ""))
        if not name:
            raise WorkshopError("WORK_SCOPE_INVALID", f"topology header blueprint mapping is invalid: {header}")
        for role, relative, path in (
            ("topology-blueprint", f"blueprints/{name}", root / "work" / "blueprints" / name),
            ("topology-managed", f"managed/{name}", root / "work" / "managed" / name),
        ):
            if not path.is_file():
                raise WorkshopError("WORK_FILE_MISSING", f"topology blueprint work file is missing: {path}")
            raw = path.read_bytes()
            rows[f"{role}:{relative}"] = {
                "role": role,
                "path": relative,
                "sha256": sha256_bytes(raw),
                "size": len(raw),
            }
    for relative in state.get("dependent_tests", []):
        path = root / "work" / str(relative)
        if not path.is_file():
            raise WorkshopError("WORK_FILE_MISSING", f"dependent test work file is missing: {path}")
        raw = path.read_bytes()
        rows[f"dependent-test:{relative}"] = {
            "role": "dependent-test",
            "path": str(relative),
            "sha256": sha256_bytes(raw),
            "size": len(raw),
        }
    for relative in state.get("authority_documents", []):
        path = root / "work" / "authority" / str(relative)
        if not path.is_file():
            raise WorkshopError("WORK_FILE_MISSING", f"authority document work file is missing: {path}")
        raw = path.read_bytes()
        rows[f"authority:{relative}"] = {
            "role": "authority",
            "path": str(relative),
            "sha256": sha256_bytes(raw),
            "size": len(raw),
        }
    for relative in state.get("machine_authority_files", []):
        path = root / "work" / "machine" / str(relative)
        if not path.is_file():
            raise WorkshopError("WORK_FILE_MISSING", f"machine authority work file is missing: {path}")
        raw = path.read_bytes()
        rows[f"machine-authority:{relative}"] = {
            "role": "machine-authority",
            "path": str(relative),
            "sha256": sha256_bytes(raw),
            "size": len(raw),
        }
    for role, relative, path in ContextDocuments(config).work_files(root, state):
        raw = path.read_bytes()
        rows[f"{role}:{relative}"] = {
            "role": role, "path": relative, "sha256": sha256_bytes(raw), "size": len(raw),
        }
    if state.get("schema") == "runtime-sync-transaction/v1":
        # Reviews are evidence about these exact bytes, not an editable sidecar
        # that can silently change after prepare or shadow verification.
        review = root / "review" / "metadata_review.json"
        if not review.is_file() or review.is_symlink() or review.stat().st_nlink != 1:
            raise WorkshopError("WORK_FILE_INVALID", "metadata review must be a regular transaction file")
        raw = review.read_bytes()
        rows["review:metadata_review.json"] = {
            "role": "review", "path": "review/metadata_review.json",
            "sha256": sha256_bytes(raw), "size": len(raw),
        }
        audit = root / "review" / "metadata_audit.json"
        if audit.exists() or audit.is_symlink():
            if not audit.is_file() or audit.is_symlink() or audit.stat().st_nlink != 1:
                raise WorkshopError("WORK_FILE_INVALID", "metadata audit must be a regular transaction file")
            raw = audit.read_bytes()
            rows["review:metadata_audit.json"] = {
                "role": "review", "path": "review/metadata_audit.json",
                "sha256": sha256_bytes(raw), "size": len(raw),
            }
    from .corpus import framework_projection_files
    for role, relative, path in framework_projection_files(config, root, state):
        raw = path.read_bytes()
        rows[f"{role}:{relative}"] = dict(role=role, path=relative, sha256=sha256_bytes(raw), size=len(raw))
    ordered = [rows[key] for key in sorted(rows)]
    return {
        "schema": "runtime-sync-work-package/v1",
        "file_count": len(ordered),
        "byte_count": sum(row["size"] for row in ordered),
        "package_sha256": package_hash(ordered),
        "files": ordered,
    }


class ScientificParityGate:
    """Fail-closed compatibility shell for an optional parity adapter.

    The generic framework has no build command, input fixture, scientific domain, or
    external authority.  The shell reports that fact and refuses parity commands until a
    separately supplied adapter is bound under the generic ``parity_gate`` key.
    """

    def __init__(self, config: WorkshopConfig) -> None:
        self.workshop_config = config
        raw = config.raw.get("parity_gate")
        if raw is None:
            self.config: dict[str, Any] | None = None
        elif isinstance(raw, dict) and raw.get("schema") == PARITY_SCHEMA:
            self.config = dict(raw)
        else:
            raise WorkshopError("PARITY_CONFIG_INVALID", f"parity_gate must use schema {PARITY_SCHEMA}")

    def preflight(self) -> dict[str, Any]:
        if self.config is None:
            return {
                "schema": "runtime-sync-parity-preflight/v1",
                "configured": False,
                "verified": True,
            }
        return {
            "schema": "runtime-sync-parity-preflight/v1",
            "configured": True,
            "verified": False,
            "reason": "adapter_required",
        }

    def _require_adapter(self) -> None:
        if self.config is None:
            raise WorkshopError(
                "PARITY_GATE_NOT_CONFIGURED",
                "no project parity adapter is bound; mechanical Workshop verification remains available",
            )
        raise WorkshopError(
            "PARITY_GATE_ADAPTER_REQUIRED",
            "parity_gate is declared but this canonical package contains no project-specific adapter",
        )

    def baseline(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        self._require_adapter()
        raise AssertionError("unreachable")

    def reproducibility(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        self._require_adapter()
        raise AssertionError("unreachable")

    def stage_runner_authority(
        self,
        root: Path,
        state: dict[str, Any],
        reproducibility_state: dict[str, Any],
        *,
        decision_id: str,
        expected_runner_sha256: str,
    ) -> dict[str, Any]:
        self._require_adapter()
        raise AssertionError("unreachable")

    def verify_candidate(
        self,
        root: Path,
        state: dict[str, Any],
        baseline_state: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_adapter()
        raise AssertionError("unreachable")

    def assert_apply_ready(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        if self.config is None:
            return {"required": False, "reason": "not_configured"}
        self._require_adapter()
        raise AssertionError("unreachable")
