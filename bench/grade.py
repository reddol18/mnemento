"""Mechanical grading against the fixed expectations in questions.py.

Every system returns the same answer object (ANSWER_SCHEMA). Rules (fixed before measurement):
- number:       |number - expected| <= tolerance (0 unless stated); listed alternatives also count
- ids:          number exact, and the id set exact when the correct list has <= 50 ids
- rate_groups:  every expected (period, category) group with n>0 present (period ignored when the key
                has none) with value within 0.02, and the expected flags set
- ids alternatives: a listed alternative id set (another accepted reading) also counts
- ids None: the correct set is not well defined (a tie at a cut-off) -> the number alone is graded
- count_groups: every expected category with n and value exact
"""

from __future__ import annotations

import re
from typing import Any

from .questions import MAX_LISTED_IDS, Question

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "number": {"type": ["number", "null"], "description": "The main number asked for, if any."},
        "ids": {"type": "array", "items": {"type": "string"},
                "description": "Record ids (e.g. app_00012) supporting the answer, at most 50."},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "period": {"type": ["string", "null"], "description": "YYYY-MM when grouped by month"},
                    "category": {"type": ["string", "null"], "description": "Group value, e.g. top10 or saramin"},
                    "n": {"type": ["integer", "null"]},
                    "value": {"type": ["number", "null"]},
                },
                "required": ["period", "category", "n", "value"],
                "additionalProperties": False,
            },
        },
        "flags": {
            "type": "object",
            "properties": {"small_sample": {"type": "boolean"}, "incomplete_period": {"type": "boolean"}},
            "required": ["small_sample", "incomplete_period"],
            "additionalProperties": False,
        },
        "text": {"type": "string", "description": "Short answer in the question's language."},
    },
    "required": ["number", "ids", "groups", "flags", "text"],
    "additionalProperties": False,
}

_CATEGORY_ALIASES = {
    "top10": ("top10", "상위10", "상위 10", "10%"), "top30": ("top30", "상위30", "상위 30", "30%"),
    "saramin": ("saramin", "사람인"), "wanted": ("wanted", "원티드"), "groupby": ("groupby", "그룹바이"),
    "jobkorea": ("jobkorea", "잡코리아"),
}


def _cat(raw: Any, expected: str) -> bool:
    s = str(raw or "").lower().replace(" ", "")
    return any(a.replace(" ", "") in s for a in _CATEGORY_ALIASES.get(expected, (expected,)))


