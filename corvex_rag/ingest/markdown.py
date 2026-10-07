"""Deterministic, dependency-free Markdown structure parsing: YAML frontmatter + an
ATX-heading section tree with real character offsets. Used by the documentation and blog
chunkers so chunk boundaries are explainable and stable."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(slots=True)
class Section:
    heading_path: list[str]        # e.g. ["Configuration", "workers"]  (max depth 3)
    level: int                     # heading level of this section's own heading (0 = preamble)
    text: str                      # content lines under the heading, excluding the heading line
    start: int                     # char offset of `text` within the source body
    end: int
    has_subsections: bool = False


def strip_frontmatter(body: str) -> tuple[dict, str, int]:
    """Return (frontmatter_dict, remaining_body, char_offset_of_remaining)."""
    if not body.startswith("---"):
        return {}, body, 0
    m = re.match(r"^---\n(.*?)\n---\n?", body, re.S)
    if not m:
        return {}, body, 0
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    offset = m.end()
    return fm, body[offset:], offset


def parse_sections(body: str, *, max_depth: int = 3) -> list[Section]:
    """Split `body` into sections at ATX headings (H1..H{max_depth}); deeper headings stay
    inline as content. Fenced code blocks are never treated as headings."""
    lines = body.splitlines(keepends=True)
    # precompute char offset of each line start
    offsets: list[int] = []
    acc = 0
    for ln in lines:
        offsets.append(acc)
        acc += len(ln)

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []          # (level, title)
    cur_level = 0
    cur_start_line = 0
    in_fence = False
    fence_tok = ""

    def flush(end_line: int) -> None:
        s_char = offsets[cur_start_line] if cur_start_line < len(offsets) else len(body)
        e_char = offsets[end_line] if end_line < len(offsets) else len(body)
        text = body[s_char:e_char].strip("\n")
        sections.append(
            Section(
                heading_path=[t for _, t in stack],
                level=cur_level,
                text=text,
                start=s_char,
                end=s_char + len(text) if text else s_char,
            )
        )

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if _FENCE_RE.match(line):
            tok = line.lstrip()[:3]
            if not in_fence:
                in_fence, fence_tok = True, tok
            elif line.lstrip().startswith(fence_tok):
                in_fence = False
            continue
        if in_fence:
            continue
        hm = _HEADING_RE.match(line)
        if not hm:
            continue
        level = len(hm.group(1))
        title = hm.group(2).strip()
        if level > max_depth:
            continue
        # close the section that ends right before this heading line
        flush(i)
        # adjust the heading stack
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        cur_level = level
        cur_start_line = i + 1

    flush(len(lines))

    # mark parents that have children
    for idx, sec in enumerate(sections):
        depth = len(sec.heading_path)
        for later in sections[idx + 1:]:
            ld = len(later.heading_path)
            if ld <= depth:
                break
            if later.heading_path[:depth] == sec.heading_path:
                sec.has_subsections = True
                break
    return [s for s in sections if s.text.strip()]
