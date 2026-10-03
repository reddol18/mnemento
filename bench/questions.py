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
    grader: str  # number | ids | rate_groups | count_groups
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
    return qs


def build_unseen_v2(ds: Dataset) -> list[Question]:
    """Unseen questions of evaluation set v2 — empty until the directing agent delivers them."""
    return []


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
