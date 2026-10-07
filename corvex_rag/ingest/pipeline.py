"""Orchestrates load → chunk → enrich → embed → index. Deterministic and idempotent."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from ..config import Config
from ..schema import Chunk, SourceType
from .chunking import get_chunker
from .embed import get_embedder
from .loaders import load_all
from .metadata import enrich


def chunk_id(source_id: str, char_span: tuple[int, int], strategy_version: int) -> str:
    key = f"{source_id}|{char_span[0]}:{char_span[1]}|v{strategy_version}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def build_chunks(cfg: Config, *, fixed_size_chunking: bool = False) -> list[Chunk]:
    embedder = get_embedder(cfg)
    strat_v = int(cfg.section("ingestion", "chunking", "strategy_version", default=1))
    chunks: list[Chunk] = []

    for doc in load_all(cfg):
        chunker = get_chunker(cfg, doc.source_type, fixed_size=fixed_size_chunking, embedder=embedder)
        for raw in chunker.split(doc):
            meta = enrich(cfg, doc, raw, chunker.name)
            cid = chunk_id(doc.source_id, meta.char_span, strat_v)
            chunks.append(Chunk(chunk_id=cid, text=raw.text, metadata=meta))

    # de-dup identical ids (can happen if a chunker emits the same span twice); keep first
    seen: set[str] = set()
    unique = []
    for c in chunks:
        if c.chunk_id in seen:
            continue
        seen.add(c.chunk_id)
        unique.append(c)

    vectors = embedder.embed_documents([c.text for c in unique])
    for c, v in zip(unique, vectors):
        c.embedding = v
    return unique


def run(cfg: Config, *, fixed_size_chunking: bool = False, recreate: bool = True) -> dict:
    from ..index.base import get_backend

    chunks = build_chunks(cfg, fixed_size_chunking=fixed_size_chunking)
    backend = get_backend(cfg)
    if recreate:
        backend.recreate()
    backend.index(chunks)

    per_source = Counter(c.metadata.source_type.value for c in chunks)
    embedder = get_embedder(cfg)
    summary = {
        "n_documents": len({c.metadata.source_id for c in chunks}),
        "n_chunks": len(chunks),
        "per_source": dict(per_source),
        "index_backend": cfg.section("index", "backend"),
        "embedder": embedder.name,
        "embedding_dim": embedder.dim,
        "chunk_strategy": "fixed_size" if fixed_size_chunking else "structure_aware",
        "config_hash": cfg.hash(),
    }
    manifest_dir = cfg.logs_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "ingest_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
