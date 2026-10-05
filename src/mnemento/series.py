"""Series records (ADR-0016): numeric measurements per (series key..., time point).

No event per point. Points arrive in ingest batches; re-ingesting a point updates it and the batch keeps the previous
values, so a whole batch can be reverted. A batch is all or nothing: one invalid row and nothing is written.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import MnementoError
from .ledger import Ledger
from .schema.definition import SchemaDef
from .timeutil import format_instant, is_calendar_date, is_instant, now, parse_instant, utc_sort_key


class SeriesError(MnementoError):
    pass


@dataclass
class BatchReport:
    batch_id: str | None
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def series_schema(ledger: Ledger, type_: str) -> SchemaDef:
    schema = ledger.schemas.get(type_)
    if schema.kind != "series":
        raise SeriesError(f"{type_} is not a series schema (kind {schema.kind})")
    return schema


def point_key(schema: SchemaDef, row: dict[str, Any]) -> str:
    return json.dumps([row.get(k) for k in schema.series_key], ensure_ascii=False, separators=(",", ":"))


def point_time(schema: SchemaDef, value: Any) -> str:
    """Stored time: the calendar date for date series, a fixed-width UTC string for date-time series."""
    if schema.fields[schema.time_field].format == "date":
        if not (isinstance(value, str) and is_calendar_date(value)):
            raise SeriesError(f"{schema.time_field}: {value!r} is not YYYY-MM-DD")
        return value
    if not (isinstance(value, str) and is_instant(value)):
        raise SeriesError(f"{schema.time_field}: {value!r} is not a date-time with offset")
    return utc_sort_key(parse_instant(value))


def ingest(ledger: Ledger, type_: str, rows: list[dict[str, Any]], *, by: str, source: str,
           at: str | None = None) -> BatchReport:
    """Write one batch of points. Invalid rows -> nothing written, `rejected` lists why."""
    schema = series_schema(ledger, type_)
    if not by or not source:
        raise SeriesError("`by` and `source` are required")
    rejected, prepared, seen = [], [], set()
    for i, row in enumerate(rows):
        errs = schema.validation_errors(row)
        missing = [k for k in (*schema.series_key, schema.time_field) if row.get(k) is None]
        if missing:
            errs.append(f"missing {missing}")
        if not any(row.get(m) is not None for m in schema.measures):
            errs.append("no measure value")
        if not errs:
            try:
                t = point_time(schema, row[schema.time_field])
            except SeriesError as exc:
                errs.append(str(exc))
        if not errs:
            k = point_key(schema, row)
            if (k, t) in seen:
                errs.append("the same key and time point twice in one batch")
            seen.add((k, t))
        if errs:
            rejected.append({"row": i, "errors": errs})
        else:
            prepared.append((k, t, {f: v for f, v in row.items() if v is not None}))
    if rejected:
        return BatchReport(None, rejected=rejected)
    batch_id = "batch_" + uuid.uuid4().hex[:12]
    rep = BatchReport(batch_id)
    changes = []
    st = ledger.storage
    with st.transaction():
        for k, t, doc in prepared:
            cur = st.get_series_point(type_, k, t)
            if cur is None:
                rep.inserted += 1
                changes.append({"key": k, "t": t, "before": None, "before_batch": None})
            elif cur["doc"] == doc:
                rep.unchanged += 1
                continue
            else:
                rep.updated += 1
                changes.append({"key": k, "t": t, "before": cur["doc"], "before_batch": cur["batch_id"]})
            st.put_series_point(type_, k, t, doc, batch_id)
        st.insert_batch({"id": batch_id, "type": type_, "at": at or format_instant(now(ledger.tz)), "by": by,
                         "source": source, "inserted": rep.inserted, "updated": rep.updated,
                         "unchanged": rep.unchanged, "changes": changes})
    return rep


def revert_batch(ledger: Ledger, batch_id: str, *, at: str | None = None) -> dict[str, Any]:
    """Undo a whole batch: inserted points are removed, updated points get their previous values back. Refused when
    a later batch changed one of its points (revert that one first) — nothing is changed then."""
    st = ledger.storage
    batch = st.get_batch(batch_id)
    if batch is None:
        raise SeriesError(f"no such batch: {batch_id}")
    if batch["reverted_at"]:
        raise SeriesError(f"{batch_id} was already reverted at {batch['reverted_at']}")
    later = sorted({cur["batch_id"] for c in batch["changes"]
                    if (cur := st.get_series_point(batch["type"], c["key"], c["t"])) and cur["batch_id"] != batch_id})
    if later:
        raise SeriesError(f"points of {batch_id} were changed later by {later}; revert those first")
    with st.transaction():
        for c in reversed(batch["changes"]):
            if c["before"] is None:
                st.delete_series_point(batch["type"], c["key"], c["t"])
            else:
                st.put_series_point(batch["type"], c["key"], c["t"], c["before"], c["before_batch"])
        st.mark_batch_reverted(batch_id, at or format_instant(now(ledger.tz)))
    return {"batch_id": batch_id, "reverted_points": len(batch["changes"])}
