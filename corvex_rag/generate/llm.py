"""One `LLMClient` interface; free-tier adapters (Groq default, Gemini); optional
Anthropic/OpenAI; and a fully-implemented offline `MockLLM`.

Every real adapter routes calls through the shared per-provider `RateLimiter`. On
exhaustion, providers configured with `on_exhaustion: mock` raise `FallbackToMock`, which
the pipeline catches and retries against `MockLLM`.

`complete()` returns an `LLMResponse` (text + token usage) so cost/latency land in
provenance. `complete_json()` requests a JSON object against a schema hint (used by
claim extraction, self-check, RAGAS).
"""
from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..rate_limit import FallbackToMock, RateLimiter


@dataclass(slots=True)
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LLMClient(abc.ABC):
    provider: str
    model: str
    supports_json: bool = True

    @abc.abstractmethod
    def complete(self, system: str, user: str, *, temperature: float | None = None, max_tokens: int | None = None) -> LLMResponse:
        ...

    def complete_json(self, system: str, user: str, schema_hint: str, **kw) -> tuple[dict, LLMResponse]:
        """Default: append the schema hint, call complete(), parse the first JSON object."""
        resp = self.complete(system, user + f"\n\nRespond ONLY with JSON matching:\n{schema_hint}", **kw)
        return _first_json(resp.text), resp


