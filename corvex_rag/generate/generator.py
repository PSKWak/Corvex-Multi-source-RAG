"""Draft-answer generation (DESIGN.md §8)."""
from __future__ import annotations

import dataclasses
import re

from ..config import Config
from ..reason.context_builder import build_context
from ..schema import Citation, DraftAnswer, ResolvedContext
from .llm import LLMClient
from .prompts import GENERATION_SYSTEM, GENERATION_USER

_STRICTER_SUFFIX = (
    "\n\nSTRICT MODE: every sentence must end with at least one [n] citation or be removed. "
    "Do not include any claim that is not directly supported by an evidence block."
)
_CITE_LINE_RE = re.compile(r"\**CITATIONS:?\**\s*[:\-]?\s*\[([0-9,\s]*)\]", re.I)
_INLINE_CITE_RE = re.compile(r"\[(\d+)\]")
# gpt-oss and some models emit 【7†L1-L4】 / [oai_citation:7] style markers — normalise to [7]
_ALT_CITE_RE = re.compile(r"【\s*(\d+)\s*[†:][^】]*】|\[oai_citation:?\s*(\d+)[^\]]*\]|\bcite\s*(\d+)\b", re.I)


def _normalise_markers(text: str) -> str:
    return _ALT_CITE_RE.sub(lambda m: f"[{next(g for g in m.groups() if g)}]", text)


def _parse_citations(text: str, allowed: list[Citation]) -> tuple[str, list[Citation]]:
    text = _normalise_markers(text)
    allowed_by_n = {c.n: c for c in allowed}
    cited_ns: list[int] = []

    m = _CITE_LINE_RE.search(text)
    body = text[: m.start()].rstrip() if m else text.rstrip()
    if m:
        cited_ns = [int(x) for x in m.group(1).split(",") if x.strip().isdigit()]
    # union with inline markers actually used in the body
    for n in _INLINE_CITE_RE.findall(body):
        if int(n) not in cited_ns:
            cited_ns.append(int(n))

    # drop fabricated ids, renumber sequentially, rewrite inline markers
    kept = [n for n in cited_ns if n in allowed_by_n]
    remap = {old: i + 1 for i, old in enumerate(kept)}
    body = _INLINE_CITE_RE.sub(lambda mm: f"[{remap[int(mm.group(1))]}]" if int(mm.group(1)) in remap else "", body)
    body = _tidy(body)
    citations = [dataclasses.replace(allowed_by_n[old], n=new) for old, new in remap.items()]
    return body, citations


def _tidy(body: str) -> str:
    """Trim orphan markdown left by a truncated final line or the stripped CITATIONS line."""
    lines = body.rstrip().splitlines()
    while lines and lines[-1].strip() in ("", "*", "-", "•", "#", ">", "|"):
        lines.pop()
    out = "\n".join(lines).strip()
    return re.sub(r"[ \t]+\n", "\n", out)


def generate(
    cfg: Config,
    llm: LLMClient,
    question: str,
    resolved: ResolvedContext,
    *,
    stricter: bool = False,
) -> tuple[DraftAnswer, dict]:
    budget = int(cfg.section("generation", "max_tokens", default=1024))
    ctx_budget = 6000 - budget
    context, allowed = build_context(cfg, resolved, token_budget=ctx_budget)

    system = GENERATION_SYSTEM + (_STRICTER_SUFFIX if stricter else "")
    user = GENERATION_USER.format(question=question, context=context)
    resp = llm.complete(system, user, temperature=cfg.section("generation", "temperature", default=0.1),
                        max_tokens=budget)

    body, citations = _parse_citations(resp.text, allowed)
    draft = DraftAnswer(answer_md=body, citations=citations, raw_model_output=resp.text)
    usage = {
        "provider": resp.provider, "model": resp.model,
        "prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
        "fell_back_to_mock": getattr(llm, "fell_back", False),
        "n_evidence_in_context": len(allowed),
    }
    return draft, usage
