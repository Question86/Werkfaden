from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .corpus import build_corpus_manifest
from .repair import _runtime_tree_manifest
from .util import (
    WorkshopError,
    atomic_write_json,
    copy_exact,
    package_hash,
    read_json,
    sha256_bytes,
    utc_now,
)


class AuxiliaryDocumentTransaction:
    """Exclusive document-only transaction for configured process authorities."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.config = engine.config

    def _root(self, transaction_id: str) -> Path:
        return self.engine._transaction_path(transaction_id)

    def _load(self, transaction_id: str) -> tuple[Path, dict[str, Any]]:
        root = self._root(transaction_id)
        path = root / "state.json"
        if not path.is_file():
            raise WorkshopError(
                "AUXILIARY_TRANSACTION_MISSING",
                f"auxiliary transaction does not exist: {transaction_id}",
            )
        state = read_json(path)
        if (
            state.get("schema") != "runtime-sync-auxiliary-transaction/v1"
            or state.get("transaction_id") != transaction_id
            or state.get("runtime_write_allowed") is not False
        ):
            raise WorkshopError(
                "AUXILIARY_TRANSACTION_INVALID",
                f"transaction is not a document-only auxiliary transaction: {transaction_id}",
            )
        return root, state

    def _assert_baseline(self, state: dict[str, Any]) -> None:
        manifest = build_corpus_manifest(self.config)
        if not manifest["verified"]:
            raise WorkshopError(
                "LIVE_CORPUS_INVALID",
                "live corpus failed verification during auxiliary transaction",
                details=manifest["issues"],
            )
        if manifest["package_sha256"] != state["baseline_package_sha256"]:
            raise WorkshopError(
                "AUXILIARY_BASELINE_CHANGED",
                "live corpus changed after auxiliary checkout",
                details={
                    "baseline": state["baseline_package_sha256"],
                    "current": manifest["package_sha256"],
                },
            )
        runtime = _runtime_tree_manifest(self.config)
        if runtime != state["baseline_runtime"]:
            raise WorkshopError(
                "RUNTIME_BASELINE_CHANGED",
                "Runtime changed after auxiliary checkout",
            )

    def checkout(self, filenames: Iterable[str], *, purpose: str) -> dict[str, Any]:
        selected = sorted(set(str(value) for value in filenames))
        if not selected:
            raise WorkshopError("AUXILIARY_SCOPE_EMPTY", "at least one auxiliary document is required")
        unknown = sorted(set(selected) - set(self.config.auxiliary_documents))
        if unknown:
            raise WorkshopError(
                "AUXILIARY_DOCUMENT_UNKNOWN",
                "document is not in the configured auxiliary authority",
                details=unknown,
            )
        if len(purpose.strip()) < 12:
            raise WorkshopError("PURPOSE_TOO_SHORT", "purpose must contain at least 12 characters")
        if not self.engine.seal_path.is_file():
            raise WorkshopError("SEAL_MISSING", "a verified corpus seal is required before checkout")
        manifest = build_corpus_manifest(self.config)
        seal = read_json(self.engine.seal_path)
        if not manifest["verified"] or seal.get("package_sha256") != manifest["package_sha256"]:
            raise WorkshopError(
                "SEAL_MISMATCH",
                "live corpus must be verified and equal the trusted seal before checkout",
                details=manifest["issues"],
            )
        created_at = utc_now()
        transaction_id = "TXN_" + package_hash({
            "mode": "auxiliary-document-only",
            "baseline": manifest["package_sha256"],
            "documents": selected,
            "purpose": purpose.strip(),
            "created_at": created_at,
        })[:24]
        root = self._root(transaction_id)
        if root.exists():
            raise WorkshopError("TRANSACTION_COLLISION", f"transaction already exists: {transaction_id}")
        self.engine._acquire_lease(transaction_id)
        try:
            (root / "baseline").mkdir(parents=True)
            (root / "work").mkdir(parents=True)
            (root / "rollback").mkdir(parents=True)
            baselines: list[dict[str, Any]] = []
            for filename in selected:
                live = self.config.blueprint_root / filename
                baseline = root / "baseline" / filename
                work = root / "work" / filename
                copy_exact(live, baseline)
                copy_exact(live, work)
                baselines.append({
                    "filename": filename,
                    "sha256": sha256_bytes(live.read_bytes()),
                    "bytes": live.stat().st_size,
                })
            state = {
                "schema": "runtime-sync-auxiliary-transaction/v1",
                "transaction_id": transaction_id,
                "state": "CHECKED_OUT",
                "runtime_write_allowed": False,
                "created_at": created_at,
                "updated_at": created_at,
                "purpose": purpose.strip(),
                "documents": selected,
                "baseline_documents": baselines,
                "baseline_package_sha256": manifest["package_sha256"],
                "baseline_runtime": _runtime_tree_manifest(self.config),
                "journal": [],
            }
            self.engine._save_transaction(
                root,
                state,
                event="AUXILIARY_CHECKOUT_COMPLETED",
                details={"documents": selected},
            )
        except Exception:
            if self.engine.lease_path.is_file():
                lease = read_json(self.engine.lease_path)
                if lease.get("transaction_id") == transaction_id:
                    self.engine._release_lease(transaction_id)
            raise
        return {
            "schema": "runtime-sync-auxiliary-checkout/v1",
            "transaction_id": transaction_id,
            "state": "CHECKED_OUT",
            "documents": selected,
            "work_directory": str(root / "work"),
        }

    def verify(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        self.engine._require_lease(transaction_id)
        if state["state"] not in {"CHECKED_OUT", "SHADOW_VERIFIED"}:
            raise WorkshopError(
                "AUXILIARY_STATE_INVALID",
                f"auxiliary verify is not allowed from {state['state']}",
            )
        self._assert_baseline(state)
        changed: list[dict[str, Any]] = []
        for row in state["baseline_documents"]:
            filename = row["filename"]
            work = root / "work" / filename
            if not work.is_file():
                raise WorkshopError("WORK_FILE_MISSING", f"auxiliary work file is missing: {work}")
            raw = work.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkshopError("AUXILIARY_NON_UTF8", f"auxiliary document is not UTF-8: {filename}") from exc
            if "\x00" in text:
                raise WorkshopError("AUXILIARY_NUL_BYTE", f"auxiliary document contains NUL: {filename}")
            digest = sha256_bytes(raw)
            if digest != row["sha256"]:
                changed.append({
                    "filename": filename,
                    "baseline_sha256": row["sha256"],
                    "staged_sha256": digest,
                    "bytes": len(raw),
                })
        if not changed:
            raise WorkshopError("TRANSACTION_NO_CHANGES", "auxiliary verify found no document changes")
        state["state"] = "SHADOW_VERIFIED"
        state["changed_documents"] = changed
        self.engine._save_transaction(
            root,
            state,
            event="AUXILIARY_SHADOW_VERIFIED",
            details=changed,
        )
        return {
            "schema": "runtime-sync-auxiliary-verification/v1",
            "transaction_id": transaction_id,
            "state": "SHADOW_VERIFIED",
            "runtime_write_allowed": False,
            "changed_documents": changed,
        }

    def apply(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        self.engine._require_lease(transaction_id)
        if state["state"] != "SHADOW_VERIFIED":
            raise WorkshopError(
                "AUXILIARY_STATE_INVALID",
                f"auxiliary apply requires SHADOW_VERIFIED, found {state['state']}",
            )
        self._assert_baseline(state)
        backups: list[dict[str, Any]] = []
        for row in state["changed_documents"]:
            filename = row["filename"]
            live = self.config.blueprint_root / filename
            backup = root / "rollback" / filename
            copy_exact(live, backup)
            backups.append({
                "filename": filename,
                "live": str(live),
                "backup": str(backup),
                "sha256": sha256_bytes(live.read_bytes()),
            })
        atomic_write_json(root / "rollback" / "targets.json", backups)
        state["state"] = "APPLYING"
        self.engine._save_transaction(
            root,
            state,
            event="AUXILIARY_APPLY_STARTED",
            details={"documents": [row["filename"] for row in backups]},
        )
        try:
            for row in state["changed_documents"]:
                filename = row["filename"]
                copy_exact(
                    root / "work" / filename,
                    self.config.blueprint_root / filename,
                )
            runtime = _runtime_tree_manifest(self.config)
            if runtime != state["baseline_runtime"]:
                raise WorkshopError(
                    "RUNTIME_WRITE_INVARIANT_FAILED",
                    "Runtime changed during auxiliary document apply",
                )
            post = build_corpus_manifest(self.config)
            if not post["verified"]:
                raise WorkshopError(
                    "AUXILIARY_POSTCHECK_FAILED",
                    "corpus failed verification after auxiliary document apply",
                    details=post["issues"],
                )
            for row in state["changed_documents"]:
                filename = row["filename"]
                live = self.config.blueprint_root / filename
                work = root / "work" / filename
                if live.read_bytes() != work.read_bytes():
                    raise WorkshopError(
                        "AUXILIARY_POSTCHECK_MISMATCH",
                        f"live auxiliary document differs from staged bytes: {filename}",
                    )
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
                "auxiliary_documents": [row["filename"] for row in state["changed_documents"]],
                "runtime_package_sha256": runtime["package_sha256"],
            }
            atomic_write_json(self.engine.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["runtime_postcheck"] = runtime
            self.engine._save_transaction(
                root,
                state,
                event="AUXILIARY_POSTCHECK_VERIFIED",
                details={"package_sha256": post["package_sha256"]},
            )
            self.engine._release_lease(transaction_id)
            receipt = {
                "schema": "runtime-sync-auxiliary-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "runtime_write_allowed": False,
                "runtime_package_sha256_before": state["baseline_runtime"]["package_sha256"],
                "runtime_package_sha256_after": runtime["package_sha256"],
                "baseline_package_sha256": state["baseline_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "changed_documents": state["changed_documents"],
                "bit_exact": True,
            }
            path = self.engine._write_receipt("auxiliary-postcheck", receipt)
            return {**receipt, "receipt": str(path)}
        except Exception as exc:
            rollback: list[dict[str, Any]] = []
            for row in reversed(backups):
                try:
                    live = Path(row["live"])
                    if live.resolve().parent != self.config.blueprint_root.resolve():
                        raise WorkshopError("ROLLBACK_PATH_INVALID", f"invalid rollback target: {live}")
                    copy_exact(Path(row["backup"]), live)
                    rollback.append({
                        "path": str(live),
                        "restored": sha256_bytes(live.read_bytes()) == row["sha256"],
                    })
                except Exception as rollback_exc:
                    rollback.append({
                        "path": row["live"],
                        "restored": False,
                        "error": str(rollback_exc),
                    })
            failure = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(exc),
            }
            runtime_ok = _runtime_tree_manifest(self.config) == state["baseline_runtime"]
            if all(row.get("restored") for row in rollback) and runtime_ok:
                state["state"] = "ROLLED_BACK"
                state["failure"] = failure
                self.engine._save_transaction(
                    root,
                    state,
                    event="AUXILIARY_APPLY_ROLLED_BACK",
                    details=rollback,
                )
                self.engine._release_lease(transaction_id)
                raise WorkshopError(
                    "APPLY_ROLLED_BACK",
                    "auxiliary apply failed and exact document baselines were restored",
                    details={"cause": failure, "rollback": rollback},
                ) from exc
            state["state"] = "RECOVERY_REQUIRED"
            state["failure"] = failure
            self.engine._save_transaction(
                root,
                state,
                event="AUXILIARY_RECOVERY_REQUIRED",
                details={"rollback": rollback, "runtime_unchanged": runtime_ok},
            )
            raise WorkshopError(
                "RECOVERY_REQUIRED",
                "auxiliary apply could not prove exact rollback; lease retained",
                details={"cause": failure, "rollback": rollback},
            ) from exc

    def abort(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        self.engine._require_lease(transaction_id)
        if state["state"] not in {"CHECKED_OUT", "SHADOW_VERIFIED"}:
            raise WorkshopError(
                "AUXILIARY_STATE_INVALID",
                f"auxiliary abort is not allowed from {state['state']}",
            )
        self._assert_baseline(state)
        state["state"] = "ABORTED"
        self.engine._save_transaction(root, state, event="AUXILIARY_ABORTED")
        self.engine._release_lease(transaction_id)
        return {
            "schema": "runtime-sync-auxiliary-abort/v1",
            "transaction_id": transaction_id,
            "state": "ABORTED",
            "live_corpus_unchanged": True,
            "runtime_unchanged": True,
        }

    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        root, state = self._load(transaction_id)
        lease_owned = False
        if self.engine.lease_path.is_file():
            lease_owned = read_json(self.engine.lease_path).get("transaction_id") == transaction_id
        return {**state, "root": str(root), "lease_owned": lease_owned}
