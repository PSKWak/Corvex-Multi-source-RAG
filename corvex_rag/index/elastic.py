"""Elasticsearch backend — BM25 + `dense_vector` kNN, Python-side RRF by default (works on
ES 8.x and Elastic Cloud Serverless), with an optional native `rrf` retriever path.

Connection (first match wins):
  * ELASTIC_HOST env var, else `index.elastic.hosts` from config
  * ELASTIC_API_KEY env var (the "Encoded" API-key value)  -> api_key auth
  * else ELASTIC_USER / ELASTIC_PASSWORD                    -> basic auth

One index, `{index_prefix}_chunks`. `_id == chunk_id` so re-indexing is idempotent.
"""
from __future__ import annotations

import datetime as dt
import os

import structlog

from ..config import Config
from ..ingest.embed import embedding_dim
from ..schema import Chunk, ChunkMetadata, SourceType, VersionConfidence
from .base import SearchBackend, SearchHit, meta_to_dict

log = structlog.get_logger("elastic")

_MAPPING_PROPS = {
    "chunk_id": {"type": "keyword"},
    "text": {"type": "text", "analyzer": "english"},
    "source_type": {"type": "keyword"},
    "source_id": {"type": "keyword"},
    "title": {"type": "keyword"},
    "url": {"type": "keyword"},
    "heading_path": {"type": "keyword"},
    "chunk_strategy": {"type": "keyword"},
    "strategy_version": {"type": "integer"},
    "char_span": {"type": "integer"},
    "token_count": {"type": "integer"},
    "product_version": {"type": "keyword"},
    "version_confidence": {"type": "keyword"},
    "published_at": {"type": "date", "null_value": "1970-01-01"},
    "updated_at": {"type": "date", "null_value": "1970-01-01"},
    "has_code": {"type": "boolean"},
    "has_error_code": {"type": "boolean"},
    "error_codes": {"type": "keyword"},
    "config_keys": {"type": "keyword"},
    "is_deprecated": {"type": "boolean"},
    "is_accepted_answer": {"type": "boolean"},
    "votes": {"type": "integer"},
    "author_role": {"type": "keyword"},
    "source_authority": {"type": "float"},
}


