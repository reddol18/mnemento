"""QuerySpec — the structured form of a question (PLAN 5-1 A ①).

The LLM (or the rule-based fast path, or a calling agent directly) produces a QuerySpec. Code then
validates it against the schema dictionary and compiles it to SQL; the model never writes SQL.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ...schema.definition import SchemaDef
from ...timeutil import is_calendar_date

Scalar = Union[str, int, float, bool]

Op = Literal[
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "exists", "missing", "contains", "name_is", "reached",
]
Bucket = Literal["none", "day", "week", "month", "year"]
Agg = Literal["count", "count_if", "sum", "avg", "min", "max", "avg_days_between",
              "avg_hours_between_events", "first", "last", "change", "change_pct"]
SERIES_VALUE_AGGS = ("sum", "avg", "min", "max", "first", "last", "change", "change_pct")
SHORT_NAME = re.compile(r"^[^\W\d]\w{0,40}$")

RELATIVE_DATE_RE = re.compile(
    r"^@(today|this_week_start|this_month_start|last_month_start|this_year_start)([+-]\d{1,4}[dwm])?$"
)
# calendar tokens of a named month: weeks are Monday-Sunday; the "last full week" is the last such week
# that lies entirely inside the month
MONTH_TOKEN_RE = re.compile(
    r"^@(month_start|month_end|last_full_week_start|last_full_week_end)\((\d{4})-(\d{2})\)$"
)


def is_date_token(value: object) -> bool:
    return isinstance(value, str) and bool(RELATIVE_DATE_RE.match(value) or MONTH_TOKEN_RE.match(value))


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(description="A field of the target schema.")
    op: Op = Field(description="Comparison. name_is: the field references another entity "
                               "(see 'ref'); value is that entity's name, resolved by the Keeper. "
                               "reached (status only): the record EVER had this status (history), "
                               "even if its current status is later; eq/in on status = current state.")
    value: Scalar | list[Scalar] | None = Field(
        default=None,
        description="Comparison value. Lists for in/not_in. Dates as YYYY-MM-DD or a token: @today, "
                    "@today-1d, @this_week_start, @this_month_start, @last_month_start, or for a named "
                    "month @month_start(2026-11), @month_end(2026-11), @last_full_week_start(2026-11), "
                    "@last_full_week_end(2026-11).",
    )


class GroupKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    bucket: Bucket = Field(default="none", description="Date bucketing for date fields.")


class EventRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["created", "updated", "status_changed"]
    to: str | None = Field(default=None, description="For status_changed: the new status, e.g. 'viewed'.")


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Result column name, e.g. 'applications', 'viewed'.")
    agg: Agg = Field(description="count: rows; count_if: rows matching `where`; "
                                 "avg_days_between: average days from `field` to `field_end`.")
    field: str | None = None
    field_end: str | None = None
    event_from: EventRef | None = Field(default=None, description="avg_hours_between_events: start event.")
    event_to: EventRef | None = Field(default=None, description="avg_hours_between_events: end event.")
    where: list[Filter] = Field(default_factory=list)


class TimePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str | None = Field(default=None, description="A date field of the record.")
    event: EventRef | None = Field(default=None, description="When this event of the record happened "
                                                             "(exact times only).")
    today: bool = Field(default=False, description="Now / today.")


class Elapsed(BaseModel):
    """Condition on the time between two points of the same record (ADR-0018)."""
    model_config = ConfigDict(extra="forbid")
    start: TimePoint
    end: TimePoint = Field(default_factory=lambda: TimePoint(today=True),
                           description="Defaults to today.")
    unit: Literal["days", "hours"] = Field(description="days: between dates (date fields, or today); "
                                                       "hours: between event times (events, or now).")
    op: Literal["gt", "gte", "lt", "lte", "eq"]
    value: float = Field(description="Amount of `unit`, e.g. 3 for 'more than three days' with op gt.")


class Window(BaseModel):
    """series: a value computed for every point over the points before it (same key), e.g. a moving average."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Column name, e.g. 'avg_52w'.")
    measure: str
    agg: Literal["avg", "min", "max", "sum"] = "avg"
    size: int = Field(ge=1, le=5000, description="How far back, including the point itself.")
    unit: Literal["points", "days"] = Field(description="points: the last N points; days: the last N days.")


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: str | None = Field(default=None, description="Name of a window column.")
    value: float | None = None


