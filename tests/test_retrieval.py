"""hybrid fusion + source weighting + retriever wiring (offline: hashing + identity)."""
import pytest

from corvex_rag.index.base import SearchHit
from corvex_rag.retrieve.hybrid import reciprocal_rank_fusion, weighted_fusion
from corvex_rag.retrieve.source_weighting import (
    recency_factor,
    target_version_for_query,
    version_match_factor,
)


def _hit(cid, rank, score=1.0):
    return SearchHit(chunk_id=cid, score=score, rank=rank, text="", metadata={})


def test_rrf_rewards_agreement():
    fused = reciprocal_rank_fusion(
        {"bm25": [_hit("a", 1), _hit("b", 2)], "vector": [_hit("b", 1), _hit("a", 2)]}, k=60
    )
    # b is rank1 in one list and rank2 in the other, same for a -> tie; c absent
    assert set(fused) == {"a", "b"}
    assert abs(fused["a"][0] - fused["b"][0]) < 1e-9


def test_weighted_fusion_normalises():
    fused = weighted_fusion({"vector": [_hit("a", 1, 10.0), _hit("b", 2, 0.0)]}, {"vector": 1.0})
    assert fused["a"][0] == pytest.approx(1.0)
    assert fused["b"][0] == pytest.approx(0.0)


def test_recency_factor_halves_at_half_life():
    import datetime as dt
    d = dt.date(2024, 1, 1)
    assert recency_factor(d, 365, as_of=dt.date(2025, 1, 1)) == pytest.approx(0.5, abs=0.02)
    assert recency_factor(None, 365) == 1.0


def test_version_match_factor(cfg):
    assert version_match_factor(cfg, "2.6", "2.6", False) > 1.0        # exact -> boost
    assert version_match_factor(cfg, "1.9", "2.6", False) < 1.0        # wrong major -> penalty
    assert version_match_factor(cfg, "2.x", "2.6", False) > 1.0        # N.x matches major
    assert version_match_factor(cfg, "2.3", "2.6", False) == 1.0       # same major diff minor -> neutral


def test_target_version_for_query(cfg):
    assert target_version_for_query(cfg, "how do I do X in v1.9?") == "1.9"
    assert target_version_for_query(cfg, "what is the current default?") == "2.6"
    assert target_version_for_query(cfg, "generic question") == "2.6"


def test_retriever_end_to_end(cfg):
    from corvex_rag.index.base import get_backend
    from corvex_rag.ingest.embed import get_embedder
    from corvex_rag.ingest.pipeline import build_chunks
    from corvex_rag.retrieve.rerank import IdentityReranker
    from corvex_rag.retrieve.retriever import Retriever

    backend = get_backend(cfg)
    backend.recreate()
    backend.index(build_chunks(cfg))
    r = Retriever(cfg, backend, get_embedder(cfg), IdentityReranker())

    res = r.retrieve("What does error CVX-4210 mean?", top_k=6)
    assert res.intent.value in {"documentation_lookup", "default", "troubleshooting"}
    assert 1 <= len(res.reranked) <= 6
    # diversity guard: >=2 source types when the pool has them
    assert len({sc.chunk.metadata.source_type for sc in res.reranked}) >= 2
    for sc in res.reranked:
        assert "source_weight" in sc.components and "version_match" in sc.components
