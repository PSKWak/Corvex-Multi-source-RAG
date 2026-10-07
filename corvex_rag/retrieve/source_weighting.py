"""Combine per-source pools into one ranked list with source weights, recency decay, and
version-match factors (DESIGN.md §5). Every factor is written into
`ScoredChunk.components` for provenance.

Shipped mode is `static`. `recency_factor` and `version_match_factor` apply under every
mode; only the per-source multiplier differs (off = all 1.0)."""
from __future__ import annotations

import datetime as dt
import re

from ..config import Config
from ..schema import QueryIntent, ScoredChunk, SourceType

_INTENT_TO_KEY = {
    QueryIntent.DOCUMENTATION_LOOKUP: "documentation_lookup",
    QueryIntent.TROUBLESHOOTING: "troubleshooting",
    QueryIntent.BACKGROUND_DEEPDIVE: "background_deepdive",
    QueryIntent.DEFAULT: "default",
}
_VER_IN_QUERY = re.compile(r"\bv?([1-3])(?:\.(\d+))?(?:\.x|\s*\.x)?\b", re.I)
_LATEST = re.compile(r"\b(latest|current|newest|now|today|2\.6|v2\.6)\b", re.I)


def recency_factor(published_at: dt.date | None, half_life_days: int, as_of: dt.date | None = None) -> float:
    if published_at is None or half_life_days <= 0:
        return 1.0
    age = ((as_of or dt.date.today()) - published_at).days
    return 0.5 ** (max(age, 0) / half_life_days)


def _norm_major_minor(v: str | None) -> tuple[int, int | None] | None:
    if not v:
        return None
    m = re.match(r"([1-3])(?:\.(\d+|x))?", str(v))
    if not m:
        return None
    minor = None if (m.group(2) in (None, "x")) else int(m.group(2))
    return int(m.group(1)), minor


def version_matches(chunk_version: str | None, target_version: str | None) -> bool:
    """True if a chunk's version is compatible with the target (same major, and same minor
    unless either side is 'N.x' / unspecified minor)."""
    cv, tv = _norm_major_minor(chunk_version), _norm_major_minor(target_version)
    if cv is None or tv is None:
        return False
    if cv[0] != tv[0]:
        return False
    return cv[1] is None or tv[1] is None or cv[1] == tv[1]


def version_match_factor(cfg: Config, chunk_version: str | None, target_version: str | None, is_deprecated: bool) -> float:
    boost = float(cfg.section("retrieval", "version_match_boost", default=1.35))
    penalty = float(cfg.section("retrieval", "version_stale_penalty", default=0.6))
    cv, tv = _norm_major_minor(chunk_version), _norm_major_minor(target_version)
    if cv is None or tv is None:
        return 0.9 if is_deprecated else 1.0
    if cv[0] != tv[0]:
        return penalty                      # different major line
    if cv[1] is None or tv[1] is None or cv[1] == tv[1]:
        return boost if not is_deprecated else 1.0
    return 1.0                               # same major, different minor -> neutral


def target_version_for_query(cfg: Config, query: str) -> str | None:
    current = str(cfg.section("product", "current_version", default="2.6"))
    if _LATEST.search(query):
        return current
    m = _VER_IN_QUERY.search(query)
    if m:
        major = m.group(1)
        if m.group(2):
            return f"{major}.{m.group(2)}"
        # "v1" -> last release of that major; "v2" -> current
        rels = [str(r["version"]) for r in cfg.section("product", "releases", default=[])
                if str(r["version"]).startswith(major)]
        return rels[-1] if rels else f"{major}.x"
    return current                           # default: assume the user wants current behaviour


def apply_source_weighting(
    cfg: Config,
    query: str,
    intent: QueryIntent,
    per_source: dict[SourceType, list[ScoredChunk]],
) -> list[ScoredChunk]:
    mode = cfg.section("source_weighting", "mode", default="static")
    half_life = int(cfg.section("retrieval", "recency_half_life_days", default=540))
    floor_k = int(cfg.section("retrieval", "retrieval_floor_k", default=2))
    target_version = target_version_for_query(cfg, query)

    if mode == "off":
        weights = {st.value: 1.0 for st in SourceType}
    elif mode == "static":
        weights = {st.value: float(cfg.section("source_weighting", "static", st.value, default=1.0)) for st in SourceType}
    else:  # adaptive
        key = _INTENT_TO_KEY[intent]
        block = cfg.section("source_weighting", "adaptive", key, default={}) or {}
        weights = {st.value: float(block.get(st.value, 1.0)) for st in SourceType}

    merged: list[ScoredChunk] = []
    for st, pool in per_source.items():
        kept_ids = {sc.chunk.chunk_id for sc in pool[:floor_k]}   # protect the top floor_k
        for rank, sc in enumerate(pool):
            m = sc.chunk.metadata
            pub = _parse_iso(m.extra.get("published_at_iso"))
            sw = weights[st.value] * float(m.source_authority)
            rf = recency_factor(pub, half_life)
            vf = version_match_factor(cfg, m.product_version, target_version, m.is_deprecated)
            final = sc.score * sw * rf * vf
            sc.components.update(
                source_weight=sw, recency=rf, version_match=vf,
                target_version=target_version, weighting_mode=mode, pre_weight_score=sc.score,
                floor_protected=sc.chunk.chunk_id in kept_ids,
            )
            sc.score = final
            merged.append(sc)

    merged.sort(key=lambda s: (s.components.get("floor_protected", False), s.score), reverse=True)
    return merged


def _parse_iso(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
