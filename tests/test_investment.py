"""Investment records on fictional data (task 0008 step 3a): example schemas, idempotent import, relation
notes, one event counted from one type. All LLM calls go to ScriptedLLM (no network)."""

from datetime import datetime
from pathlib import Path

import pytest

from mnemento import Ledger
from mnemento.importer import ImportReport, report_vanished, stable_id, upsert
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.interpret import render_dictionary

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "investment"
NOW = datetime.fromisoformat("2026-09-30T18:00:00+09:00")
BY = "fixture-import"

# fictional securities and records only
SOURCE = {
    "security": [{"key": "security:900001", "doc": {"code": "900001", "name": "가상바이오"}},
                 {"key": "security:900002", "doc": {"code": "900002", "name": "샘플전자", "asset_type": "ETF"}}],
    "trade": [
        {"key": "paper:900001:tranche1:buy", "doc": {"security_id": "sec_900001", "account": "paper", "side": "buy",
                                                    "traded_at": "2026-07-30", "tranche": 1, "units": 3,
                                                    "price": 12340, "amount": 37020}},
        {"key": "paper:900001:tranche2:buy", "doc": {"security_id": "sec_900001", "account": "paper", "side": "buy",
                                                    "traded_at": "2026-08-20", "tranche": 2, "units": 3,
                                                    "price": 11800, "amount": 35400}},
        {"key": "real:900002:sell:2026-09-01", "doc": {"security_id": "sec_900002", "account": "real",
                                                      "side": "sell", "traded_at": "2026-09-03", "units": 10,
                                                      "amount": 52000, "pnl": -3000, "return_pct": -5.45,
                                                      "reason": "추세 이탈로 정리"}},
    ],
    "decision": [
        {"key": "paper:900001:log:2026-07-30:0", "doc": {"security_id": "sec_900001", "decided_at": "2026-07-30",
                                                        "topic": "log", "decided_by": "user",
                                                        "text": "1회차 매수 — 수출 반등이 근거(사용자 판단)"}},
        {"key": "real:900002:exit_decision_20260901", "doc": {"security_id": "sec_900002",
                                                             "decided_at": "2026-09-01", "topic": "exit_decision",
                                                             "text": "추세 이탈 확인, 보유분 정리"}},
    ],
}


def eid(kind, key):
    return "sec_" + key.split(":", 1)[1] if kind == "security" else stable_id(kind, key)


def run_import(led, source):
    report, seen = ImportReport(), set()
    for kind in ("security", "trade", "decision"):
        for rec in source[kind]:
            doc = dict(rec["doc"])
            if kind != "security":
                doc["source_key"] = rec["key"]
            d = doc.get("traded_at") or doc.get("decided_at")
            at, prec = (f"{d}T00:00:00+09:00", "date") if d else (NOW.isoformat(), "unknown")
            i = eid(kind, rec["key"])
            seen.add(i)
            upsert(led, report, entity_type=kind, entity_id=i, doc=doc, at=at, at_precision=prec, by=BY,
                   evidence=f"fixture {rec['key']}", now=NOW.isoformat())
    report_vanished(led, report, ["security", "trade", "decision"], seen, BY)
    return report


@pytest.fixture
def led(tmp_path):
    ledger = Ledger.open(tmp_path / "inv.db")
    ledger.schemas.load_dir(EXAMPLES)
    yield ledger
    ledger.close()


def test_example_schemas_load_with_relations(led):
    assert {"security", "trade", "decision"} <= set(led.schemas.names())
    text = render_dictionary([led.schemas.get("trade"), led.schemas.get("decision")], {})
    assert "related to decision:" in text and "related to trade:" in text


def test_import_is_idempotent_and_corrects_changes(led):
    first = run_import(led, SOURCE)
    assert first.created == {"security": 2, "trade": 3, "decision": 2} and not first.vanished
    again = run_import(led, SOURCE)
    assert again.created == {} and again.corrected == {} and sum(again.unchanged.values()) == 7
    changed = {**SOURCE, "trade": [{**SOURCE["trade"][0], "doc": {**SOURCE["trade"][0]["doc"], "price": 12350,
                                                                  "amount": 37050}}, *SOURCE["trade"][1:]]}
    third = run_import(led, changed)
    assert third.corrected == {"trade": 1}
    t = led.get_entity(stable_id("trade", "paper:900001:tranche1:buy"))
    assert t.doc["price"] == 12350
    kinds = [e.kind for e in led.history(t.id)]
    assert kinds == ["created", "corrected"]
    assert "source changed" in led.history(t.id)[-1].evidence


def test_vanished_records_are_reported_not_deleted(led):
    run_import(led, SOURCE)
    smaller = {**SOURCE, "decision": SOURCE["decision"][:1]}
    report = run_import(led, smaller)
    gone = stable_id("decision", "real:900002:exit_decision_20260901")
    assert report.vanished == [gone]
    assert not led.get_entity(gone).retracted


def test_buys_are_counted_from_trades_not_decisions(led):
    run_import(led, SOURCE)
    k = Keeper(led, ScriptedLLM())
    buys = k.ask("가상바이오 몇 번 샀어?", spec={"entity_type": "trade", "mode": "count", "filters": [
        {"field": "security_id", "op": "name_is", "value": "가상바이오"},
        {"field": "side", "op": "eq", "value": "buy"}]}, now=NOW)
    assert buys.result["total"] == 2  # the decision about the first buy is not a third buy


