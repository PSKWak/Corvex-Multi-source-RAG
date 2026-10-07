"""Retrieval + reranking performance analysis.

Runs retrieval-only metrics (no generation, no LLM) over evaluation/retrieval_cases.json
for every variant of three axes:

    fusion            : bm25_only | vector_only | rrf | weighted
    reranker          : identity  | lexical     | bge | cohere
    source_weighting  : off       | static      | adaptive

Metrics per variant: NDCG@{3,5,7,10}, MRR, Recall@{k}, Context Precision@{k},
per-source recall, mean retrieval latency. Writes:
    logs/perf/retrieval_reranking_<ts>.json
    logs/perf/retrieval_reranking_<ts>.csv

    python -m scripts.perf_analysis            # uses current config.yaml backend/embedder
    python -m scripts.perf_analysis --local    # force local backend + hashing (fast, offline)
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from corvex_rag.config import REPO_ROOT, Config, load_config
from corvex_rag.eval.retrieval_metrics import evaluate_retrieval
from corvex_rag.index.base import get_backend
from corvex_rag.ingest.embed import get_embedder
from corvex_rag.retrieve.rerank import get_reranker
from corvex_rag.retrieve.retriever import Retriever

AXES: dict[str, list[tuple[str, dict]]] = {
    "fusion": [
        ("bm25_only",   {"retrieval": {"per_source_vector_k": 0, "fusion": "rrf"}}),
        ("vector_only", {"retrieval": {"per_source_bm25_k": 0, "fusion": "rrf"}}),
        ("rrf",         {"retrieval": {"fusion": "rrf"}}),
        ("weighted",    {"retrieval": {"fusion": "weighted"}}),
    ],
    "reranker": [
        ("identity", {"reranker": {"provider": "identity"}}),
        ("lexical",  {"reranker": {"provider": "lexical"}}),
        ("bge",      {"reranker": {"provider": "bge"}}),
        ("cohere",   {"reranker": {"provider": "cohere"}}),
    ],
    "source_weighting": [
        ("off",      {"source_weighting": {"mode": "off"}}),
        ("static",   {"source_weighting": {"mode": "static"}}),
        ("adaptive", {"source_weighting": {"mode": "adaptive"}}),
    ],
}


def _merge(base: dict, ov: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in ov.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class _RetrieveOnly:
    """Minimal stand-in for RAGPipeline exposing just retrieve_only()."""

    def __init__(self, cfg: Config, backend, embedder):
        limiter = None
        try:
            from corvex_rag.rate_limit import RateLimiter
            limiter = RateLimiter.for_provider(cfg, "cohere")
        except Exception:  # noqa: BLE001
            pass
        self._r = Retriever(cfg, backend, embedder, get_reranker(cfg, limiter))
        self._k = int(cfg.section("reranker", "top_k_out", default=7))

    def retrieve_only(self, q, *, top_k=None):
        return self._r.retrieve(q, top_k=top_k or self._k)


def run(force_local: bool) -> dict:
    base = load_config(REPO_ROOT / "config.yaml")
    raw = copy.deepcopy(base.raw)
    if force_local:
        raw = _merge(raw, {"index": {"backend": "local"}, "embedding": {"provider": "hashing"}})

    cases = json.loads((REPO_ROOT / "evaluation" / "retrieval_cases.json").read_text(encoding="utf-8"))
    ks = raw.get("evaluation", {}).get("retrieval_k", [3, 5, 7, 10])

    # one backend + embedder shared across variants that don't touch them
    shared_cfg = Config(raw=raw, path=base.path)
    backend = get_backend(shared_cfg)
    embedder = get_embedder(shared_cfg)
    if backend.count().get("total", 0) == 0:
        raise SystemExit("index is empty - run `corvex-rag ingest` for this backend/embedder first")

    results: dict[str, list[dict]] = {}
    for axis, variants in AXES.items():
        rows = []
        for label, ov in variants:
            cfg = Config(raw=_merge(raw, ov), path=base.path)
            t0 = time.perf_counter()
            try:
                pipe = _RetrieveOnly(cfg, backend, embedder)
                rep = evaluate_retrieval(pipe, cases, ks)
                s = rep["summary"]
                rows.append({
                    "axis": axis, "variant": label,
                    **{k: s[k] for k in s if k.startswith(("ndcg@", "recall@", "context_precision@"))},
                    "mrr": s.get("mrr"), "per_source_recall": s.get("per_source_recall"),
                    "mean_latency_ms": round(s.get("latency_ms", 0), 1),
                    "wall_s": round(time.perf_counter() - t0, 1),
                })
                print(f"  [{axis}/{label}] ndcg@7={s.get('ndcg@7')} mrr={s.get('mrr')} "
                      f"recall@7={s.get('recall@7')} ({rows[-1]['wall_s']}s)")
            except Exception as exc:  # noqa: BLE001
                rows.append({"axis": axis, "variant": label, "error": str(exc)})
                print(f"  [{axis}/{label}] ERROR: {exc}")
        results[axis] = rows

    out_dir = REPO_ROOT / "logs" / "perf"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    meta = {
        "backend": raw["index"]["backend"], "embedder": raw["embedding"]["provider"],
        "reranker_default": base.section("reranker", "provider"),
        "n_retrieval_cases": len(cases), "ks": ks, "config_hash": shared_cfg.hash(),
    }
    (out_dir / f"retrieval_reranking_{stamp}.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    all_rows = [r for rows in results.values() for r in rows]
    keys = sorted({k for r in all_rows for k in r})
    with (out_dir / f"retrieval_reranking_{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nsaved logs/perf/retrieval_reranking_{stamp}.json + .csv")
    return {"meta": meta, "results": results}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="force local backend + hashing embedder")
    a = ap.parse_args()
    run(a.local)
