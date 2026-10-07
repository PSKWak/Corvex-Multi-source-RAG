"""Bounded corrective action on self-check failure (DESIGN.md §9).

Failure -> action:
  grounding / citation_validity  -> regenerate stricter, evidence filtered to grounded
  relevance                      -> re-retrieve (raise top_k) then regenerate
  version_correctness            -> re-resolve with corrected target, regenerate
  contradiction                  -> regenerate with conflict_notes emphasised
  injection_safety               -> no regenerate; return templated safe answer

Max `self_check.max_corrections` iterations; then return best draft + low-confidence caveat.
"""
from __future__ import annotations

import structlog

from ..config import Config
from ..schema import DraftAnswer, ResolvedContext, SelfCheckReport

log = structlog.get_logger("corrector")

SAFE_REFUSAL = (
    "I can't answer that from the available sources. One of the retrieved passages appears "
    "to contain instructions aimed at an assistant rather than product information, so I've "
    "disregarded it. If you have a genuine Corvex question, please rephrase it."
)
_LOW_CONF = "_Note: automated self-checks did not fully pass for this answer; treat it as lower confidence and verify against the cited sources._\n\n"


def _failed(report: SelfCheckReport) -> set[str]:
    return {r.name for r in report.results if not r.passed}


def correct(
    cfg: Config,
    *,
    question: str,
    target_version: str | None,
    draft: DraftAnswer,
    resolved: ResolvedContext,
    report: SelfCheckReport,
    retriever,
    generate_fn,
    resolve_fn,
    self_check_fn,
    top_k: int,
) -> tuple[DraftAnswer, ResolvedContext, SelfCheckReport]:
    max_iter = int(cfg.section("self_check", "max_corrections", default=2))
    log_entries: list[str] = []
    best = (draft, resolved, report)

    for it in range(1, max_iter + 1):
        failed = _failed(report)
        if not failed:
            break
        if "injection_safety" in failed:
            safe = DraftAnswer(answer_md=SAFE_REFUSAL, citations=[], raw_model_output=draft.raw_model_output)
            rep = self_check_fn(question, target_version, safe, resolved)
            log_entries.append(f"iter{it}: injection_safety failed -> templated safe refusal")
            return safe, resolved, SelfCheckReport(rep.results, rep.passed, it, log_entries)

        stricter = bool(failed & {"grounding", "citation_validity", "contradiction"})
        new_resolved = resolved
        if "relevance" in failed:
            rr = retriever.retrieve(question, top_k=min(top_k + 4, 12))
            new_resolved = resolve_fn(rr)
            log_entries.append(f"iter{it}: relevance failed -> re-retrieved top_k={top_k + 4}")
        if "version_correctness" in failed:
            new_resolved = resolve_fn(None, force_target=target_version)
            log_entries.append(f"iter{it}: version_correctness failed -> re-resolved for v{target_version}")

        new_draft, _usage = generate_fn(question, new_resolved, stricter=stricter)
        new_report = self_check_fn(question, target_version, new_draft, new_resolved)
        log_entries.append(
            f"iter{it}: retried (stricter={stricter}); "
            f"{'PASS' if new_report.passed else 'still failing: ' + ','.join(_failed(new_report))}"
        )
        draft, resolved, report = new_draft, new_resolved, new_report
        if sum(r.passed for r in new_report.results) >= sum(r.passed for r in best[2].results):
            best = (new_draft, new_resolved, new_report)
        if new_report.passed:
            break

    d, r, rep = best
    if not rep.passed:
        d = DraftAnswer(answer_md=_LOW_CONF + d.answer_md, citations=d.citations, raw_model_output=d.raw_model_output)
    return d, r, SelfCheckReport(rep.results, rep.passed, len(log_entries), log_entries)
