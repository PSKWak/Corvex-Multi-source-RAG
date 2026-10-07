"""Per-source chunking. Structure-first, semantic-second (see DESIGN.md §3.1).

Each chunker returns `RawChunk(text, heading_path, char_span, extra)`; `pipeline.py` turns
those into `Chunk`s with stable ids `sha1(source_id|char_span|strategy_version)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import Config
from ..schema import SourceDocument, SourceType
from ..textutil import count_tokens, has_code_fence, iter_paragraphs, split_sentences, strip_quote_lines
from .markdown import Section, parse_sections


@dataclass(slots=True)
class RawChunk:
    text: str
    heading_path: list[str]
    char_span: tuple[int, int]
    extra: dict | None = None


class Chunker(Protocol):
    name: str
    def split(self, doc: SourceDocument) -> list[RawChunk]: ...


def _breadcrumb(title: str, heading_path: list[str]) -> str:
    segs = [title, *heading_path]
    deduped: list[str] = []
    for s in segs:
        if not deduped or deduped[-1].strip().lower() != s.strip().lower():
            deduped.append(s)
    return " > ".join(deduped)


def _pack_paragraphs(
    paras: list[str], target: int, max_tokens: int, overlap_sentences: int
) -> list[str]:
    """Greedy pack paragraphs to ~target tokens; a single oversized paragraph that is a
    code block is emitted whole; oversized prose is sentence-split."""
    out: list[str] = []
    buf: list[str] = []
    buf_tok = 0

    def emit() -> None:
        nonlocal buf, buf_tok
        if buf:
            out.append("\n\n".join(buf).strip())
            buf, buf_tok = [], 0

    for para in paras:
        ptok = count_tokens(para)
        if ptok > max_tokens and not has_code_fence(para):
            emit()
            sents = split_sentences(para)
            cur: list[str] = []
            cur_tok = 0
            for s in sents:
                st = count_tokens(s)
                if cur and cur_tok + st > target:
                    out.append(" ".join(cur))
                    cur = cur[-overlap_sentences:] if overlap_sentences else []
                    cur_tok = sum(count_tokens(x) for x in cur)
                cur.append(s)
                cur_tok += st
            if cur:
                out.append(" ".join(cur))
            continue
        if buf and buf_tok + ptok > target:
            emit()
        buf.append(para)
        buf_tok += ptok
    emit()
    return [o for o in out if o.strip()]


# --------------------------------------------------------------------------- documentation
class MarkdownHeadingChunker:
    """H1/H2/H3-scoped. Never splits fenced code blocks or tables. Oversized sections
    sub-split on paragraph boundaries with a small sentence overlap. Heading breadcrumb is
    prepended to each chunk and kept in `heading_path`."""
    name = "markdown_heading"

    def __init__(self, cfg: Config) -> None:
        c = cfg.section("ingestion", "chunking", "documentation")
        self.target = int(c["target_tokens"])
        self.max_tokens = int(c["max_tokens"])
        self.min_tokens = int(c["min_tokens"])
        self.overlap = int(c["subsplit_overlap_sentences"])

    def _sections(self, doc: SourceDocument) -> list[Section]:
        return parse_sections(doc.body, max_depth=3)

    def split(self, doc: SourceDocument) -> list[RawChunk]:
        out: list[RawChunk] = []
        pending: RawChunk | None = None
        for sec in self._sections(doc):
            bc = _breadcrumb(doc.title, sec.heading_path)
            if count_tokens(sec.text) <= self.max_tokens:
                pieces = [sec.text]
            else:
                pieces = _pack_paragraphs(
                    iter_paragraphs(sec.text), self.target, self.max_tokens, self.overlap
                )
            for j, piece in enumerate(pieces):
                span = (sec.start, sec.end) if len(pieces) == 1 else (sec.start + j, sec.start + j + 1)
                rc = RawChunk(text=f"{bc}\n\n{piece}", heading_path=sec.heading_path, char_span=span)
                # merge tiny sections forward so we don't emit 1-line chunks
                if count_tokens(piece) < self.min_tokens and pending is None:
                    pending = rc
                    continue
                if pending is not None:
                    rc = RawChunk(
                        text=pending.text + "\n\n" + piece,
                        heading_path=pending.heading_path,
                        char_span=(pending.char_span[0], rc.char_span[1]),
                    )
                    pending = None
                out.append(rc)
        if pending is not None:
            out.append(pending)
        return out


# --------------------------------------------------------------------------- forums
class ThreadAwareChunker:
    """Unit = question + accepted answer when they fit; else question alone + each
    substantive answer alone. Drops trivial replies, strips nested `>` quotes, keeps
    per-post metadata."""
    name = "thread_aware"

    def __init__(self, cfg: Config) -> None:
        c = cfg.section("ingestion", "chunking", "forums")
        self.target = int(c["target_tokens"])
        self.max_tokens = int(c["max_tokens"])
        self.drop_below = int(c["drop_reply_below_tokens"])
        self.pair_accepted = bool(c["pair_question_with_accepted_answer"])
        self.strip_quotes = bool(c["strip_quotes"])

    def _post_text(self, title: str, post: dict) -> str:
        body = post.get("body", "")
        if self.strip_quotes:
            body = strip_quote_lines(body)
        meta = f"[{post['_tag']}"
        if post.get("is_accepted"):
            meta += " (accepted)"
        if post.get("votes") is not None:
            meta += f", {post['votes']} votes"
        meta += f"] {post.get('author', 'unknown')} ({post.get('author_role') or 'community'})"
        return f"{title}\n{meta}\n{body}".strip()

    def _substantive(self, post: dict) -> bool:
        body = post.get("body", "")
        if post["_index"] == 0 or post.get("is_accepted"):
            return True
        if count_tokens(body) >= self.drop_below:
            return True
        return "```" in body or "http" in body

    def split(self, doc: SourceDocument) -> list[RawChunk]:
        posts: list[dict] = doc.raw.get("posts", [])
        if not posts:
            return []
        out: list[RawChunk] = []
        question = posts[0]
        accepted = next((p for p in posts[1:] if p.get("is_accepted")), None)
        covered: set[int] = set()

        if self.pair_accepted and accepted is not None:
            combined = self._post_text(doc.title, question) + "\n\n---\n\n" + self._post_text(doc.title, accepted)
            if count_tokens(combined) <= self.max_tokens:
                span = (question["_span"][0], accepted["_span"][1])
                out.append(RawChunk(combined, [], span, extra={
                    "is_accepted_answer": True, "votes": accepted.get("votes"),
                    "author_role": accepted.get("author_role"), "post_indices": [0, accepted["_index"]],
                }))
                covered = {0, accepted["_index"]}

        for p in posts:
            if p["_index"] in covered or not self._substantive(p):
                continue
            out.append(RawChunk(
                self._post_text(doc.title, p), [], p["_span"],
                extra={
                    "is_accepted_answer": bool(p.get("is_accepted")),
                    "votes": p.get("votes"),
                    "author_role": p.get("author_role"),
                    "post_indices": [p["_index"]],
                },
            ))
        return out


# --------------------------------------------------------------------------- blogs
class HeadingThenSemanticChunker:
    """Heading-aware like docs; within an oversized structureless span, split on
    percentile-threshold drops in consecutive-sentence embedding similarity. The semantic
    path only runs when a real embedding model (BGE) is available — with the hashing
    embedder it falls back to paragraph packing (single-sentence hash vectors are too
    noisy for a meaningful breakpoint)."""
    name = "heading_then_semantic"

    def __init__(self, cfg: Config, embedder=None) -> None:
        c = cfg.section("ingestion", "chunking", "blogs")
        self.target = int(c["target_tokens"])
        self.max_tokens = int(c["max_tokens"])
        self.pct = float(c["semantic_breakpoint_percentile"])
        self.embedder = embedder
        self._semantic_ok = getattr(embedder, "name", "") == "bge"

    def _semantic_split(self, text: str) -> list[str]:
        import numpy as np

        sents = split_sentences(text)
        if len(sents) < 4:
            return [text]
        vecs = np.array(self.embedder.embed_documents(sents))
        sims = (vecs[:-1] * vecs[1:]).sum(axis=1)
        thresh = np.percentile(sims, 100 - self.pct)
        pieces, cur = [], [sents[0]]
        for i, s in enumerate(sents[1:]):
            if sims[i] < thresh and count_tokens(" ".join(cur)) >= self.target // 2:
                pieces.append(" ".join(cur))
                cur = [s]
            else:
                cur.append(s)
        pieces.append(" ".join(cur))
        # enforce max
        final: list[str] = []
        for p in pieces:
            if count_tokens(p) <= self.max_tokens:
                final.append(p)
            else:
                final.extend(_pack_paragraphs([p], self.target, self.max_tokens, 1))
        return final

    def split(self, doc: SourceDocument) -> list[RawChunk]:
        out: list[RawChunk] = []
        for sec in parse_sections(doc.body, max_depth=3):
            bc = _breadcrumb(doc.title, sec.heading_path)
            if count_tokens(sec.text) <= self.max_tokens:
                pieces = [sec.text]
            elif self._semantic_ok and not has_code_fence(sec.text):
                pieces = self._semantic_split(sec.text)
            else:
                pieces = _pack_paragraphs(iter_paragraphs(sec.text), self.target, self.max_tokens, 1)
            for j, piece in enumerate(pieces):
                span = (sec.start, sec.end) if len(pieces) == 1 else (sec.start + j, sec.start + j + 1)
                out.append(RawChunk(f"{bc}\n\n{piece}", sec.heading_path, span))
        return out


# --------------------------------------------------------------------------- fixed-size (ablation baseline)
class FixedSizeChunker:
    """Token-window chunker with overlap. Ignores structure. Only used by the chunking
    ablation."""
    name = "fixed_size"

    def __init__(self, cfg: Config, window_tokens: int = 400, overlap_tokens: int = 60) -> None:
        self.window = window_tokens
        self.overlap = overlap_tokens

    def split(self, doc: SourceDocument) -> list[RawChunk]:
        words = doc.body.split()
        approx_words = int(self.window / 1.3)
        step = max(1, approx_words - int(self.overlap / 1.3))
        out: list[RawChunk] = []
        for i in range(0, len(words), step):
            piece = " ".join(words[i : i + approx_words])
            if not piece.strip():
                continue
            out.append(RawChunk(f"{doc.title}\n\n{piece}", [], (i, i + approx_words)))
            if i + approx_words >= len(words):
                break
        return out


def get_chunker(cfg: Config, source_type: SourceType | str, *, fixed_size: bool = False, embedder=None) -> Chunker:
    if fixed_size:
        return FixedSizeChunker(cfg)
    st = SourceType(source_type) if not isinstance(source_type, SourceType) else source_type
    if st is SourceType.DOCUMENTATION:
        return MarkdownHeadingChunker(cfg)
    if st is SourceType.FORUM:
        return ThreadAwareChunker(cfg)
    if st is SourceType.BLOG:
        return HeadingThenSemanticChunker(cfg, embedder=embedder)
    raise ValueError(f"no chunker for {source_type!r}")
