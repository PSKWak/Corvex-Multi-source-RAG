"""One-toggle-at-a-time ablations (DESIGN.md §11). Each variant is a config override on a
deep copy of the base config; variants on a REINDEX axis re-ingest first."""
from __future__ import annotations

import copy
import json
import time

import structlog

from ..config import Config

log = structlog.get_logger("ablations")

# axis -> [(label, override dict)]
ABLATIONS: dict[str, list[tuple[str, dict]]] = {
    "retrieval": [
        ("bm25_only",   {"retrieval": {"per_source_vector_k": 0}}),
        ("vector_only", {"retrieval": {"per_source_bm25_k": 0}}),
        ("rrf",         {"retrieval": {"fusion": "rrf"}}),
        ("weighted",    {"retrieval": {"fusion": "weighted"}}),
    ],
    "reranker": [
        ("none",    {"reranker": {"provider": "identity"}}),
        ("lexical", {"reranker": {"provider": "lexical"}}),
        ("bge",     {"reranker": {"provider": "bge"}}),
        ("cohere",  {"reranker": {"provider": "cohere"}}),
    ],
    "source_weighting": [
        ("off",      {"source_weighting": {"mode": "off"}}),
        ("static",   {"source_weighting": {"mode": "static"}}),
        ("adaptive", {"source_weighting": {"mode": "adaptive"}}),
    ],
    "contradiction": [
        ("off", {"contradiction": {"enabled": False}}),
        ("on",  {"contradiction": {"enabled": True}}),
    ],
    "self_check": [
        ("off", {"self_check": {"enabled": False}}),
        ("on",  {"self_check": {"enabled": True}}),
    ],
    "chunking": [
        ("fixed_size",      {"_chunking": "fixed_size"}),
        ("structure_aware", {"_chunking": "structure_aware"}),
    ],
    "embedding": [
        ("hashing",   {"embedding": {"provider": "hashing"}}),
        ("bge_small", {"embedding": {"provider": "bge", "bge": {"model": "BAAI/bge-small-en-v1.5"}}}),
        ("bge_base",  {"embedding": {"provider": "bge", "bge": {"model": "BAAI/bge-base-en-v1.5"}}}),
    ],
}
REINDEX_AXES = {"chunking", "embedding"}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def run_ablation(base_cfg: Config, axis: str) -> list[dict]:
    from ..eval.retrieval_metrics import evaluate_retrieval
    from ..eval.runner import evaluate_contradiction_cases, load_eval_data, run_edge_cases
    from ..ingest.pipeline import run as ingest_run
    from ..pipeline import RAGPipeline

    if axis not in ABLATIONS:
        raise ValueError(f"unknown axis {axis!r}; choose from {sorted(ABLATIONS)}")

    data = load_eval_data(base_cfg.evaluation_dir)
    ks = base_cfg.section("evaluation", "retrieval_k", default=[3, 5, 7, 10])
    rows: list[dict] = []

    for label, override in ABLATIONS[axis]:
        fixed_size = override.pop("_chunking", None) == "fixed_size"
        merged = deep_merge(base_cfg.raw, override)
        cfg = Config(raw=merged, path=base_cfg.path)
        t0 = time.perf_counter()
        try:
            if axis in REINDEX_AXES:
                ingest_run(cfg, fixed_size_chunking=fixed_size, recreate=True)
            pipe = RAGPipeline(cfg)
            retr = evaluate_retrieval(pipe, data["retrieval_cases"] or [], ks)
            edge = run_edge_cases(pipe, data["edge_cases"] or [])
            contra = evaluate_contradiction_cases(pipe, data["contradiction_cases"] or [])
            rows.append({
                "axis": axis, "variant": label,
                **{f"retr_{k}": v for k, v in retr["summary"].items()},
                "edge_pass_rate": round(edge["passed"] / max(edge["n"], 1), 3),
                "contradiction_pass_rate": round(contra["passed"] / max(contra["n"], 1), 3),
                "wall_s": round(time.perf_counter() - t0, 1),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"axis": axis, "variant": label, "error": str(exc)})
            log.warning("ablation_variant_failed", variant=label, detail=str(exc))

    if axis in REINDEX_AXES:                     # restore the base index
        ingest_run(base_cfg, recreate=True)
    return rows


def render_matrix(rows: list[dict]) -> tuple[str, str]:
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in ("axis",) and k not in keys:
                keys.append(k)
    csv = ",".join(keys) + "\n" + "\n".join(",".join(str(r.get(k, "")) for k in keys) for r in rows)
    md = "| " + " | ".join(keys) + " |\n| " + " | ".join("---" for _ in keys) + " |\n"
    md += "\n".join("| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in rows)
    return csv, md


def run_ablation_cli(cfg: Config, axis: str) -> int:
    rows = run_ablation(cfg, axis)
    csv, md = render_matrix(rows)
    out_dir = cfg.logs_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"ablation_{axis}_{stamp}.csv").write_text(csv, encoding="utf-8")
    (out_dir / f"ablation_{axis}_{stamp}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(md)
    print(f"\nsaved: {out_dir / f'ablation_{axis}_{stamp}.csv'}")
    return 0
