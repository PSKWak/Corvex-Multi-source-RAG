"""ingest + local index: chunking rules, metadata enrichment, search."""
import pytest

from corvex_rag.ingest.chunking import get_chunker
from corvex_rag.ingest.loaders import load_all
from corvex_rag.ingest.metadata import extract_config_keys, extract_error_codes
from corvex_rag.ingest.pipeline import build_chunks
from corvex_rag.schema import SourceType


@pytest.fixture
def _cfg(cfg):
    return cfg


def test_loaders_produce_28_docs(_cfg):
    docs = load_all(_cfg)
    by_type = {}
    for d in docs:
        by_type.setdefault(d.source_type, 0)
        by_type[d.source_type] += 1
    assert by_type[SourceType.DOCUMENTATION] == 10
    assert by_type[SourceType.FORUM] == 12
    assert by_type[SourceType.BLOG] == 6


def test_doc_chunker_keeps_code_fences_whole(_cfg):
    docs = {d.source_id: d for d in load_all(_cfg)}
    chunker = get_chunker(_cfg, SourceType.DOCUMENTATION)
    for rc in chunker.split(docs["doc-getting-started"]):
        assert rc.text.count("```") % 2 == 0, "a fenced code block was split"


def test_forum_chunker_pairs_question_with_accepted_answer(_cfg):
    docs = {d.source_id: d for d in load_all(_cfg)}
    chunks = get_chunker(_cfg, SourceType.FORUM).split(docs["forum-1001"])
    first = chunks[0]
    assert "[Question" in first.text and "accepted" in first.text.lower()
    assert first.extra["is_accepted_answer"] is True


def test_metadata_extractors():
    assert extract_error_codes(type("C", (), {"section": staticmethod(lambda *a, **k: r"CVX-\d{3,4}")})(),
                               "see CVX-4210 and cvx-1001") == ["CVX-1001", "CVX-4210"]
    keys = extract_config_keys("set workers.concurrency and storage.redis.url; not example.com or a.b.c()")
    assert "workers.concurrency" in keys and "storage.redis.url" in keys
    assert "example.com" not in keys


def test_build_chunks_deterministic_ids(_cfg):
    a = build_chunks(_cfg)
    b = build_chunks(_cfg)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert all(c.embedding is not None and len(c.embedding) == 512 for c in a)


def test_version_inference(_cfg):
    chunks = {c.metadata.source_id: c for c in build_chunks(_cfg)}
    # the 2023 v1-era blog should be tagged 1.x / 1.9, never 2.x
    blog = next(c for c in build_chunks(_cfg) if c.metadata.source_id == "blog-2023-01-getting-started")
    assert blog.metadata.product_version.startswith("1")


def test_local_backend_search(tmp_path, _cfg):
    _cfg.raw["paths"]["index_dir"] = str(tmp_path)
    from corvex_rag.index.base import get_backend
    backend = get_backend(_cfg)
    backend.recreate()
    backend.index(build_chunks(_cfg))
    assert backend.count()["total"] == 100

    hits = backend.bm25("CVX-4210 queue depth limit", k=5, source_type=SourceType.DOCUMENTATION)
    assert hits and all(h.metadata["source_type"] == "documentation" for h in hits)
    assert any("4210" in h.text for h in hits)

    from corvex_rag.ingest.embed import get_embedder
    qv = get_embedder(_cfg).embed_query("how long are completed jobs kept")
    vhits = backend.knn(qv, k=5)
    assert vhits and vhits[0].rank == 1
