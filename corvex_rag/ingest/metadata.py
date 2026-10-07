"""Rule-based metadata enrichment (DESIGN.md §3.2). No LLM."""
from __future__ import annotations

import datetime as dt
import re

from ..config import Config
from ..schema import ChunkMetadata, SourceDocument, SourceType, VersionConfidence
from ..textutil import count_tokens, has_code_fence
from .chunking import RawChunk

# Strong version cues only — a bare "1.x" mid-sentence is almost always a cross-reference
# ("the 1.x query param"), not the version the chunk documents.
_VERSION_STRONG = re.compile(
    r"(?:corvex\s+|applies to[^.\n]{0,30}?|targets[^.\n]{0,20}?|version[:\s]+)"
    r"v?([1-3]\.\d+(?:-beta)?)\b",
    re.I,
)
_VERSION_STRONG_XY = re.compile(r"corvex\s+v?([1-3])\.x\b", re.I)
_KNOWN_V1_KEYS = ("worker_threads", "redis_url", "http_port", "keep_completed_days", "stats_enabled")
_CONFIG_NAMESPACES = {
    "workers", "storage", "queue", "retention", "http", "auth", "telemetry",
}
_CONFIG_KEY_RE = re.compile(r"(?<![\w./@-])([a-z_]+(?:\.[a-z_]+){1,3})(?![\w-])")


def _releases(cfg: Config) -> list[tuple[str, dt.date]]:
    out = []
    for r in cfg.section("product", "releases", default=[]):
        out.append((str(r["version"]), dt.date.fromisoformat(str(r["date"]))))
    return sorted(out, key=lambda x: x[1])


def infer_version(cfg: Config, doc: SourceDocument, chunk_text: str) -> tuple[str | None, VersionConfidence]:
    """Priority: strong in-text cue > structured stated version > publish-date inference.
    A bare "1.x" / "2.0" mid-sentence is treated as a cross-reference, not the chunk's
    own version."""
    m = _VERSION_STRONG.search(chunk_text)
    if m:
        return m.group(1), VersionConfidence.EXPLICIT
    mx = _VERSION_STRONG_XY.search(chunk_text)
    if mx:
        return f"{mx.group(1)}.x", VersionConfidence.EXPLICIT

    if doc.stated_version:
        return doc.stated_version, VersionConfidence.EXPLICIT

    if doc.publish_date:
        prior = [v for v, d in _releases(cfg) if d <= doc.publish_date]
        if prior:
            return prior[-1], VersionConfidence.INFERRED

    return None, VersionConfidence.UNKNOWN


def extract_error_codes(cfg: Config, text: str) -> list[str]:
    pat = cfg.section("ingestion", "enrichment", "error_code_regex", default=r"CVX-\d{3,4}")
    return sorted({m.upper() for m in re.findall(pat, text, re.I)})


def extract_config_keys(text: str) -> list[str]:
    """Dotted keys whose first segment is a known Corvex config namespace, plus the known
    1.x flat keys. Deliberately strict — domains/paths/method chains are not config keys."""
    found: set[str] = set()
    for m in _CONFIG_KEY_RE.finditer(text):
        key = m.group(1)
        if key.split(".")[0] in _CONFIG_NAMESPACES:
            found.add(key)
    for k in _KNOWN_V1_KEYS:
        if re.search(rf"(?<![\w-]){k}(?![\w-])", text):
            found.add(k)
    return sorted(found)


def detect_deprecation(cfg: Config, text: str) -> bool:
    markers = cfg.section("ingestion", "enrichment", "deprecation_markers", default=[])
    low = text.lower()
    return any(m.lower() in low for m in markers)


def compute_age_days(published_at: dt.date | None, as_of: dt.date | None = None) -> int | None:
    if published_at is None:
        return None
    return ((as_of or dt.date.today()) - published_at).days


def enrich(cfg: Config, doc: SourceDocument, raw: RawChunk, chunk_strategy: str) -> ChunkMetadata:
    text = raw.text
    version, confidence = infer_version(cfg, doc, text)
    codes = extract_error_codes(cfg, text)
    authority = cfg.section("ingestion", "enrichment", "source_authority", doc.source_type.value, default=1.0)
    extra = raw.extra or {}
    return ChunkMetadata(
        source_type=doc.source_type,
        source_id=doc.source_id,
        title=doc.title,
        url=doc.url,
        heading_path=list(raw.heading_path),
        chunk_strategy=chunk_strategy,
        strategy_version=int(cfg.section("ingestion", "chunking", "strategy_version", default=1)),
        char_span=tuple(raw.char_span),
        token_count=count_tokens(text),
        product_version=version,
        version_confidence=confidence,
        published_at=doc.publish_date,
        updated_at=doc.updated_date,
        has_code=has_code_fence(text) or "`" in text,
        has_error_code=bool(codes),
        error_codes=codes,
        config_keys=extract_config_keys(text),
        is_deprecated=detect_deprecation(cfg, text),
        is_accepted_answer=extra.get("is_accepted_answer"),
        votes=extra.get("votes"),
        author_role=extra.get("author_role") or (doc.author_role if doc.source_type is not SourceType.FORUM else None),
        source_authority=float(authority),
        extra={k: v for k, v in extra.items() if k == "post_indices"},
    )
