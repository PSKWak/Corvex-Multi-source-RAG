"""Typed contracts shared by every stage of the pipeline.

These dataclasses are the *glue*: each stage takes and returns these types, so stages can
be developed, tested, and swapped independently. This module is intentionally fully
implemented even though the rest of the package is stubbed — the contracts should be
reviewed as part of the design.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import enum
from typing import Any, Literal, Optional


class SourceType(str, enum.Enum):
    DOCUMENTATION = "documentation"
    FORUM = "forum"
    BLOG = "blog"


class VersionConfidence(str, enum.Enum):
    EXPLICIT = "explicit"      # the text names the version
    INFERRED = "inferred"      # derived from publish date vs release timeline
    UNKNOWN = "unknown"


class QueryIntent(str, enum.Enum):
    DOCUMENTATION_LOOKUP = "documentation_lookup"   # error code, config key, "what is the default"
    TROUBLESHOOTING = "troubleshooting"             # "why does X hang", OS-specific, workarounds
    BACKGROUND_DEEPDIVE = "background_deepdive"     # architecture, benchmarks, internals
    DEFAULT = "default"


# --------------------------------------------------------------------------- documents & chunks
@dc.dataclass(slots=True)
class SourceDocument:
    """One raw document as loaded from disk (before chunking)."""
    source_id: str
    source_type: SourceType
    title: str
    url: str
    body: str                                  # markdown for docs/blogs; rendered thread text for forums
    publish_date: Optional[dt.date] = None
    updated_date: Optional[dt.date] = None
    stated_version: Optional[str] = None       # if the doc explicitly declares a product version
    author_role: Optional[str] = None          # "staff" | "community" | None
    raw: dict[str, Any] = dc.field(default_factory=dict)   # source-specific extra (forum posts, votes, ...)


@dc.dataclass(slots=True)
class ChunkMetadata:
    source_type: SourceType
    source_id: str
    title: str
    url: str
    heading_path: list[str] = dc.field(default_factory=list)
    chunk_strategy: str = ""
    strategy_version: int = 1
    char_span: tuple[int, int] = (0, 0)
    token_count: int = 0

    product_version: Optional[str] = None
    version_confidence: VersionConfidence = VersionConfidence.UNKNOWN
    published_at: Optional[dt.date] = None
    updated_at: Optional[dt.date] = None

    has_code: bool = False
    has_error_code: bool = False
    error_codes: list[str] = dc.field(default_factory=list)
    config_keys: list[str] = dc.field(default_factory=list)
    is_deprecated: bool = False

    # forum-only
    is_accepted_answer: Optional[bool] = None
    votes: Optional[int] = None
    author_role: Optional[str] = None

    source_authority: float = 1.0             # starting prior, from config

    extra: dict[str, Any] = dc.field(default_factory=dict)


@dc.dataclass(slots=True)
class Chunk:
    chunk_id: str                            # sha1(source_id + char_span + strategy_version)
    text: str                                # includes the prepended heading breadcrumb / title
    metadata: ChunkMetadata
    embedding: Optional[list[float]] = None


# --------------------------------------------------------------------------- retrieval
@dc.dataclass(slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float
    # component scores retained for explainability / provenance
    components: dict[str, float] = dc.field(default_factory=dict)
    # e.g. {"bm25_rank": 3, "vector_rank": 1, "rrf": 0.031, "source_weight": 1.4,
    #       "recency": 0.9, "version_match": 1.35, "rerank": 0.87}
    retrieved_by: list[str] = dc.field(default_factory=list)   # ["bm25", "vector"]


@dc.dataclass(slots=True)
class RetrievalResult:
    query: str
    intent: QueryIntent
    per_source: dict[SourceType, list[ScoredChunk]]   # after per-source fusion
    merged: list[ScoredChunk]                         # after source weighting
    reranked: list[ScoredChunk]                       # final top-K evidence
    timings_ms: dict[str, float] = dc.field(default_factory=dict)
    per_source_counts: dict[str, int] = dc.field(default_factory=dict)
    intent_explain: dict[str, Any] = dc.field(default_factory=dict)


# --------------------------------------------------------------------------- contradiction
class ContradictionType(str, enum.Enum):
    VALUE = "value"                 # same subject/predicate, different value
    VERSION = "version"             # differ because they describe different versions
    DEPRECATION = "deprecation"     # one says X is removed/renamed, another still uses X


@dc.dataclass(slots=True)
class Claim:
    subject: str
    predicate: str
    value: str
    version: Optional[str] = None
    condition: Optional[str] = None
    evidence_id: str = ""


@dc.dataclass(slots=True)
class Contradiction:
    type: ContradictionType
    claim_a: Claim
    claim_b: Claim
    evidence_ids: list[str]
    nli_label: Optional[str] = None            # entail | neutral | contradict
    detail: str = ""


@dc.dataclass(slots=True)
class ResolutionDecision:
    contradiction: Contradiction
    winner_evidence_id: Optional[str]          # None => surface the conflict, don't pick
    rule_applied: str                          # version_match | explicit_deprecation | recency | authority | unresolved
    rationale: str


@dc.dataclass(slots=True)
class ResolvedContext:
    evidence: list[ScoredChunk]                # possibly re-ordered / filtered
    contradictions: list[Contradiction]
    decisions: list[ResolutionDecision]
    conflict_notes: list[str]                  # human-readable, passed into the prompt


# --------------------------------------------------------------------------- generation & verification
@dc.dataclass(slots=True)
class Citation:
    n: int
    chunk_id: str
    source_type: SourceType
    source_id: str
    title: str
    url: str
    heading_path: list[str]
    product_version: Optional[str]
    published_at: Optional[dt.date]
    char_span: tuple[int, int]


@dc.dataclass(slots=True)
class DraftAnswer:
    answer_md: str
    citations: list[Citation]
    raw_model_output: str = ""


@dc.dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    score: float
    detail: str = ""


@dc.dataclass(slots=True)
class SelfCheckReport:
    results: list[CheckResult]
    passed: bool
    corrections_applied: int = 0
    correction_log: list[str] = dc.field(default_factory=list)


@dc.dataclass(slots=True)
class Answer:
    query_id: str
    question: str
    answer_md: str
    citations: list[Citation]
    confidence: float
    conflict_notes: list[str]
    self_check: SelfCheckReport
    sources_used: dict[str, int]               # {"documentation": 3, "forum": 2, ...}
    latency_ms: float
    provenance_path: Optional[str] = None


# --------------------------------------------------------------------------- feedback
FeedbackRating = Literal["up", "down"]


@dc.dataclass(slots=True)
class Feedback:
    query_id: str
    rating: FeedbackRating
    note: str = ""
    better_answer: Optional[str] = None
    ts: dt.datetime = dc.field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
