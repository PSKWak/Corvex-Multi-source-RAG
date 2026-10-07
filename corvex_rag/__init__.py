"""Corvex Multi-Source RAG for technical support.

Pipeline stages (see DESIGN.md):
    ingest → index → retrieve (hybrid) → source-weight → rerank → contradiction
    → resolution → context-build → generate → self-check/correct → answer + provenance

Everything is wired from `config.yaml`. The default config runs fully offline
(backend=local, embedder=hashing, llm=mock).
"""
__version__ = "0.1.0"
