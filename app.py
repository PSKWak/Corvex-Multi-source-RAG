"""Minimal Streamlit UI for the Corvex multi-source RAG.

    .venv\\Scripts\\streamlit.exe run app.py      (or: python -m streamlit run app.py)

Four tabs:
  1. Ask            - question box -> answer, citations, self-check, evidence
  2. Retrieval perf - NDCG/MRR/recall from the eval report + reranker/retrieval ablations
  3. Source-usage   - logs/queries.jsonl: which sources every answer used
  4. Eval scores    - latest logs/eval report: RAGAS, edge cases, contradiction cases
"""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from corvex_rag.config import load_config

REPO = Path(__file__).resolve().parent
LOGS = REPO / "logs"

st.set_page_config(page_title="Corvex Support RAG", page_icon="", layout="wide")


# --------------------------------------------------------------------------- helpers
@st.cache_resource(show_spinner="Loading pipeline (embedder + reranker + backend)...")
def get_pipeline():
    from corvex_rag.pipeline import RAGPipeline
    return RAGPipeline(load_config(REPO / "config.yaml"))


@st.cache_data
def cfg_summary() -> dict:
    c = load_config(REPO / "config.yaml")
    return {
        "backend": c.section("index", "backend"),
        "embedder": c.section("embedding", "provider"),
        "reranker": c.section("reranker", "provider"),
        "llm": c.section("generation", "llm"),
        "source_weighting": c.section("source_weighting", "mode"),
        "nli": c.section("contradiction", "nli", "enabled"),
        "config_hash": c.hash(),
    }


def _latest(pattern: str) -> Path | None:
    files = glob.glob(str(LOGS / pattern))
    return Path(max(files, key=lambda f: Path(f).stat().st_mtime)) if files else None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------- header
c = cfg_summary()
st.title("Corvex Multi-Source RAG")
st.caption(
    f"backend **{c['backend']}** - embedder **{c['embedder']}** - reranker **{c['reranker']}** - "
    f"llm **{c['llm']}** - source-weighting **{c['source_weighting']}** - nli **{c['nli']}** - "
    f"config `{c['config_hash']}`"
)

tab_ask, tab_perf, tab_logs, tab_eval = st.tabs(
    ["Ask", "Retrieval performance", "Source-usage log", "Eval scores"]
)

