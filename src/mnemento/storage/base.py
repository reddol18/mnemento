"""Storage interface. The SQLite implementation is the default; a Postgres (JSONB) adapter can
implement the same interface later (ADR-0001)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class SchemaRecord:
    name: str
    version: int
    definition: dict[str, Any]  # the SchemaDef as dict (fields + descriptions + enums + flags)
    json_schema: dict[str, Any]  # compiled JSON Schema used for validation
    created_at: str


@dataclass(frozen=True)
class Event:
    id: str
    entity_id: str
    entity_type: str
    kind: str
    payload: dict[str, Any]
    at: str  # when it happened (offset ISO 8601)
    at_utc: str  # sortable UTC key derived from `at`
    recorded_at: str  # when it was written (offset ISO 8601)
    by: str
    evidence: str | None
    schema_version: int
    target_event_id: str | None = None  # for corrected/retracted
    seq: int | None = None  # assigned by storage on append
    at_precision: str = "time"  # time | date (day known, at = 00:00) | unknown (at = recording time), ADR-0013


@dataclass
class EntityState:
    id: str
    type: str
    schema_version: int
    doc: dict[str, Any]
    retracted: bool = False
    created_at: str | None = None  # `at` of the created event
    updated_at: str | None = None  # latest `at` among applied events
    last_event_seq: int | None = None
    event_ids: list[str] = field(default_factory=list)  # applied (non-voided) data events
    reached: list[str] = field(default_factory=list)  # every status the record has had (ADR-0012)

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, **self.doc}


class Storage(ABC):
    """Persistence for the three tables: schemas, events (append-only), entities."""

    # set when a derived column was added to an existing database: entities must be rebuilt from events
    needs_rebuild: bool = False

    # ---- transactions -------------------------------------------------------------------
    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Exclusive write transaction. Nested use joins the outer transaction."""

    # ---- schemas ------------------------------------------------------------------------
    @abstractmethod
    def insert_schema(self, record: SchemaRecord) -> None: ...

    @abstractmethod
    def get_schema(self, name: str, version: int | None = None) -> SchemaRecord | None:
        """Latest version when `version` is None."""

    @abstractmethod
    def list_schema_versions(self, name: str) -> list[SchemaRecord]: ...

    @abstractmethod
    def list_schema_names(self) -> list[str]: ...

    @abstractmethod
    def ensure_indexed_field(self, field_name: str) -> None:
        """Expose `field_name` as a generated column on entities with an index."""

    # ---- events -------------------------------------------------------------------------
    @abstractmethod
    def append_event(self, event: Event) -> Event:
        """Append and return the event with `seq` set. Events are never updated or deleted."""

    @abstractmethod
    def get_event(self, event_id: str) -> Event | None: ...

    @abstractmethod
    def events_for_entity(self, entity_id: str) -> list[Event]:
        """All events of one entity in recording (seq) order."""

    @abstractmethod
    def entity_ids_with_events(self) -> Iterator[str]: ...

    # ---- entities -----------------------------------------------------------------------
    @abstractmethod
    def put_entity(self, state: EntityState) -> None: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> EntityState | None: ...

    @abstractmethod
    def find_entities(
        self,
        entity_type: str,
        filters: dict[str, Any],
        *,
        indexed_fields: set[str],
        include_retracted: bool = False,
    ) -> list[EntityState]:
        """Equality filters on document fields. `indexed_fields` lets the backend use its
        generated columns; other fields are read from the JSON document."""

    @abstractmethod
    def fetch_all(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Run a read-only query produced by the backend's query compiler."""

    # ---- schema observations (fields seen in write requests but not in the schema) --------
    @abstractmethod
    def note_unknown_fields(self, entity_type: str, fields: dict[str, Any], seen_at: str, by: str) -> None: ...

    @abstractmethod
    def unknown_field_stats(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        """[{entity_type, field, count, samples: [...], first_seen, last_seen, agents: [...]}]"""

    # ---- schema organize approvals (ADR-0014) --------------------------------------------
    @abstractmethod
    def record_schema_change(self, change: dict[str, Any]) -> None: ...

    @abstractmethod
    def schema_changes(self, entity_type: str | None = None) -> list[dict[str, Any]]: ...

    # ---- query plan cache (PLAN 2-1 principle 4) -----------------------------------------
    @abstractmethod
    def get_cached_plan(self, key: str) -> str | None: ...

    @abstractmethod
    def put_cached_plan(self, key: str, plan: str, created_at: str) -> None: ...

    # ---- query log (ADR-0015); reads go through fetch_all ----------------------------------
    @abstractmethod
    def insert_query_log(self, row: dict[str, Any]) -> int: ...

    @abstractmethod
    def mark_query_log_diverging(self, question_norm: str) -> None: ...

    @abstractmethod
    def delete_query_log(self, ids: list[int]) -> int: ...

    # ---- series (ADR-0016); reads for queries go through fetch_all ------------------------
    @abstractmethod
    def get_series_point(self, type_: str, key: str, t: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def put_series_point(self, type_: str, key: str, t: str, doc: dict[str, Any], batch_id: str) -> None: ...

    @abstractmethod
    def delete_series_point(self, type_: str, key: str, t: str) -> None: ...

    @abstractmethod
    def insert_batch(self, batch: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_batch(self, batch_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_batches(self, type_: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def mark_batch_reverted(self, batch_id: str, at: str) -> None: ...

    @abstractmethod
    def put_coverage(self, entity_id: str, field: str, sources: int, present: int, detail: dict[str, Any],
                     checked_at: str) -> None: ...

    @abstractmethod
    def delete_coverage(self, entity_id: str, field: str) -> None: ...

    @abstractmethod
    def delete_all_entities(self) -> None:
        """Drop the derived current-state table contents (used by full rebuilds/tests)."""

    @abstractmethod
    def close(self) -> None: ...
