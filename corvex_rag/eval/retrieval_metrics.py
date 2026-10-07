"""Deterministic retrieval metrics — no LLM. Driven by evaluation/retrieval_cases.json.

Relevance is matched at the parent `source_id` level (doc-level), which is how the eval
cases are authored (`relevant_doc_ids`). `supporting_doc_ids` count as relevant for recall
but are not required for precision.
"""
from __future__ import annotations

import math
import time
from statistics import mean
from typing import Sequence


def _dcg(rels: Sequence[int]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at_k(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    gains = [1 if cid in relevant else 0 for cid in ranked_ids[:k]]
    ideal = sorted(gains, reverse=True)
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg else 0.0


def mrr(ranked_ids: Sequence[str], relevant: set[str]) -> float:
    for i, cid in enumerate(ranked_ids, start=1):
        if cid in relevant:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked_ids[:k]) & relevant) / len(relevant)


def context_precision(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    top = ranked_ids[:k]
    if not top:
        return 0.0
    return sum(1 for c in top if c in relevant) / len(top)


def context_recall(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    return recall_at_k(ranked_ids, relevant, k)


def per_source_recall(ranked_sources: Sequence[str], expected_sources: Sequence[str]) -> float:
    if not expected_sources:
        return 1.0
    return len(set(ranked_sources) & set(expected_sources)) / len(set(expected_sources))


def evaluate_retrieval(pipeline, cases: list[dict], ks: list[int]) -> dict:
    """Run pipeline.retrieve_only per case; aggregate every metric at each k + latency."""
    per_case: list[dict] = []
    agg: dict[str, list[float]] = {}

    for case in cases:
        must = set(case.get("relevant_doc_ids", []))
        may = must | set(case.get("supporting_doc_ids", []))
        t0 = time.perf_counter()
        res = pipeline.retrieve_only(case["question"], top_k=max(ks))
        latency = (time.perf_counter() - t0) * 1000
        ranked_sources_ids = [sc.chunk.metadata.source_id for sc in res.reranked]
        ranked_source_types = [sc.chunk.metadata.source_type.value for sc in res.reranked]

        row = {"id": case["id"], "latency_ms": round(latency, 1),
               "retrieved": ranked_sources_ids[: max(ks)]}
        if not must:                       # no-answer case: reward retrieving little/nothing relevant
            row["is_no_answer"] = True
        for k in ks:
            row[f"ndcg@{k}"] = round(ndcg_at_k(ranked_sources_ids, must or may, k), 3)
            row[f"recall@{k}"] = round(recall_at_k(ranked_sources_ids, may, k), 3)
            row[f"context_precision@{k}"] = round(context_precision(ranked_sources_ids, may, k), 3)
        row["mrr"] = round(mrr(ranked_sources_ids, must or may), 3)
        row["per_source_recall"] = round(
            per_source_recall(ranked_source_types, case.get("expected_sources", [])), 3
        )
        per_case.append(row)
        for key, val in row.items():
            if isinstance(val, (int, float)) and key != "latency_ms":
                agg.setdefault(key, []).append(float(val))
        agg.setdefault("latency_ms", []).append(latency)

    summary = {k: round(mean(v), 3) for k, v in agg.items() if v}
    return {"summary": summary, "per_case": per_case, "n_cases": len(cases), "ks": ks}
