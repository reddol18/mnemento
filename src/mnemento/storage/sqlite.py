"""SQLite implementation of the storage interface (JSON1 + WAL, ADR-0001)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from ..schema.definition import NAME_RE
from .base import EntityState, Event, SchemaRecord, Storage

MIN_SQLITE = (3, 31, 0)  # generated columns

_DDL = """
CREATE TABLE IF NOT EXISTS schemas (
    name        TEXT    NOT NULL,
    version     INTEGER NOT NULL,
    definition  TEXT    NOT NULL CHECK (json_valid(definition)),
    json_schema TEXT    NOT NULL CHECK (json_valid(json_schema)),
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT    NOT NULL UNIQUE,
    entity_id       TEXT    NOT NULL,
    entity_type     TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    payload         TEXT    NOT NULL CHECK (json_valid(payload)),
    at              TEXT    NOT NULL,
    at_utc          TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL,
    by              TEXT    NOT NULL,
    evidence        TEXT,
    schema_version  INTEGER NOT NULL,
    target_event_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_entity ON events (entity_id, seq);
CREATE INDEX IF NOT EXISTS ix_events_target ON events (target_event_id);
CREATE INDEX IF NOT EXISTS ix_events_type_at ON events (entity_type, at_utc);

-- append-only (ADR-0003). Physical purge (ADR-0007) will be a separate, explicit command.
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE IF NOT EXISTS entities (
    id             TEXT    PRIMARY KEY,
    type           TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    doc            TEXT    NOT NULL CHECK (json_valid(doc)),
    retracted      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT,
    updated_at     TEXT,
    last_event_seq INTEGER,
    event_ids      TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_entities_type ON entities (type, retracted);

-- fields that write requests tried to use but the schema does not define (PLAN 5-1 B)
CREATE TABLE IF NOT EXISTS schema_observations (
    entity_type TEXT NOT NULL,
    field       TEXT NOT NULL,
    sample      TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    by          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_type_field ON schema_observations (entity_type, field);

-- interpreted question patterns -> QuerySpec templates (derived data; safe to clear)
CREATE TABLE IF NOT EXISTS query_cache (
    key        TEXT PRIMARY KEY,
    plan       TEXT NOT NULL CHECK (json_valid(plan)),
    created_at TEXT NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0
);
"""


def column_for(field_name: str) -> str:
    """Generated column name for an indexed document field (shared across entity types)."""
    if not NAME_RE.match(field_name):
        raise ValueError(f"invalid field name: {field_name!r}")
    return f"f_{field_name}"


class SQLiteStorage(Storage):
    def __init__(self, path: str | Path = ":memory:"):
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            raise RuntimeError(f"SQLite >= {'.'.join(map(str, MIN_SQLITE))} required")
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we manage transactions explicitly (BEGIN IMMEDIATE).
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_DDL)

    # ---- transactions -------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    # ---- schemas ------------------------------------------------------------------------

    def insert_schema(self, record: SchemaRecord) -> None:
        self._conn.execute(
            "INSERT INTO schemas (name, version, definition, json_schema, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (record.name, record.version, _dumps(record.definition),
             _dumps(record.json_schema), record.created_at),
        )

    def get_schema(self, name: str, version: int | None = None) -> SchemaRecord | None:
        if version is None:
            row = self._conn.execute(
                "SELECT * FROM schemas WHERE name = ? ORDER BY version DESC LIMIT 1", (name,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM schemas WHERE name = ? AND version = ?", (name, version)
            ).fetchone()
        return _schema_from_row(row) if row else None

    def list_schema_versions(self, name: str) -> list[SchemaRecord]:
        rows = self._conn.execute(
            "SELECT * FROM schemas WHERE name = ? ORDER BY version", (name,)
        ).fetchall()
        return [_schema_from_row(r) for r in rows]

    def list_schema_names(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT name FROM schemas ORDER BY name")]

    def ensure_indexed_field(self, field_name: str) -> None:
        col = column_for(field_name)
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_xinfo(entities)")}
        if col not in existing:
            # Only VIRTUAL generated columns can be added with ALTER TABLE.
            self._conn.execute(
                f"ALTER TABLE entities ADD COLUMN {col} "
                f"GENERATED ALWAYS AS (json_extract(doc, '$.{field_name}')) VIRTUAL"
            )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS ix_entities_{field_name} ON entities (type, {col})"
        )

    # ---- events -------------------------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        cur = self._conn.execute(
            "INSERT INTO events (id, entity_id, entity_type, kind, payload, at, at_utc, "
            "recorded_at, by, evidence, schema_version, target_event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.id, event.entity_id, event.entity_type, event.kind, _dumps(event.payload),
             event.at, event.at_utc, event.recorded_at, event.by, event.evidence,
             event.schema_version, event.target_event_id),
        )
        return replace(event, seq=cur.lastrowid)

    def get_event(self, event_id: str) -> Event | None:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _event_from_row(row) if row else None

    def events_for_entity(self, entity_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE entity_id = ? ORDER BY seq", (entity_id,)
        ).fetchall()
        return [_event_from_row(r) for r in rows]

    def entity_ids_with_events(self) -> Iterator[str]:
        rows = self._conn.execute(
            "SELECT entity_id FROM events GROUP BY entity_id ORDER BY MIN(seq)"
        ).fetchall()
        return iter([r[0] for r in rows])

    # ---- entities -----------------------------------------------------------------------

    def put_entity(self, state: EntityState) -> None:
        self._conn.execute(
            "INSERT INTO entities (id, type, schema_version, doc, retracted, created_at, "
            "updated_at, last_event_seq, event_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, "
            "schema_version=excluded.schema_version, doc=excluded.doc, "
            "retracted=excluded.retracted, created_at=excluded.created_at, "
            "updated_at=excluded.updated_at, last_event_seq=excluded.last_event_seq, "
            "event_ids=excluded.event_ids",
            (state.id, state.type, state.schema_version, _dumps(state.doc), int(state.retracted),
             state.created_at, state.updated_at, state.last_event_seq, _dumps(state.event_ids)),
        )

    def get_entity(self, entity_id: str) -> EntityState | None:
        row = self._conn.execute(
            "SELECT id, type, schema_version, doc, retracted, created_at, updated_at, "
            "last_event_seq, event_ids FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return _entity_from_row(row) if row else None

    def find_entities(
        self,
        entity_type: str,
        filters: dict[str, Any],
        *,
        indexed_fields: set[str],
        include_retracted: bool = False,
    ) -> list[EntityState]:
        where = ["type = ?"]
        params: list[Any] = [entity_type]
        if not include_retracted:
            where.append("retracted = 0")
        for fname, value in filters.items():
            expr = column_for(fname) if fname in indexed_fields else (
                f"json_extract(doc, '$.{_checked(fname)}')"
            )
            if value is None:
                where.append(f"{expr} IS NULL")
            else:
                where.append(f"{expr} = ?")
                params.append(_sql_value(value))
        sql = (
            "SELECT id, type, schema_version, doc, retracted, created_at, updated_at, "
            "last_event_seq, event_ids FROM entities WHERE " + " AND ".join(where) + " ORDER BY id"
        )
        return [_entity_from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def explain_find(self, entity_type: str, filters: dict[str, Any], indexed_fields: set[str]) -> str:
        """EXPLAIN QUERY PLAN text for a find (diagnostics/tests)."""
        where = ["type = ?"]
        params: list[Any] = [entity_type]
        for fname, value in filters.items():
            expr = column_for(fname) if fname in indexed_fields else (
                f"json_extract(doc, '$.{_checked(fname)}')"
            )
            where.append(f"{expr} = ?")
            params.append(_sql_value(value))
        rows = self._conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM entities WHERE " + " AND ".join(where), params
        ).fetchall()
        return "\n".join(r["detail"] for r in rows)

    def fetch_all(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not sql.lstrip().upper().startswith(("SELECT", "WITH", "EXPLAIN")):
            raise ValueError("fetch_all is read-only")
        return [dict(r) for r in self._conn.execute(sql, list(params)).fetchall()]

    def note_unknown_fields(self, entity_type: str, fields: dict[str, Any], seen_at: str, by: str) -> None:
        self._conn.executemany(
            "INSERT INTO schema_observations (entity_type, field, sample, seen_at, by) VALUES (?, ?, ?, ?, ?)",
            [(entity_type, f, _dumps(v), seen_at, by) for f, v in fields.items()],
        )

    def unknown_field_stats(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE entity_type = ?", [entity_type]) if entity_type else ("", [])
        rows = self._conn.execute(
            "SELECT entity_type, field, COUNT(*) AS count, json_group_array(json(sample)) AS samples, "
            "MIN(seen_at) AS first_seen, MAX(seen_at) AS last_seen, "
            "json_group_array(DISTINCT by) AS agents "
            f"FROM schema_observations {where} GROUP BY entity_type, field ORDER BY count DESC, field",
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["samples"] = json.loads(d["samples"])[:5]
            d["agents"] = json.loads(d["agents"])
            out.append(d)
        return out

    def get_cached_plan(self, key: str) -> str | None:
        row = self._conn.execute("SELECT plan FROM query_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        with self.transaction():
            self._conn.execute("UPDATE query_cache SET hits = hits + 1 WHERE key = ?", (key,))
        return row[0]

    def put_cached_plan(self, key: str, plan: str, created_at: str) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO query_cache (key, plan, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET plan = excluded.plan, created_at = excluded.created_at",
                (key, plan, created_at))

    def delete_all_entities(self) -> None:
        self._conn.execute("DELETE FROM entities")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Raw read access (diagnostics/tests)."""
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()


# ---- helpers ------------------------------------------------------------------------------

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _checked(field_name: str) -> str:
    if not NAME_RE.match(field_name):
        raise ValueError(f"invalid field name: {field_name!r}")
    return field_name


def _sql_value(value: Any) -> Any:
    # json_extract returns JSON true/false as 1/0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (str, int, float)):
        return value
    raise TypeError(f"unsupported filter value: {value!r}")


def _schema_from_row(row: sqlite3.Row) -> SchemaRecord:
    return SchemaRecord(
        name=row["name"], version=row["version"], definition=json.loads(row["definition"]),
        json_schema=json.loads(row["json_schema"]), created_at=row["created_at"],
    )


def _event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"], entity_id=row["entity_id"], entity_type=row["entity_type"],
        kind=row["kind"], payload=json.loads(row["payload"]), at=row["at"], at_utc=row["at_utc"],
        recorded_at=row["recorded_at"], by=row["by"], evidence=row["evidence"],
        schema_version=row["schema_version"], target_event_id=row["target_event_id"],
        seq=row["seq"],
    )


def _entity_from_row(row: sqlite3.Row) -> EntityState:
    return EntityState(
        id=row["id"], type=row["type"], schema_version=row["schema_version"],
        doc=json.loads(row["doc"]), retracted=bool(row["retracted"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        last_event_seq=row["last_event_seq"], event_ids=json.loads(row["event_ids"]),
    )
