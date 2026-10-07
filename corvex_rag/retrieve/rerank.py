"""Rerankers behind one interface (DESIGN.md §6). BGE baseline, Cohere optional (rate
limited), Identity for ablation. Includes the cross-source diversity guard."""
from __future__ import annotations

import abc
import re

import structlog

from ..config import Config
from ..rate_limit import RateLimiter
from ..schema import ScoredChunk, SourceType

log = structlog.get_logger("rerank")


class Reranker(abc.ABC):
    name: str

    @abc.abstractmethod
    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        """Return top_k, writing the cross-encoder score into components['rerank']."""


class IdentityReranker(Reranker):
    name = "identity"

    def rerank(self, query, candidates, top_k):
        for i, c in enumerate(candidates):
            c.components.setdefault("rerank", c.score)
            c.components["rerank_rank"] = i + 1
        return candidates[:top_k]


_WORD = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")
_STOP = {"the", "and", "for", "with", "you", "that", "this", "how", "what", "does", "are",
         "was", "when", "why", "can", "corvex", "from", "into", "your", "default"}
_ERRCODE = re.compile(r"cvx-\d{3,4}", re.I)


class LexicalReranker(Reranker):
    """Deterministic lexical reranker — the offline fallback when the BGE cross-encoder
    isn't installed. Beats identity by rewarding query-term coverage, exact error-code /
    config-key hits, and heading matches. Not as good as a cross-encoder; explainable."""
    name = "lexical"

    @staticmethod
    def _kw(t: str) -> set[str]:
        return {w for w in _WORD.findall(t.lower()) if w not in _STOP}

    def rerank(self, query, candidates, top_k):
        qk = self._kw(query)
        q_codes = {c.lower() for c in _ERRCODE.findall(query)}
        q_bigrams = {f"{a} {b}" for a, b in zip(sorted(qk), sorted(qk)[1:])}
        scored = []
        for c in candidates:
            m = c.chunk.metadata
            ck = self._kw(c.chunk.text)
            cover = len(qk & ck) / (len(qk) or 1)
            head = len(qk & self._kw(" ".join([m.title, *m.heading_path]))) / (len(qk) or 1)
            code_hit = 1.0 if q_codes & {e.lower() for e in m.error_codes} else 0.0
            key_hit = 1.0 if any(k.lower() in query.lower() for k in m.config_keys) else 0.0
            low = c.chunk.text.lower()
            bigram = sum(1 for b in q_bigrams if b in low) / (len(q_bigrams) or 1)
            s = cover + 0.5 * head + 0.6 * code_hit + 0.4 * key_hit + 0.3 * bigram
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for rank, (s, c) in enumerate(scored):
            c.components["rerank"] = round(float(s), 4)
            c.components["rerank_rank"] = rank + 1
            out.append(c)
        return out[:top_k]


class BGEReranker(Reranker):
    """`BAAI/bge-reranker-base` cross-encoder via sentence-transformers.CrossEncoder."""
    name = "bge"

    def __init__(self, cfg: Config) -> None:
        from sentence_transformers import CrossEncoder  # optional dep

        self.model_name = cfg.section("reranker", "bge", "model")
        self._model = CrossEncoder(self.model_name, cache_folder=str(cfg.model_cache_dir))

    def rerank(self, query, candidates, top_k):
        if not candidates:
            return []
        pairs = [(query, c.chunk.text) for c in candidates]
        scores = self._model.predict(pairs)
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        out: list[ScoredChunk] = []
        for rank, i in enumerate(order):
            c = candidates[i]
            c.components["rerank"] = float(scores[i])
            c.components["rerank_rank"] = rank + 1
            out.append(c)
        return out[:top_k]


class CohereReranker(Reranker):
    """Cohere `rerank-english-v3.0`. Opt-in, for the reranker ablation only. Every call
    goes through the shared `cohere` RateLimiter."""
    name = "cohere"

    def __init__(self, cfg: Config, limiter: RateLimiter) -> None:
        import cohere  # optional dep

        self.model = cfg.section("reranker", "cohere", "model")
        api_key = cfg.require_env(cfg.section("reranker", "cohere", "api_key_env"))
        self._client = cohere.Client(api_key)
        self.limiter = limiter

    def rerank(self, query, candidates, top_k):
        if not candidates:
            return []
        docs = [c.chunk.text for c in candidates]
        resp = self.limiter.call(
            self._client.rerank, model=self.model, query=query, documents=docs, top_n=len(docs)
        )
        out: list[ScoredChunk] = []
        for rank, r in enumerate(resp.results):
            c = candidates[r.index]
            c.components["rerank"] = float(r.relevance_score)
            c.components["rerank_rank"] = rank + 1
            out.append(c)
        return out[:top_k]


def apply_diversity_guard(
    reranked: list[ScoredChunk], pre_rerank: list[ScoredChunk], top_k: int
) -> list[ScoredChunk]:
    """If >=2 source types exist among the candidates, ensure the returned top_k covers
    >=2 by swapping the weakest entry of the dominant source for the best-ranked entry of a
    missing source."""
    avail = {c.chunk.metadata.source_type for c in pre_rerank}
    if len(avail) < 2 or len(reranked) <= 1:
        return reranked[:top_k]

    head = reranked[:top_k]
    present = {c.chunk.metadata.source_type for c in head}
    if len(present) >= 2:
        return head

    missing = avail - present
    replacement = next((c for c in reranked[top_k:] if c.chunk.metadata.source_type in missing), None)
    if replacement is None:
        return head
    dominant = head[0].chunk.metadata.source_type
    for i in range(len(head) - 1, -1, -1):
        if head[i].chunk.metadata.source_type == dominant:
            replacement.components["diversity_guard"] = f"swapped in for {head[i].chunk.chunk_id}"
            head[i] = replacement
            break
    return head


def get_reranker(cfg: Config, limiter: RateLimiter | None = None) -> Reranker:
    provider = cfg.section("reranker", "provider")
    if provider == "identity":
        return IdentityReranker()
    if provider == "lexical":
        return LexicalReranker()
    if provider == "bge":
        try:
            return BGEReranker(cfg)
        except ImportError:
            log.warning("bge_reranker_unavailable",
                        detail="sentence-transformers not installed; falling back to lexical reranker")
            return LexicalReranker()
    if provider == "cohere":
        if limiter is None:
            limiter = RateLimiter.for_provider(cfg, "cohere")
        return CohereReranker(cfg, limiter)
    raise ValueError(f"unknown reranker provider {provider!r}")
