"""`SearchBackend` interface — the single seam between retrieval and storage.

Both backends must return results the same way so the retriever and every ablation are
backend-agnostic. RRF fusion happens either natively (ES >= 8.15) or in the retriever
(local, or ES with use_native_rrf=false) — the backend just needs to expose ranked BM25
and ranked kNN lists, plus an optional one-shot hybrid.
"""
from __future__ import annotations

import abc
import dataclasses
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..schema import Chunk, SourceType


def meta_to_dict(chunk: Chunk) -> dict[str, Any]:
    """Flat, JSON-friendly view of a chunk's metadata for a SearchHit / ES document."""
    d = dataclasses.asdict(chunk.metadata)
    d["source_type"] = chunk.metadata.source_type.value
    d["version_confidence"] = chunk.metadata.version_confidence.value
    d["published_at"] = chunk.metadata.published_at.isoformat() if chunk.metadata.published_at else None
    d["updated_at"] = chunk.metadata.updated_at.isoformat() if chunk.metadata.updated_at else None
    d["char_span"] = list(chunk.metadata.char_span)
    d["chunk_id"] = chunk.chunk_id
    return d


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    score: float
    rank: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchBackend(abc.ABC):
    supports_native_hybrid: bool = False

    @abc.abstractmethod
    def recreate(self) -> None:
        """Drop and re-create the index / clear local stores."""

    @abc.abstractmethod
    def index(self, chunks: list[Chunk]) -> None:
        """Add chunks (text + embedding + metadata). Idempotent on chunk_id."""

    @abc.abstractmethod
    def bm25(self, query: str, k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        ...

    @abc.abstractmethod
    def knn(self, query_vector: list[float], k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        ...

    def hybrid(
        self,
        query: str,
        query_vector: list[float],
        k: int,
        source_type: SourceType | None = None,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        """One-shot hybrid. Default: not implemented — the retriever fuses bm25()+knn().
        ES overrides this with the native `rrf` retriever."""
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, chunk_id: str) -> Chunk | None:
        ...

    @abc.abstractmethod
    def count(self) -> dict[str, int]:
        """{'total': N, 'documentation': .., 'forum': .., 'blog': ..}"""


def get_backend(cfg: Config) -> SearchBackend:
    backend = cfg.section("index", "backend")
    if backend == "local":
        from .local import LocalBackend
        return LocalBackend(cfg)
    if backend == "elastic":
        from .elastic import ElasticBackend
        return ElasticBackend(cfg)
    raise ValueError(f"unknown index.backend {backend!r}")
