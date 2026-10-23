from __future__ import annotations

from .guardian_common import *


class GuardianCloseMixin:
    def patch_close(self, guardian_id: str, *, state_ticket_id: str, package_sha256: str) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            self._assert_rolling_package(payload, package_sha256)
            gate = self._consume_gate(
                payload, step="HEARTBEAT", ticket_id=state_ticket_id, package_sha256=package_sha256
            )
            if payload.get("next_step") != "HEARTBEAT":
                raise WorkshopError("GUARDIAN_STEP_ORDER", f"expected {payload.get('next_step')}, received HEARTBEAT close")
            session = payload.get("patch_session") or {}
            if session.get("status") != "OPEN" or session.get("active_transaction"):
                raise WorkshopError("GUARDIAN_PATCH_NOT_CLOSABLE", "patch session is not idle/open")
            remaining = session.get("available") or {}
            nonempty = {kind: value for kind, value in remaining.items() if any(value.values())}
            if nonempty:
                raise WorkshopError("GUARDIAN_PATCH_SCOPE_REMAINS", "proven patch scope remains unconsumed; reframe instead of silently dropping it", details=nonempty)
            successful = [row for row in session.get("transactions") or [] if row.get("state") == "POSTCHECK_VERIFIED"]
            if not successful:
                raise WorkshopError("GUARDIAN_PATCH_EMPTY", "patch session has no POSTCHECK_VERIFIED transaction")
            heartbeat_id = session.get("final_heartbeat_id")
            if not heartbeat_id:
                raise WorkshopError("GUARDIAN_HEARTBEAT_MISSING", "final transaction has no verified Workshop heartbeat")
            with self._db() as connection:
                row = connection.execute("SELECT receipt_json,verified FROM heartbeat_receipts WHERE heartbeat_id=?", (heartbeat_id,)).fetchone()
                if not row or not int(row["verified"]):
                    raise WorkshopError("GUARDIAN_HEARTBEAT_INVALID", f"heartbeat receipt is not verified: {heartbeat_id}")
                heartbeat_snapshot = json.loads(row["receipt_json"])
            passages = list(gate.get("memory_passages") or [])
            session["status"] = "WORKSHOP_COMPLETE"
            session["closed_at"] = utc_now()
            payload["current_step"] = "HEARTBEAT"
            payload["next_step"] = "FRESH_RUN"
            payload["active_gate"] = None
            self._append(payload, "PATCH_CLOSE", {"heartbeat_id": heartbeat_id, "heartbeat_sha256": sha256_text(canonical_json(heartbeat_snapshot)), "memory_passages": passages, "transaction_count": len(successful)})
            self._save(payload)
            return self._view(payload)


    def fresh_run(self, guardian_id: str, *, receipt_file: Path, state_ticket_id: str, package_sha256: str) -> dict[str, Any]:
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            self._assert_scope_identity(payload)
            self._assert_rolling_package(payload, package_sha256)
            gate = self._consume_gate(
                payload, step="FRESH_RUN", ticket_id=state_ticket_id, package_sha256=package_sha256
            )
            session = payload.get("patch_session") or {}
            if session.get("status") != "WORKSHOP_COMPLETE" or payload.get("next_step") != "FRESH_RUN":
                raise WorkshopError("GUARDIAN_FRESH_RUN_NOT_READY", "patch session must close with verified heartbeat before fresh run")
            path = Path(receipt_file)
            if not path.is_file():
                raise WorkshopError("GUARDIAN_RUN_RECEIPT_MISSING", f"fresh-run receipt is missing: {path}")
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt.get("schema") != FRESH_RUN_SCHEMA:
                raise WorkshopError("GUARDIAN_RUN_RECEIPT_INVALID", f"fresh-run receipt schema must be {FRESH_RUN_SCHEMA}")
            if receipt.get("guardian_id") != guardian_id:
                raise WorkshopError("GUARDIAN_RUN_SESSION_MISMATCH", "fresh-run receipt belongs to another Guardian session")
            if receipt.get("state_ticket_id") != state_ticket_id:
                raise WorkshopError("GUARDIAN_RUN_GATE_MISMATCH", "fresh-run receipt is not bound to the pre-run Memory gate")
            if receipt.get("package_sha256") != package_sha256:
                raise WorkshopError("GUARDIAN_RUN_PACKAGE_MISMATCH", "fresh-run receipt is not bound to the final patch package")
            if receipt.get("heartbeat_id") != session.get("final_heartbeat_id"):
                raise WorkshopError("GUARDIAN_RUN_HEARTBEAT_MISMATCH", "fresh-run receipt is not bound to the patch-closing heartbeat")
            patch_head = receipt.get("patch_head_hash")
            if patch_head != gate.get("head_hash_before"):
                raise WorkshopError("GUARDIAN_RUN_REPLAY", "fresh-run receipt is not bound to the patch-close ledger head that preceded the FRESH_RUN Memory gate")
            if not isinstance(receipt.get("run_id"), str) or not receipt["run_id"].strip():
                raise WorkshopError("GUARDIAN_RUN_RECEIPT_INVALID", "fresh-run receipt requires a run_id")
            completed_at = receipt.get("completed_at")
            if not isinstance(completed_at, str) or _parse_time(completed_at) <= _parse_time(str(gate["opened_at"])):
                raise WorkshopError("GUARDIAN_RUN_RECEIPT_STALE", "fresh-run receipt must complete after the FRESH_RUN Memory gate opened")
            status = receipt.get("problem_status")
            if status not in {"FIXED", "STILL_PRESENT", "INCONCLUSIVE"}:
                raise WorkshopError("GUARDIAN_RUN_STATUS_INVALID", "problem_status must be FIXED, STILL_PRESENT or INCONCLUSIVE")
            executor = receipt.get("executor")
            if not isinstance(executor, dict) or not isinstance(executor.get("path"), str) or not isinstance(executor.get("sha256"), str):
                raise WorkshopError("GUARDIAN_RUN_EXECUTOR_INVALID", "fresh-run receipt requires a content-bound executor path and sha256")
            executor_path = Path(executor["path"])
            if not executor_path.is_file() or sha256_bytes(executor_path.read_bytes()) != executor["sha256"]:
                raise WorkshopError("GUARDIAN_RUN_EXECUTOR_INVALID", "fresh-run executor bytes do not match the receipt")
            validations = receipt.get("validations") or []
            if not isinstance(validations, list) or any(not isinstance(item, dict) for item in validations):
                raise WorkshopError("GUARDIAN_RUN_VALIDATION_INVALID", "fresh-run validations must be an object array")
            validation_rows: list[dict[str, Any]] = []
            labels: set[str] = set()
            for item in validations:
                label = str(item.get("label", "")).strip()
                path_value = item.get("path")
                digest = str(item.get("sha256", ""))
                if not label or not isinstance(path_value, str) or len(digest) != 64:
                    raise WorkshopError("GUARDIAN_RUN_VALIDATION_INVALID", "each validation requires label, path and sha256")
                validation_path = Path(path_value)
                if not validation_path.is_file() or sha256_bytes(validation_path.read_bytes()) != digest:
                    raise WorkshopError("GUARDIAN_RUN_VALIDATION_INVALID", f"validation bytes do not match: {label}")
                if item.get("package_sha256") not in {None, package_sha256}:
                    raise WorkshopError("GUARDIAN_RUN_VALIDATION_PACKAGE", f"validation {label} belongs to another package")
                labels.add(label)
                validation_rows.append({"label": label, "path": str(validation_path.resolve()), "sha256": digest})
            missing_validations = sorted(set(self.required_run_validations) - labels)
            if missing_validations:
                raise WorkshopError(
                    "GUARDIAN_RUN_VALIDATION_MISSING",
                    "fresh run lacks project-required validation receipts",
                    details={"missing": missing_validations},
                )
            problem_evidence = receipt.get("problem_evidence")
            if not isinstance(problem_evidence, dict):
                raise WorkshopError("GUARDIAN_RUN_PROBLEM_EVIDENCE_MISSING", "fresh-run receipt requires problem_evidence")
            criterion = problem_evidence.get("criterion_id")
            expected_criterion = (payload.get("project_scope") or {}).get("criterion_id")
            if expected_criterion and criterion != expected_criterion:
                raise WorkshopError("GUARDIAN_RUN_PROBLEM_SCOPE", "fresh-run problem evidence belongs to another criterion")
            problem_path = Path(str(problem_evidence.get("path", "")))
            problem_sha = str(problem_evidence.get("sha256", ""))
            if not problem_path.is_file() or sha256_bytes(problem_path.read_bytes()) != problem_sha:
                raise WorkshopError("GUARDIAN_RUN_PROBLEM_EVIDENCE_INVALID", "problem evidence bytes do not match the fresh-run receipt")
            artifacts = receipt.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                raise WorkshopError("GUARDIAN_RUN_ARTIFACT_MISSING", "fresh-run receipt requires at least one content-bound artifact")
            verified_artifacts: list[dict[str, Any]] = []
            for item in artifacts:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                    raise WorkshopError("GUARDIAN_RUN_ARTIFACT_INVALID", "fresh-run artifact requires path and sha256")
                artifact = Path(item["path"])
                if not artifact.is_file():
                    raise WorkshopError("GUARDIAN_RUN_ARTIFACT_MISSING", f"fresh-run artifact missing: {artifact}")
                actual = sha256_bytes(artifact.read_bytes())
                if actual != item["sha256"]:
                    raise WorkshopError("GUARDIAN_RUN_ARTIFACT_HASH", f"fresh-run artifact hash mismatch: {artifact}")
                verified_artifacts.append({"path": str(artifact.resolve()), "sha256": actual, "bytes": artifact.stat().st_size})
            passages = list(gate.get("memory_passages") or [])
            payload["current_step"] = "FRESH_RUN"
            payload["next_step"] = None
            payload["active_gate"] = None
            payload["closed"] = True
            payload["fresh_run"] = {
                "run_id": receipt["run_id"],
                "problem_status": status,
                "package_sha256": package_sha256,
                "heartbeat_id": session.get("final_heartbeat_id"),
                "executor": {"path": str(executor_path.resolve()), "sha256": executor["sha256"]},
                "validations": validation_rows,
                "problem_evidence": {"criterion_id": criterion, "path": str(problem_path.resolve()), "sha256": problem_sha},
                "receipt_path": str(path.resolve()),
                "receipt_sha256": sha256_bytes(path.read_bytes()),
                "artifacts": verified_artifacts,
            }
            self._append(payload, "FRESH_RUN", {**payload["fresh_run"], "memory_passages": passages})
            self._save(payload)
            return self._view(payload)
