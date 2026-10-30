from __future__ import annotations

from ._core_contract import EngineCoreContract

import tomllib
from pathlib import Path
from typing import Any

from .corpus import (
    build_corpus_manifest,
)
from .scientific import transaction_work_manifest
from .util import (
    WorkshopError,
    atomic_write_json,
    read_json,
    sha256_bytes,
    utc_now,
)


from ._core_state import PRE_APPLY_STATES


class RecoveryCore(EngineCoreContract):
    """Private recovery responsibility; composed only by the canonical engine base."""

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
        self.context_documents.assert_baseline(root, state)
        checked.extend(str(self.config.kairos_workspace / relative) for relative in state.get("documents", []))
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
        self.context_documents.assert_baseline(root, state)
        checked.extend(str(self.config.kairos_workspace / relative) for relative in state.get("documents", []))
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
        if state.get("framework_projection"):
            from .corpus import framework_projection_recover
            recovered = framework_projection_recover(self, root, state)
            if recovered is not None:
                return recovered
            root, state = self._load_transaction(transaction_id)
        if state["state"] != "RECOVERY_REQUIRED":
            raise WorkshopError("TRANSACTION_STATE_INVALID", f"recover requires RECOVERY_REQUIRED, found {state['state']}")
        post = build_corpus_manifest(self.config)
        if not post["verified"]:
            raise WorkshopError("RECOVERY_STILL_BLOCKED", "live corpus is not bit-exact; lease retained", details=post["issues"])
        baseline_restored = post["package_sha256"] == state["baseline_package_sha256"]
        if baseline_restored:
            # A context-only edit can leave the source corpus hash unchanged.
            # Do not mistake successfully written context for a complete rollback.
            try:
                self.context_documents.assert_baseline(root, state)
            except WorkshopError as exc:
                if exc.code != "CONTEXT_BASELINE_CHANGED":
                    raise
                baseline_restored = False
        if baseline_restored:
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
        if transaction_work_manifest(self.config, root, state)["package_sha256"] != state.get("verified_work_package_sha256"):
            raise WorkshopError("RECOVERY_STILL_BLOCKED", "preserved work or review differs from the verified package")
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
        self.context_documents.postcheck(root, state)
        from .corpus import framework_projection_postcheck
        framework_projection_postcheck(self.config, root, state)
        mirror = self._mirror(post, documents=state.get("documents", []))
        seal = {
            "schema": "runtime-sync-seal/v1",
            "created_at": utc_now(),
            "package_sha256": post["package_sha256"],
            "mirror_sha256": mirror["mirror_sha256"],
            "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"),
            "counts": post["counts"],
            "transaction_id": transaction_id,
            "recovered": True,
            "context_documents": state.get("documents", []),
            "work_package_sha256": state["verified_work_package_sha256"],
        }
        atomic_write_json(self.seal_path, seal)
        state["state"] = "POSTCHECK_VERIFIED"
        state["postcheck_package_sha256"] = post["package_sha256"]
        receipt = {"schema": "runtime-sync-recovery/v1", "transaction_id": transaction_id,
                   "state": "POSTCHECK_VERIFIED", "package_sha256": post["package_sha256"],
                   "postcheck_package_sha256": post["package_sha256"]}
        if state.get("framework_projection"):
            receipt["framework_heartbeat"] = state["framework_recovery_heartbeat"]
        state["postcheck_receipt_payload"] = receipt
        self._save_transaction(root, state, event="RECOVERY_POSTCHECK_VERIFIED")
        return receipt

