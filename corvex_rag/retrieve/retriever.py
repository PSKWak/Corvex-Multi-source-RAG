"""Top-level retrieval: intent -> hybrid per-source -> source weighting -> rerank ->
diversity guard -> RetrievalResult. The single entry point the pipeline calls."""
from __future__ import annotations

import time

from ..config import Config
from ..ingest.embed import Embedder
from ..index.base import SearchBackend
from ..rate_limit import RateLimiter
from ..schema import RetrievalResult, SourceType
from . import hybrid, query_intent, source_weighting
from .rerank import Reranker, apply_diversity_guard


class Retriever:
    def __init__(
        self,
        cfg: Config,
        backend: SearchBackend,
        embedder: Embedder,
        reranker: Reranker,
        llm=None,
        limiters: dict[str, RateLimiter] | None = None,
    ) -> None:
        self.cfg = cfg
        self.backend = backend
        self.embedder = embedder
        self.reranker = reranker
        self.llm = llm
        self.limiters = limiters or {}

    def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        t: dict[str, float] = {}
        top_m = int(self.cfg.section("reranker", "top_m_in", default=30))
        top_k = top_k or int(self.cfg.section("reranker", "top_k_out", default=7))

        t0 = time.perf_counter()
        intent, intent_explain = query_intent.classify(self.cfg, query, llm=self.llm)
        t["intent_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        qvec = self.embedder.embed_query(query)
        t["embed_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        per_source = hybrid.retrieve_per_source(
            self.cfg, self.backend, query, qvec, embedder_name=getattr(self.embedder, "name", "")
        )
        t["hybrid_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        merged = source_weighting.apply_source_weighting(self.cfg, query, intent, per_source)
        t["weighting_ms"] = (time.perf_counter() - t0) * 1000

        candidates = merged[:top_m]
        t0 = time.perf_counter()
        reranked = self.reranker.rerank(query, list(candidates), top_k=top_m)
        if self.cfg.section("reranker", "diversity_guard", default=True):
            reranked = apply_diversity_guard(reranked, candidates, top_k)
        else:
            reranked = reranked[:top_k]
        t["rerank_ms"] = (time.perf_counter() - t0) * 1000

        return RetrievalResult(
            query=query, intent=intent, per_source=per_source,
            merged=merged, reranked=reranked, timings_ms=t,
            per_source_counts={st.value: len(v) for st, v in per_source.items()},
            intent_explain=intent_explain,
        )
