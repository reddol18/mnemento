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
    res = k.record({"entity_type": "application", "kind": "updated", "entity_id": "app_o05",
                    "payload": {"applied_at": "10/02"}}, by="agent_a")
    assert res.status == "rejected" and any("applied_at" in e for e in res.errors)


def test_store_first_then_organize(k):
    """ADR-0014 a-d: an unknown platform and an unknown field are stored and queryable at once; organizing
    (descriptions, labels, index, merge) needs the user's answer and keeps history."""
    from mnemento.errors import SchemaDefinitionError

    led = k.ledger
    base = {"entity_type": "application", "kind": "created", "at": AT,
            "payload": {"company_id": "co_v13", "status": "applied", "applied_at": "2026-10-03"}}
    # a. a platform value outside the list is stored, not rewritten
    r = k.record({**base, "entity_id": "app_r1", "payload": {**base["payload"], "platform": "remember"}}, by="a")
    assert r.status == "recorded" and r.entity["platform"] == "remember" and r.drafts["values"] == {"platform": "remember"}
    # b. an unregistered field is stored on the record
    r = k.record({**base, "entity_id": "app_n1", "payload": {**base["payload"], "platform": "saramin",
                                                            "applicants": 25}}, by="a")
    assert r.status == "recorded" and led.get_entity("app_n1").doc["applicants"] == 25
    assert r.drafts["fields"] == ["applicants"]
    # d. a look-alike name is stored too, with a question — never merged automatically
    r = k.record({**base, "entity_id": "app_n2", "payload": {**base["payload"], "platform": "saramin",
                                                            "applicant_count": 40}}, by="a")
    assert r.status == "recorded" and any("applicants" in q for q in r.questions)
    assert led.get_entity("app_n2").doc["applicant_count"] == 40

    # c. organize: proposal -> refuses without consent / descriptions / labels -> applies with them
    before = led.schemas.get("application").version
    [prop] = k.propose_schema("application")
    assert set(prop["register_fields"]) == {"applicants", "applicant_count"}
    assert prop["register_values"] == {"platform": {"remember": 1}}
    assert {"field": "applicant_count", "into": "applicants"} in prop["merge_suggestions"]
    with pytest.raises(SchemaDefinitionError):
        k.apply_schema_proposal(prop["id"], approved_by="", user_answer="")
    with pytest.raises(SchemaDefinitionError):  # labels for remember are required
        k.apply_schema_proposal(prop["id"], approved_by="user", user_answer="네",
                                descriptions={"applicants": "지원자 수"}, merges={"applicant_count": "applicants"})
    out = k.apply_schema_proposal(prop["id"], approved_by="user", user_answer="네, 정리해 주세요",
                                  descriptions={"applicants": "Number of applicants shown on the posting (지원자 수)"},
                                  labels={"platform": {"remember": ["리멤버"]}},
                                  merges={"applicant_count": "applicants"}, index=["applicants"])
    schema = led.schemas.get("application")
    assert out["version"] == schema.version == before + 1 and out["moved_values"] == 1
    assert "remember" in schema.fields["platform"].enum and schema.fields["platform"].labels["remember"] == ("리멤버",)
    assert schema.fields["applicants"].indexed
    assert led.get_entity("app_n2").doc == {**led.get_entity("app_n2").doc, "applicants": 40}
    assert "applicant_count" not in led.get_entity("app_n2").doc
    assert [e.kind for e in led.history("app_n2")][-1] == "migrated"  # history kept, nothing deleted
    [change] = led.storage.schema_changes("application")
    assert change["approved_by"] == "user" and change["user_answer"] == "네, 정리해 주세요"
    assert k.propose_schema("application") == []  # nothing left to organize


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
