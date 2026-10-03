"""Write path: structured requests by code, free text via (scripted) LLM, ask back when unsure."""

from datetime import datetime

import pytest

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import Keeper, ScriptedLLM

NOW = datetime.fromisoformat(DEMO_NOW)
AT = "2026-10-03T10:00:00+09:00"


@pytest.fixture
def k():
    led = open_demo()
    keeper = Keeper(led, ScriptedLLM())
    yield keeper
    led.close()


def n_events(k):
    return k.ledger.storage.fetch_all("SELECT COUNT(*) AS n FROM events")[0]["n"]


def test_structured_create_resolves_company_by_alias(k):
    res = k.record({"entity_type": "application", "kind": "created", "entity_id": "app_new1", "at": AT,
                    "payload": {"company_id": "Gasang Tech", "platform": "wanted", "status": "applied",
                                "applied_at": "2026-10-03"}}, by="agent_a", evidence="test")
    assert res.status == "recorded", res.message
    assert res.entity["company_id"] == "co_gasangtech"
    assert k.ledger.history("app_new1")[0].by == "agent_a"


def test_status_change_by_match_fields(k):
    k.record({"entity_type": "posting", "kind": "created", "entity_id": "posting_saramin_10000001", "at": AT,
              "payload": {"platform": "saramin", "posting_id": "10000001", "title": "백엔드",
                          "company_id": "co_v13"}}, by="agent_a")
    k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o04", "at": AT,
              "payload": {"posting_id": "10000001"}}, by="agent_a")  # resolved by identifier field
    assert k.ledger.get_entity("app_o04").doc["posting_id"] == "posting_saramin_10000001"
    res = k.record({"entity_type": "application", "kind": "status_changed",
                    "match": {"platform": "saramin", "posting_id": "posting_saramin_10000001"},
                    "payload": {"to": "viewed"}, "at": AT}, by="agent_a", evidence="viewed notice")
    assert res.status == "recorded" and res.entity_id == "app_o04"
    assert k.ledger.get_entity("app_o04").doc["status"] == "viewed"


def test_ambiguous_target_asks_back_and_stores_nothing(k):
    before = n_events(k)
    res = k.record({"entity_type": "application", "kind": "status_changed",
                    "match": {"platform": "saramin", "applied_at": "2026-10-02"}, "payload": {"to": "viewed"}},
                   by="agent_a")
    assert res.status == "clarify" and len(res.options) == 10 and "13 application records" in res.message
    assert n_events(k) == before


def test_unknown_company_asks_back(k):
    before = n_events(k)
    res = k.record({"entity_type": "application", "kind": "created", "entity_id": "app_x",
                    "payload": {"company_id": "가상텍", "platform": "saramin", "status": "applied",
                                "applied_at": "2026-10-03"}}, by="agent_a")
    assert res.status == "clarify"
    assert res.options[0].startswith("co_gasangtech") and res.options[-1].startswith("create new company")
    assert n_events(k) == before


def test_duplicate_company_is_not_created(k):
    res = k.record({"entity_type": "company", "kind": "created",
                    "payload": {"name": "가상테크 주식회사", "normalized_name": "가상테크"}}, by="agent_a")
    assert res.status == "clarify" and res.entity_id == "co_gasangtech"


def test_conflict_from_backfill_becomes_clarification(k):
    res = k.record({"entity_type": "application", "kind": "status_changed", "entity_id": "app_o05",
                    "payload": {"from": "viewed", "to": "rejected"}, "at": AT}, by="agent_a")
    assert res.status == "clarify" and "Current status: 'applied'" in res.message
    assert k.ledger.get_entity("app_o05").doc["status"] == "applied"


def test_schema_violation_rejected(k):
    res = k.record({"entity_type": "application", "kind": "status_changed", "entity_id": "app_o05",
                    "payload": {"to": "ghosted"}}, by="agent_a")
    assert res.status == "rejected" and any("ghosted" in e for e in res.errors)


def test_unknown_fields_are_not_stored_and_become_a_proposal(k):
    req = {"entity_type": "application", "kind": "updated", "entity_id": "app_o05",
           "payload": {"recruiter_type": "search_firm"}}
    first = k.record(req, by="agent_a")
    assert first.status == "rejected" and first.proposals == []  # seen once: not yet
    second = k.record({**req, "entity_id": "app_o06"}, by="agent_b")
    assert second.status == "rejected"
    [prop] = second.proposals
    assert prop["kind"] == "extend_schema" and prop["entity_type"] == "application"
    assert prop["to_version"] == 3 and "recruiter_type" in prop["add_fields"]
    assert "not applied" in prop["status"]
    assert k.ledger.schemas.get("application").version == 2  # never applied automatically
    assert "recruiter_type" not in k.ledger.get_entity("app_o05").doc


def test_propose_new_schema_from_samples(k):
    [draft] = k.propose_schema("trade", [
        {"ticker": "AAA", "side": "buy", "qty": 10, "traded_on": "2026-10-01"},
        {"ticker": "BBB", "side": "sell", "qty": 5, "traded_on": "2026-10-02"},
        {"ticker": "AAA", "side": "buy", "qty": 3, "traded_on": "2026-10-02", "note": "x"},
    ])
    f = draft["draft"]["fields"]
    assert f["qty"]["type"] == "integer" and f["traded_on"]["format"] == "date"
    assert f["side"]["enum_candidates"] == ["buy", "sell"] and f["ticker"]["required"] is True
    assert "required" not in f["note"]
    assert "trade" not in k.ledger.schemas.names()


def test_free_text_record_goes_through_the_same_checks(k):
    k.recorder.llm = ScriptedLLM([{
        "kind": "record",
        "request": {"entity_type": "application", "kind": "status_changed",
                    "entity_id": "app_o06", "payload": {"to": "viewed"}, "at": AT}}])
    res = k.record_text("가상기업15 열람됨", by="agent_a")
    assert res.status == "recorded"
    ev = k.ledger.history("app_o06")[-1]
    assert ev.evidence == "text: 가상기업15 열람됨" and ev.at == AT
    assert res.trace["path"] == "llm" and res.trace["totals"]["llm_calls"] == 1


def test_free_text_clarify(k):
    k.recorder.llm = ScriptedLLM([{"kind": "clarify", "clarify_question": "Which posting?",
                                   "options": ["10000001", "10000002"]}])
    before = n_events(k)
    res = k.record_text("가상테크 지원했어", by="agent_a")
    assert res.status == "clarify" and res.options == ["10000001", "10000002"]
    assert n_events(k) == before
