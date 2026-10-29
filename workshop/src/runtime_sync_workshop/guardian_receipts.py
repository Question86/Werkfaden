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


    def _source_receipt_facts(
        self, payload: dict[str, Any], permit: dict[str, Any], receipt: dict[str, Any],
        paths: list[str], matches: list[dict[str, Any]], *, positive: bool | None,
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], str]:
        import re
        from pathlib import Path

        try:
            origin = json.loads(permit["receipt_json"])
        except (TypeError, ValueError, KeyError) as exc:
            raise WorkshopError("GUARDIAN_SOURCE_ORIGIN_INVALID", "source permit origin is unreadable") from exc
        relative_root = origin.get("root_relative")
        if not isinstance(relative_root, str) or not relative_root or Path(relative_root).is_absolute():
            raise WorkshopError("GUARDIAN_SOURCE_ROOT_UNBOUND", "source permit requires its explicit relative inspection root")
        if not self.kairos_workspace or not self.codebase_root:
            raise WorkshopError("GUARDIAN_SOURCE_ROOT_UNBOUND", "source proof requires its workspace and codebase binding")
        workspace = self.kairos_workspace.resolve()
        codebase = self.codebase_root.resolve()
        root = (workspace / relative_root).resolve()
        try:
            root.relative_to(workspace.parent)
        except ValueError as exc:
            raise WorkshopError("GUARDIAN_SOURCE_ROOT_ESCAPE", "inspection origin escapes its declared project boundary") from exc
        reject_link_components(root, label="Guardian inspection origin")
        # Native v1 SIR binds the digest through its exact permit foreign key.
        # If a receipt also repeats the digest, that duplicate must agree.
        if "scope_sha256" in receipt and receipt["scope_sha256"] != permit["scope_sha256"]:
            raise WorkshopError("GUARDIAN_SOURCE_SCOPE_MISMATCH", "source receipt differs from its permit scope")
        for key in ("guardian_id", "state_ticket_id", "step"):
            if (origin.get("guardian") or {}).get(key) != (receipt.get("guardian") or {}).get(key):
                raise WorkshopError("GUARDIAN_EVIDENCE_GATE_MISMATCH", "source permit and receipt have different Guardian bindings")
        if sorted(origin.get("paths") or []) != paths:
            raise WorkshopError("GUARDIAN_SOURCE_SCOPE_MISMATCH", "source receipt paths differ from its permit")
        snapshots = {item["path"]: item for item in origin.get("file_snapshot", [])
                     if isinstance(item, dict) and isinstance(item.get("path"), str)}
        if set(snapshots) != set(paths):
            raise WorkshopError("GUARDIAN_SOURCE_SNAPSHOT_MISSING", "exact inspected files require their permit snapshots")

        aliases = None

        def ledger_aliases():
            nonlocal aliases
            if aliases is not None:
                return aliases
            from .engine import WorkshopEngine
            from .corpus import build_corpus_manifest
            from .util import read_json
            config_path = workspace / ".kairos" / "workshop.config.json"
            if not config_path.is_file():
                raise WorkshopError("GUARDIAN_SOURCE_AUTHORITY_MISSING", "managed ledger proof requires canonical Workshop authority")
            engine = WorkshopEngine(config_path)
            if (engine.config.codebase_root.resolve() != codebase
                    or engine.config.kairos_workspace.resolve() != workspace
                    or engine.config.kairos_database.resolve() != self.kairos_database.resolve()):
                raise WorkshopError("GUARDIAN_SOURCE_AUTHORITY_MISMATCH", "ledger authority belongs to another project or database")
            manifest = build_corpus_manifest(engine.config)
            seal = read_json(engine.seal_path) if engine.seal_path.is_file() else {}
            expected = str(payload.get("rolling_package_sha256") or "")
            if (not manifest.get("verified") or manifest.get("package_sha256") != expected
                    or seal.get("package_sha256") != expected):
                raise WorkshopError("GUARDIAN_SOURCE_AUTHORITY_INVALID", "ledger source must match the verified current sealed package")
            aliases = {}
            for record in manifest["records"].values():
                document = record.get("database") or {}
                if not document.get("path"):
                    continue
                path = (workspace / document["path"]).resolve()
                if not path.is_relative_to(workspace):
                    raise WorkshopError("GUARDIAN_SOURCE_PATH_ESCAPE", "native ledger path escapes its workspace")
                bindings = {}
                for label, role in (("C", "source"), ("H", "header")):
                    facts = record.get(role)
                    if not isinstance(facts, dict) or not facts.get("path"):
                        continue
                    source = Path(facts["path"])
                    source = source.resolve() if source.is_absolute() else (codebase / source).resolve()
                    if not source.is_relative_to(codebase):
                        raise WorkshopError("GUARDIAN_SOURCE_PATH_ESCAPE", "native source binding escapes its codebase")
                    bindings[label] = (source.relative_to(codebase).as_posix(), source, facts["sha256"])
                if not bindings:
                    continue
                previous = aliases.setdefault(path, (bindings, document["content_sha256"]))
                if previous != (bindings, document["content_sha256"]):
                    raise WorkshopError("GUARDIAN_SOURCE_AUTHORITY_MISMATCH", "native ledger has ambiguous source ownership")
            return aliases

        facts_by_path: dict[str, dict[str, Any]] = {}
        normalized_matches = []
        for relative in paths:
            safe = _safe_rel(relative, label="source receipt path")
            inspected = (root / safe).resolve()
            if not inspected.is_relative_to(root):
                raise WorkshopError("GUARDIAN_SOURCE_PATH_ESCAPE", "source receipt escapes its actual inspection origin")
            reject_link_components(inspected, label="source receipt path")
            if not inspected.is_file():
                raise WorkshopError("GUARDIAN_SOURCE_FILE_REQUIRED", f"exact inspected file is missing: {relative}")
            stat = inspected.stat()
            snapshot = snapshots[relative]
            if stat.st_size != snapshot.get("size") or stat.st_mtime_ns != snapshot.get("mtime_ns"):
                raise WorkshopError("GUARDIAN_SOURCE_SNAPSHOT_CHANGED", "inspected file changed after source permit issuance")
            selected = [match for match in matches if match["path"] == relative]
            if inspected.is_relative_to(workspace) and codebase != workspace:
                mapping = ledger_aliases().get(inspected)
                if mapping is None:
                    raise WorkshopError("GUARDIAN_SOURCE_LEDGER_UNOWNED", "workspace source proof is not a verified owned implementation ledger")
                if positive is not True:
                    raise WorkshopError("GUARDIAN_SOURCE_LEDGER_NEGATIVE_UNSUPPORTED", "decorated ledger absence is not raw-source regex absence")
                bindings, document_sha = mapping
                raw = inspected.read_bytes()
                text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                if sha256_text(text) != document_sha:
                    raise WorkshopError("GUARDIAN_SOURCE_LEDGER_CHANGED", "implementation ledger differs from its native projection")
                lines = text.splitlines()
                starts = [index for index, line in enumerate(lines) if line == '<a id="s-source-ledger"></a>']
                if len(starts) != 1:
                    raise WorkshopError("GUARDIAN_SOURCE_LEDGER_INVALID", "implementation ledger requires its unique source section")
                end = next((index for index in range(starts[0] + 1, len(lines))
                            if lines[index].startswith('<a id="')), len(lines))
                if not selected:
                    raise WorkshopError("GUARDIAN_SOURCE_LEDGER_MATCH_REQUIRED", "each inspected ledger requires actual source-line evidence")
                for match in selected:
                    index = match["line"] - 1
                    if not starts[0] < index < end:
                        raise WorkshopError("GUARDIAN_SOURCE_LEDGER_MATCH_INVALID", "metadata or history is not exact source-line evidence")
                    numbered = re.fullmatch(r"([CH]):([0-9]+) (.*)", lines[index])
                    if numbered is None or numbered.group(1) not in bindings:
                        raise WorkshopError("GUARDIAN_SOURCE_LEDGER_MATCH_INVALID", "matched line has no native source/header binding")
                    source_relative, source, expected_sha = bindings[numbered.group(1)]
                    reject_link_components(source, label="native ledger source")
                    source_raw = source.read_bytes()
                    if sha256_bytes(source_raw) != expected_sha:
                        raise WorkshopError("GUARDIAN_SOURCE_LEDGER_CHANGED", "native ledger source bytes changed")
                    source_lines = source_raw.decode("utf-8").splitlines()
                    line = int(numbered.group(2))
                    if line < 1 or line > len(source_lines) or source_lines[line - 1] != numbered.group(3):
                        raise WorkshopError("GUARDIAN_SOURCE_LEDGER_MATCH_INVALID", "ledger line does not equal its declared source bytes")
                    facts_by_path[source_relative] = {
                        "path": source_relative, "sha256": expected_sha, "bytes": len(source_raw),
                        "inspection_path": relative, "inspection_sha256": sha256_bytes(raw),
                        "source_view": "managed_ledger",
                    }
                    normalized_matches.append({**match, "path": source_relative, "line": line,
                                               "inspection_path": relative, "inspection_line": match["line"]})
            else:
                if not inspected.is_relative_to(codebase):
                    raise WorkshopError("GUARDIAN_SOURCE_ROOT_MISMATCH", "raw inspected file is outside its bound codebase")
                source_relative = inspected.relative_to(codebase).as_posix()
                raw = inspected.read_bytes()
                facts_by_path[source_relative] = {"path": source_relative, "sha256": sha256_bytes(raw), "bytes": len(raw),
                                                 "inspection_path": relative, "source_view": "raw"}
                normalized_matches.extend({**match, "path": source_relative} for match in selected)
        return sorted(facts_by_path), normalized_matches, list(facts_by_path.values()), relative_root

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
            inspection_paths = list(paths)
            inspection_matches = list(matches)
            if len(matches) != count:
                raise WorkshopError("GUARDIAN_SOURCE_MATCH_INVALID", "source receipt match count or coordinates are invalid")
            paths, matches, path_facts, inspection_root = self._source_receipt_facts(
                payload, permit, receipt, paths, matches, positive=positive,
            )
            return {
                "id": receipt_id,
                "kind": "SIR",
                "permit_id": row["permit_id"],
                "routing_receipt_id": permit["routing_receipt_id"],
                "purpose": permit["purpose"],
                "scope_sha256": permit["scope_sha256"],
                "paths": paths,
                "path_facts": path_facts,
                "inspection_paths": inspection_paths,
                "inspection_matches": inspection_matches,
                "inspection_root_relative": inspection_root,
                "matches": matches,
                "match_count": count,
                "created_at": row["created_at"],
                "receipt_sha256": sha256_text(canonical_json(receipt)),
            }
