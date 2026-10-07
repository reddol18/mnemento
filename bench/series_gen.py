"""Fictional series data for task 0008 step 4 (ADR-0016): daily weight per person and daily spending per category.

Deterministic for a seed. Some days are missing on purpose (gap warnings). Answer keys are computed from these
rows, never from Mnemento.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

WEIGHT_SCHEMA = {
    "name": "weight", "version": 1, "kind": "series",
    "description": "Body weight measured about once a day, per person (fictional).",
    "keywords": ["체중", "몸무게", "weight"],
    "series_key": ["person"], "time_field": "measured_on", "granularity": "day", "measures": ["kg", "body_fat_pct"],
    "fields": {
        "person": {"type": "string", "description": "Who was measured.", "indexed": True},
        "measured_on": {"type": "string", "format": "date", "description": "Day of the measurement."},
        "kg": {"type": "number", "description": "Body weight, kg."},
        "body_fat_pct": {"type": "number", "description": "Body fat, percent (not every day)."},
    },
}

SPENDING_SCHEMA = {
    "name": "spending", "version": 1, "kind": "series",
    "description": "Money spent per day and category (fictional).",
    "keywords": ["지출", "소비", "썼", "spending"],
    "series_key": ["category"], "time_field": "spent_on", "granularity": "day", "measures": ["amount", "count"],
    "fields": {
        "category": {"type": "string", "description": "Spending category, e.g. 식비, 교통, 카페.", "indexed": True},
        "spent_on": {"type": "string", "format": "date", "description": "Day of the spending."},
        "amount": {"type": "integer", "description": "Total spent that day in the category, KRW."},
        "count": {"type": "integer", "description": "Number of payments that day in the category."},
    },
}

PERSONS = ["가상인", "샘플인"]
CATEGORIES = ["식비", "교통", "카페"]


@dataclass
class SeriesData:
    start: date
    end: date
    weight: list[dict]
    spending: list[dict]


def generate(seed: int = 20261005, start: date = date(2026, 1, 1), days: int = 270) -> SeriesData:
    rng = random.Random(seed)
    end = start + timedelta(days=days - 1)
    weight = []
    for p, base in zip(PERSONS, (72.0, 58.5)):
        kg = base
        for i in range(days):
            d = start + timedelta(days=i)
            kg = round(kg + rng.uniform(-0.35, 0.33), 1)
            if rng.random() < 0.12:  # a skipped day
                continue
            row = {"person": p, "measured_on": d.isoformat(), "kg": kg}
            if rng.random() < 0.3:
                row["body_fat_pct"] = round(rng.uniform(18, 28), 1)
            weight.append(row)
    spending = []
    for c, mean in zip(CATEGORIES, (24000, 6000, 9000)):
        for i in range(days):
            d = start + timedelta(days=i)
            if rng.random() < (0.1 if c == "식비" else 0.35):  # no spending that day
                continue
            n = rng.randint(1, 4)
            spending.append({"category": c, "spent_on": d.isoformat(), "count": n,
                             "amount": max(100, int(round(rng.gauss(mean, mean * 0.4) / 100)) * 100)})
    return SeriesData(start, end, weight, spending)


# ---- task 0008 step 5 (issue #2): fictional securities, daily prices, holdings and trades -------------------------

SECURITIES = [("900001", "가상바이오"), ("900002", "샘플전자"), ("900003", "모의펀드")]


@dataclass
class MarketData:
    prices: list[dict]  # price series rows (security_id, price_on, close)
    holdings: dict[str, dict]  # id -> doc
    trades: dict[str, dict]  # id -> doc


def generate_market(seed: int = 20261007) -> MarketData:
    """Weekday closes from 2026-07-01: 900001 up to 09-25, 900002 stops on 09-10 (a stale price), 900003 never
    priced. Holdings and trades refer to them; answers are computed from these rows."""
    rng = random.Random(seed)
    prices = []
    for code, start, last_day in (("900001", 12000.0, date(2026, 9, 25)), ("900002", 5200.0, date(2026, 9, 10))):
        p, d = start, date(2026, 7, 1)
        while d <= last_day:
            if d.weekday() < 5:
                p = round(p * (1 + rng.uniform(-0.03, 0.031)), -1)
                prices.append({"security_id": f"sec_{code}", "price_on": d.isoformat(), "close": p})
            d += timedelta(days=1)
    holdings = {
        "hold_a": {"security_id": "sec_900001", "account": "real", "status": "held", "units": 12, "invested": 150000},
        "hold_b": {"security_id": "sec_900002", "account": "paper", "status": "held", "units": 30, "invested": 160000},
        "hold_c": {"security_id": "sec_900003", "account": "real", "status": "held", "units": 7, "invested": 70000},
    }
    trades = {
        "trade_a": {"source_key": "gen:a", "security_id": "sec_900001", "account": "real", "side": "buy",
                    "traded_at": "2026-08-12", "units": 12, "amount": 150000},
        "trade_b": {"source_key": "gen:b", "security_id": "sec_900002", "account": "paper", "side": "buy",
                    "traded_at": "2026-07-20", "units": 30, "amount": 160000},
    }
    return MarketData(prices, holdings, trades)
