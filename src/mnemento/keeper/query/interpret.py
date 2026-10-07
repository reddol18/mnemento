"""LLM interpretation: question -> QuerySpec or Clarification (PLAN 5-1 A ①).

The model sees only the relevant part of the schema dictionary (field names, types, descriptions,
allowed values, and for free-text fields a short list of observed values) — never the records.
Output is structured (JSON Schema). A spec that uses anything outside the dictionary is rejected
and the model is asked once more with the error list.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...schema.definition import SchemaDef
from ..llm import LLMAdapter
from ..trace import Trace
from .spec import QuerySpec, validate_spec


class InterpretOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["query", "clarify"] = Field(
        description="query if the question can be answered with the schemas (vague words read with a "
                    "default, listed in spec.defaults_used); clarify only if no default reading would give "
                    "a fair answer (e.g. which of two different records, or information the dictionary lacks)."
    )
    spec: QuerySpec | None = Field(default=None, description="Required when kind=query.")
    clarify_question: str | None = Field(default=None, description="Required when kind=clarify.")
    options: list[str] = Field(default_factory=list, description="2-5 concrete choices for clarify.")


@dataclass
class Clarification:
    question: str
    options: list[str] = field(default_factory=list)
    reason: str = ""


class InterpretError(Exception):
    def __init__(self, message: str, errors: list[str]):
        super().__init__(message)
        self.errors = errors


SYSTEM_PROMPT = """You translate questions about a structured record book into a QuerySpec (JSON).
Rules:
- Use ONLY the record types, fields and allowed values listed in the dictionary. Never invent fields.
  If the question needs information the dictionary does not have, choose kind=clarify and say what is missing.
- Values of enum fields must be one of the allowed values (map words to them using the labels).
- Dates: YYYY-MM-DD, or relative tokens resolved by the system: @today, @today-Nd, @this_week_start,
  @this_month_start, @last_month_start, @this_year_start (offsets like +7d, -1m allowed).
  Prefer tokens for words like today/yesterday/this month/last month.
- To filter a reference field (one with "ref") by the referenced record's name, use op "name_is" with the name
  exactly as the question writes it, including any code or number given with it (e.g. "바이오주(900001)"):
  the Keeper matches identifiers first.
- Current state vs history: op "eq"/"in" on status tests the CURRENT status only. Whether a record EVER
  reached a status ("was viewed", "got to", "ever", "~된 적", "~까지 간", "열람된 지원") uses op "reached"
  on status — e.g. viewed-then-rejected records count as "viewed".
- mode: "count" for how-many, "list" for which/what records, "aggregate" for grouped numbers,
  comparisons and rates (use group_by + measures; count_if with `where` for "how many of them ...").
- For comparisons over time ("last time", "before", "again", "지난번", "예전에도"), do NOT ask which period:
  group by month of the record's main date (group_by with bucket "month") so every period is shown side by
  side, and say so in `interpretation`.
- "Viewed first / sooner" can be measured as the share of records with a view date (count_if exists).
- Order of events ("in the order they were viewed", "most recently rejected", "the first one to ...") is
  ordered by when the event happened: use order_by_event (descending=true for most recent first). Date
  fields hold only the day, so ordering by them ties records of the same day.
- Weeks are Monday-Sunday. "The last week of <month>" is the last Monday-Sunday week that lies entirely
  inside that month: @last_full_week_start(YYYY-MM) .. @last_full_week_end(YYYY-MM). "This week" starts
  at @this_week_start, "last week" is @this_week_start-1w .. @this_week_start-1d.
- The event log records corrections (corrected), retractions (retracted) and ordinary edits (updated)
  separately; use the kind whose label matches the question's word.
- Time BETWEEN two points of the same record as a condition ("viewed more than 3 days after applying",
  "applied over a month ago and still ...", "took less than a day") uses `elapsed`: start/end are a date
  field, an event (exact times, e.g. {kind: status_changed, to: viewed}) or today. unit "days" with date
  fields (or today), unit "hours" with events (or now); never mix a date field with an event.
  For an average of such a time use the measures avg_days_between / avg_hours_between_events instead.
