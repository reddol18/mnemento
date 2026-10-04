"""Answer composition (PLAN 5-1 A ③): results + evidence (entity ids) + automatic limitation
warnings. The text is produced by a template; optional LLM narration sees aggregates only."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ...schema.definition import SchemaDef
from .compile import CompiledQuery
from .spec import QuerySpec

MIN_SAMPLE = 5
RECENT_DAYS = 3  # records younger than this may not have an outcome yet


@dataclass
class QueryResult:
    mode: str
    rows: list[dict[str, Any]]
    total: int
    evidence: list[str]
    groups: list[dict[str, Any]] = field(default_factory=list)  # aggregate rows (with ids)


def execute(compiled: CompiledQuery, fetch) -> QueryResult:
    raw = fetch(compiled.sql, compiled.params)
    if compiled.mode == "list":
        rows = [_row(r) for r in raw]
        total = raw[0]["_total"] if raw else 0
        return QueryResult("list", rows, total, [r["id"] for r in rows])
    groups = []
    evidence: list[str] = []
    for r in raw:
        ids = [i for i in json.loads(r["_ids"]) if i is not None]
        g = {name: r[f"g{i}"] for i, name in enumerate(compiled.group_columns)}
        m = {name: _num(r[f"m{i}"]) for i, name in enumerate(compiled.measure_names)}
        group = {"group": g, "measures": m, "n": r["_n"], "ids": ids}
        if "_eids" in r.keys():  # events: the records they belong to
            group["entity_ids"] = [i for i in json.loads(r["_eids"]) if i is not None]
        groups.append(group)
        evidence += ids
    total = sum(g["n"] for g in groups)
    if not compiled.group_columns and groups and groups[0]["n"] == 0:
        groups = [{**groups[0], "ids": []}]
    return QueryResult(compiled.mode, [], total, evidence, groups)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    if "doc" in r:  # entity
        return {"id": r["id"], **json.loads(r["doc"])}
    out = {k: v for k, v in r.items() if not k.startswith("_")}  # event
    out["payload"] = json.loads(out["payload"])
    return out


def _num(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 2)
    return v


def ref_labels(schema: SchemaDef, result: QueryResult, get_entity) -> dict[str, str]:
    """Names of the records that reference fields point to (company name, posting title), so answers
    are readable: co_031 -> its name."""
    refs = [f for f, fd in schema.fields.items() if fd.ref]
    ids = {r[f] for r in result.rows for f in refs if isinstance(r.get(f), str)}
    ids |= {v for g in result.groups for k, v in g["group"].items() if k in refs and isinstance(v, str)}
    labels = {}
    for i in sorted(ids):
        e = get_entity(i)
        name = e and (e.doc.get("name") or e.doc.get("title"))
        if name:
            labels[i] = name
    return labels


def warnings_for(spec: QuerySpec, schema: SchemaDef, result: QueryResult, now: datetime,
                 resolved_dates: dict[str, str], fetch) -> list[str]:
    out: list[str] = []
    if spec.source == "entities":
        # ADR-0013/0014: records without a field the question relies on cannot match it — say how many.
        # (Optional fields, unknown dates, fields added later or still drafts.)
        presence = {"exists", "missing"}
        used = {f.field for f in spec.filters if f.op not in presence}
        used |= {g.field for g in spec.group_by}
        for m in spec.measures:
            used |= {x for x in (m.field, m.field_end) if x}
            used |= {w.field for w in m.where if w.op not in presence}
        for fname in sorted(f for f in used if f in schema.fields):
            rows = fetch(f"SELECT COUNT(*) AS n FROM entities WHERE type = ? AND retracted = 0 "
                         f"AND json_extract(doc, '$.{fname}') IS NULL", [spec.entity_type])
            missing = rows[0]["n"] if rows else 0
            if missing:
                tag = " (unregistered draft field)" if schema.fields[fname].draft else ""
                out.append(f"{missing} {spec.entity_type} record(s) have no {fname}{tag} and could not be "
                           f"counted by it.")
        uses_event_time = spec.order_by_event is not None or any(
            m.agg == "avg_hours_between_events" for m in spec.measures)
        if uses_event_time:
            rows = fetch("SELECT COUNT(DISTINCT entity_id) AS n FROM events WHERE entity_type = ? "
                         "AND at_precision != 'time'", [spec.entity_type])
            inexact = rows[0]["n"] if rows else 0
            if inexact:
                out.append(f"{inexact} record(s) have events without an exact time; they are left out of "
                           f"time-based ordering and durations (ADR-0013).")
    today = now.date()
    if result.total == 0:
        out.append("No matching records. If you expected some, they may not have been recorded yet.")
    # sample size
    if result.mode == "aggregate" and spec.group_by:
        small = [g for g in result.groups if g["n"] < MIN_SAMPLE]
        if small:
            labels = ", ".join(_group_label(g["group"]) + f" (n={g['n']})" for g in small)
            out.append(f"Small sample (n<{MIN_SAMPLE}) in: {labels}. Treat differences as anecdotal.")
    elif 0 < result.total < MIN_SAMPLE and spec.mode == "aggregate":
        out.append(f"Small sample: n={result.total} (<{MIN_SAMPLE}).")
    # incomplete periods: buckets that include today
    current = {"day": today.isoformat(), "month": today.isoformat()[:7], "year": str(today.year),
               "week": today.strftime("%Y-W%W")}
    for i, g in enumerate(spec.group_by):
        if g.bucket in current:
            name = g.field if g.bucket == "none" else f"{g.field}_{g.bucket}"
            if any(gr["group"].get(name) == current[g.bucket] for gr in result.groups):
                out.append(f"{name}={current[g.bucket]} is still in progress (incomplete period).")
    # immature records: outcome measures over records created in the last few days
    date_field = schema.default_date_field
    uses_outcome = any(m.agg == "count_if" for m in spec.measures) or any(
        f.field == "status" for f in spec.filters)
    if date_field and uses_outcome and result.evidence:
        cutoff = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
        ids = result.evidence[:1000]
        marks = ", ".join("?" for _ in ids)
        rows = fetch(
            f"SELECT COUNT(*) AS n FROM entities WHERE id IN ({marks}) "
            f"AND json_extract(doc, '$.{date_field}') >= ?", [*ids, cutoff])
        recent = rows[0]["n"] if rows else 0
        if recent:
            out.append(f"{recent} of these records have {date_field} within the last {RECENT_DAYS} days "
                       f"(since {cutoff}); their outcome may not be known yet.")
    # free-text matching is fragile: say so and suggest a structured field (PLAN 5-1 B)
    text_filters = {f.field for f in [*spec.filters, *[w for m in spec.measures for w in m.where]]
                    if f.op == "contains" and f.field in schema.fields
                    and schema.fields[f.field].type == "string" and not schema.fields[f.field].enum}
    for fname in sorted(text_filters):
        out.append(f"This answer relies on matching free text in '{fname}', so records worded differently "
                   f"are missed. Proposal: add a structured field (e.g. an enum) for this distinction "
                   f"(propose_schema / additive change, ADR-0005).")
    if resolved_dates:
        out.append("Relative dates resolved as: " +
                   ", ".join(f"{k} = {v}" for k, v in resolved_dates.items()) + f" ({now.tzinfo}).")
    return out


def _group_label(group: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    return " / ".join(f"{k}={v}" + (f" ({labels[v]})" if isinstance(v, str) and v in labels else "")
                      for k, v in group.items()) or "all"


def render_text(spec: QuerySpec, result: QueryResult, notes: list[str],
                labels: dict[str, str] | None = None, default_fields: list[str] | None = None) -> str:
    labels = labels or {}
    named = lambda v: f"{v} ({labels[v]})" if isinstance(v, str) and v in labels else f"{v}"  # noqa: E731
    lines = []
    if spec.interpretation:
        lines.append(f"Interpretation: {spec.interpretation}")
    if result.mode == "count":
        lines.append(f"Answer: {result.total}")
        if result.evidence:
            lines.append("Evidence: " + ", ".join(result.evidence[:50])
                         + (f" … (+{len(result.evidence) - 50})" if len(result.evidence) > 50 else ""))
    elif result.mode == "list":
        shown = len(result.rows)
        lines.append(f"Answer: {result.total} record(s)" + (f", showing {shown}" if shown < result.total else ""))
        keys = spec.list_fields or default_fields or []  # no fields asked for: who/what and the status
        for r in result.rows:
            if spec.source == "events":
                what = r["kind"] + (f" {r['payload'].get('to')}" if r["kind"] == "status_changed" else "")
                if r.get("target_event_id"):
                    what += f" of {r['target_event_id']}"
                lines.append(f"- {r['id']} {r['entity_id']}: {what} at {r['at']} by {r['by']}"
                             + (f" — evidence: {r['evidence']}" if r.get("evidence") else ""))
                continue
            extra = ", ".join(f"{k}={named(r.get(k))}" for k in keys if k in r)
            lines.append(f"- {r['id']}" + (f" ({extra})" if extra else ""))
    else:
        lines.append(f"Answer: {len(result.groups)} group(s), {result.total} record(s)")
        for g in result.groups:
            ms = []
            for k, v in g["measures"].items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and g["n"] and v <= g["n"] \
                        and any(m.name == k and m.agg == "count_if" for m in spec.measures):
                    ms.append(f"{k}={v}/{g['n']} ({v / g['n']:.0%})")
                else:
                    ms.append(f"{k}={v}")
            lines.append(f"- {_group_label(g['group'], labels)}: n={g['n']}" + (f", {', '.join(ms)}" if ms else "")
                         + f"  [evidence: {', '.join(g['ids'][:10])}{' …' if len(g['ids']) > 10 else ''}]")
    for n in notes:
        lines.append(f"Note: {n}")
    return "\n".join(lines)


def narration_payload(question: str, interpretation: str, result: dict[str, Any], warnings: list[str],
                      list_fields: list[str]) -> dict[str, Any]:
    """What an LLM narrator may see: the question, the interpretation and aggregates. No documents.
    `result` is the answer's result dict (mode, total, groups, rows)."""
    payload: dict[str, Any] = {"question": question, "interpretation": interpretation,
                               "mode": result.get("mode"), "total": result.get("total"), "warnings": warnings}
    if result.get("mode") == "aggregate":
        payload["groups"] = [{"group": g["group"], "n": g["n"], "measures": g["measures"]}
                             for g in result.get("groups") or []]
    elif result.get("mode") == "list":
        payload["records"] = [{k: r.get(k) for k in ["id", *list_fields]} for r in (result.get("rows") or [])[:50]]
    return payload


NARRATE_SYSTEM = """You write the final answer to a question about a record book.
You receive the interpretation, aggregated results and warnings computed by the system.
Answer in the question's language, 1-4 sentences, using only these numbers. Mention every warning
that affects the conclusion (small samples, incomplete periods, immature records). Do not invent data."""

NARRATE_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
