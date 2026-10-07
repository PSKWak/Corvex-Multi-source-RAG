"""Embedders behind one interface. HashingEmbedder is fully implemented (the offline /
repro default); BGEEmbedder is stubbed."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import Config

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")


class Embedder(Protocol):
    dim: int
    name: str
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic bag-of-hashed-tokens vector, L2-normalised. No model download, no
    network. NOT competitive on quality — it exists so the whole pipeline + the tests run
    anywhere, reproducibly. Same vector for documents and queries."""
    name = "hashing"

    def __init__(self, cfg: Config) -> None:
        self.dim = int(cfg.section("embedding", "hashing", "dim", default=512))

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            v[idx] += sign
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class BGEEmbedder:
    """`BAAI/bge-small-en-v1.5` (or bge-base) via sentence-transformers. Applies the
    query-side instruction prefix; normalises embeddings. Model cached under
    `paths.model_cache_dir`."""
    name = "bge"

    def __init__(self, cfg: Config) -> None:
        from sentence_transformers import SentenceTransformer  # optional dep

        self.cfg = cfg
        self._bge = cfg.section("embedding", "bge")
        self._instr = self._bge.get("query_instruction", "")
        self._normalize = bool(self._bge.get("normalize", True))
        self._batch = int(self._bge.get("batch_size", 32))
        self._model = SentenceTransformer(self._bge["model"], cache_folder=str(cfg.model_cache_dir))
        get_dim = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = get_dim()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(
            texts, batch_size=self._batch, normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        vec = self._model.encode(
            self._instr + text, normalize_embeddings=self._normalize, show_progress_bar=False,
        )
        return vec.tolist()


def get_embedder(cfg: Config) -> Embedder:
    provider = cfg.section("embedding", "provider")
    if provider == "hashing":
        return HashingEmbedder(cfg)
    if provider == "bge":
        return BGEEmbedder(cfg)
    raise ValueError(f"unknown embedding provider {provider!r}")


def embedding_dim(cfg: Config) -> int:
    """The vector width without loading a model (needed for the ES `dense_vector` mapping)."""
    provider = cfg.section("embedding", "provider")
    if provider == "hashing":
        return int(cfg.section("embedding", "hashing", "dim", default=512))
    model = cfg.section("embedding", "bge", "model", default="").lower()
    if "small" in model:
        return 384
    if "large" in model or "m3" in model:
        return 1024
    return 768
