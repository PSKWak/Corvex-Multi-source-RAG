"""Search backends behind one interface. `local` = numpy + rank_bm25 (offline default);
`elastic` = Elasticsearch hybrid (BM25 + kNN + RRF)."""
from .base import SearchBackend, SearchHit, get_backend, meta_to_dict

__all__ = ["SearchBackend", "SearchHit", "get_backend", "meta_to_dict"]
