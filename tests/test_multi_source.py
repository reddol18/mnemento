"""Issue #8 / ADR-0024: one flag kept in several source files — imported as their union, the disagreement reported
and remembered, and answers that use the field say so. Fictional data."""

from datetime import datetime
from pathlib import Path

import pytest

from mnemento import Ledger
from mnemento.importer import ImportReport, combine, upsert
from mnemento.keeper import Keeper, ScriptedLLM

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "investment"
NOW = "2026-09-30T18:00:00+09:00"
# two config files of the source project both mark "watch only"; they do not agree
WATCH_CONFIG = {"sec_900001": True, "sec_900002": True, "sec_900003": False}
DASHBOARD = {"sec_900001": True, "sec_900003": True, "sec_900004": True}  # 900004 is not in the watch config


def run(led, watch, dash):
    rep = ImportReport()
    for sid in sorted({*watch, *dash}):
        hid = f"hold_{sid[-1]}"
        flag = combine(led, rep, entity_id=hid, field="watch_only", how="any", now=NOW,
                       by_source={"watch config": watch.get(sid), "dashboard": dash.get(sid)})
        upsert(led, rep, entity_type="holding", entity_id=hid, change_kind="updated", now=NOW, by="fixture",
               evidence="fixture", at=NOW, at_precision="time",
               doc={"security_id": sid, "account": "paper", "watch_only": flag})
    return rep


@pytest.fixture
def k(tmp_path):
    led = Ledger.open(tmp_path / "m.db")
    led.schemas.load_dir(EXAMPLES)
    keeper = Keeper(led, ScriptedLLM())
    keeper.pipeline.query_log = None
    yield keeper
    led.close()


def test_union_is_stored_and_the_disagreement_reported(k):
    rep = run(k.ledger, WATCH_CONFIG, DASHBOARD)
    flags = {e.id: e.doc["watch_only"] for e in k.ledger.find("holding")}
    assert flags == {"hold_1": True, "hold_2": True, "hold_3": True, "hold_4": True}
    assert sorted(m["entity_id"] for m in rep.mismatches) == ["hold_2", "hold_3", "hold_4"]  # 1 agrees in both
    m3 = next(m for m in rep.mismatches if m["entity_id"] == "hold_3")
    assert m3["by_source"] == {"watch config": False, "dashboard": True}


def test_answers_using_the_field_say_how_many_records_rest_on_part_of_the_sources(k):
    run(k.ledger, WATCH_CONFIG, DASHBOARD)
    ans = k.ask("관찰 전용 종목", spec={"entity_type": "holding", "mode": "list",
                                    "filters": [{"field": "watch_only", "op": "eq", "value": True}]},
                now=datetime.fromisoformat(NOW))
    assert ans.result["total"] == 4
    assert any("watch_only: for 3 holding record(s)" in w and "of 2" in w for w in ans.warnings)
    other = k.ask("q", spec={"entity_type": "holding", "mode": "count"}, now=datetime.fromisoformat(NOW))
    assert not any("sources" in w for w in other.warnings)  # questions that do not use it are not warned


def test_a_later_import_where_the_sources_agree_clears_the_note(k):
    run(k.ledger, WATCH_CONFIG, DASHBOARD)
    agree = {"sec_900001": True, "sec_900002": True, "sec_900003": True, "sec_900004": True}
    rep = run(k.ledger, agree, agree)
    assert rep.mismatches == []
    assert k.ledger.storage.fetch_all("SELECT COUNT(*) AS n FROM source_coverage")[0]["n"] == 0


def test_first_and_union(k):
    rep = ImportReport()
    assert combine(k.ledger, rep, entity_id="x", field="f", by_source={"a": None, "b": 2, "c": 3}, now=NOW) == 2
    assert combine(k.ledger, rep, entity_id="x", field="g", how="union",
                   by_source={"a": ["p", "q"], "b": ["q", "r"]}, now=NOW) == ["p", "q", "r"]
    with pytest.raises(ValueError):
        combine(k.ledger, rep, entity_id="x", field="h", how="max", by_source={"a": 1}, now=NOW)


def test_no_source_having_the_value_is_not_a_mismatch(k):
    rep = ImportReport()
    assert combine(k.ledger, rep, entity_id="x", field="f", how="any", by_source={"a": None, "b": None}, now=NOW) is None
    assert rep.mismatches == []
