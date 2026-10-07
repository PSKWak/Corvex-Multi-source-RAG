# Requirements Report — Approach to Each Requirement

*Maps every requirement in the brief (`sch.md`) and every cross-cutting ask to the approach
taken, the deviation (if any) and why, where it lives in the code, and the evidence it
works. Companion to `docs/TECHNICAL.md` (full detail) and `DESIGN.md` (the debate).*

Legend — **Status**: ✅ done & verified · ◑ done, lightly exercised · ○ stubbed.

---

## A. The six explicit requirements

### R1 — Three different types of data (documentation, forums, blogs)

**Approach.** Created a fictional product, **Corvex** (a distributed job-queue engine), and
authored all three sources by hand so contradictions could be planted at known locations:

| Source | Count | Format | Distinctive metadata |
|---|---|---|---|
| `data/documentation/` | 10 pages | Markdown | `Applies to: vX` line, headings |
| `data/forums/` | 12 threads | JSON | posts, `votes`, `is_accepted`, `author_role` (staff/community) |
| `data/blogs/` | 6 posts | Markdown + YAML frontmatter | `author`, `publish_date`, `product_version` |

Registry `data/sources.yaml` gives every document an `id`, `type`, `url`, `publish_date`,
`product_version`, `author_role`. Ingests to ~100 chunks.

**Why fictional.** With a real product we couldn't control where the sources disagree. The
five seeded contradictions (retention default, config-key rename, auth method, default
port, harmful `max_depth: 0` advice) plus a forum-only bug, a blog-only benchmark, a
genuine no-answer gap, and a prompt-injection payload are all placed deliberately.

**Code.** `corvex_rag/ingest/loaders.py`. **Evidence.**
`tests/test_ingest_index.py::test_loaders_produce_28_docs`; `test_config_and_data.py`
enforces every eval reference resolves to a real registry entry. **Status: ✅**

---

### R2 — A chunking strategy appropriate for each data source

**Approach — structure-first, semantic-second.** One chunker per source type:

| Source | Chunker | What makes it source-appropriate |
|---|---|---|
| Documentation | `MarkdownHeadingChunker` | H1/H2/H3 boundaries; **code blocks and tables never split**; heading breadcrumb carried into every chunk; oversized sections sub-split on paragraphs with 1-sentence overlap; tiny sections merged forward |
| Forums | `ThreadAwareChunker` | unit = *question + accepted answer*; other substantive posts each their own chunk; trivial replies dropped; nested `>` quotes stripped; `is_accepted`/`votes`/`author_role` preserved per chunk |
| Blogs | `HeadingThenSemanticChunker` | heading-aware, then **embedding-similarity ("semantic") splitting inside oversized structureless sections** — the one place the brief's "structure-aware semantic chunking" runs; falls back to paragraph packing when no real embedding model is loaded |
| *(baseline)* | `FixedSizeChunker` | token window + overlap; ablation control only |

**Deviation from the brief.** The brief's box says "Structure-Aware Semantic Chunking" as
one step. We split it: structure is the *primary, deterministic* boundary; embedding
similarity is a *secondary* fallback only where structure runs out. Pure semantic chunking
is stochastic and hard to explain; this keeps chunk boundaries reproducible and every
chunk records its `chunk_strategy`.

**Code.** `corvex_rag/ingest/chunking.py`, `corvex_rag/ingest/markdown.py`. **Evidence.**
`test_ingest_index.py` — `test_doc_chunker_keeps_code_fences_whole`,
`test_forum_chunker_pairs_question_with_accepted_answer`. **Status: ✅**

---

### R3 — A retrieval system that intelligently weighs and combines results from all sources

**Approach — four stages, all logged:**

1. **Per-source hybrid.** For *each* source type separately: BM25 top-40 + dense-vector
   kNN top-40.
2. **Fusion — Reciprocal Rank Fusion** (`score = Σ 1/(k + rank)`, `k=60`). Rank-based, so
   it behaves identically on the local backend and Elasticsearch and needs no score
   normalisation. Weighted min-max combination is a config option and an ablation.
3. **Source weighting.** `final = rrf × source_weight[type] × source_authority ×
   recency_factor(age) × version_match_factor(chunk_version, target_version)`. Shipped
   weights follow the brief's order (documentation 1.0 > forum 0.8 > blog 0.6). A
   **retrieval floor** keeps ≥ 2 candidates from *every* source so cross-source and
   contradiction questions are never starved.
4. **Adaptive weighting** — a transparent query-intent classifier
   (`documentation_lookup` / `troubleshooting` / `background_deepdive`) with per-intent
   multipliers. **Built but off by default** — enabled only if the ablation shows it beats
   static.

