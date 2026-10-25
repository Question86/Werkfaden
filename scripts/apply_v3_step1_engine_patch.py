from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "kickstart/universal.py",
    '''    sections = [\n        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact initial source mirror.", "content": f"The governed source file is `{source.relative_path}`."},\n''',
    '''    source_authority = "compiler_backed" if source.ecosystem == "c_family" else "static_source_membership"\n    sections = [\n        {"id": "s-purpose", "title": "PURPOSE", "capsule": "Exact initial source mirror.", "content": f"The governed source file is `{source.relative_path}`."},\n''',
)
replace_once(
    "kickstart/universal.py",
    '''        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Initial membership is static for this ecosystem; build/runtime semantics are not invented.", "content": f"- Ecosystem: `{source.ecosystem}`\\n- Authority: `static_source_membership`\\n- SHA-256: `{source.sha256.upper()}`"},\n''',
    '''        {"id": "s-boundary", "title": "AUTHORITY BOUNDARY", "capsule": "Initial membership is explicit and ecosystem-bounded; build/runtime semantics are not invented.", "content": f"- Ecosystem: `{source.ecosystem}`\\n- Authority: `{source_authority}`\\n- SHA-256: `{source.sha256.upper()}`"},\n''',
)

replace_once(
    "kickstart/universal_workshop.py",
    '''    source_rows = "\\n".join(\n        f"| `{source.relative_path}` | `{source.ecosystem}` | `{source.sha256.upper()}` |"\n        for source in survey.sources\n    )\n''',
    '''    source_rows = "\\n".join(\n        f"| `{source.relative_path}` | `{source.ecosystem}` |"\n        for source in survey.sources\n    )\n''',
)
replace_once(
    "kickstart/universal_workshop.py",
    '''        "capsule": "Exact universal source membership with ecosystem classification, hashes and compiler-backed C-family header closure.",\n''',
    '''        "capsule": "Exact universal source membership with ecosystem classification and compiler-backed C-family header closure.",\n''',
)
replace_once(
    "kickstart/universal_workshop.py",
    '''        "### 2.7 End of governed source membership\\n\\n"\n        "### Exact source hashes\\n\\n"\n        "| Source | Ecosystem | SHA-256 |\\n|---|---|---|\\n"\n        + source_rows\n''',
    '''        "### 2.7 End of governed source membership\\n\\n"\n        "### Ecosystem classification\\n\\n"\n        "| Source | Ecosystem |\\n|---|---|\\n"\n        + source_rows\n''',
)

replace_once(
    "workshop/src/runtime_sync_workshop/corpus.py",
    '''    queue: list[tuple[Path, str, dict[str, Any]]] = []\n    for relative in translation_units:\n        logical = (codebase / relative).resolve()\n        for variant in _translation_unit_include_variants(config, relative):\n            queue.append((logical, relative, variant))\n''',
    '''    queue: list[tuple[Path, str, dict[str, Any]]] = []\n    ecosystem_map = config.raw.get("source_ecosystems") or {}\n    if not isinstance(ecosystem_map, dict):\n        raise WorkshopError("CONFIG_INVALID", "source_ecosystems must map governed source paths to ecosystem names")\n    for relative in translation_units:\n        # Legacy compiler-backed workspaces predate source_ecosystems and are all\n        # C-family. Universal workspaces must never run a C preprocessor scanner\n        # over Python/Rust/JS/etc. where '# include' or similar text can be a\n        # comment/string rather than preprocessor syntax.\n        if ecosystem_map and str(ecosystem_map.get(relative, "")) != "c_family":\n            continue\n        logical = (codebase / relative).resolve()\n        for variant in _translation_unit_include_variants(config, relative):\n            queue.append((logical, relative, variant))\n''',
)

replace_once(
    "workshop/src/runtime_sync_workshop/engine.py",
    '''            token_changed = bool(\n                mapped_work_source\n                and cpp_token_signature(baseline_source.read_bytes())\n                != cpp_token_signature(mapped_work_source.read_bytes())\n            )\n            if baseline_header and work_header:\n                token_changed = token_changed or cpp_token_signature(baseline_header.read_bytes()) != cpp_token_signature(work_header.read_bytes())\n''',
    '''            ecosystem_map = self.config.raw.get("source_ecosystems") or {}\n            ecosystem = str(ecosystem_map.get(source, "c_family")) if isinstance(ecosystem_map, dict) else "c_family"\n            if ecosystem == "c_family":\n                token_changed = bool(\n                    mapped_work_source\n                    and cpp_token_signature(baseline_source.read_bytes())\n                    != cpp_token_signature(mapped_work_source.read_bytes())\n                )\n                if baseline_header and work_header:\n                    token_changed = token_changed or cpp_token_signature(baseline_header.read_bytes()) != cpp_token_signature(work_header.read_bytes())\n            else:\n                # Universal static blueprints intentionally make no semantic claim\n                # beyond exact bytes/ledger. A language-agnostic C++ lexer must not\n                # manufacture a semantic-metadata obligation for Python/Rust/JS/etc.\n                # Their exact mechanical mirror still advances on every byte change.\n                token_changed = False\n''',
)

replace_once(
    "kickstart/tests/test_universal.py",
    '''            self.assertEqual(result["state"], "MARKDOWN_MATERIALIZED_PENDING_WORKSHOP_BINDING")\n''',
    '''            self.assertEqual(result["state"], "VERIFIED_PENDING_SEAL")\n            self.assertTrue(result["corpus"]["verified"])\n            self.assertTrue(Path(result["workshop_config"]).is_file())\n''',
)

print("v3 step1 engine patch applied")
