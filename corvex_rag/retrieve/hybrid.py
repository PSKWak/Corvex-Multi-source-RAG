"""Per-source hybrid retrieval + fusion (DESIGN.md §4)."""
from __future__ import annotations

from ..config import Config
from ..index.base import SearchBackend, SearchHit
from ..schema import Chunk, ChunkMetadata, ScoredChunk, SourceType, VersionConfidence


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[SearchHit]], k: int = 60, weights: dict[str, float] | None = None
) -> dict[str, tuple[float, dict[str, int]]]:
    """RRF over named ranked lists. score(d) = sum w_list / (k + rank_list(d)), rank
    1-indexed. `weights` lets a noisy retriever (e.g. the hashing embedder's vector list)
    contribute less; defaults to 1.0 per list."""
    weights = weights or {}
    out: dict[str, tuple[float, dict[str, int]]] = {}
    for list_name, hits in ranked_lists.items():
        w = weights.get(list_name, 1.0)
        for h in hits:
            score, ranks = out.get(h.chunk_id, (0.0, {}))
            score += w / (k + h.rank)
            ranks[list_name] = h.rank
            out[h.chunk_id] = (score, ranks)
    return out


def weighted_fusion(
    ranked_lists: dict[str, list[SearchHit]], weights: dict[str, float]
) -> dict[str, tuple[float, dict[str, float]]]:
    """Min-max normalise each list's raw scores, then weighted sum. Config option only."""
    out: dict[str, tuple[float, dict[str, float]]] = {}
    for list_name, hits in ranked_lists.items():
        if not hits:
            continue
        vals = [h.score for h in hits]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        w = weights.get(list_name, 0.0)
        for h in hits:
            norm = (h.score - lo) / span
            score, parts = out.get(h.chunk_id, (0.0, {}))
            score += w * norm
            parts[list_name] = norm
            out[h.chunk_id] = (score, parts)
    return out


def _hit_to_chunk(h: SearchHit) -> Chunk:
    m = h.metadata
    span = m.get("char_span") or [0, 0]
    meta = ChunkMetadata(
        source_type=SourceType(m["source_type"]),
        source_id=m["source_id"],
        title=m.get("title", ""),
        url=m.get("url", ""),
        heading_path=m.get("heading_path", []),
        chunk_strategy=m.get("chunk_strategy", ""),
        char_span=(int(span[0]), int(span[1])),
        token_count=m.get("token_count", 0),
        product_version=m.get("product_version"),
        version_confidence=VersionConfidence(m.get("version_confidence", "unknown")),
        has_code=m.get("has_code", False),
        has_error_code=m.get("has_error_code", False),
        error_codes=m.get("error_codes", []),
        config_keys=m.get("config_keys", []),
        is_deprecated=m.get("is_deprecated", False),
        is_accepted_answer=m.get("is_accepted_answer"),
        votes=m.get("votes"),
        author_role=m.get("author_role"),
        source_authority=m.get("source_authority", 1.0),
    )
    iso = m.get("published_at")
    meta.extra["published_at_iso"] = iso
    if iso:
        import datetime as _dt
        try:
            meta.published_at = _dt.date.fromisoformat(str(iso)[:10])
        except ValueError:
            pass
    return Chunk(chunk_id=h.chunk_id, text=h.text, metadata=meta)


def retrieve_per_source(
    cfg: Config,
    backend: SearchBackend,
    query: str,
    query_vector: list[float],
    *,
    embedder_name: str = "",
) -> dict[SourceType, list[ScoredChunk]]:
    """For each SourceType: BM25 top-N + vector top-N -> fuse -> ScoredChunks carrying
    component scores. Uses the backend's native hybrid only when it advertises support and
    fusion == rrf and elastic.use_native_rrf is on."""
    rc = cfg.section("retrieval")
    bm25_k = int(rc["per_source_bm25_k"])
    vec_k = int(rc["per_source_vector_k"])
    fusion = rc["fusion"]
    rrf_k = int(rc["rrf_k"])
    weights = rc.get("weighted", {})

    native = (
        backend.supports_native_hybrid
        and fusion == "rrf"
        and bool(cfg.section("index", "elastic", "use_native_rrf", default=False))
    )

    per_source: dict[SourceType, list[ScoredChunk]] = {}
    for st in SourceType:
        if native:
            hits = backend.hybrid(query, query_vector, k=max(bm25_k, vec_k), source_type=st, rrf_k=rrf_k)
            scored = [
                ScoredChunk(chunk=_hit_to_chunk(h), score=h.score,
                            components={"hybrid_rank": h.rank}, retrieved_by=["hybrid"])
                for h in hits
            ]
            per_source[st] = scored
            continue

        bm25_hits = backend.bm25(query, bm25_k, source_type=st)
        vec_hits = backend.knn(query_vector, vec_k, source_type=st)
        by_id = {h.chunk_id: h for h in (*vec_hits, *bm25_hits)}

        if fusion == "weighted":
            fused = weighted_fusion(
                {"vector": vec_hits, "bm25": bm25_hits},
                {"vector": float(weights.get("vector_weight", 0.6)),
                 "bm25": float(weights.get("bm25_weight", 0.4))},
            )
            comp_key = "weighted_parts"
        else:
            # the hashing embedder's vectors are near-random; let BM25 lead on that path
            rrf_weights = {"bm25": 1.0, "vector": 0.3} if embedder_name == "hashing" else None
            fused = reciprocal_rank_fusion({"bm25": bm25_hits, "vector": vec_hits}, k=rrf_k, weights=rrf_weights)
            comp_key = "ranks"

        scored = []
        for cid, (score, parts) in fused.items():
            h = by_id[cid]
            retrieved_by = [n for n in ("bm25", "vector") if n in parts]
            scored.append(ScoredChunk(
                chunk=_hit_to_chunk(h),
                score=score,
                components={"fusion": score, comp_key: parts,
                            "bm25_raw": next((x.score for x in bm25_hits if x.chunk_id == cid), None),
                            "vector_raw": next((x.score for x in vec_hits if x.chunk_id == cid), None)},
                retrieved_by=retrieved_by,
            ))
        scored.sort(key=lambda s: s.score, reverse=True)
        per_source[st] = scored
    return per_source