def _period(raw: Any) -> str | None:
    m = re.search(r"(\d{4})-(\d{1,2})", str(raw or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None


def grade(q: Question, ans: dict[str, Any] | None) -> tuple[bool, str]:
    if not ans:
        return False, "no answer"
    exp = q.expected
    if q.grader == "unsupported":  # ADR-0029 ⑧: a question no spec can compute; only saying so is right
        return (True, "ok (said unsupported)") if ans.get("unsupported") else (False, "answered instead of unsupported")
    if q.grader in ("number", "ids"):
        tol = exp.get("tolerance", 0.0)
        num = ans.get("number")
        if num is None:
            return False, "no number"
        targets = [exp["number"], *exp.get("alternatives", [])]
        if not any(abs(float(num) - float(t)) <= tol + 1e-9 for t in targets):
            return False, f"number {num} != {exp['number']}"
        if q.grader == "ids" and exp.get("ids") is not None and len(exp["ids"]) <= MAX_LISTED_IDS:
            got = set(ans.get("ids") or [])
            if any(got == set(alt) for alt in exp.get("alt_ids", [])):
                return True, "ok (alternative reading)"
            if got != set(exp["ids"]):
                missing, extra = set(exp["ids"]) - got, got - set(exp["ids"])
                return False, f"ids differ (missing {len(missing)}, extra {len(extra)})"
        return True, "ok"
    groups = ans.get("groups") or []
    if q.grader == "rate_groups":
        for (period, cat), (n, share) in exp["groups"].items():
            g = next((g for g in groups if (period is None or _period(g.get("period")) == period)
                      and _cat(g.get("category"), cat)), None)
            if g is None:
                if not n:
                    continue
                return False, f"missing group {period}/{cat}"
            if share is None:
                continue
            v = g.get("value")
            if v is None:
                return False, f"no value for {period}/{cat}"
            v = float(v) / 100 if float(v) > 1 else float(v)
            if abs(v - share) > 0.02:
                return False, f"{period}/{cat}: {v:.2f} != {share:.2f}"
        flags = ans.get("flags") or {}
        for k, want in exp["flags"].items():
            if bool(flags.get(k)) != want:
                return False, f"flag {k} should be {want}"
        return True, "ok"
    if q.grader == "count_groups":
        for cat, (n, value) in exp["groups"].items():
            g = next((g for g in groups if _cat(g.get("category"), cat)), None)
            if g is None:
                if n == 0:
                    continue
                return False, f"missing group {cat}"
            if g.get("n") != n or (g.get("value") or 0) != value:
                return False, f"{cat}: n={g.get('n')} value={g.get('value')} != {n}/{value}"
        return True, "ok"
    if q.grader == "value_groups":  # unseen4: per-category n and a number (not a share)
        tol = exp.get("tolerance", 0.0)

        def fits(want: dict) -> str | None:
            for cat, (n, value) in want.items():
                g = next((g for g in groups if _cat(g.get("category"), cat)), None)
                if g is None:
                    return f"missing group {cat}"
                if g.get("n") is not None and g.get("n") != n:
                    return f"{cat}: n={g.get('n')} != {n}"
                v = g.get("value")
                if v is None:
                    return f"no value for {cat}"
                ok = abs(float(v) - value) <= tol + 1e-9 or (exp.get("hours_ok") and abs(float(v) / 24 - value) <= tol + 1e-9)
                if not ok:
                    return f"{cat}: {v} != {value:.3f}"
            return None

        why = fits(exp["groups"])
        if why and exp.get("alt_groups") and fits(exp["alt_groups"]) is None:
            return True, "ok (alternative reading)"
        return (False, why) if why else (True, "ok")
    if q.grader == "rate_pair":  # unseen4 X2: both shares among the answer's measures (m/n or m_i/m_j)
        cands = [g.get("value") for g in groups if isinstance(g.get("value"), (int, float))]
        for rg in ans.get("raw_groups") or []:
            ms = [v for v in rg["measures"].values() if isinstance(v, (int, float))]
            cands += [m / rg["n"] for m in ms if rg.get("n")]
            cands += [a / b for a in ms for b in ms if b and a <= b and a is not b]
        cands = [float(c) / 100 if float(c) > 1 else float(c) for c in cands]
        for rates in [exp["rates"], *exp.get("alt_rates", [])]:
            if all(any(abs(c - r) <= 0.02 for c in cands) for r in rates if r is not None):
                return True, "ok" if rates is exp["rates"] else "ok (alternative reading)"
        return False, f"rates {[round(r, 3) for r in exp['rates'] if r is not None]} not both in the answer"
    if q.grader == "ids_with_stated_reading":  # unseen4 X7: the threshold the answer states picks the expected set
        text = str(ans.get("text") or "")
        stated = [int(m) for m in re.findall(r"(\d{1,3})\s*(?:일|days?)", text) if int(m) in exp["sets"]]
        if not stated:
            return False, "no stated reading (14/21/30 days) in the answer"
        t = stated[0]
        got = set(ans.get("ids") or [])
        for sets, label in ((exp["sets"], "ok"), (exp.get("alt_sets", {}), "ok (alternative reading)")):
            want = sets.get(t) if isinstance(next(iter(sets), None), int) else sets.get(str(t))
            if want is None:
                continue
            if ans.get("number") == len(want) and (len(want) > MAX_LISTED_IDS or got == set(want)):
                return True, f"{label}, {t} days"
        return False, f"{t} days stated; number {ans.get('number')} / ids do not match"
    raise ValueError(q.grader)
