"""Series records (ADR-0016, task 0008 step 4): schema kind, kind judgement with counterexamples, ingest batches
(same point updates, batch revert), fictional weight and spending data."""

from datetime import date, timedelta

import pytest

from bench.series_gen import SPENDING_SCHEMA, WEIGHT_SCHEMA, generate
from mnemento import Ledger
from mnemento.errors import SchemaDefinitionError
from mnemento.keeper.kind import judge_kind
from mnemento.schema.definition import SchemaDef
from mnemento.series import SeriesError, ingest, revert_batch


@pytest.fixture
def led():
    ledger = Ledger.open(":memory:")
    ledger.schemas.register(WEIGHT_SCHEMA)
    ledger.schemas.register(SPENDING_SCHEMA)
    yield ledger
    ledger.close()


def points(led, type_):
    return led.storage.fetch_all("SELECT key, t, doc, batch_id FROM series_points WHERE type = ? ORDER BY key, t",
                                 [type_])


# ---- schema ---------------------------------------------------------------------------------

def test_series_schema_round_trip_and_validation():
    s = SchemaDef.from_dict(WEIGHT_SCHEMA)
    assert (s.kind, s.series_key, s.time_field, s.granularity, s.measures) == (
        "series", ("person",), "measured_on", "day", ("kg", "body_fat_pct"))
    assert SchemaDef.from_dict(s.to_dict()) == s and s.with_version(2).measures == s.measures
    bad = [
        {"measures": ["person"]},                       # a measure must be numeric
        {"time_field": "kg"},                           # the time field must be a date
        {"series_key": ["kg"]},                         # the key must be text or integer
        {"granularity": "instant"},                     # instant needs a date-time field
        {"kind": "table"},
        {"measures": []},
    ]
    for change in bad:
        with pytest.raises(SchemaDefinitionError):
            SchemaDef.from_dict({**WEIGHT_SCHEMA, **change})
    plain = {k: v for k, v in WEIGHT_SCHEMA.items() if k not in ("kind",)}
    with pytest.raises(SchemaDefinitionError):  # series parts on an entity schema
        SchemaDef.from_dict(plain)
    assert SchemaDef.from_dict({"name": "n", "version": 1, "description": "d",
                                "fields": {"a": {"type": "string", "description": "a"}}}).kind == "entity"


def test_changing_kind_is_breaking():
    s = SchemaDef.from_dict(WEIGHT_SCHEMA)
    e = SchemaDef.from_dict({k: v for k, v in WEIGHT_SCHEMA.items()
                             if k not in ("kind", "series_key", "time_field", "granularity", "measures")})
    assert any("kind changed" in r for r in s.breaking_changes_to(e.with_version(2)))


# ---- kind judgement: the signals and the counterexamples of ADR-0016 --------------------------

