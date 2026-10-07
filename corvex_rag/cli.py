"""`corvex-rag <command>` — thin argparse wrapper over the pipeline and eval harness.

    corvex-rag ingest   [--config config.yaml] [--fixed-size-chunking] [--no-recreate]
    corvex-rag ask      "question"  [--top-k 7] [--json] [--show-provenance]
    corvex-rag eval     [--sample N] [--skip-ragas]
    corvex-rag ablate   --axis retrieval|reranker|source_weighting|contradiction|self_check|chunking|embedding
    corvex-rag feedback  <query_id> up|down [--note "..."]
    corvex-rag doctor    # print resolved config, backend reachability, model cache status
"""
from __future__ import annotations

import argparse
import json

from .config import load_config
from .logging_setup import configure


def _cmd_ingest(args) -> int:
    from .ingest.pipeline import run

    cfg = load_config(args.config)
    configure(cfg.section("logging", "level", default="INFO"), jsonl=False)
    summary = run(cfg, fixed_size_chunking=args.fixed_size_chunking, recreate=not args.no_recreate)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_doctor(args) -> int:
    import os
    import shutil

    cfg = load_config(args.config)
    print(f"config              : {cfg.path}  (hash {cfg.hash()})")
    print(f"index.backend       : {cfg.section('index', 'backend')}")
    print(f"embedding.provider  : {cfg.section('embedding', 'provider')}")
    print(f"reranker.provider   : {cfg.section('reranker', 'provider')}")
    print(f"generation.llm      : {cfg.section('generation', 'llm')}")
    print(f"source_weighting    : {cfg.section('source_weighting', 'mode')}")
    print(f"contradiction.nli   : {cfg.section('contradiction', 'nli', 'enabled')}")

    idx = cfg.index_dir / "local_store.pkl"
    print(f"local index         : {'present' if idx.exists() else 'absent'} ({idx})")
    man = cfg.logs_dir / "ingest_manifest.json"
    if man.exists():
        print(f"last ingest         : {json.loads(man.read_text())}")

    for name, env in [
        ("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY"),
        ("cohere", "COHERE_API_KEY"), ("elastic", "ELASTIC_API_KEY"),
    ]:
        print(f"env {env:16}: {'set' if os.environ.get(env) else 'unset'}")

    host = os.environ.get("ELASTIC_HOST") or (cfg.section("index", "elastic", "hosts") or ["?"])[0]
    print(f"ELASTIC_HOST        : {host}")
    if cfg.section("index", "backend") == "elastic":
        try:
            from .index.elastic import ElasticBackend
            be = ElasticBackend(cfg)
            print(f"elasticsearch       : connected · docs = {be.count()}")
        except Exception as e:  # noqa: BLE001
            print(f"elasticsearch       : UNREACHABLE ({type(e).__name__}: {e})")

    print(f"docker              : {'found' if shutil.which('docker') else 'not found'}")
    return 0


def _cmd_ask(args) -> int:
    from .pipeline import RAGPipeline

    cfg = load_config(args.config)
    configure(cfg.section("logging", "level", default="INFO"), jsonl=False)
    ans = RAGPipeline(cfg).answer(args.question, top_k=args.top_k)
    if args.json:
        from dataclasses import asdict
        print(json.dumps(asdict(ans), indent=2, default=str))
        return 0
    print(ans.answer_md)
    print("\nCitations:")
    for c in ans.citations:
        print(f"  [{c.n}] {c.source_type.value} · {c.title} · {c.url}")
    print(f"\nsources_used={ans.sources_used}  confidence={ans.confidence:.2f}  "
          f"self_check={'PASS' if ans.self_check.passed else 'FAIL'}  latency={ans.latency_ms:.0f}ms")
    if args.show_provenance and ans.provenance_path:
        print(f"provenance: {ans.provenance_path}")
    return 0


def _cmd_eval(args) -> int:
    from .eval.runner import run_all

    cfg = load_config(args.config)
    configure(cfg.section("logging", "level", default="INFO"), jsonl=False)
    report = run_all(cfg, sample=args.sample, skip_ragas=args.skip_ragas)
    print(json.dumps(report, indent=2, default=str))
    return 0


def _cmd_ablate(args) -> int:
    from .eval.ablations import run_ablation_cli

    cfg = load_config(args.config)
    configure(cfg.section("logging", "level", default="INFO"), jsonl=False)
    return run_ablation_cli(cfg, args.axis)


def _cmd_feedback(args) -> int:
    from .pipeline import RAGPipeline

    cfg = load_config(args.config)
    RAGPipeline(cfg).record_feedback(args.query_id, args.rating, note=args.note)
    print(f"recorded {args.rating} for {args.query_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corvex-rag")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest"); p.add_argument("--fixed-size-chunking", action="store_true")
    p.add_argument("--no-recreate", action="store_true"); p.set_defaults(fn=_cmd_ingest)

    p = sub.add_parser("ask"); p.add_argument("question"); p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--json", action="store_true"); p.add_argument("--show-provenance", action="store_true")
    p.set_defaults(fn=_cmd_ask)

    p = sub.add_parser("eval"); p.add_argument("--sample", type=int, default=None)
    p.add_argument("--skip-ragas", action="store_true"); p.set_defaults(fn=_cmd_eval)

    p = sub.add_parser("ablate"); p.add_argument("--axis", required=True); p.set_defaults(fn=_cmd_ablate)

    p = sub.add_parser("feedback"); p.add_argument("query_id"); p.add_argument("rating", choices=["up", "down"])
    p.add_argument("--note", default=""); p.set_defaults(fn=_cmd_feedback)

    p = sub.add_parser("doctor"); p.set_defaults(fn=_cmd_doctor)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
