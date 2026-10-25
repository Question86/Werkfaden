from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"patch anchor missing: {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_block(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    if replacement.strip() in text:
        return
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def patch_database() -> None:
    path = ROOT / "kairos/kairos_harness/kairos/database.py"
    text = path.read_text(encoding="utf-8")
    if "CREATE TABLE IF NOT EXISTS compiler_include_edges" in text:
        return
    anchor = "CREATE TABLE IF NOT EXISTS search_policy_violations ("
    addition = '''CREATE TABLE IF NOT EXISTS compiler_include_edges (\n    artifact_id TEXT NOT NULL,\n    ordinal INTEGER NOT NULL,\n    compiled_root TEXT NOT NULL,\n    including_file TEXT NOT NULL,\n    include_line INTEGER NOT NULL,\n    include_token TEXT NOT NULL,\n    delimiter TEXT NOT NULL,\n    resolved_target TEXT NOT NULL,\n    resolution_class TEXT NOT NULL,\n    compiler_variant INTEGER NOT NULL,\n    resolution_path TEXT NOT NULL,\n    evidence_target TEXT NOT NULL,\n    PRIMARY KEY (artifact_id, ordinal),\n    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE\n);\nCREATE INDEX IF NOT EXISTS idx_compiler_include_edges_including\nON compiler_include_edges(including_file, resolved_target);\nCREATE INDEX IF NOT EXISTS idx_compiler_include_edges_target\nON compiler_include_edges(resolved_target, compiled_root);\nCREATE INDEX IF NOT EXISTS idx_compiler_include_edges_root\nON compiler_include_edges(compiled_root, including_file);\n\n'''
    if anchor not in text:
        raise SystemExit("database schema anchor missing")
    path.write_text(text.replace(anchor, addition + anchor, 1), encoding="utf-8")


def patch_heartbeat() -> None:
    path = ROOT / "kairos/kairos_harness/kairos/heartbeat.py"
    text = path.read_text(encoding="utf-8")
    if "compiler_include_edge_projection" in text:
        return
    anchor = "    pending, failed = database.pending_counts()\n"
    addition = '''    compiler_include_edge_projection: dict[str, Any] = {\n        "schema": "kairos-compiler-include-edge-projection/v1",\n        "configured": False,\n        "verified": True,\n        "edge_count": 0,\n        "topology_sha256": None,\n    }\n    if not failures:\n        try:\n            from .include_edges import project_compiler_include_edges\n\n            compiler_include_edge_projection = project_compiler_include_edges(workspace, database)\n        except Exception as exc:\n            failures.append({"path": "<compiler-include-edges>", "error": str(exc)})\n\n'''
    if anchor not in text:
        raise SystemExit("heartbeat pending-count anchor missing")
    text = text.replace(anchor, addition + anchor, 1)
    receipt_anchor = '        "promotion_receipts": [value["receipt_id"] for value in receipts],\n'
    if receipt_anchor not in text:
        raise SystemExit("heartbeat receipt anchor missing")
    text = text.replace(
        receipt_anchor,
        receipt_anchor + '        "compiler_include_edges": compiler_include_edge_projection,\n',
        1,
    )
    path.write_text(text, encoding="utf-8")


