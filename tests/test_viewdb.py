"""Readable views and database discovery (ADR-0031). Fictional data only."""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from bench.series_gen import WEIGHT_SCHEMA
from mnemento import Ledger
from mnemento.demo import BY, seed
from mnemento.series import ingest
from mnemento.viewdb import DbEntry, build_view, find_databases, is_mnemento_db, main, select


def make_demo_db(path: Path) -> Path:
    led = Ledger.open(path)
    seed(led)
    # a field the schema does not know yet (store first, organize later)
    led.record_event("app_o01", "updated", {"interview_mode": "online"}, "2026-10-04T10:00:00+09:00", BY, "memo")
    led.schemas.register(WEIGHT_SCHEMA)
    ingest(led, "weight", [{"person": "가상인물", "measured_on": "2026-10-01", "kg": 70.5}], by=BY, source="test")
    led.close()
    return path


def rows(db: Path, sql: str, *params):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def demo_db(tmp_path):
    return make_demo_db(tmp_path / "demo.db")


def test_view_flattens_fields_with_labels_and_names(demo_db, tmp_path):
    out = build_view(demo_db, tmp_path / "views" / "demo-view.db")
    app = rows(out, "SELECT * FROM application WHERE id = 'app_o02'")[0]
    assert app["display_name"] == "(주)가상테크"          # no name of its own: the company's
    assert app["company"] == "(주)가상테크"               # company_id shown as the company's name
    assert "company_id" not in app
    assert (app["platform"], app["status"]) == ("사람인", "열람")  # first label of each code
    company = rows(out, "SELECT * FROM company WHERE id = 'co_gasangtech'")[0]
    assert company["name"] == "(주)가상테크" and company["aliases"] == "Gasang Tech"


def test_view_follows_corrections_and_hides_retracted(demo_db, tmp_path):
    out = build_view(demo_db, tmp_path / "v.db")
    ids = {r["id"] for r in rows(out, "SELECT id FROM application")}
    assert "app_o15" not in ids                                      # retracted duplicate
    assert rows(out, "SELECT applied_at FROM application WHERE id = 'app_o14'")[0]["applied_at"] == "2026-10-02"
    summary = {r["type"]: r for r in rows(out, "SELECT * FROM _summary")}
    assert summary["application"]["retracted"] == 1
    assert summary["application"]["records"] == len(ids)
    changes = [r["change"] for r in rows(out, "SELECT change FROM _history WHERE id IN ('app_o02', 'app_o14')")]
    assert "-> 열람" in changes
    assert any(c.startswith("corrected: ") and "applied_at=2026-10-02" in c for c in changes)


def test_view_lists_draft_fields_and_series(demo_db, tmp_path):
    out = build_view(demo_db, tmp_path / "v.db")
    assert rows(out, "SELECT interview_mode FROM application WHERE id = 'app_o01'")[0]["interview_mode"] == "online"
    fields = {(r["type"], r["field"]): r for r in rows(out, "SELECT * FROM _fields")}
    assert fields[("application", "interview_mode")]["source"] == "draft"
    assert fields[("application", "status")]["labels"].startswith("applied=지원완료")
    assert fields[("application", "company_id")]["ref"] == "company"
    weight = rows(out, "SELECT * FROM weight")
    assert weight == [{"person": "가상인물", "measured_on": "2026-10-01", "kg": 70.5, "body_fat_pct": None}]
    meta = {r["key"]: r["value"] for r in rows(out, "SELECT * FROM _meta")}
    assert meta["source"] == str(demo_db.resolve())


def test_source_database_is_not_written(demo_db, tmp_path):
    before = digest(demo_db)
    build_view(demo_db, tmp_path / "v.db")
    build_view(demo_db, tmp_path / "v.db")  # rebuilding replaces the old view
    assert digest(demo_db) == before
    assert not Path(str(demo_db) + "-wal").exists() or Path(str(demo_db) + "-wal").stat().st_size == 0
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("v.db")] == ["v.db"]


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_find_databases_from_mcp_configs_local_folder_and_scan(tmp_path, monkeypatch):
    home, cwd, scan = tmp_path / "home", tmp_path / "proj", tmp_path / "scan"
    monkeypatch.delenv("MNEMENTO_DB", raising=False)
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    shared = make_demo_db(home / ".mnemento" / "shared.db")
    other = make_demo_db(tmp_path / "elsewhere" / "other.db")
    write_json(home / ".claude.json", {
        "mcpServers": {"mnemento": {"env": {"MNEMENTO_DB": str(shared)}}},
        "projects": {"/work/alpha": {"mcpServers": {"mnemento": {"env": {"MNEMENTO_DB": str(shared)}}}},
                     "/work/beta": {"mcpServers": {"keeper": {"env": {"MNEMENTO_DB": str(other)}}}}},
    })
    write_json(cwd / ".mcp.json", {"mcpServers": {"x": {"command": "y"}}})  # no MNEMENTO_DB: ignored
    (home / ".mnemento" / "views").mkdir()
    scanned = make_demo_db(scan / "run1" / "bench.db")
    plain = scan / "run1" / "not-mnemento.db"
    sqlite3.connect(plain).execute("CREATE TABLE t (a)").connection.close()

    found = find_databases(home=home, cwd=cwd, scan=[scan])
    by_name = {e.name: e for e in found}
    assert set(by_name) == {"shared", "other", "bench"}
    assert by_name["shared"].sources == ["mnemento @ user", "mnemento @ alpha", "~/.mnemento"]
    assert by_name["other"].sources == ["keeper @ beta"]
    assert (by_name["bench"].kind, by_name["bench"].path) == ("scanned", scanned.resolve())
    assert [e.kind for e in found] == ["configured", "configured", "scanned"]
    assert is_mnemento_db(scanned) and not is_mnemento_db(plain)


def test_select_by_number_name_part_and_path(tmp_path):
    entries = [DbEntry(tmp_path / "mnemento.db", "configured", ["mnemento @ user"]),
               DbEntry(tmp_path / "notes-agent.db", "configured", ["mnemento @ notes-agent"])]
    assert select(entries, "2") == [entries[1]]
    assert select(entries, "9") == []
    assert select(entries, "mnemento") == [entries[0]]         # exact name beats partial matches
    assert select(entries, "notes") == [entries[1]]
    assert select(entries, "agent") == [entries[1]]
    assert len(select(entries, "mnemento @")) == 2               # ambiguous: caller asks for a number


@pytest.fixture
def empty_home(tmp_path, monkeypatch):
    """No MCP configs and no ~/.mnemento: nothing is discovered."""
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.delenv("MNEMENTO_DB", raising=False)
    monkeypatch.chdir(home)
    return home


def test_cli_with_nothing_discovered(empty_home, capsys):
    assert main(["list"]) == 0
    assert "No Mnemento databases found." in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No database matches 'missing'"):
        main(["view", "missing", "--no-open"])


def test_cli_view_builds_next_to_the_database_and_reports_stale(demo_db, tmp_path, empty_home, capsys):
    assert main(["view", str(demo_db), "--no-open"]) == 0  # a path works with nothing discovered
    view = tmp_path / "views" / "demo-view.db"
    assert view.is_file() and f"Built view: {view}" in capsys.readouterr().out
    entry = DbEntry(demo_db.resolve(), "path")
    assert entry.view_status() != "-" and "(stale)" not in entry.view_status()
    later = time.time() + 60
    os.utime(demo_db, (later, later))
    assert entry.view_status().endswith("(stale)")
