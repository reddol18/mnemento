"""A small, fixed, entirely fictional job-search dataset for tests and the demo log.

Shapes mirror the motivating story in docs/PLAN.md with invented companies:
- 13 saramin applications on 2026-10-02 (one was first recorded as 10/1 and later corrected,
  one duplicate entry was retracted)
- September vs October view rates by expected pass rate (top10 vs top30)
- one application to 가상테크 (also known as "Gasang Tech")
"""

from __future__ import annotations

from pathlib import Path

from .ledger import Ledger

DEMO_NOW = "2026-10-03T12:00:00+09:00"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
BY = "demo_agent"

COMPANIES = [
    ("co_gasangtech", "(주)가상테크", "가상테크", ["Gasang Tech"]),
    ("co_gasangmedia", "가상미디어", "가상미디어", []),
    ("co_samplelabs", "샘플랩스 주식회사", "샘플랩스", ["Sample Labs"]),
] + [(f"co_v{i:02d}", f"가상기업{i:02d}", f"가상기업{i:02d}", []) for i in range(1, 31)]

# (id, company, platform, applied_at, expected_rate, viewed_at | None, final status)
APPLICATIONS = [
    # September: top10 3/6 viewed, top30 4/6 viewed
    ("app_s01", "co_v01", "saramin", "2026-09-10", "top10", "2026-09-11", "rejected"),
    ("app_s02", "co_v02", "saramin", "2026-09-10", "top10", "2026-09-12", "viewed"),
    ("app_s03", "co_v03", "wanted", "2026-09-12", "top10", "2026-09-13", "passed"),
    ("app_s04", "co_v04", "saramin", "2026-09-15", "top10", None, "applied"),
    ("app_s05", "co_v05", "wanted", "2026-09-18", "top10", None, "applied"),
    ("app_s06", "co_v06", "saramin", "2026-09-20", "top10", None, "applied"),
    ("app_s07", "co_v07", "saramin", "2026-09-10", "top30", "2026-09-11", "viewed"),
    ("app_s08", "co_v08", "saramin", "2026-09-12", "top30", "2026-09-14", "rejected"),
    ("app_s09", "co_v09", "wanted", "2026-09-15", "top30", "2026-09-16", "viewed"),
    ("app_s10", "co_v10", "saramin", "2026-09-18", "top30", "2026-09-19", "viewed"),
    ("app_s11", "co_v11", "saramin", "2026-09-22", "top30", None, "applied"),
    ("app_s12", "co_v12", "groupby", "2026-09-25", "top30", None, "applied"),
    # October: top10 3/4 viewed, top30 0/9 viewed
    ("app_o01", "co_samplelabs", "saramin", "2026-10-01", "top10", "2026-10-02", "viewed"),
    ("app_o02", "co_gasangtech", "saramin", "2026-10-02", "top10", "2026-10-02", "viewed"),
    ("app_o03", "co_gasangmedia", "saramin", "2026-10-02", "top10", "2026-10-03", "viewed"),
    ("app_o04", "co_v13", "saramin", "2026-10-02", "top10", None, "applied"),
] + [
    (f"app_o{n:02d}", f"co_v{n + 9:02d}", "saramin", "2026-10-02", "top30", None, "applied")
    for n in range(5, 14)
] + [
    ("app_w01", "co_v25", "wanted", "2026-10-02", None, None, "applied"),
    ("app_w02", "co_v26", "wanted", "2026-10-02", None, None, "applied"),
]


def seed(ledger: Ledger, schema_dir: Path = SCHEMA_DIR) -> None:
    ledger.schemas.load_dir(schema_dir)
    for cid, name, norm, aliases in COMPANIES:
        doc = {"name": name, "normalized_name": norm}
        if aliases:
            doc["aliases"] = aliases
        ledger.record_event(cid, "created", doc, "2026-09-01T09:00:00+09:00", BY, "fictional seed",
                            entity_type="company")
    for aid, company, platform, applied, rate, viewed, final in APPLICATIONS:
        doc = {"company_id": company, "platform": platform, "status": "applied", "applied_at": applied}
        if rate:
            doc["expected_rate"] = rate
        ledger.record_event(aid, "created", doc, f"{applied}T10:00:00+09:00", BY, "fictional seed",
                            entity_type="application")
        if viewed:
            ledger.record_event(aid, "status_changed", {"from": "applied", "to": "viewed"},
                                f"{viewed}T17:00:00+09:00", BY, "platform: viewed notice")
            ledger.record_event(aid, "updated", {"viewed_at": viewed}, f"{viewed}T17:00:00+09:00", BY,
                                "platform: viewed notice")
        if final not in ("applied", "viewed"):
            ledger.record_event(aid, "status_changed", {"to": final}, f"{viewed or applied}T18:00:00+09:00",
                                BY, f"mail: {final}")
    # recorded with the wrong date, then corrected (the "12 -> 13" story)
    wrong = ledger.record_event(
        "app_o14", "created",
        {"company_id": "co_v23", "platform": "saramin", "status": "applied", "applied_at": "2026-10-01"},
        "2026-10-02T11:00:00+09:00", BY, "memo", entity_type="application")
    ledger.record_event("app_o14", "corrected",
                        {"target": wrong.id, "payload": {**wrong.payload, "applied_at": "2026-10-02"}},
                        "2026-10-03T09:00:00+09:00", BY, "application-complete mail is dated 10/2")
    # a duplicate entry, retracted
    ledger.record_event(
        "app_o15", "created",
        {"company_id": "co_v14", "platform": "saramin", "status": "applied", "applied_at": "2026-10-02"},
        "2026-10-02T12:00:00+09:00", BY, "memo", entity_type="application")
    ledger.record_event("app_o15", "retracted", {}, "2026-10-03T09:05:00+09:00", BY,
                        "duplicate of app_o05")


def open_demo(path: str | Path = ":memory:") -> Ledger:
    ledger = Ledger.open(path)
    seed(ledger)
    return ledger
