from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .database import KnowledgeDatabase
from .util import read_json


EDGE_SCHEMA = "kairos-compiler-include-edges/v1"
EDGE_ARTIFACT_ID = "PROJECT_SOURCE_INDEX"
EDGE_EVIDENCE_TARGET = "s-include-edges"
EDGE_KEYS = (
    "compiled_root",
    "including_file",
    "include_line",
    "include_token",
    "delimiter",
    "resolved_target",
    "resolution_class",
    "compiler_variant",
    "resolution_path",
)


class IncludeEdgeError(RuntimeError):
    pass


def _canonical_edges(edges: Any) -> list[dict[str, Any]]:
    if not isinstance(edges, list):
        raise IncludeEdgeError("include-edge authority edges must be an array")
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for ordinal, raw in enumerate(edges, 1):
        if not isinstance(raw, dict):
            raise IncludeEdgeError(f"include-edge row {ordinal} is not an object")
        missing = [key for key in EDGE_KEYS if key not in raw]
        if missing:
            raise IncludeEdgeError(f"include-edge row {ordinal} is missing: {', '.join(missing)}")
        try:
            row = {
                "compiled_root": str(raw["compiled_root"]).replace("\\", "/"),
                "including_file": str(raw["including_file"]).replace("\\", "/"),
                "include_line": int(raw["include_line"]),
                "include_token": str(raw["include_token"]),
                "delimiter": str(raw["delimiter"]),
                "resolved_target": str(raw["resolved_target"]).replace("\\", "/"),
                "resolution_class": str(raw["resolution_class"]),
                "compiler_variant": int(raw["compiler_variant"]),
                "resolution_path": str(raw["resolution_path"]),
            }
        except (TypeError, ValueError) as exc:
            raise IncludeEdgeError(f"include-edge row {ordinal} has invalid scalar values") from exc
        if row["include_line"] < 1 or row["compiler_variant"] < 1:
            raise IncludeEdgeError(f"include-edge row {ordinal} has an invalid positive integer")
        if row["delimiter"] not in {"quote", "angle"}:
            raise IncludeEdgeError(f"include-edge row {ordinal} has an invalid delimiter")
        if row["resolution_class"] not in {"local", "external"}:
            raise IncludeEdgeError(f"include-edge row {ordinal} has an invalid resolution class")
        for key in ("compiled_root", "including_file", "include_token", "resolved_target", "resolution_path"):
            if not row[key]:
                raise IncludeEdgeError(f"include-edge row {ordinal} has an empty {key}")
        key = tuple(row[name] for name in EDGE_KEYS)
        rows[key] = row
    return [rows[key] for key in sorted(rows)]


