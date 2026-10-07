"""Contradiction detection over the top-K evidence (DESIGN.md §7).

Three layers, cheapest first:
  1. rule-based claim extraction — domain-tuned patterns for the claim shapes that matter
     in Corvex support (defaults, ports, config keys, auth method). Deterministic, offline.
  2. NLI cross-encoder on topically-close cross-source/cross-version pairs (when available).
  3. LLM adjudication — only on pairs the cheaper layers flagged, and only when the LLM is
     not the offline mock.
"""
from __future__ import annotations

import re

import structlog

from ..config import Config
from ..ingest.embed import Embedder
from ..rate_limit import RateLimiter
from ..schema import Claim, Contradiction, ContradictionType, ScoredChunk
from .nli import NLIModel

log = structlog.get_logger("contradiction")


# subject -> (regex, value normaliser). First capture group is the value.
_CLAIM_PATTERNS: list[tuple[str, re.Pattern, "callable"]] = [
    ("completed_job_retention_default",
     re.compile(r"(?:retention\.completed|completed[- ]job).{0,60}?default[s]?\s*(?:to|:|is|of)?\s*"
                r"(\d+\s*(?:h|hours?|days?)|\d+h|forever|unlimited)", re.I),
     lambda v: v.lower().replace(" ", "")),
    ("completed_job_retention_default",
     re.compile(r"keeps? completed jobs (?:for )?(\d+\s*days?|forever|\d+h)", re.I),
     lambda v: v.lower().replace(" ", "")),
    ("worker_concurrency_config_key",
     re.compile(r"\b(workers\.concurrency|worker_threads)\b"),
     lambda v: v.lower()),
    ("default_api_port",
     re.compile(r"(?:port|http\.addr|http_port|listen).{0,30}?\b(\d{4})\b", re.I),
     lambda v: v),
    ("api_auth_method",
     re.compile(r"(Authorization:\s*Bearer|\?token=)", re.I),
     lambda v: "bearer_header" if v.lower().startswith("auth") else "query_param"),
    ("cvx_4210_fix_max_depth_zero",
     re.compile(r"queue\.max_depth\s*(?::|to)?\s*(0)\b|max_depth:\s*0", re.I),
     lambda v: "set_zero"),
    ("purge_command",
     re.compile(r"\bcorvex (prune|purge)\b", re.I),
     lambda v: v.lower()),
]

_EMPIRICAL_CUES = re.compile(r"\b(hang|hangs|crash|crashes|freez|stuck|macos|windows|arm64|only reproduces|confirmed here)\b", re.I)
_NORMATIVE_CUES = re.compile(r"\b(default|the value is|is set to|must be|reference)\b", re.I)


def extract_claims_rule_based(chunk: ScoredChunk) -> list[Claim]:
    text = chunk.chunk.text
    m = chunk.chunk.metadata
    claims: list[Claim] = []
    for subject, pat, norm in _CLAIM_PATTERNS:
        found = pat.search(text)
        if not found:
            continue
        raw = found.group(1) if found.lastindex else found.group(0)
        claims.append(Claim(
            subject=subject, predicate="is", value=norm(raw),
            version=m.product_version, evidence_id=chunk.chunk.chunk_id,
        ))
    return claims


def topical_pairs(cfg: Config, embedder: Embedder, evidence: list[ScoredChunk]) -> list[tuple[int, int]]:
    import numpy as np

    thr = float(cfg.section("contradiction", "topical_cosine_threshold", default=0.55))
    vecs = np.array(embedder.embed_documents([e.chunk.text for e in evidence]))
    pairs: list[tuple[int, int]] = []
    for i in range(len(evidence)):
        for j in range(i + 1, len(evidence)):
            mi, mj = evidence[i].chunk.metadata, evidence[j].chunk.metadata
            if mi.source_type is mj.source_type and mi.product_version == mj.product_version:
                continue
            if float(vecs[i] @ vecs[j]) >= thr:
                pairs.append((i, j))
    return pairs


