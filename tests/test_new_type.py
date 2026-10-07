"""Issue #6 / ADR-0021: a new kind of record over MCP — stored at once under a draft type with the agent's suggested
schema, registered only when the user approves. Fictional data; LLM calls go to ScriptedLLM."""

import anyio
import pytest
from mcp import Client

from mnemento.demo import open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.mcp_server import create_server

DRAFT = {
    "description": "Notes on books I read (fictional).",
    "keywords": ["독서", "책", "reading"],
    "fields": {
        "title": {"type": "string", "description": "Book title.", "required": True, "indexed": True},
        "read_on": {"type": "string", "format": "date", "description": "Day I finished it."},
        "rating": {"type": "integer", "description": "1-5 stars."},
        "status": {"type": "string", "description": "Where I am with it.", "enum": ["reading", "done"],
                   "labels": {"reading": ["읽는 중"], "done": ["다 읽음"]}},
    },
}
NOTE = {"title": "가상의 책", "read_on": "2026-10-01", "rating": 4, "status": "done"}


@pytest.fixture
def k():
    led = open_demo()
    yield Keeper(led, ScriptedLLM())
    led.close()


def new(k, payload=NOTE, draft=DRAFT, etype="reading_note", **kw):
    req = {"entity_type": etype, "kind": "created", "payload": payload, **kw}
    if draft is not None:
        req["schema_draft"] = draft
    return k.record(req, by="agent_a")


def count(k, etype="reading_note", filters=()):
    return k.ask("q", spec={"entity_type": etype, "mode": "count", "filters": list(filters)}).result["total"]


def test_first_record_of_a_new_kind_is_stored_and_queryable_with_a_pending_proposal(k):
    res = new(k)
    assert res.status == "recorded"
    schema = k.ledger.schemas.get("reading_note")
    assert schema.is_draft and schema.fields == {} and schema.proposal["fields"]["title"]["required"]
    prop = res.proposals[0]
    assert prop["id"] == "type_reading_note_v1" and prop["kind"] == "register_type" and not prop["missing"]
    assert any("apply_schema_proposal('type_reading_note_v1')" in q for q in res.questions)
    # stored fields are drafts until approval: queryable at once
    assert count(k, filters=[{"field": "rating", "op": "gte", "value": 4}]) == 1
    assert any(p["id"] == "type_reading_note_v1" for p in k.propose_schema())


def test_approval_registers_the_type_and_enforcement_starts_then(k):
    new(k)
    assert new(k, payload={"rating": 3}, draft=None).status == "recorded"  # a draft type enforces nothing yet
    with pytest.raises(Exception, match="title"):  # the stored record without a title does not fit `required`
        k.apply_schema_proposal("type_reading_note_v1", approved_by="user", user_answer="응 등록해")
    second = [e for e in k.ledger.find("reading_note") if "title" not in e.doc][0]
    k.record({"entity_type": "reading_note", "kind": "updated", "entity_id": second.id,
              "payload": {"title": "또 다른 가상의 책"}}, by="agent_a")
    out = k.apply_schema_proposal("type_reading_note_v1", approved_by="user", user_answer="응 등록해")
    assert out["registered_type"] and out["version"] == 2 and out["indexed"] == ["title"]
    schema = k.ledger.schemas.get("reading_note")
    assert not schema.is_draft and schema.fields["status"].labels["done"] == ("다 읽음",)
    rejected = new(k, payload={"rating": 5}, draft=None)
    assert rejected.status == "rejected"  # required title is enforced now
    assert k.ledger.storage.schema_changes("reading_note")[-1]["user_answer"] == "응 등록해"
    assert not new(k, payload={"title": "세 번째 책"}, draft=None).proposals  # no more approval questions


def test_without_a_draft_the_proposal_is_inferred_and_needs_descriptions(k):
    res = new(k, draft=None)
    prop = res.proposals[0]
    assert set(prop["fields"]) == set(NOTE) and prop["missing"]
    with pytest.raises(Exception, match="still needed"):
        k.apply_schema_proposal(prop["id"], approved_by="user", user_answer="yes")
    out = k.apply_schema_proposal(prop["id"], approved_by="user", user_answer="yes",
                                  type_description="Books I read.",
                                  descriptions={"title": "Book title.", "read_on": "Day finished.",
                                                "rating": "Stars.", "status": "Progress."})
    assert out["registered_type"]
    assert k.ledger.schemas.get("reading_note").fields["read_on"].format == "date"


def test_a_new_name_that_looks_like_an_existing_type_is_asked_first(k):
    new(k)
    res = new(k, etype="reading_notes", payload={"title": "x"})
    assert res.status == "clarify" and "reading_note" in res.message
    assert "reading_notes" not in k.ledger.schemas.names()
    assert new(k, etype="reading_notes", payload={"title": "x"}, new_type=True).status == "recorded"


def test_a_suggested_schema_that_cannot_be_registered_stores_nothing(k):
    bad = {**DRAFT, "fields": {**DRAFT["fields"], "status": {"type": "string", "description": "s",
                                                             "labels": {"x": ["y"]}}}}
    res = new(k, draft=bad)
    assert res.status == "rejected" and res.errors
    assert "reading_note" not in k.ledger.schemas.names()
    assert new(k, draft={**DRAFT, "kind": "series"}).status == "rejected"


def test_a_new_draft_replaces_the_proposal_and_its_id(k):
    new(k)
    res = new(k, payload={"title": "둘째 책"}, draft={**DRAFT, "description": "Books, with my notes."})
    assert res.proposals[0]["id"] == "type_reading_note_v2"
    with pytest.raises(Exception, match="outdated"):
        k.apply_schema_proposal("type_reading_note_v1", approved_by="user", user_answer="yes")


def test_unknown_type_cannot_start_with_an_update_or_from_free_text(k):
    res = k.record({"entity_type": "reading_note", "kind": "updated", "entity_id": "x", "payload": {}}, by="a")
    assert res.status == "rejected" and "starts with a created record" in res.message
    k.recorder.llm = ScriptedLLM([{"kind": "record", "request": {"entity_type": "reading_note", "kind": "created",
                                                                 "payload": {"title": "t"}}}])
    res = k.record_text("가상의 책 다 읽음", by="a")
    assert res.status == "rejected" and "reading_note" not in k.ledger.schemas.names()


def test_registered_types_refuse_a_schema_draft(k):
    res = k.record({"entity_type": "company", "kind": "created", "payload": {"name": "가상회사"},
                    "schema_draft": DRAFT}, by="a")
    assert res.status == "rejected" and "already registered" in res.message


def test_new_type_over_mcp():
    led = open_demo()
    srv = create_server(Keeper(led, ScriptedLLM()))

    async def go():
        async with Client(srv) as c:
            r = await c.call_tool("record", {"by": "agent_a", "entity_type": "reading_note", "kind": "created",
                                             "payload": NOTE, "schema_draft": DRAFT})
            p = await c.call_tool("propose_schema", {"entity_type": "reading_note"})
            a = await c.call_tool("apply_schema_proposal", {"proposal_id": "type_reading_note_v1",
                                                            "approved_by": "user", "user_answer": "등록해"})
            s = await c.call_tool("list_schemas", {"name": "reading_note"})
            return r, p, a, s

    r, p, a, s = anyio.run(go)
    assert r.structured_content["status"] == "recorded" and r.structured_content["proposals"]
    assert p.structured_content["proposals"][0]["kind"] == "register_type"
    assert a.structured_content["registered_type"]
    assert "status" not in s.structured_content["schemas"][0] and s.structured_content["schemas"][0]["version"] == 2
    led.close()
