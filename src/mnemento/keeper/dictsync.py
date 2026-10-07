"""Repository dictionary improvements into a database the user organized (ADR-0023, issue #12).

Once the user organized a type (ADR-0014 approvals), the database is that type's dictionary and a differing schema
file is not applied at start (ADR-0018). The difference is offered instead, item by item, as a proposal:

- additive items are proposed for application by default: a new optional field, a new enum value with its labels,
  more labels or keywords, a vague word or relation note the database does not have, an index, an identifier mark;
- replacing items are applied only when the user picks them: a different field or type description, a different
  reading of a vague word, a different default date field;
- breaking differences (type changes, new required fields) are listed as not applicable.

Nothing is applied without approved_by and the user's words; items the user declined are not offered again for the
same file content.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..errors import SchemaDefinitionError
from ..ledger import Ledger
from ..schema.definition import SchemaDef
from ..timeutil import format_instant, now as tz_now

PREFIX = "dict_"


def _fp(v: Any) -> str:
    return hashlib.sha1(json.dumps(v, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


def diff_items(db: SchemaDef, file_def: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(items, not_applicable). Each item: key, default (applied unless excluded), what, and the change itself."""
    f = SchemaDef.from_dict({**file_def, "version": db.version})  # parse only; version is irrelevant here
    items: list[dict[str, Any]] = []
    na: list[str] = []

    def add(key: str, default: bool, what: str, change: dict[str, Any]) -> None:
        items.append({"key": key, "default": default, "what": what, "change": change, "fp": _fp(change)})

    if f.description != db.description:
        add("type_description", False, f"type description -> {f.description!r}", {"description": f.description})
    for kw in [k for k in f.keywords if k not in db.keywords]:
        add(f"keyword:{kw}", True, f"keyword '{kw}'", {"keyword": kw})
    dbv = dict(db.vague_terms)
    for w, r in f.vague_terms:
        if w not in dbv:
            add(f"vague:{w}", True, f"default reading of '{w}': {r}", {"vague": [w, r]})
        elif dbv[w] != r:
            add(f"vague:{w}", False, f"default reading of '{w}' -> {r} (now: {dbv[w]})", {"vague": [w, r]})
    dbrel = {t for t, _ in db.relations}
    for t, note in f.relations:
        if t not in dbrel:
            add(f"relation:{t}", True, f"relation to {t}: {note}", {"relation": [t, note]})
    if f.default_date_field and f.default_date_field != db.default_date_field and f.default_date_field in db.fields:
        add("default_date_field", db.default_date_field is None,
            f"bare dates refer to {f.default_date_field}", {"default_date_field": f.default_date_field})
    for name, ff in f.fields.items():
        dbf = db.fields.get(name)
        if dbf is None:
            if ff.required:
                na.append(f"new field {name} is required (a breaking change)")
            else:
                add(f"field:{name}", True, f"new field {name} ({ff.type}): {ff.description}",
                    {"field": [name, ff.to_dict()]})
            continue
        if (ff.type, ff.items, ff.format) != (dbf.type, dbf.items, dbf.format):
            na.append(f"{name}: type/format differs ({dbf.type} -> {ff.type}); not applied")
            continue
        if ff.required and not dbf.required:
            na.append(f"{name}: becomes required in the file; not applied")
        if ff.description != dbf.description:
            add(f"description:{name}", False, f"{name} description -> {ff.description!r}",
                {"field_description": [name, ff.description]})
        if ff.indexed and not dbf.indexed:
            add(f"index:{name}", True, f"index {name}", {"index": name})
        if ff.identifier and not dbf.identifier and dbf.type == "string" and not dbf.enum:
            add(f"identifier:{name}", True, f"{name} is an external identifier", {"identifier": name})
        if ff.enum is not None and dbf.enum is not None:
            for v in [v for v in ff.enum if v not in dbf.enum]:
                syns = list((ff.labels or {}).get(v, ()))
                if syns:
                    add(f"value:{name}={v}", True, f"{name} value '{v}' ({'/'.join(syns)})",
                        {"value": [name, v, syns]})
                else:
                    na.append(f"{name} value '{v}' has no labels in the file; register it with propose_schema")
            for v, syns in (ff.labels or {}).items():
                new = [s for s in syns if s not in (dbf.labels or {}).get(v, ())]
                if v in dbf.enum and new:
                    add(f"labels:{name}={v}", True, f"{name} '{v}' also called {'/'.join(new)}",
                        {"labels": [name, v, new]})
            for v, earlier in (ff.implies or {}).items():
                if v in dbf.enum and set(earlier) <= set(dbf.enum) and tuple(earlier) != (dbf.implies or {}).get(v):
                    add(f"implies:{name}={v}", (dbf.implies or {}).get(v) is None,
                        f"{name} '{v}' implies {', '.join(earlier)}", {"implies": [name, v, list(earlier)]})
    return items, na


def _declined(ledger: Ledger, name: str) -> set[str]:
    out: set[str] = set()
    for ch in ledger.storage.schema_changes(name):
        out |= set((ch.get("summary") or {}).get("declined", []))
    return out