def topology_sha256(edges: list[dict[str, Any]]) -> str:
    payload = json.dumps(_canonical_edges(edges), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_authority(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != EDGE_SCHEMA:
        raise IncludeEdgeError(f"unsupported include-edge authority: {path}")
    edges = _canonical_edges(payload.get("edges"))
    expected_digest = topology_sha256(edges)
    if payload.get("topology_sha256") != expected_digest:
        raise IncludeEdgeError("include-edge topology digest does not match canonical edges")
    if int(payload.get("edge_count", -1)) != len(edges):
        raise IncludeEdgeError("include-edge count does not match canonical edges")
    if payload.get("edges") != edges:
        raise IncludeEdgeError("include-edge authority is not canonically ordered")
    return {**payload, "edges": edges}


def configured_authority_path(workspace: Path) -> Path | None:
    machine_root = workspace.resolve() / ".kairos"
    config_path = machine_root / "workshop.config.json"
    if not config_path.is_file():
        return None
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise IncludeEdgeError("Workshop configuration is not an object")
    source_authority = config.get("source_authority") or {}
    if not isinstance(source_authority, dict):
        raise IncludeEdgeError("Workshop source_authority must be an object")
    relative = source_authority.get("include_edges_file")
    if relative is None:
        return None
    if not isinstance(relative, str) or not relative.strip():
        raise IncludeEdgeError("include_edges_file must be a non-empty machine-relative path")
    normalized = relative.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise IncludeEdgeError("include_edges_file escapes the KAIROS machine root")
    candidate = (machine_root / path).resolve()
    try:
        candidate.relative_to(machine_root)
    except ValueError as exc:
        raise IncludeEdgeError("include_edges_file escapes the KAIROS machine root") from exc
    return candidate


def project_compiler_include_edges(
    workspace: Path,
    database: KnowledgeDatabase,
    *,
    authority_path: Path | None = None,
) -> dict[str, Any]:
    """Project compiler-observed direct include edges into the governed SQLite graph.

    This is called only by the heartbeat or an isolated Workshop shadow. The machine
    authority is validated first and the reopened database must contain the exact same
    canonical rows before the projection is reported verified.
    """
    path = authority_path.resolve() if authority_path is not None else configured_authority_path(workspace)
    if path is None:
        return {
            "schema": "kairos-compiler-include-edge-projection/v1",
            "configured": False,
            "verified": True,
            "edge_count": 0,
            "topology_sha256": None,
        }
    if not path.is_file():
        raise IncludeEdgeError(f"configured include-edge authority is missing: {path}")
    authority = load_authority(path)
    edges = authority["edges"]
    database.initialize()
    with database.transaction() as connection:
        artifact = connection.execute(
            "SELECT artifact_id FROM artifacts WHERE artifact_id=?",
            (EDGE_ARTIFACT_ID,),
        ).fetchone()
        section = connection.execute(
            "SELECT section_id FROM sections WHERE artifact_id=? AND section_id=?",
            (EDGE_ARTIFACT_ID, EDGE_EVIDENCE_TARGET),
        ).fetchone()
        if not artifact or not section:
            raise IncludeEdgeError("PROJECT_SOURCE_INDEX#s-include-edges must exist before include-edge projection")
        connection.execute("DELETE FROM compiler_include_edges WHERE artifact_id=?", (EDGE_ARTIFACT_ID,))
        for ordinal, edge in enumerate(edges, 1):
            connection.execute(
                """
                INSERT INTO compiler_include_edges(
                    artifact_id,ordinal,compiled_root,including_file,include_line,include_token,
                    delimiter,resolved_target,resolution_class,compiler_variant,resolution_path,evidence_target
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    EDGE_ARTIFACT_ID,
                    ordinal,
                    edge["compiled_root"],
                    edge["including_file"],
                    edge["include_line"],
                    edge["include_token"],
                    edge["delimiter"],
                    edge["resolved_target"],
                    edge["resolution_class"],
                    edge["compiler_variant"],
                    edge["resolution_path"],
                    EDGE_EVIDENCE_TARGET,
                ),
            )
    connection = database.connect(read_only=True)
    try:
        actual = [
            {
                "compiled_root": row["compiled_root"],
                "including_file": row["including_file"],
                "include_line": int(row["include_line"]),
                "include_token": row["include_token"],
                "delimiter": row["delimiter"],
                "resolved_target": row["resolved_target"],
                "resolution_class": row["resolution_class"],
                "compiler_variant": int(row["compiler_variant"]),
                "resolution_path": row["resolution_path"],
            }
            for row in connection.execute(
                """
                SELECT compiled_root,including_file,include_line,include_token,delimiter,
                       resolved_target,resolution_class,compiler_variant,resolution_path
                FROM compiler_include_edges
                WHERE artifact_id=?
                ORDER BY ordinal
                """,
                (EDGE_ARTIFACT_ID,),
            ).fetchall()
        ]
    finally:
        connection.close()
    if actual != edges:
        raise IncludeEdgeError("reopened compiler_include_edges projection differs from authority")
    return {
        "schema": "kairos-compiler-include-edge-projection/v1",
        "configured": True,
        "verified": True,
        "edge_count": len(edges),
        "topology_sha256": authority["topology_sha256"],
        "authority_path": str(path),
    }
