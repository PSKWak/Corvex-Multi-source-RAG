"""End-to-end orchestration. The one class the CLI, the eval harness, and tests use.

    RAGPipeline(cfg).answer("What is the default completed-job retention?") -> Answer
"""
from __future__ import annotations

from collections import Counter
from time import perf_counter

from .config import Config, load_config
from .generate.generator import generate
from .generate.llm import get_llm_with_mock_fallback
from .ingest.embed import get_embedder
from .index.base import get_backend
from .logging_setup import append_query_log, get_logger
from .observability.feedback import get_feedback_store
from .observability.provenance import ProvenanceRecorder
from .rate_limit import RateLimiter
from .reason.contradiction import detect as detect_contradictions
from .reason.resolution import resolve as resolve_conflicts
from .retrieve.rerank import get_reranker
from .retrieve.retriever import Retriever
from .retrieve.source_weighting import target_version_for_query
from .schema import Answer, Feedback, RetrievalResult
from .verify.corrector import correct
from .verify.self_check import run_self_check

log = get_logger("pipeline")

# notional prices $/1M tokens for the cost estimate (free tiers -> 0)
_PRICES = {"mock": (0.0, 0.0), "groq": (0.0, 0.0), "gemini": (0.0, 0.0),
           "anthropic": (2.0, 10.0), "openai": (0.15, 0.6)}


def _ev_brief(sc) -> dict:
    m = sc.chunk.metadata
    return {"chunk_id": sc.chunk.chunk_id, "source_id": m.source_id, "source_type": m.source_type.value,
            "version": m.product_version, "score": round(sc.score, 5),
            "components": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sc.components.items()}}


