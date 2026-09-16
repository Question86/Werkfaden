from __future__ import annotations

from .guardian_common import *


class GuardianReceiptMixin:
    def _active_gate_time(self, payload: dict[str, Any], purpose: str) -> datetime:
        expected = {
            "falsifier1": "FALSIFIER_1",
            "falsifier2": "FALSIFIER_2",
            "map": "MAP",
            "counterprobe": "COUNTERPROBE",
            "exact_source": "EXACT_SOURCE",
        }.get(purpose)
        gate = payload.get("active_gate") or {}
        if expected and gate.get("step") != expected:
            raise WorkshopError("GUARDIAN_MEMORY_GATE_MISSING", f"{expected} evidence requires its active Memory gate")
        opened = gate.get("opened_at")
        if not isinstance(opened, str):
            raise WorkshopError("GUARDIAN_MEMORY_GATE_MISSING", "evidence has no active Memory gate timestamp")
        return _parse_time(opened)

    def _evidence_highwater(self) -> dict[str, int]:
        with self._db() as connection:
            values: dict[str, int] = {}
            for key, table in (("srr_rowid", "search_routing_receipts"), ("sir_rowid", "source_inspection_receipts")):
                try:
                    row = connection.execute(f"SELECT COALESCE(MAX(rowid),0) AS value FROM {table}").fetchone()
                except sqlite3.Error as exc:
                    raise WorkshopError("GUARDIAN_EVIDENCE_TABLE_MISSING", f"required evidence table is unavailable: {table}") from exc
                values[key] = int(row["value"] if row else 0)
            return values


    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        if not self.kairos_database.is_file():
            raise WorkshopError("GUARDIAN_KAIROS_DB_MISSING", f"KAIROS database is missing: {self.kairos_database}")
        resolved = self.kairos_database.resolve()
        connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


    def _validate_srr(self, payload: dict[str, Any], receipt_id: str, *, purpose: str) -> dict[str, Any]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT rowid AS receipt_rowid,routing_receipt_id,status,task_id,criterion_id,receipt_json,created_at FROM search_routing_receipts WHERE routing_receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if not row:
                raise WorkshopError("GUARDIAN_ROUTING_RECEIPT_MISSING", f"routing receipt does not exist: {receipt_id}")
            receipt = json.loads(row["receipt_json"])
            gate = payload.get("active_gate") or {}
            binding = receipt.get("guardian")
            if not isinstance(binding, dict):
                raise WorkshopError("GUARDIAN_EVIDENCE_UNBOUND", f"routing receipt is not bound to a Guardian state gate: {receipt_id}")
            if (
                binding.get("guardian_id") != payload.get("guardian_id")
                or binding.get("state_ticket_id") != gate.get("ticket_id")
                or binding.get("step") != gate.get("step")
            ):
                raise WorkshopError("GUARDIAN_EVIDENCE_GATE_MISMATCH", f"routing receipt belongs to another Guardian state gate: {receipt_id}")
            project_scope = payload.get("project_scope") or {}
            if project_scope.get("task_id") and row["task_id"] != project_scope.get("task_id"):
                raise WorkshopError("GUARDIAN_EVIDENCE_SCOPE_MISMATCH", f"routing receipt belongs to another task: {receipt_id}")
            if project_scope.get("criterion_id") and row["criterion_id"] != project_scope.get("criterion_id"):
                raise WorkshopError("GUARDIAN_EVIDENCE_SCOPE_MISMATCH", f"routing receipt belongs to another criterion: {receipt_id}")
            gate = payload.get("active_gate") or {}
            floor = int((gate.get("evidence_floor") or {}).get("srr_rowid", 0))
            if int(row["receipt_rowid"]) <= floor:
                raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"routing receipt existed before the state Memory gate: {receipt_id}")
            if _parse_time(str(row["created_at"])) < self._active_gate_time(payload, purpose):
                raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"routing receipt timestamp predates the state Memory gate: {receipt_id}")
            freshness = receipt.get("freshness") or {}
            if not freshness.get("checked") or (freshness.get("refreshed") and not freshness.get("verified")):
                raise WorkshopError("GUARDIAN_EVIDENCE_STALE", f"routing receipt lacks verified freshness: {receipt_id}")
            status = str(row["status"])
            if purpose in {"falsifier1", "falsifier2", "map"} and status != "ROUTED":
                raise WorkshopError("GUARDIAN_EVIDENCE_STATUS", f"{purpose} requires ROUTED receipt, found {status}")
            if purpose == "counterprobe" and status not in {"ROUTED", "NO_MATCH"}:
                raise WorkshopError("GUARDIAN_EVIDENCE_STATUS", f"counterprobe cannot use {status}")
            graph_route = receipt.get("graph_route")
            if purpose == "falsifier1" and graph_route:
                raise WorkshopError("GUARDIAN_FALSIFIER_NOT_INDEPENDENT", "FALSIFIER_1 must be semantic/authority evidence, not graph-routed evidence")
            if purpose in {"falsifier2", "map"}:
                if not isinstance(graph_route, dict) or int(graph_route.get("seed_total", 0)) <= 0:
                    raise WorkshopError("GUARDIAN_GRAPH_EVIDENCE_REQUIRED", f"{purpose} requires exact graph-routed evidence")
                if graph_route.get("truncated"):
                    raise WorkshopError("GUARDIAN_EVIDENCE_TRUNCATED", f"graph route is truncated: {receipt_id}")
                if int(graph_route.get("resolved_anchor_total", 0)) <= 0:
                    raise WorkshopError("GUARDIAN_GRAPH_ANCHOR_UNRESOLVED", f"graph evidence has no resolved authority anchor: {receipt_id}")
            primary = receipt.get("primary") or {}
            source_snapshot = None
            if primary.get("artifact_id"):
                artifact = connection.execute(
                    "SELECT path,authority,revision,content_sha256 FROM artifacts WHERE artifact_id=?",
                    (primary["artifact_id"],),
                ).fetchone()
                if not artifact:
                    raise WorkshopError("GUARDIAN_AUTHORITY_MISSING", f"routing primary artifact disappeared: {primary['artifact_id']}")
                if self.kairos_workspace:
                    source = (self.kairos_workspace / artifact["path"]).resolve()
                    try:
                        source.relative_to(self.kairos_workspace)
                    except ValueError as exc:
                        raise WorkshopError("GUARDIAN_AUTHORITY_PATH_ESCAPE", "routing primary path escapes workspace") from exc
                    if source.is_file():
                        normalized = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
                        if sha256_text(normalized) != artifact["content_sha256"]:
                            raise WorkshopError("GUARDIAN_AUTHORITY_DRIFT", f"routing primary source differs from DB projection: {artifact['path']}")
                    source_snapshot = {"path": artifact["path"], "authority": artifact["authority"], "revision": int(artifact["revision"]), "content_sha256": artifact["content_sha256"]}
            return {
                "id": receipt_id,
                "kind": "SRR",
                "status": status,
                "created_at": row["created_at"],
                "query": receipt.get("query"),
                "query_sha256": sha256_text(str(receipt.get("query", ""))),
                "primary": primary,
                "graph_route": graph_route,
                "source_snapshot": source_snapshot,
                "receipt_sha256": sha256_text(canonical_json(receipt)),
            }


    def _validate_sir(self, payload: dict[str, Any], receipt_id: str, *, positive: bool | None = None, purpose: str = "exact_source") -> dict[str, Any]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT rowid AS receipt_rowid,inspection_id,permit_id,task_id,criterion_id,status,match_count,receipt_json,created_at FROM source_inspection_receipts WHERE inspection_id=?",
                (receipt_id,),
            ).fetchone()
            if not row:
                raise WorkshopError("GUARDIAN_SOURCE_RECEIPT_MISSING", f"source receipt does not exist: {receipt_id}")
            receipt = json.loads(row["receipt_json"])
            gate = payload.get("active_gate") or {}
            binding = receipt.get("guardian")
            if not isinstance(binding, dict):
                raise WorkshopError("GUARDIAN_EVIDENCE_UNBOUND", f"source receipt is not bound to a Guardian state gate: {receipt_id}")
            if (
                binding.get("guardian_id") != payload.get("guardian_id")
                or binding.get("state_ticket_id") != gate.get("ticket_id")
                or binding.get("step") != gate.get("step")
            ):
                raise WorkshopError("GUARDIAN_EVIDENCE_GATE_MISMATCH", f"source receipt belongs to another Guardian state gate: {receipt_id}")
            scope = payload.get("project_scope") or {}
            if scope.get("task_id") and row["task_id"] != scope.get("task_id"):
                raise WorkshopError("GUARDIAN_EVIDENCE_SCOPE_MISMATCH", f"source receipt belongs to another task: {receipt_id}")
            if scope.get("criterion_id") and row["criterion_id"] != scope.get("criterion_id"):
                raise WorkshopError("GUARDIAN_EVIDENCE_SCOPE_MISMATCH", f"source receipt belongs to another criterion: {receipt_id}")
            gate = payload.get("active_gate") or {}
            floor = int((gate.get("evidence_floor") or {}).get("sir_rowid", 0))
            if int(row["receipt_rowid"]) <= floor:
                raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"source receipt existed before the state Memory gate: {receipt_id}")
            if _parse_time(str(row["created_at"])) < self._active_gate_time(payload, purpose):
                raise WorkshopError("GUARDIAN_EVIDENCE_REPLAY", f"source receipt timestamp predates the state Memory gate: {receipt_id}")
            if row["status"] != "VERIFIED" or receipt.get("status") != "VERIFIED" or not receipt.get("scope_stable"):
                raise WorkshopError("GUARDIAN_SOURCE_NOT_VERIFIED", f"source receipt is not stable VERIFIED evidence: {receipt_id}")
            if receipt.get("truncated"):
                raise WorkshopError("GUARDIAN_EVIDENCE_TRUNCATED", f"source receipt is truncated: {receipt_id}")
            if receipt.get("exception"):
                raise WorkshopError("GUARDIAN_SOURCE_EXCEPTION_UNSUPPORTED", "Guardian exact-source evidence must use normal routed escalation")
            count = int(row["match_count"])
            if positive is True and count <= 0:
                raise WorkshopError("GUARDIAN_SOURCE_MATCH_REQUIRED", "EXACT_SOURCE requires a positive predicted-source match")
            if positive is False and count != 0:
                raise WorkshopError("GUARDIAN_NEGATIVE_PROBE_FAILED", "counterprobe expected zero source matches")
            permit = connection.execute(
                "SELECT routing_receipt_id,purpose,scope_sha256,receipt_json FROM source_inspection_permits WHERE permit_id=?",
                (row["permit_id"],),
            ).fetchone()
            if not permit:
                raise WorkshopError("GUARDIAN_SOURCE_PERMIT_MISSING", f"source permit disappeared: {row['permit_id']}")
            paths = sorted(str(value) for value in receipt.get("paths") or [])
            matches: list[dict[str, Any]] = []
            for match in receipt.get("matches") or []:
                if not isinstance(match, dict) or not isinstance(match.get("path"), str):
                    continue
                try:
                    line = int(match.get("line"))
                except (TypeError, ValueError):
                    continue
                if line > 0:
                    matches.append({
                        "path": _safe_rel(match["path"], label="source match path"),
                        "line": line,
                        "patterns": [str(value) for value in match.get("patterns") or []],
                    })
            path_facts: list[dict[str, Any]] = []
            if self.codebase_root:
                for relative in paths:
                    safe = _safe_rel(relative, label="source receipt path")
                    candidate = (self.codebase_root / safe).resolve()
                    try:
                        candidate.relative_to(self.codebase_root)
                    except ValueError as exc:
                        raise WorkshopError("GUARDIAN_SOURCE_PATH_ESCAPE", f"source receipt path escapes codebase: {relative}") from exc
                    reject_link_components(candidate, label="source receipt path")
                    if not candidate.is_file():
                        raise WorkshopError("GUARDIAN_SOURCE_FILE_REQUIRED", f"Guardian evidence requires exact source files, not directories: {relative}")
                    raw = candidate.read_bytes()
                    path_facts.append({"path": safe, "sha256": sha256_bytes(raw), "bytes": len(raw)})
            return {
                "id": receipt_id,
                "kind": "SIR",
                "permit_id": row["permit_id"],
                "routing_receipt_id": permit["routing_receipt_id"],
                "purpose": permit["purpose"],
                "scope_sha256": permit["scope_sha256"],
                "paths": paths,
                "path_facts": path_facts,
                "matches": matches,
                "match_count": count,
                "created_at": row["created_at"],
                "receipt_sha256": sha256_text(canonical_json(receipt)),
            }
