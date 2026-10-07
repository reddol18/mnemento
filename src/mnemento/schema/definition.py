"""Schema definitions — the "dictionary" the Keeper uses for validation and query planning.

A definition is a small JSON document:

    {
      "name": "application",
      "version": 1,
      "description": "...",
      "fields": {
        "status": {"type": "string", "description": "...", "enum": [...],
                   "required": true, "indexed": true},
        "applied_at": {"type": "string", "format": "date", "description": "..."}
      },
      "examples": [{"id": "app_...", "doc": {...}}]
    }

It is compiled into a JSON Schema (draft 2020-12) for validation. Unknown fields are rejected
(`additionalProperties: false`) for the *registered* view of the dictionary. Writes are checked leniently
(ADR-0014): fields and enum values the dictionary does not know yet are stored and surface as drafts; only
violations of registered fields (missing required field, wrong type, bad date) are rejected.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import DocumentValidationError, SchemaDefinitionError
from ..timeutil import is_calendar_date, is_instant

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
FIELD_TYPES = {"string", "integer", "number", "boolean", "array"}
ITEM_TYPES = {"string", "integer", "number", "boolean"}
FORMATS = {"date", "date-time"}
_FIELD_KEYS = {"type", "description", "enum", "format", "required", "indexed", "items", "ref", "labels",
               "implies", "identifier"}
_SCHEMA_KEYS = {"name", "version", "description", "fields", "examples", "keywords", "default_date_field",
                "vague_terms", "relations", "kind", "series_key", "time_field", "granularity", "measures",
                "status", "proposal"}
KINDS = ("entity", "series")
STATUSES = ("registered", "draft")
GRANULARITIES = ("instant", "hour", "day", "week", "month")

FORMAT_CHECKER = FormatChecker(formats=())
FORMAT_CHECKER.checks("date")(is_calendar_date)
FORMAT_CHECKER.checks("date-time")(is_instant)


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: str
    description: str
    enum: tuple[Any, ...] | None = None
    format: str | None = None
    required: bool = False
    indexed: bool = False
    items: str | None = None  # item type for arrays
    ref: str | None = None  # this field holds the id of an entity of type `ref`
    labels: dict[str, tuple[str, ...]] | None = None  # enum value -> natural-language synonyms
    implies: dict[str, tuple[str, ...]] | None = None  # status -> earlier statuses it passed through
    draft: bool = False  # ADR-0014: seen in stored records, not registered (no description yet)
    draft_values: tuple[Any, ...] = ()  # ADR-0014: values seen outside `enum`, not registered
    identifier: bool = False  # external identifier (stock code, business number): same value = same entity

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.items:
            d["items"] = {"type": self.items}
        if self.enum is not None:
            d["enum"] = list(self.enum)
        if self.labels:
            d["labels"] = {k: list(v) for k, v in self.labels.items()}
        if self.implies:
            d["implies"] = {k: list(v) for k, v in self.implies.items()}
        if self.format:
            d["format"] = self.format
        if self.ref:
            d["ref"] = self.ref
        if self.required:
            d["required"] = True
        if self.indexed:
            d["indexed"] = True
        if self.identifier:
            d["identifier"] = True
        return d

    def json_schema(self) -> dict[str, Any]:
        s: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.items:
            s["items"] = {"type": self.items}
        if self.enum is not None:
            s["enum"] = list(self.enum)
        if self.format:
            s["format"] = self.format
        return s


@dataclass(frozen=True)
class SchemaDef:
    name: str
    version: int
    description: str
    fields: dict[str, FieldDef]
    examples: tuple[dict[str, Any], ...] = field(default=())
    keywords: tuple[str, ...] = ()  # natural-language words that point at this record type
    default_date_field: str | None = None  # the date a bare "on 10/2" refers to
    # vague words and the reading used unless the user says otherwise (ADR-0018), e.g. "빠르게 열람"
    vague_terms: tuple[tuple[str, str], ...] = ()
    # how this type relates to others, shown to the interpreter (ADR-0017: count each event from one type),
    # e.g. (("decision", "a trade and its decision may be the same event; count trades from trade"),)
    relations: tuple[tuple[str, str], ...] = ()
    # ADR-0016: entity = records with an event history; series = numeric measurements per (key..., time point)
    kind: str = "entity"
    series_key: tuple[str, ...] = ()
    time_field: str | None = None
    granularity: str | None = None
    measures: tuple[str, ...] = ()
    # ADR-0021: a draft type is created by the first record of a new kind; it has no fields and enforces nothing
    # (every field is an ADR-0014 draft) until the user approves `proposal`, the schema the agent suggested
    status: str = "registered"
    proposal: dict[str, Any] | None = None

    @property
    def is_draft(self) -> bool:
        return self.status == "draft"

    # ---- construction ---------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchemaDef":
        if not isinstance(data, dict):
            raise SchemaDefinitionError("schema definition must be an object")
        name = data.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SchemaDefinitionError(f"invalid schema name: {name!r}")
        unknown = set(data) - _SCHEMA_KEYS
        if unknown:
            raise SchemaDefinitionError(f"{name}: unknown keys {sorted(unknown)}")
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SchemaDefinitionError(f"{name}: version must be a positive integer")
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            raise SchemaDefinitionError(f"{name}: description is required")
        status = data.get("status", "registered")
        if status not in STATUSES:
            raise SchemaDefinitionError(f"{name}: status must be one of {STATUSES}")
        proposal = data.get("proposal")
        if proposal is not None and (status != "draft" or not isinstance(proposal, dict)):
            raise SchemaDefinitionError(f"{name}: proposal is an object on draft types only")
        raw_fields = data.get("fields", {} if status == "draft" else None)
        if not isinstance(raw_fields, dict) or (not raw_fields and status != "draft"):
            raise SchemaDefinitionError(f"{name}: fields must be a non-empty object")
        if status == "draft" and (raw_fields or data.get("kind", "entity") != "entity"):
            raise SchemaDefinitionError(f"{name}: a draft type is an entity type without fields (they are drafts)")
        fields = {fname: _parse_field(name, fname, fdef) for fname, fdef in raw_fields.items()}
        examples = data.get("examples", [])
        if not isinstance(examples, list):
            raise SchemaDefinitionError(f"{name}: examples must be a list")
        keywords = data.get("keywords", [])
        if not isinstance(keywords, list) or not all(isinstance(k, str) and k for k in keywords):
            raise SchemaDefinitionError(f"{name}: keywords must be a list of strings")
        default_date_field = data.get("default_date_field")
        if default_date_field is not None:
            f = fields.get(default_date_field)
            if f is None or f.format not in FORMATS:
                raise SchemaDefinitionError(f"{name}: default_date_field must be a date field")
        vague = data.get("vague_terms", {})
        if not isinstance(vague, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v.strip()
                                                  for k, v in vague.items()):
            raise SchemaDefinitionError(f"{name}: vague_terms must map words to non-empty readings")
        relations = data.get("relations", [])
        if not isinstance(relations, list) or not all(
                isinstance(r, dict) and set(r) == {"type", "note"} and isinstance(r["type"], str)
                and NAME_RE.match(r["type"]) and isinstance(r["note"], str) and r["note"].strip() for r in relations):
            raise SchemaDefinitionError(f"{name}: relations must be a list of {{type, note}}")
        kind = data.get("kind", "entity")
        if kind not in KINDS:
            raise SchemaDefinitionError(f"{name}: kind must be one of {KINDS}")
        series_key, time_field = tuple(data.get("series_key", [])), data.get("time_field")
        granularity, measures = data.get("granularity"), tuple(data.get("measures", []))
        if kind == "series":
            if not series_key or not all(isinstance(k, str) and k in fields and fields[k].type in ("string", "integer")
                                         and not fields[k].format for k in series_key):
                raise SchemaDefinitionError(f"{name}: series_key must list string/integer fields of the schema")
            if time_field not in fields or fields[time_field].format not in FORMATS:
                raise SchemaDefinitionError(f"{name}: time_field must be a date or date-time field")
            if granularity not in GRANULARITIES:
                raise SchemaDefinitionError(f"{name}: granularity must be one of {GRANULARITIES}")
            if (granularity in ("instant", "hour")) != (fields[time_field].format == "date-time"):
                raise SchemaDefinitionError(f"{name}: granularity {granularity} does not fit a "
                                            f"{fields[time_field].format} time_field")
            if not measures or not all(isinstance(m, str) and m in fields and fields[m].type in ("integer", "number")
                                       for m in measures):
                raise SchemaDefinitionError(f"{name}: measures must list numeric fields of the schema")
            if set(measures) & (set(series_key) | {time_field}):
                raise SchemaDefinitionError(f"{name}: a measure cannot be a key or the time field")
        elif series_key or time_field or granularity or measures:
            raise SchemaDefinitionError(f"{name}: series_key/time_field/granularity/measures need kind series")
        schema = cls(name, version, description, fields, tuple(copy.deepcopy(examples)),
                     tuple(keywords), default_date_field, tuple(vague.items()),
                     tuple((r["type"], r["note"]) for r in relations),
                     kind, series_key, time_field, granularity, measures, status,
                     copy.deepcopy(proposal) if proposal is not None else None)
        schema._check_json_schema()
        for i, ex in enumerate(schema.examples):
            if not isinstance(ex, dict) or not isinstance(ex.get("doc"), dict):
                raise SchemaDefinitionError(f"{name}: examples[{i}] must be {{id, doc}}")
            errors = schema.validation_errors(ex["doc"])
            if errors:
                raise SchemaDefinitionError(f"{name}: examples[{i}] is invalid: {'; '.join(errors)}")
        return schema

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "fields": {n: f.to_dict() for n, f in self.fields.items()},
        }
        if self.keywords:
            d["keywords"] = list(self.keywords)
        if self.default_date_field:
            d["default_date_field"] = self.default_date_field
        if self.vague_terms:
            d["vague_terms"] = dict(self.vague_terms)
        if self.relations:
            d["relations"] = [{"type": t, "note": n} for t, n in self.relations]
        if self.kind == "series":
            d.update({"kind": "series", "series_key": list(self.series_key), "time_field": self.time_field,
                      "granularity": self.granularity, "measures": list(self.measures)})
        if self.examples:
            d["examples"] = copy.deepcopy(list(self.examples))
        if self.is_draft:
            d["status"] = "draft"
            if self.proposal is not None:
                d["proposal"] = copy.deepcopy(self.proposal)
        return d

    def with_version(self, version: int) -> "SchemaDef":
        return SchemaDef(self.name, version, self.description, self.fields, self.examples,
                         self.keywords, self.default_date_field, self.vague_terms, self.relations,
                         self.kind, self.series_key, self.time_field, self.granularity, self.measures,
                         self.status, self.proposal)

    # ---- JSON Schema --------------------------------------------------------------------

    def json_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"mnemento:{self.name}:v{self.version}",
            "title": self.name,
            "description": self.description,
            "type": "object",
            "properties": {n: f.json_schema() for n, f in self.fields.items()},
            "required": [n for n, f in self.fields.items() if f.required],
            "additionalProperties": False,
        }

    def _check_json_schema(self) -> None:
        try:
            Draft202012Validator.check_schema(self.json_schema())
        except Exception as exc:  # jsonschema.SchemaError
            raise SchemaDefinitionError(f"{self.name}: {exc}") from exc

    def lenient_json_schema(self) -> dict[str, Any]:
        """Write-time schema (ADR-0014): unknown fields allowed, enums not enforced; types, formats and
        required fields of registered fields still are."""
        js = self.json_schema()
        js["additionalProperties"] = True
        js["properties"] = {n: {k: v for k, v in p.items() if k != "enum"} for n, p in js["properties"].items()}
        return js

    def validation_errors(self, doc: dict[str, Any], lenient: bool = False) -> list[str]:
        schema = self.lenient_json_schema() if lenient else self.json_schema()
        validator = Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        out = []
        for e in errors:
            where = ".".join(str(p) for p in e.absolute_path) or "(document)"
            out.append(f"{where}: {e.message}")
        return out

    def validate(self, doc: dict[str, Any], lenient: bool = False) -> None:
        errors = self.validation_errors(doc, lenient)
        if errors:
            raise DocumentValidationError(self.name, errors)

    @property
    def indexed_fields(self) -> list[str]:
        return [n for n, f in self.fields.items() if f.indexed]

    # ---- evolution (ADR-0005) -----------------------------------------------------------

    def breaking_changes_to(self, new: "SchemaDef") -> list[str]:
        """Changes from self -> new that could make an existing valid document invalid. A draft type promised
        nothing: approving it may add required fields and enums (stored records are checked when it is approved)."""
        reasons: list[str] = []
        if self.is_draft:
            return reasons
        if new.kind != self.kind:
            reasons.append(f"kind changed {self.kind} -> {new.kind}")
        if (new.series_key, new.time_field, new.granularity) != (self.series_key, self.time_field, self.granularity):
            reasons.append("series key, time field or granularity changed")
        for fname, old in self.fields.items():
            nf = new.fields.get(fname)
            if nf is None:
                reasons.append(f"field {fname!r} removed")
                continue
            if nf.type != old.type or nf.items != old.items:
                reasons.append(f"field {fname!r} type changed")
            if nf.format != old.format:
                reasons.append(f"field {fname!r} format changed")
            if nf.enum is not None:
                if old.enum is None:
                    reasons.append(f"field {fname!r} gained an enum restriction")
                else:
                    dropped = [v for v in old.enum if v not in nf.enum]
                    if dropped:
                        reasons.append(f"field {fname!r} enum values removed: {dropped}")
            if nf.required and not old.required:
                reasons.append(f"field {fname!r} became required")
        for fname, nf in new.fields.items():
            if fname not in self.fields and nf.required:
                reasons.append(f"new field {fname!r} is required")
        return reasons


def _parse_field(schema_name: str, fname: str, fdef: Any) -> FieldDef:
    where = f"{schema_name}.{fname}"
    if not isinstance(fname, str) or not NAME_RE.match(fname):
        raise SchemaDefinitionError(f"{where}: field names must match {NAME_RE.pattern}")
    if not isinstance(fdef, dict):
        raise SchemaDefinitionError(f"{where}: field definition must be an object")
    unknown = set(fdef) - _FIELD_KEYS
    if unknown:
        raise SchemaDefinitionError(f"{where}: unknown keys {sorted(unknown)}")
    ftype = fdef.get("type")
    if ftype not in FIELD_TYPES:
        raise SchemaDefinitionError(f"{where}: type must be one of {sorted(FIELD_TYPES)}")
    description = fdef.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SchemaDefinitionError(f"{where}: description is required")
    items = None
    if ftype == "array":
        raw_items = fdef.get("items")
        if not isinstance(raw_items, dict) or raw_items.get("type") not in ITEM_TYPES:
            raise SchemaDefinitionError(f"{where}: arrays need items.type in {sorted(ITEM_TYPES)}")
        items = raw_items["type"]
    elif "items" in fdef:
        raise SchemaDefinitionError(f"{where}: items is only valid for arrays")
    enum = fdef.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum or len(set(map(repr, enum))) != len(enum):
            raise SchemaDefinitionError(f"{where}: enum must be a non-empty list of unique values")
        enum = tuple(enum)
    fmt = fdef.get("format")
    if fmt is not None:
        if fmt not in FORMATS:
            raise SchemaDefinitionError(f"{where}: format must be one of {sorted(FORMATS)}")
        if ftype != "string":
            raise SchemaDefinitionError(f"{where}: format requires type string")
    required = fdef.get("required", False)
    indexed = fdef.get("indexed", False)
    if not isinstance(required, bool) or not isinstance(indexed, bool):
        raise SchemaDefinitionError(f"{where}: required/indexed must be booleans")
    if indexed and ftype == "array":
        raise SchemaDefinitionError(f"{where}: array fields cannot be indexed")
    ref = fdef.get("ref")
    if ref is not None and (not isinstance(ref, str) or not NAME_RE.match(ref) or ftype != "string"):
        raise SchemaDefinitionError(f"{where}: ref must name an entity type on a string field")
    labels = fdef.get("labels")
    if labels is not None:
        if enum is None or not isinstance(labels, dict) or set(labels) - set(enum):
            raise SchemaDefinitionError(f"{where}: labels must map enum values to synonym lists")
        if not all(isinstance(v, list) and all(isinstance(s, str) and s for s in v) for v in labels.values()):
            raise SchemaDefinitionError(f"{where}: labels values must be lists of strings")
        labels = {k: tuple(v) for k, v in labels.items()}
    implies = fdef.get("implies")
    if implies is not None:
        if enum is None or not isinstance(implies, dict) or set(implies) - set(enum) or not all(
                isinstance(v, list) and set(v) <= set(enum) for v in implies.values()):
            raise SchemaDefinitionError(f"{where}: implies must map enum values to lists of enum values")
        implies = {k: tuple(v) for k, v in implies.items()}
    identifier = fdef.get("identifier", False)
    if not isinstance(identifier, bool) or (identifier and (ftype != "string" or enum is not None)):
        raise SchemaDefinitionError(f"{where}: identifier must be true/false on a free string field")
    return FieldDef(fname, ftype, description, enum, fmt, required, indexed, items, ref, labels, implies,
                    identifier=identifier)