def test_stop_loss_question_and_no_record(led):
    run_import(led, SOURCE)
    k = Keeper(led, ScriptedLLM())
    losses = k.ask("올해 손절한 종목", spec={"entity_type": "trade", "mode": "list", "filters": [
        {"field": "side", "op": "eq", "value": "sell"}, {"field": "pnl", "op": "lt", "value": 0},
        {"field": "traded_at", "op": "gte", "value": "@this_year_start"}]}, now=NOW)
    assert losses.evidence == [stable_id("trade", "real:900002:sell:2026-09-01")]
    why = k.ask("샘플전자 왜 팔았어", spec={"entity_type": "decision", "mode": "list", "filters": [
        {"field": "security_id", "op": "name_is", "value": "없는종목"}]}, now=NOW)
    assert why.result["total"] == 0 and any("matches no recorded entity" in w for w in why.warnings)


def test_identifier_in_the_text_finds_the_security(led):
    """Step 3b: a code written with a nickname ("바이오주(900001)") resolves by the identifier field first."""
    from mnemento.keeper.identity import IdentityResolver, identifier_tokens

    run_import(led, SOURCE)
    r = IdentityResolver(led, "security")
    for text in ("바이오주(900001)", "900001", "가상 바이오 900001 종목"):
        res = r.resolve(text)
        assert res.matches == ["sec_900001"] and res.rule == "identifier:code", text
    assert r.resolve("바이오주").status in ("candidates", "none")  # a nickname alone is never matched
    assert identifier_tokens("10/2 사람인 지원 3곳") == set()  # dates and small numbers are not identifiers
    k = Keeper(led, ScriptedLLM())
    ans = k.ask("바이오주(900001) 몇 번 샀어?", spec={"entity_type": "trade", "mode": "count", "filters": [
        {"field": "security_id", "op": "name_is", "value": "바이오주(900001)"},
        {"field": "side", "op": "eq", "value": "buy"}]}, now=NOW)
    assert ans.result["total"] == 2 and any("identifier:code" in w for w in ans.warnings)


def test_state_records_change_with_updated_events_at_the_source_date(led):
    """Step 3b: criteria are state — a change is `updated` (only the changed fields), dated by the source."""
    rep = ImportReport()
    base = {"security_id": "sec_900001", "source_key": "criteria:900001", "trigger_price": 12000,
            "band_basis_date": "2026-07-24", "observe_only": True}
    kw = dict(entity_type="buy_criteria", entity_id="crit_1", at="2026-07-24T00:00:00+09:00", at_precision="date",
              by=BY, evidence="fixture", now=NOW.isoformat(), change_kind="updated")
    led.schemas.register({"name": "buy_criteria", "version": 1, "description": "criteria", "fields": {
        "security_id": {"type": "string", "description": "s", "ref": "security"},
        "source_key": {"type": "string", "description": "k"},
        "trigger_price": {"type": "number", "description": "t"},
        "band_basis_date": {"type": "string", "format": "date", "description": "d"},
        "observe_only": {"type": "boolean", "description": "o"}}})
    assert upsert(led, rep, doc=base, **kw) == "created"
    assert upsert(led, rep, doc=base, **kw) == "unchanged"
    new = {k: v for k, v in base.items() if k != "observe_only"} | {"trigger_price": 11500,
                                                                   "band_basis_date": "2026-08-05"}
    assert upsert(led, rep, doc=new, change_at="2026-08-05T00:00:00+09:00", **kw) == "updated"
    last = led.history("crit_1")[-1]
    assert last.kind == "updated" and last.at_precision == "date" and last.at.startswith("2026-08-05")
    assert last.payload == {"trigger_price": 11500, "band_basis_date": "2026-08-05", "observe_only": None}
    assert led.get_entity("crit_1").doc == new and rep.updated == {"buy_criteria": 1}
    with pytest.raises(ValueError):
        upsert(led, rep, doc=new, **{**kw, "change_kind": "deleted"})


def test_vanished_is_limited_to_the_records_a_source_owns(led):
    run_import(led, SOURCE)
    rep = ImportReport()
    upsert(led, rep, entity_type="decision", entity_id="dec_other", doc={"source_key": "other:1", "text": "x",
           "decided_at": "2026-09-18"}, at="2026-09-18T00:00:00+09:00", at_precision="date", by=BY,
           evidence="another source", now=NOW.isoformat())
    report = run_import(led, SOURCE)  # without an owner filter the other source's record looks vanished
    assert report.vanished == ["dec_other"]
    narrowed = ImportReport()
    report_vanished(led, narrowed, ["decision"], set(), BY,
                    owns=lambda e: not e.doc.get("source_key", "").startswith("other:"))
    assert "dec_other" not in narrowed.vanished


def test_schema_selection_follows_relation_notes(led):
    """Step 3b: a 'why' question picks decision (keyword); a type that declares a relation to decision (here
    buy_criteria) is shown too, so the interpreter can choose where the reason actually is."""
    from mnemento.keeper.query.interpret import select_schemas

    led.schemas.register({"name": "buy_criteria", "version": 1, "description": "criteria",
                          "relations": [{"type": "decision", "note": "reasons may be in either"}],
                          "fields": {"security_id": {"type": "string", "description": "s", "ref": "security"},
                                     "source_note": {"type": "string", "description": "why"}}})
    schemas = {n: led.schemas.get(n) for n in led.schemas.names()}
    picked = [s.name for s in select_schemas("왜 이 종목은 안 사기로 했지?", schemas)]
    assert "decision" in picked and "buy_criteria" in picked
