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
        description="query if the question can be answered with the schemas; clarify if it is "
                    "ambiguous (e.g. unclear period, target or comparison)."
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
- To filter a reference field (one with "ref") by the referenced record's name, use op "name_is" with the name.
- Current state vs history: op "eq"/"in" on status tests the CURRENT status only. Whether a record EVER
  reached a status ("was viewed", "got to", "ever", "~된 적", "~까지 간", "열람된 지원") uses op "reached"
  on status — e.g. viewed-then-rejected records count as "viewed".
- mode: "count" for how-many, "list" for which/what records, "aggregate" for grouped numbers,
  comparisons and rates (use group_by + measures; count_if with `where` for "how many of them ...").
- For comparisons over time ("last time", "before", "again", "지난번", "예전에도"), do NOT ask which period:
  group by month of the record's main date (group_by with bucket "month") so every period is shown side by
  side, and say so in `interpretation`.
- "Viewed first / sooner" can be measured as the share of records with a view date (count_if exists).
- Clarify only when no reasonable default exists and the choice changes the answer (e.g. which of two
  different records, or a field the dictionary lacks); then use kind=clarify with 2-5 options.
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
    if fd.enum:
        labels = fd.labels or {}
        vals = [f"{v}" + (f" [{'/'.join(labels[v])}]" if v in labels else "") for v in fd.enum]
        line += f"\n      allowed: {', '.join(vals)}"
    elif observed:
        line += f"\n      observed values: {', '.join(observed)}"
    return line


def render_dictionary(schemas: list[SchemaDef], observed: dict[tuple[str, str], list[str]]) -> str:
    out = []
    for s in schemas:
        out.append(f"* {s.name}: {s.description}")
        if s.default_date_field:
            out.append(f"  (a bare date refers to {s.default_date_field})")
        for fname, fd in s.fields.items():
            out.append(_render_field(fname, fd, observed.get((s.name, fname))))
    return "\n".join(out)


_WORD = re.compile(r"[\w가-힣]+")


def select_schemas(question: str, schemas: dict[str, SchemaDef]) -> list[SchemaDef]:
    """Relevant schemas: those whose keywords/labels/names appear in the question, plus the types
    their reference fields point to. Falls back to all schemas when nothing matches."""
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
    for name in list(picked):
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
            rows = fetch(
                f"SELECT json_extract(doc, '$.{fname}') AS v, COUNT(*) AS n FROM entities "
                f"WHERE type = ? AND retracted = 0 AND v IS NOT NULL GROUP BY v ORDER BY n DESC, v LIMIT ?",
                [s.name, max_distinct + 1],
            )
            if 0 < len(rows) <= max_distinct:
                out[(s.name, fname)] = [str(r["v"]) for r in rows]
    return out


def dump_spec(spec: QuerySpec) -> dict[str, Any]:
    return json.loads(spec.model_dump_json(exclude_defaults=True))
