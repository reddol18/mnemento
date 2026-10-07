"""Mixed-text ingest (ADR-0017, ADR-0025, issue #3): notes that mix several kinds of facts with rules and prose are
split, classified, extracted with evidence, previewed, and applied as one batch that can be reverted.

1. split    — paragraphs, list items, table rows (with their header), front matter, code blocks; each chunk is a span
              (source name, first/last line, text, heading path)
2. classify — LLM, sees the dictionary and the chunks only: data (which types; new type names allowed) / not_data /
              ambiguous
3. new types — LLM drafts a schema per new type from its chunks (ADR-0021: stored at once as a draft type on apply)
4. extract  — LLM gives every field value with a quote; code keeps a value only if the quote is in the chunk and the
              value comes from the quote (numbers and dates normalized). Anything else is reported, never stored.
5. identity — reference fields are resolved by ADR-0006 (certain matches only); an unknown or uncertain reference
              puts the record on the ambiguous list
6. preview  — nothing written; the same facts from several chunks become one record with several spans; differing
              values are conflicts; facts already in the ledger are reported, not written again
7. apply    — with approved_by + the user's words, exactly the preview, as one ingest; revert retracts its records and
              reverts its series batches
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..errors import MnementoError, SchemaNotFoundError
from ..ledger import Ledger
from ..schema.definition import NAME_RE, SchemaDef
from ..timeutil import format_instant, now as tz_now
from .identity import IdentityResolver
from .llm import LLMAdapter
from .query.interpret import render_dictionary
from .trace import Trace


class IngestError(MnementoError):
    pass


# ---- 1. split ----------------------------------------------------------------------------------------------------

@dataclass
class Chunk:
    span_id: str
    source: str
    start: int  # 1-based line numbers, inclusive
    end: int
    text: str
    context: str = ""  # heading path, and the header row for a table row

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


_LIST = re.compile(r"^\s{0,3}([-*+]|\d{1,3}[.)])\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def span_id(source: str, start: int, end: int, text: str) -> str:
    return "span_" + hashlib.sha1(f"{source}\n{start}\n{end}\n{text}".encode()).hexdigest()[:12]


def split(text: str, source: str) -> list[Chunk]:
    lines = text.lstrip("﻿").splitlines()  # a byte-order mark would hide front matter
    chunks: list[Chunk] = []
    headings: list[str] = []
    cur: list[tuple[int, str]] = []
    table_header = ""
    in_code = False

    def flush(ctx_extra: str = "") -> None:
        nonlocal cur
        if cur and any(t.strip() for _, t in cur):
            body = "\n".join(t for _, t in cur).strip("\n")
            a, b = cur[0][0], cur[-1][0]
            ctx = " > ".join(headings) + (f" | {ctx_extra}" if ctx_extra else "")
            chunks.append(Chunk(span_id(source, a, b, body), source, a, b, body, ctx))
        cur = []

    i = 0
    if lines and lines[0].strip() == "---":  # front matter
        j = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        if j is not None:
            cur = [(k + 1, lines[k]) for k in range(0, j + 1)]
            flush()
            i = j + 1
    while i < len(lines):
        n, line = i + 1, lines[i]
        if line.strip().startswith("```"):
            if not in_code:
                flush()
            cur.append((n, line))
            in_code = not in_code
            if not in_code:
                flush()
            i += 1
            continue
        if in_code:
            cur.append((n, line))
            i += 1
            continue
        h = _HEADING.match(line)
        if h:
            flush()
            level = len(h.group(1))
            headings = headings[:level - 1] + [h.group(2).strip()]
            i += 1
            continue
        if line.lstrip().startswith("|"):
            flush()
            cells = line.strip()
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if re.fullmatch(r"\|?\s*:?-{3,}.*", nxt):  # header row + separator
                table_header = cells
                i += 2
                continue
            cur = [(n, line)]
            flush(f"table header: {table_header}" if table_header else "")
            i += 1
            continue
        table_header = "" if line.strip() else table_header
        if not line.strip():
            flush()
        elif _LIST.match(line):
            flush()
            cur.append((n, line))
        else:
            cur.append((n, line))
        i += 1
    flush()
    return chunks


# ---- 4. evidence check -------------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip().casefold()


def _numbers(s: str) -> list[float]:
    out = []
    for m in re.finditer(r"(?<!\d)[-−]?\d[\d,]*(?:\.\d+)?", unicodedata.normalize("NFKC", s)):
        raw = m.group(0).replace(",", "").replace("−", "-")
        try:
            out.append(float(raw))
        except ValueError:
            pass
    # Korean unit words: 1.2만 -> 12000, 3억 -> 300000000
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(만|억)", s):
        out.append(float(m.group(1)) * (10_000 if m.group(2) == "만" else 100_000_000))
    return out


def value_from_quote(value: Any, quote: str, fd) -> str | None:
    """None when `value` comes from `quote`; otherwise why not."""
    q = _norm(quote)
    if value is None or quote is None:
        return "no value or quote"
    if isinstance(value, bool):
        return None if q else "empty quote"  # a flag is stated by the quoted words; the quote itself is checked
    if isinstance(value, (int, float)):
        return None if any(abs(n - float(value)) < 1e-9 for n in _numbers(quote)) else "number not in the quote"
    if isinstance(value, list):
        bad = [v for v in value if value_from_quote(v, quote, None)]
        return None if not bad else f"items not in the quote: {bad[:3]}"
    s = str(value)
    if fd is not None and fd.format in ("date", "date-time") and re.match(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
        nums = {int(x) for x in re.findall(r"\d+", unicodedata.normalize("NFKC", quote))}
        if m in nums and d in nums and (y in nums or y % 100 in nums or not any(n > 31 for n in nums)):
            return None
        return "date not in the quote"
    if fd is not None and fd.enum and s in fd.enum:
        words = [s, *((fd.labels or {}).get(s, ()))]
        return None if any(_norm(w) in q for w in words) else "neither the value nor its labels are in the quote"
    return None if _norm(s) in q else "text not in the quote"


# ---- LLM output shapes (flat JSON Schemas, no recursion) ---------------------------------------------------------

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"chunks": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"},
                       "kind": {"type": "string", "enum": ["data", "not_data", "ambiguous"]},
                       "types": {"type": "array", "items": {"type": "string"}},
                       "reason": {"type": "string"}},
        "required": ["id", "kind", "types", "reason"], "additionalProperties": False}}},
    "required": ["chunks"], "additionalProperties": False,
}
CLASSIFY_SYSTEM = """You sort chunks of a person's notes for a record book.
For every chunk decide:
- data: it states facts that belong in record types. List every type it has facts for: an existing type from the
  dictionary, or a new snake_case type name when no existing type fits (one chunk may hold facts of several types).
