"""The record API (Python level; the MCP server wraps this in a later task).

`Ledger.record_event` is the only write path for entity data: it validates, appends one event and
recomputes the entity's current state from its full event stream, all in one transaction. If any
check fails nothing is stored.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import events as ev
from .errors import (
    EntityExistsError,
    EntityNotFoundError,
    EntityRetractedError,
    InvalidEventError,
    UnknownFieldError,
)
from .schema.registry import SchemaRegistry
from .storage.base import EntityState, Event, Storage
from .storage.sqlite import SQLiteStorage
from .timeutil import DEFAULT_TZ, format_instant, now, parse_instant, utc_sort_key

ENTITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class Ledger:
    def __init__(self, storage: Storage, tz: str = DEFAULT_TZ):
        self.storage = storage
        self.tz = tz
        self.schemas = SchemaRegistry(storage, tz)
        if storage.needs_rebuild:  # derived columns were added: recompute them from the events
            self.rebuild_all()
            storage.needs_rebuild = False

    @classmethod
    def open(cls, path: str | Path = ":memory:", tz: str = DEFAULT_TZ) -> "Ledger":
        return cls(SQLiteStorage(path), tz)

    def close(self) -> None:
        self.storage.close()

    # ---- writes -------------------------------------------------------------------------

    def record_event(
        self,
        entity_id: str,
        kind: str,
        payload: dict[str, Any],
        at: str | datetime,
        by: str,
        evidence: str | None = None,
        *,
        entity_type: str | None = None,
        at_precision: str = "time",
    ) -> Event:
        """Append one event and update the entity's current state.

        `entity_type` is required for `created` (it selects the schema) and, if given for other
        kinds, must match the existing entity. `at` is when it happened and must carry a UTC
        offset (ADR-0004); `recorded_at` is set to now.
        """
        ev.check_payload(kind, payload)
        if not isinstance(entity_id, str) or not ENTITY_ID_RE.match(entity_id):
            raise InvalidEventError(f"invalid entity id: {entity_id!r}")
        if not isinstance(by, str) or not by.strip():
            raise InvalidEventError("`by` (the recording agent) is required")
        if kind in ev.META_KINDS and not (evidence and evidence.strip()):
            raise InvalidEventError(f"{kind} requires evidence (why)")
        at_dt = parse_instant(at)
        if at_precision not in ("time", "date", "unknown"):
            raise InvalidEventError("at_precision must be time, date or unknown (ADR-0013)")

        with self.storage.transaction():
            existing = self.storage.get_entity(entity_id)
            history = self.storage.events_for_entity(entity_id)
            if kind == ev.CREATED:
                if existing is not None or history:
                    raise EntityExistsError(f"entity already exists: {entity_id}")
                if not entity_type:
                    raise InvalidEventError("entity_type is required for created")
                etype = entity_type
            else:
                if existing is None:
                    raise EntityNotFoundError(f"no such entity: {entity_id}")
                if entity_type and entity_type != existing.type:
                    raise InvalidEventError(
                        f"{entity_id} is a {existing.type}, not {entity_type}"
                    )
                if existing.retracted:
                    raise EntityRetractedError(f"entity is retracted: {entity_id}")
                etype = existing.type

            schema = self.schemas.get(etype)  # latest version
            target = self._check_target(kind, payload, entity_id, history)

            event = Event(
                id=f"evt_{uuid.uuid4().hex}",
                entity_id=entity_id,
                entity_type=etype,
                kind=kind,
                payload=payload,
                at=format_instant(at_dt),
                at_utc=utc_sort_key(at_dt),
                recorded_at=format_instant(now(self.tz)),
                by=by,
                evidence=evidence,
                schema_version=schema.version,
                target_event_id=target,
                at_precision=at_precision,
            )
            if kind == ev.CORRECTED:
                # the replacement payload must itself be a well-formed payload for the target kind
                target_kind = next(e.kind for e in history if e.id == target)
                ev.check_payload(target_kind, payload["payload"])

            state = ev.replay(entity_id, etype, [*history, event])
            schema.validate(state.doc)  # raises before anything is written

            stored = self.storage.append_event(event)
            state.last_event_seq = stored.seq
            self.storage.put_entity(state)
        return stored

    def _check_target(
        self, kind: str, payload: dict[str, Any], entity_id: str, history: list[Event]
    ) -> str | None:
        if kind not in ev.META_KINDS or "target" not in payload:
            return None
        target_id = payload["target"]
        by_id = {e.id: e for e in history}
        target = by_id.get(target_id)
        if target is None:
            raise InvalidEventError(f"target {target_id} is not an event of {entity_id}")
        voided = {e.target_event_id for e in history if e.kind == ev.RETRACTED and e.target_event_id}
        if target_id in voided:
            raise InvalidEventError(f"target {target_id} is already retracted")
        if kind == ev.CORRECTED and target.kind not in ev.DATA_KINDS:
            raise InvalidEventError("only created/updated/status_changed events can be corrected")
        if kind == ev.RETRACTED:
            if target.kind == ev.CREATED:
                raise InvalidEventError(
                    "retract the whole entity (retracted without target) instead of its created event"
                )
            if target.kind == ev.RETRACTED:
                raise InvalidEventError("a retraction cannot be retracted")
        return target_id

    # ---- reads --------------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> EntityState | None:
        return self.storage.get_entity(entity_id)

    def history(self, entity_id: str) -> list[Event]:
        """Every event of the entity in recording order, including corrections/retractions."""
        return self.storage.events_for_entity(entity_id)

    def find(
        self, entity_type: str, filters: dict[str, Any] | None = None, *, include_retracted: bool = False
    ) -> list[EntityState]:
        """Entities of a type matching equality filters. Only schema fields may be used."""
        filters = filters or {}
        schema = self.schemas.get(entity_type)
        unknown = [f for f in filters if f not in schema.fields]
        if unknown:
            raise UnknownFieldError(f"{entity_type} has no field(s) {unknown}")
        return self.storage.find_entities(
            entity_type, filters, indexed_fields=set(schema.indexed_fields),
            include_retracted=include_retracted,
        )

    def count(self, entity_type: str, filters: dict[str, Any] | None = None) -> int:
        return len(self.find(entity_type, filters))

    # ---- rebuild (ADR-0003: entities are derivable from events) -------------------------

    def rebuild_entity(self, entity_id: str) -> EntityState:
        with self.storage.transaction():
            history = self.storage.events_for_entity(entity_id)
            if not history:
                raise EntityNotFoundError(f"no events for entity: {entity_id}")
            state = ev.replay(entity_id, history[0].entity_type, history)
            self.storage.put_entity(state)
        return state

    def rebuild_all(self) -> int:
        """Recreate the whole entities table from events. Returns the number of entities."""
        with self.storage.transaction():
            self.storage.delete_all_entities()
            n = 0
            for entity_id in self.storage.entity_ids_with_events():
                self.rebuild_entity(entity_id)
                n += 1
        return n
