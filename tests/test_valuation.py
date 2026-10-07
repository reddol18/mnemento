"""Issue #2 / ADR-0022: records joined with a series only along a declared reference — the price of what a record
points at, as of a date — and arithmetic over it (unrealized P/L). Fictional data, answers computed by hand."""

from datetime import datetime
from pathlib import Path

import pytest

from mnemento import Ledger
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.spec import QuerySpec, validate_spec
from mnemento.series import ingest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "investment"
NOW = datetime.fromisoformat("2026-09-30T18:00:00+09:00")

PRICES = [  # 900001 every day; 900002 stops on 09-10 (stale); 900003 has no price at all
    *({"security_id": "sec_900001", "price_on": f"2026-09-{d:02d}", "close": 10000 + d * 100} for d in range(1, 31)),
    *({"security_id": "sec_900002", "price_on": f"2026-09-{d:02d}", "close": 5000 - d * 10} for d in range(1, 11)),
]
HOLDINGS = {
    "hold_a": {"security_id": "sec_900001", "account": "real", "status": "held", "units": 10, "invested": 100000},
    "hold_b": {"security_id": "sec_900002", "account": "paper", "status": "held", "units": 20, "invested": 110000},
    "hold_c": {"security_id": "sec_900003", "account": "real", "status": "held", "units": 5, "invested": 30000},
}


@pytest.fixture
def k(tmp_path):
    led = Ledger.open(tmp_path / "v.db")
    led.schemas.load_dir(EXAMPLES)
    for code, name in (("900001", "가상바이오"), ("900002", "샘플전자"), ("900003", "모의펀드")):
        led.record_event(f"sec_{code}", "created", {"code": code, "name": name}, "2026-09-01T09:00:00+09:00", "t",
                         None, entity_type="security")
    for i, doc in HOLDINGS.items():
        led.record_event(i, "created", doc, "2026-09-01T09:00:00+09:00", "t", None, entity_type="holding")
    led.record_event("trade_1", "created", {"source_key": "t1", "security_id": "sec_900001", "account": "real",
                                            "side": "buy", "traded_at": "2026-09-05", "units": 10, "amount": 100000},
                     "2026-09-05T09:00:00+09:00", "t", None, entity_type="trade")
    ingest(led, "price", PRICES, by="t", source="fixture")
    keeper = Keeper(led, ScriptedLLM())
    keeper.pipeline.query_log = None
    yield keeper
    led.close()


PNL_VALUES = [
    {"name": "last_close", "asof": {"field": "security_id", "series": "price", "measure": "close"}},
    {"name": "market_value", "expr": {"op": "mul", "args": ["units", "last_close"]}},
    {"name": "pnl", "expr": {"op": "sub", "args": ["market_value", "invested"]}},
]


def test_unrealized_pnl_per_holding_matches_a_hand_calculation(k):
    ans = k.ask("보유 종목 평가 손익", spec={"entity_type": "holding", "mode": "list", "values": PNL_VALUES,
                                         "list_fields": ["security_id", "units", "last_close", "pnl"],
                                         "order_by": "pnl", "descending": True}, now=NOW)
    rows = {r["id"]: r for r in ans.result["rows"]}
    assert rows["hold_a"]["last_close"] == 13000 and rows["hold_a"]["pnl"] == 10 * 13000 - 100000  # +30,000
    assert rows["hold_b"]["last_close"] == 4900 and rows["hold_b"]["pnl"] == 20 * 4900 - 110000  # -12,000
    assert rows["hold_c"]["last_close"] is None and rows["hold_c"]["pnl"] is None
    assert [r["id"] for r in ans.result["rows"]] == ["hold_a", "hold_b", "hold_c"]  # empty values last
    assert any("1 record(s) have no price point" in w for w in ans.warnings)
    assert any("1 record(s) use a price point more than 7 days before @today" in w and "2026-09-10" in w
               for w in ans.warnings)


def test_total_pnl_as_a_measure(k):
    ans = k.ask("총 평가 손익", spec={"entity_type": "holding", "mode": "aggregate", "values": PNL_VALUES,
                                   "filters": [{"field": "security_id", "op": "in", "value": ["sec_900001", "sec_900002"]}],
                                   "measures": [{"name": "total_pnl", "agg": "sum", "field": "pnl"}]}, now=NOW)
    assert ans.result["groups"][0]["measures"]["total_pnl"] == 30000 - 12000


def test_value_at_the_records_own_date(k):
    """The price on the trade date (a date field of the record), not today."""
    ans = k.ask("체결일 종가", spec={"entity_type": "trade", "mode": "list", "list_fields": ["traded_at", "close_then"],
                                  "values": [{"name": "close_then", "asof": {"field": "security_id", "series": "price",
                                                                             "measure": "close", "at": "traded_at"}}]},
                now=NOW)
    assert ans.result["rows"][0]["close_then"] == 10500


def test_asof_on_a_past_date_takes_the_point_before_it(k):
    ans = k.ask("q", spec={"entity_type": "holding", "mode": "list", "list_fields": ["c"],
                           "filters": [{"field": "security_id", "op": "eq", "value": "sec_900002"}],
                           "values": [{"name": "c", "asof": {"field": "security_id", "series": "price",
                                                             "measure": "close", "at": "2026-09-20"}}]}, now=NOW)
    assert ans.result["rows"][0]["c"] == 4900  # 09-10, the last point before 09-20


@pytest.mark.parametrize("values,needle", [
    ([{"name": "c", "asof": {"field": "account", "series": "price", "measure": "close"}}], "same type as the series key"),
    ([{"name": "c", "asof": {"field": "security_id", "series": "holding", "measure": "close"}}], "not a series type"),
    ([{"name": "c", "asof": {"field": "security_id", "series": "price", "measure": "open"}}], "not a measure"),
    ([{"name": "c", "asof": {"field": "security_id", "series": "price", "measure": "close", "at": "status"}}],
     "at must be"),
    ([{"name": "x", "expr": {"op": "mul", "args": ["units", "account"]}}], "not a numeric field"),
    ([{"name": "x", "expr": {"op": "mul", "args": ["units", "later"]}},
      {"name": "later", "expr": {"op": "add", "args": ["units", 1]}}], "not a numeric field or an earlier value"),
    ([{"name": "units", "expr": {"op": "add", "args": ["units", 1]}}], "must be a new short word"),
])
def test_values_are_checked_against_the_dictionary(k, values, needle):
    schemas = {n: k.ledger.schemas.get(n) for n in k.ledger.schemas.names()}
    errs = validate_spec(QuerySpec.model_validate({"entity_type": "holding", "mode": "list", "values": values}),
                         schemas)
    assert any(needle in e for e in errs), errs


def test_interpreter_is_told_how_to_join_a_series(k):
    from mnemento.keeper.query.interpret import SYSTEM_PROMPT

    assert "asof" in SYSTEM_PROMPT and "unrealized" in SYSTEM_PROMPT


def test_a_question_about_holdings_is_shown_the_price_series(k):
    from mnemento.keeper.query.interpret import select_schemas

    schemas = {n: k.ledger.schemas.get(n) for n in k.ledger.schemas.names()}
    assert "price" in [s.name for s in select_schemas("보유 종목 평가 손익 알려줘", schemas)]
