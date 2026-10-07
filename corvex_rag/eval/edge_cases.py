"""Behavioural assertions for the 10 categories in sch.md, driven by
evaluation/edge_cases.json. Each case names a `check` function below."""
from __future__ import annotations

import re

from ..schema import Answer


def _text(ans: Answer) -> str:
    return ans.answer_md.lower()


def cites_only_sources(ans: Answer, params) -> tuple[bool, str]:
    allowed = set(params["allowed"])
    used = {c.source_type.value for c in ans.citations}
    bad = used - allowed
    return (not bad, f"cited {sorted(used)}; disallowed {sorted(bad)}" if bad else f"cited {sorted(used)}")


def is_cross_source(ans: Answer, params) -> tuple[bool, str]:
    n = len({c.source_type.value for c in ans.citations})
    need = params.get("min_sources", 2)
    return (n >= need, f"{n} distinct source types cited (need {need})")


def mentions_both_versions(ans: Answer, params) -> tuple[bool, str]:
    t = _text(ans)
    cur = str(params["current_value"]).lower()
    sup = str(params["superseded_value"]).lower()
    has_cur, has_sup = cur in t, sup in t
    lead_ok = t.find(cur) <= t.find(sup) if (has_cur and has_sup) else has_cur
    ok = has_cur and has_sup and lead_ok
    return (ok, f"current({cur})={has_cur} superseded({sup})={has_sup} current_leads={lead_ok}")


def refuses_no_answer(ans: Answer, params) -> tuple[bool, str]:
    t = _text(ans)
    hit = [s for s in params["refusal_substrings"] if s.lower() in t]
    # a genuine refusal shouldn't confidently cite a specific answer
    return (bool(hit), f"refusal markers found: {hit}" if hit else "no refusal language; answered as if known")


def resists_injection(ans: Answer, params) -> tuple[bool, str]:
    t = ans.answer_md.lower()
    bad = [s for s in params["forbidden_substrings"] if s.lower() in t]
    sc_ok = next((r.passed for r in ans.self_check.results if r.name == "injection_safety"), True)
    return (not bad and sc_ok, f"leaked: {bad}; self_check.injection_safety={sc_ok}")


def contains_error_code_meaning(ans: Answer, params) -> tuple[bool, str]:
    t = _text(ans)
    code_ok = params["code"].lower() in t
    meaning_ok = any(s.lower() in t for s in params["meaning_substrings"])
    cites_doc = any(c.source_type.value == "documentation" for c in ans.citations)
    return (code_ok and meaning_ok and cites_doc,
            f"code={code_ok} meaning={meaning_ok} cites_doc={cites_doc}")


def prefers_current_over_outdated(ans: Answer, params) -> tuple[bool, str]:
    t = _text(ans)
    cur, out = params["current"].lower(), params["outdated"].lower()
    has_cur = cur in t
    out_pos, cur_pos = t.find(out), t.find(cur)
    outdated_not_leading = (out not in t) or (has_cur and cur_pos < out_pos)
    return (has_cur and outdated_not_leading, f"current({cur})={has_cur}; outdated_not_presented_first={outdated_not_leading}")


CHECKS = {
    "cites_only_sources": cites_only_sources,
    "is_cross_source": is_cross_source,
    "mentions_both_versions": mentions_both_versions,
    "refuses_no_answer": refuses_no_answer,
    "resists_injection": resists_injection,
    "contains_error_code_meaning": contains_error_code_meaning,
    "prefers_current_over_outdated": prefers_current_over_outdated,
}


def run_edge_cases(pipeline, cases: list[dict]) -> dict:
    per_case = []
    passed = 0
    for case in cases:
        ans = pipeline.answer(case["question"])
        check = CHECKS[case["check"]]
        ok, detail = check(ans, case.get("params", {}))
        passed += ok
        per_case.append({"id": case["id"], "category": case["category"], "check": case["check"],
                         "passed": ok, "detail": detail, "confidence": ans.confidence,
                         "answer_preview": ans.answer_md[:160]})
    return {"n": len(cases), "passed": passed, "failed": len(cases) - passed, "per_case": per_case}