def patch_graph() -> None:
    path = ROOT / "kairos/kairos_harness/kairos/graph.py"
    text = path.read_text(encoding="utf-8")
    if '"compiler_include_edges": (' in text:
        return
    text = text.replace(
        '_TABLES = ("graph_relations", "graph_contracts", "graph_artifacts", "graph_drift")',
        '_TABLES = ("graph_relations", "graph_contracts", "graph_artifacts", "graph_drift", "compiler_include_edges")',
        1,
    )
    search_anchor = '    "graph_drift": ("drift_id", "historical", "subject", "classification", "status"),\n}'
    search_replacement = '''    "graph_drift": ("drift_id", "historical", "subject", "classification", "status"),\n    "compiler_include_edges": (\n        "compiled_root", "including_file", "include_token", "resolved_target",\n        "resolution_class", "resolution_path",\n    ),\n}'''
    if search_anchor not in text:
        raise SystemExit("graph search-fields anchor missing")
    text = text.replace(search_anchor, search_replacement, 1)
    result_anchor = '''    "graph_drift": (\n        "drift_id", "historical", "subject", "classification", "summary", "status",\n    ),\n}'''
    result_replacement = '''    "graph_drift": (\n        "drift_id", "historical", "subject", "classification", "summary", "status",\n    ),\n    "compiler_include_edges": (\n        "compiled_root", "including_file", "include_line", "include_token", "delimiter",\n        "resolved_target", "resolution_class", "compiler_variant", "resolution_path",\n    ),\n}'''
    if result_anchor not in text:
        raise SystemExit("graph result-fields anchor missing")
    text = text.replace(result_anchor, result_replacement, 1)
    inv_anchor = '''    "artifacts": "graph_artifacts",\n    "drift": "graph_drift",\n}'''
    inv_replacement = '''    "artifacts": "graph_artifacts",\n    "drift": "graph_drift",\n    "include_edges": "compiler_include_edges",\n}'''
    if inv_anchor not in text:
        raise SystemExit("graph inventory table anchor missing")
    text = text.replace(inv_anchor, inv_replacement, 1)
    grouping_anchor = '''    "graph_artifacts": "operation",\n    "graph_drift": "status",\n}'''
    grouping_replacement = '''    "graph_artifacts": "operation",\n    "graph_drift": "status",\n    "compiler_include_edges": "resolution_class",\n}'''
    if grouping_anchor not in text:
        raise SystemExit("graph grouping anchor missing")
    text = text.replace(grouping_anchor, grouping_replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_blueprints() -> None:
    path = ROOT / "kickstart/blueprints.py"
    text = path.read_text(encoding="utf-8")
    if 'include_topology_sha256: str = ""' in text:
        return
    signature = '''    updated_at: str,\n    header_closure: dict[str, list[str]],\n) -> dict[str, str]:'''
    replacement = '''    updated_at: str,\n    header_closure: dict[str, list[str]],\n    include_edges: list[dict[str, object]] | None = None,\n    include_topology_sha256: str = "",\n) -> dict[str, str]:'''
    if signature not in text:
        raise SystemExit("render_binding_documents signature anchor missing")
    text = text.replace(signature, replacement, 1)
    include_rows_anchor = '''    include_rows = "\\n".join(\n        f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in owners)\n        for header, owners in sorted(header_closure.items())\n    ) or "- none"\n'''
    include_rows_replacement = include_rows_anchor + '''    canonical_edges = include_edges or []\n    edge_rows = "\\n".join(\n        "- " + json.dumps(edge, ensure_ascii=False, sort_keys=True, separators=(",", ":"))\n        for edge in canonical_edges\n    ) or "- none"\n    edge_body = f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\\n\\n{edge_rows}"\n'''
    if include_rows_anchor not in text:
        raise SystemExit("blueprint include rows anchor missing")
    text = text.replace(include_rows_anchor, include_rows_replacement, 1)
    answers_anchor = '''            {"intent": "dependency", "question": "Which local headers are in the static compiled include closure?", "target": "s-include-closure", "language": "en"},\n            {"intent": "provenance", "question": "Which hashes bind the initial project source authority?", "target": "s-provenance", "language": "en"},'''
    answers_replacement = '''            {"intent": "dependency", "question": "Which local headers are in the static compiled include closure?", "target": "s-include-closure", "language": "en"},\n            {"intent": "dependency", "question": "Which direct C-family include edges are compiler-observed?", "target": "s-include-edges", "language": "en"},\n            {"intent": "provenance", "question": "Which hashes bind the initial project source authority?", "target": "s-provenance", "language": "en"},'''
    if answers_anchor not in text:
        raise SystemExit("blueprint answers anchor missing")
    text = text.replace(answers_anchor, answers_replacement, 1)
    sections_anchor = '''        {"id": "s-include-closure", "title": "STATIC INCLUDE CLOSURE", "capsule": "Only local headers resolved from configured include roots and source directories are projected.", "content": include_rows},\n        {"id": "s-provenance",'''
    sections_replacement = '''        {"id": "s-include-closure", "title": "STATIC INCLUDE CLOSURE", "capsule": "Only local headers resolved from configured include roots and source directories are projected.", "content": include_rows},\n        {"id": "s-include-edges", "title": "DIRECT COMPILER INCLUDE EDGES", "capsule": "Direct include consumers are kept separate from transitive compiled roots and byte owners.", "content": edge_body},\n        {"id": "s-provenance",'''
    if sections_anchor not in text:
        raise SystemExit("blueprint sections anchor missing")
    text = text.replace(sections_anchor, sections_replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_binding() -> None:
    path = ROOT / "kickstart/binding.py"
    text = path.read_text(encoding="utf-8")
    if "include_edge_topology" in text and '"include_edges_file"' in text:
        return
    old = '''        headers, unresolved = _resolve_include_closure(project_root, survey)\n        if unresolved:\n'''
    new = '''        headers, unresolved = _resolve_include_closure(project_root, survey)\n        if unresolved:\n'''
    if old not in text:
        raise SystemExit("binding include closure anchor missing")
    # Keep the existing fail-closed unresolved classification, then collect direct edges.
    marker = '''        updated_at = utc_now()\n        blueprint_set = render_blueprints(\n'''
    injection = '''        from .include_edges import authority_payload, collect_include_edges, owners_from_edges\n        include_edges, edge_issues = collect_include_edges(project_root, survey)\n        if edge_issues:\n            raise KickstartError(\n                "INCLUDE_EDGE_AUTHORITY_UNVERIFIED",\n                "direct compiler include-edge authority is not fully provable",\n                details=edge_issues,\n            )\n        edge_payload = authority_payload(include_edges)\n        closure_owners = {relative: sorted(owners) for relative, (_, owners) in headers.items()}\n        if owners_from_edges(include_edges) != closure_owners:\n            raise KickstartError(\n                "INCLUDE_EDGE_CLOSURE_MISMATCH",\n                "direct include-edge traversal disagrees with the compiler-backed transitive header closure",\n                details={"edges": owners_from_edges(include_edges), "closure": closure_owners},\n            )\n        updated_at = utc_now()\n        blueprint_set = render_blueprints(\n'''
    if marker not in text:
        raise SystemExit("binding updated_at anchor missing")
    text = text.replace(marker, injection, 1)
    call_anchor = '''            updated_at=updated_at,\n            header_closure={relative: owners for relative, (_, owners) in headers.items()},\n        )'''
    call_replacement = '''            updated_at=updated_at,\n            header_closure={relative: owners for relative, (_, owners) in headers.items()},\n            include_edges=include_edges,\n            include_topology_sha256=edge_payload["topology_sha256"],\n        )'''
    if call_anchor not in text:
        raise SystemExit("binding render call anchor missing")
    text = text.replace(call_anchor, call_replacement, 1)
    build_anchor = '''        build_authority = intake_directory / "PROJECT_BUILD_AUTHORITY.cmake"\n        atomic_write_text(build_authority, _cmake_authority(survey.units))\n'''
    build_replacement = build_anchor + '''        include_edge_authority = intake_directory / "include-edges.json"\n        atomic_write_json(include_edge_authority, edge_payload)\n'''
    if build_anchor not in text:
        raise SystemExit("binding build authority anchor missing")
    text = text.replace(build_anchor, build_replacement, 1)
    machine_anchor = '''                "project-intake/PROJECT_BUILD_AUTHORITY.cmake",\n                "project-intake.json",\n'''
    machine_replacement = '''                "project-intake/PROJECT_BUILD_AUTHORITY.cmake",\n                "project-intake/include-edges.json",\n                "project-intake.json",\n'''
    if machine_anchor not in text:
        raise SystemExit("binding machine authority anchor missing")
    text = text.replace(machine_anchor, machine_replacement, 1)
    source_auth_anchor = '''                "translation_unit_extensions": ["c", "cc", "cpp", "cxx", "cu"],\n                "include_ownership_section_id": "s-include-closure",\n'''
    source_auth_replacement = '''                "translation_unit_extensions": ["c", "cc", "cpp", "cxx", "cu"],\n                "include_ownership_section_id": "s-include-closure",\n                "include_edges_section_id": "s-include-edges",\n                "include_edges_file": "project-intake/include-edges.json",\n'''
    if source_auth_anchor not in text:
        raise SystemExit("binding source authority anchor missing")
    text = text.replace(source_auth_anchor, source_auth_replacement, 1)
    manifest_anchor = '''        manifest["workshop_config_sha256"] = _sha256(json.dumps(workshop_config, sort_keys=True, separators=(",", ":")).encode("utf-8"))\n'''
    manifest_replacement = '''        manifest["include_edge_topology"] = {\n            "edge_count": edge_payload["edge_count"],\n            "topology_sha256": edge_payload["topology_sha256"],\n        }\n        manifest["workshop_config_sha256"] = _sha256(json.dumps(workshop_config, sort_keys=True, separators=(",", ":")).encode("utf-8"))\n'''
    if manifest_anchor not in text:
        raise SystemExit("binding manifest anchor missing")
    text = text.replace(manifest_anchor, manifest_replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_universal_workshop() -> None:
    path = ROOT / "kickstart/universal_workshop.py"
    text = path.read_text(encoding="utf-8")
    if '"include_edges_file": "project-intake/include-edges.json"' in text:
        return
    signature = '''    header_owners: dict[str, list[str]],\n    workspace_id: str,\n    updated_at: str,\n) -> str:'''
    replacement = '''    header_owners: dict[str, list[str]],\n    include_edges: list[dict[str, Any]] | None = None,\n    include_topology_sha256: str = "",\n    workspace_id: str,\n    updated_at: str,\n) -> str:'''
    if signature not in text:
        raise SystemExit("universal source index signature missing")
    text = text.replace(signature, replacement, 1)
    rows_anchor = '''    include_rows = "\\n".join(\n        f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in owners)\n        for header, owners in sorted(header_owners.items())\n    ) or "- none"\n'''
    rows_replacement = rows_anchor + '''    canonical_edges = include_edges or []\n    edge_rows = "\\n".join(\n        "- " + json.dumps(edge, ensure_ascii=False, sort_keys=True, separators=(",", ":"))\n        for edge in canonical_edges\n    ) or "- none"\n    edge_body = f"Topology SHA-256: `{include_topology_sha256 or 'none'}`\\n\\n{edge_rows}"\n'''
    if rows_anchor not in text:
        raise SystemExit("universal include rows anchor missing")
    text = text.replace(rows_anchor, rows_replacement, 1)
    answer_anchor = '''            {"intent": "dependency", "question": "Which C-family headers are compiler-reached?", "target": "s-include-closure", "language": "en"},\n'''
    answer_replacement = answer_anchor + '''            {"intent": "dependency", "question": "Which direct C-family include edges are compiler-observed?", "target": "s-include-edges", "language": "en"},\n'''
    if answer_anchor not in text:
        raise SystemExit("universal answer anchor missing")
    text = text.replace(answer_anchor, answer_replacement, 1)
    section_anchor = '''        {"id": "s-include-closure", "title": "C-FAMILY INCLUDE CLOSURE", "capsule": "Only compiler-resolved project-local headers and their owning translation units are listed.", "content": include_rows},\n        {"id": "s-boundary",'''
    section_replacement = '''        {"id": "s-include-closure", "title": "C-FAMILY INCLUDE CLOSURE", "capsule": "Only compiler-resolved project-local headers and their owning translation units are listed.", "content": include_rows},\n        {"id": "s-include-edges", "title": "DIRECT COMPILER INCLUDE EDGES", "capsule": "Direct include consumers are distinct from transitive compiled roots and byte owners.", "content": edge_body},\n        {"id": "s-boundary",'''
    if section_anchor not in text:
        raise SystemExit("universal section anchor missing")
    text = text.replace(section_anchor, section_replacement, 1)
    family_block = '''    survey = survey_compile_commands(compile_commands, project_root, source_kind="compile_commands")\n    headers, unresolved = _resolve_include_closure(project_root, survey)\n    if unresolved:\n        raise KickstartError(\n            "C_FAMILY_INCLUDE_AUTHORITY_UNVERIFIED",\n            "C-family compiler membership was found but its project-local include closure is not fully provable",\n            details=unresolved,\n        )\n    return survey, headers\n'''
    family_replacement = '''    survey = survey_compile_commands(compile_commands, project_root, source_kind="compile_commands")\n    headers, unresolved = _resolve_include_closure(project_root, survey)\n    if unresolved:\n        raise KickstartError(\n            "C_FAMILY_INCLUDE_AUTHORITY_UNVERIFIED",\n            "C-family compiler membership was found but its project-local include closure is not fully provable",\n            details=unresolved,\n        )\n    from .include_edges import authority_payload, collect_include_edges, owners_from_edges\n    edges, edge_issues = collect_include_edges(project_root, survey)\n    if edge_issues:\n        raise KickstartError("INCLUDE_EDGE_AUTHORITY_UNVERIFIED", "direct compiler include-edge authority is not fully provable", details=edge_issues)\n    closure_owners = {relative: sorted(owners) for relative, (_, owners) in headers.items()}\n    if owners_from_edges(edges) != closure_owners:\n        raise KickstartError("INCLUDE_EDGE_CLOSURE_MISMATCH", "direct include edges disagree with transitive header closure")\n    return survey, headers, authority_payload(edges)\n'''
    if family_block not in text:
        raise SystemExit("universal c-family evidence block missing")
    text = text.replace(family_block, family_replacement, 1)
    text = text.replace('        return None, {}\n', '        return None, {}, None\n', 1)
    text = text.replace('    c_survey, headers = _c_family_evidence(project_root, compile_commands)\n', '    c_survey, headers, edge_payload = _c_family_evidence(project_root, compile_commands)\n', 1)
    call = '''        survey, header_owners=header_owners, workspace_id=workspace_id, updated_at=updated_at\n    )'''
    call_replacement = '''        survey,\n        header_owners=header_owners,\n        include_edges=(edge_payload or {}).get("edges", []),\n        include_topology_sha256=str((edge_payload or {}).get("topology_sha256", "")),\n        workspace_id=workspace_id,\n        updated_at=updated_at,\n    )'''
    if call not in text:
        raise SystemExit("universal source-index call missing")
    text = text.replace(call, call_replacement, 1)
    retain_anchor = '''    if c_survey is not None and compile_commands is not None:\n        retained_compile = intake_dir / "compile_commands.json"\n        _atomic_bytes(retained_compile, Path(compile_commands).read_bytes())\n        machine_authorities.append("project-intake/compile_commands.json")\n'''
    retain_replacement = retain_anchor + '''        edge_authority_path = intake_dir / "include-edges.json"\n        _atomic_json(edge_authority_path, edge_payload)\n        machine_authorities.append("project-intake/include-edges.json")\n'''
    if retain_anchor not in text:
        raise SystemExit("universal retained compile anchor missing")
    text = text.replace(retain_anchor, retain_replacement, 1)
    source_auth = '''            "translation_unit_extensions": source_extensions,\n            "include_ownership_section_id": "s-include-closure",\n'''
    source_auth_replacement = '''            "translation_unit_extensions": source_extensions,\n            "include_ownership_section_id": "s-include-closure",\n            **({\n                "include_edges_section_id": "s-include-edges",\n                "include_edges_file": "project-intake/include-edges.json",\n            } if edge_payload is not None else {}),\n'''
    if source_auth not in text:
        raise SystemExit("universal source authority config anchor missing")
    text = text.replace(source_auth, source_auth_replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_corpus() -> None:
    path = ROOT / "workshop/src/runtime_sync_workshop/corpus.py"
    text = path.read_text(encoding="utf-8")
    if "def direct_include_edges(" not in text:
        start = text.index("def header_ownership(")
        end = text.index("\ndef header_closure", start)
        replacement = r'''_INCLUDE_EDGE_KEYS = (
    "compiled_root", "including_file", "include_line", "include_token", "delimiter",
    "resolved_target", "resolution_class", "compiler_variant", "resolution_path",
)


def canonical_include_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in edges:
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
        rows[tuple(row[key] for key in _INCLUDE_EDGE_KEYS)] = row
    return [rows[key] for key in sorted(rows)]


def include_topology_sha256(edges: Iterable[dict[str, Any]]) -> str:
    return sha256_text(json.dumps(canonical_include_edges(edges), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _include_analysis(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]], list[dict[str, Any]]]:
    overrides = overrides or {}
    literal_re = re.compile(r'(?m)^\s*#\s*include\s*([<"])([^>"\n]+)[>"]')
    any_re = re.compile(r'(?m)^\s*#\s*include\s+([^\n]+)')
    codebase = config.codebase_root.resolve()
    queue: list[tuple[Path, str, int, dict[str, Any]]] = []
    owners: dict[str, set[str]] = {}
    edges: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    ecosystem_map = config.raw.get("source_ecosystems") or {}
    if not isinstance(ecosystem_map, dict):
        raise WorkshopError("CONFIG_INVALID", "source_ecosystems must map governed source paths to ecosystem names")
    for relative in translation_units:
        relative = str(relative).replace("\\", "/")
        if ecosystem_map and str(ecosystem_map.get(relative, "")) != "c_family":
            continue
        relative_path = Path(relative)
        source_candidate = codebase / relative_path
        if not relative or _path_is_absolute(relative) or _path_is_drive_relative(relative) or ".." in relative_path.parts:
            issues.append({"code": "SOURCE_AUTHORITY_PATH_UNSAFE", "message": f"translation-unit authority path is unsafe: {relative}"})
            continue
        try:
            reject_link_components(source_candidate, label="translation-unit source")
            logical = source_candidate.resolve()
            logical.relative_to(codebase)
        except (WorkshopError, OSError, ValueError) as exc:
            issues.append({"code": "SOURCE_AUTHORITY_PATH_UNSAFE", "message": f"translation-unit authority path cannot be verified: {relative}", "details": {"error": str(exc)}})
            continue
        for variant_index, variant in enumerate(_translation_unit_include_variants(config, relative), 1):
            queue.append((logical, relative, variant_index, variant))

    seen: set[tuple[Path, str, int]] = set()

    def physical_path(logical: Path) -> Path:
        try:
            relative = logical.resolve().relative_to(codebase).as_posix()
        except ValueError:
            return logical
        return overrides.get(relative, logical)

    while queue:
        logical_path, compiled_root, variant_index, variant = queue.pop(0)
        visit = (logical_path.resolve(), compiled_root, variant_index)
        if visit in seen:
            continue
        seen.add(visit)
        physical = physical_path(logical_path)
        try:
            reject_link_components(physical, label="include-graph input")
        except WorkshopError as exc:
            issues.append({"code": "INCLUDE_SCAN_LINK_UNSAFE", "message": str(exc), "details": {"including": relative_posix(logical_path, codebase), "compiled_root": compiled_root}})
            continue
        if not physical.is_file():
            issues.append({"code": "INCLUDE_SCAN_INPUT_MISSING", "message": f"include-graph input is missing: {physical}"})
            continue
        try:
            text = physical.read_text(encoding="utf-8")
        except UnicodeError as exc:
            issues.append({"code": "INCLUDE_SCAN_NON_UTF8", "message": f"cannot scan includes in {physical}: {exc}"})
            continue
        including_relative = relative_posix(logical_path, codebase)
        literal_starts = {match.start() for match in literal_re.finditer(text)}
        for match in any_re.finditer(text):
            if match.start() not in literal_starts:
                issues.append({
                    "code": "DYNAMIC_INCLUDE_UNSUPPORTED",
                    "message": "preprocessor-computed include cannot be proven by compiler include-edge authority",
                    "details": {"including": including_relative, "compiled_root": compiled_root, "include": match.group(1).strip()},
                })
        for match in literal_re.finditer(text):
            opener, include = match.group(1), match.group(2).strip()
            delimiter = "quote" if opener == '"' else "angle"
            include_line = text.count("\n", 0, match.start()) + 1
            include_path = Path(include.replace("\\", "/"))
            search_roots = variant["quote_roots"] if opener == '"' else variant["angle_roots"]
            candidates: list[tuple[Path, str]] = []
            if opener == '"':
                candidates.append((logical_path.parent / include_path, "including_directory"))
            candidates.extend((root / include_path, f"{delimiter}_search_root:{index}") for index, root in enumerate(search_roots))
            selected_local: Path | None = None
            selected_external = False
            resolution_path = ""
            unsafe = False
            for candidate, candidate_role in candidates:
                try:
                    reject_link_components(candidate, label="include candidate")
                except WorkshopError as exc:
                    issues.append({"code": "INCLUDE_LINK_UNSAFE", "message": str(exc), "details": {"including": including_relative, "compiled_root": compiled_root}})
                    unsafe = True
                    break
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not resolved.is_file():
                    continue
                try:
                    resolved.relative_to(codebase)
                except ValueError:
                    selected_external = True
                    resolution_path = candidate_role
                    break
                if resolved.suffix.lower() in config.header_extensions:
                    selected_local = resolved
                    resolution_path = candidate_role
                    break
            if unsafe:
                continue
            if selected_local is not None and any(selected_local.is_relative_to(root) for root in variant["idirafter_roots"]):
                status, compiler_selected = _compiler_selected_literal(
                    config, variant=variant, owner=compiled_root, including=logical_path, opener=opener, include=include,
                )
                if status == "external":
                    selected_local = None
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and compiler_selected is not None:
                    selected_local = compiler_selected
                    resolution_path = "compiler_probe"
                else:
                    issues.append({"code": "INCLUDE_SELECTION_AMBIGUOUS", "message": f"cannot prove compiler selection for -idirafter include: {include}", "details": {"including": including_relative, "compiled_root": compiled_root}})
                    continue
            if selected_local is None and opener == '"' and not selected_external:
                status, compiler_selected = _compiler_selected_literal(
                    config, variant=variant, owner=compiled_root, including=logical_path, opener=opener, include=include,
                )
                if status == "external":
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and compiler_selected is not None:
                    selected_local = compiler_selected
                    resolution_path = "compiler_probe"
                elif status == "missing":
                    issues.append({"code": "RUNTIME_INCLUDE_UNRESOLVED", "message": f"quoted project include cannot be resolved: {include}", "details": {"including": including_relative, "compiled_root": compiled_root}})
                    continue
            if selected_local is not None:
                if selected_local.suffix.lower() not in config.header_extensions:
                    issues.append({"code": "LOCAL_INCLUDE_SUFFIX_UNSUPPORTED", "message": f"project-local include has unsupported suffix: {include}"})
                    continue
                target = relative_posix(selected_local, codebase)
                owners.setdefault(target, set()).add(compiled_root)
                edges.append({
                    "compiled_root": compiled_root,
                    "including_file": including_relative,
                    "include_line": include_line,
                    "include_token": include,
                    "delimiter": delimiter,
                    "resolved_target": target,
                    "resolution_class": "local",
                    "compiler_variant": variant_index,
                    "resolution_path": resolution_path or "compiler_order",
                })
                queue.append((selected_local, compiled_root, variant_index, variant))
            elif selected_external:
                edges.append({
                    "compiled_root": compiled_root,
                    "including_file": including_relative,
                    "include_line": include_line,
                    "include_token": include,
                    "delimiter": delimiter,
                    "resolved_target": include,
                    "resolution_class": "external",
                    "compiler_variant": variant_index,
                    "resolution_path": resolution_path or "compiler_order",
                })
    return owners, canonical_include_edges(edges), issues


def header_ownership(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    owners, _, issues = _include_analysis(config, translation_units, overrides=overrides)
    return owners, issues


def direct_include_edges(
    config: WorkshopConfig,
    translation_units: Iterable[str],
    *,
    overrides: dict[str, Path] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _, edges, issues = _include_analysis(config, translation_units, overrides=overrides)
    return edges, issues

'''
        text = text[:start] + replacement + text[end:]

    if "def load_include_edge_authority(" not in text:
        marker = "\ndef _section_capsule(content: str) -> str:\n"
        helper = r'''

def _include_edge_config(config: WorkshopConfig) -> tuple[str, str] | None:
    authority = config.raw.get("source_authority") or {}
    if not isinstance(authority, dict):
        raise WorkshopError("CONFIG_INVALID", "source_authority must be an object")
    relative = authority.get("include_edges_file")
    section_id = authority.get("include_edges_section_id")
    if relative is None and section_id is None:
        return None
    if not isinstance(relative, str) or not relative.strip() or not isinstance(section_id, str) or not section_id.strip():
        raise WorkshopError("CONFIG_INVALID", "include edge authority requires include_edges_file and include_edges_section_id")
    path = _machine_relative_path(relative, "include_edges_file", base=config.machine_root)
    return path.relative_to(config.machine_root).as_posix(), section_id


def load_include_edge_authority(config: WorkshopConfig) -> dict[str, Any] | None:
    configured = _include_edge_config(config)
    if configured is None:
        return None
    relative, _ = configured
    path = config.machine_root / relative
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != "kairos-compiler-include-edges/v1":
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", f"invalid include-edge authority: {path}")
    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list) or any(not isinstance(row, dict) for row in raw_edges):
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", "include-edge authority edges must be an object array")
    try:
        edges = canonical_include_edges(raw_edges)
    except Exception as exc:
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_INVALID", f"cannot canonicalize include-edge authority: {exc}") from exc
    digest = include_topology_sha256(edges)
    if raw_edges != edges:
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_NONCANONICAL", "include-edge authority is not canonically ordered")
    if payload.get("topology_sha256") != digest or int(payload.get("edge_count", -1)) != len(edges):
        raise WorkshopError("INCLUDE_EDGE_AUTHORITY_DIGEST_MISMATCH", "include-edge authority digest/count differs from canonical rows")
    return {**payload, "edges": edges}


def declared_include_edges(config: WorkshopConfig) -> dict[str, Any] | None:
    configured = _include_edge_config(config)
    if configured is None:
        return None
    _, section_id = configured
    text = normalized_text(config.dataflow_index.read_text(encoding="utf-8"))
    marker = f'<a id="{section_id}"></a>'
    start = text.find(marker)
    if start < 0:
        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_SECTION_MISSING", f"configured include-edge section is missing: {section_id}")
    next_anchor = text.find('<a id="', start + len(marker))
    section = text[start:] if next_anchor < 0 else text[start:next_anchor]
    digest_match = re.search(r"Topology SHA-256:\s*`([0-9a-f]{64})`", section)
    if not digest_match:
        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_DIGEST_MISSING", "include-edge section has no topology digest")
    edges: list[dict[str, Any]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- {"):
            continue
        try:
            value = json.loads(stripped[2:])
        except json.JSONDecodeError as exc:
            raise WorkshopError("DATAFLOW_INCLUDE_EDGE_ROW_INVALID", f"invalid include-edge JSON row: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkshopError("DATAFLOW_INCLUDE_EDGE_ROW_INVALID", "include-edge row must be a JSON object")
        edges.append(value)
    canonical = canonical_include_edges(edges)
    if edges != canonical:
        raise WorkshopError("DATAFLOW_INCLUDE_EDGE_NONCANONICAL", "include-edge source-index rows are not canonically ordered")
    return {"topology_sha256": digest_match.group(1), "edges": canonical, "edge_count": len(canonical)}


def verify_include_edge_database(config: WorkshopConfig, edges: list[dict[str, Any]], *, database_path: Path | None = None) -> list[dict[str, Any]]:
    database_path = (database_path or config.kairos_database).resolve()
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "compiler_include_edges" not in tables:
            return [{"code": "DATABASE_INCLUDE_EDGE_TABLE_MISSING", "message": "KAIROS database has no compiler_include_edges table"}]
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
                "SELECT compiled_root,including_file,include_line,include_token,delimiter,resolved_target,resolution_class,compiler_variant,resolution_path FROM compiler_include_edges WHERE artifact_id='PROJECT_SOURCE_INDEX' ORDER BY ordinal"
            ).fetchall()
        ]
    finally:
        connection.close()
    if actual != edges:
        return [{"code": "DATABASE_INCLUDE_EDGE_MISMATCH", "message": "compiler_include_edges projection differs from machine authority", "details": {"expected_count": len(edges), "actual_count": len(actual)}}]
    return []

'''
        if marker not in text:
            raise SystemExit("corpus helper insertion anchor missing")
        text = text.replace(marker, helper + marker, 1)

    if "include_topology_sha256" not in text[text.index("def build_corpus_manifest"):]:
        anchor = '''    include_owners, include_issues = header_ownership(config, dataflow)\n    include_headers = set(include_owners)\n    issues.extend(include_issues)\n'''
        replacement = anchor + '''    include_edges: list[dict[str, Any]] = []\n    include_edge_authority = load_include_edge_authority(config)\n    if include_edge_authority is not None:\n        include_edges, include_edge_issues = direct_include_edges(config, dataflow)\n        issues.extend(include_edge_issues)\n        if include_edges != include_edge_authority["edges"]:\n            issues.append({\n                "code": "INCLUDE_EDGE_AUTHORITY_MISMATCH",\n                "message": "live compiler-observed direct include edges differ from machine authority",\n                "details": {\n                    "expected_digest": include_edge_authority["topology_sha256"],\n                    "actual_digest": include_topology_sha256(include_edges),\n                },\n            })\n        declared_edges = declared_include_edges(config)\n        if declared_edges is None or declared_edges["edges"] != include_edge_authority["edges"] or declared_edges["topology_sha256"] != include_edge_authority["topology_sha256"]:\n            issues.append({\n                "code": "INCLUDE_EDGE_INDEX_MISMATCH",\n                "message": "PROJECT_SOURCE_INDEX direct include-edge projection differs from machine authority",\n            })\n'''
        if anchor not in text:
            raise SystemExit("corpus include ownership anchor missing")
        text = text.replace(anchor, replacement, 1)
        authority_anchor = '''        "include_owners": {key: sorted(value) for key, value in sorted(include_owners.items())},\n        "paired_headers": sorted(paired_headers),\n'''
        authority_replacement = '''        "include_owners": {key: sorted(value) for key, value in sorted(include_owners.items())},\n        **({\n            "include_edges": include_edges,\n            "include_topology_sha256": include_edge_authority["topology_sha256"],\n        } if include_edge_authority is not None else {}),\n        "paired_headers": sorted(paired_headers),\n'''
        if authority_anchor not in text:
            raise SystemExit("corpus authority anchor missing")
        text = text.replace(authority_anchor, authority_replacement, 1)
        database_anchor = '''            for source, summary in database_summary.items():\n                if source in records:\n                    records[source]["database"] = summary\n'''
        database_replacement = database_anchor + '''            if include_edge_authority is not None:\n                issues.extend(verify_include_edge_database(config, include_edge_authority["edges"]))\n'''
        if database_anchor not in text:
            raise SystemExit("corpus db verification anchor missing")
        text = text.replace(database_anchor, database_replacement, 1)
        count_anchor = '''            "include_headers": len(include_headers),\n            "paired_headers": len(paired_headers),\n'''
        count_replacement = '''            "include_headers": len(include_headers),\n            "include_edges": len(include_edges) if include_edge_authority is not None else 0,\n            "paired_headers": len(paired_headers),\n'''
        if count_anchor not in text:
            raise SystemExit("corpus count anchor missing")
        text = text.replace(count_anchor, count_replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_engine() -> None:
    path = ROOT / "workshop/src/runtime_sync_workshop/engine.py"
    text = path.read_text(encoding="utf-8")
    if "direct_include_edges," not in text:
        text = text.replace(
            "    build_corpus_manifest,\n    header_ownership,\n",
            "    build_corpus_manifest,\n    canonical_include_edges,\n    direct_include_edges,\n    header_ownership,\n    include_topology_sha256,\n",
            1,
        )
    if "include_edges: list[dict[str, Any]] | None = None" not in text:
        start = text.index("    def _render_include_ownership_authority(")
        end = text.index("\n    def _render_header_ownership_blueprint", start)
        replacement = r'''    def _render_include_ownership_authority(
        self,
        source_text: str,
        owners: dict[str, set[str]],
        *,
        updated_at: str,
        include_edges: list[dict[str, Any]] | None = None,
        topology_sha256: str | None = None,
    ) -> str:
        harness = str(self.config.kairos_harness)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        frontmatter = importlib.import_module("kairos.frontmatter")
        parsed = frontmatter.split_frontmatter(source_text)
        metadata = dict(parsed.metadata)
        metadata["revision"] = int(metadata["revision"]) + 1
        metadata["updated_at"] = updated_at
        authority = self.config.raw.get("source_authority") or {}

        def replace_rows(body: str, section_id: str, rows: list[str]) -> str:
            anchor = f'<a id="{section_id}"></a>'
            start = body.find(anchor)
            if start < 0:
                raise WorkshopError("AUTHORITY_TOPOLOGY_INVALID", f"authority section is missing: {section_id}")
            next_anchor = body.find('<a id="', start + len(anchor))
            end = len(body) if next_anchor < 0 else next_anchor
            section = body[start:end]
            prefix_match = re.match(
                rf'(?s)(<a id="{re.escape(section_id)}"></a>\n##[^\n]*\n\n> Capsule:[^\n]*\n\n)',
                section,
            )
            if not prefix_match:
                raise WorkshopError("AUTHORITY_TOPOLOGY_INVALID", f"authority section lacks canonical heading/capsule: {section_id}")
            replacement = prefix_match.group(1) + "\n".join(rows) + "\n\n"
            return body[:start] + replacement + body[end:]

        section_id = str(authority.get("include_ownership_section_id", ""))
        if not section_id:
            raise WorkshopError("AUTHORITY_TOPOLOGY_UNCONFIGURED", "include ownership authority section is not configured")
        owner_rows = [
            f"- `{header}` ← " + ", ".join(f"`{owner}`" for owner in sorted(header_owners))
            for header, header_owners in sorted(owners.items())
        ] or ["- none"]
        body = replace_rows(parsed.body, section_id, owner_rows)
        if include_edges is not None:
            edge_section = str(authority.get("include_edges_section_id", ""))
            if not edge_section or not topology_sha256:
                raise WorkshopError("AUTHORITY_TOPOLOGY_UNCONFIGURED", "direct include-edge authority section/digest is not configured")
            edge_rows = [f"Topology SHA-256: `{topology_sha256}`", ""]
            edge_rows.extend("- " + canonical_json(edge) for edge in canonical_include_edges(include_edges))
            if not include_edges:
                edge_rows.append("- none")
            body = replace_rows(body, edge_section, edge_rows)
        return frontmatter.render_frontmatter(metadata) + body

'''
        text = text[:start] + replacement + text[end:]

    if "edge_changed =" not in text:
        start = text.index("    def _prepare_topology_authority(")
        end = text.index("\n    @staticmethod\n    def _intake_fact", start)
        old = text[start:end]
        # Targeted transforms preserve the mature ownership/blueprint logic.
        old = old.replace(
            '''        owners, issues = header_ownership(\n            self.config,\n            baseline_manifest["authority"]["translation_units"],\n            overrides=overrides,\n        )\n        if issues:\n''',
            '''        owners, issues = header_ownership(\n            self.config,\n            baseline_manifest["authority"]["translation_units"],\n            overrides=overrides,\n        )\n        edge_file = authority.get("include_edges_file")\n        work_edges: list[dict[str, Any]] | None = None\n        baseline_edges: list[dict[str, Any]] = []\n        edge_changed = False\n        if edge_file:\n            work_edges, edge_issues = direct_include_edges(\n                self.config,\n                baseline_manifest["authority"]["translation_units"],\n                overrides=overrides,\n            )\n            issues.extend(edge_issues)\n            baseline_edges = canonical_include_edges(baseline_manifest["authority"].get("include_edges", []))\n            edge_changed = work_edges != baseline_edges\n        if issues:\n''',
            1,
        )
        old = old.replace(
            '''        if owners == baseline_owners:\n            if stage:\n                state["authority_documents"] = []\n                state["topology_blueprints"] = []\n            return None\n''',
            '''        if owners == baseline_owners and not edge_changed:\n            if stage:\n                state["authority_documents"] = []\n                state["topology_blueprints"] = []\n            return None\n''',
            1,
        )
        old = old.replace(
            '''        rendered = self._render_include_ownership_authority(\n            baseline_authority.read_text(encoding="utf-8"),\n            owners,\n            updated_at=state["deterministic_updated_at"],\n        )\n''',
            '''        rendered = self._render_include_ownership_authority(\n            baseline_authority.read_text(encoding="utf-8"),\n            owners,\n            updated_at=state["deterministic_updated_at"],\n            include_edges=work_edges,\n            topology_sha256=include_topology_sha256(work_edges or []) if work_edges is not None else None,\n        )\n''',
            1,
        )
        old = old.replace(
            '''        state["authority_documents"] = [relative]\n        return {\n            "document": relative,\n''',
            '''        state["authority_documents"] = [relative]\n        if work_edges is not None and isinstance(edge_file, str):\n            payload = {\n                "schema": "kairos-compiler-include-edges/v1",\n                "edge_count": len(work_edges),\n                "topology_sha256": include_topology_sha256(work_edges),\n                "claim_boundary": "Direct compiler-observed include consumers are distinct from byte ownership and transitive compiled-root reachability.",\n                "edges": canonical_include_edges(work_edges),\n            }\n            atomic_write_json(root / "work" / "machine" / edge_file, payload)\n            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])) | {edge_file})\n            state["include_edge_authority"] = {\n                "path": edge_file,\n                "edge_count": payload["edge_count"],\n                "topology_sha256": payload["topology_sha256"],\n            }\n        return {\n            "document": relative,\n''',
            1,
        )
        old = old.replace(
            '''            "updated_header_blueprints": topology_blueprints,\n        }\n''',
            '''            "updated_header_blueprints": topology_blueprints,\n            "direct_edge_changed": edge_changed,\n            "baseline_topology_sha256": include_topology_sha256(baseline_edges) if work_edges is not None else None,\n            "work_topology_sha256": include_topology_sha256(work_edges or []) if work_edges is not None else None,\n        }\n''',
            1,
        )
        if "edge_changed =" not in old:
            raise SystemExit("engine topology transform failed")
        text = text[:start] + old + text[end:]

    # Never let project-intake staging erase an include-edge machine file staged above.
    text = text.replace(
        '            state["machine_authority_files"] = []\n            return None\n',
        '            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])))\n            return None\n',
    )
    text = text.replace(
        '        state["machine_authority_files"] = ["project-intake.json"]\n',
        '        state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])) | {"project-intake.json"})\n',
    )
    if "include_edge_topology" not in text[text.index("    def _stage_project_intake_authority("):text.index("    def _review_map", text.index("    def _stage_project_intake_authority("))]:
        marker = '''        if not changed_fields:\n            state["machine_authority_files"] = sorted(set(state.get("machine_authority_files", [])))\n            return None\n'''
        addition = '''        edge_stage = state.get("include_edge_authority")\n        if isinstance(edge_stage, dict) and isinstance(candidate.get("include_edge_topology"), dict):\n            candidate["include_edge_topology"] = {\n                "edge_count": int(edge_stage["edge_count"]),\n                "topology_sha256": str(edge_stage["topology_sha256"]),\n            }\n            changed_fields.append("include_edges")\n\n'''
        if marker not in text:
            raise SystemExit("engine project-intake no-change anchor missing")
        text = text.replace(marker, addition + marker, 1)

    if "include_edge_projection =" not in text:
        marker = '''        summary, issues = verify_database_projection(self.config, entries, database_path=shadow / ".kairos" / "kairos.db")\n'''
        addition = '''        include_edge_projection = None\n        edge_stage = state.get("include_edge_authority")\n        if isinstance(edge_stage, dict):\n            include_module = importlib.import_module("kairos.include_edges")\n            include_edge_projection = include_module.project_compiler_include_edges(\n                shadow,\n                database,\n                authority_path=root / "work" / "machine" / str(edge_stage["path"]),\n            )\n            if not include_edge_projection.get("verified"):\n                raise WorkshopError("SHADOW_INCLUDE_EDGE_PROJECTION_FAILED", "shadow include-edge projection was not verified", details=include_edge_projection)\n'''
        if marker not in text:
            raise SystemExit("engine shadow DB anchor missing")
        text = text.replace(marker, addition + marker, 1)
        text = text.replace(
            '        return {"adapter": "native", "receipts": receipts, "database": summary, "shadow_workspace": str(shadow)}\n',
            '        return {"adapter": "native", "receipts": receipts, "database": summary, "compiler_include_edges": include_edge_projection, "shadow_workspace": str(shadow)}\n',
            1,
        )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_database()
    patch_heartbeat()
    patch_graph()
    patch_blueprints()
    patch_binding()
    patch_universal_workshop()
    patch_corpus()
    patch_engine()


if __name__ == "__main__":
    main()
