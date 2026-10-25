from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .errors import KickstartError
from .survey import HEADER_SUFFIXES, Survey, compiler_probe_argv, reject_links


EDGE_SCHEMA = "kairos-compiler-include-edges/v1"
_LITERAL_INCLUDE_RE = re.compile(r'(?m)^\s*#\s*include\s*([<"])([^>"\n]+)[>"]')
_ANY_INCLUDE_RE = re.compile(r'(?m)^\s*#\s*include\s+([^\n]+)')
_EDGE_KEYS = (
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


def canonical_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[tuple[Any, ...], dict[str, Any]] = {}
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
        key = tuple(row[name] for name in _EDGE_KEYS)
        normalized[key] = row
    return [normalized[key] for key in sorted(normalized)]


def topology_sha256(edges: list[dict[str, Any]]) -> str:
    canonical = canonical_edges(edges)
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def authority_payload(edges: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = canonical_edges(edges)
    return {
        "schema": EDGE_SCHEMA,
        "edge_count": len(canonical),
        "topology_sha256": topology_sha256(canonical),
        "claim_boundary": (
            "Compiler-observed direct include edges are distinct from byte ownership and transitive compiled-root reachability. "
            "Project-local targets are path exact; external targets retain only stable include spelling and resolution class."
        ),
        "edges": canonical,
    }


def owners_from_edges(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for edge in canonical_edges(edges):
        if edge["resolution_class"] != "local":
            continue
        target = edge["resolved_target"]
        if Path(target).suffix.casefold() not in HEADER_SUFFIXES:
            continue
        result.setdefault(target, set()).add(edge["compiled_root"])
    return {key: sorted(values) for key, values in sorted(result.items())}


def _compiler_selected_literal(
    *,
    argv: tuple[str, ...],
    directory: Path,
    including: Path,
    opener: str,
    include: str,
    project_root: Path,
) -> tuple[str, Path | None]:
    probe = compiler_probe_argv(argv, directory=directory, project_root=project_root)
    if not probe:
        return "unsupported", None
    language = "c" if including.suffix.casefold() == ".c" else "c++"
    command = [*probe]
    if opener == '"':
        command.extend(("-iquote", str(including.parent)))
    command.extend(("-H", "-E", "-x", language, "-"))
    source = f"#include {opener}{include}{'>' if opener == '<' else chr(34)}\n"
    try:
        completed = __import__("subprocess").run(
            command,
            cwd=directory,
            input=source,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, __import__("subprocess").TimeoutExpired):
        return "unsupported", None
    selected: Path | None = None
    for line in completed.stderr.splitlines():
        match = re.match(r"^\.\s+(.+?)\s*$", line)
        if not match:
            continue
        candidate = Path(match.group(1).strip())
        try:
            selected = candidate.resolve(strict=True)
        except OSError:
            continue
        if selected.is_file():
            break
    if selected is None:
        return ("missing", None) if completed.returncode != 0 else ("unsupported", None)
    try:
        selected.relative_to(project_root.resolve())
    except ValueError:
        return "external", selected
    return "local", selected


def collect_include_edges(project_root: Path, survey: Survey) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Collect direct include edges per compiled root and compiler variant.

    The traversal deliberately keeps ``compiled_root`` separate from ``including_file``:
    the former is transitive reachability context, while the latter is the direct consumer
    that owns the ``#include`` line. Dynamic/macro includes remain fail-closed.
    """
    root = project_root.resolve()
    queue: list[tuple[Path, str, int, Any]] = []
    for unit in survey.units:
        for variant_index, variant in enumerate(unit.variants, 1):
            queue.append((unit.absolute_path, unit.relative_path, variant_index, variant))
    seen: set[tuple[Path, str, int]] = set()
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []

    while queue:
        path, compiled_root, variant_index, variant = queue.pop(0)
        logical = path.resolve()
        visit = (logical, compiled_root, variant_index)
        if visit in seen:
            continue
        seen.add(visit)
        try:
            text = logical.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise KickstartError("INCLUDE_EDGE_SCAN_FAILED", f"cannot read include graph input: {logical}") from exc
        including_relative = logical.relative_to(root).as_posix()
        literal_starts = {match.start() for match in _LITERAL_INCLUDE_RE.finditer(text)}
        for match in _ANY_INCLUDE_RE.finditer(text):
            if match.start() not in literal_starts:
                unresolved.append({
                    "including": including_relative,
                    "include": match.group(1).strip(),
                    "reason": "dynamic_or_macro_include",
                })

        for match in _LITERAL_INCLUDE_RE.finditer(text):
            opener, include = match.group(1), match.group(2).strip()
            include_path = Path(include.replace("/", os.sep))
            delimiter = "quote" if opener == '"' else "angle"
            include_line = text.count("\n", 0, match.start()) + 1
            search_roots = variant.quote_include_roots if opener == '"' else variant.angle_include_roots
            candidates: list[tuple[Path, str]] = []
            if opener == '"':
                candidates.append((logical.parent / include_path, "including_directory"))
            candidates.extend(
                (search_root / include_path, f"{delimiter}_search_root:{index}")
                for index, search_root in enumerate(search_roots)
            )
            selected_local: Path | None = None
            selected_external = False
            resolution_path = ""
            for candidate, candidate_role in candidates:
                try:
                    reject_links(candidate, root, code="LINKED_HEADER_UNSUPPORTED")
                except KickstartError:
                    try:
                        resolved = candidate.resolve(strict=True)
                    except OSError:
                        continue
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        if resolved.is_file():
                            selected_external = True
                            resolution_path = candidate_role
                            break
                        continue
                    raise
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not resolved.is_file():
                    continue
                try:
                    resolved.relative_to(root)
                except ValueError:
                    selected_external = True
                    resolution_path = candidate_role
                    break
                if resolved.suffix.casefold() in HEADER_SUFFIXES:
                    selected_local = resolved
                    resolution_path = candidate_role
                    break

            if selected_local is not None and any(
                selected_local.is_relative_to(after_root) for after_root in variant.idirafter_roots
            ):
                status, selected = _compiler_selected_literal(
                    argv=variant.argv,
                    directory=variant.directory,
                    including=logical,
                    opener=opener,
                    include=include,
                    project_root=root,
                )
                if status == "external":
                    selected_local = None
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and selected is not None:
                    selected_local = selected
                    resolution_path = "compiler_probe"
                else:
                    unresolved.append({
                        "including": including_relative,
                        "include": include,
                        "reason": "include_selection_ambiguous",
                    })
                    continue

            if selected_local is None and opener == '"' and not selected_external:
                status, selected = _compiler_selected_literal(
                    argv=variant.argv,
                    directory=variant.directory,
                    including=logical,
                    opener=opener,
                    include=include,
                    project_root=root,
                )
                if status == "external":
                    selected_external = True
                    resolution_path = "compiler_probe"
                elif status == "local" and selected is not None:
                    selected_local = selected
                    resolution_path = "compiler_probe"
                elif status == "missing":
                    unresolved.append({
                        "including": including_relative,
                        "include": include,
                        "reason": "quoted_include_unresolved",
                    })
                    continue

            if selected_local is not None:
                if selected_local.suffix.casefold() not in HEADER_SUFFIXES:
                    unresolved.append({
                        "including": including_relative,
                        "include": include,
                        "reason": "local_include_suffix_unsupported",
                        "selected": selected_local.relative_to(root).as_posix(),
                    })
                    continue
                target = selected_local.relative_to(root).as_posix()
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
            # Unresolved angle includes are normally compiler/system dependencies outside
            # the governed project. They are not promoted to a guessed external identity.

    return canonical_edges(edges), unresolved
