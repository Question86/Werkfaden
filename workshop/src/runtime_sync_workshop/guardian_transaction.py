from __future__ import annotations

from .guardian_common import *


class GuardianTransactionMixin:
    def _actual_scope(self, transaction_id: str, kind: str, state: dict[str, Any]) -> dict[str, Any]:
        if kind == "normal":
            changes = {str(row.get("source")): row for row in state.get("changes") or [] if isinstance(row, dict)}
            headers: list[str] = []
            if self.transaction_directory and self.codebase_root:
                manifest_path = self.transaction_directory / transaction_id / "baseline" / "manifest.json"
                if manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    records = manifest.get("records") or {}
                    for source, row in changes.items():
                        if not row.get("header_changed"):
                            continue
                        record = records.get(source) or {}
                        header = record.get("header") or {}
                        value = header.get("path")
                        if isinstance(value, str) and value:
                            try:
                                relative = Path(value).resolve().relative_to(self.codebase_root).as_posix()
                            except ValueError:
                                raise WorkshopError("GUARDIAN_SCOPE_INVALID", f"changed header escapes codebase: {value}")
                            headers.append(relative)
            return {
                "sources": sorted(str(value) for value in state.get("changed_sources") or []),
                "headers": sorted(set(headers)),
                "dependent_tests": sorted(str(row.get("source")) for row in state.get("test_changes") or [] if isinstance(row, dict) and row.get("changed")),
            }
        if kind == "auxiliary":
            return {"documents": sorted(str(row.get("filename")) for row in state.get("changed_documents") or [] if isinstance(row, dict))}
        if kind == "source_set":
            operations: list[dict[str, str]] = []
            renames = {str(k): str(v) for k, v in (state.get("renames") or {}).items()}
            renamed_old = set(renames)
            renamed_new = set(renames.values())
            for old, new in sorted(renames.items()):
                operations.append({"op": "rename", "path": old, "to": new})
            for path in sorted(set(state.get("added_sources") or []) - renamed_new):
                operations.append({"op": "add", "path": str(path)})
            for path in sorted(set(state.get("removed_sources") or []) - renamed_old):
                operations.append({"op": "remove", "path": str(path)})
            return {"operations": sorted(operations, key=lambda item: (item["op"], item["path"], item.get("to", "")))}
        if kind == "authority":
            return {
                "add_headers": sorted(str(value) for value in state.get("added_headers") or []),
                "remove_headers": sorted(str(value) for value in state.get("removed_headers") or []),
            }
        return {}

    def _validate_normal_edit_windows(self, payload: dict[str, Any], transaction_id: str, actual: dict[str, Any]) -> None:
        if not self.transaction_directory:
            raise WorkshopError("GUARDIAN_TRANSACTION_ROOT_MISSING", "cannot enforce exact edit windows without transaction_directory")
        windows = (payload.get("patch_session") or {}).get("inspection_windows") or {}
        root = self.transaction_directory / transaction_id
        changed = sorted(set(actual.get("sources") or []) | set(actual.get("headers") or []) | set(actual.get("dependent_tests") or []))
        for relative in changed:
            allowed = windows.get(relative) or []
            if not allowed:
                raise WorkshopError("GUARDIAN_EDIT_WINDOW_MISSING", f"changed file has no exact-source edit window: {relative}")
            baseline = root / "baseline" / relative
            work = root / "work" / relative
            if not baseline.is_file() or not work.is_file():
                raise WorkshopError("GUARDIAN_EDIT_WINDOW_FILE_MISSING", f"cannot compare exact edit window for {relative}")
            before = baseline.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()
            after = work.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()
            matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
            touched: set[int] = set()
            for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                if i2 > i1:
                    touched.update(range(i1 + 1, i2 + 1))
                else:
                    anchor = 1 if not before else max(1, min(len(before), i1 + 1))
                    touched.add(anchor)
            outside = sorted(line for line in touched if not any(int(start) <= line <= int(end) for start, end in allowed))
            if outside:
                raise WorkshopError("GUARDIAN_EDIT_OUTSIDE_INSPECTION", f"transaction changes {relative} outside exact-source inspected windows", details={"allowed": allowed, "outside_lines": outside[:64]})

    def observe_transaction_state(self, transaction_id: str, *, event: str, state: dict[str, Any]) -> None:
        owner = self.assert_transaction_active(transaction_id)
        guardian_id = owner["guardian_id"]
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            record = self._find_tx(payload, transaction_id)
            record["state"] = str(state.get("state", event))
            record["last_event"] = event
            record["derived"] = {
                "authority_documents": sorted(str(value) for value in state.get("authority_documents") or []),
                "topology_blueprints": sorted(str(value) for value in state.get("topology_blueprints") or []),
                "machine_authority_files": sorted(str(value) for value in state.get("machine_authority_files") or []),
                "prepared_blueprints": sorted(str(value) for value in state.get("prepared_blueprints") or []),
            }
            if event in {"PREPARED", "SOURCE_SET_PREPARED", "AUTHORITY_PREPARED", "AUXILIARY_SHADOW_VERIFIED"} or state.get("state") in {"PREPARED", "SHADOW_VERIFIED"}:
                actual = self._actual_scope(transaction_id, record["kind"], state)
                requested = record["requested"]
                if record["kind"] in {"source_set", "authority"}:
                    if actual != requested:
                        raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", "prepared structural transaction differs from PROVE scope", details={"requested": requested, "actual": actual})
                else:
                    for field, values in actual.items():
                        if not set(values).issubset(set(requested.get(field, []))):
                            raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", f"prepared transaction changed unproven {field}", details={"requested": requested.get(field, []), "actual": values})
                    if record["kind"] == "normal":
                        self._validate_normal_edit_windows(payload, transaction_id, actual)
                record["scope_validated"] = True
                record["actual"] = actual
            if event in {"APPLY_STARTED", "AUXILIARY_APPLY_STARTED", "SOURCE_SET_APPLY_STARTED", "AUTHORITY_APPLY_STARTED"} or state.get("state") == "APPLYING":
                if not record.get("scope_validated"):
                    raise WorkshopError("GUARDIAN_SCOPE_NOT_VALIDATED", "live apply is blocked until prepared scope matches PROVE")
            self._append(payload, "TX_STATE", {"transaction_id": transaction_id, "event": event, "state": record["state"], "scope_validated": record.get("scope_validated", False)})
            self._save(payload)

    def cancel_transaction(self, transaction_id: str, *, reason: str) -> dict[str, Any]:
        owner = self.assert_transaction_active(transaction_id)
        guardian_id = owner["guardian_id"]
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            record = self._find_tx(payload, transaction_id)
            session = payload["patch_session"]
            record["state"] = "CHECKOUT_FAILED"
            record["failure"] = str(reason)[:2000]
            session["active_transaction"] = None
            payload["current_step"] = "WORKSHOP"
            payload["next_step"] = "PATCH"
            self._append(payload, "TX_CANCEL", {"transaction_id": transaction_id, "reason": record["failure"]})
            self._save(payload)
            return self._view(payload)

    def assert_release_transaction(self, transaction_id: str, *, state: dict[str, Any], package_sha256: str) -> dict[str, Any]:
        owner = self.assert_transaction_active(transaction_id)
        guardian_id = owner["guardian_id"]
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            record = self._find_tx(payload, transaction_id)
            terminal = str(state.get("state", ""))
            if terminal == "POSTCHECK_VERIFIED":
                post = str(state.get("postcheck_package_sha256") or package_sha256)
                if post != package_sha256:
                    raise WorkshopError("GUARDIAN_POSTCHECK_PACKAGE_MISMATCH", "transaction postcheck package differs from current sealed package")
                if not record.get("scope_validated"):
                    actual = self._actual_scope(transaction_id, record["kind"], state)
                    requested = record["requested"]
                    for field, values in actual.items():
                        if field in requested and not set(values).issubset(set(requested.get(field, []))):
                            raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", f"terminal transaction changed unproven {field}")
                    if record["kind"] == "normal":
                        self._validate_normal_edit_windows(payload, transaction_id, actual)
            elif terminal not in {"ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK", "ABORTED_ORPHANED_CHECKOUT"}:
                raise WorkshopError("GUARDIAN_RELEASE_STATE", f"lease release is not allowed from guardian-visible state {terminal}")
            return {"guardian_id": guardian_id, "terminal": terminal, "package_sha256": package_sha256}

    def release_transaction(self, transaction_id: str, *, state: dict[str, Any], package_sha256: str) -> dict[str, Any]:
        owner = self.assert_transaction_active(transaction_id)
        guardian_id = owner["guardian_id"]
        with _file_lock(self._lock_path(guardian_id)):
            payload = self._load(guardian_id)
            self._assert_memory(payload)
            record = self._find_tx(payload, transaction_id)
            session = payload["patch_session"]
            terminal = str(state.get("state"))
            record["state"] = terminal
            if terminal == "POSTCHECK_VERIFIED":
                post = str(state.get("postcheck_package_sha256") or package_sha256)
                if post != package_sha256:
                    raise WorkshopError("GUARDIAN_POSTCHECK_PACKAGE_MISMATCH", "transaction postcheck package differs from current sealed package")
                if not record.get("scope_validated"):
                    actual = self._actual_scope(transaction_id, record["kind"], state)
                    requested = record["requested"]
                    for field, values in actual.items():
                        if field in requested and not set(values).issubset(set(requested.get(field, []))):
                            raise WorkshopError("GUARDIAN_SCOPE_MISMATCH", f"terminal transaction changed unproven {field}")
                    if record["kind"] == "normal":
                        self._validate_normal_edit_windows(payload, transaction_id, actual)
                    record["actual"] = actual
                    record["scope_validated"] = True
                record["postcheck_package_sha256"] = post
                heartbeat = state.get("heartbeat") or {}
                record["heartbeat_id"] = heartbeat.get("heartbeat_id")
                payload["rolling_package_sha256"] = post
                actual = record.get("actual") or record["requested"]
                available = session["available"][record["kind"]]
                consumed = session["consumed"][record["kind"]]
                if record["kind"] in {"normal", "auxiliary", "authority"}:
                    for field, values in actual.items():
                        if field not in available:
                            continue
                        available[field] = [value for value in available[field] if value not in set(values)]
                        consumed[field] = sorted(set(consumed[field]) | set(values))
                else:
                    available["operations"] = []
                    consumed["operations"] = actual.get("operations", record["requested"].get("operations", []))
                session["final_heartbeat_id"] = record.get("heartbeat_id") or session.get("final_heartbeat_id")
            elif terminal in {"ABORTED", "ABORTED_STALE_BASELINE", "ROLLED_BACK", "ABORTED_ORPHANED_CHECKOUT"}:
                record["scope_validated"] = False
            else:
                raise WorkshopError("GUARDIAN_RELEASE_STATE", f"lease release is not allowed from guardian-visible state {terminal}")
            session["active_transaction"] = None
            remaining = session.get("available") or {}
            has_remaining = any(any(value.values()) for value in remaining.values())
            payload["current_step"] = "WORKSHOP"
            payload["next_step"] = "PATCH" if has_remaining else "HEARTBEAT"
            self._append(payload, "TX_RELEASE", {"transaction_id": transaction_id, "state": terminal, "rolling_package_sha256": payload["rolling_package_sha256"], "next_step": payload["next_step"]})
            self._save(payload)
            return self._view(payload)