def days(n, start=date(2026, 7, 1)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_weight_and_spending_are_series():
    data = generate(days=40)
    for rows, key, measures in ((data.weight, ["person"], ["kg", "body_fat_pct"]),
                                (data.spending, ["category"], ["count", "amount"])):
        j = judge_kind(rows)
        assert j["kind"] == "series", j
        assert j["series"]["series_key"] == key and set(j["series"]["measures"]) == set(measures)
        assert j["series"]["granularity"] == "day" and j["question"] is None


def test_trades_are_entities_despite_time_and_numbers():
    rows = [{"trade_id": f"t{i}", "security_id": "sec_900001", "side": "buy" if i % 3 else "sell",
             "traded_at": d, "price": 1000 + i, "units": 3} for i, d in enumerate(days(12))]
    j = judge_kind(rows)
    assert j["kind"] == "entity"
    assert any("row_identifier" in r for r in j["reasons"]) and j["signals"]["key"] == ["security_id"]


def test_subscription_contract_is_an_entity():
    rows = [{"service": s, "started_on": "2026-01-0%d" % (i + 1), "monthly_fee": 9900 + i * 1000,
             "status": "active"} for i, s in enumerate(["음악", "영상", "클라우드", "뉴스"])]
    assert judge_kind(rows)["kind"] == "entity"  # one row per key: a contract, not a measurement


def test_interview_schedule_is_an_entity():
    rows = [{"company_id": "co_x", "at": f"2026-08-{10 + i:02d}T10:00:00+09:00",
             "status": ["scheduled", "done", "cancelled"][i % 3], "round": i + 1} for i in range(9)]
    j = judge_kind(rows)
    assert j["kind"] == "entity" and any("changing_category" in r or "reference" in r for r in j["reasons"])


def test_mixed_signals_ask_instead_of_guessing():
    rows = [{"person": "가상인", "day": d, "kg": 70 + i * 0.1, "mood": ["good", "bad"][i % 2]}
            for i, d in enumerate(days(20))]
    j = judge_kind(rows)
    assert j["kind"] is None and "piles up over time" in j["question"]


def test_draft_schema_carries_the_kind_proposal():
    from mnemento.keeper.proposals import draft_schema

    out = draft_schema("weight_log", generate(days=30).weight)
    assert out["draft"]["kind"] == "series" and out["record_kind"]["signals"]["key"] == ["person"]
    SchemaDef.from_dict({**out["draft"], "description": "w",
                         "fields": {k: {**{a: b for a, b in v.items() if a != "enum_candidates"}, "description": "x"}
                                    for k, v in out["draft"]["fields"].items()}})


# ---- ingest batches -------------------------------------------------------------------------

def test_ingest_inserts_updates_and_keeps_unchanged(led):
    rows = [{"person": "가상인", "measured_on": d, "kg": 70.0 + i / 10} for i, d in enumerate(days(5))]
    first = ingest(led, "weight", rows, by="t", source="scale export 1")
    assert (first.inserted, first.updated, first.unchanged) == (5, 0, 0) and first.batch_id
    again = ingest(led, "weight", rows, by="t", source="scale export 1")
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 5)
    fixed = [dict(rows[2], kg=69.5), *rows[3:], {"person": "가상인", "measured_on": days(6)[5], "kg": 70.6}]
    third = ingest(led, "weight", fixed, by="t", source="scale export 2")
    assert (third.inserted, third.updated, third.unchanged) == (1, 1, 2)
    assert len(points(led, "weight")) == 6
    batches = led.storage.list_batches("weight")
    assert [b["source"] for b in batches] == ["scale export 1", "scale export 1", "scale export 2"]


def test_a_batch_with_an_invalid_row_writes_nothing(led):
    rows = [{"person": "가상인", "measured_on": "2026-07-01", "kg": 70.1},
            {"person": "가상인", "measured_on": "07/02", "kg": 70.2},
            {"person": "가상인", "measured_on": "2026-07-03"},
            {"person": "가상인", "measured_on": "2026-07-01", "kg": 70.3}]
    rep = ingest(led, "weight", rows, by="t", source="s")
    assert rep.batch_id is None and [r["row"] for r in rep.rejected] == [1, 2, 3]
    assert points(led, "weight") == [] and led.storage.list_batches() == []


def test_revert_a_whole_batch_and_refuse_out_of_order(led):
    rows = [{"category": "식비", "spent_on": d, "amount": 10000, "count": 1} for d in days(3)]
    a = ingest(led, "spending", rows, by="t", source="card export 9/1")
    b = ingest(led, "spending", [dict(rows[0], amount=12000), {"category": "카페", "spent_on": days(1)[0],
                                                                 "amount": 4500, "count": 1}],
               by="t", source="card export 9/2")
    with pytest.raises(SeriesError, match="revert those first"):
        revert_batch(led, a.batch_id)
    assert revert_batch(led, b.batch_id)["reverted_points"] == 2
    docs = {(p["key"], p["t"]): p for p in points(led, "spending")}
    assert len(docs) == 3 and '"amount":10000' in docs[('["식비"]', days(1)[0])]["doc"]
    with pytest.raises(SeriesError, match="already reverted"):
        revert_batch(led, b.batch_id)
    revert_batch(led, a.batch_id)
    assert points(led, "spending") == []


def test_entity_schemas_are_not_series(led):
    led.schemas.register({"name": "note", "version": 1, "description": "n",
                          "fields": {"t": {"type": "string", "description": "t"}}})
    with pytest.raises(SeriesError):
        ingest(led, "note", [{"t": "x"}], by="t", source="s")


def test_fictional_data_loads(led):
    data = generate()
    w = ingest(led, "weight", data.weight, by="gen", source="series_gen")
    s = ingest(led, "spending", data.spending, by="gen", source="series_gen")
    assert w.inserted == len(data.weight) and s.inserted == len(data.spending)
    assert len(data.weight) < 2 * 270  # some days are missing on purpose
