import copy
import json

import pytest

from mnemento import Ledger, SchemaDef
from mnemento.errors import (
    BreakingSchemaChangeError,
    DocumentValidationError,
    SchemaDefinitionError,
    SchemaNotFoundError,
)

from .conftest import SCHEMA_DIR

BASE = {
    "name": "note",
    "version": 1,
    "description": "A fictional note.",
    "fields": {
        "title": {"type": "string", "description": "Title.", "required": True, "indexed": True},
        "kind": {"type": "string", "description": "Kind.", "enum": ["a", "b"]},
        "day": {"type": "string", "description": "Day.", "format": "date"},
    },
}


@pytest.fixture
def reg(tmp_path):
    led = Ledger.open(tmp_path / "s.db")
    yield led.schemas
    led.close()


def v2(**field_changes):
    d = copy.deepcopy(BASE)
    d["version"] = 2
    for name, fdef in field_changes.items():
        if fdef is None:
            del d["fields"][name]
        else:
            d["fields"][name] = fdef
    return d


# ---- definitions -------------------------------------------------------------------------

def test_compiles_to_strict_json_schema():
    js = SchemaDef.from_dict(BASE).json_schema()
    assert js["additionalProperties"] is False
    assert js["required"] == ["title"]
    assert js["properties"]["kind"]["enum"] == ["a", "b"]
    assert js["properties"]["day"]["format"] == "date"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(name="Bad-Name"),
        lambda d: d["fields"].update({"x": {"type": "string"}}),  # no description
        lambda d: d["fields"].update({"x": {"type": "date", "description": "d"}}),
        lambda d: d["fields"].update({"x": {"type": "string", "description": "d", "format": "time"}}),
        lambda d: d["fields"].update({"x": {"type": "string", "description": "d", "enum": []}}),
        lambda d: d["fields"].update({"x": {"type": "string", "description": "d", "colour": 1}}),
        lambda d: d["fields"].update({"Bad Field": {"type": "string", "description": "d"}}),
        lambda d: d.update(examples=[{"id": "n1", "doc": {"kind": "a"}}]),  # missing required title
    ],
)
def test_malformed_definitions_rejected(mutate):
    d = copy.deepcopy(BASE)
    mutate(d)
    with pytest.raises(SchemaDefinitionError):
        SchemaDef.from_dict(d)


def test_validate_reports_errors():
    s = SchemaDef.from_dict(BASE)
    s.validate({"title": "t", "kind": "a", "day": "2026-10-02"})
    with pytest.raises(DocumentValidationError) as exc:
        s.validate({"kind": "c", "day": "10/02", "extra": 1})
    msg = str(exc.value)
    assert "title" in msg and "'c' is not one of" in msg and "extra" in msg and "date" in msg


def test_date_time_field_requires_offset():
    d = copy.deepcopy(BASE)
    d["fields"]["when"] = {"type": "string", "description": "w", "format": "date-time"}
    s = SchemaDef.from_dict(d)
    s.validate({"title": "t", "when": "2026-10-02T17:40:00+09:00"})
    with pytest.raises(DocumentValidationError):
        s.validate({"title": "t", "when": "2026-10-02T17:40:00"})


# ---- registry ----------------------------------------------------------------------------

def test_register_and_get(reg):
    reg.register(BASE)
    s = reg.get("note")
    assert s.version == 1 and s.fields["kind"].enum == ("a", "b")
    assert reg.names() == ["note"]
    with pytest.raises(SchemaNotFoundError):
        reg.get("nope")


def test_reregister_identical_is_noop(reg):
    reg.register(BASE)
    reg.register(BASE)
    assert [r.version for r in reg.history("note")] == [1]


def test_first_registration_starts_history_at_its_version(reg):
    reg.register({**BASE, "version": 2})  # a new database may begin from the current definition
    assert [r.version for r in reg.history("note")] == [2]
    with pytest.raises(SchemaDefinitionError):  # but later versions must still follow on
        reg.register({**v2(), "version": 4, "description": "changed"})


def test_additive_bump_keeps_history(reg):
    reg.register(BASE)
    reg.register(v2(kind={"type": "string", "description": "Kind.", "enum": ["a", "b", "c"]},
                    extra={"type": "string", "description": "New optional field."}))
    assert reg.get("note").version == 2
    assert "extra" not in reg.get("note", 1).fields  # old version still answerable (ADR-0005)
    assert [r.version for r in reg.history("note")] == [1, 2]


def test_bump_assigns_next_version(reg):
    reg.register(BASE)
    d = copy.deepcopy(BASE)
    d["fields"]["extra"] = {"type": "string", "description": "x"}
    assert reg.bump(d).version == 2


def test_version_gap_rejected(reg):
    reg.register(BASE)
    with pytest.raises(SchemaDefinitionError):
        reg.register({**v2(), "version": 3, "description": "changed"})


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": None},  # removed
        {"kind": {"type": "integer", "description": "Kind."}},  # type changed
        {"kind": {"type": "string", "description": "Kind.", "enum": ["a"]}},  # enum shrunk
        {"day": {"type": "string", "description": "Day.", "format": "date", "required": True}},
        {"day": {"type": "string", "description": "Day.", "format": "date-time"}},
        {"new": {"type": "string", "description": "n", "required": True}},
    ],
)
def test_breaking_bump_rejected(reg, changes):
    reg.register(BASE)
    with pytest.raises(BreakingSchemaChangeError):
        reg.register(v2(**changes))
    assert reg.get("note").version == 1


# ---- domain schemas ----------------------------------------------------------------------

def test_domain_schemas_load_and_examples_valid(reg):
    loaded = reg.load_dir(SCHEMA_DIR)
    assert sorted(s.name for s in loaded) == ["application", "company", "posting"]
    for s in loaded:
        assert s.examples, f"{s.name} should ship fictional examples"
        for f in s.fields.values():
            assert f.description.strip()


def test_domain_schema_files_are_valid_json():
    for p in SCHEMA_DIR.glob("*.json"):
        SchemaDef.from_dict(json.loads(p.read_text(encoding="utf-8")))


def test_application_status_values():
    s = SchemaDef.from_dict(json.loads((SCHEMA_DIR / "application.json").read_text(encoding="utf-8")))
    assert s.fields["status"].enum == ("applied", "viewed", "passed", "rejected", "withdrawn")
    assert s.fields["applied_at"].format == "date"


def test_load_dir_keeps_a_database_that_moved_ahead(tmp_path):
    """After an approved organize the database holds v(n+1); restarting with the v(n) files must not fail."""
    import json as _json

    from mnemento import Ledger

    d = {"name": "note", "version": 1, "description": "a note", "fields": {"title": {"type": "string", "description": "title"}}}
    (tmp_path / "note.json").write_text(_json.dumps(d), encoding="utf-8")
    led = Ledger.open(tmp_path / "s.db")
    led.schemas.load_dir(tmp_path)
    led.schemas.bump({**d, "fields": {**d["fields"], "body": {"type": "string", "description": "body"}}})
    assert [s.version for s in led.schemas.load_dir(tmp_path)] == [2]
    assert led.schemas.get("note").version == 2
    led.close()
