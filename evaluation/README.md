# Evaluation datasets

All questions target the fictional product **Corvex** described in `../data/`.

| File | Purpose | Consumed by |
|---|---|---|
| `questions.json` | The question set: `id`, `question`, `category`, `expected_sources` | everything |
| `ground_truth.json` | Per-id reference answer + `must_mention` facts + `must_cite_sources` + `must_not_contain` | RAGAS `answer_correctness`, answer assertions |
| `retrieval_cases.json` | Per-id `relevant_doc_ids` (must retrieve) + `supporting_doc_ids` (helpful) + `expected_sources` | NDCG@k, MRR, Recall@k, context precision/recall |
| `contradiction_cases.json` | The 5 seeded conflicts (C1–C5): conflicting sources, expected conflict type, expected resolution rule + winner, expected answer shape | `corvex_rag.eval.runner.evaluate_contradiction_cases` |
| `edge_cases.json` | Behavioural assertions per category, each naming a `check` in `corvex_rag/eval/edge_cases.py` | edge-case suite / CI gate |

`relevant_doc_ids` / `must_cite_sources` use the source ids from `../data/sources.yaml`
(e.g. `doc-error-codes`, `forum-1004`, `blog-2024-09-backpressure`). The retrieval
harness matches on `chunk_id` when a case lists chunk ids, otherwise on the parent
`source_id` (doc-level relevance).

## Categories covered (from `sch.md`)

documentation-only · forum-only · blog-only · cross-source · contradictory sources ·
version conflicts · outdated information · exact error codes · no-answer · prompt injection

## Growing the set

Thumbs-down feedback (`logs/feedback.jsonl`) is auto-appended to
`logs/eval_candidates.jsonl`. Review those and promote the good ones into
`questions.json` + `ground_truth.json`.
