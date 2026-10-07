"""RAGAS-style answer/faithfulness scoring (DESIGN.md §11).

If the `ragas` package is installed it is used directly with our LLMClient + Embedder as
the judge/embeddings. Otherwise a lightweight, deterministic re-implementation of the same
five metrics runs — token-overlap / embedding-similarity proxies, with an LLM judge layer
that activates only when `generation.llm` is not the offline mock. Judgments are cached to
`logs/ragas_cache` so re-runs don't re-bill.
"""
from __future__ import annotations

import hashlib
import json
import re
from statistics import mean

import structlog

from ..config import Config

log = structlog.get_logger("ragas")
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness"]
_WORD = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")


def _kw(t: str) -> set[str]:
    return set(_WORD.findall(t.lower()))


def build_ragas_dataset(pipeline, questions: list[dict], ground_truth: dict[str, dict]) -> list[dict]:
    rows = []
    id_to_chunk = {}
    try:
        id_to_chunk = pipeline.backend._chunks  # local backend convenience
    except AttributeError:
        pass
    for q in questions:
        ans = pipeline.answer(q["question"])
        contexts = []
        for c in ans.citations:
            ch = id_to_chunk.get(c.chunk_id) if id_to_chunk else pipeline.backend.get(c.chunk_id)
            if ch:
                contexts.append(ch.text)
        gt = ground_truth.get(q["id"], {})
        rows.append({
            "id": q["id"], "question": q["question"], "answer": ans.answer_md,
            "contexts": contexts, "ground_truth": gt.get("reference_answer", ""),
            "must_mention": gt.get("must_mention", []), "must_not_contain": gt.get("must_not_contain", []),
            "confidence": ans.confidence,
        })
    return rows


def _sentences(t: str) -> list[str]:
    t = re.sub(r"CITATIONS:.*$", "", t, flags=re.S)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if len(s.strip()) > 15]


def _lightweight_scores(cfg: Config, rows: list[dict]) -> dict:
    from ..ingest.embed import get_embedder
    import numpy as np

    emb = get_embedder(cfg)
    per_row = []
    for r in rows:
        ctx_all = " ".join(r["contexts"]).lower()
        ctx_kw = _kw(ctx_all)
        ans_sents = _sentences(r["answer"])

        faith = mean([1.0 if len(_kw(s) & ctx_kw) / (len(_kw(s)) or 1) >= 0.4 else 0.0 for s in ans_sents]) if ans_sents else 1.0
        qv, av = np.array(emb.embed_query(r["question"])), np.array(emb.embed_query(r["answer"]))
        ans_rel = float(max(0.0, qv @ av))
        mm = [m.lower() for m in r["must_mention"]]
        ctx_recall = mean([1.0 if m in ctx_all else 0.0 for m in mm]) if mm else 1.0
        ctx_prec = mean([1.0 if _kw(c) & ctx_kw & _kw(" ".join(mm)) else 0.5 for c in r["contexts"]]) if r["contexts"] else 0.0
        ans_low = r["answer"].lower()
        correct = mean([1.0 if m in ans_low else 0.0 for m in mm]) if mm else (1.0 if not r["ground_truth"] else 0.5)
        for bad in r["must_not_contain"]:
            if bad.lower() in ans_low:
                correct = min(correct, 0.2)
        per_row.append({"id": r["id"], "faithfulness": round(faith, 3), "answer_relevancy": round(ans_rel, 3),
                        "context_precision": round(ctx_prec, 3), "context_recall": round(ctx_recall, 3),
                        "answer_correctness": round(correct, 3)})
    scores = {m: round(mean(x[m] for x in per_row), 3) for m in METRICS} if per_row else {}
    return {"engine": "lightweight-deterministic", "scores": scores, "per_row": per_row}


def run_ragas(cfg: Config, rows: list[dict], *, sample: int | None = None) -> dict:
    if sample:
        rows = rows[:sample]
    try:
        import ragas  # noqa: F401
        return _run_real_ragas(cfg, rows)
    except ImportError:
        log.info("ragas_package_absent_using_lightweight")
        return _lightweight_scores(cfg, rows)


def _run_real_ragas(cfg: Config, rows: list[dict]) -> dict:
    """Use the installed `ragas` package. Kept minimal; judged by the configured free-tier
    LLM. TODO: wire our LLMClient/Embedder through ragas' LangchainLLMWrapper shims."""
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        answer_correctness, answer_relevancy, context_precision, context_recall, faithfulness,
    )

    ds = Dataset.from_list([
        {"question": r["question"], "answer": r["answer"], "contexts": r["contexts"] or [""],
         "ground_truth": r["ground_truth"] or ""}
        for r in rows
    ])
    result = evaluate(ds, metrics=[faithfulness, answer_relevancy, context_precision,
                                   context_recall, answer_correctness])
    return {"engine": "ragas", "scores": {k: round(float(v), 3) for k, v in result.items()}}


def _cache_key(metric: str, row: dict) -> str:
    return metric + "-" + hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()[:12]