- not_data: rules, instructions, plans without facts, opinions about the notes, metadata, headings.
- ambiguous: you cannot tell.
Use the chunk text and its context only. reason: one short line (it is shown to the user)."""

SCHEMA_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "kind": {"type": "string", "enum": ["entity", "series"]},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "fields": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "type": {"type": "string", "enum": ["string", "integer", "number", "boolean"]},
                           "format": {"type": "string", "enum": ["", "date", "date-time"]},
                           "description": {"type": "string"},
                           "enum": {"type": "array", "items": {"type": "string"}},
                           "labels": {"type": "array", "items": {"type": "string"},
                                      "description": "one 'value=word/word' per enum value"},
                           "ref": {"type": "string", "description": "existing type this field names, or ''"}},
            "required": ["name", "type", "format", "description", "enum", "labels", "ref"],
            "additionalProperties": False}},
        "series_key": {"type": "array", "items": {"type": "string"}},
        "time_field": {"type": "string"},
        "measures": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "kind", "keywords", "fields", "series_key", "time_field", "measures"],
    "additionalProperties": False,
}
SCHEMA_SYSTEM = """You draft a schema for a new record type from example chunks of a person's notes.
- fields: snake_case names; type string/integer/number/boolean; format date (YYYY-MM-DD) or date-time when it is a
  point in time; a short description each; enum with labels only for a small fixed set of words; ref = an existing
  type name when the field names a record of that type (e.g. a company), else ''.
- kind series only for numbers measured again and again per key and day (prices, weight); then give series_key,
  time_field and measures (numeric fields). Otherwise kind entity and empty series fields.
