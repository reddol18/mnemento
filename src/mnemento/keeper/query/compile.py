"""QuerySpec -> parameterised SQL for the SQLite backend (PLAN 5-1 A ②).

Every identifier in the SQL comes from the schema dictionary (validated names), every value is a
bound parameter. Indexed fields use their generated columns (f_<field>).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from ...schema.definition import SchemaDef
from ...storage.sqlite import column_for
from .reltime import resolve_relative
from ...timeutil import utc_sort_key
from .spec import EVENT_SCHEMA, Elapsed, EventRef, Filter, QuerySpec, TimePoint, is_date_token


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any]
    mode: str
    group_columns: list[str] = field(default_factory=list)
    measure_names: list[str] = field(default_factory=list)
    resolved_dates: dict[str, str] = field(default_factory=dict)  # token -> YYYY-MM-DD
    value_names: list[str] = field(default_factory=list)  # list mode: computed columns v0, v1 ... (ADR-0022)


class Compiler:
    def __init__(self, schema: SchemaDef, now: datetime, schemas: dict[str, SchemaDef] | None = None):
        self.schema = schema
        self.now = now
        self.schemas = schemas or {}
        self.params: list[Any] = []
        self.resolved: dict[str, str] = {}
        self.values: dict[str, Any] = {}  # name -> Value (ADR-0022)

    # ---- expressions ----------------------------------------------------------------------

    def col(self, fname: str) -> str:
        if fname in self.values:
            return self.value_sql(fname)
        fd = self.schema.fields[fname]  # validated beforehand
        if fd.indexed:
            return column_for(fname)
        return f"json_extract(doc, '$.{fname}')"

    def qcol(self, fname: str) -> str:
        """A field of the outer record, safe inside a subquery that has its own `doc`."""
        fd = self.schema.fields[fname]
        return f"entities.{column_for(fname)}" if fd.indexed else f"json_extract(entities.doc, '$.{fname}')"

    def value_sql(self, name: str) -> str:
        v = self.values[name]
        if v.asof is not None:
            return self.asof_sql(v.asof, "value")
        sym = {"add": "+", "sub": "-", "mul": "*"}
        parts = [self.col(a) if isinstance(a, str) else self._bind_raw(a) for a in v.expr.args]
        if v.expr.op == "div":
            out = parts[0]
            for p in parts[1:]:
                out = f"({out} * 1.0 / NULLIF({p}, 0))"
            return out
        return "(" + f" {sym[v.expr.op]} ".join(parts) + ")"

    def asof_sql(self, a, what: str) -> str:
        """The latest point of the series key this record points at, at or before `at` (ADR-0016 §5): its
        measure value, or (what="t") its date for the gap warning."""
        ser = self.schemas[a.series]
        type_ = self._bind_raw(ser.name)  # bind in the order the placeholders appear in the SQL text
        at = self.asof_at_sql(a)
        sel = f"json_extract(sp.doc, '$.{a.measure}')" if what == "value" else "sp.t"
        return (f"(SELECT {sel} FROM series_points sp WHERE sp.type = {type_} "
                f"AND sp.key = json_array({self.qcol(a.field)}) AND sp.t <= {at} "
                f"AND json_extract(sp.doc, '$.{a.measure}') IS NOT NULL ORDER BY sp.t DESC LIMIT 1)")

    def asof_at_sql(self, a) -> str:
        return f"substr({self.qcol(a.at)}, 1, 10)" if a.at in self.schema.fields else self._bind_raw(
            self.value(a.at, a.at))

    def bucket(self, fname: str, bucket: str) -> str:
        c = self.col(fname)
        return {
            "none": c,
            "day": f"substr({c}, 1, 10)",
            "month": f"substr({c}, 1, 7)",
            "year": f"substr({c}, 1, 4)",
            "week": f"strftime('%Y-W%W', substr({c}, 1, 10))",
        }[bucket]

    def value(self, fname: str, v: Any) -> Any:
        if is_date_token(v):
            self.resolved[v] = resolve_relative(v, self.now)
            v = self.resolved[v]
        if isinstance(v, bool):
            return int(v)  # json_extract yields 1/0 for JSON booleans
        return v

    def bind(self, fname: str, v: Any) -> str:
        self.params.append(self.value(fname, v))
        return "?"

    def cond(self, f: Filter) -> str:
        fd = self.schema.fields[f.field]
        c = self.col(f.field)
        op, v = f.op, f.value
        if op == "exists":
            return f"{c} IS NOT NULL"
        if op == "missing":
            return f"{c} IS NULL"
        if op in ("in", "not_in"):
            marks = ", ".join(self.bind(f.field, x) for x in v)  # type: ignore[union-attr]
            return f"{c} IN ({marks})" if op == "in" else f"({c} IS NULL OR {c} NOT IN ({marks}))"
        if op == "contains":
            if fd.type == "array":
                return (f"EXISTS (SELECT 1 FROM json_each(doc, '$.{f.field}') "
                        f"WHERE json_each.value = {self.bind(f.field, v)})")
            self.params.append("%" + str(v).replace("\\", "\\\\").replace("%", "\\%")
                               .replace("_", "\\_") + "%")
            return f"{c} LIKE ? ESCAPE '\\'"
        if op == "reached":
            # ever had the status (or a later one that implies it), from the replayed history (ADR-0012)
            wanted = set(v if isinstance(v, list) else [v])
            for later, earlier in (fd.implies or {}).items():
                if wanted & set(earlier):
                    wanted.add(later)
            marks = ", ".join(self.bind(f.field, x) for x in sorted(wanted))
            return f"EXISTS (SELECT 1 FROM json_each(entities.reached) WHERE json_each.value IN ({marks}))"
        if op == "name_is":
            raise ValueError("name_is filters must be resolved to ids before compiling")
        sql_op = {"eq": "=", "ne": "IS NOT", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"{c} {sql_op} {self.bind(f.field, v)}"

    # ---- statement -------------------------------------------------------------------------

    table = "entities"
    list_columns = "id, doc"

    def base_where(self, spec: QuerySpec) -> list[str]:
        self.params.append(spec.entity_type)
        return ["type = ?", "retracted = 0"]

    def compile(self, spec: QuerySpec) -> CompiledQuery:
        self.values = {v.name: v for v in spec.values}
        value_cols, value_params = [], []
        if spec.mode == "list" and spec.values:  # SELECT comes first in the SQL text: its parameters too
            for i, v in enumerate(spec.values):
                value_cols.append(f"{self.value_sql(v.name)} AS v{i}")
            value_params, self.params = self.params, []
        where = self.base_where(spec)
        where += [self.cond(f) for f in spec.filters]
        where += [self.elapsed(e) for e in spec.elapsed]
        where_sql = " AND ".join(where)
        where_params = list(self.params)
        self.params = []

        if spec.mode == "list":
            if spec.order_by_event is not None:
                direction = "DESC" if spec.descending else "ASC"
                when = self._event_time(spec.order_by_event, "MAX" if spec.descending else "MIN")
                order = f"{when} {direction} NULLS LAST, id"  # records without an exact time go last
            else:
                order = self._order(spec, default=self.default_order)
            order_params, self.params = self.params, []
            cols = ", ".join([self.list_columns, *value_cols])
            sql = (f"SELECT {cols}, COUNT(*) OVER () AS _total FROM {self.table} "
                   f"WHERE {where_sql} ORDER BY {order} LIMIT ?")
            return CompiledQuery(sql, [*value_params, *where_params, *order_params, spec.limit or 100], "list",
                                 resolved_dates=self.resolved, value_names=[v.name for v in spec.values])

        selects, groups, gnames = [], [], []
        for i, g in enumerate(spec.group_by):
            expr = self.bucket(g.field, g.bucket)
            name = g.field if g.bucket == "none" else f"{g.field}_{g.bucket}"
            selects.append(f"{expr} AS g{i}")
            groups.append(f"g{i}")
            gnames.append(name)
        measures = spec.measures or []
        mnames = []
        for i, m in enumerate(measures):
            selects.append(f"{self._measure(m)} AS m{i}")
            mnames.append(m.name)
        selects.append("COUNT(*) AS _n")
        selects.append("json_group_array(id) AS _ids")
        if self.table == "events":  # the records the counted events belong to
            selects.append("json_group_array(DISTINCT entity_id) AS _eids")
        measure_params = self.params
        sql = f"SELECT {', '.join(selects)} FROM {self.table} WHERE {where_sql}"
        params = [*measure_params, *where_params]
        if groups:
            sql += f" GROUP BY {', '.join(groups)}"
            if spec.having:
                conds = []
                for h in spec.having:
                    col = "_n" if h.measure == "count" else f"m{mnames.index(h.measure)}"
                    sql_op = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[h.op]
                    conds.append(f"{col} {sql_op} ?")
                    params.append(h.value)
                sql += " HAVING " + " AND ".join(conds)
            sql += f" ORDER BY {self._order(spec, default=', '.join(groups), groups=gnames, measures=mnames)}"
        return CompiledQuery(sql, params, spec.mode if groups or measures else "count",
                             gnames, mnames, self.resolved)

    def _measure(self, m) -> str:
        # `where` narrows any measure, not only count_if (avg of top10 vs avg of top30 in one row)
        cond = " AND ".join(self.cond(f) for f in m.where) if m.where else None
        if m.agg in ("count", "count_if"):
            return f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)" if cond else "COUNT(*)"
        if m.agg == "avg_days_between":
            a, b = self.col(m.field), self.col(m.field_end)
            expr = f"julianday({b}) - julianday({a})"
            fn = "AVG"
        elif m.agg == "avg_hours_between_events":
            end = self._event_time(m.event_to)  # bind order follows the SQL text: end, then start
            start = self._event_time(m.event_from)
            expr = f"(julianday({end}) - julianday({start})) * 24"
            fn = "AVG"
        else:  # first/last reach here only for date fields (validated): the earliest/latest date
            expr, fn = self.col(m.field), {"first": "MIN", "last": "MAX"}.get(m.agg, m.agg.upper())
        return f"{fn}(CASE WHEN {cond} THEN {expr} END)" if cond else f"{fn}({expr})"

    def elapsed(self, e: Elapsed) -> str:
        """Time between two points of the record; a missing point makes the condition false (NULL)."""
        end = self._point(e.end, e.unit)  # bind order follows the SQL text: end, start, value
        start = self._point(e.start, e.unit)
        scale = " * 24" if e.unit == "hours" else ""
        sql_op = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[e.op]
        return f"((julianday({end}) - julianday({start})){scale}) {sql_op} {self._bind_raw(e.value)}"

    def _point(self, p: TimePoint, unit: str) -> str:
        if p.field is not None:
            return self.col(p.field)
        if p.event is not None:
            return self._event_time(p.event)
        # today: the local day for day counts, the exact UTC instant for hours
        return self._bind_raw(self.now.date().isoformat() if unit == "days" else utc_sort_key(self.now))

    def _event_time(self, ref: EventRef, agg: str = "MIN") -> str:
        """Earliest (MIN) or latest (MAX) UTC time of a matching, non-voided event of the entity."""
        conds = ["ev.entity_id = entities.id", f"ev.kind = {self._bind_raw(ref.kind)}",
                 "ev.id IN (SELECT value FROM json_each(entities.event_ids))",
                 "ev.at_precision = 'time'"]  # only exact event times (ADR-0013)
        if ref.to is not None:
            conds.append(f"json_extract(ev.payload, '$.to') = {self._bind_raw(ref.to)}")
        return f"(SELECT {agg}(ev.at_utc) FROM events ev WHERE {' AND '.join(conds)})"

    def _bind_raw(self, v: Any) -> str:
        self.params.append(v)
        return "?"

    default_order = "id"

    def _order(self, spec: QuerySpec, default: str, groups: list[str] | None = None,
               measures: list[str] | None = None) -> str:
        if not spec.order_by:
            return default
        direction = "DESC" if spec.descending else "ASC"
        if measures and spec.order_by in measures:
            return f"m{measures.index(spec.order_by)} {direction}"
        if spec.order_by == "count":
            return f"_n {direction}" if spec.mode != "list" else default
        if groups and spec.order_by in groups:
            return f"g{groups.index(spec.order_by)} {direction}"
        if spec.order_by in self.values and spec.mode == "list":  # the selected column, not a second subquery
            return f"v{list(self.values).index(spec.order_by)} {direction} NULLS LAST, id"
        return f"{self.col(spec.order_by)} {direction}, id"


class EventsCompiler(Compiler):
    """source=events: the change log. Event dates are interpreted in the user's time zone."""

    table = "events"
    list_columns = ("id, seq, entity_id, entity_type, kind, payload, at, recorded_at, by, evidence, "
                    "target_event_id")
    default_order = "seq"
    _COLS = {"kind": "kind", "by": "by", "entity_type": "entity_type", "entity_id": "entity_id",
             "at": "at", "evidence": "evidence", "to": "json_extract(payload, '$.to')",
             "from": "json_extract(payload, '$.from')"}

    def __init__(self, now: datetime, record_schema: SchemaDef | None = None):
        super().__init__(EVENT_SCHEMA, now)
        self.record_schema = record_schema

    def base_where(self, spec: QuerySpec) -> list[str]:
        self.params.append(spec.entity_type)
        where = ["entity_type = ?"]
        if spec.record_filters:  # ADR-0026: the events of the records whose current state matches
            sub = Compiler(self.record_schema, self.now)
            sub.params = self.params  # one parameter list, in the order of the SQL text
            sub.resolved = self.resolved
            self.params.append(spec.entity_type)
            conds = " AND ".join(sub.cond(f) for f in spec.record_filters)
            where.append(f"entity_id IN (SELECT id FROM entities WHERE type = ? AND retracted = 0 AND {conds})")
        return where

    def col(self, fname: str) -> str:
        return self._COLS[fname]

    def _day_start(self, v: Any) -> date:
        return date.fromisoformat(self.value("at", v))

    def _utc(self, d: date) -> str:
        return utc_sort_key(datetime.combine(d, time.min, tzinfo=self.now.tzinfo))

    def cond(self, f: Filter) -> str:
        if f.field != "at" or f.op in ("exists", "missing"):
            return super().cond(f)
        one = timedelta(days=1)
        if f.op in ("eq", "in"):
            parts = []
            for v in (f.value if isinstance(f.value, list) else [f.value]):
                d = self._day_start(v)
                parts.append(f"(at_utc >= {self._bind_raw(self._utc(d))} AND at_utc < {self._bind_raw(self._utc(d + one))})")
            return "(" + " OR ".join(parts) + ")"
        d = self._day_start(f.value)
        bound, op = {"gte": (d, ">="), "gt": (d + one, ">="), "lt": (d, "<"), "lte": (d + one, "<")}.get(
            f.op, (None, None))
        if bound is None:
            raise ValueError(f"op {f.op} is not supported on event time")
        return f"at_utc {op} {self._bind_raw(self._utc(bound))}"


def compile_spec(spec: QuerySpec, schema: SchemaDef, now: datetime,
                 schemas: dict[str, SchemaDef] | None = None) -> CompiledQuery:
    if spec.source == "events":
        return EventsCompiler(now, (schemas or {}).get(spec.entity_type)).compile(spec)
    return Compiler(schema, now, schemas).compile(spec)
