# Corvex Multi-Source RAG for Technical Support

A production-grade Retrieval-Augmented Generation system that answers customer support
questions about a fictional software product (**Corvex**, a distributed job queue) by
retrieving from three distinct knowledge sources: **product documentation**, **customer
forums**, and **technical blogs**.

> **Status: implemented and tested on the offline path.** `DESIGN.md` is the architecture
> and the trade-off debate. The corpus (`data/`), evaluation datasets (`evaluation/`), and
> the full pipeline (`corvex_rag/`) are implemented. 29 tests pass. The zero-dependency
> stack (hashing embedder + lexical reranker + MockLLM) runs the whole pipeline —
> retrieval → contradiction detection → resolution → generation → self-check/correction →
> provenance — with no Docker, network, or API keys. The BGE embedder + BGE reranker + NLI
> paths are implemented and verified (`pip install -r requirements-optional.txt`). Groq
> generation needs a free `GROQ_API_KEY`; without one the pipeline auto-falls back to
> MockLLM. Elasticsearch backend and the `ragas` package path are wired but exercised less.

## Read this first

| Document | What it is |
|---|---|
| **[DESIGN.md](DESIGN.md)** | The architecture, and every trade-off — including where and why it diverges from the brief (`sch.md`). **Start here.** |
| [docs/TECHNICAL.md](docs/TECHNICAL.md) | Full technical writeup — every pipeline stage explained. Also published as an artifact. |
| [docs/STAKEHOLDERS.md](docs/STAKEHOLDERS.md) | Plain-language overview for non-technical audiences. Also published as an artifact. |
| [evaluation/README.md](evaluation/README.md) | The evaluation datasets and how they map to the pipeline |
| `sch.md` | The original task brief |

## What's decided (see DESIGN.md for the reasoning)

- **Elasticsearch** for hybrid BM25 + vector + RRF retrieval (primary); **in-process
  `numpy` + `rank_bm25`** as a zero-infra fallback. **Weaviate dropped** — Elasticsearch
  already does everything the brief needed a second store for.
- **Hugging Face BGE** embeddings + reranker as the baseline (local, free, deterministic);
  **Cohere Rerank** kept as an opt-in comparison. A **deterministic hashing embedder** lets
  the whole pipeline run with no downloads.
- **Free-tier LLM, pluggable, rate-limited**: **Groq** (`llama-3.3-70b-versatile`) for
  generation, self-check, and the RAGAS judge — one provider, one key. **MockLLM** is the
  automatic offline fallback; Gemini·Anthropic·OpenAI are opt-in. One shared `RateLimiter`
  (per-provider RPM + persisted daily quota + backoff) guards every external call.
- **Source weighting**: ship **static** (documentation > forums > blogs); adaptive is built
  but stays off until the ablation proves it helps.
- **Source-specific chunking** (structure-first, semantic-second), **query-adaptive source
  weighting**, **NLI+LLM contradiction detection** with a **deterministic resolution
  policy**, a **bounded self-check/correction loop**, full **citations + provenance +
  JSONL logging + feedback store**, and **RAGAS + retrieval metrics + one-toggle ablations
  + a per-category edge-case suite**.

## The zero-dependency path

The default `config.yaml` (`backend: local`, `embedding.provider: hashing`,
`generation.llm: mock`) runs the entire pipeline with **no Docker, no network, no API
keys** — this is the CI path and how the tests run.

```bash
pip install -r requirements.txt
python -m corvex_rag.cli ingest
python -m corvex_rag.cli ask "What is the default retention period for completed jobs?"
python -m corvex_rag.cli eval --skip-ragas
```

## The full path (verified: Elastic Cloud Serverless + BGE + Groq)

```bash
pip install -r requirements-optional.txt          # sentence-transformers, elasticsearch, groq, ragas
cp .env.example .env                               # fill in the keys below
```

`.env`:
```
GROQ_API_KEY=...            # console.groq.com
ELASTIC_API_KEY=...         # Kibana > Management > API keys > "Encoded" value
ELASTIC_HOST=https://<project>.es.<region>.<cloud>.elastic.cloud   # Kibana URL with .kb. -> .es.
```

`config.yaml` (the shipped values are already this — `index.backend: elastic`,
`embedding.provider: bge`, `reranker.provider: bge`, `generation.llm: groq`):

```bash
python -m corvex_rag.cli doctor      # confirms ES connection + which keys are set
python -m corvex_rag.cli ingest      # creates the corvex_rag_chunks index, 100 chunks
python -m corvex_rag.cli ask "How do I fix CVX-4210?" --show-provenance
python -m corvex_rag.cli eval
python -m corvex_rag.cli ablate --axis reranker
```

**Groq model note:** the default `generation.groq.model` is `openai/gpt-oss-120b`. Groq's
Llama chat models are not on every account — run this to see yours and pick any chat model:

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); from groq import Groq; [print(m.id) for m in Groq().models.list().data]"
```

**Latency:** ~40–90 s/query on CPU (BGE reranker + NLI model + Groq round-trips). Drop
`reranker.provider` to `lexical` and `contradiction.nli.enabled` to `false` for ~5 s/query.

## Fully offline (no keys, no services)

Set in `config.yaml`: `index.backend: local`, `embedding.provider: hashing`,
`reranker.provider: lexical`, `generation.llm: mock`. Then `ingest` + `ask` + `eval` run
with nothing external. This is also what `pytest` uses.

Windows without `make`: use `python tasks.py <ingest|ask|eval|ablate|test|doctor>` or
`scripts\bootstrap.ps1`.

## Layout

```
data/                three sources for the fictional product Corvex + sources.yaml registry
evaluation/          questions / ground_truth / retrieval_cases / contradiction_cases / edge_cases
corvex_rag/
  schema.py          typed contracts between every stage
  config.py          config load + validate + hash
  rate_limit.py      shared per-provider token-bucket + persisted daily quota + backoff
  textutil.py        offline token counting / paragraph & sentence splitting
  ingest/            loaders, markdown parser, per-source chunkers, enrichment, embed, pipeline
  index/             SearchBackend: local (numpy cosine + rank_bm25) | elastic (hybrid+RRF, wired)
  retrieve/          query intent -> hybrid RRF fusion -> source weighting -> rerank (lexical/bge/cohere)
  reason/            NLI + rule-based contradiction detection -> deterministic resolution -> context builder
  generate/          LLMClient adapters (Groq + MockLLM impl'd; Gemini/Anthropic/OpenAI stubs) -> generator
  verify/            self-check (6 checks) -> bounded corrector loop
  observability/     provenance JSON per query + queries.jsonl + feedback store
  eval/              retrieval metrics + RAGAS-style scorer + one-toggle ablations + edge-case suite
  pipeline.py        RAGPipeline.answer() — end-to-end orchestration
  cli.py             corvex-rag ingest | ask | eval | ablate | feedback | doctor
tests/               29 tests: rate limiter, chunking, retrieval, pipeline, eval, dataset consistency
```
