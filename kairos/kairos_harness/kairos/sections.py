from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import FIRST_WINDOW_BYTES, MAX_SECTION_BYTES, MAX_SECTIONS
from .markdown import mask_markdown_code


class SectionError(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    capsule: str
    body: str
    position: int


ANCHOR_HEADING_RE = re.compile(
    r'^<a id="(?P<id>s-[a-z0-9][a-z0-9-]{1,62})"></a>\s*\n##\s+(?P<title>[^\n]+)\s*$',
    re.MULTILINE,
)


def _section_capsule(content: str) -> str:
    lines = content.strip().splitlines()
    quoted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if quoted:
                break
            continue
        if stripped.startswith(">"):
            value = stripped[1:].strip()
            value = re.sub(r"^(?:capsule|context)\s*:\s*", "", value, flags=re.I)
            quoted.append(value)
            continue
        if quoted:
            break
    capsule = " ".join(quoted).strip()
    if capsule:
        return capsule[:480]
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "<!--", "[ref:")):
            return stripped[:480]
    raise SectionError("every indexed section requires a blockquote capsule or a first summary line")


def parse_sections(body: str, *, header_bytes: int, ingested: bool = False) -> list[Section]:
    # The context index is a restatement of the section map for a reader opening the raw
    # file. An ingested document is reached through the database, which serves that map from
    # the parsed anchors and capsules, so the restatement is not required of it. The
    # deviation stays visible: the corpus census reports every document that omits it.
    if not ingested and "## CONTEXT INDEX" not in body:
        raise SectionError("document must contain '## CONTEXT INDEX' immediately after its title")
    structural = mask_markdown_code(body)
    matches = list(ANCHOR_HEADING_RE.finditer(structural))
    if not matches:
        raise SectionError("document contains no stable <a id=\"s-*\"></a> section anchors")
    if len(matches) > MAX_SECTIONS:
        raise SectionError(f"document contains {len(matches)} sections; maximum is {MAX_SECTIONS}")
    # The first-window contract buys a cheap first read of the raw file. An ingested document
    # is never read that way: it is reached through the database and opened at a named
    # anchor, so its header is calibration surface rather than a first read. Enforcing the
    # window there would only forbid the structure that removes the read.
    if not ingested:
        first_window = header_bytes + len(body[: matches[0].start()].encode("utf-8"))
        if first_window > FIRST_WINDOW_BYTES:
            raise SectionError(
                f"header plus context index uses {first_window} bytes; first indexed section must begin by byte {FIRST_WINDOW_BYTES}"
            )
    seen: set[str] = set()
    sections: list[Section] = []
    for position, match in enumerate(matches):
        section_id = match.group("id")
        if section_id in seen:
            raise SectionError(f"duplicate section id: {section_id}")
        seen.add(section_id)
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        section_body = body[match.end() : end].strip()
        section_bytes = len(section_body.encode("utf-8"))
        if section_bytes > MAX_SECTION_BYTES:
            raise SectionError(
                f"section {section_id} uses {section_bytes} bytes; maximum is {MAX_SECTION_BYTES}"
            )
        sections.append(
            Section(
                section_id=section_id,
                title=match.group("title").strip(),
                capsule=_section_capsule(section_body),
                body=section_body,
                position=position,
            )
        )
    all_h2 = [value.strip() for value in re.findall(r"^##\s+([^\n]+)$", structural, re.MULTILINE)]
    expected = ["CONTEXT INDEX", *[section.title for section in sections]]
    unexpected = [title for title in all_h2 if title not in expected]
    if unexpected:
        raise SectionError(f"H2 headings without stable section anchors: {', '.join(unexpected)}")
    return sections


def validate_answer_targets(metadata: dict, sections: list[Section]) -> None:
    available = {section.section_id for section in sections}
    missing = sorted({answer["target"] for answer in metadata.get("answers", [])} - available)
    if missing:
        raise SectionError(f"answer handles target missing sections: {', '.join(missing)}")