class Compare(BaseModel):
    """series: keep the points where `measure` op baseline (a window column or a fixed value)."""
    model_config = ConfigDict(extra="forbid")
    measure: str
    op: Literal["gt", "gte", "lt", "lte"]
    baseline: Baseline


class Having(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: str = Field(description="'count' (rows in the group) or a measure name.")
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: float


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["entities", "events", "series"] = Field(
        default="entities",
        description="entities: current state of records (default). events: the change log itself "
                    "(who recorded what when, corrections, retractions); fields: kind, by, entity_type, "
                    "entity_id, at, evidence, to, from. series: measurements over time of a series type "
                    "(kind series in the dictionary): one row per key and time point.")
    entity_type: str = Field(description="Which record type (schema name) to query. With source=events "
                                          "it limits the log to that record type.")
    mode: Literal["count", "list", "aggregate"] = Field(
        description="count: how many (+ evidence ids); list: which records; "
                    "aggregate: grouped measures/comparisons."
    )
    filters: list[Filter] = Field(default_factory=list)
    elapsed: list[Elapsed] = Field(default_factory=list, description="Conditions on the time between two "
                                   "points of each record, e.g. viewed more than 3 days after applying.")
    window: list[Window] = Field(default_factory=list, description="series only: computed columns over earlier "
                                 "points, e.g. a 7-point moving average or the 364-day average.")
    compare: list[Compare] = Field(default_factory=list, description="series only: keep points where a "
                                   "measure is above/below a window column or a value.")
    group_by: list[GroupKey] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    list_fields: list[str] = Field(default_factory=list, description="Fields to show in list mode.")
    having: list[Having] = Field(default_factory=list, description="Conditions on group results, e.g. "
                                 "groups with count >= 2. Requires group_by.")
    order_by: str | None = Field(default=None, description="Field or measure name to sort by.")
    order_by_event: EventRef | None = Field(
        default=None, description="list mode: sort records by when this event happened (e.g. the "
                                  "rejection), latest first with descending=true.")
    descending: bool = False
    limit: int | None = Field(default=100, ge=1, le=1000)
    interpretation: str = Field(
        default="",
        description="One sentence restating the question in schema terms, incl. resolved periods.",
    )
    defaults_used: list[str] = Field(
        default_factory=list, description="Vague words read with a default, as 'word = reading' "
                                          "(e.g. '빠르게 = viewed within 1 day of applying'); shown to the user.")


# ---- validation against the schema dictionary ----------------------------------------------

_NUMERIC = {"integer", "number"}


EVENT_SCHEMA = SchemaDef.from_dict({
    "name": "event",
    "description": "The append-only change log.",
    "fields": {
        "kind": {"type": "string",
                 "description": "Event kind. created: first recorded; updated: an ordinary edit or addition; "
                                "status_changed: the status moved; corrected: an earlier event was corrected "
                                "because it was wrong (target = that event); retracted: a record or event was "
                                "invalidated (e.g. a duplicate).",
                 "enum": ["created", "updated", "status_changed", "corrected", "retracted", "migrated"],
                 "indexed": True,
                 "labels": {"created": ["최초 기록", "등록"], "updated": ["수정", "보완", "추가 기록"],
                            "status_changed": ["상태 변경"], "corrected": ["정정", "바로잡음", "잘못 기록"],
                            "retracted": ["무효", "철회", "취소 처리", "중복"],
                            "migrated": ["정리", "이행", "필드 이동"]}},
        "by": {"type": "string", "description": "Agent that recorded the event.", "indexed": True},
        "entity_type": {"type": "string", "description": "Record type.", "indexed": True},
        "entity_id": {"type": "string", "description": "Record id.", "indexed": True},
        "at": {"type": "string", "format": "date-time", "description": "When it happened.", "indexed": True},
        "evidence": {"type": "string", "description": "Why the recorder believed it."},
        "to": {"type": "string", "description": "status_changed: new status."},
        "from": {"type": "string", "description": "status_changed: previous status."},
    },
})


def validate_spec(spec: QuerySpec, schemas: dict[str, SchemaDef]) -> list[str]:
    """Return problems; empty means the spec only uses fields/values the dictionary defines."""
    if spec.entity_type not in schemas:
        return [f"unknown entity_type {spec.entity_type!r}; known: {sorted(schemas)}"]
    schema = EVENT_SCHEMA if spec.source == "events" else schemas[spec.entity_type]
    errs: list[str] = []
    if spec.source != "events" and (spec.source == "series") != (schema.kind == "series"):
        return [f"{spec.entity_type} is kind {schema.kind}: use source "
                f"{'series' if schema.kind == 'series' else 'entities'}"]
    if spec.source == "series":
        return _check_series(spec, schema)
    if spec.window or spec.compare:
        errs.append("window and compare work with source series only")
    for i, m in enumerate(spec.measures):
        # first/last of a date field is its earliest/latest date (compiled as MIN/MAX); other uses are series-only
        if m.agg in ("change", "change_pct") or (
                m.agg in ("first", "last") and (schema.fields.get(m.field or "") is None
                                                or schema.fields[m.field].format not in ("date", "date-time"))):
            errs.append(f"measures[{i}]: {m.agg} works with source series only (records: min/max of a field)")
    if spec.source == "events" and any(m.agg not in ("count", "count_if") for m in spec.measures):
        errs.append("source=events supports count/count_if measures only")
    for i, f in enumerate(spec.filters):
        errs += _check_filter(schema, f, f"filters[{i}]")
    for i, e in enumerate(spec.elapsed):
        errs += _check_elapsed(spec, schema, e, f"elapsed[{i}]")
    for i, g in enumerate(spec.group_by):
        fd = schema.fields.get(g.field)
        if fd is None:
            errs.append(f"group_by[{i}]: unknown field {g.field!r}")
        elif g.bucket != "none" and fd.format not in ("date", "date-time"):
            errs.append(f"group_by[{i}]: bucket {g.bucket} needs a date field, {g.field} is not")
        elif fd.type == "array":
            errs.append(f"group_by[{i}]: cannot group by array field {g.field}")
    names = set()
    for i, m in enumerate(spec.measures):
        where = f"measures[{i}]"
        if m.name in names:
            errs.append(f"{where}: duplicate measure name {m.name!r}")
        names.add(m.name)
        # names label result columns only (SQL uses positional aliases m0, m1 ...), so any language
        # works: Korean names from the model are fine
        if not re.match(r"^[^\W\d]\w{0,40}$", m.name):
            errs.append(f"{where}: measure name must be a short word (letters, digits, _), got {m.name!r}")
        if m.agg == "count_if" and not m.where:
            errs.append(f"{where}: count_if needs `where`")
        for j, wf in enumerate(m.where):
            errs += _check_filter(schema, wf, f"{where}.where[{j}]")
        if m.agg in ("sum", "avg", "min", "max"):
            fd = schema.fields.get(m.field or "")
            if fd is None:
                errs.append(f"{where}: unknown field {m.field!r}")
            elif m.agg in ("sum", "avg") and fd.type not in _NUMERIC:
                errs.append(f"{where}: {m.agg} needs a numeric field")
        if m.agg == "avg_hours_between_events":
            if spec.source != "entities" or not (m.event_from and m.event_to):
                errs.append(f"{where}: avg_hours_between_events needs event_from and event_to on entities")
            elif "status" in schema.fields:
                allowed = schema.fields["status"].enum or ()
                for ref in (m.event_from, m.event_to):
                    if ref.to is not None and ref.to not in allowed:
                        errs.append(f"{where}: status {ref.to!r} not allowed; allowed: {list(allowed)}")
        if m.agg == "avg_days_between":
            for fname in (m.field, m.field_end):
                fd = schema.fields.get(fname or "")
                if fd is None or fd.format not in ("date", "date-time"):
                    errs.append(f"{where}: avg_days_between needs two date fields, got {fname!r}")
    for fname in spec.list_fields:
        if fname not in schema.fields:
            errs.append(f"list_fields: unknown field {fname!r}")
    if spec.order_by and spec.order_by not in schema.fields and spec.order_by not in names \
            and spec.order_by != "count":
        errs.append(f"order_by: unknown field or measure {spec.order_by!r}")
    if spec.mode != "aggregate" and spec.group_by:
        errs.append("group_by requires mode 'aggregate'")
    for i, h in enumerate(spec.having):
        if not spec.group_by:
            errs.append("having requires group_by")
            break
        if h.measure != "count" and h.measure not in names:
            errs.append(f"having[{i}]: unknown measure {h.measure!r} (use 'count' or a measure name)")
    if spec.order_by_event is not None:
        if spec.mode != "list" or spec.source != "entities":
            errs.append("order_by_event works in list mode on entities")
        elif spec.order_by_event.to is not None and "status" in schema.fields:
            if spec.order_by_event.to not in (schema.fields["status"].enum or ()):
                errs.append(f"order_by_event: status {spec.order_by_event.to!r} not allowed")
    return errs


def _check_series(spec: QuerySpec, schema: SchemaDef) -> list[str]:
    errs: list[str] = []
    measures = set(schema.measures)
    for i, f in enumerate(spec.filters):
        errs += _check_filter(schema, f, f"filters[{i}]")
        if f.op in ("name_is", "reached"):
            errs.append(f"filters[{i}]: {f.op} does not apply to series")
    if spec.elapsed or spec.order_by_event:
        errs.append("series: elapsed and order_by_event are for entities")
    windows = {}
    for i, w in enumerate(spec.window):
        if w.measure not in measures:
            errs.append(f"window[{i}]: {w.measure!r} is not a measure ({sorted(measures)})")
        if w.name in windows or w.name in schema.fields or not SHORT_NAME.match(w.name):
            errs.append(f"window[{i}]: name {w.name!r} must be a new short word")
        windows[w.name] = w
    for i, c in enumerate(spec.compare):
        if c.measure not in measures:
            errs.append(f"compare[{i}]: {c.measure!r} is not a measure")
        if (c.baseline.window is None) == (c.baseline.value is None):
            errs.append(f"compare[{i}]: give exactly one of baseline.window, baseline.value")
        elif c.baseline.window is not None and c.baseline.window not in windows:
            errs.append(f"compare[{i}]: unknown window {c.baseline.window!r}")
    for i, g in enumerate(spec.group_by):
        if g.field not in schema.fields:
            errs.append(f"group_by[{i}]: unknown field {g.field!r}")
        elif g.bucket != "none" and g.field != schema.time_field:
            errs.append(f"group_by[{i}]: buckets apply to the time field {schema.time_field}")
        elif g.bucket == "none" and g.field not in schema.series_key:
            errs.append(f"group_by[{i}]: group by a key field ({list(schema.series_key)}) or the time field "
                        f"with a bucket")
    names = set()
    for i, m in enumerate(spec.measures):
        if m.name in names or not SHORT_NAME.match(m.name):
            errs.append(f"measures[{i}]: bad or duplicate name {m.name!r}")
        names.add(m.name)
        if m.agg in SERIES_VALUE_AGGS and m.field not in measures:
            errs.append(f"measures[{i}]: {m.agg} needs a measure field ({sorted(measures)}), got {m.field!r}")
        elif m.agg not in SERIES_VALUE_AGGS + ("count", "count_if"):
            errs.append(f"measures[{i}]: {m.agg} does not apply to series")
        if m.agg == "count_if" and not m.where:
            errs.append(f"measures[{i}]: count_if needs `where`")
        for j, wf in enumerate(m.where):
            errs += _check_filter(schema, wf, f"measures[{i}].where[{j}]")
    for fname in spec.list_fields:
        if fname not in schema.fields and fname not in windows:
            errs.append(f"list_fields: unknown field {fname!r}")
    if spec.order_by and spec.order_by not in schema.fields and spec.order_by not in names \
            and spec.order_by not in windows and spec.order_by != "count":
        errs.append(f"order_by: unknown field or measure {spec.order_by!r}")
    if spec.mode != "aggregate" and spec.group_by:
        errs.append("group_by requires mode 'aggregate'")
    for i, h in enumerate(spec.having):
        if not spec.group_by:
            errs.append("having requires group_by")
            break
        if h.measure != "count" and h.measure not in names:
            errs.append(f"having[{i}]: unknown measure {h.measure!r}")
    return errs


def _check_elapsed(spec: QuerySpec, schema: SchemaDef, e: Elapsed, where: str) -> list[str]:
    if spec.source != "entities":
        return [f"{where}: elapsed works on entities"]
    errs: list[str] = []
    kinds = set()
    for side, p in (("start", e.start), ("end", e.end)):
        given = [x for x in (p.field, p.event, p.today or None) if x is not None]
        if len(given) != 1:
            errs.append(f"{where}.{side}: give exactly one of field, event, today")
            continue
        if p.field is not None:
            fd = schema.fields.get(p.field)
            if fd is None or fd.format not in ("date", "date-time"):
                errs.append(f"{where}.{side}: {p.field!r} is not a date field")
            kinds.add("field")
        elif p.event is not None:
            if p.event.to is not None and "status" in schema.fields and                     p.event.to not in (schema.fields["status"].enum or ()):
                errs.append(f"{where}.{side}: status {p.event.to!r} not allowed")
            kinds.add("event")
    if kinds == {"field", "event"}:
        errs.append(f"{where}: do not mix a date field with an event time (dates are local days, events "
                    f"are exact times); use two date fields (unit days) or two events (unit hours)")
    if e.unit == "hours" and "field" in kinds:
        errs.append(f"{where}: date fields hold only the day; use unit days, or events for hours")
    if e.value < 0:
        errs.append(f"{where}: value must not be negative")
    return errs


def _check_filter(schema: SchemaDef, f: Filter, where: str) -> list[str]:
    fd = schema.fields.get(f.field)
    if fd is None:
        return [f"{where}: unknown field {f.field!r} (fields: {sorted(schema.fields)})"]
    errs: list[str] = []
    v = f.value
    if f.op in ("exists", "missing"):
        return []
    if v is None:
        return [f"{where}: op {f.op} needs a value"]
    if f.op in ("in", "not_in"):
        if not isinstance(v, list) or not v:
            return [f"{where}: {f.op} needs a non-empty list"]
        values = v
    elif isinstance(v, list):
        return [f"{where}: op {f.op} takes a single value"]
    else:
        values = [v]
    if f.op == "reached":
        if f.field != "status" or fd.enum is None:
            return [f"{where}: reached only works on the status field"]
        bad = [x for x in values if x not in fd.enum]
        return [f"{where}: {bad} not allowed for status; allowed: {list(fd.enum)}"] if bad else []
    if f.op == "name_is":
        if not fd.ref:
            errs.append(f"{where}: name_is only works on reference fields")
        return errs
    if f.op == "contains" and fd.type not in ("string", "array"):
        errs.append(f"{where}: contains needs a text or array field")
    if fd.enum is not None and f.op != "contains":
        bad = [x for x in values if x not in fd.enum]
        if bad:
            errs.append(f"{where}: {bad} not allowed for {f.field}; allowed: {list(fd.enum)}")
    if fd.format in ("date", "date-time"):
        for x in values:
            if not (isinstance(x, str) and (is_calendar_date(x) or is_date_token(x))):
                errs.append(f"{where}: {x!r} is not YYYY-MM-DD or a relative date token")
    if f.op in ("gt", "gte", "lt", "lte") and fd.type not in _NUMERIC and fd.format is None:
        errs.append(f"{where}: {f.op} needs a numeric or date field")
    return errs


def spec_json_schema() -> dict[str, Any]:
    return QuerySpec.model_json_schema()