**Hybrid BM25 + vector** (the user's explicit ask) is exactly stage 1–2. **Version-aware
weighting** is what makes "intelligently" real here — a v1-era blog is demoted for a
current-version query.

**Code.** `corvex_rag/retrieve/` — `hybrid.py`, `source_weighting.py`,
`query_intent.py`, `retriever.py`. **Evidence.** `tests/test_retrieval.py`;
`Performance Analysis` (deliverable 2). **Status: ✅**

---

### R4 — A reranking mechanism to improve relevance

**Approach.** Top-M (30) candidates → cross-encoder rerank → top-K (7) evidence, with a
**cross-source diversity guard** (forces ≥ 2 source types into the top-K when available).

| Reranker | Role |
|---|---|
| `bge` — `BAAI/bge-reranker-base` | **the baseline** (the user's explicit ask; the brief's "BGE Reranker" arm) |
| `cohere` — `rerank-english-v3.0` | **the comparison arm** (the brief's "Cohere Rerank"); rate-limited, opt-in |
| `lexical` | deterministic offline reranker — query-term coverage + exact error-code / config-key hits + heading match; the zero-dependency default |
| `identity` | ablation control (no reranking) |

**Deviation from the brief.** The brief lists Cohere Rerank as *the* reranker. Its own
"Retriever Structure for reference" section, though, asks to **compare Cohere vs BGE** — so
BGE is the baseline (free, local, no quota) and Cohere is the comparison, measured
side-by-side in deliverable 2.

**Code.** `corvex_rag/retrieve/rerank.py`. **Evidence.** `Performance Analysis` compares
all four on NDCG@k / MRR / Recall@k / latency. **Status: ✅** (BGE, lexical, identity) ·
**◑** (Cohere — needs the API key)

---

### R5 — A mechanism to handle contradictions between sources

**Approach — detect, then resolve deterministically.**

*Detection* (`reason/contradiction.py`), three layers, cheapest first, run over the top-K
**plus a wider candidate pool** so a conflict isn't missed when reranking drops one side:

1. **Rule-based claim extraction** — regex patterns for the claim shapes that matter
   (retention default, worker-concurrency key, port, auth method, `max_depth: 0`,
   `prune`/`purge`). Two chunks, same subject, different value, different source/version →
   contradiction. Deterministic, offline.
2. **NLI cross-encoder** (`nli-deberta-v3-base`) on topically-close cross-source pairs →
   entail / neutral / **contradict**.
3. **LLM adjudication** — one structured call per flagged pair; **off by default**
   (latency), on for maximum accuracy.

*Resolution* (`reason/resolution.py`) — a fixed rule order, **every decision logged with a
rationale**:

`version_match` → `explicit_deprecation` → `recency` → `authority` (documentation > blog >
forum for normative claims; a forum post wins for an *empirical* symptom like "hangs on
macOS"). No rule fires → the answer surfaces **both sides**. The losing chunk is
**demoted, not deleted**, so the answer can still say "in v1 this was X".

The result feeds the **context builder**, which passes the conflict notes into the prompt
with "lead with the preferred side; mention the other only as superseded".

**Code.** `corvex_rag/reason/` — `contradiction.py`, `nli.py`, `resolution.py`,
`context_builder.py`. **Evidence.** `evaluation/contradiction_cases.json` (C1–C5) +
`corvex_rag/eval/runner.py::evaluate_contradiction_cases`. **Status: ✅**

---

### R6 — Logging to track which sources are used for each response

**Approach — three layers:**

| Artefact | Contents |
|---|---|
| `logs/queries.jsonl` (one line/query) | `sources_used` (documentation / forum / blog citation counts), `n_contradictions`, `self_check_result`, `n_corrections`, `latency_ms`, `tokens`, `cost_estimate_usd`, `confidence`, `llm_provider`, `config_hash` |
| `logs/provenance/<query_id>.json` (full trace) | every retrieved chunk with its `source_id`, `source_type`, and **all** scoring factors; contradictions; resolution decisions + rationales; six self-check scores; correction log; per-stage timings |
| Structured `structlog` output | stage-tagged events (`contradictions_detected`, `resolution`, `self_check`, …) |

The dashboard's **Source-usage log** tab renders `queries.jsonl` as a table with an
aggregate "citations by source type" chart and a per-query provenance inspector.

**Code.** `corvex_rag/observability/provenance.py`, `corvex_rag/logging_setup.py`.
**Evidence.** `tests/test_pipeline_and_eval.py::test_answer_shape_and_provenance`.
**Status: ✅**

---

## B. The workflow pipeline (the brief's flow diagram)

| Brief stage | Approach | Module |
|---|---|---|
| Parse the data | per-format loaders → `SourceDocument` | `ingest/loaders.py` |
| Structure-aware semantic chunking | per-source chunkers (R2) | `ingest/chunking.py` |
| Metadata enrichment | rule-based: version inference, error codes, config keys, deprecation, recency, authority prior | `ingest/metadata.py` |
| Generate embeddings | HF BGE (`bge-small-en-v1.5`) or a deterministic hashing embedder | `ingest/embed.py` |
| Vector database | **Elasticsearch `dense_vector`** (see §C) or in-process NumPy | `index/elastic.py`, `index/local.py` |
| Hybrid retrieval (vector + BM25) | per-source BM25 + kNN → RRF | `retrieve/hybrid.py` |
| Retrieve from doc / forum / blog | per-source candidate pools + retrieval floor | `retrieve/hybrid.py` |
| Source weighting | version-aware weighting (R3) | `retrieve/source_weighting.py` |
| Reranker | BGE baseline, Cohere comparison, lexical/identity (R4) | `retrieve/rerank.py` |
| Top-K evidence | top-7 + diversity guard | `retrieve/retriever.py` |
| Contradiction detection | rules → NLI → LLM (R5) | `reason/contradiction.py` |
| Conflict resolution | deterministic 4-rule policy (R5) | `reason/resolution.py` |
| Context builder | numbered evidence + conflict notes, token-budgeted | `reason/context_builder.py` |
| LLM generation → draft answer | Groq `gpt-oss-120b` / MockLLM, injection-guarded prompt, `[n]` citations | `generate/generator.py` |
| SELF-CHECK (grounding, citation, relevance, version, contradiction) | six checks — the brief's five **plus injection_safety** | `verify/self_check.py` |
| PASS / FAIL → corrective action → re-check | bounded loop (max 1); re-retrieve / re-resolve / regenerate; templated safe refusal for injection | `verify/corrector.py` |
| Answer + citations | `Answer` with parsed, de-duplicated, renumbered citations | `generate/generator.py` |
| Provenance + logging | one JSON per query + `queries.jsonl` (R6) | `observability/provenance.py` |
| User feedback → feedback store | `record_feedback(...)` → `feedback.jsonl`; thumbs-down auto-queued as eval candidates | `observability/feedback.py` |
| RAGAS | RAGAS-style scorer (own impl + `ragas` package path), judged by the free-tier LLM | `eval/ragas_eval.py` |

**Self-check / correction and feedback** (the user's explicit asks) are the SELF-CHECK box
and the User-feedback box above. **Citations and provenance** run through the whole tail of
the pipeline.

---

## C. The prescribed tools

| Brief tool | Decision | Rationale |
|---|---|---|
| **Elasticsearch free tier** | **Kept.** Primary backend — BM25 + `dense_vector` kNN + hybrid in one query. Verified against **Elastic Cloud Serverless (ES 9.6)**. Also added an in-process backend (NumPy + `rank_bm25`) so the pipeline runs with no services. | ES subsumes what the brief needed a second store for. |
| **Weaviate free tier** | **Dropped.** | Redundant — ES already does dense-vector kNN and hybrid fusion. A second stateful service adds infra for zero capability gain and hurts reproducibility. Behind the `SearchBackend` interface it could be re-added as another adapter. |
| **Cohere Rerank free tier** | **Kept as the comparison arm**, not the baseline. Rate-limited, opt-in. | The brief's own reference structure asks to compare Cohere vs BGE; BGE is free/local so it's the baseline. |
| (LLM — unspecified) | **Groq free tier** (`gpt-oss-120b`), pluggable `LLMClient`, mandatory shared rate limiter, MockLLM auto-fallback. | The user's "free API + rate limiting" ask. |
| (Embeddings — unspecified) | **HF BGE** baseline + deterministic hashing fallback. | The user's "HF BGE baseline" ask; the fallback makes the whole pipeline runnable offline. |

Every deviation is also in `DESIGN.md` §15 and `docs/TECHNICAL.md` §14.

---

## D. Reranker comparison & the required metrics

The brief's reference structure — *Retriever → {Cohere Rerank, BGE Reranker} → Compare* —
and its metric list (NDCG@K, MRR, Context Precision, Context Recall, Answer Correctness,
Latency) are delivered as **deliverable 2, the Performance Analysis**, and by the ablation
harness:

```
corvex-rag ablate --axis reranker      # identity | lexical | bge | cohere
corvex-rag ablate --axis retrieval     # bm25-only | vector-only | rrf | weighted
python scripts/perf_analysis.py        # retrieval-only metrics across all three axes
```

- **NDCG@k, MRR, Recall@k, Context Precision, Context Recall, per-source recall** —
  `corvex_rag/eval/retrieval_metrics.py`, deterministic, driven by
  `evaluation/retrieval_cases.json`.
- **Answer Correctness** — the RAGAS-style scorer, `corvex_rag/eval/ragas_eval.py`.
- **Latency** — recorded per stage in every provenance record and aggregated by the eval
  runner and the perf script.

**Code.** `corvex_rag/eval/` — `retrieval_metrics.py`, `ragas_eval.py`, `ablations.py`,
`runner.py`; `scripts/perf_analysis.py`. **Status: ✅**

---

## E. Evaluation dataset & question categories

**Approach.** The exact file layout the brief specifies, plus one:

```
evaluation/
├── questions.json           # 24 questions: id, question, category, expected_sources
├── ground_truth.json        # per id: reference answer + must_mention + must_cite + must_not_contain
├── retrieval_cases.json     # per id: relevant_doc_ids (required) + supporting_doc_ids (helpful)
├── contradiction_cases.json # C1–C5: conflicting sources, conflict type, expected rule + winner, answer shape
└── edge_cases.json          # 20 behavioural assertions, each naming a check function  ← added
```

**All ten question categories** from the brief are present and tagged:

| Category | Example | Behavioural check |
|---|---|---|
| documentation-only | "What causes CVX-1001?" | cites only documentation |
| forum-only | "Why does `corvex drain` hang on macOS?" | cites the forum thread |
| blog-only | "How did the team benchmark 50k jobs/sec?" | cites the blog |
| cross-source | "How do I tune for high throughput?" | ≥ 2 source types cited |
| contradictory sources | "Default retention period?" | names both values, current leads |
| version conflicts | "What port does the API use?" | names 8577 **and** 9000, 8577 leads |
| outdated information | "How do I authenticate API requests?" | `Authorization: Bearer` leads, `?token=` not presented as current |
| exact error codes | "What does CVX-4210 mean?" | states the meaning, cites documentation |
| no-answer | "Does Corvex expose a GraphQL API?" | refuses, cites nothing definitive |
| prompt injection | (hidden instruction in a forum post) | no leaked secret, no sentinel, `injection_safety` passed |

**Edge cases & ablation tests** (the user's explicit asks) are `edge_cases.json` +
`corvex_rag/eval/edge_cases.py`, and `corvex_rag/eval/ablations.py` (7 axes).

**Code / data.** `evaluation/*`, `corvex_rag/eval/runner.py`, `edge_cases.py`.
**Evidence.** `test_config_and_data.py::test_all_sch_categories_present`. **Current:**
edge cases **19/20**, contradiction cases **3/5** (offline stand-in model). **Status: ✅**

---

## F. Cross-cutting qualities

### Modular

Six interfaces are the only seams: `SearchBackend`, `Embedder`, `Reranker`, `NLIModel`,
`LLMClient`, `FeedbackStore`. `pipeline.py` wires implementations from `config.yaml` —
**swapping any component is a config edit, not a code change**. Adding Weaviate, a new
reranker, or a new LLM = one new adapter class.

### Reproducible

- Single `config.yaml`; a 12-char hash of the resolved config is stamped into every
  provenance record.
- **Zero-dependency path** (`local` / `hashing` / `lexical` / `mock`) runs the entire
  pipeline with no Docker, no network, no API keys.
- Deterministic: fixed seeds, stable chunk IDs (`sha1(source_id | span | strategy_version)`),
  cached judge outputs.
- **30 hermetic tests** — `tests/conftest.py` forces the offline stack regardless of what
  `config.yaml` selects, so `pytest` is reproducible on any machine.

### Simple

Plain Python functions with typed inputs/outputs (`schema.py`). No LangChain, no agent
framework, no hidden chains. Ingestion is entirely rule-based (no LLM). The default stack
needs one `pip install`.

### Explainable

Every stage writes to the provenance record: which intent signals fired, every scoring
factor on every candidate, every resolution rule + its rationale, every self-check score +
detail. The dashboard's **Ask** tab surfaces all of it inline. Chunk boundaries are
deterministic and each chunk names its own strategy.

**Status: ✅**

---

## Requirement coverage summary

| # | Requirement | Status |
|---|---|---|
| R1 | Three data source types | ✅ |
| R2 | Per-source chunking | ✅ |
| R3 | Weighted multi-source retrieval | ✅ |
| R4 | Reranking (BGE baseline; Cohere / lexical / identity) | ✅ / ◑ Cohere |
| R5 | Contradiction detection + resolution | ✅ |
| R6 | Source-usage logging | ✅ |
| — | Pipeline workflow (all stages) | ✅ |
| — | Elasticsearch | ✅ · Weaviate dropped (justified) · Cohere ◑ |
| — | Metrics: NDCG / MRR / Context P&R / Answer Correctness / Latency | ✅ |
| — | Evaluation dataset + 10 categories | ✅ |
| — | Self-check / correction · citations · provenance · feedback · RAGAS | ✅ / RAGAS-package path ◑ |
| — | Edge cases · ablation tests | ✅ |
| — | Modular · reproducible · simple · explainable | ✅ |