def extract_claims(llm, chunk: ScoredChunk, question: str, limiter: RateLimiter | None) -> list[Claim]:
    if llm is None or getattr(llm, "provider", "mock") == "mock":
        return []
    from ..generate.prompts import CLAIM_EXTRACTION_SYSTEM
    try:
        obj, _ = llm.complete_json(
            CLAIM_EXTRACTION_SYSTEM,
            f"Question: {question}\n\nPassage:\n{chunk.chunk.text}",
            '{"claims": [{"subject": "...", "predicate": "...", "value": "...", "version": "...", "condition": "..."}]}',
        )
        return [
            Claim(subject=c.get("subject", ""), predicate=c.get("predicate", "is"),
                  value=str(c.get("value", "")), version=c.get("version") or chunk.chunk.metadata.product_version,
                  condition=c.get("condition"), evidence_id=chunk.chunk.chunk_id)
            for c in obj.get("claims", [])
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("claim_extraction_failed", detail=str(exc))
        return []


def _claim_type(a: Claim, b: Claim) -> ContradictionType:
    if a.subject == "worker_concurrency_config_key" or a.subject == "purge_command":
        return ContradictionType.DEPRECATION
    if a.version and b.version and a.version[0] != b.version[0]:
        return ContradictionType.VERSION
    return ContradictionType.VALUE


def detect(
    cfg: Config,
    embedder: Embedder,
    nli: NLIModel,
    llm,
    question: str,
    evidence: list[ScoredChunk],
    limiter: RateLimiter | None = None,
) -> list[Contradiction]:
    if not cfg.section("contradiction", "enabled", default=True) or len(evidence) < 2:
        return []

    by_id = {e.chunk.chunk_id: e for e in evidence}
    contradictions: list[Contradiction] = []
    seen: set[tuple] = set()

    # ---- layer 1: rule-based claims grouped by subject ----
    claims_by_subject: dict[str, list[Claim]] = {}
    for e in evidence:
        for c in extract_claims_rule_based(e):
            claims_by_subject.setdefault(c.subject, []).append(c)

    for subject, claims in claims_by_subject.items():
        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                a, b = claims[i], claims[j]
                if a.value == b.value:
                    continue
                ea, eb = by_id[a.evidence_id], by_id[b.evidence_id]
                if ea.chunk.metadata.source_type is eb.chunk.metadata.source_type and a.version == b.version:
                    continue
                key = tuple(sorted([a.evidence_id, b.evidence_id])) + (subject,)
                if key in seen:
                    continue
                seen.add(key)
                contradictions.append(Contradiction(
                    type=_claim_type(a, b), claim_a=a, claim_b=b,
                    evidence_ids=[a.evidence_id, b.evidence_id],
                    detail=f"{subject}: {a.value!r} (v{a.version}) vs {b.value!r} (v{b.version})",
                ))

    # ---- layer 2: NLI on topical pairs ----
    if cfg.section("contradiction", "nli", "enabled", default=False) and getattr(nli, "name", "null") != "null":
        for i, j in topical_pairs(cfg, embedder, evidence):
            label, prob = nli.predict(evidence[i].chunk.text, evidence[j].chunk.text)
            if label == "contradict" and prob >= 0.5:
                key = tuple(sorted([evidence[i].chunk.chunk_id, evidence[j].chunk.chunk_id])) + ("nli",)
                if key in seen:
                    continue
                seen.add(key)
                ca = Claim(subject="nli_detected", predicate="conflicts_with", value="",
                           version=evidence[i].chunk.metadata.product_version, evidence_id=evidence[i].chunk.chunk_id)
                cb = Claim(subject="nli_detected", predicate="conflicts_with", value="",
                           version=evidence[j].chunk.metadata.product_version, evidence_id=evidence[j].chunk.chunk_id)
                contradictions.append(Contradiction(
                    type=ContradictionType.VALUE, claim_a=ca, claim_b=cb,
                    evidence_ids=[ca.evidence_id, cb.evidence_id], nli_label=label,
                    detail=f"NLI contradiction (p={prob:.2f})",
                ))

    # ---- layer 3: LLM adjudication of flagged pairs (skipped for mock) ----
    if cfg.section("contradiction", "llm_adjudication", default=False) and getattr(llm, "provider", "mock") != "mock":
        contradictions = _llm_adjudicate(llm, question, by_id, contradictions, limiter)

    log.info("contradictions_detected", n=len(contradictions),
             subjects=[c.claim_a.subject for c in contradictions])
    return contradictions


def _llm_adjudicate(llm, question, by_id, contradictions, limiter):
    from ..generate.prompts import CLAIM_EXTRACTION_SYSTEM  # reuse verifier-style prompt

    kept = []
    for c in contradictions:
        a, b = by_id.get(c.evidence_ids[0]), by_id.get(c.evidence_ids[1])
        if not a or not b:
            continue
        try:
            obj, _ = llm.complete_json(
                "You judge whether two passages genuinely contradict each other for the user's question.",
                f"Question: {question}\n\nPassage A (v{a.chunk.metadata.product_version}):\n{a.chunk.text}\n\n"
                f"Passage B (v{b.chunk.metadata.product_version}):\n{b.chunk.text}",
                '{"contradict": true, "type": "value|version|deprecation", "explanation": "..."}',
            )
            if obj.get("contradict"):
                c.detail += f" | LLM: {obj.get('explanation', '')}"
                try:
                    c.type = ContradictionType(obj.get("type", c.type.value))
                except ValueError:
                    pass
                kept.append(c)
        except Exception:  # noqa: BLE001
            kept.append(c)   # keep on error — better a false positive than a missed conflict
    return kept
