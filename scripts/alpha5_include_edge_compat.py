from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"compat patch anchor missing: {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_graph_compat() -> None:
    path = ROOT / "kairos/kairos_harness/kairos/graph.py"
    text = path.read_text(encoding="utf-8")
    if "def _available_tables(" not in text:
        anchor = '''def _total(connection, sql: str, parameters: tuple[Any, ...]) -> int:\n    return int(connection.execute(sql, parameters).fetchone()[0])\n\n\n'''
        helper = anchor + '''def _available_tables(connection) -> set[str]:\n    """Return normalized graph tables present in this database.\n\n    ``compiler_include_edges`` is additive in alpha.5. Read-only alpha.4 framework\n    bundles and existing workspaces remain valid inputs; absence means the stronger\n    edge authority is unavailable, never that the known graph is corrupt.\n    """\n    return {\n        str(row[0])\n        for row in connection.execute(\n            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"\n        ).fetchall()\n    }\n\n\n'''
        if anchor not in text:
            raise SystemExit("graph helper anchor missing")
        text = text.replace(anchor, helper, 1)

    # resolve_identity: do not query an additive table absent from an older read-only DB.
    old = '''    counts: dict[str, dict[str, Any]] = {}\n    for table, fields in _SEARCH_FIELDS.items():\n        for field in fields:\n'''
    new = '''    counts: dict[str, dict[str, Any]] = {}\n    available = _available_tables(connection)\n    for table, fields in _SEARCH_FIELDS.items():\n        if table not in available:\n            continue\n        for field in fields:\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("resolve_identity compatibility anchor missing")
        text = text.replace(old, new, 1)

    # query_graph_context: explicitly report absent optional tables as zero rows.
    old = '''    facts: list[dict[str, Any]] = []\n    table_totals: dict[str, int] = {}\n    unresolved_anchor_total = 0\n    for table in _SEARCH_FIELDS:\n        where, parameters = _literal_where(table, identifiers)\n'''
    new = '''    facts: list[dict[str, Any]] = []\n    table_totals: dict[str, int] = {}\n    unresolved_anchor_total = 0\n    available = _available_tables(connection)\n    for table in _SEARCH_FIELDS:\n        if table not in available:\n            table_totals[table] = 0\n            continue\n        where, parameters = _literal_where(table, identifiers)\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("query_graph_context compatibility anchor missing")
        text = text.replace(old, new, 1)

    # inventory: named additive table can be queried safely even on an old bundle.
    old = '''    table = INVENTORY_TABLES[table_name]\n    bound = _bounded(limit)\n    where, parameters = "1=1", ()\n'''
    new = '''    table = INVENTORY_TABLES[table_name]\n    bound = _bounded(limit)\n    if table not in _available_tables(connection):\n        return {\n            "schema": "kairos-graph-inventory/v1",\n            "table": table_name,\n            "filter": {"field": field, "value": value} if field else None,\n            "available": False,\n            "total": 0,\n            "returned": 0,\n            "truncated": False,\n            "documents": 0,\n            "grouped_by": INVENTORY_GROUPING[table],\n            "groups": [],\n            "rows": [],\n        }\n    where, parameters = "1=1", ()\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("inventory compatibility anchor missing")
        text = text.replace(old, new, 1)

    # artifact_graph: keep response shape but mark missing additive surfaces unavailable.
    old = '''    result: dict[str, Any] = {"artifact_id": artifact_id, "tables": {}}\n    for table in _TABLES:\n        total = _total(\n            connection, f"SELECT count(*) FROM {table} WHERE artifact_id=?", (artifact_id,)\n        )\n'''
    new = '''    result: dict[str, Any] = {"artifact_id": artifact_id, "tables": {}}\n    available = _available_tables(connection)\n    for table in _TABLES:\n        if table not in available:\n            result["tables"][table] = {\n                "available": False, "total": 0, "returned": 0,\n                "truncated": False, "rows": [],\n            }\n            continue\n        total = _total(\n            connection, f"SELECT count(*) FROM {table} WHERE artifact_id=?", (artifact_id,)\n        )\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("artifact_graph compatibility anchor missing")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def patch_bounded_markdown() -> None:
    # Exact edges live in machine authority + SQLite. Markdown binds the topology by
    # count + digest so a large C++ graph cannot turn PROJECT_SOURCE_INDEX into a dump.
    for relative in ("kickstart/blueprints.py", "kickstart/universal_workshop.py"):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        old = '''    canonical_edges = include_edges or []\n    edge_rows = "\\n".join(\n        "- " + json.dumps(edge, ensure_ascii=False, sort_keys=True, separators=(",", ":"))\n        for edge in canonical_edges\n    ) or "- none"\n    edge_body = f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\\n\\n{edge_rows}"\n'''
        new = '''    canonical_edges = include_edges or []\n    edge_body = (\n        f"Edge count: `{len(canonical_edges)}`\\n"\n        f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\\n\\n"\n        "Exact compiler-observed rows are retained in machine authority and the SQLite projection; "\n        "this human-readable index binds them by count and digest without duplicating the graph."\n    )\n'''
        if new not in text:
            if old not in text:
                raise SystemExit(f"bounded Markdown anchor missing: {relative}")
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")

    corpus = ROOT / "workshop/src/runtime_sync_workshop/corpus.py"
    text = corpus.read_text(encoding="utf-8")
    old = '''    digest_match = re.search(r"Topology SHA-256:\\s*`([0-9a-f]{64})`", section)\n    if not digest_match:\n        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_DIGEST_MISSING", "include-edge section has no topology digest")\n    edges: list[dict[str, Any]] = []\n    for line in section.splitlines():\n        stripped = line.strip()\n        if not stripped.startswith("- {"):\n            continue\n        try:\n            value = json.loads(stripped[2:])\n        except json.JSONDecodeError as exc:\n            raise WorkshopError("DATAFLOW_INCLUDE_EDGE_ROW_INVALID", f"invalid include-edge JSON row: {exc}") from exc\n        if not isinstance(value, dict):\n            raise WorkshopError("DATAFLOW_INCLUDE_EDGE_ROW_INVALID", "include-edge row must be a JSON object")\n        edges.append(value)\n    canonical = canonical_include_edges(edges)\n    if edges != canonical:\n        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_NONCANONICAL", "include-edge source-index rows are not canonically ordered")\n    return {"topology_sha256": digest_match.group(1), "edges": canonical, "edge_count": len(canonical)}\n'''
    new = '''    count_match = re.search(r"Edge count:\\s*`([0-9]+)`", section)\n    digest_match = re.search(r"Topology SHA-256:\\s*`([0-9a-f]{64})`", section)\n    if not count_match or not digest_match:\n        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_DIGEST_MISSING", "include-edge section has no edge count/topology digest")\n    return {\n        "topology_sha256": digest_match.group(1),\n        "edge_count": int(count_match.group(1)),\n    }\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("corpus bounded index parser anchor missing")
        text = text.replace(old, new, 1)
    old_compare = '''        declared_edges = declared_include_edges(config)\n        if declared_edges is None or declared_edges["edges"] != include_edge_authority["edges"] or declared_edges["topology_sha256"] != include_edge_authority["topology_sha256"]:\n            issues.append({\n                "code": "INCLUDE_EDGE_INDEX_MISMATCH",\n                "message": "PROJECT_SOURCE_INDEX direct include-edge projection differs from machine authority",\n            })\n'''
    new_compare = '''        declared_edges = declared_include_edges(config)\n        if (\n            declared_edges is None\n            or declared_edges["edge_count"] != include_edge_authority["edge_count"]\n            or declared_edges["topology_sha256"] != include_edge_authority["topology_sha256"]\n        ):\n            issues.append({\n                "code": "INCLUDE_EDGE_INDEX_MISMATCH",\n                "message": "PROJECT_SOURCE_INDEX direct include-edge digest/count differs from machine authority",\n            })\n'''
    if new_compare not in text:
        if old_compare not in text:
            raise SystemExit("corpus bounded compare anchor missing")
        text = text.replace(old_compare, new_compare, 1)
    corpus.write_text(text, encoding="utf-8")

    engine = ROOT / "workshop/src/runtime_sync_workshop/engine.py"
    text = engine.read_text(encoding="utf-8")
    old = '''            edge_rows = [f"Topology SHA-256: `{topology_sha256}`", ""]\n            edge_rows.extend("- " + canonical_json(edge) for edge in canonical_include_edges(include_edges))\n            if not include_edges:\n                edge_rows.append("- none")\n            body = replace_rows(body, edge_section, edge_rows)\n'''
    new = '''            canonical = canonical_include_edges(include_edges)\n            edge_rows = [\n                f"Edge count: `{len(canonical)}`",\n                f"Topology SHA-256: `{topology_sha256}`",\n                "",\n                "Exact compiler-observed rows are retained in machine authority and the SQLite projection; this index binds them by count and digest.",\n            ]\n            body = replace_rows(body, edge_section, edge_rows)\n'''
    if new not in text:
        if old not in text:
            raise SystemExit("engine bounded edge renderer anchor missing")
        text = text.replace(old, new, 1)
    engine.write_text(text, encoding="utf-8")


def patch_version() -> None:
    framework = ROOT / "kairos/kairos_harness/kairos/framework.py"
    replace_once(framework, 'FRAMEWORK_VERSION = "0.1.0-alpha.4"', 'FRAMEWORK_VERSION = "0.1.0-alpha.5"')


def main() -> None:
    patch_graph_compat()
    patch_bounded_markdown()
    patch_version()


if __name__ == "__main__":
    main()
