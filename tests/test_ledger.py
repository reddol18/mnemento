import pytest

from mnemento.errors import (
    ConflictError,
    DocumentValidationError,
    EntityExistsError,
    EntityNotFoundError,
    EntityRetractedError,
    InvalidEventError,
    InvalidTimeError,
    UnknownFieldError,
)

from .conftest import AT, make_app

T1 = "2026-10-02T09:00:00+09:00"
T2 = "2026-10-02T17:40:00+09:00"
T3 = "2026-10-03T10:00:00+09:00"


def n_events(ledger):
    return ledger.storage.execute("SELECT COUNT(*) FROM events")[0][0]


def saramin_on_1002(ledger):
    return ledger.count("application", {"platform": "saramin", "applied_at": "2026-10-02"})


# ---- validation: rejected writes store nothing -------------------------------------------

@pytest.mark.parametrize(
    "doc",
    [
        {"company_id": "co_x", "platform": "saramin", "status": "pending", "applied_at": "2026-10-02"},
        {"company_id": "co_x", "platform": "saramin", "status": "applied"},  # missing applied_at
        {"company_id": "co_x", "platform": "saramin", "status": "applied", "applied_at": "10/02"},
        {"company_id": "co_x", "platform": "saramin", "status": "applied", "applied_at": "2026-10-02",
         "salary": 1},  # field not in the dictionary
    ],
)
def test_schema_violation_rejects_create(ledger, doc):
    with pytest.raises(DocumentValidationError):
        ledger.record_event("app_x", "created", doc, AT, "agent", entity_type="application")
    assert ledger.get_entity("app_x") is None
    assert n_events(ledger) == 0


def test_schema_violation_rejects_update(ledger):
    make_app(ledger, 1)
    before = n_events(ledger)
    with pytest.raises(DocumentValidationError):
        ledger.record_event("app_saramin_1", "updated", {"platform": "linkedin"}, T1, "agent")
    with pytest.raises(DocumentValidationError):  # removing a required field
        ledger.record_event("app_saramin_1", "updated", {"applied_at": None}, T1, "agent")
    assert n_events(ledger) == before
    assert ledger.get_entity("app_saramin_1").doc["platform"] == "saramin"


@pytest.mark.parametrize("at", ["2026-10-02T17:40:00", "2026-10-02 17:40", "2026-10-02"])
def test_at_without_offset_rejected(ledger, at):
    with pytest.raises(InvalidTimeError):
        make_app(ledger, 1, at=at)
    assert n_events(ledger) == 0


def test_date_time_field_without_offset_rejected(ledger):
    ledger.schemas.register({
        "name": "meeting", "version": 1, "description": "A fictional meeting.",
        "fields": {"starts": {"type": "string", "format": "date-time", "description": "Start.",
                              "required": True}},
    })
    ledger.record_event("m1", "created", {"starts": "2026-10-02T15:00:00+09:00"}, T1, "a",
                        entity_type="meeting")
    with pytest.raises(DocumentValidationError):
        ledger.record_event("m2", "created", {"starts": "2026-10-02T15:00:00"}, T1, "a",
                            entity_type="meeting")
    assert ledger.get_entity("m2") is None


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("deleted", {}),
        ("updated", {}),
        ("status_changed", {"from": "applied"}),
        ("status_changed", {"to": "viewed", "note": "x"}),
        ("corrected", {"target": "evt_x"}),
        ("retracted", {"reason": "x"}),
    ],
)
def test_malformed_events_rejected(ledger, kind, payload):
    make_app(ledger, 1)
    with pytest.raises(InvalidEventError):
        ledger.record_event("app_saramin_1", kind, payload, T1, "agent", evidence="e")


def test_create_requires_type_and_unique_id(ledger):
    doc = {"company_id": "co_x", "platform": "saramin", "status": "applied", "applied_at": "2026-10-02"}
    with pytest.raises(InvalidEventError):
        ledger.record_event("app_1", "created", doc, AT, "agent")
    ledger.record_event("app_1", "created", doc, AT, "agent", entity_type="application")
    with pytest.raises(EntityExistsError):
        ledger.record_event("app_1", "created", doc, AT, "agent", entity_type="application")


def test_event_on_missing_entity(ledger):
    with pytest.raises(EntityNotFoundError):
        ledger.record_event("app_none", "updated", {"reason": "x"}, T1, "agent")


def test_by_required(ledger):
    make_app(ledger, 1)
    with pytest.raises(InvalidEventError):
        ledger.record_event("app_saramin_1", "updated", {"reason": "x"}, T1, " ")


# ---- status_changed ----------------------------------------------------------------------

