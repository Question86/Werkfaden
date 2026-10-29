from __future__ import annotations

from ._core_contract import EngineCoreContract

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .blueprint import (
    parse_blueprint,
)
from .corpus import (
    GRAPH_PROJECTIONS,
    build_corpus_manifest,
    parsed_sections,
)
from .scientific import transaction_work_manifest
from .util import (
    WorkshopError,
    atomic_write_json,
    capture_rollback_preimage,
    copy_exact,
    read_json,
    restore_rollback_preimage,
    sha256_bytes,
    sha256_text,
    utc_now,
)




class ApplyCore(EngineCoreContract):
    """Private apply responsibility; composed only by the canonical engine base."""

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

    def _import_kairos(self) -> tuple[Any, Any]:
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        governance = importlib.import_module("kairos.governance")
        heartbeat = importlib.import_module("kairos.heartbeat")
        return governance, heartbeat

    def _backup_apply_targets(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
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
                rows.append({"role": role, "live": str(live), "backup": str(backup), **capture_rollback_preimage(live, backup, copier=copy_exact)})
        for change in state.get("test_changes", []):
            if change["changed"]:
                relative = change["source"]
                live = self.config.codebase_root / relative
                backup = root / "rollback" / relative
                rows.append({"role": "dependent-test", "live": str(live),
                             "backup": str(backup), **capture_rollback_preimage(live, backup, copier=copy_exact)})
        for header in state.get("topology_blueprints", []):
            record = baseline_manifest["records"][header]
            name = record["blueprint"]["filename"]
            for role, live in (
                ("blueprint", self.config.blueprint_root / name),
                ("managed", self.config.managed_blueprint_root / name),
            ):
                key = f"{role}/{sha256_bytes(str(live).encode('utf-8'))[:16]}_{live.name}"
                backup = root / "rollback" / key
                rows.append({"role": role, "live": str(live), "backup": str(backup), **capture_rollback_preimage(live, backup, copier=copy_exact)})
        for relative in state.get("authority_documents", []):
            live = self.config.kairos_workspace / relative
            backup = root / "rollback" / "authority" / relative
            rows.append({
                "role": "authority",
                "live": str(live),
                "backup": str(backup),
                **capture_rollback_preimage(live, backup, copier=copy_exact),
            })
        for relative in state.get("changed_documents", []):
            live = self.config.kairos_workspace / relative
            backup = root / "rollback" / "documents" / relative
            rows.append({
                "role": "context", "live": str(live), "backup": str(backup),
                **capture_rollback_preimage(live, backup, copier=copy_exact),
            })
        for relative in state.get("machine_authority_files", []):
            live = (self.config.machine_root / relative).resolve()
            try:
                live.relative_to(self.config.machine_root.resolve())
            except ValueError as exc:
                raise WorkshopError("PATH_ESCAPE", f"machine authority escapes Workshop root: {relative}") from exc
            backup = root / "rollback" / "machine" / relative
            rows.append({
                "role": "machine-authority",
                "live": str(live),
                "backup": str(backup),
                **capture_rollback_preimage(live, backup, copier=copy_exact),
            })
        atomic_write_json(root / "rollback" / "targets.json", rows)
        return rows

    def _rollback_files(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in reversed(rows):
            live = Path(row["live"])
            backup = Path(row["backup"])
            try:
                valid = restore_rollback_preimage(backup, live, row["sha256"], row.get("mtime_ns"), copier=copy_exact)
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
        metadata_aftercheck = self._assert_metadata_checked(root, state, current_work["package_sha256"])
        scientific_parity = self.scientific_gate.assert_apply_ready(root, state)
        adapter = str(self.config.raw.get("shadow_adapter", "native"))
        changed_managed = [f"code/{baseline_manifest['records'][source]['blueprint']['filename']}" for source in state["changed_sources"]]
        changed_managed.extend(
            f"code/{baseline_manifest['records'][header]['blueprint']['filename']}"
            for header in state.get("topology_blueprints", [])
        )
        changed_managed.extend(state.get("authority_documents", []))
        changed_managed.extend(state.get("changed_documents", []))
        changed_managed = sorted(set(changed_managed))
        backups = self._backup_apply_targets(root, state, baseline_manifest)
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
            for relative in state.get("changed_documents", []):
                copy_exact(root / "work" / "documents" / relative, self.config.kairos_workspace / relative)
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
            self.context_documents.postcheck(root, state)
            mirror = self._mirror(post, documents=state.get("documents", []))
            seal = {
                "schema": "runtime-sync-seal/v1",
                "created_at": utc_now(),
                "package_sha256": post["package_sha256"],
                "mirror_sha256": mirror["mirror_sha256"],
                "mirror_manifest": str(self.config.state_directory / "mirrors" / f"{mirror['mirror_sha256']}.json"),
                "counts": post["counts"],
                "transaction_id": transaction_id,
                "context_documents": state.get("documents", []),
                "work_package_sha256": state["verified_work_package_sha256"],
            }
            atomic_write_json(self.seal_path, seal)
            state["state"] = "POSTCHECK_VERIFIED"
            state["postcheck_package_sha256"] = post["package_sha256"]
            state["heartbeat"] = heartbeat_receipt
            receipt = {
                "schema": "runtime-sync-postcheck/v1",
                "transaction_id": transaction_id,
                "state": "POSTCHECK_VERIFIED",
                "baseline_package_sha256": state["baseline_package_sha256"],
                "postcheck_package_sha256": post["package_sha256"],
                "heartbeat": heartbeat_receipt,
                "bit_exact": True,
                "context_documents": state.get("documents", []),
                "changed_documents": state.get("changed_documents", []),
                "work_package_sha256": state["verified_work_package_sha256"],
                "scientific_parity": scientific_parity,
                "metadata_aftercheck": metadata_aftercheck,
            }
            # Preserve the exact result before returning to the public terminal owner.
            state["postcheck_receipt_payload"] = receipt
            self._save_transaction(root, state, event="POSTCHECK_VERIFIED", details={"package_sha256": post["package_sha256"]})
            return receipt
        except Exception as exc:
            if heartbeat_started:
                state["state"] = "RECOVERY_REQUIRED"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self._save_transaction(root, state, event="RECOVERY_REQUIRED", details=state["failure"])
                raise WorkshopError("RECOVERY_REQUIRED", "apply crossed the heartbeat boundary and failed; lease retained", details=state["failure"]) from exc
            rollback = self._rollback_files(backups)
            if all(row.get("restored") for row in rollback):
                if permit and adapter == "native":
                    try:
                        governance, _ = self._import_kairos()
                        state["permit_revocation"] = governance.revoke_action_permit(
                            self.config.kairos_workspace,
                            permit_id=str(permit["permit_id"]),
                            reason=f"Workshop {transaction_id} restored all apply targets before heartbeat; the edit permit is no longer needed.",
                        )
                    except Exception as revoke_error:
                        state["state"] = "RECOVERY_REQUIRED"
                        state["failure"] = {"code": "PERMIT_REVOCATION_FAILED", "message": str(revoke_error)}
                        self._save_transaction(root, state, event="RECOVERY_REQUIRED", details=state["failure"])
                        raise WorkshopError("RECOVERY_REQUIRED", "files restored but unused edit permit could not be revoked; lease retained", details=state["failure"]) from revoke_error
                state["state"] = "ROLLED_BACK"
                state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
                self._save_transaction(root, state, event="APPLY_ROLLED_BACK", details=rollback)
                self._release_lease(transaction_id)
                raise WorkshopError("APPLY_ROLLED_BACK", "apply failed before heartbeat and all named files were restored", details={"cause": state["failure"], "rollback": rollback}) from exc
            state["state"] = "RECOVERY_REQUIRED"
            state["failure"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            self._save_transaction(root, state, event="ROLLBACK_INCOMPLETE", details=rollback)
            raise WorkshopError("RECOVERY_REQUIRED", "apply failed and rollback was incomplete; lease retained", details={"cause": state["failure"], "rollback": rollback}) from exc

