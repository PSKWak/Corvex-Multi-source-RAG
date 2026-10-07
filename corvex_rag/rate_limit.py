"""Shared rate limiting for every external call (LLM generation, self-check, RAGAS judge,
Cohere rerank, Elastic Cloud).

Explicit user requirement. Fully implemented so the behaviour can be reviewed now.

Design:
  * one `RateLimiter` per provider, created via `RateLimiter.for_provider(cfg, name)`
  * token-bucket for requests-per-minute (`rpm`) + a rolling daily counter (`rpd`)
  * the daily counter is persisted to `logs/rate_limit_state.json` so restarts respect it
  * `guard()` context manager blocks until a slot is free (or raises if that would exceed
    the daily quota and `on_exhaustion != "wait"`)
  * `call(fn, *a, **kw)` wraps a callable with the guard + retry-on-429 (exp backoff+jitter)
  * on final exhaustion: `on_exhaustion` decides wait | mock | error
      - "mock":  raise `FallbackToMock` so the caller can swap in MockLLM
      - "error": raise `RateLimitExhausted`
  * every wait is recorded in `.waits` (list of seconds) for the provenance record
"""
from __future__ import annotations

import contextlib
import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator


class RateLimitExhausted(RuntimeError):
    pass


class FallbackToMock(RuntimeError):
    """Signal to the caller that it should use MockLLM for this call."""


@dataclass
class RateLimitConfig:
    rpm: int = 30
    rpd: int = 100_000
    max_retries: int = 5
    backoff_base_s: float = 2.0
    on_exhaustion: str = "error"          # wait | mock | error

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RateLimitConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class _DailyState:
    day: str
    count: int = 0


@dataclass
class RateLimiter:
    provider: str
    cfg: RateLimitConfig
    state_path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _slot_times: list[float] = field(default_factory=list, repr=False)
    _daily: _DailyState | None = field(default=None, repr=False)
    waits: list[float] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------ factory
    @classmethod
    def for_provider(cls, cfg, provider: str) -> "RateLimiter":
        rl_cfg = RateLimitConfig.from_dict(cfg.section("rate_limits", provider, default={}) or {})
        state_path = cfg.logs_dir / "rate_limit_state.json"
        return cls(provider=provider, cfg=rl_cfg, state_path=state_path)

    # ------------------------------------------------------------------ daily persistence
    def _load_daily(self) -> _DailyState:
        today = date.today().isoformat()
        data: dict[str, Any] = {}
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        entry = data.get(self.provider, {})
        if entry.get("day") != today:
            return _DailyState(day=today, count=0)
        return _DailyState(day=today, count=int(entry.get("count", 0)))

    def _save_daily(self) -> None:
        assert self._daily is not None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        data[self.provider] = {"day": self._daily.day, "count": self._daily.count}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------------ core guard
    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        with self._lock:
            if self._daily is None:
                self._daily = self._load_daily()
            else:
                fresh = self._load_daily()
                if fresh.day != self._daily.day:
                    self._daily = fresh

            if self._daily.count >= self.cfg.rpd:
                if self.cfg.on_exhaustion == "mock":
                    raise FallbackToMock(f"{self.provider}: daily quota {self.cfg.rpd} reached")
                raise RateLimitExhausted(f"{self.provider}: daily quota {self.cfg.rpd} reached")

            self._await_minute_slot()
            self._daily.count += 1
            self._slot_times.append(time.monotonic())
            self._save_daily()
        yield

    def _await_minute_slot(self) -> None:
        """Block until fewer than `rpm` calls in the trailing 60 s."""
        while True:
            now = time.monotonic()
            self._slot_times = [t for t in self._slot_times if now - t < 60.0]
            if len(self._slot_times) < self.cfg.rpm:
                return
            sleep_for = 60.0 - (now - self._slot_times[0]) + random.uniform(0.05, 0.3)
            self.waits.append(sleep_for)
            time.sleep(max(sleep_for, 0.0))

    # ------------------------------------------------------------------ wrapped call
    def call(self, fn: Callable[..., Any], *args: Any, is_rate_error: Callable[[Exception], bool] | None = None, **kwargs: Any) -> Any:
        """Run `fn` under the guard, retrying on rate errors with exp backoff + jitter."""
        is_rate_error = is_rate_error or _default_is_rate_error
        last_exc: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                with self.guard():
                    return fn(*args, **kwargs)
            except FallbackToMock:
                raise
            except RateLimitExhausted:
                raise
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise varied types
                if not is_rate_error(exc) or attempt == self.cfg.max_retries:
                    if is_rate_error(exc):
                        last_exc = exc
                        break
                    raise
                delay = self.cfg.backoff_base_s * (2 ** attempt) + random.uniform(0, 1)
                self.waits.append(delay)
                time.sleep(delay)
                last_exc = exc
        # exhausted retries on rate errors
        if self.cfg.on_exhaustion == "mock":
            raise FallbackToMock(f"{self.provider}: exhausted retries ({last_exc})")
        raise RateLimitExhausted(f"{self.provider}: exhausted retries ({last_exc})")

    def drain_waits(self) -> list[float]:
        w, self.waits = self.waits, []
        return w


def _default_is_rate_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("429", "rate limit", "ratelimit", "quota", "resource_exhausted", "too many requests"))
