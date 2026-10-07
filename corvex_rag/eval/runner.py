"""Load evaluation/*.json and drive the sub-evaluators. Called by `corvex-rag eval`."""
from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from ..config import Config
from .edge_cases import run_edge_cases
from .retrieval_metrics import evaluate_retrieval

log = structlog.get_logger("eval")


def load_eval_data(eval_dir: Path) -> dict:
    out = {}
    for name in ("questions", "ground_truth", "retrieval_cases", "contradiction_cases", "edge_cases"):
        p = eval_dir / f"{name}.json"
        out[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


def evaluate_contradiction_cases(pipeline, cases: list[dict]) -> dict:
    per_case = []
    passed = 0
    for case in cases:
        ans = pipeline.answer(case["question"])
        notes = " ".join(ans.conflict_notes).lower()
        text = ans.answer_md.lower()
        detected = any(
            any(tok in notes for tok in _subject_tokens(case))
            for _ in [0]
        ) or len(ans.conflict_notes) > 0
        answer_ok = all(s.lower() in text for s in case.get("expected_answer_contains", []))
        must_not = [s for s in case.get("must_not_contain", []) if s.lower() in text]
        rule_ok = case.get("expected_resolution_rule", "") in notes or not case.get("expected_resolution_rule")
        ok = detected and answer_ok and not must_not
        passed += ok
        per_case.append({
            "id": case["id"], "passed": ok, "detected_conflict": detected,
            "answer_contains_ok": answer_ok, "resolution_rule_seen": rule_ok,
            "leaked_forbidden": must_not, "n_conflict_notes": len(ans.conflict_notes),
            "answer_preview": ans.answer_md[:200],
        })
    return {"n": len(cases), "passed": passed, "failed": len(cases) - passed, "per_case": per_case}


def _subject_tokens(case: dict) -> list[str]:
    toks = []
    for cs in case.get("conflicting_sources", []):
        toks += cs.get("claim", "").lower().split()[:4]
    return toks


def run_all(cfg: Config, *, sample: int | None = None, skip_ragas: bool = False) -> dict:
    from ..pipeline import RAGPipeline

    data = load_eval_data(cfg.evaluation_dir)
    pipe = RAGPipeline(cfg)
    ks = cfg.section("evaluation", "retrieval_k", default=[3, 5, 7, 10])

    r_cases = data["retrieval_cases"] or []
    e_cases = data["edge_cases"] or []
    c_cases = data["contradiction_cases"] or []
    if sample:
        r_cases, e_cases, c_cases = r_cases[:sample], e_cases[:sample], c_cases[:sample]

    t0 = time.perf_counter()
    report = {
        "config_hash": cfg.hash(),
        "backend": cfg.section("index", "backend"),
        "embedder": cfg.section("embedding", "provider"),
        "reranker": cfg.section("reranker", "provider"),
        "llm": cfg.section("generation", "llm"),
        "retrieval": evaluate_retrieval(pipe, r_cases, ks),
        "contradiction_cases": evaluate_contradiction_cases(pipe, c_cases),
        "edge_cases": run_edge_cases(pipe, e_cases),
    }

    if not skip_ragas:
        try:
            from .ragas_eval import build_ragas_dataset, run_ragas
            rows = build_ragas_dataset(pipe, data["questions"], data["ground_truth"])
            report["ragas"] = run_ragas(cfg, rows, sample=sample)
        except Exception as exc:  # noqa: BLE001
            log.warning("ragas_skipped", detail=str(exc))
            report["ragas"] = {"skipped": str(exc)}
    else:
        report["ragas"] = {"skipped": "--skip-ragas"}

    report["wall_seconds"] = round(time.perf_counter() - t0, 1)

    out_dir = cfg.logs_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"report_{stamp}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / f"report_{stamp}.md").write_text(_render_md(report), encoding="utf-8")
    report["_saved"] = str(out_dir / f"report_{stamp}.json")
    return report


def _render_md(r: dict) -> str:
    L = [f"# Eval report", "",
         f"- backend `{r['backend']}` · embedder `{r['embedder']}` · reranker `{r['reranker']}` · llm `{r['llm']}`",
         f"- config hash `{r['config_hash']}` · wall {r.get('wall_seconds')}s", "",
         "## Retrieval", ""]
    for k, v in r["retrieval"]["summary"].items():
        L.append(f"- {k}: {v}")
    L += ["", f"## Contradiction cases: {r['contradiction_cases']['passed']}/{r['contradiction_cases']['n']} passed",
          f"## Edge cases: {r['edge_cases']['passed']}/{r['edge_cases']['n']} passed", ""]
    for c in r["edge_cases"]["per_case"]:
        L.append(f"- [{'PASS' if c['passed'] else 'FAIL'}] {c['id']} ({c['category']}) — {c['detail']}")
    if "skipped" not in r.get("ragas", {}):
        L += ["", "## RAGAS", ""] + [f"- {k}: {v}" for k, v in r["ragas"].get("scores", {}).items()]
    return "\n".join(L)
