"""Benchmark questions and their correct answers, computed from the generator's ground truth.

Two sets (ADR-0008, review condition of task 0003):
- dev    — the docs/eval Q1–Q7 shapes; Mnemento was developed against these
- unseen — rephrased and new questions, written before any measurement and never used for tuning

Every question carries a short `format` hint that all systems receive identically, so grading can be
mechanical. Grading rules are fixed here, before measurement.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .generate import SEARCH_FIRM_REASON, Dataset

MAX_LISTED_IDS = 50  # ids are graded only when the correct list is at most this long


@dataclass
class Question:
    id: str
    set: str  # "dev" | "unseen"
    text: str
    format: str  # answer-format hint shown to every system
    grader: str  # number | ids | rate_groups | count_groups | value_groups | unsupported | ...
    expected: dict[str, Any] = field(default_factory=dict)


def _ym(d: date) -> str:
    return d.isoformat()[:7]


def _prev_month(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def build(ds: Dataset) -> list[Question]:
    qs = _build_v0_shapes(ds)
    if ds.version == "v2":
        # evaluation set v2: every v0 question was seen during v1 development -> dev;
        # unseen questions come from the directing agent (docs/eval/), added before measuring
        for q in qs:
            q.set = "dev"
        qs += build_unseen_v2(ds)
    elif ds.version == "v3":
        # evaluation set v3: the v0 and v2 questions were all seen (v1/v1.1 development) -> dev
        qs += build_unseen_v2(ds)
        for q in qs:
            q.set = "dev"
        qs += build_unseen_v3(ds)
        qs += build_unseen_v4(ds)  # set "unseen4" (issue #13)
        qs += build_unseen_v5(ds)  # set "unseen5" (issue #16)
        qs += build_unseen_v6(ds)  # set "unseen6" (issue #20, ADR-0029)
        qs += build_dev_spans(ds)  # set "dev" (issue #21, ADR-0030)
    return qs


def build_unseen_v3(ds: Dataset) -> list[Question]:
    """Unseen questions of evaluation set v3, written by the directing agent (docs/eval/unseen-v3.md).
    Wording is kept verbatim; only the answer keys are computed here, from the generator's ground truth.
    Dates are those of the v3 reference date (2027-02-01, a Monday) and are derived from `today`."""
    live = ds.live
    today = ds.now.date()
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    qs: list[Question] = []

    last_dec = date(today.year - 1, 12, 1)
    hit = [a for a in live if a.platform in ("saramin", "wanted") and _ym(a.applied_date) == _ym(last_dec)]
    qs.append(Question("W1", "unseen", "작년 12월에 사람인이랑 원티드 합쳐서 몇 군데 넣었어?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))

    this_monday = today - timedelta(days=today.weekday())
    week_start, yesterday = this_monday - timedelta(days=7), today - timedelta(days=1)
    hit = [a for a in live if a.status == "viewed" and week_start <= a.viewed_at.date() <= yesterday]
    qs.append(Question("W2", "unseen", "지난주에 열람됐는데 아직 결과 안 나온 지원 목록 줘",
                       "number = how many; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    start, end = date(today.year - 1, 12, 24), date(today.year, 1, 3)
    hit = [a for a in live if a.status == "passed" and start <= a.applied_date <= end]
    qs.append(Question("W3", "unseen", "연말연시(12월 24일부터 1월 3일까지)에 지원한 것 중에 서류 합격한 거 있어?",
                       "number = how many; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    groups, small = {}, False
    for p in ("saramin", "wanted", "groupby", "jobkorea"):
        g = [a for a in live if a.platform == p]
        groups[(None, p)] = (len(g), sum(1 for a in g if a.status == "passed") / len(g) if g else None)
        small |= len(g) < 5
    qs.append(Question("W4", "unseen", "서류 합격률이 제일 높은 플랫폼이 어디야?",
                       "groups = one per platform with category=platform, n = applications, value = share passed "
                       "(0-1); flags: small_sample if any group has n<5", "rate_groups",
                       {"groups": groups, "flags": {"small_sample": small}}))

    viewed = [a for a in live if a.viewed_at]
    by_hours = [a for a in viewed if (a.viewed_at - a.applied).total_seconds() > 72 * 3600]
    days = lambda a: (a.viewed_at.date() - a.applied_date).days  # noqa: E731
    qs.append(Question("W5", "unseen", "열람되기까지 사흘 넘게 걸린 지원은 몇 건이야?",
                       "number = count", "number",
                       {"number": len(by_hours),
                        "alternatives": [sum(1 for a in viewed if days(a) > 3), sum(1 for a in viewed if days(a) >= 3)]}))

    plats: dict[str, set] = {}
    for a in live:
        plats.setdefault(a.company_id, set()).add(a.platform)
    both = sorted(cid for cid, ps in plats.items() if {"wanted", "saramin"} <= ps)
    qs.append(Question("W6", "unseen", "같은 회사에 원티드랑 사람인 둘 다로 지원한 적 있어?",
                       "number = how many companies; ids = those companies' ids (co_...)", "ids",
                       {"number": len(both), "ids": both}))

    hit = [a for a in live if a.applied_date == yesterday]
    qs.append(Question("W7", "unseen", "어제 지원한 거 몇 개야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.viewed_at and a.viewed_at.date() >= today.replace(day=1)]
    qs.append(Question("W8", "unseen", "이번 달 들어서 열람된 거 있어?",
                       "number = how many; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.status != "withdrawn"]
    qs.append(Question("W9", "unseen", "내가 취소한 거 빼고 지금까지 지원 총 몇 건이야?",
                       "number = count", "number", {"number": len(hit)}))
    return qs


def build_unseen_v4(ds: Dataset) -> list[Question]:
    """Unseen questions "v4" (issue #13) on the v3 data, written by the directing agent without seeing the code
    (docs/eval/unseen-v4.md). Set name "unseen4" so dev regressions do not pick them up. Wording verbatim; only the
    answer keys are computed here. X6 (posting deadlines) is dropped: the generator has no postings."""
    live = ds.live
    today = ds.now.date()
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    hours = lambda a: (a.viewed_at - a.applied).total_seconds() / 3600  # noqa: E731
    ddays = lambda a: (a.viewed_at.date() - a.applied_date).days  # noqa: E731
    viewed = [a for a in live if a.viewed_at]
    qs: list[Question] = []

    fast_h = [a for a in viewed if hours(a) <= 24]
    fast_d = [a for a in viewed if ddays(a) <= 1]
    qs.append(Question("X1", "unseen4", "빠르게 열람된 지원은 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(fast_h), "ids": ids(fast_h), "alternatives": [len(fast_d)]}))

    rate = lambda g: sum(1 for a in g if a.status == "passed") / len(g) if g else None  # noqa: E731
    qs.append(Question("X2", "unseen4", "전체 서류 합격률이랑, 빠르게 열람된 것만 따로 본 합격률을 같이 알려줘",
                       "groups = two groups with category=subset (\"all\", \"fast_viewed\"), n = applications, "
                       "value = share passed (0-1); flags: small_sample if any group has n<5", "rate_pair",
                       {"rates": [rate(live), rate(fast_h)], "alt_rates": [[rate(live), rate(fast_d)]]}))

    hit = [a for a in live if a.status == "applied" and not a.viewed_at and (today - a.applied_date).days > 14]
    alt = [a for a in live if a.status == "applied" and not a.viewed_at and (today - a.applied_date).days >= 14]
    qs.append(Question("X3", "unseen4", "지원한 지 2주 넘었는데 아직 열람도 안 된 거 몇 개야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit), "alternatives": [len(alt)]}))

    groups, alt_groups = {}, {}
    for p in ("saramin", "wanted", "groupby", "jobkorea", "other"):
        g = [a for a in viewed if a.platform == p]
        if g:
            groups[p] = (len(g), statistics.mean(hours(a) / 24 for a in g))
            alt_groups[p] = (len(g), statistics.mean(ddays(a) for a in g))
    qs.append(Question("X4", "unseen4", "플랫폼별로 열람까지 평균 며칠 걸렸어?",
                       "groups = one per platform with category=platform, n = viewed applications, "
                       "value = mean days (float)", "value_groups",
                       {"groups": groups, "alt_groups": alt_groups, "tolerance": 0.1, "hours_ok": True}))

    jan = [a for a in live if a.applied_date.year == today.year and a.applied_date.month == 1]
    groups, alt_groups = {}, {}
    for p in ("saramin", "wanted", "groupby", "jobkorea", "other"):
        g = [a for a in jan if a.platform == p]
        if g:
            groups[p] = (len(g), sum(1 for a in g if a.viewed_at and hours(a) <= 168))
            alt_groups[p] = (len(g), sum(1 for a in g if a.viewed_at and ddays(a) <= 7))
    qs.append(Question("X5", "unseen4", "1월에 넣은 지원 수랑, 그중 일주일 안에 열람된 수를 플랫폼별로 보여줘",
                       "groups = one per platform with category=platform, n = applications applied in Jan 2027, "
                       "value = count viewed within 7 days", "value_groups",
                       {"groups": groups, "alt_groups": alt_groups, "tolerance": 0.0}))

    open_ = [a for a in live if a.status in ("applied", "viewed")]
    sets = {t: ids([a for a in open_ if (today - a.applied_date).days > t]) for t in (14, 21, 30)}
    any_ = {t: ids([a for a in live if (today - a.applied_date).days > t]) for t in (14, 21, 30)}
    qs.append(Question("X7", "unseen4", "오래 묵혀둔 지원 목록 줘",
                       "number = how many; ids = those applications", "ids_with_stated_reading",
                       {"sets": sets, "alt_sets": any_}))

    results = []
    for a in live:
        ev = next((e for e in a.events if e.to in ("passed", "rejected")), None)
        if ev:
            results.append(a)
    rdays = lambda a: (next(e for e in a.events if e.to in ("passed", "rejected")).at - a.applied  # noqa: E731
                       ).total_seconds() / 86400
    rdd = lambda a: (next(e for e in a.events if e.to in ("passed", "rejected")).at.date() - a.applied_date).days  # noqa: E731
    qs.append(Question("X8", "unseen4", "서류 결과 나오기까지 보통 며칠 걸려?",
                       "number = mean days (float)", "number",
                       {"number": round(statistics.mean(rdays(a) for a in results), 4), "tolerance": 0.1,
                        "alternatives": [round(statistics.median(rdays(a) for a in results), 4),
                                         round(statistics.mean(rdd(a) for a in results), 4)]}))
    return qs


def build_unseen_v5(ds: Dataset) -> list[Question]:
    """Unseen questions "v5" (issue #16: hours vs days) on the v3 data, written by the directing agent without seeing
    the code (docs/eval/unseen-v5.md). Set "unseen5". A value in a unit the question did not ask for is wrong even if
    it converts (the author's rule): no unit conversion is accepted here."""
    live = ds.live
    today = ds.now.date()
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    first = lambda a, sts: next((e.at for e in a.events if e.to in sts), None)  # noqa: E731
    h = lambda t0, t1: (t1 - t0).total_seconds() / 3600  # noqa: E731
    viewed = [a for a in live if a.viewed_at]
    res = [a for a in live if first(a, ("passed", "rejected"))]
    qs: list[Question] = []

    qs.append(Question("Y1", "unseen5", "지원하고 열람되기까지 평균 몇 시간 걸렸어?", "number = mean hours (float)", "number",
                       {"number": statistics.mean(h(a.applied, a.viewed_at) for a in viewed), "tolerance": 1.0,
                        "alternatives": [statistics.mean((a.viewed_at.date() - a.applied_date).days * 24
                                                         for a in viewed)]}))

    groups, alt = {}, {}
    for p in ("saramin", "wanted", "groupby", "jobkorea", "other"):
        g = [a for a in res if a.platform == p]
        if g:
            groups[p] = (len(g), statistics.mean(h(a.applied, first(a, ("passed", "rejected"))) / 168 for a in g))
            alt[p] = (len(g), statistics.mean((first(a, ("passed", "rejected")).date() - a.applied_date).days / 7
                                              for a in g))
    qs.append(Question("Y2", "unseen5", "플랫폼별로 지원부터 결과 나오기까지 평균 몇 주 걸렸어?",
                       "groups = one per platform with category=platform, n = applications with a result, "
                       "value = mean weeks (float)", "value_groups",
                       {"groups": groups, "alt_groups": alt, "tolerance": 0.05}))

    hit = [a for a in res if h(a.applied, first(a, ("passed", "rejected"))) <= 48]
    alt_hit = [a for a in res if (first(a, ("passed", "rejected")).date() - a.applied_date).days <= 2]
    qs.append(Question("Y3", "unseen5", "지원하고 48시간 안에 합격이든 불합격이든 결과가 나온 건 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit), "alternatives": [len(alt_hit)]}))

    qs.append(Question("Y4", "unseen5", "열람될 때까지 기다린 시간을 전부 합치면 며칠이야?", "number = total days (float)",
                       "number", {"number": sum(h(a.applied, a.viewed_at) for a in viewed) / 24, "tolerance": 0.5,
                                  "alternatives": [sum((a.viewed_at.date() - a.applied_date).days for a in viewed)]}))

    both = [a for a in res if first(a, ("passed", "rejected")) >= a.viewed_at] if all(a.viewed_at for a in res) else \
        [a for a in res if a.viewed_at and first(a, ("passed", "rejected")) >= a.viewed_at]
    qs.append(Question("Y5", "unseen5", "열람되고 나서 결과가 나오기까지는 평균 며칠 걸렸어?", "number = mean days (float)",
                       "number", {"number": statistics.mean(h(a.viewed_at, first(a, ("passed", "rejected"))) / 24
                                                            for a in both), "tolerance": 0.1,
                                  "alternatives": [statistics.mean((first(a, ("passed", "rejected")).date()
                                                                    - a.viewed_at.date()).days for a in both)]}))

    open_ = [a for a in live if a.status in ("applied", "viewed")]
    hit = [a for a in open_ if (today - a.applied_date).days > 21]
    alt1 = [a for a in open_ if (today - a.applied_date).days >= 21]
    midnight = ds.now.replace(hour=0, minute=0, second=0, microsecond=0)
    alt2 = [a for a in open_ if h(a.applied, midnight) > 504]
    qs.append(Question("Y6", "unseen5", "지원한 지 3주가 넘었는데 아직 끝나지 않은 지원은 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit), "alternatives": [len(alt1), len(alt2)]}))
    return qs


def build_unseen_v6(ds: Dataset) -> list[Question]:
    """Unseen questions "v6" (issue #20: composable measures, ADR-0029) on the v3 data, written by the directing agent
    without seeing the code (docs/eval/unseen-v6.md). Set "unseen6". As in v5, no unit conversion is accepted. Z8 and
    Z9 cannot be expressed: only an honest "unsupported" is right."""
    live = ds.live
    today = ds.now.date()
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    first = lambda a, sts: next((e.at for e in a.events if e.to in sts), None)  # noqa: E731
    h = lambda t0, t1: (t1 - t0).total_seconds() / 3600  # noqa: E731
    result = lambda a: first(a, ("passed", "rejected"))  # noqa: E731
    res = [a for a in live if result(a)]
    viewed = [a for a in live if a.viewed_at]
    qs: list[Question] = []

    qs.append(Question("Z1", "unseen6", "합격이든 불합격이든 서류 결과가 나오기까지 걸린 기간의 중앙값은 며칠이야?",
                       "number = median days (float)", "number",
                       {"number": statistics.median(h(a.applied, result(a)) / 24 for a in res), "tolerance": 0.1}))

    took = {a.id: h(a.applied, a.viewed_at) for a in viewed}
    longest = max(took.values())
    qs.append(Question("Z2", "unseen6", "열람되기까지 가장 오래 걸린 시간은 몇 시간이었어?", "number = hours (float)",
                       "number", {"number": longest, "tolerance": 0.5}))
    top = sorted(i for i, v in took.items() if abs(v - longest) < 1e-9)
    qs.append(Question("Z3", "unseen6", "열람되기까지 가장 오래 걸린 지원은 어느 회사 거야?",
                       "number = 1; ids = that application", "ids",
                       {"number": 1, "ids": top[:1], "alt_ids": [[i] for i in top[1:]]}))  # a tie: any one of them

    wanted = [a for a in res if a.platform == "wanted"]
    qs.append(Question("Z4", "unseen6", "원티드로 낸 지원은 서류 결과까지 평균 몇 주 걸렸어?", "number = mean weeks (float)",
                       "number", {"number": statistics.mean(h(a.applied, result(a)) / 168 for a in wanted)
                                  if wanted else None, "tolerance": 0.05}))

    groups = {}
    for p in ("saramin", "wanted", "groupby", "jobkorea", "other"):
        g = [a for a in viewed if a.platform == p]
        if g:
            groups[p] = (len(g), sum(took[a.id] for a in g) / 24)
    qs.append(Question("Z5", "unseen6", "열람될 때까지 기다린 시간을 플랫폼별로 다 합치면 각각 며칠이야?",
                       "groups = one per platform with category=platform, n = viewed applications, "
                       "value = total days (float)", "value_groups", {"groups": groups, "tolerance": 0.5}))

    not_decided = [a for a in live if a.status not in ("passed", "rejected")]
    hit = [a for a in not_decided if (today - a.applied_date).days > 10]
    alt = [a for a in not_decided if (today - a.applied_date).days >= 10]
    no_wd = [a for a in hit if a.status != "withdrawn"]
    qs.append(Question("Z6", "unseen6", "합격이나 불합격 상태가 아닌 지원 중에 지원한 지 열흘 넘은 건 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit), "alternatives": [len(alt)],
                        # leaving out the withdrawn ones counts only when the answer says it read the words so
                        "stated_alternatives": [[len(no_wd), ["withdrawn", "철회", "취소"]],
                                                [len([a for a in alt if a.status != "withdrawn"]),
                                                 ["withdrawn", "철회", "취소"]]]}))

    ever = [a for a in live if a.platform == "saramin" and (a.viewed_at or first(a, ("viewed", "passed")))]
    qs.append(Question("Z7", "unseen6", "한 번이라도 열람되거나 합격한 적 있는 사람인 지원은 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(ever), "ids": ids(ever)}))

    qs.append(Question("Z8", "unseen6", "지원과 다음 지원 사이 간격은 평균 며칠이었어?", "status = unsupported",
                       "unsupported"))
    qs.append(Question("Z9", "unseen6", "회사 한 곳당 평균 몇 번 지원했어?", "status = unsupported", "unsupported"))
    return qs


def build_dev_spans(ds: Dataset) -> list[Question]:
    """dev questions for issue #21 (ADR-0030, from the directing agent's check): a total and an average of the time
    from applying to the first view, worded without naming the dates. The answer is from the exact event times;
    the whole days between the dates miss the tolerance (all: 47 vs about 44.2; saramin mean: 1.93 vs 1.81)."""
    viewed = [a for a in ds.live if a.viewed_at]
    days = lambda a: (a.viewed_at - a.applied).total_seconds() / 86400  # noqa: E731
    saramin = [days(a) for a in viewed if a.platform == "saramin"]
    return [
        Question("E1", "dev", "지원하고 열람될 때까지 기다린 시간을 전부 합치면 며칠이야?", "number = total days (float)",
                 "number", {"number": sum(days(a) for a in viewed), "tolerance": 0.5}),
        Question("E2", "dev", "사람인으로 낸 지원은 열람까지 평균 며칠 걸렸어?", "number = mean days (float)", "number",
                 {"number": statistics.mean(saramin) if saramin else None, "tolerance": 0.05}),
    ]


def build_unseen_v2(ds: Dataset) -> list[Question]:
    """Unseen questions of evaluation set v2, written by the directing agent (docs/eval/unseen-v2.md).
    Wording is kept verbatim; only the answer keys are computed here, from the generator's ground truth."""
    live = ds.live
    today = ds.now.date()
    prev = _prev_month(today)
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    qs: list[Question] = []

    hit = [a for a in live if a.platform == "groupby" and _ym(a.applied_date) == _ym(prev) and a.viewed_at]
    qs.append(Question("V1", "unseen", "지난달에 그룹바이로 낸 지원 중에 회사가 한 번이라도 열어본 건 몇 개야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))

    per_company: dict[str, list] = {}
    for a in live:
        per_company.setdefault(a.company_id, []).append(a)
    candidates = [c for c in ds.companies if c.aliases
                  and any(a.company_label for a in per_company.get(c.id, []))
                  and any(not a.company_label for a in per_company.get(c.id, []))]
    if candidates:  # otherwise the question is excluded at this scale (reported)
        c = sorted(candidates, key=lambda c: (-len(per_company[c.id]), c.id))[0]
        hit = per_company[c.id]
        qs.append(Question("V2", "unseen", f"{c.normalized}에 지원한 거 전부 몇 건이야?",
                           "number = how many applications; ids = those applications", "ids",
                           {"number": len(hit), "ids": ids(hit)}))

    never = sorted(cid for cid, apps in per_company.items()
                   if len(apps) >= 2 and all(a.viewed_at is None for a in apps))
    qs.append(Question("V3", "unseen", "두 번 이상 지원했는데 단 한 번도 열람된 적 없는 회사는 어디야?",
                       "number = how many companies; ids = those companies' ids (co_...)", "ids",
                       {"number": len(never), "ids": never}))

    viewed = sorted((a for a in live if a.viewed_at), key=lambda a: a.viewed_at, reverse=True)
    top = viewed[:5]
    tie = len(viewed) > 5 and viewed[4].viewed_at == viewed[5].viewed_at
    qs.append(Question("V4", "unseen", "최근에 열람된 순서대로 다섯 건만 보여줘",
                       "number = 5 (or fewer); ids = those applications", "ids",
                       {"number": len(top), "ids": None if tie else ids(top)}))  # tie at the 5th: number only

    groups, small = {}, False
    for p in ("saramin", "wanted", "groupby", "jobkorea"):
        g = [a for a in live if a.platform == p]
        groups[(None, p)] = (len(g), sum(1 for a in g if a.status == "rejected") / len(g) if g else None)
        small |= len(g) < 5
    qs.append(Question("V5", "unseen", "플랫폼별로 불합격 통보까지 받은 비율 좀 비교해줘",
                       "groups = one per platform with category=platform, n = applications, value = share "
                       "rejected (0-1); flags: small_sample if any group has n<5", "rate_groups",
                       {"groups": groups, "flags": {"small_sample": small}}))

    if today == date(2026, 12, 4):  # V6 names fixed dates; excluded for any other reference date
        def week(start: date, end: date) -> list:
            return [a for a in live if a.status in ("applied", "viewed") and start <= a.applied_date <= end]
        mon_sun = week(date(2026, 11, 23), date(2026, 11, 29))
        last7 = week(date(2026, 11, 24), date(2026, 11, 30))
        qs.append(Question("V6", "unseen", "11월 마지막 주에 지원한 것 중에 아직 진행 중인 건 몇 개야?",
                           "number = count; ids = the applications counted", "number",
                           {"number": len(mon_sun), "ids": ids(mon_sun), "alternatives": [len(last7)],
                            "alt_ids": [ids(last7)]}))

    hit = [a for a in live if a.status == "withdrawn"]
    qs.append(Question("V7", "unseen", "내가 직접 지원 취소한 건 몇 건이고 다 무슨 이유였어?",
                       "number = how many; ids = those applications; text = reasons", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.platform == "jobkorea" and a.status == "passed"]
    qs.append(Question("V8", "unseen", "잡코리아로 서류 합격한 적 있어?",
                       "number = how many; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    this_monday = today - timedelta(days=today.weekday())
    start, end = this_monday - timedelta(days=7), today - timedelta(days=1)
    hit = [a for a in live if a.viewed_at and start <= a.viewed_at.date() <= end]
    qs.append(Question("V9", "unseen", "지난주 월요일부터 어제까지 열람된 지원 몇 건이야?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))
    return qs


def _build_v0_shapes(ds: Dataset) -> list[Question]:
    live = ds.live
    today = ds.now.date()
    qs: list[Question] = []
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731

    # ---- dev: docs/eval Q1-Q7 ------------------------------------------------------------
    by_day = Counter(a.applied_date for a in live if a.platform == "saramin")
    d1 = max(sorted(by_day), key=lambda d: by_day[d])
    hit = [a for a in live if a.platform == "saramin" and a.applied_date == d1]
    qs.append(Question("D1", "dev", f"{d1.month}/{d1.day} 사람인 지원 몇 곳?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))

    aliased = [c for c in ds.companies if c.aliases]
    per_company = Counter(a.company_id for a in live)
    aliased.sort(key=lambda c: (-per_company[c.id], c.id))
    c2 = aliased[0]
    hit = [a for a in live if a.company_id == c2.id]
    qs.append(Question("D2", "dev", f"{c2.aliases[0]} 예전에 지원한 적 있나?",
                       "number = how many applications to that company; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.status == "viewed"]
    qs.append(Question("D3", "dev", "열람됐는데 결과 없는 곳은?",
                       "number = how many; ids = those applications (at most 50)", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    m_now, m_prev = _ym(today), _ym(_prev_month(today))
    groups = {}
    small = False
    for month in (m_prev, m_now):
        for rate in ("top10", "top30"):
            g = [a for a in live if _ym(a.applied_date) == month and a.rate == rate]
            viewed = [a for a in g if a.viewed_at is not None]
            groups[(month, rate)] = (len(g), len(viewed) / len(g) if g else None)
            small |= len(g) < 5
    qs.append(Question(
        "D4", "dev", "예상 합격률 상위 10%가 30%보다 먼저 열람되나? 지난번에도 그랬나?",
        "groups = one per (month, expected rate) for the current and the previous month, with period=YYYY-MM, "
        "category=top10|top30, n = applications, "
        "value = share viewed (0-1); flags: small_sample if any group has n<5, incomplete_period if the "
        "current month is included", "rate_groups",
        {"groups": groups, "flags": {"incomplete_period": True, "small_sample": small}}))

    hours = [(a.viewed_at - a.applied).total_seconds() / 3600 for a in live if a.viewed_at]
    qs.append(Question("D5", "dev", "열람까지 평균 몇 시간 걸렸나? 빠르게 열람된 곳의 결과는?",
                       "number = average hours from application to first view", "number",
                       {"number": round(statistics.mean(hours), 2), "tolerance": 2.0}))

    hit = [a for a in ds.apps if a.recorded_applied_date]
    qs.append(Question("D6", "dev", "지원 개수를 정정한 적 있나? 왜?",
                       "number = how many records were corrected; ids = those applications; text = why", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.reason == SEARCH_FIRM_REASON and a.status == "withdrawn"]
    qs.append(Question("D7", "dev", "서치펌 공고로 확인돼 패스한 곳은?",
                       "number = how many; ids = those applications (at most 50)", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    # ---- unseen: rephrased + new --------------------------------------------------------
    prev = _prev_month(today)
    hit = [a for a in live if a.platform == "wanted" and _ym(a.applied_date) == _ym(prev)]
    qs.append(Question("U1", "unseen", f"{prev.month}월에 원티드로 몇 군데 지원했지?",
                       "number = count; ids = the applications counted", "number",
                       {"number": len(hit), "ids": ids(hit)}))

    c_u2 = aliased[1] if len(aliased) > 1 else aliased[0]
    hit = [a for a in live if a.company_id == c_u2.id]
    qs.append(Question("U2", "unseen", f"{c_u2.aliases[0]} 쪽에 넣은 지원서 상태 알려줘",
                       "number = how many applications; ids = those applications; text = their statuses", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    hit = [a for a in live if a.status == "rejected" and a.platform == "saramin"]
    qs.append(Question("U3", "unseen", "불합격 통보 받은 곳 중 사람인 공고는 몇 개야?",
                       "number = count", "number", {"number": len(hit)}))

    # "more than a week": applied 7+ or 8+ days ago, both readings accepted
    a7 = [a for a in live if a.status == "applied" and a.applied_date <= today - timedelta(days=7)]
    a8 = [a for a in live if a.status == "applied" and a.applied_date <= today - timedelta(days=8)]
    qs.append(Question("U4", "unseen", "지원하고 일주일 넘게 아무 연락 없는 곳 몇 곳?",
                       "number = count", "number", {"number": len(a7), "alternatives": [len(a8)]}))

    plat = {}
    for p in ("saramin", "wanted", "groupby", "jobkorea"):
        g = [a for a in live if a.platform == p]
        plat[p] = (len(g), sum(1 for a in g if a.status == "passed"))
    qs.append(Question("U5", "unseen", "플랫폼별로 지원 수와 서류 합격 수 비교해줘",
                       "groups = one per platform with category=platform, n = applications, "
                       "value = applications that passed document screening", "count_groups",
                       {"groups": plat}))

    hit = [a for a in ds.apps if a.retracted]
    qs.append(Question("U6", "unseen", "중복이라 무효로 처리한 지원 기록이 있었어? 어떤 거였어?",
                       "number = how many; ids = those records", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    days = [(a.viewed_at - a.applied).total_seconds() / 86400 for a in live if a.viewed_at]
    date_days = [(a.viewed_at.date() - a.applied_date).days for a in live if a.viewed_at]
    qs.append(Question("U7", "unseen", "평균적으로 지원 후 며칠 만에 열람됐어?",
                       "number = average days from application to first view", "number",
                       {"number": round(statistics.mean(days), 2), "tolerance": 0.3,
                        "alternatives": [round(statistics.mean(date_days), 2)]}))

    y = today - timedelta(days=1)
    hit = [a for a in live if a.viewed_at and a.viewed_at.date() == y]
    qs.append(Question("U8", "unseen", "어제 열람된 곳?",
                       "number = how many; ids = those applications", "ids",
                       {"number": len(hit), "ids": ids(hit)}))

    qs += build_hq(ds)
    return qs


def build_hq(ds: Dataset) -> list[Question]:
    """Unseen questions written independently by the directing agent (wording kept verbatim);
    only the answer keys are computed here."""
    live = ds.live
    today = ds.now.date()
    ids = lambda apps: sorted(a.id for a in apps)  # noqa: E731
    qs: list[Question] = []

    # H1 "the last two weeks": since today-14 or since today-13 (both readings accepted)
    h14 = [a for a in live if a.platform == "wanted" and a.status == "applied"
           and a.applied_date >= today - timedelta(days=14)]
    h13 = [a for a in h14 if a.applied_date >= today - timedelta(days=13)]
    qs.append(Question("H1", "unseen", "지난 2주 동안 원티드로 지원했는데 아직 열람도 안 된 회사는 어디야?",
                       "number = how many applications; ids = those applications", "ids",
                       {"number": len(h14), "ids": ids(h14),
                        "alternatives": [len(h13)], "alt_ids": [ids(h13)]}))

    groups, small = {}, False
    for p in ("saramin", "wanted", "groupby", "jobkorea"):
        viewed = [a for a in live if a.platform == p and a.viewed_at is not None]
        passed = [a for a in viewed if a.status == "passed"]
        groups[(None, p)] = (len(viewed), len(passed) / len(viewed) if viewed else None)
        small |= 0 < len(viewed) < 5
    qs.append(Question("H2", "unseen", "플랫폼별로 열람된 지원 중에서 서류 합격까지 간 비율이 어떻게 돼?",
                       "groups = one per platform with category=platform, n = viewed applications, "
                       "value = share of them that passed (0-1); flags: small_sample if any group has n<5",
                       "rate_groups", {"groups": groups, "flags": {"small_sample": small}}))

    per_company: dict[str, list] = {}
    for a in live:
        per_company.setdefault(a.company_id, []).append(a)
    multi = sorted(cid for cid, apps in per_company.items() if len(apps) >= 2)
    qs.append(Question("H3", "unseen", "같은 회사에 두 번 이상 지원한 적 있어? 있으면 어디랑 언제?",
                       "number = how many companies; ids = those companies' ids (co_...); "
                       "text = companies with application dates", "ids",
                       {"number": len(multi), "ids": multi}))

    rejected = [(next(e.at for e in a.events if e.to == "rejected"), a) for a in live if a.status == "rejected"]
    rejected.sort(key=lambda t: t[0], reverse=True)
    top3 = [a for _, a in rejected[:3]]
    qs.append(Question("H4", "unseen", "가장 최근에 떨어진 곳 세 군데랑 각각 무슨 이유로 지원했었는지 알려줘",
                       "number = 3 (or fewer if fewer rejections); ids = those applications; "
                       "text = company and reason for each", "ids",
                       {"number": len(top3), "ids": ids(top3), "reasons": {a.id: a.reason for a in top3}}))

    # "more than a month": applied before today-30 (strict) or on/before it (both accepted)
    open_ = [a for a in live if a.status in ("applied", "viewed")]
    strict = [a for a in open_ if a.applied_date < today - timedelta(days=30)]
    loose = [a for a in open_ if a.applied_date <= today - timedelta(days=30)]
    qs.append(Question("H5", "unseen", "지원한 지 한 달 넘었는데 아직 아무 결과도 없는 곳은 몇 군데야?",
                       "number = count", "number", {"number": len(strict), "alternatives": [len(loose)]}))
    return qs