Draft only what the examples show."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": {
        "type": "object",
        "properties": {"chunk": {"type": "string"}, "type": {"type": "string"},
                       "values": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"field": {"type": "string"},
                                          "value": {"type": ["string", "number", "boolean"]},
                                          "quote": {"type": "string"}},
                           "required": ["field", "value", "quote"], "additionalProperties": False}}},
        "required": ["chunk", "type", "values"], "additionalProperties": False}}},
    "required": ["records"], "additionalProperties": False,
}
EXTRACT_SYSTEM = """You extract records from chunks of a person's notes into the given record types.
- One record per fact; a chunk may give several records (e.g. a trade and the decision behind it).
- For every value give `quote`: the exact words of the chunk it comes from (copy them; do not paraphrase).
- Use only what the chunk says. Never fill a field the chunk does not state. Dates as YYYY-MM-DD (the year from the
  chunk or its context; if no year is given anywhere, leave the date out). Numbers without units or separators.
- Enum fields take one of the allowed values (map words with the labels). Reference fields take the name as written.
- Copy a value into each record that has it (shared values are normal)."""


# ---- 2-6. preview ------------------------------------------------------------------------------------------------

def _type_schema(name: str, draft: dict[str, Any]) -> SchemaDef:
    """A SchemaDef for a proposed type, used only to check extracted values (never registered from here)."""
    fields = {}
    for f in draft["fields"]:
        if not NAME_RE.match(f["name"]):
            continue
        d = {"type": f["type"], "description": f["description"] or f["name"]}
        if f.get("format"):
            d["format"] = f["format"]
        if f.get("enum"):
            d["enum"] = list(dict.fromkeys(f["enum"]))
            labels = {}
            for item in f.get("labels") or []:
                if "=" in item:
                    v, words = item.split("=", 1)
                    if v.strip() in d["enum"]:
                        labels[v.strip()] = [w.strip() for w in words.split("/") if w.strip()]
            if labels:
                d["labels"] = labels
        if f.get("ref"):
            d["ref"] = f["ref"]
        fields[f["name"]] = d
    out = {"name": name, "version": 1, "description": draft["description"] or name, "fields": fields or
           {"note": {"type": "string", "description": "text"}}}
    if draft.get("keywords"):
        out["keywords"] = [k for k in draft["keywords"] if k]
    if draft.get("kind") == "series" and draft.get("series_key") and draft.get("time_field") and draft.get("measures"):
        fmt = fields.get(draft["time_field"], {}).get("format")
        out.update({"kind": "series", "series_key": draft["series_key"], "time_field": draft["time_field"],
                    "granularity": "day" if fmt == "date" else "instant", "measures": draft["measures"]})
    return SchemaDef.from_dict(out)


def _identity_fields(s: SchemaDef) -> list[str]:
    if s.kind == "series":
        return [*s.series_key, s.time_field]
    keys = [f for f, d in s.fields.items() if d.identifier or d.ref]
    keys += [f for f in ("name", "title") if f in s.fields]
    if s.default_date_field:
        keys.append(s.default_date_field)
    return keys