# =========================================================================== 1. ASK
with tab_ask:
    q = st.text_input("Ask a question about Corvex", placeholder="How do I fix error CVX-4210?")
    col_a, col_b = st.columns([1, 4])
    go = col_a.button("Ask", type="primary", disabled=not q)
    top_k = col_b.slider("top-k evidence", 3, 12, 7)

    if go:
        try:
            pipe = get_pipeline()
        except Exception as e:  # noqa: BLE001
            st.error(f"Pipeline unavailable: {e}\n\nRun `python -m corvex_rag.cli ingest` first.")
            st.stop()
        with st.spinner("Retrieving -> detecting contradictions -> generating -> self-checking..."):
            t0 = time.perf_counter()
            ans = pipe.answer(q, top_k=top_k)
            wall = time.perf_counter() - t0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Confidence", f"{ans.confidence:.2f}")
        m2.metric("Self-check", "PASS" if ans.self_check.passed else "FAIL")
        m3.metric("Latency", f"{wall:.1f} s")
        m4.metric("Sources used", sum(ans.sources_used.values()))

        st.markdown("### Answer")
        st.markdown(ans.answer_md)

        if ans.sources_used:
            st.bar_chart(pd.Series(ans.sources_used, name="citations"), horizontal=True)

        with st.expander(f"Citations ({len(ans.citations)})", expanded=True):
            st.dataframe(pd.DataFrame([
                {"[n]": ci.n, "source": ci.source_type.value, "title": ci.title,
                 "version": ci.product_version, "url": ci.url}
                for ci in ans.citations
            ]), hide_index=True)

        with st.expander("Self-check breakdown"):
            st.dataframe(pd.DataFrame([
                {"check": r.name, "passed": r.passed, "score": round(r.score, 3), "detail": r.detail}
                for r in ans.self_check.results
            ]), hide_index=True)
            if ans.self_check.correction_log:
                st.write("**Corrections:**")
                for line in ans.self_check.correction_log:
                    st.text(f"  {line}")

        if ans.conflict_notes:
            with st.expander(f"Contradictions and resolution ({len(ans.conflict_notes)})", expanded=True):
                for n in ans.conflict_notes:
                    st.markdown(f"- {n}")

        # evidence detail from the provenance record
        if ans.provenance_path and Path(ans.provenance_path).exists():
            prov = json.loads(Path(ans.provenance_path).read_text(encoding="utf-8"))
            ev = prov.get("stages", {}).get("retrieval", {}).get("evidence", [])
            with st.expander(f"Evidence retrieved ({len(ev)}) - scores and weighting factors"):
                rows = []
                for e in ev:
                    comp = e.get("components", {})
                    rows.append({
                        "source_id": e["source_id"], "type": e["source_type"], "version": e.get("version"),
                        "final_score": e.get("score"),
                        "rerank": comp.get("rerank"), "source_weight": comp.get("source_weight"),
                        "recency": comp.get("recency"), "version_match": comp.get("version_match"),
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True)
            st.caption(f"query intent: `{prov['stages']['retrieval'].get('intent')}` - "
                       f"provenance: `{Path(ans.provenance_path).name}`")

# =========================================================================== 2. PERF
with tab_perf:
    st.subheader("Retrieval and reranking metrics")
    rep = _latest("eval/report_*.json")
    if rep:
        d = json.loads(rep.read_text(encoding="utf-8"))
        s = d["retrieval"]["summary"]
        st.caption(f"from `{rep.name}` - embedder `{d['embedder']}` - reranker `{d['reranker']}`")
        ndcg = {k.replace("ndcg@", "@"): v for k, v in s.items() if k.startswith("ndcg@")}
        rec = {k.replace("recall@", "@"): v for k, v in s.items() if k.startswith("recall@")}
        prec = {k.replace("context_precision@", "@"): v for k, v in s.items() if k.startswith("context_precision@")}
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("MRR", s.get("mrr"))
        cc2.metric("per-source recall", s.get("per_source_recall"))
        cc3.metric("mean retrieval latency (ms)", round(s.get("latency_ms", 0), 1))
        st.write("**NDCG / Recall / Context-precision @ k**")
        st.line_chart(pd.DataFrame({"NDCG": ndcg, "Recall": rec, "Context precision": prec}))
    else:
        st.info("No eval report yet. Run `corvex-rag eval` or the button on the Eval tab.")

    st.divider()
    st.subheader("Ablations - one component at a time")
    axis = st.selectbox("axis", ["reranker", "retrieval", "source_weighting", "contradiction", "self_check"])
    prev = _latest(f"eval/ablation_{axis}_*.json")
    if prev:
        st.caption(f"latest: `{prev.name}`")
        st.dataframe(pd.DataFrame(json.loads(prev.read_text(encoding="utf-8"))),
                     hide_index=True)
    if st.button(f"Run `{axis}` ablation now (slow - runs the eval set per variant)"):
        from corvex_rag.eval.ablations import render_matrix, run_ablation
        with st.spinner(f"Running {axis} ablation across the eval set..."):
            rows = run_ablation(load_config(REPO / "config.yaml"), axis)
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        num = [c for c in ("retr_ndcg@5", "retr_mrr", "edge_pass_rate", "contradiction_pass_rate")
               if rows and c in rows[0]]
        if num:
            st.bar_chart(pd.DataFrame(rows).set_index("variant")[num])

# =========================================================================== 3. LOGS
with tab_logs:
    st.subheader("Which sources each answer used")
    rows = load_jsonl(LOGS / "queries.jsonl")
    if not rows:
        st.info("No queries logged yet - ask something on the first tab.")
    else:
        df = pd.DataFrame(rows)
        for stype in ("documentation", "forum", "blog"):
            df[stype] = df["sources_used"].apply(lambda d: d.get(stype, 0) if isinstance(d, dict) else 0)
        st.write(f"**{len(df)} logged queries**")
        totals = df[["documentation", "forum", "blog"]].sum()
        st.bar_chart(totals.rename("total citations"), horizontal=True)

        show = df[["ts", "question", "documentation", "forum", "blog", "n_contradictions",
                   "self_check_result", "n_corrections", "latency_ms", "confidence", "llm_provider"]]
        show = show.sort_values("ts", ascending=False)
        st.dataframe(show, hide_index=True)

        st.download_button("Download queries.jsonl", (LOGS / "queries.jsonl").read_bytes(),
                           "queries.jsonl")
        pick = st.selectbox("Inspect full provenance for query", df["query_id"].tolist())
        pv = LOGS / "provenance" / f"{pick}.json"
        if pv.exists():
            st.json(json.loads(pv.read_text(encoding="utf-8")), expanded=False)

# =========================================================================== 4. EVAL
with tab_eval:
    st.subheader("Evaluation scores")
    rep = _latest("eval/report_*.json")
    cq1, cq2 = st.columns(2)
    if cq1.button("Run quick eval (sample 5, no RAGAS)"):
        from corvex_rag.eval.runner import run_all
        with st.spinner("Running quick eval..."):
            run_all(load_config(REPO / "config.yaml"), sample=5, skip_ragas=True)
        st.rerun()
    if cq2.button("Run full eval (all questions + RAGAS - slow)"):
        from corvex_rag.eval.runner import run_all
        with st.spinner("Running full eval + RAGAS..."):
            run_all(load_config(REPO / "config.yaml"))
        st.rerun()

    if not rep:
        st.info("No eval report yet - use a button above.")
    else:
        d = json.loads(rep.read_text(encoding="utf-8"))
        st.caption(f"`{rep.name}` - backend `{d['backend']}` - embedder `{d['embedder']}` - "
                   f"reranker `{d['reranker']}` - llm `{d['llm']}` - wall {d.get('wall_seconds')}s")

        e1, e2, e3 = st.columns(3)
        ec = d.get("edge_cases", {})
        cc = d.get("contradiction_cases", {})
        e1.metric("Edge cases passed", f"{ec.get('passed', '?')}/{ec.get('n', '?')}")
        e2.metric("Contradiction cases", f"{cc.get('passed', '?')}/{cc.get('n', '?')}")
        e3.metric("Retrieval NDCG@7", d["retrieval"]["summary"].get("ndcg@7"))

        ragas = d.get("ragas", {})
        scores = ragas.get("scores")
        if scores:
            st.write(f"**RAGAS-style scores** ({ragas.get('engine', 'n/a')})")
            st.bar_chart(pd.Series(scores, name="score"))
        else:
            st.warning(f"RAGAS not in this report ({ragas.get('skipped', 'not run')}).")

        with st.expander("Edge-case detail", expanded=True):
            st.dataframe(pd.DataFrame(ec.get("per_case", [])), hide_index=True)
        with st.expander("Contradiction-case detail"):
            st.dataframe(pd.DataFrame(cc.get("per_case", [])), hide_index=True)
        with st.expander("Retrieval metrics (full)"):
            st.json(d["retrieval"]["summary"])
