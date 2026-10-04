import pytest

from mnemento.demo import open_demo
from mnemento.keeper.identity import IdentityResolver, normalize_name


@pytest.mark.parametrize(
    "raw,key",
    [
        ("(주)가상테크", "가상테크"),
        ("㈜가상테크", "가상테크"),
        ("주식회사 가상테크", "가상테크"),
        ("가상 테크 주식회사", "가상테크"),
        ("가상테크(Gasang Tech)", "가상테크"),
        ("Example Labs Inc.", "examplelabs"),
        ("EXAMPLE LABS, INC", "examplelabs"),
        ("Princeton Tools Co., Ltd.", "princetontools"),  # 'inc' inside a word survives
        ("Sample Labs", "samplelabs"),
    ],
)
def test_normalize_name(raw, key):
    assert normalize_name(raw) == key


@pytest.fixture(scope="module")
def resolver():
    led = open_demo()
    yield IdentityResolver(led, "company")
    led.close()


@pytest.mark.parametrize(
    "query,rule",
    [("가상테크", "normalized_name"), ("(주)가상테크", "normalized_name"), ("주식회사 가상테크", "normalized_name"),
     ("Gasang Tech", "alias"), ("GASANG TECH", "alias"), ("Sample Labs", "alias")],
)
def test_same_company_by_name_or_alias(resolver, query, rule):
    res = resolver.resolve(query)
    assert res.status == "match" and res.rule == rule
    assert res.matches[0] in {"co_gasangtech", "co_samplelabs"}


def test_similar_name_is_only_a_candidate(resolver):
    res = resolver.resolve("가상텍")
    assert res.status == "candidates" and res.matches == []
    assert res.candidates[0]["id"] == "co_gasangtech"


def test_unknown_name_has_no_match(resolver):
    assert resolver.resolve("전혀없는회사").status == "none"


def test_business_number_is_strongest(tmp_path):
    from mnemento import Ledger
    from mnemento.demo import SCHEMA_DIR

    led = Ledger.open(tmp_path / "b.db")
    led.schemas.load_dir(SCHEMA_DIR)
    t = "2026-10-01T09:00:00+09:00"
    led.record_event("co_a", "created", {"name": "알파", "normalized_name": "알파",
                                         "business_number": "000-00-00001"}, t, "a", entity_type="company")
    led.record_event("co_b", "created", {"name": "베타", "normalized_name": "베타"}, t, "a",
                     entity_type="company")
    res = IdentityResolver(led, "company").resolve("0000000001")
    assert res.matches == ["co_a"] and res.rule == "identifier:business_number"
    led.close()


def test_name_resolution_in_query(resolver):
    from datetime import datetime

    from mnemento.demo import DEMO_NOW
    from mnemento.keeper import Keeper, ScriptedLLM

    k = Keeper(resolver.ledger, ScriptedLLM())
    spec = {"entity_type": "application", "mode": "list",
            "filters": [{"field": "company_id", "op": "name_is", "value": "Gasang Tech"}]}
    ans = k.ask("Gasang Tech 예전에 지원한 적 있나?", spec=spec, now=datetime.fromisoformat(DEMO_NOW))
    assert ans.status == "answered" and ans.evidence == ["app_o02"]
    assert ans.resolved["names"]["Gasang Tech"] == {"matches": ["co_gasangtech"], "rule": "alias"}

    ans = k.ask("가상텍 지원했나?", spec={**spec, "filters": [
        {"field": "company_id", "op": "name_is", "value": "가상텍"}]}, now=datetime.fromisoformat(DEMO_NOW))
    assert ans.status == "clarify" and ans.options[0].startswith("co_gasangtech")


def test_business_number_inside_a_sentence_and_identifier_validation(tmp_path):
    """Step 3b: the identifier rule is general — a business number written inside a sentence matches."""
    import pytest

    from mnemento import Ledger
    from mnemento.errors import SchemaDefinitionError
    from mnemento.keeper.identity import IdentityResolver
    from mnemento.schema.definition import SchemaDef

    from .conftest import SCHEMA_DIR

    led = Ledger.open(tmp_path / "i.db")
    led.schemas.load_dir(SCHEMA_DIR)
    t = "2026-10-01T09:00:00+09:00"
    led.record_event("co_x", "created", {"name": "가상솔루션", "normalized_name": "가상솔루션", "business_number": "111-22-33333"}, t, "a",
                     entity_type="company")
    res = IdentityResolver(led, "company").resolve("사업자번호 111-22-33333인 회사")
    assert res.matches == ["co_x"] and res.rule == "identifier:business_number"
    base = {"name": "x", "version": 1, "description": "x"}
    with pytest.raises(SchemaDefinitionError):
        SchemaDef.from_dict({**base, "fields": {"k": {"type": "string", "description": "k", "enum": ["a"],
                                                      "identifier": True}}})
    with pytest.raises(SchemaDefinitionError):
        SchemaDef.from_dict({**base, "fields": {"k": {"type": "integer", "description": "k", "identifier": True}}})
    led.close()