def proposal(ledger: Ledger, name: str, file_def: dict[str, Any]) -> dict[str, Any] | None:
    db = ledger.schemas.get(name)
    items, na = diff_items(db, file_def)
    declined = _declined(ledger, name)
    items = [i for i in items if f"{i['key']}#{i['fp']}" not in declined]
    if not items:
        return None
    pid = f"{PREFIX}{name}_v{db.version}_{_fp([i['key'] + i['fp'] for i in items])}"
    by_default = [i["what"] for i in items if i["default"]]
    optional = [f"{i['key']}: {i['what']}" for i in items if not i["default"]]
    q = f"The repository's dictionary for {name} has improvements your database does not."
    if by_default:
        q += " Add: " + "; ".join(by_default) + "."
    if optional:
        q += " Also change (only if you pick them, by key): " + "; ".join(optional) + "."
    return {"id": pid, "kind": "update_dictionary", "entity_type": name, "from_version": db.version,
            "to_version": db.version + 1, "items": [{k: i[k] for k in ("key", "default", "what")} for i in items],
            "not_applicable": na, "question": q,
            "status": "proposed — apply only after the user explicitly agrees (apply_schema_proposal; `items` "
                      "picks keys, default: the additive ones)"}


def pending(ledger: Ledger) -> list[dict[str, Any]]:
    """Proposals for every organized type whose repository file differs (files remembered by load_dir)."""
    out = []
    for name, d in sorted(getattr(ledger.schemas, "skipped_files", {}).items()):
        p = proposal(ledger, name, d)
        if p:
            out.append(p)
    return out


def apply(ledger: Ledger, pid: str, *, approved_by: str, user_answer: str, items: list[str] | None = None,
          now: str | None = None) -> dict[str, Any]:
    if not (approved_by or "").strip() or not (user_answer or "").strip():
        raise SchemaDefinitionError("apply needs approved_by and the user's answer (explicit consent)")
    files = getattr(ledger.schemas, "skipped_files", {})
    name = next((n for n in files if (p := proposal(ledger, n, files[n])) and p["id"] == pid), None)
    if name is None:
        raise SchemaDefinitionError("unknown or outdated proposal id — call propose_schema again")
    db = ledger.schemas.get(name)
    all_items, _ = diff_items(db, files[name])
    declined_before = _declined(ledger, name)
    all_items = [i for i in all_items if f"{i['key']}#{i['fp']}" not in declined_before]
    keys = {i["key"] for i in all_items}
    unknown = [k for k in items or [] if k not in keys]
    if unknown:
        raise SchemaDefinitionError(f"unknown item keys {unknown}; this proposal has {sorted(keys)}")
    chosen = [i for i in all_items if (i["key"] in items if items is not None else i["default"])]
    new = db.to_dict()
    new["version"] = db.version + 1
    for i in chosen:
        _apply_change(new, i["change"])
    applied = ledger.schemas.register(SchemaDef.from_dict(new))
    declined = [f"{i['key']}#{i['fp']}" for i in all_items if i not in chosen]
    at = now or format_instant(tz_now(ledger.tz))
    with ledger.storage.transaction():
        ledger.storage.record_schema_change({
            "entity_type": name, "version": applied.version, "proposal_id": pid, "approved_by": approved_by,
            "user_answer": user_answer, "applied_at": at,
            "summary": {"dictionary_update": [i["key"] for i in chosen], "declined": declined}})
    return {"entity_type": name, "version": applied.version, "applied": [i["key"] for i in chosen],
            "declined": [d.split("#")[0] for d in declined]}


def _apply_change(d: dict[str, Any], c: dict[str, Any]) -> None:
    fields = d["fields"]
    if "description" in c:
        d["description"] = c["description"]
    elif "keyword" in c:
        d.setdefault("keywords", []).append(c["keyword"])
    elif "vague" in c:
        d.setdefault("vague_terms", {})[c["vague"][0]] = c["vague"][1]
    elif "relation" in c:
        d.setdefault("relations", []).append({"type": c["relation"][0], "note": c["relation"][1]})
    elif "default_date_field" in c:
        d["default_date_field"] = c["default_date_field"]
    elif "field" in c:
        fields[c["field"][0]] = copy.deepcopy(c["field"][1])
    elif "field_description" in c:
        fields[c["field_description"][0]]["description"] = c["field_description"][1]
    elif "index" in c:
        fields[c["index"]]["indexed"] = True
    elif "identifier" in c:
        fields[c["identifier"]]["identifier"] = True
    elif "value" in c:
        f, v, syns = c["value"]
        fields[f]["enum"].append(v)
        fields[f].setdefault("labels", {})[v] = list(syns)
    elif "labels" in c:
        f, v, syns = c["labels"]
        fields[f].setdefault("labels", {}).setdefault(v, [])
        fields[f]["labels"][v] += [s for s in syns if s not in fields[f]["labels"][v]]
    elif "implies" in c:
        f, v, earlier = c["implies"]
        fields[f].setdefault("implies", {})[v] = list(earlier)
