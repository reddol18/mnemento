"""Issue #9 / ADR-0028: one phrase that means several enum values ("주시 중" = watching + observe_only) — in the
dictionary, shown to the interpreter, added to a user's database only with consent. Fictional data."""

import pytest

from mnemento import Ledger
from mnemento.errors import SchemaDefinitionError
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.keeper.query.interpret import render_dictionary

WATCH = {"name": "watch_item", "version": 1, "description": "A security I keep an eye on (fictional).",
         "keywords": ["주시", "관심"],
         "fields": {"name": {"type": "string", "description": "Security name.", "required": True},
                    "status": {"type": "string", "description": "Where it stands.",
                               "enum": ["watching", "observe_only", "dropped"],
                               "labels": {"watching": ["주시"], "observe_only": ["관찰만"], "dropped": ["제외"]}}}}


@pytest.fixture
def k():
    led = Ledger.open(":memory:")
    led.schemas.register(WATCH)
    keeper = Keeper(led, ScriptedLLM())
    keeper.pipeline.query_log = None
    for i, st in enumerate(["watching", "observe_only", "dropped", "watching"]):
        keeper.record({"entity_type": "watch_item", "kind": "created", "payload": {"name": f"가상종목{i}", "status": st}},
                      by="t")
    yield keeper
    led.close()


def test_a_value_group_needs_consent_and_is_recorded(k):
    with pytest.raises(SchemaDefinitionError, match="explicit consent"):
        k.add_value_group("watch_item", "status", "주시 중", ["watching", "observe_only"], approved_by="",
                          user_answer="")
    out = k.add_value_group("watch_item", "status", "주시 중", ["watching", "observe_only"], approved_by="user",
                            user_answer="응, 주시 중은 둘 다야")
    s = k.ledger.schemas.get("watch_item")
    assert out["version"] == 2 and s.fields["status"].value_groups == {"주시 중": ("watching", "observe_only")}
    assert k.ledger.storage.schema_changes("watch_item")[-1]["user_answer"] == "응, 주시 중은 둘 다야"


@pytest.mark.parametrize("values", [["watching"], ["watching", "unknown_status"]])
def test_a_group_takes_two_or_more_allowed_values(k, values):
    with pytest.raises(SchemaDefinitionError):
        k.add_value_group("watch_item", "status", "주시 중", values, approved_by="user", user_answer="yes")


def test_the_interpreter_sees_the_group_and_the_in_filter_counts_both(k):
    k.add_value_group("watch_item", "status", "주시 중", ["watching", "observe_only"], approved_by="user",
                      user_answer="yes")
    text = render_dictionary([k.ledger.schemas.get("watch_item")], {})
    assert "phrase groups (op eq with the list of all of them): '주시 중' = watching, observe_only" in text
    ans = k.ask("지금 주시 중인 종목", spec={"entity_type": "watch_item", "mode": "count", "filters": [
        {"field": "status", "op": "in", "value": ["watching", "observe_only"]}]})
    assert ans.result["total"] == 3


def test_a_repository_file_offers_its_groups_to_an_organized_database(k, tmp_path):
    import json

    # the user organized the type once (ADR-0014), so the file is offered as items (ADR-0023)
    k.record({"entity_type": "watch_item", "kind": "created", "payload": {"name": "x", "note": "draft"}}, by="t")
    prop = next(p for p in k.propose_schema("watch_item") if p["kind"] == "organize_schema")
    k.apply_schema_proposal(prop["id"], approved_by="user", user_answer="ok", descriptions={"note": "A note."})
    d = json.loads(json.dumps(WATCH))
    d["version"] = 9
    d["fields"]["status"]["value_groups"] = {"주시 중": ["watching", "observe_only"]}
    (tmp_path / "watch_item.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    k.ledger.schemas.load_dir(tmp_path)
    p = next(p for p in k.propose_schema() if p["kind"] == "update_dictionary")
    assert {"key": "group:status=주시 중", "default": True} in [{x: i[x] for x in ("key", "default")} for i in p["items"]]
    k.apply_schema_proposal(p["id"], approved_by="user", user_answer="추가분만", items=["group:status=주시 중"])
    assert k.ledger.schemas.get("watch_item").fields["status"].value_groups["주시 중"] == ("watching", "observe_only")
