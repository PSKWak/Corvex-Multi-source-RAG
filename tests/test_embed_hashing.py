"""HashingEmbedder is fully implemented — the offline default."""
import math

from corvex_rag.ingest.embed import HashingEmbedder, get_embedder


def test_deterministic_and_normalised(cfg):
    e = HashingEmbedder(cfg)
    a = e.embed_query("How do I fix CVX-4210 backpressure?")
    b = e.embed_query("How do I fix CVX-4210 backpressure?")
    assert a == b
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)
    assert len(a) == e.dim


def test_related_texts_more_similar_than_unrelated(cfg):
    e = HashingEmbedder(cfg)
    q = e.embed_query("worker concurrency configuration key")
    near = e.embed_documents(["Set workers.concurrency to control worker concurrency."])[0]
    far = e.embed_documents(["The dead-letter queue holds jobs after max_retries."])[0]
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))
    assert dot(q, near) > dot(q, far)


def test_factory_returns_hashing_by_default(cfg):
    assert get_embedder(cfg).name == "hashing"
