"""Seeded generator of a fictional job-search history (task 0003).

The generator keeps its own ground truth (plain Python objects). Correct answers are computed from
that truth, never through Mnemento's code path, so a bug in Mnemento cannot make its own answers
look right. Names are synthetic (가상/모의 + syllables, English aliases); no real companies.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Seoul")
BENCH_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)  # deliberately not the dev-demo date

PLATFORMS = [("saramin", 0.55), ("wanted", 0.25), ("groupby", 0.12), ("jobkorea", 0.08)]
RATES = [("top10", 0.2), ("top30", 0.35), ("top50", 0.25), (None, 0.2)]
VIEW_P = {"top10": 0.55, "top30": 0.45, "top50": 0.35, None: 0.4}
_SYL = list("가나다라마바사아자차카타파하고노도로모보소오조초코토포호구누두루무부수우주")
_EN = ["Arc", "Blue", "Core", "Delta", "Echo", "Flux", "Grid", "Halo", "Ion", "Jade", "Kite", "Lumen",
       "Mint", "Nova", "Orbit", "Pixel", "Quill", "Rune", "Sol", "Tide", "Umber", "Vela", "Wave", "Zen"]
_SUFFIX = ["소프트", "랩스", "테크", "데이터", "시스템즈", "웍스", "네트웍스", "솔루션"]
_EN_SUFFIX = {"소프트": "Soft", "랩스": "Labs", "테크": "Tech", "데이터": "Data", "시스템즈": "Systems",
              "웍스": "Works", "네트웍스": "Networks", "솔루션": "Solutions"}
REASONS = ["백엔드 경력 일치", "AI 개발 가능", "원격 근무", "스타트업 성장성", "연봉 수준 적정", "도메인 관심",
           "기술 스택 일치", "출퇴근 거리"]
SEARCH_FIRM_REASON = "서치펌 공고로 확인돼 패스"


@dataclass
class Company:
    id: str
    name: str
    normalized: str
    aliases: list[str]


@dataclass
class StatusEvent:
    to: str
    at: datetime


@dataclass
class App:
    id: str
    company_id: str
    platform: str
    applied: datetime  # occurrence time of the application
    rate: str | None
    reason: str
    events: list[StatusEvent] = field(default_factory=list)
    recorded_applied_date: date | None = None  # wrong date first recorded (then corrected)
    correction_reason: str | None = None
    retracted: bool = False  # a duplicate entry that was retracted
    retraction_reason: str | None = None

    @property
    def applied_date(self) -> date:
        return self.applied.date()

    @property
    def status(self) -> str:
        return self.events[-1].to if self.events else "applied"

    @property
    def viewed_at(self) -> datetime | None:
        return next((e.at for e in self.events if e.to == "viewed"), None)


@dataclass
class Dataset:
    seed: int
    scale: int
    now: datetime
    companies: list[Company]
    apps: list[App]  # includes retracted duplicates (flag), excluded from answers

    @property
    def live(self) -> list[App]:
        return [a for a in self.apps if not a.retracted]

    def company(self, cid: str) -> Company:
        return next(c for c in self.companies if c.id == cid)


def _pick(rng: random.Random, weighted):
    r, acc = rng.random(), 0.0
    for value, w in weighted:
        acc += w
        if r < acc:
            return value
    return weighted[-1][0]


def _company_name(rng: random.Random, used: set[str]) -> tuple[str, str]:
    while True:
        suffix = rng.choice(_SUFFIX)
        core = "".join(rng.choice(_SYL) for _ in range(2))
        base = f"{core}{suffix}"
        if base not in used:
            used.add(base)
            return base, suffix


def generate(scale: int, seed: int = 20261120, now: datetime = BENCH_NOW) -> Dataset:
    rng = random.Random(f"{seed}:{scale}")
    n_companies = max(20, scale // 3)
    used: set[str] = set()
    companies = []
    for i in range(n_companies):
        base, suffix = _company_name(rng, used)
        style = rng.random()
        name = f"(주){base}" if style < 0.4 else (f"{base} 주식회사" if style < 0.55 else base)
        aliases = []
        if rng.random() < 0.3:
            aliases.append(f"{rng.choice(_EN)}{rng.choice(_EN).lower()} {_EN_SUFFIX[suffix]}")
        companies.append(Company(f"co_{i:05d}", name, base, aliases))

    span_days = max(45, scale // 8)
    apps: list[App] = []
    for i in range(scale):
        # more recent days are denser
        age_days = int(span_days * (rng.random() ** 1.6))
        day = now.date() - timedelta(days=age_days)
        hour = rng.randint(9, 22)
        applied = datetime(day.year, day.month, day.day, hour, rng.choice([0, 15, 30, 45]), tzinfo=TZ)
        if applied > now:
            applied = now - timedelta(hours=1)
        rate = _pick(rng, RATES)
        app = App(id=f"app_{i:05d}", company_id=rng.choice(companies).id, platform=_pick(rng, PLATFORMS),
                  applied=applied, rate=rate, reason=rng.choice(REASONS))
        if rng.random() < 0.04:
            app.reason = SEARCH_FIRM_REASON
            app.events.append(StatusEvent("withdrawn", applied + timedelta(hours=rng.randint(2, 48))))
        elif rng.random() < VIEW_P[rate]:
            viewed = applied + timedelta(hours=max(1, int(rng.lognormvariate(3.2, 0.9))))
            if viewed < now:
                app.events.append(StatusEvent("viewed", viewed))
                r = rng.random()
                outcome_at = viewed + timedelta(days=rng.randint(1, 14), hours=rng.randint(0, 8))
                if outcome_at < now:
                    if r < 0.4:
                        app.events.append(StatusEvent("rejected", outcome_at))
                    elif r < 0.55:
                        app.events.append(StatusEvent("passed", outcome_at))
        apps.append(app)
        if rng.random() < 0.02:  # first recorded with the wrong date, corrected later
            app.recorded_applied_date = app.applied_date - timedelta(days=1)
            app.correction_reason = "지원완료 메일 날짜 확인 — 하루 앞당겨 잘못 적음"
    # retracted duplicates (~1%)
    for j, src in enumerate(rng.sample(apps, max(1, scale // 100))):
        dup = App(id=f"app_dup{j:04d}", company_id=src.company_id, platform=src.platform, applied=src.applied,
                  rate=src.rate, reason=src.reason, retracted=True, retraction_reason=f"{src.id}와 중복 기록")
        apps.append(dup)
    return Dataset(seed, scale, now, companies, apps)
