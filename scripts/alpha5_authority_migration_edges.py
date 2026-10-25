from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "workshop/src/runtime_sync_workshop/migration.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"authority-edge patch anchor missing: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    # Candidate include topology is computed once under the candidate compiler context.
    old = '''        headers, unresolved = resolve_include_closure(candidate_root, survey)\n        if unresolved:\n            dynamic = [item for item in unresolved if item.get("reason") == "dynamic_or_macro_include"]\n            code = "DYNAMIC_INCLUDE_UNSUPPORTED" if dynamic else "QUOTED_INCLUDE_UNRESOLVED"\n            raise WorkshopError(code, "candidate include closure is not fully provable", details=unresolved)\n        workspace_id, goal_id, milestone_id, task_id, project_intent_sha256 = self._scope()\n'''
    new = '''        headers, unresolved = resolve_include_closure(candidate_root, survey)\n        if unresolved:\n            dynamic = [item for item in unresolved if item.get("reason") == "dynamic_or_macro_include"]\n            code = "DYNAMIC_INCLUDE_UNSUPPORTED" if dynamic else "QUOTED_INCLUDE_UNRESOLVED"\n            raise WorkshopError(code, "candidate include closure is not fully provable", details=unresolved)\n        from kickstart.include_edges import authority_payload, collect_include_edges, owners_from_edges\n        candidate_include_edges, edge_issues = collect_include_edges(candidate_root, survey)\n        if edge_issues:\n            raise WorkshopError(\n                "INCLUDE_EDGE_AUTHORITY_UNVERIFIED",\n                "candidate direct compiler include-edge authority is not fully provable",\n                details=edge_issues,\n            )\n        candidate_edge_payload = authority_payload(candidate_include_edges)\n        candidate_closure_owners = {\n            relative: sorted(owners) for relative, (_, owners) in sorted(headers.items())\n        }\n        if owners_from_edges(candidate_include_edges) != candidate_closure_owners:\n            raise WorkshopError(\n                "INCLUDE_EDGE_CLOSURE_MISMATCH",\n                "candidate direct include edges disagree with transitive compiler header closure",\n            )\n        workspace_id, goal_id, milestone_id, task_id, project_intent_sha256 = self._scope()\n'''
    text = replace_once(text, old, new, "candidate edge collection")

    old = '''            updated_at=created_at,\n            header_closure={relative: owners for relative, (_, owners) in headers.items()},\n        )\n'''
    new = '''            updated_at=created_at,\n            header_closure={relative: owners for relative, (_, owners) in headers.items()},\n            include_edges=candidate_include_edges,\n            include_topology_sha256=candidate_edge_payload["topology_sha256"],\n        )\n'''
    text = replace_once(text, old, new, "candidate source-index edge rendering")

    old = '''                "candidate_include_owners": {\n                    relative: sorted(owners) for relative, (_, owners) in sorted(headers.items())\n                },\n                "candidate_include_roots": [\n'''
    new = '''                "candidate_include_owners": {\n                    relative: sorted(owners) for relative, (_, owners) in sorted(headers.items())\n                },\n                "candidate_include_edges": candidate_edge_payload["edges"],\n                "candidate_include_topology_sha256": candidate_edge_payload["topology_sha256"],\n                "candidate_include_edge_count": candidate_edge_payload["edge_count"],\n                "candidate_include_roots": [\n'''
    text = replace_once(text, old, new, "candidate edge state")

    # The final machine authority advances atomically with project-intake/config.
    old = '''        final_intake["include_closure"] = {\n            relative: {\n                "owners": state["candidate_include_owners"].get(relative, []),\n                "facts": {\n                    "path": (self.config.codebase_root / relative).as_posix(),\n                    "sha256": sha256_bytes((candidate_root / relative).read_bytes()),\n                    "bytes": (candidate_root / relative).stat().st_size,\n                },\n            }\n            for relative in state["candidate_headers"]\n        }\n        final_intake["blueprints"] = [\n'''
    new = '''        final_intake["include_closure"] = {\n            relative: {\n                "owners": state["candidate_include_owners"].get(relative, []),\n                "facts": {\n                    "path": (self.config.codebase_root / relative).as_posix(),\n                    "sha256": sha256_bytes((candidate_root / relative).read_bytes()),\n                    "bytes": (candidate_root / relative).stat().st_size,\n                },\n            }\n            for relative in state["candidate_headers"]\n        }\n        final_intake["include_edge_topology"] = {\n            "edge_count": int(state["candidate_include_edge_count"]),\n            "topology_sha256": str(state["candidate_include_topology_sha256"]),\n        }\n        final_intake["blueprints"] = [\n'''
    text = replace_once(text, old, new, "project-intake edge topology")

    old = '''        copy_exact(machine / "compile_commands.json", machine / "project-intake" / "compile_commands.json")\n        copy_exact(machine / "PROJECT_BUILD_AUTHORITY.cmake", machine / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")\n\n        state["state"] = "PREPARED"\n'''
    new = '''        copy_exact(machine / "compile_commands.json", machine / "project-intake" / "compile_commands.json")\n        copy_exact(machine / "PROJECT_BUILD_AUTHORITY.cmake", machine / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")\n        atomic_write_json(\n            machine / "project-intake" / "include-edges.json",\n            {\n                "schema": "kairos-compiler-include-edges/v1",\n                "edge_count": int(state["candidate_include_edge_count"]),\n                "topology_sha256": str(state["candidate_include_topology_sha256"]),\n                "claim_boundary": (\n                    "Direct compiler-observed include consumers are distinct from byte ownership "\n                    "and transitive compiled-root reachability."\n                ),\n                "edges": state["candidate_include_edges"],\n            },\n        )\n\n        state["state"] = "PREPARED"\n'''
    text = replace_once(text, old, new, "prepared include-edge machine authority")

    # Verify candidate machine authority and SQLite projection before live mutation.
    old = '''        copy_exact(root / "work" / "machine" / "project-intake" / "compile_commands.json", machine_verify / "project-intake" / "compile_commands.json")\n        copy_exact(root / "work" / "machine" / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake", machine_verify / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")\n        verify_intake = read_json(root / "work" / "machine" / "project-intake.json")\n'''
    new = '''        copy_exact(root / "work" / "machine" / "project-intake" / "compile_commands.json", machine_verify / "project-intake" / "compile_commands.json")\n        copy_exact(root / "work" / "machine" / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake", machine_verify / "project-intake" / "PROJECT_BUILD_AUTHORITY.cmake")\n        copy_exact(root / "work" / "machine" / "project-intake" / "include-edges.json", machine_verify / "project-intake" / "include-edges.json")\n        verify_intake = read_json(root / "work" / "machine" / "project-intake.json")\n'''
    text = replace_once(text, old, new, "machine-verify include-edge copy")

    old = '''        atomic_write_json(machine_verify / "project-intake.json", verify_intake)\n\n        # Assemble candidate managed active docs from shadow and external active docs.\n        external_final = root / "work" / "external-final"\n        candidate_config = load_config(machine_verify / "workshop.config.json")\n        candidate_manifest = build_corpus_manifest(candidate_config, verify_database=True)\n'''
    new = '''        atomic_write_json(machine_verify / "project-intake.json", verify_intake)\n\n        # Project exact direct edges into the isolated candidate database before the\n        # corpus postcheck. This is the same mechanical projection the live heartbeat\n        # will perform after apply.\n        include_module = importlib.import_module("kairos.include_edges")\n        include_projection = include_module.project_compiler_include_edges(\n            shadow,\n            database,\n            authority_path=machine_verify / "project-intake" / "include-edges.json",\n        )\n        if not include_projection.get("verified"):\n            raise WorkshopError(\n                "AUTHORITY_MIGRATION_INCLUDE_EDGE_SHADOW_FAILED",\n                "candidate include-edge SQLite projection was not verified",\n                details=include_projection,\n            )\n\n        # Assemble candidate managed active docs from shadow and external active docs.\n        external_final = root / "work" / "external-final"\n        candidate_config = load_config(machine_verify / "workshop.config.json")\n        candidate_manifest = build_corpus_manifest(candidate_config, verify_database=True)\n'''
    text = replace_once(text, old, new, "shadow edge projection")

    old = '''            "candidate_counts": candidate_manifest["counts"],\n        }\n'''
    new = '''            "candidate_counts": candidate_manifest["counts"],\n            "include_edge_projection": include_projection,\n        }\n'''
    text = replace_once(text, old, new, "shadow edge receipt")

    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
