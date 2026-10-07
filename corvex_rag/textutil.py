"""Small text utilities shared across ingestion. Deliberately dependency-light: the token
count is an approximation (no tiktoken download) which is fine for chunk sizing."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"\S+")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[`])")


def count_tokens(text: str) -> int:
    """~1.3 tokens per whitespace-delimited word. Good enough to pack chunks; the pipeline
    never bills on this number."""
    words = len(_WORD_RE.findall(text))
    return max(1, round(words * 1.3))


def split_sentences(text: str) -> list[str]:
    parts = _SENT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def iter_paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs, but a fenced code block counts as one paragraph
    even if it contains blank lines."""
    out: list[str] = []
    buf: list[str] = []
    in_fence = False
    fence = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence, fence = True, stripped[:3]
            buf.append(line)
            continue
        if in_fence and stripped.startswith(fence):
            in_fence = False
            buf.append(line)
            continue
        if in_fence:
            buf.append(line)
            continue
        if line.strip() == "":
            if buf:
                out.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        out.append("\n".join(buf).strip())
    return [p for p in out if p]


def has_code_fence(text: str) -> bool:
    return bool(re.search(r"^\s*(```|~~~)", text, re.M))


def strip_quote_lines(text: str) -> str:
    """Drop leading '>' quote lines (forum reply quoting the parent)."""
    kept = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(kept).strip()