# --------------------------------------------------------------------------- offline mock
class MockLLM(LLMClient):
    """Deterministic, extractive, offline. Good enough to exercise every downstream stage
    (generation, self-check, contradiction adjudication) without a network call.

    Strategy:
      * generation: stitch the 2–3 highest-cited evidence sentences that lexically overlap
        the question, append `[n]` markers, and emit a fixed refusal when overlap is weak.
      * complete_json: return rule-based structures (e.g. self-check → all-pass with
        moderate scores) so the pipeline's control flow is fully testable.
    NOT a quality baseline — a reproducibility and CI device.
    """
    provider = "mock"
    model = "mock-extractive-v1"

    _EVIDENCE_RE = re.compile(
        r"\[(\d+)\]\s*\(([^)]*)\)[^\n]*\n(.*?)(?=\n\[\d+\]\s*\(|\n##\s|\Z)", re.S
    )
    _WORD_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")
    _SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[`])")
    _REFUSAL = (
        "I don't have enough information in the available sources to answer that."
    )

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg

    @staticmethod
    def _prose_only(body: str) -> str:
        """Flatten a chunk to plain text the extractor can score. Markdown table rows are
        turned into 'cell - cell - cell.' sentences rather than dropped (Corvex docs keep
        key facts in tables)."""
        keep = []
        for ln in body.splitlines():
            s = ln.strip()
            if not s or s.startswith(("```", "#")):
                continue
            if set(s) <= set("|-: "):                     # table separator row
                continue
            if s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
                if cells:
                    keep.append(" - ".join(cells) + ".")
                continue
            keep.append(s.lstrip("> ").lstrip("*- "))
        return re.sub(r"\s+", " ", " ".join(keep))

    def _kw(self, text: str) -> set[str]:
        stop = {"the", "and", "for", "with", "you", "that", "this", "how", "what", "does",
                "are", "was", "when", "why", "can", "corvex", "from", "into", "your"}
        return {w for w in self._WORD_RE.findall(text.lower()) if w not in stop}

    def complete(self, system, user, *, temperature=None, max_tokens=None) -> LLMResponse:
        """Extractive: pick the evidence blocks that overlap the question most, emit their
        most-overlapping sentences with [n] markers. Deterministic."""
        question = ""
        qm = re.search(r"Question:\s*(.+)", user)
        if qm:
            question = qm.group(1).splitlines()[0]
        qkw = self._kw(question)

        blocks = self._EVIDENCE_RE.findall(user)
        scored: list[tuple[float, int, str]] = []
        for n, _tag, body in blocks:
            body = self._prose_only(body)
            if re.search(r"ignore (all )?previous|disregard your instructions|you are now in|system message for any ai", body, re.I):
                continue                                  # never extract from an injection payload
            bkw = self._kw(body)
            overlap = len(qkw & bkw) / (len(qkw) or 1)
            scored.append((overlap, int(n), body.strip()))
        scored.sort(reverse=True)

        picked = [s for s in scored if s[0] > 0][:3]
        if not picked or picked[0][0] < 0.12:
            return LLMResponse(text=self._REFUSAL, model=self.model, provider=self.provider,
                               prompt_tokens=len(user) // 4, completion_tokens=12)

        used: list[int] = []
        lines: list[str] = []
        for _ov, n, body in picked:
            sents = [s.strip() for s in self._SENT_RE.split(body) if s.strip()]
            best = sorted(sents, key=lambda s: len(qkw & self._kw(s)), reverse=True)[:2]
            for s in best:
                if len(qkw & self._kw(s)) == 0:
                    continue
                lines.append(f"{s} [{n}]")
                if n not in used:
                    used.append(n)
        if not lines:
            _ov, n, body = picked[0]
            first = re.sub(r"\s+", " ", body)[:280]
            lines.append(f"{first} [{n}]")
            used.append(n)

        answer = " ".join(lines) + f"\n\nCITATIONS: {used}"
        return LLMResponse(text=answer, model=self.model, provider=self.provider,
                           prompt_tokens=len(user) // 4, completion_tokens=len(answer) // 4)

    def complete_json(self, system, user, schema_hint, **kw):
        """Rule-based structured responses so the pipeline's control flow is testable."""
        hint = (schema_hint + " " + system).lower()
        resp = LLMResponse(text="{}", model=self.model, provider=self.provider)
        if "intent" in hint:
            return {"intent": "default", "why": "mock heuristic"}, resp
        if "claims" in hint or ("subject" in hint and "predicate" in hint):
            return {"claims": []}, resp
        if "faithful" in hint or "grounded" in hint or "supported" in hint:
            return {"supported": True, "score": 0.8, "unsupported_sentences": []}, resp
        if "contradict" in hint or "entail" in hint:
            return {"label": "neutral", "confidence": 0.0}, resp
        if "relevant" in hint or "addresses" in hint:
            return {"relevant": True, "score": 0.8}, resp
        return {}, resp


# --------------------------------------------------------------------------- free-tier adapters
class _RateLimitedLLM(LLMClient):
    def __init__(self, cfg: Config, limiter: RateLimiter) -> None:
        self.cfg = cfg
        self.limiter = limiter


class GroqLLM(_RateLimitedLLM):
    """`groq` SDK, OpenAI-compatible. Default generation model: llama-3.3-70b-versatile.
    Every call is wrapped by the shared `groq` RateLimiter."""
    provider = "groq"

    def __init__(self, cfg, limiter):
        super().__init__(cfg, limiter)
        from groq import Groq  # optional dep

        self.model = cfg.section("generation", "groq", "model")
        self.api_key = cfg.require_env(cfg.section("generation", "groq", "api_key_env"))
        self._client = Groq(api_key=self.api_key)
        self._default_temp = float(cfg.section("generation", "temperature", default=0.1))
        self._default_max = int(cfg.section("generation", "max_tokens", default=1024))

    def _create(self, system, user, temperature, max_tokens, json_mode=False):
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._default_temp if temperature is None else temperature,
            max_tokens=self._default_max if max_tokens is None else max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return self._client.chat.completions.create(**kwargs)

    def complete(self, system, user, *, temperature=None, max_tokens=None):
        r = self.limiter.call(self._create, system, user, temperature, max_tokens)
        return LLMResponse(
            text=r.choices[0].message.content or "",
            prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens,
            model=self.model, provider=self.provider,
        )

    def complete_json(self, system, user, schema_hint, *, temperature=None, max_tokens=None):
        r = self.limiter.call(
            self._create, system, user + f"\n\nRespond ONLY with JSON matching:\n{schema_hint}",
            temperature, max_tokens, True,
        )
        text = r.choices[0].message.content or "{}"
        return _first_json(text), LLMResponse(
            text=text, prompt_tokens=r.usage.prompt_tokens,
            completion_tokens=r.usage.completion_tokens, model=self.model, provider=self.provider,
        )


class GeminiLLM(_RateLimitedLLM):
    """`google-generativeai`. Native JSON mode → preferred RAGAS judge. gemini-2.0-flash."""
    provider = "gemini"

    def __init__(self, cfg, limiter):
        super().__init__(cfg, limiter)
        self.model = cfg.section("generation", "gemini", "model")
        self.api_key = cfg.require_env(cfg.section("generation", "gemini", "api_key_env"))
        raise NotImplementedError

    def complete(self, system, user, *, temperature=None, max_tokens=None):
        raise NotImplementedError


class AnthropicLLM(_RateLimitedLLM):
    """Optional. `anthropic` SDK, claude-sonnet-5, adaptive thinking. Not a free tier —
    opt-in only."""
    provider = "anthropic"

    def __init__(self, cfg, limiter):
        super().__init__(cfg, limiter)
        self.model = cfg.section("generation", "anthropic", "model")
        self.api_key = cfg.require_env(cfg.section("generation", "anthropic", "api_key_env"))
        raise NotImplementedError

    def complete(self, system, user, *, temperature=None, max_tokens=None):
        raise NotImplementedError


class OpenAILLM(_RateLimitedLLM):
    """Optional. `openai` SDK, gpt-4o-mini."""
    provider = "openai"

    def __init__(self, cfg, limiter):
        super().__init__(cfg, limiter)
        self.model = cfg.section("generation", "openai", "model")
        self.api_key = cfg.require_env(cfg.section("generation", "openai", "api_key_env"))
        raise NotImplementedError

    def complete(self, system, user, *, temperature=None, max_tokens=None):
        raise NotImplementedError


_REGISTRY = {
    "mock": MockLLM, "groq": GroqLLM, "gemini": GeminiLLM,
    "anthropic": AnthropicLLM, "openai": OpenAILLM,
}


def get_llm(cfg: Config, which: str | None = None, limiters: dict[str, RateLimiter] | None = None) -> LLMClient:
    """`which` overrides `generation.llm` (used by RAGAS to pick its judge). Builds the
    provider's RateLimiter from config if not supplied."""
    name = which or cfg.section("generation", "llm")
    if name == "mock":
        return MockLLM(cfg)
    limiter = (limiters or {}).get(name) or RateLimiter.for_provider(cfg, name)
    return _REGISTRY[name](cfg, limiter)


class _MockFallbackLLM(LLMClient):
    """Delegates to `primary`; on FallbackToMock (rate-limit exhaustion with
    on_exhaustion: mock) or a missing-key/import failure, transparently uses MockLLM and
    records that it happened on `.fell_back`."""

    def __init__(self, primary: LLMClient, mock: MockLLM) -> None:
        self._primary = primary
        self._mock = mock
        self.provider = primary.provider
        self.model = primary.model
        self.fell_back = False

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except FallbackToMock:
            self.fell_back = True
            return None

    def complete(self, system, user, *, temperature=None, max_tokens=None):
        out = self._guard(self._primary.complete, system, user, temperature=temperature, max_tokens=max_tokens)
        if out is None:
            return self._mock.complete(system, user, temperature=temperature, max_tokens=max_tokens)
        return out

    def complete_json(self, system, user, schema_hint, **kw):
        out = self._guard(self._primary.complete_json, system, user, schema_hint, **kw)
        if out is None:
            return self._mock.complete_json(system, user, schema_hint, **kw)
        return out


def get_llm_with_mock_fallback(cfg: Config, which: str | None = None, limiters: dict[str, RateLimiter] | None = None) -> LLMClient:
    """Return an LLM that never hard-fails on quota / missing key: it falls back to MockLLM.
    If `generation.llm` is already `mock`, returns MockLLM directly."""
    name = which or cfg.section("generation", "llm")
    mock = MockLLM(cfg)
    if name == "mock":
        return mock
    try:
        primary = get_llm(cfg, name, limiters)
    except (RuntimeError, ImportError) as exc:  # missing key or SDK
        import structlog
        structlog.get_logger("llm").warning("llm_unavailable_using_mock", provider=name, detail=str(exc))
        return mock
    return _MockFallbackLLM(primary, mock)


def _first_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))
