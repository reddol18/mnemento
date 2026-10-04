"""Event kinds and the pure replay function: events -> current entity state (ADR-0003, 0007, 0009).

Replay rules (see ADR-0009):
1. `retracted` with a `target` voids that event; without a target it retracts the whole entity.
2. `corrected` replaces the payload of its `target` event; the latest non-voided correction wins.
3. Data events (`created`, `updated`, `status_changed`) that are not voided are applied with
   `created` first, then by occurrence time (`at`, UTC) and recording order (`seq`).
"""

from __future__ import annotations

from typing import Any, Iterable

from .errors import ConflictError, InvalidEventError
from .storage.base import EntityState, Event

CREATED = "created"
UPDATED = "updated"
STATUS_CHANGED = "status_changed"
CORRECTED = "corrected"
RETRACTED = "retracted"
MIGRATED = "migrated"  # schema organize: values moved between fields (ADR-0014, like updated)

DATA_KINDS = frozenset({CREATED, UPDATED, STATUS_CHANGED, MIGRATED})
META_KINDS = frozenset({CORRECTED, RETRACTED})
KINDS = DATA_KINDS | META_KINDS

STATUS_FIELD = "status"


def check_payload(kind: str, payload: Any) -> None:
    """Shape checks that do not need the current state."""
    if kind not in KINDS:
        raise InvalidEventError(f"unknown event kind {kind!r}; expected one of {sorted(KINDS)}")
    if not isinstance(payload, dict):
        raise InvalidEventError("payload must be an object")
    if kind == CREATED:
        return
    if kind in (UPDATED, MIGRATED):
        if not payload:
            raise InvalidEventError("updated payload must set at least one field")
    elif kind == STATUS_CHANGED:
        if set(payload) - {"from", "to"} or "to" not in payload:
            raise InvalidEventError("status_changed payload must be {to, from?}")
    elif kind == CORRECTED:
        if set(payload) != {"target", "payload"} or not isinstance(payload["payload"], dict):
            raise InvalidEventError("corrected payload must be {target, payload}")
        if not isinstance(payload["target"], str):
            raise InvalidEventError("corrected target must be an event id")
    elif kind == RETRACTED:
        if set(payload) - {"target"}:
            raise InvalidEventError("retracted payload must be {} or {target}")
        if "target" in payload and not isinstance(payload["target"], str):
            raise InvalidEventError("retracted target must be an event id")


def apply_data_event(doc: dict[str, Any] | None, kind: str, payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    if kind == CREATED:
        if doc is not None:
            raise ConflictError(f"{event_id}: entity already created")
        return dict(payload)
    if doc is None:
        raise ConflictError(f"{event_id}: {kind} before created")
    new = dict(doc)
    if kind in (UPDATED, MIGRATED):
        check_payload(kind, payload)
        for k, v in payload.items():
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        return new
    if kind == STATUS_CHANGED:
        check_payload(STATUS_CHANGED, payload)
        if "from" in payload and doc.get(STATUS_FIELD) != payload["from"]:
            raise ConflictError(
                f"{event_id}: status is {doc.get(STATUS_FIELD)!r}, not {payload['from']!r}"
            )
        new[STATUS_FIELD] = payload["to"]
        return new
    raise InvalidEventError(f"{kind} is not a data event")


def _seq(e: Event) -> float:
    # an event being validated before it is appended has no seq yet; it is the newest
    return e.seq if e.seq is not None else float("inf")


def replay(entity_id: str, entity_type: str, events: Iterable[Event]) -> EntityState:
    events = sorted(events, key=_seq)
    if not events:
        raise InvalidEventError(f"{entity_id}: no events to replay")

    voided: set[str] = set()
    entity_retracted = False
    for e in events:
        if e.kind == RETRACTED:
            if e.target_event_id:
                voided.add(e.target_event_id)
            else:
                entity_retracted = True

    corrections: dict[str, dict[str, Any]] = {}
    for e in events:  # seq order: later corrections overwrite earlier ones
        if e.kind == CORRECTED and e.id not in voided:
            corrections[e.target_event_id] = e.payload["payload"]  # type: ignore[index]

    data = [e for e in events if e.kind in DATA_KINDS and e.id not in voided]
    # An event whose time is unknown (ADR-0013) has only a placeholder `at` (when it was recorded). Order it
    # right after the latest event of known time recorded before it, not by the placeholder — otherwise an
    # undated "viewed" recorded today would land after a dated "rejected on 9/18".
    sort_at: dict[str, str] = {}
    known = ""
    for e in events:  # recording order
        if getattr(e, "at_precision", "time") == "unknown":
            sort_at[e.id] = known
        else:
            sort_at[e.id] = e.at_utc
            known = max(known, e.at_utc)
    data.sort(key=lambda e: (e.kind != CREATED, sort_at[e.id], _seq(e)))

    doc: dict[str, Any] | None = None
    applied: list[str] = []
    reached: list[str] = []
    created_at = updated_at = None
    latest_utc = ""
    for e in data:
        payload = corrections.get(e.id, e.payload)
        doc = apply_data_event(doc, e.kind, payload, e.id)
        applied.append(e.id)
        status = doc.get(STATUS_FIELD)
        if isinstance(status, str) and status not in reached:
            reached.append(status)
        if e.kind == CREATED:
            created_at = e.at
        if e.at_utc >= latest_utc:
            latest_utc, updated_at = e.at_utc, e.at

    if doc is None:
        raise ConflictError(f"{entity_id}: no valid created event")
    last = events[-1]
    return EntityState(
        id=entity_id,
        type=entity_type,
        schema_version=last.schema_version,
        doc=doc,
        retracted=entity_retracted,
        created_at=created_at,
        updated_at=updated_at,
        last_event_seq=last.seq,
        event_ids=applied,
        reached=reached,
    )
