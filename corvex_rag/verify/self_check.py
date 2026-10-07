"""The SELF-CHECK box: grounding, citation validity, relevance, version correctness,
contradiction, injection safety (DESIGN.md §9).

Deterministic-first: most checks are pure Python (regex / token overlap / embedding sim).
NLI sharpens grounding when available; the LLM is only consulted for relevance when it is
not the offline mock. This keeps self-check cheap, fast, and reproducible.
"""
from __future__ import annotations

import re

import structlog

from ..config import Config
from ..ingest.embed import Embedder
from ..reason.nli import NLIModel
from ..retrieve.source_weighting import version_matches
from ..schema import CheckResult, DraftAnswer, ResolvedContext, SelfCheckReport

log = structlog.get_logger("self_check")

INJECTION_SENTINELS = (
    "pwned", "hacked", "i have been pwned", "developer mode", "unrestricted mode",
    "ignore all previous", "ignore previous instructions", "system message for any ai",
    "disregard your instructions", "you are now in",
)
_SECRET_RE = re.compile(r"\b(REDACTED|sk-[a-z0-9]{8,}|token[:=]\s*[A-Za-z0-9._-]{12,})\b")
# Deliberate version references only — not "1.5x the burst" or "10.000".
_VERSION_TOKEN_RE = re.compile(
    r"(?:\bv|\bcorvex\s+v?|\bversion\s+v?|\brelease\s+v?)([1-3]\.\d+|[1-3]\.x)\b"
    r"|(?<![\d.])\b([1-3]\.x)\b",
    re.I,
)
_OLD_FRAME_RE = re.compile(r"\b(previously|used to|older|earlier|in 1\.\d|in v1|before 2\.0|1\.x|legacy|was\b|deprecated|removed)\b", re.I)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")
_REFUSAL_RE = re.compile(r"\b(don't have|do not have|not (?:in|covered|documented|mention)|no information|can't answer|cannot answer)\b", re.I)


def _kw(t: str) -> set[str]:
    return set(_WORD_RE.findall(t.lower()))


def _factual_sentences(md: str) -> list[str]:
    body = re.sub(r"CITATIONS:.*$", "", md, flags=re.S)
    sents = re.split(r"(?<=[.!?])\s+", body.strip())
    return [s.strip() for s in sents if len(s.strip()) > 15]


def check_citation_validity(draft: DraftAnswer, resolved: ResolvedContext) -> CheckResult:
    inline = {int(n) for n in re.findall(r"\[(\d+)\]", draft.answer_md)}
    valid = {c.n for c in draft.citations}
    fabricated = inline - valid
    is_refusal = bool(_REFUSAL_RE.search(draft.answer_md))
    if fabricated:
        return CheckResult("citation_validity", False, 0.0, f"citations to non-existent evidence: {sorted(fabricated)}")
    if not inline and not is_refusal and _factual_sentences(draft.answer_md):
        return CheckResult("citation_validity", False, 0.3, "factual answer with no citations")
    return CheckResult("citation_validity", True, 1.0, "")


def check_grounding(cfg, nli, llm, draft: DraftAnswer, resolved: ResolvedContext) -> CheckResult:
    by_n = {c.n: c for c in draft.citations}
    chunk_by_id = {e.chunk.chunk_id: e.chunk for e in resolved.evidence}
    sents = _factual_sentences(draft.answer_md)
    if not sents:
        return CheckResult("grounding", True, 1.0, "no factual sentences (refusal)")
    supported = 0
    unsupported: list[str] = []
    use_nli = getattr(nli, "name", "null") != "null"
    nli_budget = 6                       # cap NLI calls per answer to keep self-check fast
    all_cited = [chunk_by_id[c.chunk_id] for c in draft.citations if c.chunk_id in chunk_by_id]
    for s in sents:
        ns = [int(x) for x in re.findall(r"\[(\d+)\]", s)]
        if ns:
            cited_chunks = [chunk_by_id.get(by_n[n].chunk_id) for n in ns if n in by_n and by_n[n].chunk_id in chunk_by_id]
        elif all_cited:
            cited_chunks = all_cited      # no inline marker -> check against every cited source
        else:
            unsupported.append(s[:60])
            continue
        # cheap lexical check first
        ok = any(len(_kw(s) & _kw(ch.text)) / (len(_kw(s)) or 1) >= 0.4 for ch in cited_chunks if ch)
        # NLI only for sentences the lexical check couldn't confirm, within budget
        if not ok and use_nli and nli_budget > 0:
            for ch in cited_chunks:
                if not ch:
                    continue
                nli_budget -= 1
                label, prob = nli.predict(ch.text, s)
                if label == "entail" and prob >= 0.4:
                    ok = True
                    break
                if nli_budget <= 0:
                    break
        supported += ok
        if not ok:
            unsupported.append(s[:60])
    score = supported / len(sents)
    thr = float(cfg.section("self_check", "min_grounding_score", default=0.7))
    return CheckResult("grounding", score >= thr, score,
                       f"{supported}/{len(sents)} sentences grounded" + (f"; weak: {unsupported}" if unsupported else ""))