- Lengths of time ("over three days", "more than a week", "한 달 넘게", "두 달 넘은") count days:
  1 week = 7 days, 1 month = 30 days, e.g. elapsed from the date field to today, op gt, value 30 — or the
  same as a filter "< @today-30d". Calendar tokens (@this_month_start, @last_month_start, -1m) are for named
  calendar periods only ("this month", "last month", "in January").
- Vague words ("quickly", "recently", "a lot", "빠르게", "최근", "오래된"): use the reading given under
  "vague words" in the dictionary; if there is none, pick a reasonable reading yourself. Either way write it
  into spec.defaults_used as "word = reading" (the user sees it and can correct it) — do not ask about it.
- A question with several parts: answer every part you can express in one spec (e.g. the average plus
  count_if measures for the follow-up part); never drop the main part because a side part is vague.
- Clarify only when no default reading would give a fair answer (e.g. which of two different records, or a
  field the dictionary lacks); then use kind=clarify with 2-5 options.
- Types marked SERIES (measurements over time, e.g. weight per day, spending per category per day) use
  source "series": filter the key field and the time field (a period), group_by a key field or the time
  field with a bucket, and measures avg/min/max/sum over a measure field, or first/last/change/change_pct
  for "from ... to", "how much did it go up/down" (the first and last value in the period). Points above or
  below an average of earlier points ("below its 4-week average", "above the 7-day moving average") use
  `window` (unit days or points, counting the point itself) plus `compare` with baseline.window. Without a
  period, a series question covers all points.
- Write `interpretation` in the question's language: one sentence restating what will be counted/listed."""


def _render_field(name: str, fd, observed: list[str] | None) -> str:
    bits = [fd.type if fd.type != "array" else f"array of {fd.items}"]
    if fd.format:
        bits.append(f"format {fd.format}")
    if fd.ref:
        bits.append(f"ref {fd.ref} (id; filter by name with name_is)")
    line = f"  - {name} ({', '.join(bits)}): {fd.description}"
    if fd.implies:
        line += "\n      history: " + "; ".join(f"{k} implies it went through {', '.join(v)}"
                                         for k, v in fd.implies.items()) + " (use op reached)"
    if fd.draft:
        line += "\n      UNREGISTERED (draft): stored in some records, no description yet; only records that have it count"
    if fd.enum:
        labels = fd.labels or {}
        registered = [v for v in fd.enum if v not in fd.draft_values]
        vals = [f"{v}" + (f" [{'/'.join(labels[v])}]" if v in labels else "") for v in registered]
        line += f"\n      allowed: {', '.join(vals)}"
        if fd.draft_values:
            line += f"\n      unregistered values in use (draft): {', '.join(map(str, fd.draft_values))}"
    elif observed:
        line += f"\n      observed values: {', '.join(observed)}"
    return line


def render_dictionary(schemas: list[SchemaDef], observed: dict[tuple[str, str], list[str]]) -> str:
    from .spec import EVENT_SCHEMA

    out = []
    for s in schemas:
        out.append(f"* {s.name}: {s.description}")
        if s.is_draft:
            out.append("  NEW TYPE, not approved yet: every field below is as stored (draft)")
        if s.kind == "series":
            out.append(f"  SERIES (QuerySpec source=series): one point per {' + '.join(s.series_key)} and "
                       f"{s.time_field} ({s.granularity}); measures: {', '.join(s.measures)}")
        if s.default_date_field:
            out.append(f"  (a bare date refers to {s.default_date_field})")
        if s.vague_terms:
            out.append("  vague words (default readings): " +
                       "; ".join(f"'{w}' = {r}" for w, r in s.vague_terms))
        for other, note in s.relations:
            out.append(f"  related to {other}: {note}")
        for fname, fd in s.fields.items():
            out.append(_render_field(fname, fd, observed.get((s.name, fname))))
    out.append(f"* event log (QuerySpec source=events, entity_type = the record type): {EVENT_SCHEMA.description}")
    for fname, fd in EVENT_SCHEMA.fields.items():
        out.append(_render_field(fname, fd, None))
    return "\n".join(out)


_WORD = re.compile(r"[\w가-힣]+")