def test_status_changed_updates_state_and_keeps_history(ledger):
    make_app(ledger, 1)
    e = ledger.record_event(
        "app_saramin_1", "status_changed", {"from": "applied", "to": "viewed"}, T2,
        by="agent_a", evidence="user: '열람'",
    )
    state = ledger.get_entity("app_saramin_1")
    assert state.doc["status"] == "viewed"
    assert state.updated_at == T2

    hist = ledger.history("app_saramin_1")
    assert [h.kind for h in hist] == ["created", "status_changed"]
    assert hist[0].payload["status"] == "applied"  # original preserved
    assert hist[1].id == e.id and hist[1].by == "agent_a" and hist[1].evidence == "user: '열람'"
    assert hist[1].at == T2 and hist[1].recorded_at  # occurrence vs recording time
    assert ledger.count("application", {"status": "viewed"}) == 1
    assert ledger.count("application", {"status": "applied"}) == 0


def test_status_changed_from_mismatch_is_conflict(ledger):
    make_app(ledger, 1)
    with pytest.raises(ConflictError):
        ledger.record_event("app_saramin_1", "status_changed", {"from": "viewed", "to": "passed"}, T2, "a")
    assert ledger.get_entity("app_saramin_1").doc["status"] == "applied"


def test_status_changed_to_unknown_value_rejected(ledger):
    make_app(ledger, 1)
    with pytest.raises(DocumentValidationError):
        ledger.record_event("app_saramin_1", "status_changed", {"to": "ghosted"}, T2, "a")


def test_backfilled_event_applies_in_occurrence_order(ledger):
    make_app(ledger, 1)
    # recorded first, happened later
    ledger.record_event("app_saramin_1", "status_changed", {"to": "rejected"}, T3, "a")
    # recorded second, happened earlier
    ledger.record_event("app_saramin_1", "status_changed", {"to": "viewed"}, T2, "a")
    assert ledger.get_entity("app_saramin_1").doc["status"] == "rejected"


def test_updated_sets_and_removes_fields(ledger):
    make_app(ledger, 1, reason="fit")
    ledger.record_event("app_saramin_1", "updated", {"viewed_at": "2026-10-02", "reason": None}, T2, "a")
    doc = ledger.get_entity("app_saramin_1").doc
    assert doc["viewed_at"] == "2026-10-02" and "reason" not in doc


# ---- corrected / retracted change aggregates ----------------------------------------------

def test_corrected_changes_count(ledger):
    for n in range(1, 13):
        make_app(ledger, n)
    # one application was wrongly recorded as made on 10/1
    wrong = make_app(ledger, 13, applied_at="2026-10-01")
    assert saramin_on_1002(ledger) == 12

    fixed = {**wrong.payload, "applied_at": "2026-10-02"}
    ledger.record_event("app_saramin_13", "corrected", {"target": wrong.id, "payload": fixed}, T3,
                        by="agent_a", evidence="지원완료 메일 10/2 수신")
    assert saramin_on_1002(ledger) == 13

    hist = ledger.history("app_saramin_13")
    assert hist[0].payload["applied_at"] == "2026-10-01"  # original event untouched
    assert hist[1].kind == "corrected" and hist[1].target_event_id == wrong.id


def test_correcting_a_status_change(ledger):
    make_app(ledger, 1)
    sc = ledger.record_event("app_saramin_1", "status_changed", {"to": "rejected"}, T2, "a")
    assert ledger.count("application", {"status": "rejected"}) == 1
    ledger.record_event("app_saramin_1", "corrected", {"target": sc.id, "payload": {"to": "viewed"}},
                        T3, "a", evidence="was viewed, not rejected")
    assert ledger.count("application", {"status": "rejected"}) == 0
    assert ledger.get_entity("app_saramin_1").doc["status"] == "viewed"


def test_latest_correction_wins(ledger):
    created = make_app(ledger, 1)
    for reason in ("first", "second"):
        ledger.record_event("app_saramin_1", "corrected",
                            {"target": created.id, "payload": {**created.payload, "reason": reason}},
                            T3, "a", evidence="fix")
    assert ledger.get_entity("app_saramin_1").doc["reason"] == "second"


def test_invalid_correction_rejected(ledger):
    created = make_app(ledger, 1)
    with pytest.raises(DocumentValidationError):
        ledger.record_event("app_saramin_1", "corrected",
                            {"target": created.id, "payload": {**created.payload, "status": "x"}},
                            T3, "a", evidence="fix")
    with pytest.raises(InvalidEventError):  # requires evidence
        ledger.record_event("app_saramin_1", "corrected",
                            {"target": created.id, "payload": created.payload}, T3, "a")
    with pytest.raises(InvalidEventError):  # target from another entity
        other = make_app(ledger, 2)
        ledger.record_event("app_saramin_1", "corrected",
                            {"target": other.id, "payload": created.payload}, T3, "a", evidence="e")
    assert len(ledger.history("app_saramin_1")) == 1


def test_retracted_entity_excluded_from_count(ledger):
    for n in range(1, 4):
        make_app(ledger, n)
    assert saramin_on_1002(ledger) == 3
    ledger.record_event("app_saramin_2", "retracted", {}, T3, by="agent_a",
                        evidence="duplicate of app_saramin_1")
    assert saramin_on_1002(ledger) == 2

    state = ledger.get_entity("app_saramin_2")
    assert state.retracted is True  # still visible in history
    assert [e.kind for e in ledger.history("app_saramin_2")] == ["created", "retracted"]
    assert len(ledger.find("application", {"platform": "saramin"}, include_retracted=True)) == 3
    with pytest.raises(EntityRetractedError):
        ledger.record_event("app_saramin_2", "updated", {"reason": "x"}, T3, "a")


