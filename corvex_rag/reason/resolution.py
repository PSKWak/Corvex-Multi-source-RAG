"""Deterministic conflict resolution policy (DESIGN.md §7). Every decision logged."""
from __future__ import annotations

import datetime as dt
import re

import structlog

from ..config import Config
from ..schema import (
    Contradiction,
    ResolutionDecision,
    ResolvedContext,
    ScoredChunk,
    SourceType,
)
from ..retrieve.source_weighting import version_matches

log = structlog.get_logger("resolution")

_DEPRECATION_RE = re.compile(r"\b(removed in|renamed to|replaced by|no longer|deprecated)\b", re.I)
_EMPIRICAL_RE = re.compile(r"\b(hang|hangs|crash|crashes|freez|stuck|macos|windows|arm64|reproduce)\b", re.I)
_AUTHORITY = {SourceType.DOCUMENTATION: 3, SourceType.BLOG: 2, SourceType.FORUM: 1}


def _published(sc: ScoredChunk) -> dt.date:
    return sc.chunk.metadata.published_at or dt.date.max  # undated docs treated as "current"


def _rule_version_match(cfg, c, ev, target_version):
    a, b = ev[c.evidence_ids[0]], ev[c.evidence_ids[1]]
    am = version_matches(a.chunk.metadata.product_version, target_version)
    bm = version_matches(b.chunk.metadata.product_version, target_version)
    if am and not bm:
        return ResolutionDecision(c, a.chunk.chunk_id, "version_match",
                                  f"{a.chunk.metadata.source_id} matches target v{target_version}; "
                                  f"{b.chunk.metadata.source_id} is v{b.chunk.metadata.product_version}")
    if bm and not am:
        return ResolutionDecision(c, b.chunk.chunk_id, "version_match",
                                  f"{b.chunk.metadata.source_id} matches target v{target_version}; "
                                  f"{a.chunk.metadata.source_id} is v{a.chunk.metadata.product_version}")
    return None


def _rule_explicit_deprecation(cfg, c, ev, target_version):
    a, b = ev[c.evidence_ids[0]], ev[c.evidence_ids[1]]
    a_dep = bool(_DEPRECATION_RE.search(a.chunk.text)) or a.chunk.metadata.is_deprecated
    b_dep = bool(_DEPRECATION_RE.search(b.chunk.text)) or b.chunk.metadata.is_deprecated
    # the chunk that *describes the change* wins as the authority on current state
    if a_dep and not b_dep:
        return ResolutionDecision(c, a.chunk.chunk_id, "explicit_deprecation",
                                  f"{a.chunk.metadata.source_id} explicitly states the change/deprecation")
    if b_dep and not a_dep:
        return ResolutionDecision(c, b.chunk.chunk_id, "explicit_deprecation",
                                  f"{b.chunk.metadata.source_id} explicitly states the change/deprecation")
    return None


def _rule_recency(cfg, c, ev, target_version):
    a, b = ev[c.evidence_ids[0]], ev[c.evidence_ids[1]]
    da, db = _published(a), _published(b)
    if da == db:
        return None
    winner, loser = (a, b) if da > db else (b, a)
    return ResolutionDecision(c, winner.chunk.chunk_id, "recency",
                              f"{winner.chunk.metadata.source_id} ({_published(winner)}) is newer than "
                              f"{loser.chunk.metadata.source_id} ({_published(loser)})")


def _rule_authority(cfg, c, ev, target_version):
    a, b = ev[c.evidence_ids[0]], ev[c.evidence_ids[1]]
    # a forum post reporting an empirical symptom outranks docs for that *observation*
    a_emp = a.chunk.metadata.source_type is SourceType.FORUM and _EMPIRICAL_RE.search(a.chunk.text)
    b_emp = b.chunk.metadata.source_type is SourceType.FORUM and _EMPIRICAL_RE.search(b.chunk.text)
    if a_emp and not b_emp:
        return ResolutionDecision(c, a.chunk.chunk_id, "authority", "forum reports an empirical symptom")
    if b_emp and not a_emp:
        return ResolutionDecision(c, b.chunk.chunk_id, "authority", "forum reports an empirical symptom")
    pa, pb = _AUTHORITY[a.chunk.metadata.source_type], _AUTHORITY[b.chunk.metadata.source_type]
    if pa == pb:
        return None
    winner = a if pa > pb else b
    return ResolutionDecision(c, winner.chunk.chunk_id, "authority",
                              f"{winner.chunk.metadata.source_type.value} outranks the other source for a normative claim")


_RULES = {
    "version_match": _rule_version_match,
    "explicit_deprecation": _rule_explicit_deprecation,
    "recency": _rule_recency,
    "authority": _rule_authority,
}


def resolve(
    cfg: Config,
    question: str,
    target_version: str | None,
    evidence: list[ScoredChunk],
    contradictions: list[Contradiction],
) -> ResolvedContext:
    ev = {e.chunk.chunk_id: e for e in evidence}
    policy = cfg.section("contradiction", "resolution_policy", default=list(_RULES))
    decisions: list[ResolutionDecision] = []
    notes: list[str] = []
    demoted: set[str] = set()

    for c in contradictions:
        if not all(eid in ev for eid in c.evidence_ids[:2]):
            continue
        decision = None
        for rule_name in policy:
            rule = _RULES.get(rule_name)
            if rule is None:
                continue
            decision = rule(cfg, c, ev, target_version)
            if decision is not None:
                break
        if decision is None:
            decision = ResolutionDecision(c, None, "unresolved",
                                          "no rule produced a winner; surfacing both sides")
        decisions.append(decision)

        a_id, b_id = c.evidence_ids[0], c.evidence_ids[1]
        wid = decision.winner_evidence_id
        loser_id = b_id if wid == a_id else (a_id if wid == b_id else None)
        if loser_id:
            demoted.add(loser_id)
        wsrc = ev[wid].chunk.metadata.source_id if wid else "—"
        notes.append(
            f"[{c.type.value}] {c.detail}. Resolution: "
            + (f"prefer {wsrc} ({decision.rule_applied})." if wid else "unresolved — both sides shown.")
        )
        log.info("resolution", subject=c.claim_a.subject, rule=decision.rule_applied, winner=wsrc)

    # re-order: winners & neutral first (keep original score order within), demoted last (kept)
    winners = [e for e in evidence if e.chunk.chunk_id not in demoted]
    losers = [e for e in evidence if e.chunk.chunk_id in demoted]
    for e in losers:
        e.components["demoted_by_resolution"] = True
    return ResolvedContext(
        evidence=winners + losers,
        contradictions=contradictions,
        decisions=decisions,
        conflict_notes=notes,
    )
