"""Rate limiter is fully implemented — real behavioural tests."""
import time

import pytest

from corvex_rag.rate_limit import (
    FallbackToMock,
    RateLimitConfig,
    RateLimitExhausted,
    RateLimiter,
)


def _limiter(tmp_path, **over):
    base = dict(rpm=3, rpd=5, max_retries=2, backoff_base_s=0.01, on_exhaustion="error")
    base.update(over)
    return RateLimiter(
        provider="test",
        cfg=RateLimitConfig(**base),
        state_path=tmp_path / "rl.json",
    )


def test_rpm_blocks_the_fourth_call_within_a_minute(tmp_path, monkeypatch):
    rl = _limiter(tmp_path)
    slept = []
    monkeypatch.setattr("corvex_rag.rate_limit.time.sleep", lambda s: slept.append(s))
    for _ in range(3):
        with rl.guard():
            pass
    # 4th call would exceed rpm=3 -> _await_minute_slot sleeps
    with rl.guard():
        pass
    assert slept, "expected the limiter to sleep before allowing the 4th call"


def test_daily_quota_persists_across_instances(tmp_path):
    rl1 = _limiter(tmp_path, rpm=100)
    for _ in range(5):
        with rl1.guard():
            pass
    rl2 = _limiter(tmp_path, rpm=100)          # fresh instance, same state file
    with pytest.raises(RateLimitExhausted):
        with rl2.guard():
            pass


def test_on_exhaustion_mock_raises_fallback(tmp_path):
    rl = _limiter(tmp_path, rpm=100, rpd=1, on_exhaustion="mock")
    with rl.guard():
        pass
    with pytest.raises(FallbackToMock):
        with rl.guard():
            pass


def test_call_retries_rate_errors_then_gives_up(tmp_path, monkeypatch):
    rl = _limiter(tmp_path, rpm=100, on_exhaustion="error")
    monkeypatch.setattr("corvex_rag.rate_limit.time.sleep", lambda s: None)
    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise RuntimeError("HTTP 429 rate limit exceeded")

    with pytest.raises(RateLimitExhausted):
        rl.call(always_429)
    assert calls["n"] == rl.cfg.max_retries + 1
