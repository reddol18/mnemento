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
    "exists", "missing", "contains", "name_is",
]
Bucket = Literal["none", "day", "week", "month", "year"]
Agg = Literal["count", "count_if", "sum", "avg", "min", "max", "avg_days_between",
              "avg_hours_between_events"]

RELATIVE_DATE_RE = re.compile(
    r"^@(today|this_week_start|this_month_start|last_month_start|this_year_start)([+-]\d{1,4}[dwm])?$"
)


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(description="A field of the target schema.")
    op: Op = Field(description="Comparison. name_is: the field references another entity "
                               "(see 'ref'); value is that entity's name, resolved by the Keeper.")
    value: Scalar | list[Scalar] | None = Field(
        default=None,
        description="Comparison value. Lists for in/not_in. Dates as YYYY-MM-DD or a relative "
                    "token like @today, @today-1d, @this_month_start, @last_month_start.",
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


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["entities", "events"] = Field(
        default="entities",
        description="entities: current state of records (default). events: the change log itself "
                    "(who recorded what when, corrections, retractions); fields: kind, by, entity_type, "
                    "entity_id, at, evidence, to, from.")
    entity_type: str = Field(description="Which record type (schema name) to query. With source=events "
                                          "it limits the log to that record type.")
    mode: Literal["count", "list", "aggregate"] = Field(
        description="count: how many (+ evidence ids); list: which records; "
                    "aggregate: grouped measures/comparisons."
    )
    filters: list[Filter] = Field(default_factory=list)
    group_by: list[GroupKey] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    list_fields: list[str] = Field(default_factory=list, description="Fields to show in list mode.")
    order_by: str | None = Field(default=None, description="Field or measure name to sort by.")
    descending: bool = False
    limit: int | None = Field(default=100, ge=1, le=1000)
    interpretation: str = Field(
        default="",
        description="One sentence restating the question in schema terms, incl. resolved periods.",
    )


# ---- validation against the schema dictionary ----------------------------------------------

_NUMERIC = {"integer", "number"}


EVENT_SCHEMA = SchemaDef.from_dict({
    "name": "event",
    "description": "The append-only change log.",
    "fields": {
        "kind": {"type": "string", "description": "Event kind.",
                 "enum": ["created", "updated", "status_changed", "corrected", "retracted"], "indexed": True},
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
    if spec.source == "events" and any(m.agg not in ("count", "count_if") for m in spec.measures):
        errs.append("source=events supports count/count_if measures only")
    for i, f in enumerate(spec.filters):
        errs += _check_filter(schema, f, f"filters[{i}]")
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
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$", m.name):
            errs.append(f"{where}: measure name must be an identifier")
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
            if not (isinstance(x, str) and (is_calendar_date(x) or RELATIVE_DATE_RE.match(x))):
                errs.append(f"{where}: {x!r} is not YYYY-MM-DD or a relative date token")
    if f.op in ("gt", "gte", "lt", "lte") and fd.type not in _NUMERIC and fd.format is None:
        errs.append(f"{where}: {f.op} needs a numeric or date field")
    return errs


def spec_json_schema() -> dict[str, Any]:
    return QuerySpec.model_json_schema()
