"""QuerySpec (source=series) -> SQL over series_points (ADR-0016), plus the series answer warnings.

Windows (moving averages, N-day averages) are computed over the whole history of each key BEFORE the period filter,
so "below the 52-week average" in March uses the year before March. Values come from bound parameters only; every
identifier is a schema field (validated before compiling).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ...schema.definition import SchemaDef
from .reltime import resolve_relative
from .spec import Filter, QuerySpec, is_date_token

_OPS = {"eq": "=", "ne": "IS NOT", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


@dataclass
class SeriesCompiled:
    sql: str
    params: list[Any]
    mode: str
    group_columns: list[str] = field(default_factory=list)
    measure_names: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)  # list mode: the columns shown
    resolved_dates: dict[str, str] = field(default_factory=dict)
    period: tuple[str | None, str | None] = (None, None)  # time filter bounds, for the gap warning


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SeriesCompiler:
    def __init__(self, schema: SchemaDef, now: datetime):
        self.schema = schema
        self.now = now
        self.params: list[Any] = []
        self.resolved: dict[str, str] = {}

    def bind(self, v: Any) -> str:
        if is_date_token(v):
            self.resolved[v] = resolve_relative(v, self.now)
            v = self.resolved[v]
        if isinstance(v, bool):
            v = int(v)
        self.params.append(v)
        return "?"

    def col(self, name: str) -> str:
        return _q(name)

    def cond(self, f: Filter) -> str:
        c = self.col(f.field)
        if f.op == "exists":
            return f"{c} IS NOT NULL"
        if f.op == "missing":
            return f"{c} IS NULL"
        if f.op in ("in", "not_in"):
            marks = ", ".join(self.bind(x) for x in f.value)  # type: ignore[union-attr]
            return f"{c} IN ({marks})" if f.op == "in" else f"({c} IS NULL OR {c} NOT IN ({marks}))"
        if f.op == "contains":
            self.params.append("%" + str(f.value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            return f"{c} LIKE ? ESCAPE '\\'"
        return f"{c} {_OPS[f.op]} {self.bind(f.value)}"

    def bucket(self, g) -> str:
        c = self.col(g.field)
        return {"none": c, "day": f"substr({c}, 1, 10)", "month": f"substr({c}, 1, 7)", "year": f"substr({c}, 1, 4)",
                "week": f"strftime('%Y-W%W', substr({c}, 1, 10))"}[g.bucket]

    def compile(self, spec: QuerySpec) -> SeriesCompiled:
        s = self.schema
        tf = s.time_field
        fields = [*s.series_key, tf, *[m for m in s.measures]] + [f for f in s.fields if f not in
                                                                   (*s.series_key, tf, *s.measures)]
        point_id = ("type || ':' || (SELECT group_concat(value, '/') FROM json_each(series_points.key)) || '@' || t")
        cols = ", ".join(f"json_extract(doc, '$.{f}') AS {_q(f)}" for f in fields)
        key_filters = [f for f in spec.filters if f.field in s.series_key]
        other_filters = [f for f in spec.filters if f.field not in s.series_key]
        base_where = ["type = ?"]
        self.params.append(s.name)
        base_where += [self.cond(f) for f in key_filters]
        base = f"SELECT key, t, {point_id} AS _id, {cols} FROM series_points WHERE {' AND '.join(base_where)}"
        win_cols = []
        for w in spec.window:
            if w.unit == "points":
                frame = f"ROWS BETWEEN {int(w.size) - 1} PRECEDING AND CURRENT ROW"
                order = "t"
            else:
                frame = f"RANGE BETWEEN {int(w.size) - 1} PRECEDING AND CURRENT ROW"
                order = "julianday(substr(t, 1, 10))"
            win_cols.append(f"{w.agg.upper()}({self.col(w.measure)}) OVER (PARTITION BY key ORDER BY {order} {frame}) "
                            f"AS {_q(w.name)}")
        windowed = f"SELECT *{', ' + ', '.join(win_cols) if win_cols else ''} FROM base"
        where = [self.cond(f) for f in other_filters]
        windows = {w.name for w in spec.window}
        for c in spec.compare:
            rhs = self.col(c.baseline.window) if c.baseline.window else self.bind(c.baseline.value)
            where.append(f"{self.col(c.measure)} {_OPS[c.op]} {rhs}")
        filtered = "SELECT * FROM windowed" + (f" WHERE {' AND '.join(where)}" if where else "")
        lo = hi = None
        for f in other_filters:  # the asked period, for the gap warning
            if f.field == tf and isinstance(f.value, str):
                v = resolve_relative(f.value, self.now) if is_date_token(f.value) else f.value
                if f.op in ("gte", "gt", "eq"):
                    lo = v if f.op != "gt" else (date.fromisoformat(v[:10]) + timedelta(days=1)).isoformat()
                if f.op in ("lte", "lt", "eq"):
                    hi = v if f.op != "lt" else (date.fromisoformat(v[:10]) - timedelta(days=1)).isoformat()
        head = f"WITH base AS ({base}), windowed AS ({windowed}), filtered AS ({filtered})"
        if spec.mode == "list":
            shown = list(dict.fromkeys(spec.list_fields or [*s.series_key, tf, *s.measures, *windows]))
            order = (f"{self.col(spec.order_by)} {'DESC' if spec.descending else 'ASC'}, t"
                     if spec.order_by and spec.order_by != "count" else f"t {'DESC' if spec.descending else 'ASC'}")
            sql = (f"{head} SELECT _id, {', '.join(self.col(c) for c in shown)}, COUNT(*) OVER () AS _total "
                   f"FROM filtered ORDER BY {order}, _id LIMIT ?")
            return SeriesCompiled(sql, [*self.params, spec.limit or 100], "list", columns=shown,
                                  resolved_dates=self.resolved, period=(lo, hi))
        group_exprs, gnames = [], []
        for g in spec.group_by:
            group_exprs.append(self.bucket(g))
            gnames.append(g.field if g.bucket == "none" else f"{g.field}_{g.bucket}")

        ranked_cols, selects = [], [f"{e} AS g{i}" for i, e in enumerate(group_exprs)]
        mnames = []
        for i, m in enumerate(spec.measures):
            cond = " AND ".join(self.cond(w) for w in m.where) if m.where else None
            v = self.col(m.field) if m.field else None
            if m.agg in ("count", "count_if"):
                expr = f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)" if cond else "COUNT(*)"
            elif m.agg in ("sum", "avg", "min", "max"):
                expr = f"{m.agg.upper()}(CASE WHEN {cond} THEN {v} END)" if cond else f"{m.agg.upper()}({v})"
            else:  # first / last / change / change_pct: the first and last non-empty value in time order
                ra, rd = f"_ra{i}", f"_rd{i}"
                partition = "PARTITION BY " + ", ".join([f"({v} IS NULL)", *group_exprs])  # empty values rank apart
                ranked_cols.append(f"ROW_NUMBER() OVER ({partition} ORDER BY t) AS {ra}")
                ranked_cols.append(f"ROW_NUMBER() OVER ({partition} ORDER BY t DESC) AS {rd}")
                first = f"MAX(CASE WHEN {v} IS NOT NULL AND {ra} = 1 THEN {v} END)"
                last = f"MAX(CASE WHEN {v} IS NOT NULL AND {rd} = 1 THEN {v} END)"
                expr = {"first": first, "last": last, "change": f"({last} - {first})",
                        "change_pct": f"(({last} - {first}) * 100.0 / NULLIF({first}, 0))"}[m.agg]
            selects.append(f"{expr} AS m{i}")
            mnames.append(m.name)
        selects += ["COUNT(*) AS _n", "json_group_array(_id) AS _ids"]
        src = "filtered"
        if ranked_cols:
            head += f", ranked AS (SELECT *, {', '.join(ranked_cols)} FROM filtered)"
            src = "ranked"
        sql = f"{head} SELECT {', '.join(selects)} FROM {src}"
        params = list(self.params)
        if group_exprs:
            sql += f" GROUP BY {', '.join(f'g{i}' for i in range(len(group_exprs)))}"
            if spec.having:
                conds = []
                for h in spec.having:
                    c = "_n" if h.measure == "count" else f"m{mnames.index(h.measure)}"
                    conds.append(f"{c} {_OPS[h.op] if h.op != 'ne' else '!='} ?")
                    params.append(h.value)
                sql += " HAVING " + " AND ".join(conds)
            if spec.order_by and spec.order_by in mnames:
                sql += f" ORDER BY m{mnames.index(spec.order_by)} {'DESC' if spec.descending else 'ASC'}"
            elif spec.order_by == "count":
                sql += f" ORDER BY _n {'DESC' if spec.descending else 'ASC'}"
            else:
                sql += f" ORDER BY {', '.join(f'g{i}' for i in range(len(group_exprs)))}"
        mode = "aggregate" if group_exprs or spec.measures else "count"
        return SeriesCompiled(sql, params, mode, gnames, mnames, resolved_dates=self.resolved, period=(lo, hi))


def compile_series(spec: QuerySpec, schema: SchemaDef, now: datetime) -> SeriesCompiled:
    return SeriesCompiler(schema, now).compile(spec)


def execute_series(compiled: SeriesCompiled, fetch):
    from .answer import QueryResult, _num

    raw = fetch(compiled.sql, compiled.params)
    if compiled.mode == "list":
        rows = [{"id": r["_id"], **{c: _num(r[c]) for c in compiled.columns}} for r in raw]
        return QueryResult("list", rows, raw[0]["_total"] if raw else 0, [r["id"] for r in rows])
    groups, evidence = [], []
    for r in raw:
        ids = [i for i in json.loads(r["_ids"]) if i is not None]
        groups.append({"group": {n: r[f"g{i}"] for i, n in enumerate(compiled.group_columns)},
                       "measures": {n: _num(r[f"m{i}"]) for i, n in enumerate(compiled.measure_names)},
                       "n": r["_n"], "ids": ids})
        evidence += ids
    total = sum(g["n"] for g in groups)
    if not compiled.group_columns and groups and groups[0]["n"] == 0:
        groups = [{**groups[0], "ids": []}]
    return QueryResult(compiled.mode, [], total, evidence, groups)


def series_warnings(spec: QuerySpec, schema: SchemaDef, compiled: SeriesCompiled, result, now: datetime,
                    fetch) -> list[str]:
    """Missing time points in the asked period (day series), small samples, a period still in progress."""
    from .answer import MIN_SAMPLE

    out: list[str] = []
    if schema.granularity == "day":
        key_filters = [f for f in spec.filters if f.field in schema.series_key]
        c = SeriesCompiler(schema, now)
        keys = "".join(f", json_extract(doc, '$.{f}') AS {_q(f)}" for f in schema.series_key)
        c.params.append(schema.name)
        where = [c.cond(f) for f in key_filters]  # on the extracted key columns, as in compile()
        where += ["t >= ?"] * bool(compiled.period[0]) + ["t <= ?"] * bool(compiled.period[1])
        rows = fetch(f"WITH p AS (SELECT key, t{keys} FROM series_points WHERE type = ?) "
                     "SELECT (SELECT group_concat(value, '/') FROM json_each(p.key)) AS k, MIN(t) AS lo, MAX(t) AS hi, "
                     f"COUNT(DISTINCT t) AS n FROM p {'WHERE ' + ' AND '.join(where) if where else ''} GROUP BY key",
                     [*c.params, *[p[:10] for p in compiled.period if p]])
        gaps = []
        for r in rows:
            lo = compiled.period[0][:10] if compiled.period[0] else r["lo"]
            hi = min(compiled.period[1][:10], now.date().isoformat()) if compiled.period[1] else r["hi"]
            expected = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
            if expected > r["n"]:
                gaps.append(f"{r['k']}: {expected - r['n']} of {expected} days have no point")
        if gaps:
            out.append(f"Missing days in {compiled.period[0] or 'the data'}..{compiled.period[1] or 'the end'}: "
                       + "; ".join(gaps) + ". Averages and changes use the days that exist.")
    if result.total == 0:
        out.append("No matching points.")
    elif result.mode == "aggregate":
        small = [g for g in result.groups if g["n"] < MIN_SAMPLE]
        if small:
            out.append("Small sample (n<%d) in %d group(s)." % (MIN_SAMPLE, len(small)))
    today = now.date().isoformat()
    for g in spec.group_by:
        if g.bucket in ("week", "month", "year"):
            cur = {"week": now.date().strftime("%Y-W%W"), "month": today[:7], "year": today[:4]}[g.bucket]
            name = f"{g.field}_{g.bucket}"
            if any(gr["group"].get(name) == cur for gr in result.groups):
                out.append(f"{name}={cur} is still in progress (incomplete period).")
    if compiled.resolved_dates:
        out.append("Relative dates resolved as: " + ", ".join(f"{k} = {v}" for k, v in compiled.resolved_dates.items())
                   + f" ({now.tzinfo}).")
    return out
