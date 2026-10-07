"""In-process backend: `rank_bm25` for lexical, numpy cosine for vector. Persists to
`paths.index_dir`. Zero services — the CI and reproducibility path."""
from __future__ import annotations

import pickle
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import Config
from ..schema import Chunk, SourceType
from .base import SearchBackend, SearchHit, meta_to_dict

_TOK = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOK.findall(text.lower())


class LocalBackend(SearchBackend):
    supports_native_hybrid = False

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.path = cfg.index_dir / "local_store.pkl"
        self._chunks: dict[str, Chunk] = {}
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None
        self._bm25: BM25Okapi | None = None
        self._tokenized: list[list[str]] = []
        if self.path.exists():
            self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        with self.path.open("rb") as fh:
            blob = pickle.load(fh)
        self._chunks = blob["chunks"]
        self._ids = blob["ids"]
        self._matrix = blob["matrix"]
        self._tokenized = blob["tokenized"]
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = {
            "chunks": self._chunks,
            "ids": self._ids,
            "matrix": self._matrix,
            "tokenized": self._tokenized,
        }
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(blob, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(self.path)

    # ------------------------------------------------------------------ interface
    def recreate(self) -> None:
        self._chunks, self._ids, self._tokenized = {}, [], []
        self._matrix, self._bm25 = None, None
        if self.path.exists():
            self.path.unlink()

    def index(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c
        self._ids = list(self._chunks)
        self._tokenized = [_tokenize(self._chunks[i].text) for i in self._ids]
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None
        mat = [self._chunks[i].embedding for i in self._ids]
        self._matrix = np.array(mat, dtype=np.float32) if mat and mat[0] is not None else None
        self._save()

    def _filtered_idx(self, source_type: SourceType | None) -> list[int]:
        if source_type is None:
            return list(range(len(self._ids)))
        return [
            n for n, cid in enumerate(self._ids)
            if self._chunks[cid].metadata.source_type is source_type
        ]

    def _hit(self, n: int, score: float, rank: int) -> SearchHit:
        cid = self._ids[n]
        c = self._chunks[cid]
        return SearchHit(chunk_id=cid, score=float(score), rank=rank, text=c.text, metadata=meta_to_dict(c))

    def bm25(self, query: str, k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        if self._bm25 is None or k <= 0:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        idx = self._filtered_idx(source_type)
        idx.sort(key=lambda n: scores[n], reverse=True)
        return [self._hit(n, scores[n], r + 1) for r, n in enumerate(idx[:k]) if scores[n] > 0]

    def knn(self, query_vector, k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        if self._matrix is None or k <= 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        sims = self._matrix @ q                      # embeddings are pre-normalised
        idx = self._filtered_idx(source_type)
        idx.sort(key=lambda n: sims[n], reverse=True)
        return [self._hit(n, sims[n], r + 1) for r, n in enumerate(idx[:k])]

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def count(self) -> dict[str, int]:
        out = {"total": len(self._chunks)}
        for st in SourceType:
            out[st.value] = sum(
                1 for c in self._chunks.values() if c.metadata.source_type is st
            )
        return out