@dataclass
class Preview:
    id: str
    created_at: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    new_types: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> schema definition (dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    rejected_values: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    already_recorded: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["summary"] = {
            "chunks": len(self.chunks),
            "by_kind": {k: sum(1 for c in self.chunks if c["kind"] == k) for k in ("data", "not_data", "ambiguous")},
            "records": {t: sum(1 for r in self.records if r["type"] == t) for t in sorted({r["type"] for r in
                                                                                           self.records})},
            "new_types": sorted(self.new_types), "ambiguous": len(self.ambiguous),
            "conflicts": len(self.conflicts), "rejected_values": len(self.rejected_values),
            "already_recorded": len(self.already_recorded),
        }
        return d


class Ingestor:
    def __init__(self, ledger: Ledger, llm: LLMAdapter | None, batch_size: int = 15):
        self.ledger = ledger
        self.llm = llm
        self.batch_size = batch_size

    # -- preview --
    def preview(self, sources: list[tuple[str, str]], now: datetime | None = None) -> dict[str, Any]:
        """sources: (name, text). Nothing is written except the preview itself (so apply can take exactly it)."""
        if self.llm is None:
            raise IngestError("ingest needs an LLM to classify and extract")
        now = now or tz_now(self.ledger.tz)
        trace = Trace(path="llm")
        chunks = [c for name, text in sources for c in split(text, name)]
        pv = Preview("ingest_" + uuid.uuid4().hex[:12], format_instant(now))
        if not chunks:
            return self._store(pv, trace)
        schemas = {n: self.ledger.schemas.get(n) for n in self.ledger.schemas.names()}
        cls = self._classify(chunks, schemas, trace)
        by_id = {c.span_id: c for c in chunks}
        new_names = sorted({t for c in cls.values() if c["kind"] == "data" for t in c["types"] if t not in schemas})
        for c in chunks:
            k = cls.get(c.span_id, {"kind": "ambiguous", "types": [], "reason": "not classified"})
            pv.chunks.append({**c.to_dict(), "kind": k["kind"], "types": k["types"], "reason": k["reason"]})
            if k["kind"] == "not_data":
                pv.notes.append({"source": c.source, "start": c.start, "end": c.end, "summary": k["reason"]})
            elif k["kind"] == "ambiguous":
                pv.ambiguous.append({"what": "classification", "span": c.span_id, "source": c.source,
                                     "lines": [c.start, c.end], "reason": k["reason"]})
        type_defs: dict[str, SchemaDef] = dict(schemas)
        self._new_types = set()
        for name in new_names:
            if not NAME_RE.match(name):
                continue
            examples = [by_id[s] for s, k in cls.items() if name in k["types"]][:8]
            draft = self._draft_schema(name, examples, schemas, trace)
            try:
                sd = _type_schema(name, draft)
            except Exception as exc:  # noqa: BLE001 — an unusable draft makes its chunks ambiguous
                pv.ambiguous.append({"what": "new type", "type": name, "reason": f"schema draft unusable: {exc}"})
                continue
            pv.new_types[name] = sd.to_dict()
            type_defs[name] = sd
            self._new_types.add(name)
        data = [c for c in chunks if cls.get(c.span_id, {}).get("kind") == "data"
                and any(t in type_defs for t in cls[c.span_id]["types"])]
        raw = self._extract(data, cls, type_defs, trace)
        self._check_and_merge(pv, raw, by_id, type_defs)
        pv.llm = trace.finish().to_dict().get("totals", {})
        return self._store(pv, trace)

    def _store(self, pv: Preview, trace: Trace) -> dict[str, Any]:
        d = pv.to_dict()
        self.ledger.storage.execute(
            "INSERT INTO ingest_previews (id, created_at, content) VALUES (?, ?, ?)",
            (pv.id, pv.created_at, json.dumps(d, ensure_ascii=False)))
        return d

    def _classify(self, chunks: list[Chunk], schemas: dict[str, SchemaDef], trace: Trace) -> dict[str, dict]:
        dictionary = render_dictionary(list(schemas.values()), {}) if schemas else "(no record types yet)"
        out: dict[str, dict] = {}
        for i in range(0, len(chunks), self.batch_size):
            part = chunks[i:i + self.batch_size]
            prompt = (f"Dictionary:\n{dictionary}\n\nChunks:\n"
                      + "\n".join(json.dumps({"id": c.span_id, "context": c.context, "text": c.text},
                                             ensure_ascii=False) for c in part))
            r = self.llm.complete_json(system=CLASSIFY_SYSTEM, prompt=prompt, schema=CLASSIFY_SCHEMA, stage="classify")
            trace.add_llm(r.usage)
            for c in r.data.get("chunks", []):
                if c.get("id") in {p.span_id for p in part}:
                    out[c["id"]] = {"kind": c["kind"], "types": [t for t in c.get("types", []) if t],
                                    "reason": c.get("reason", "")}
        return out

    def _draft_schema(self, name: str, examples: list[Chunk], schemas: dict[str, SchemaDef],
                      trace: Trace) -> dict[str, Any]:
        prompt = (f"Existing types: {', '.join(schemas) or '(none)'}\nNew type: {name}\nExample chunks:\n"
                  + "\n".join(json.dumps({"context": c.context, "text": c.text}, ensure_ascii=False)
                              for c in examples))
        r = self.llm.complete_json(system=SCHEMA_SYSTEM, prompt=prompt, schema=SCHEMA_SCHEMA, stage="draft_schema")
        trace.add_llm(r.usage)
        return r.data

    def _extract(self, chunks: list[Chunk], cls: dict[str, dict], type_defs: dict[str, SchemaDef],
                 trace: Trace) -> list[dict[str, Any]]:
        out = []
        for i in range(0, len(chunks), self.batch_size):
            part = chunks[i:i + self.batch_size]
            types = sorted({t for c in part for t in cls[c.span_id]["types"] if t in type_defs})
            dictionary = render_dictionary([type_defs[t] for t in types], {})
            prompt = (f"Record types:\n{dictionary}\n\nChunks:\n"
                      + "\n".join(json.dumps({"id": c.span_id, "types": cls[c.span_id]["types"], "context": c.context,
                                              "text": c.text}, ensure_ascii=False) for c in part))
            r = self.llm.complete_json(system=EXTRACT_SYSTEM, prompt=prompt, schema=EXTRACT_SCHEMA, stage="extract")
            trace.add_llm(r.usage)
            out += r.data.get("records", [])
        return out

    def _check_and_merge(self, pv: Preview, raw: list[dict], by_id: dict[str, Chunk],
                         type_defs: dict[str, SchemaDef]) -> None:
        facts: list[dict[str, Any]] = []
        for rec in raw:
            c, s = by_id.get(rec.get("chunk")), type_defs.get(rec.get("type"))
            if c is None or s is None:
                pv.ambiguous.append({"what": "record", "reason": f"unknown chunk or type {rec.get('type')!r}"})
                continue
            doc, quotes = {}, {}
            for v in rec.get("values", []):
                fname, value, quote = v.get("field"), v.get("value"), v.get("quote") or ""
                fd = s.fields.get(fname)
                why = None
                if fd is None or not NAME_RE.match(fname or ""):
                    why = "unknown field" if s.fields else None  # a draft type takes new field names
                    if not NAME_RE.match(fname or ""):
                        why = "invalid field name"
                if why is None and _norm(quote) not in _norm(c.text + "\n" + c.context):
                    why = "quote not in the chunk"
                if why is None:
                    why = value_from_quote(value, quote, fd)
                if why is None and fd is not None and fd.type in ("integer", "number") and isinstance(value, str):
                    nums = _numbers(value)
                    value = nums[0] if nums else value
                    if fd.type == "integer" and isinstance(value, float) and value.is_integer():
                        value = int(value)
                if why:
                    pv.rejected_values.append({"span": c.span_id, "type": s.name, "field": fname, "value": value,
                                               "quote": quote[:120], "why": why})
                    continue
                doc[fname] = value
                quotes[fname] = quote
            if not doc:
                continue
            facts.append({"type": s.name, "doc": doc, "quotes": quotes, "spans": [c.span_id],
                          "source": [c.source, c.start, c.end]})
        # references (ADR-0006: certain matches only)
        kept = []
        for f in facts:
            s = type_defs[f["type"]]
            ok = True
            for fname, fd in s.fields.items():
                v = f["doc"].get(fname)
                if not (fd.ref and isinstance(v, str)):
                    continue
                e = self.ledger.get_entity(v)
                if e is not None and e.type == fd.ref:
                    continue
                if fd.ref not in self.ledger.schemas.names():
                    ok = False
                    pv.ambiguous.append({"what": "reference", "type": f["type"], "field": fname, "value": v,
                                         "span": f["spans"][0], "reason": f"no {fd.ref} records yet"})
                    break
                res = IdentityResolver(self.ledger, fd.ref).resolve(v)
                if res.status == "match":
                    f["doc"][fname] = res.matches[0]
                else:
                    ok = False
                    pv.ambiguous.append({"what": "reference", "type": f["type"], "field": fname, "value": v,
                                         "span": f["spans"][0], "reason": f"{fd.ref} '{v}' is {res.status}",
                                         "candidates": [c["id"] for c in (res.candidates or [])[:5]]})
                    break
            missing = [n for n, fd in s.fields.items() if fd.required and f["doc"].get(n) is None]
            if ok and missing and f["type"] not in self._new_types:
                ok = False
                pv.ambiguous.append({"what": "required", "type": f["type"], "span": f["spans"][0],
                                     "reason": f"the chunk does not state required field(s) {missing}"})
            if ok:
                kept.append(f)
        # N chunks -> 1 record; differing values are conflicts (never picked automatically)
        groups: dict[tuple, list[dict]] = {}
        loose: list[dict] = []
        for f in kept:
            keys = _identity_fields(type_defs[f["type"]])
            key = tuple(f["doc"].get(k) for k in keys)
            if keys and all(v is not None for v in key):
                groups.setdefault((f["type"], key), []).append(f)
            else:
                loose.append(f)
        merged: list[dict] = []
        for (t, key), fs in groups.items():
            doc: dict[str, Any] = {}
            clash: dict[str, list] = {}
            for f in fs:
                for k, v in f["doc"].items():
                    if k in doc and doc[k] != v:
                        clash.setdefault(k, [doc[k]]).append(v)
                    doc.setdefault(k, v)
            spans = [s for f in fs for s in f["spans"]]
            if clash:
                pv.conflicts.append({"type": t, "identity": dict(zip(_identity_fields(type_defs[t]), key)),
                                     "fields": {k: list(dict.fromkeys(map(json.dumps, v))) for k, v in clash.items()},
                                     "spans": spans})
                continue
            merged.append({"type": t, "doc": doc, "quotes": {k: v for f in fs for k, v in f["quotes"].items()},
                           "spans": list(dict.fromkeys(spans)), "source": fs[0]["source"]})
        merged += loose
        # already in the ledger: same identity -> same values: report; different: conflict
        for m in merged:
            s = type_defs[m["type"]]
            keys = _identity_fields(s)
            if s.kind == "entity" and keys and m["type"] in self.ledger.schemas.names() and all(
                    m["doc"].get(k) is not None for k in keys):
                try:
                    found = self.ledger.find(m["type"], {k: m["doc"][k] for k in keys})
                except Exception:  # noqa: BLE001 — unindexed/unknown field: treat as not found
                    found = []
                if found:
                    e = found[0]
                    diff = {k: [e.doc.get(k), v] for k, v in m["doc"].items() if k in e.doc and e.doc[k] != v}
                    if diff:
                        pv.conflicts.append({"type": m["type"], "existing": e.id, "fields": diff, "spans": m["spans"]})
                    else:
                        pv.already_recorded.append({"type": m["type"], "existing": e.id, "spans": m["spans"]})
                    continue
            m["rid"] = f"r{len(pv.records) + 1}"
            pv.records.append(m)
        # siblings: records from the same chunk
        by_span: dict[str, list[str]] = {}
        for r in pv.records:
            for s in r["spans"]:
                by_span.setdefault(s, []).append(r["rid"])
        for r in pv.records:
            r["siblings"] = sorted({o for s in r["spans"] for o in by_span[s] if o != r["rid"]})

    # -- 7. apply / revert --
    def apply(self, preview_id: str, *, approved_by: str, user_answer: str, by: str = "ingest",
              now: datetime | None = None) -> dict[str, Any]:
        from ..series import ingest as series_ingest
        from .newtype import create_draft_type

        if not (approved_by or "").strip() or not (user_answer or "").strip():
            raise IngestError("apply needs approved_by and the user's answer (explicit consent)")
        row = self.ledger.storage.fetch_all("SELECT content, applied_as FROM ingest_previews WHERE id = ?",
                                            [preview_id])
        if not row:
            raise IngestError(f"no such preview: {preview_id}")
        if row[0]["applied_as"]:
            raise IngestError(f"{preview_id} was already applied as {row[0]['applied_as']}")
        pv = json.loads(row[0]["content"])
        now = now or tz_now(self.ledger.tz)
        at = format_instant(now)
        batch = "ingest_" + uuid.uuid4().hex[:12]
        created_types, entity_ids, series_batches = [], [], []
        for name, d in pv["new_types"].items():
            if name in self.ledger.schemas.names():
                continue
            if d.get("kind") == "series":
                self.ledger.schemas.register(d)  # the user approved this preview, which shows the series schema
            else:
                proposal = {k: v for k, v in d.items() if k not in ("name", "version")}
                create_draft_type(self.ledger, name, {}, proposal)  # ADR-0021: registered later with approval
            created_types.append(name)
        points: dict[str, list[dict]] = {}
        for r in pv["records"]:
            s = self.ledger.schemas.get(r["type"])
            if s.kind == "series":
                points.setdefault(r["type"], []).append(r["doc"])
                continue
            eid = f"{r['type']}_{uuid.uuid4().hex[:10]}"
            src, a, b = r["source"]
            quotes = "; ".join(f"{k}: {v[:60]}" for k, v in list(r["quotes"].items())[:6])
            evidence = f"ingest:{batch} spans:{','.join(r['spans'])} {src}:{a}-{b} — {quotes}"
            self.ledger.record_event(eid, "created", r["doc"], at, by, evidence, entity_type=r["type"],
                                     at_precision="unknown")
            entity_ids.append(eid)
            r["entity_id"] = eid
        for t, rows in points.items():
            rep = series_ingest(self.ledger, t, rows, by=by, source=f"ingest:{batch}")
            if rep.batch_id is None:
                raise IngestError(f"series rows rejected: {rep.rejected[:3]}")
            series_batches.append(rep.batch_id)
        with self.ledger.storage.transaction():
            for r in pv["records"]:
                if r.get("entity_id"):
                    for s in r["spans"]:
                        self.ledger.storage.execute(
                            "INSERT INTO ingest_spans (span_id, entity_id, batch_id) VALUES (?, ?, ?)",
                            (s, r["entity_id"], batch))
            self.ledger.storage.execute(
                "INSERT INTO text_ingests (id, preview_id, at, by, approved_by, user_answer, entity_ids, "
                "series_batches, new_types, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch, preview_id, at, by, approved_by, user_answer, json.dumps(entity_ids),
                 json.dumps(series_batches), json.dumps(created_types), json.dumps(pv["notes"], ensure_ascii=False)))
            self.ledger.storage.execute("UPDATE ingest_previews SET applied_as = ? WHERE id = ?", (batch, preview_id))
        return {"batch_id": batch, "records": len(entity_ids), "series_batches": series_batches,
                "new_types": created_types, "notes_kept": len(pv["notes"])}

    def revert(self, batch_id: str, *, by: str = "ingest", now: datetime | None = None) -> dict[str, Any]:
        from ..series import revert_batch

        row = self.ledger.storage.fetch_all("SELECT * FROM text_ingests WHERE id = ?", [batch_id])
        if not row:
            raise IngestError(f"no such ingest: {batch_id}")
        if row[0]["reverted_at"]:
            raise IngestError(f"{batch_id} was already reverted at {row[0]['reverted_at']}")
        at = format_instant(now or tz_now(self.ledger.tz))
        retracted = 0
        for eid in json.loads(row[0]["entity_ids"]):
            e = self.ledger.get_entity(eid)
            if e is not None and not e.retracted:
                self.ledger.record_event(eid, "retracted", {}, at, by, f"ingest {batch_id} reverted")
                retracted += 1
        for b in reversed(json.loads(row[0]["series_batches"])):
            revert_batch(self.ledger, b)
        self.ledger.storage.execute("UPDATE text_ingests SET reverted_at = ? WHERE id = ?", (at, batch_id))
        return {"batch_id": batch_id, "retracted_records": retracted,
                "reverted_series_batches": len(json.loads(row[0]["series_batches"]))}


def siblings(ledger: Ledger, entity_id: str) -> list[str]:
    """Records extracted from the same chunk(s) as this one (ADR-0017)."""
    rows = ledger.storage.fetch_all(
        "SELECT DISTINCT b.entity_id AS id FROM ingest_spans a JOIN ingest_spans b ON a.span_id = b.span_id "
        "WHERE a.entity_id = ? AND b.entity_id != ? ORDER BY b.entity_id", [entity_id, entity_id])
    return [r["id"] for r in rows]
