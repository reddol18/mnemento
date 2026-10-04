import sqlite3

import pytest

from mnemento import SQLiteStorage

from .conftest import make_app


def test_wal_mode(tmp_path):
    st = SQLiteStorage(tmp_path / "w.db")
    assert st.execute("PRAGMA journal_mode")[0][0] == "wal"
    st.close()


def test_tables_exist(ledger):
    names = {r[0] for r in ledger.storage.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"schemas", "events", "entities"} <= names


def test_indexed_fields_become_generated_columns(ledger):
    cols = {r["name"]: r["hidden"] for r in ledger.storage.execute("PRAGMA table_xinfo(entities)")}
    for field in ("status", "applied_at", "platform", "company_id", "normalized_name", "deadline"):
        assert f"f_{field}" in cols
        assert cols[f"f_{field}"] == 2  # 2 = virtual generated column
    assert "f_reason" not in cols  # not indexed
    indexes = {r["name"] for r in ledger.storage.execute("PRAGMA index_list(entities)")}
    assert "ix_entities_status" in indexes


def test_generated_column_tracks_document(ledger):
    make_app(ledger, 1, status="applied")
    row = ledger.storage.execute("SELECT f_status, f_applied_at FROM entities WHERE id='app_saramin_1'")[0]
    assert tuple(row) == ("applied", "2026-10-02")


def test_indexed_query_uses_index(ledger):
    plan = ledger.storage.explain_find("application", {"status": "viewed"}, {"status"})
    assert "USING INDEX" in plan or "USING COVERING INDEX" in plan


def test_events_are_append_only(ledger):
    make_app(ledger, 1)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.storage.execute("UPDATE events SET by='someone else'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.storage.execute("DELETE FROM events")


def test_reopen_persists(tmp_path):
    from mnemento import Ledger

    from .conftest import SCHEMA_DIR

    path = tmp_path / "p.db"
    led = Ledger.open(path)
    led.schemas.load_dir(SCHEMA_DIR)
    make_app(led, 7)
    version = led.schemas.get("application").version
    led.close()

    led2 = Ledger.open(path)
    assert led2.get_entity("app_saramin_7").doc["status"] == "applied"
    assert led2.schemas.get("application").version == version
    led2.close()