class RAGPipeline:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or load_config()
        self.limiters = {p: RateLimiter.for_provider(self.cfg, p)
                         for p in (self.cfg.section("rate_limits", default={}) or {})}
        self.embedder = get_embedder(self.cfg)
        self.backend = get_backend(self.cfg)
        self.llm = get_llm_with_mock_fallback(self.cfg, limiters=self.limiters)
        self.reranker = get_reranker(self.cfg, self.limiters.get("cohere"))
        from .reason.nli import get_nli
        self.nli = get_nli(self.cfg)
        self.retriever = Retriever(self.cfg, self.backend, self.embedder, self.reranker, self.llm, self.limiters)
        self.feedback = get_feedback_store(self.cfg)
        if self.backend.count().get("total", 0) == 0:
            raise RuntimeError("index is empty — run `corvex-rag ingest` first")

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, *, top_k: int | None = None) -> Answer:
        t0 = perf_counter()
        top_k = top_k or int(self.cfg.section("reranker", "top_k_out", default=7))
        prov = ProvenanceRecorder(self.cfg.logs_dir, self.cfg.hash())
        prov.set(question=question)
        target_version = target_version_for_query(self.cfg, question)
        prov.set(target_version=target_version)

        rr = self.retriever.retrieve(question, top_k=top_k)
        prov.stage("retrieval", intent=rr.intent.value, intent_explain=rr.intent_explain,
                   per_source_counts=rr.per_source_counts, timings_ms=rr.timings_ms,
                   evidence=[_ev_brief(sc) for sc in rr.reranked])

        cand_k = int(self.cfg.section("contradiction", "candidate_k", default=20))

        def _resolve(rr_or_none: RetrievalResult | None, force_target: str | None = None):
            src = rr_or_none if rr_or_none is not None else rr
            final = list(src.reranked)
            final_ids = {f.chunk.chunk_id for f in final}
            wider = [w for w in src.merged[:cand_k] if w.chunk.chunk_id not in final_ids]
            tv = force_target or target_version

            found = detect_contradictions(
                self.cfg, self.embedder, self.nli, self.llm, question, final + wider,
                self.limiters.get(self.cfg.section("generation", "llm")),
            )
            # keep conflicts touching the final set; pull their missing counterpart into evidence
            kept, extra = [], []
            for c in found:
                if not any(eid in final_ids for eid in c.evidence_ids):
                    continue
                kept.append(c)
                for eid in c.evidence_ids:
                    if eid not in final_ids and not any(e.chunk.chunk_id == eid for e in extra):
                        ch = next((w for w in wider if w.chunk.chunk_id == eid), None)
                        if ch:
                            ch.components["pulled_in_by_contradiction"] = True
                            extra.append(ch)
            return resolve_conflicts(self.cfg, question, tv, final + extra, kept)

        def _generate(q: str, res, stricter: bool = False):
            return generate(self.cfg, self.llm, q, res, stricter=stricter)

        def _self_check(q: str, tv: str | None, draft, res):
            return run_self_check(self.cfg, self.embedder, self.nli, self.llm, q, tv, draft, res)

        resolved = _resolve(rr)
        prov.stage("contradiction", items=[{"type": c.type.value, "detail": c.detail,
                                            "evidence_ids": c.evidence_ids} for c in resolved.contradictions])
        prov.stage("resolution", conflict_notes=resolved.conflict_notes,
                   decisions=[{"rule": d.rule_applied, "winner": d.winner_evidence_id,
                               "rationale": d.rationale} for d in resolved.decisions])

        draft, usage = _generate(question, resolved)
        prov.stage("generation", **usage)
        report = _self_check(question, target_version, draft, resolved)

        if not report.passed and self.cfg.section("self_check", "enabled", default=True):
            draft, resolved, report = correct(
                self.cfg, question=question, target_version=target_version, draft=draft,
                resolved=resolved, report=report, retriever=self.retriever,
                generate_fn=_generate, resolve_fn=_resolve, self_check_fn=_self_check, top_k=top_k,
            )

        sources_used = Counter(c.source_type.value for c in draft.citations)
        confidence = _confidence(report, draft, resolved)
        latency_ms = (perf_counter() - t0) * 1000
        pin, pout = _PRICES.get(usage.get("provider", "mock"), (0.0, 0.0))
        cost = (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1e6

        prov.stage("self_check", passed=report.passed,
                   results=[{"name": r.name, "passed": r.passed, "score": round(r.score, 3), "detail": r.detail}
                            for r in report.results],
                   corrections=report.correction_log)
        prov.set(latency_ms=latency_ms, cost_estimate_usd=round(cost, 6),
                 tokens={"prompt": usage.get("prompt_tokens", 0), "completion": usage.get("completion_tokens", 0)})
        prov.set(answer={"sources_used": dict(sources_used), "confidence": confidence,
                         "self_check_passed": report.passed, "n_corrections": report.corrections_applied})
        ppath = prov.write()
        append_query_log(self.cfg.logs_dir, prov.summary())

        return Answer(
            query_id=prov.query_id, question=question, answer_md=draft.answer_md,
            citations=draft.citations, confidence=confidence, conflict_notes=resolved.conflict_notes,
            self_check=report, sources_used=dict(sources_used), latency_ms=latency_ms,
            provenance_path=str(ppath),
        )

    # ------------------------------------------------------------------ feedback / retrieval-only
    def record_feedback(self, query_id: str, rating: str, note: str = "", better_answer: str | None = None) -> None:
        self.feedback.record(Feedback(query_id=query_id, rating=rating, note=note, better_answer=better_answer))

    def retrieve_only(self, question: str, *, top_k: int | None = None) -> RetrievalResult:
        return self.retriever.retrieve(question, top_k=top_k or int(self.cfg.section("reranker", "top_k_out", default=7)))


def _confidence(report, draft, resolved) -> float:
    import re
    if re.search(r"\b(don't have|not (?:documented|covered|in the)|can't answer)\b", draft.answer_md, re.I) and len(draft.answer_md) < 400:
        base = 0.6
    elif not report.results:
        base = 0.7 if report.passed else 0.4
    else:
        # weighted: grounding + relevance dominate, other checks are pass/fail nudges
        key = [r.score for r in report.results if r.name in ("grounding", "relevance")]
        others = [r for r in report.results if r.name not in ("grounding", "relevance")]
        base = (sum(key) / len(key)) if key else 0.6
        base -= 0.08 * sum(1 for r in others if not r.passed)
        if report.passed:
            base = max(base, 0.6)          # a fully-passing self-check floors confidence at 0.6
    base -= 0.08 * report.corrections_applied
    base -= 0.05 * sum(1 for d in resolved.decisions if d.rule_applied == "unresolved")
    if not report.passed:
        base = min(base, 0.5)
    return round(max(0.0, min(1.0, base)), 2)
