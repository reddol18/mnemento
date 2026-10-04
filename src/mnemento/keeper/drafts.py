"""Drafts: fields and enum values that stored records use but the dictionary has not registered (ADR-0014).

Store first, organize later: a write is not rejected for an unknown field or an enum value outside the list;
the value is stored as given and shows up here. Drafts are derived from the current records (never a
separate list that could drift), so they are always exactly what is stored.

- `draft_dictionary` — what is unregistered, with observed types, samples, counts and first-seen time
- `effective_schema` — the registered schema plus queryable drafts, used to validate and compile questions
- `similar_names` — "did you mean applicants?" checks for new names (never merged automatically, ADR-0006)
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import replace
from typing import Any

from ..ledger import Ledger
from ..schema.definition import NAME_RE, FieldDef, SchemaDef

_JSON_TO_FIELD = {"integer": "integer", "real": "number", "text": "string", "true": "boolean", "false": "boolean",
                  "array": "array"}


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.lower())


def draft_dictionary(ledger: Ledger, entity_type: str, schema: SchemaDef | None = None) -> dict[str, Any]:
    """{"fields": {name: {types, samples, count, first_seen, queryable}}, "values": {field: {value: count}}}"""
    schema = schema or ledger.schemas.get(entity_type)
    fetch = ledger.storage.fetch_all
    rows = fetch(
        "SELECT je.key AS key, je.type AS type, COUNT(*) AS n, json_group_array(je.value) AS vals "
        "FROM entities, json_each(entities.doc) AS je WHERE entities.type = ? AND entities.retracted = 0 "
        "GROUP BY je.key, je.type", [entity_type])
    fields: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["key"] in schema.fields:
            continue
        f = fields.setdefault(r["key"], {"types": [], "samples": [], "count": 0,
                                         "queryable": bool(NAME_RE.match(r["key"]))})
        f["types"].append(_JSON_TO_FIELD.get(r["type"], r["type"]))
        f["count"] += r["n"]
        for v in json.loads(r["vals"]):
            if len(f["samples"]) < 5 and v not in f["samples"]:
                f["samples"].append(v)
    for name, f in fields.items():
        first = fetch("SELECT MIN(recorded_at) AS t FROM events WHERE entity_type = ? "
                      "AND json_extract(payload, ?) IS NOT NULL", [entity_type, f'$."{name}"'])
        f["first_seen"] = first[0]["t"] if first else None
        f["types"] = sorted(set(f["types"]))
    values: dict[str, dict[Any, int]] = {}
    for fname, fd in schema.fields.items():
        if not fd.enum:
            continue
        marks = ", ".join("?" for _ in fd.enum)
        out = fetch(f"SELECT json_extract(doc, ?) AS v, COUNT(*) AS n FROM entities WHERE type = ? AND retracted = 0 "
                    f"AND json_extract(doc, ?) IS NOT NULL AND json_extract(doc, ?) NOT IN ({marks}) GROUP BY v",
                    [f"$.{fname}", entity_type, f"$.{fname}", f"$.{fname}", *fd.enum])
        if out:
            values[fname] = {r["v"]: r["n"] for r in out}
    return {"fields": fields, "values": values}


def effective_schema(ledger: Ledger, entity_type: str) -> SchemaDef:
    """The registered schema plus drafts, for validating and compiling questions. Draft fields are typed by
    what was observed (one JSON type only) and marked `draft`; draft enum values are appended to `enum` and
    listed in `draft_values`. Draft names that are not plain identifiers stay unqueryable until organized."""
    schema = ledger.schemas.get(entity_type)
    drafts = draft_dictionary(ledger, entity_type, schema)
    fields = dict(schema.fields)
    for fname, extra in drafts["values"].items():
        fd = fields[fname]
        new = tuple(v for v in extra if v not in fd.enum)
        fields[fname] = replace(fd, enum=(*fd.enum, *new), draft_values=new)
    for name, d in drafts["fields"].items():
        if not d["queryable"] or len(d["types"]) != 1 or d["types"][0] not in ("string", "integer", "number",
                                                                                "boolean"):
            continue
        fields[name] = FieldDef(name, d["types"][0],
                                f"(unregistered draft, no description) seen in {d['count']} record(s), "
                                f"e.g. {', '.join(map(str, d['samples'][:3]))}", draft=True)
    return replace(schema, fields=fields)


def similar_names(name: str, candidates: list[str], labels: dict[str, list[str]] | None = None) -> list[str]:
    """Registered or draft field names that look like `name` (normalised spelling, edit distance, labels)."""
    out = []
    n = _norm(name)
    for c in candidates:
        if c == name:
            continue
        cn = _norm(c)
        if cn == n or n in cn or cn in n or difflib.SequenceMatcher(None, n, cn).ratio() >= 0.75:
            out.append(c)
        elif labels and any(_norm(lb) == n for lb in labels.get(c, [])):
            out.append(c)
    return out


def write_questions(ledger: Ledger, entity_type: str, payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """After a successful write: which drafts it introduced or used, and "did you mean" questions."""
    schema = ledger.schemas.get(entity_type)
    drafts = draft_dictionary(ledger, entity_type, schema)
    questions: list[str] = []
    new_fields = [k for k in payload if k not in schema.fields]
    for k in new_fields:
        others = [*schema.fields, *[d for d in drafts["fields"] if d != k]]
        for s in similar_names(k, others):
            questions.append(f"'{k}' looks like the existing field '{s}' — is it the same thing? "
                             f"(stored as given; organize it with propose_schema)")
    new_values = {}
    for fname, fd in schema.fields.items():
        v = payload.get(fname)
        if fd.enum and v is not None and v not in fd.enum and not isinstance(v, (list, dict)):
            new_values[fname] = v
            for allowed in fd.enum:
                names = [str(allowed), *(fd.labels or {}).get(allowed, ())]
                if any(_norm(str(v)) == _norm(x) or difflib.SequenceMatcher(None, _norm(str(v)), _norm(x)).ratio()
                       >= 0.75 for x in names):
                    questions.append(f"'{v}' for {fname} looks like the existing value '{allowed}' — is it the same "
                                     f"thing? (stored as given)")
                    break
    return questions, {"fields": new_fields, "values": new_values}
