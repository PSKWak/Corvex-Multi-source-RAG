"""Transparent query-intent classifier driving adaptive source weighting (DESIGN.md §5).

Rule-based first (fast, free, explainable). Optional one-shot LLM confirmation when
`source_weighting.mode == adaptive` and an LLM is configured.
"""
from __future__ import annotations

import re

from ..config import Config
from ..schema import QueryIntent

_DOC_SIGNALS = [
    re.compile(r"\bCVX-\d{3,4}\b", re.I),
    re.compile(r"\b(default|config|configuration|setting|option|reference|syntax|flag|env var)\b", re.I),
    re.compile(r"\bhow do i (set|configure|enable|disable)\b", re.I),
]
_TROUBLE_SIGNALS = [
    re.compile(r"\b(why does|why is|anyone else|not working|hangs?|freezes?|crashes?|stuck|workaround|broke|regression)\b", re.I),
    re.compile(r"\b(macos|windows|linux|ubuntu|docker|kubernetes|redis \d|arm64|m1|m2)\b", re.I),
]
_DEEPDIVE_SIGNALS = [
    re.compile(r"\b(benchmark|throughput|architecture|internals?|design|at scale|how (did|do) (you|they)|load test|deep dive)\b", re.I),
]


_BUCKETS = {
    QueryIntent.DOCUMENTATION_LOOKUP: _DOC_SIGNALS,
    QueryIntent.TROUBLESHOOTING: _TROUBLE_SIGNALS,
    QueryIntent.BACKGROUND_DEEPDIVE: _DEEPDIVE_SIGNALS,
}


def classify(cfg: Config, query: str, llm=None) -> tuple[QueryIntent, dict]:
    """Rule-based query-intent classification. `explain` records which signals fired so the
    decision is auditable in the provenance record.

    The LLM confirmation path only runs when source weighting is `adaptive` AND an llm is
    supplied — under the shipped `static` mode intent is informational only.
    """
    fired: dict[str, list[str]] = {}
    scores: dict[QueryIntent, int] = {}
    for intent, signals in _BUCKETS.items():
        hits = [s.pattern for s in signals if s.search(query)]
        if hits:
            fired[intent.value] = hits
        scores[intent] = len(hits)

    best = max(scores, key=lambda k: scores[k])
    intent = best if scores[best] > 0 else QueryIntent.DEFAULT
    explain = {"method": "rules", "fired": fired, "scores": {k.value: v for k, v in scores.items()}}

    if llm is not None and cfg.section("source_weighting", "mode") == "adaptive":
        try:
            from ..generate.prompts import QUERY_INTENT_SYSTEM
            obj, _ = llm.complete_json(
                QUERY_INTENT_SYSTEM, f"Question: {query}",
                '{"intent": "documentation_lookup|troubleshooting|background_deepdive|default", "why": "..."}',
            )
            confirmed = QueryIntent(obj.get("intent", intent.value))
            explain.update(method="rules+llm", llm_intent=confirmed.value, llm_why=obj.get("why", ""))
            intent = confirmed
        except Exception as exc:  # noqa: BLE001 - never let intent classification break retrieval
            explain["llm_error"] = str(exc)

    return intent, explain