def test_retracted_event_is_voided(ledger):
    make_app(ledger, 1)
    sc = ledger.record_event("app_saramin_1", "status_changed", {"to": "rejected"}, T2, "a")
    assert ledger.count("application", {"status": "rejected"}) == 1
    ledger.record_event("app_saramin_1", "retracted", {"target": sc.id}, T3, "a", evidence="wrong entity")
    assert ledger.count("application", {"status": "rejected"}) == 0
    assert ledger.get_entity("app_saramin_1").doc["status"] == "applied"
    with pytest.raises(InvalidEventError):  # already retracted
        ledger.record_event("app_saramin_1", "retracted", {"target": sc.id}, T3, "a", evidence="again")


def test_retracting_a_correction_restores_original(ledger):
    created = make_app(ledger, 1, reason="orig")
    fix = ledger.record_event("app_saramin_1", "corrected",
                              {"target": created.id, "payload": {**created.payload, "reason": "fixed"}},
                              T2, "a", evidence="fix")
    ledger.record_event("app_saramin_1", "retracted", {"target": fix.id}, T3, "a", evidence="fix was wrong")
    assert ledger.get_entity("app_saramin_1").doc["reason"] == "orig"


def test_cannot_retract_created_event(ledger):
    created = make_app(ledger, 1)
    with pytest.raises(InvalidEventError):
        ledger.record_event("app_saramin_1", "retracted", {"target": created.id}, T3, "a", evidence="e")


# ---- rebuild -----------------------------------------------------------------------------

def _snapshot(ledger):
    rows = ledger.storage.execute(
        "SELECT id, type, schema_version, doc, retracted, created_at, updated_at, last_event_seq, "
        "event_ids FROM entities ORDER BY id"
    )
    return [tuple(r) for r in rows]


def _busy_history(ledger):
    for n in range(1, 6):
        make_app(ledger, n)
    sc = ledger.record_event("app_saramin_1", "status_changed", {"from": "applied", "to": "viewed"}, T2, "a")
    ledger.record_event("app_saramin_1", "updated", {"viewed_at": "2026-10-02"}, T2, "a")
    ledger.record_event("app_saramin_1", "status_changed", {"to": "rejected"}, T3, "a")
    ledger.record_event("app_saramin_2", "retracted", {}, T3, "a", evidence="dup")
    bad = ledger.record_event("app_saramin_3", "status_changed", {"to": "passed"}, T2, "a")
    ledger.record_event("app_saramin_3", "retracted", {"target": bad.id}, T3, "a", evidence="wrong")
    c = ledger.history("app_saramin_4")[0]
    ledger.record_event("app_saramin_4", "corrected",
                        {"target": c.id, "payload": {**c.payload, "applied_at": "2026-10-01"}},
                        T3, "a", evidence="fix")
    ledger.record_event("co_fake1", "created", {"name": "(주)가짜회사", "normalized_name": "가짜회사"},
                        AT, "a", entity_type="company")
    return sc


def test_rebuild_entity_matches_incremental_state(ledger):
    _busy_history(ledger)
    for eid in ("app_saramin_1", "app_saramin_2", "app_saramin_3", "app_saramin_4", "co_fake1"):
        before = ledger.get_entity(eid)
        rebuilt = ledger.rebuild_entity(eid)
        assert rebuilt == before


def test_rebuild_all_from_events_is_identical(ledger):
    _busy_history(ledger)
    before = _snapshot(ledger)
    counts_before = (saramin_on_1002(ledger), ledger.count("application", {"status": "rejected"}))

    ledger.storage.delete_all_entities()
    assert _snapshot(ledger) == []
    assert ledger.rebuild_all() == 6

    assert _snapshot(ledger) == before
    assert (saramin_on_1002(ledger), ledger.count("application", {"status": "rejected"})) == counts_before


def test_failed_write_leaves_db_untouched(ledger):
    _busy_history(ledger)
    before, n = _snapshot(ledger), n_events(ledger)
    with pytest.raises(ConflictError):
        ledger.record_event("app_saramin_5", "status_changed", {"from": "viewed", "to": "passed"}, T3, "a")
    assert _snapshot(ledger) == before and n_events(ledger) == n


# ---- query guard -------------------------------------------------------------------------

def test_find_rejects_fields_outside_schema(ledger):
    make_app(ledger, 1)
    with pytest.raises(UnknownFieldError):
        ledger.find("application", {"salary": 100})


def test_find_non_indexed_field(ledger):
    make_app(ledger, 1, reason="fit")
    make_app(ledger, 2)
    assert [s.id for s in ledger.find("application", {"reason": "fit"})] == ["app_saramin_1"]