class ElasticBackend(SearchBackend):
    supports_native_hybrid = True

    def __init__(self, cfg: Config) -> None:
        from elasticsearch import Elasticsearch  # optional dep

        self.cfg = cfg
        ec = cfg.section("index", "elastic")
        self.index_name = f"{ec['index_prefix']}_chunks"
        self.use_native_rrf = bool(ec.get("use_native_rrf", False))
        self.num_candidates = int(ec.get("knn_num_candidates", 100))
        self.dim = embedding_dim(cfg)

        hosts = os.environ.get("ELASTIC_HOST") or ec.get("hosts") or ["http://localhost:9200"]
        if isinstance(hosts, str):
            hosts = [hosts]
        api_key = os.environ.get(ec.get("api_key_env", "ELASTIC_API_KEY"), "")
        kwargs: dict = {"hosts": hosts, "request_timeout": 30}
        if api_key:
            kwargs["api_key"] = api_key
        elif os.environ.get("ELASTIC_USER") and os.environ.get("ELASTIC_PASSWORD"):
            kwargs["basic_auth"] = (os.environ["ELASTIC_USER"], os.environ["ELASTIC_PASSWORD"])

        self.es = Elasticsearch(**kwargs)
        info = self.es.info()
        log.info("elastic_connected", cluster=info.get("cluster_name"),
                 version=info.get("version", {}).get("number"), hosts=hosts)

    # ------------------------------------------------------------------ lifecycle
    def recreate(self) -> None:
        if self.es.indices.exists(index=self.index_name):
            self.es.indices.delete(index=self.index_name)
        props = dict(_MAPPING_PROPS)
        props["embedding"] = {"type": "dense_vector", "dims": self.dim,
                              "index": True, "similarity": "cosine"}
        self.es.indices.create(index=self.index_name, mappings={"properties": props})
        log.info("elastic_index_created", index=self.index_name, dims=self.dim)

    def index(self, chunks: list[Chunk]) -> None:
        from elasticsearch.helpers import bulk

        actions = []
        for c in chunks:
            doc = meta_to_dict(c)
            doc["text"] = c.text
            doc["embedding"] = c.embedding
            actions.append({"_index": self.index_name, "_id": c.chunk_id, "_source": doc})
        ok, errors = bulk(self.es, actions, refresh="wait_for")
        log.info("elastic_indexed", n=ok, errors=len(errors) if errors else 0)

    # ------------------------------------------------------------------ queries
    def _filter(self, source_type: SourceType | None) -> list[dict]:
        return [{"term": {"source_type": source_type.value}}] if source_type else []

    def _hits(self, resp, retrieved_by: str) -> list[SearchHit]:
        out = []
        for rank, h in enumerate(resp["hits"]["hits"], start=1):
            src = h["_source"]
            out.append(SearchHit(
                chunk_id=h["_id"], score=float(h.get("_score") or 0.0), rank=rank,
                text=src.get("text", ""), metadata=_flat_meta(src),
            ))
        return out

    def bm25(self, query: str, k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        if k <= 0:
            return []
        body = {"size": k, "query": {"bool": {
            "must": [{"match": {"text": {"query": query}}}],
            "filter": self._filter(source_type),
        }}, "_source": {"excludes": ["embedding"]}}
        return self._hits(self.es.search(index=self.index_name, body=body), "bm25")

    def knn(self, query_vector, k: int, source_type: SourceType | None = None) -> list[SearchHit]:
        if k <= 0:
            return []
        knn = {"field": "embedding", "query_vector": list(query_vector),
               "k": k, "num_candidates": max(self.num_candidates, k)}
        flt = self._filter(source_type)
        if flt:
            knn["filter"] = {"bool": {"filter": flt}}
        resp = self.es.search(index=self.index_name, knn=knn, size=k,
                              source={"excludes": ["embedding"]})
        return self._hits(resp, "vector")

    def hybrid(self, query, query_vector, k, source_type=None, rrf_k=60) -> list[SearchHit]:
        if not self.use_native_rrf:
            raise NotImplementedError  # retriever does Python RRF over bm25()+knn()
        flt = self._filter(source_type)
        retriever = {"rrf": {
            "retrievers": [
                {"standard": {"query": {"bool": {"must": [{"match": {"text": query}}], "filter": flt}}}},
                {"knn": {"field": "embedding", "query_vector": list(query_vector),
                         "k": k, "num_candidates": max(self.num_candidates, k),
                         **({"filter": {"bool": {"filter": flt}}} if flt else {})}},
            ],
            "rank_window_size": max(k, 50), "rank_constant": rrf_k,
        }}
        resp = self.es.search(index=self.index_name, retriever=retriever, size=k,
                              source={"excludes": ["embedding"]})
        return self._hits(resp, "hybrid")

    def get(self, chunk_id: str) -> Chunk | None:
        try:
            doc = self.es.get(index=self.index_name, id=chunk_id)
        except Exception:  # noqa: BLE001 - NotFoundError etc.
            return None
        src = doc["_source"]
        meta = _meta_from_source(src)
        return Chunk(chunk_id=chunk_id, text=src.get("text", ""),
                     metadata=meta, embedding=src.get("embedding"))

    def count(self) -> dict[str, int]:
        if not self.es.indices.exists(index=self.index_name):
            return {"total": 0}
        total = self.es.count(index=self.index_name)["count"]
        agg = self.es.search(index=self.index_name, size=0,
                             aggs={"by_src": {"terms": {"field": "source_type"}}})
        out = {"total": total}
        for b in agg["aggregations"]["by_src"]["buckets"]:
            out[b["key"]] = b["doc_count"]
        return out


def _flat_meta(src: dict) -> dict:
    """Shape a SearchHit.metadata dict identically to the local backend's meta_to_dict."""
    m = dict(src)
    m.pop("text", None)
    m.pop("embedding", None)
    return m


def _meta_from_source(src: dict) -> ChunkMetadata:
    def _date(v):
        try:
            return dt.date.fromisoformat(str(v)[:10]) if v and str(v)[:4] != "1970" else None
        except ValueError:
            return None

    span = src.get("char_span") or [0, 0]
    return ChunkMetadata(
        source_type=SourceType(src["source_type"]),
        source_id=src.get("source_id", ""),
        title=src.get("title", ""),
        url=src.get("url", ""),
        heading_path=src.get("heading_path") or [],
        chunk_strategy=src.get("chunk_strategy", ""),
        strategy_version=int(src.get("strategy_version", 1)),
        char_span=(int(span[0]), int(span[1])) if span else (0, 0),
        token_count=int(src.get("token_count", 0)),
        product_version=src.get("product_version"),
        version_confidence=VersionConfidence(src.get("version_confidence", "unknown")),
        published_at=_date(src.get("published_at")),
        updated_at=_date(src.get("updated_at")),
        has_code=bool(src.get("has_code")),
        has_error_code=bool(src.get("has_error_code")),
        error_codes=src.get("error_codes") or [],
        config_keys=src.get("config_keys") or [],
        is_deprecated=bool(src.get("is_deprecated")),
        is_accepted_answer=src.get("is_accepted_answer"),
        votes=src.get("votes"),
        author_role=src.get("author_role"),
        source_authority=float(src.get("source_authority", 1.0)),
    )
