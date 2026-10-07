"""Issue #12 / ADR-0023: repository dictionary improvements reach a database the user organized — item by item,
additive ones by default, replacing ones only when picked, breaking ones never; nothing without approval."""

import json

import pytest

from mnemento import Ledger
from mnemento.errors import SchemaDefinitionError
from mnemento.keeper import Keeper, ScriptedLLM

V1 = {"name": "task", "version": 1, "description": "A to-do item (fictional).", "keywords": ["할 일"],
      "fields": {"title": {"type": "string", "description": "What to do.", "required": True},
                 "status": {"type": "string", "description": "Progress.", "enum": ["open", "done"],
                            "labels": {"open": ["진행 중"], "done": ["완료"]}},
                 "due": {"type": "string", "format": "date", "description": "Due day."}}}


def file_v2():
    d = json.loads(json.dumps(V1))
    d["version"] = 2
    d["keywords"] = ["할 일", "todo"]
    d["vague_terms"] = {"곧": "due within 3 days"}
    d["default_date_field"] = "due"
    f = d["fields"]
    f["title"]["description"] = "What to do, in one line."
    f["status"]["enum"] = ["open", "done", "blocked"]
    f["status"]["labels"] = {"open": ["진행 중"], "done": ["완료", "끝남"], "blocked": ["막힘"]}
    f["status"]["indexed"] = True
    f["priority"] = {"type": "integer", "description": "1 (high) to 3."}
    f["owner"] = {"type": "string", "description": "Who.", "required": True}
    f["due"] = {"type": "string", "format": "date-time", "description": "Due time."}
    return d


@pytest.fixture
def k(tmp_path):
    led = Ledger.open(tmp_path / "t.db")
    led.schemas.register(V1)
    keeper = Keeper(led, ScriptedLLM())
    # the user organizes the type once (ADR-0014): from then on the database is its dictionary
    keeper.record({"entity_type": "task", "kind": "created", "payload": {"title": "가상의 일", "tag": "x"}}, by="a")
    prop = next(p for p in keeper.propose_schema("task") if p["kind"] == "organize_schema")
    keeper.apply_schema_proposal(prop["id"], approved_by="user", user_answer="응", descriptions={"tag": "A tag."})
    d = tmp_path / "schemas"
    d.mkdir()
    (d / "task.json").write_text(json.dumps(file_v2(), ensure_ascii=False), encoding="utf-8")
    led.schemas.load_dir(d)
    yield keeper
    led.close()


def dict_prop(k):
    return next((p for p in k.propose_schema() if p["kind"] == "update_dictionary"), None)


def test_the_difference_is_offered_item_by_item(k):
    assert "organized by the user" in k.ledger.schemas.load_notes[0]
    p = dict_prop(k)
    keys = {i["key"]: i["default"] for i in p["items"]}
    assert keys == {"keyword:todo": True, "vague:곧": True, "default_date_field": True, "field:priority": True,
                    "value:status=blocked": True, "labels:status=done": True, "index:status": True,
                    "description:title": False}
    assert any("owner" in n and "required" in n for n in p["not_applicable"])
    assert any(n.startswith("due: type/format differs") for n in p["not_applicable"])
    assert "Also change" in p["question"] and "description:title" in p["question"]


def test_default_apply_takes_the_additive_items_only_and_does_not_ask_again(k):
    p = dict_prop(k)
    before = k.ledger.schemas.get("task").version
    out = k.apply_schema_proposal(p["id"], approved_by="user", user_answer="추가분만 넣어")
    s = k.ledger.schemas.get("task")
    assert s.version == before + 1 and "description:title" in out["declined"]
    assert s.fields["status"].enum == ("open", "done", "blocked") and s.fields["status"].labels["done"] == ("완료", "끝남")
    assert s.fields["status"].indexed and "priority" in s.fields and "owner" not in s.fields
    assert s.fields["title"].description == "What to do." and s.fields["due"].format == "date"
    assert dict(s.vague_terms)["곧"] == "due within 3 days" and "todo" in s.keywords
    assert dict_prop(k) is None  # the declined description is not offered again for the same file
    assert k.ledger.storage.schema_changes("task")[-1]["user_answer"] == "추가분만 넣어"


def test_picked_items_include_a_replacement(k):
    p = dict_prop(k)
    out = k.apply_schema_proposal(p["id"], approved_by="user", user_answer="설명도 바꿔",
                                  items=["description:title", "keyword:todo"])
    s = k.ledger.schemas.get("task")
    assert out["applied"] == ["keyword:todo", "description:title"] or set(out["applied"]) == {"description:title",
                                                                                              "keyword:todo"}
    assert s.fields["title"].description == "What to do, in one line." and "priority" not in s.fields


def test_nothing_without_approval_or_with_unknown_items(k):
    p = dict_prop(k)
    with pytest.raises(SchemaDefinitionError):
        k.apply_schema_proposal(p["id"], approved_by="", user_answer="")
    with pytest.raises(SchemaDefinitionError, match="unknown item keys"):
        k.apply_schema_proposal(p["id"], approved_by="user", user_answer="yes", items=["field:nope"])