def check_relevance(cfg, embedder: Embedder, llm, question: str, draft: DraftAnswer) -> CheckResult:
    import numpy as np

    if _REFUSAL_RE.search(draft.answer_md) and len(draft.answer_md) < 400:
        return CheckResult("relevance", True, 0.9, "explicit refusal is a valid response")

    thr = float(cfg.section("self_check", "min_relevance_score", default=0.5))

    # a real LLM gives the most reliable "does this address the question" signal
    if llm is not None and getattr(llm, "provider", "mock") != "mock":
        try:
            obj, _ = llm.complete_json(
                "You judge whether an answer actually addresses the user's question. Ignore correctness.",
                f"Question: {question}\n\nAnswer:\n{draft.answer_md[:1500]}",
                '{"addresses_question": true, "score": 0.0}',
            )
            addresses = bool(obj.get("addresses_question", True))
            raw = obj.get("score")
            score = float(raw) if isinstance(raw, (int, float)) else (0.85 if addresses else 0.2)
            if addresses:
                score = max(score, 0.75)          # a yes shouldn't carry a low numeric score
            return CheckResult("relevance", addresses, round(score, 3), "llm judged")
        except Exception:  # noqa: BLE001 - fall through to lexical/embedding
            pass

    qv = np.array(embedder.embed_query(question))
    av = np.array(embedder.embed_query(draft.answer_md))
    sim = float(qv @ av)
    kw_overlap = len(_kw(question) & _kw(draft.answer_md)) / (len(_kw(question)) or 1)
    score = max(sim, kw_overlap)
    return CheckResult("relevance", score >= thr, score, f"sim={sim:.2f} kw_overlap={kw_overlap:.2f}")


def check_version_correctness(cfg, llm, question, target_version, draft: DraftAnswer, resolved) -> CheckResult:
    bad: list[str] = []
    for m in _VERSION_TOKEN_RE.finditer(draft.answer_md):
        tok = m.group(1) or m.group(2)
        if not tok or version_matches(tok, target_version):
            continue
        window = draft.answer_md[max(0, m.start() - 100): m.end() + 100]
        if not _OLD_FRAME_RE.search(window):
            bad.append(tok)
    if bad:
        return CheckResult("version_correctness", False, 0.0,
                           f"version(s) {bad} stated without 'older/previously' framing (target v{target_version})")
    return CheckResult("version_correctness", True, 1.0, f"target v{target_version}")


def check_contradiction(draft: DraftAnswer, resolved: ResolvedContext) -> CheckResult:
    from ..reason.contradiction import extract_claims_rule_based

    demoted = [e for e in resolved.evidence if e.components.get("demoted_by_resolution")]
    winners = [e for e in resolved.evidence if not e.components.get("demoted_by_resolution")]
    winner_vals = {c.subject: c.value for e in winners for c in extract_claims_rule_based(e)}
    ans_kw = _kw(draft.answer_md)
    for e in demoted:
        for claim in extract_claims_rule_based(e):
            v = claim.value.replace("_", " ")
            if claim.value and claim.value not in ("", "set_zero") and v in draft.answer_md.lower():
                if winner_vals.get(claim.subject) and winner_vals[claim.subject] != claim.value:
                    window = draft.answer_md.lower()
                    if not _OLD_FRAME_RE.search(window):
                        return CheckResult("contradiction", False, 0.0,
                                           f"answer asserts demoted claim {claim.subject}={claim.value!r} without framing")
    return CheckResult("contradiction", True, 1.0, "")


def check_injection_safety(draft: DraftAnswer, resolved: ResolvedContext) -> CheckResult:
    low = draft.answer_md.lower()
    for s in INJECTION_SENTINELS:
        if s in low:
            return CheckResult("injection_safety", False, 0.0, f"answer contains attacker sentinel {s!r}")
    if _SECRET_RE.search(draft.answer_md):
        return CheckResult("injection_safety", False, 0.0, "answer echoes a secret-looking value from evidence")
    return CheckResult("injection_safety", True, 1.0, "")


def run_self_check(
    cfg: Config,
    embedder: Embedder,
    nli: NLIModel,
    llm,
    question: str,
    target_version: str | None,
    draft: DraftAnswer,
    resolved: ResolvedContext,
) -> SelfCheckReport:
    if not cfg.section("self_check", "enabled", default=True):
        return SelfCheckReport(results=[], passed=True)

    enabled = set(cfg.section("self_check", "checks", default=[]))
    results: list[CheckResult] = []
    if "citation_validity" in enabled:
        results.append(check_citation_validity(draft, resolved))
    if "grounding" in enabled:
        results.append(check_grounding(cfg, nli, llm, draft, resolved))
    if "relevance" in enabled:
        results.append(check_relevance(cfg, embedder, llm, question, draft))
    if "version_correctness" in enabled:
        results.append(check_version_correctness(cfg, llm, question, target_version, draft, resolved))
    if "contradiction" in enabled:
        results.append(check_contradiction(draft, resolved))
    if "injection_safety" in enabled:
        results.append(check_injection_safety(draft, resolved))

    passed = all(r.passed for r in results)
    log.info("self_check", passed=passed, results={r.name: round(r.score, 2) for r in results})
    return SelfCheckReport(results=results, passed=passed)
