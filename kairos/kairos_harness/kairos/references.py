from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .constants import MAX_REFERENCES, RELATION_TYPES
from .util import resolve_workspace_path
from .markdown import mask_markdown_code


class ReferenceError(ValueError):
    pass


REF_RE = re.compile(r"\[ref:([^\]]+)\]")


@dataclass(frozen=True)
class Reference:
    raw: str
    target_path: str
    target_section: str | None
    target_id: str | None
    version: str
    relation: str
    tags: tuple[str, ...]
    source: str


def parse_reference(raw: str) -> Reference:
    parts = [part.strip() for part in raw.split("|")]
    locator = parts[0]
    if "#" in locator:
        target_path, target_section = locator.split("#", 1)
    else:
        target_path, target_section = locator, None
    fields: dict[str, str] = {}
    for part in parts[1:]:
        if ":" not in part:
            raise ReferenceError(f"invalid reference field: {part!r}")
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()
    missing = [key for key in ("v", "tags", "src") if not fields.get(key)]
    if missing:
        raise ReferenceError(f"reference missing fields: {', '.join(missing)}")
    relation = fields.get("rel", "references")
    if relation not in RELATION_TYPES:
        raise ReferenceError(f"unsupported relation type: {relation!r}")
    tags = tuple(sorted({tag.strip() for tag in fields["tags"].split(",") if tag.strip()}))
    if not target_path.strip() or not tags:
        raise ReferenceError("reference target and tags must not be empty")
    return Reference(
        raw=raw,
        target_path=target_path.strip().replace("\\", "/"),
        target_section=target_section.strip() if target_section else None,
        target_id=fields.get("id") or None,
        version=fields["v"],
        relation=relation,
        tags=tags,
        source=fields["src"],
    )


def iter_references(text: str) -> Iterable[Reference]:
    searchable = mask_markdown_code(text)
    for match in REF_RE.finditer(searchable):
        yield parse_reference(match.group(1).strip())


def collect_references(metadata: dict, sections: Iterable[object]) -> list[tuple[str | None, Reference]]:
    collected: list[tuple[str | None, Reference]] = []
    for value in metadata.get("refs", {}).values():
        refs = list(iter_references(value))
        if len(refs) != 1:
            raise ReferenceError("every header ref value must contain exactly one [ref:...] pointer")
        collected.append((None, refs[0]))
    for section in sections:
        for ref in iter_references(section.body):
            collected.append((section.section_id, ref))
    if len(collected) > MAX_REFERENCES:
        raise ReferenceError(f"document contains {len(collected)} references; maximum is {MAX_REFERENCES}")
    return collected


def validate_reference_targets(
    workspace: Path,
    references: Iterable[tuple[str | None, Reference]],
    *,
    resolve: bool = True,
) -> None:
    # A document imported from another workspace carries pointers that are relative to that
    # workspace. They cannot resolve here and are recorded as external instead: the row is
    # written, the target is not followed. External references are leaves.
    if not resolve:
        return
    for _, ref in references:
        target = resolve_workspace_path(workspace, ref.target_path)
        if not target.exists() or not target.is_file():
            raise ReferenceError(f"reference target does not exist: {ref.target_path}")
        if ref.target_section:
            if target.suffix.lower() != ".md":
                raise ReferenceError(f"section target requires Markdown document: {ref.target_path}")
            content = target.read_text(encoding="utf-8")
            marker = f'<a id="{ref.target_section}"></a>'
            if marker not in content:
                raise ReferenceError(f"reference section does not exist: {ref.target_path}#{ref.target_section}")
