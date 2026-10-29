from __future__ import annotations

from ._core_contract import EngineCoreContract

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .corpus import (
    build_corpus_manifest,
    load_config,
)
from .scientific import ScientificParityGate
from .metadata_gate import MetadataGate
from .context_documents import ContextDocuments
from .util import (
    WorkshopError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    package_hash,
    read_json,
    sha256_bytes,
    sqlite_snapshot,
    utc_now,
)


TERMINAL_STATES = {"POSTCHECK_VERIFIED", "ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK"}
PRE_APPLY_STATES = {"CHECKED_OUT", "AWAITING_METADATA_REVIEW", "PREPARED", "SHADOW_VERIFIED"}


class StateCore(EngineCoreContract):
    """Private state responsibility; composed only by the canonical engine base."""

    def __init__(self, config_path: Path) -> None:
        # Only the canonical guarded public engine may instantiate this base.
        public = sys.modules.get("runtime_sync_workshop.engine")
        canonical = getattr(public, "WorkshopEngine", None)
        if canonical is None or canonical not in type(self).__mro__:
            raise WorkshopError(
                "PRIVATE_ENGINE_DIRECT_USE",
                "instantiate runtime_sync_workshop.engine.WorkshopEngine",
            )
        self.config = load_config(config_path)
        self.scientific_gate = ScientificParityGate(self.config)
        self.metadata_gate = MetadataGate(self.config)
        self.context_documents = ContextDocuments(self.config)
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

    def _mirror(self, manifest: dict[str, Any], *, documents: Iterable[str] = ()) -> dict[str, Any]:
        objects: dict[str, str] = {}
        for relative in documents:
            objects[f"context:{relative}"] = self._store_object(
                (self.config.kairos_workspace / relative).read_bytes()
            )
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
        # Context-only transactions can leave the source corpus hash unchanged.
        # Name snapshots by their complete object set so earlier recovery points
        # remain immutable, including their Markdown and database snapshot.
        destination = self.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"
        if destination.is_file():
            previous = read_json(destination)
            if previous.get("objects") != mirror["objects"] or previous.get("package_sha256") != mirror["package_sha256"]:
                raise WorkshopError("MIRROR_STORE_CORRUPT", "content-addressed mirror does not match its object set")
        else:
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
            "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"),
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

    def _save_transaction(self, root: Path, state: dict[str, Any], *, event: str, details: Any = None, diagnostics_prepared: bool = False) -> None:
        if not diagnostics_prepared:
            state["updated_at"] = utc_now()
            state.setdefault("journal", []).append({"event": event, "at": state["updated_at"], "details": details})
        atomic_write_json(root / "state.json", state)

    def _require_lease(self, transaction_id: str) -> None:
        if not self.lease_path.is_file():
            raise WorkshopError("LEASE_MISSING", f"transaction {transaction_id} has no active lease")
        lease = read_json(self.lease_path)
        if lease.get("transaction_id") != transaction_id:
            raise WorkshopError("LEASE_OWNER_MISMATCH", f"lease belongs to {lease.get('transaction_id')}")

    def _assert_live_baseline(self, state: dict[str, Any]) -> dict[str, Any]:
        current = build_corpus_manifest(self.config)
        if not current["verified"]:
            raise WorkshopError("LIVE_CORPUS_DRIFT", "live corpus failed verification while a transaction is open", details=current["issues"])
        if current["package_sha256"] != state["baseline_package_sha256"]:
            raise WorkshopError("LIVE_CORPUS_DRIFT", "live corpus package changed after checkout", details={"baseline": state["baseline_package_sha256"], "current": current["package_sha256"]})
        self.context_documents.assert_baseline(self._transaction_path(state["transaction_id"]), state)
        return current

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load_transaction(transaction_id)
        return {**state, "root": str(root), "lease_owned": self.lease_path.is_file() and read_json(self.lease_path).get("transaction_id") == transaction_id}



def re_fullmatch_transaction(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("TXN_") or len(value) != 28:
        return False
    return all(char in "0123456789abcdef" for char in value[4:].lower())