def select_schemas(question: str, schemas: dict[str, SchemaDef]) -> list[SchemaDef]:
    """Relevant schemas: those whose keywords/labels/names appear in the question, plus the types related to
    them by a relation note (either side declares it), the types they reference AND the types that reference
    them (a question about postings or companies is usually answered from the records that point at them).
    Falls back to all schemas when nothing matches."""
    q = question.lower()
    picked: list[str] = []
    for name, s in schemas.items():
        words = [name, *s.keywords]
        for fd in s.fields.values():
            for syns in (fd.labels or {}).values():
                words += syns
        if any(w.lower() in q for w in words if w):
            picked.append(name)
    if not picked:
        return list(schemas.values())
    mentioned = list(picked)
    for name in mentioned:  # types with a declared relation to a mentioned type, either way (ADR-0017)
        related = {t for t, _ in schemas[name].relations} | {
            other for other, s in schemas.items() if any(t == name for t, _ in s.relations)}
        picked += [t for t in sorted(related) if t in schemas and t not in picked]
    for name in mentioned:  # referencing types (reverse direction)
        for other, s in schemas.items():
            if other not in picked and any(fd.ref == name for fd in s.fields.values()):
                picked.append(other)
    for name in list(picked):  # referenced types (forward direction)
        for fd in schemas[name].fields.values():
            if fd.ref and fd.ref in schemas and fd.ref not in picked:
                picked.append(fd.ref)
    return [schemas[n] for n in picked]


def interpret(
    question: str,
    *,
    llm: LLMAdapter,
    schemas: dict[str, SchemaDef],
    observed: dict[tuple[str, str], list[str]],
    now: datetime,
    tz: str,
    trace: Trace,
    max_attempts: int = 2,
    hint: str | None = None,
) -> QuerySpec | Clarification:
    relevant = select_schemas(question, schemas)
    dictionary = render_dictionary(relevant, observed)
    base_prompt = (
        f"Now: {now.isoformat()} ({tz}). Today is {now.date().isoformat()}.\n\n"
        f"Dictionary:\n{dictionary}\n\nQuestion: {question}"
    )
    if hint:
        base_prompt += f"\nRequested answer format (choose the spec whose result gives this): {hint}"
    out_schema = InterpretOutput.model_json_schema()
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = base_prompt
        if errors:
            prompt += ("\n\nYour previous answer was rejected:\n- " + "\n- ".join(errors)
                       + "\nFix it using only the dictionary.")
        result = llm.complete_json(system=SYSTEM_PROMPT, prompt=prompt, schema=out_schema,
                                   stage="interpret")
        result.usage.attempt = attempt
        trace.add_llm(result.usage)
        try:
            parsed = InterpretOutput.model_validate(result.data)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:8]
            continue
        if parsed.kind == "clarify":
            if not parsed.clarify_question:
                errors = ["kind=clarify requires clarify_question"]
                continue
            return Clarification(parsed.clarify_question, parsed.options, reason="interpreter")
        if parsed.spec is None:
            errors = ["kind=query requires spec"]
            continue
        errors = validate_spec(parsed.spec, schemas)
        if not errors:
            return parsed.spec
    raise InterpretError("could not interpret the question within the schema dictionary", errors)


def observed_values(fetch, schemas: list[SchemaDef], max_distinct: int = 12) -> dict[tuple[str, str], list[str]]:
    """Short lists of distinct values for low-cardinality, non-enum string fields (computed by
    SQL; a summary of the vocabulary, not the records)."""
    out: dict[tuple[str, str], list[str]] = {}
    for s in schemas:
        for fname, fd in s.fields.items():
            if fd.type != "string" or fd.enum or fd.ref or fd.format:
                continue
            table = "series_points WHERE type = ?" if s.kind == "series" else "entities WHERE type = ? AND retracted = 0"
            rows = fetch(
                f"SELECT json_extract(doc, '$.{fname}') AS v, COUNT(*) AS n FROM {table} "
                f"AND v IS NOT NULL GROUP BY v ORDER BY n DESC, v LIMIT ?",
                [s.name, max_distinct + 1],
            )
            if 0 < len(rows) <= max_distinct:
                out[(s.name, fname)] = [str(r["v"]) for r in rows]
    return out


def dump_spec(spec: QuerySpec) -> dict[str, Any]:
    return json.loads(spec.model_dump_json(exclude_defaults=True))
