"""New record types over MCP (ADR-0021, issue #6): the first record of a new kind is stored at once under a *draft
type*, together with the schema the agent suggests for it; the user approves that schema later.

- A draft type has no fields and enforces nothing: every field is an ADR-0014 draft, stored and queryable as given.
- Its `proposal` is the agent's schema (field descriptions, enums with labels, required, indexed), or one inferred from
  the stored records when the agent sent none. It is shown as a pending proposal with an id.
- Approval (`apply_schema_proposal` with approved_by + the user's words) registers the type; required fields, enums
  and indexes apply from then on. Stored records must fit, or the approval is refused and says which do not.
- A new type name that looks like an existing type is asked about first (nothing stored), as ADR-0014 asks about
  field names — the same kind of record under two names would split every count.
"""

from __future__ import annotations

import copy
from typing import Any

from ..errors import SchemaDefinitionError
from ..ledger import Ledger
from ..schema.definition import NAME_RE, SchemaDef
from ..timeutil import format_instant, now as tz_now
from .drafts import draft_dictionary, similar_names

PROPOSAL_PREFIX = "type_"


def similar_types(ledger: Ledger, name: str) -> list[str]:
    """Existing types whose name (or a keyword) looks like `name`."""
    out = []
    for other in ledger.schemas.names():
        s = ledger.schemas.get(other)
        if similar_names(name, [other]) or any(similar_names(name, [k]) for k in s.keywords if NAME_RE.match(k)):
            out.append(other)
    return out


def check_schema_draft(name: str, schema_draft: dict[str, Any]) -> list[str]:
    """Problems that would stop the suggested schema from ever being registered (checked when it is sent, so the agent
    can fix it while it still has the context). Missing descriptions and labels may still be added at approval."""
    if not isinstance(schema_draft, dict):
        return ["schema_draft must be an object"]
    if schema_draft.get("kind", "entity") != "entity":
        return ["record creates entity types only; a series type is registered with the user first (ADR-0016)"]
    if schema_draft.get("name", name) != name:
        return [f"schema_draft.name {schema_draft['name']!r} differs from entity_type {name!r}"]
    candidate = _final_definition(name, 1, schema_draft, {}, {}, [], fill_todo=True)
    try:
        SchemaDef.from_dict(candidate)
    except SchemaDefinitionError as exc:
        return [str(exc)]
    return []


def create_draft_type(ledger: Ledger, name: str, payload: dict[str, Any],
                      schema_draft: dict[str, Any] | None) -> SchemaDef:
    from .proposals import draft_schema

    proposal = copy.deepcopy(schema_draft) if schema_draft else draft_schema(name, [payload])["draft"]
    proposal.pop("name", None)
    proposal.pop("version", None)
    return ledger.schemas.register(_draft_definition(name, 1, proposal))


def update_proposal(ledger: Ledger, schema: SchemaDef, schema_draft: dict[str, Any]) -> SchemaDef:
    proposal = {k: v for k, v in copy.deepcopy(schema_draft).items() if k not in ("name", "version")}
    if proposal == schema.proposal:
        return schema
    return ledger.schemas.register(_draft_definition(schema.name, schema.version + 1, proposal))


def _draft_definition(name: str, version: int, proposal: dict[str, Any]) -> dict[str, Any]:
    desc = proposal.get("description") or ""
    d = {"name": name, "version": version, "status": "draft", "proposal": proposal,
         "description": (desc if desc and not desc.startswith("TODO") else
                         f"New record type '{name}', not approved yet; its fields are as stored.")}
    keywords = [k for k in proposal.get("keywords", []) if isinstance(k, str) and k]
    if keywords:
        d["keywords"] = keywords
    return d


def proposal_id(schema: SchemaDef) -> str:
    return f"{PROPOSAL_PREFIX}{schema.name}_v{schema.version}"


def type_proposal(ledger: Ledger, schema: SchemaDef) -> dict[str, Any]:
    """The pending registration of a draft type, as propose_schema / record show it."""
    fields = _proposed_fields(ledger, schema)
    todo = [f for f, d in fields.items() if _todo(d.get("description"))]
    no_label = [f"{f}={v}" for f, d in fields.items() for v in d.get("enum") or [] if not (d.get("labels") or {}).get(v)]
    parts = [f"{f} ({d['type']}{', required' if d.get('required') else ''}"
             f"{', one of ' + '/'.join(map(str, d['enum'])) if d.get('enum') else ''})" for f, d in fields.items()]
    need = []
    if todo:
        need.append(f"descriptions for {todo}")
    if no_label:
        need.append(f"labels for {no_label}")
    return {
        "id": proposal_id(schema),
        "kind": "register_type",
        "entity_type": schema.name,
        "from_version": schema.version,
        "to_version": schema.version + 1,
        "description": schema.proposal.get("description") if schema.proposal else None,
        "fields": fields,
        "missing": need,
        "question": f"Register the new record type '{schema.name}' with fields " + "; ".join(parts) + "?"
                    + (f" Still needed: {'; '.join(need)}." if need else ""),
        "status": "proposed — the records are already stored and queryable; apply only after the user explicitly "
                  "agrees (apply_schema_proposal)",
    }


def _todo(desc: Any) -> bool:
    return not isinstance(desc, str) or not desc.strip() or desc.strip().startswith("TODO")


def _proposed_fields(ledger: Ledger, schema: SchemaDef) -> dict[str, dict[str, Any]]:
    """The proposal's fields plus fields the stored records use that it lacks (typed by what was stored); enum values
    the records use outside a proposed enum are added to it."""
    from .proposals import infer_field

    fields = copy.deepcopy((schema.proposal or {}).get("fields") or {})
    stored = draft_dictionary(ledger, schema.name, schema)["fields"]
    for name, d in stored.items():
        if name in fields or not d["queryable"]:
            continue
        typed = infer_field(d["samples"]) if len(d["types"]) == 1 else {"type": "string"}
        fields[name] = {k: v for k, v in typed.items() if k in ("type", "format", "items")}
        fields[name]["description"] = "TODO: describe this field."
    for name, fd in fields.items():
        if fd.get("enum"):
            seen = ledger.storage.fetch_all(
                "SELECT DISTINCT json_extract(doc, ?) AS v FROM entities WHERE type = ? AND retracted = 0 "
                "AND json_extract(doc, ?) IS NOT NULL", [f"$.{name}", schema.name, f"$.{name}"])
            fd["enum"] = [*fd["enum"], *[r["v"] for r in seen if r["v"] not in fd["enum"]]]
        fd.pop("enum_candidates", None)
    return fields


def _final_definition(name: str, version: int, proposal: dict[str, Any], descriptions: dict[str, str],
                      labels: dict[str, dict[str, list[str]]], index: list[str], *, fill_todo: bool = False,
                      fields: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {k: copy.deepcopy(v) for k, v in proposal.items() if k not in ("name", "version", "status", "proposal",
                                                                         "fields", "kind")}
    out.update({"name": name, "version": version})
    if fill_todo and _todo(out.get("description")):
        out["description"] = "(to be described)"
    flds = copy.deepcopy(fields if fields is not None else proposal.get("fields") or {})
    for fname, fd in flds.items():
        if not isinstance(fd, dict):
            continue
        fd.pop("enum_candidates", None)
        fd.pop("note", None)
        if descriptions.get(fname):
            fd["description"] = descriptions[fname]
        elif fill_todo and _todo(fd.get("description")):
            fd["description"] = "(to be described)"
        for v, syns in (labels.get(fname) or {}).items():
            fd.setdefault("labels", {})[v] = list(syns)
        if fname in index:
            fd["indexed"] = True
    out["fields"] = flds
    return out


def apply_type_proposal(ledger: Ledger, pid: str, *, approved_by: str, user_answer: str,
                        descriptions: dict[str, str] | None = None, labels: dict[str, dict[str, list[str]]] | None = None,
                        index: list[str] | None = None, description: str | None = None,
                        now: str | None = None) -> dict[str, Any]:
    """Register a draft type with the user's explicit consent. Refused while a field lacks a description or an enum
    value lacks a label, or when stored records do not fit the schema (they are listed)."""
    if not (approved_by or "").strip() or not (user_answer or "").strip():
        raise SchemaDefinitionError("apply needs approved_by and the user's answer (explicit consent)")
    name = next((n for n in ledger.schemas.names() if ledger.schemas.get(n).is_draft
                 and proposal_id(ledger.schemas.get(n)) == pid), None)
    if name is None:
        raise SchemaDefinitionError("unknown or outdated proposal id (the draft type changed?) — call propose_schema "
                                    "again")
    schema = ledger.schemas.get(name)
    descriptions, labels, index = descriptions or {}, labels or {}, index or []
    fields = _proposed_fields(ledger, schema)
    proposal = dict(schema.proposal or {})
    if description:
        proposal["description"] = description
    final = _final_definition(name, schema.version + 1, proposal, descriptions, labels, index, fields=fields)
    missing_desc = [f for f, d in final["fields"].items() if _todo(d.get("description"))]
    missing_labels = [f"{f}={v}" for f, d in final["fields"].items() for v in d.get("enum") or []
                      if not (d.get("labels") or {}).get(v)]
    if _todo(final.get("description")) or missing_desc or missing_labels:
        raise SchemaDefinitionError(
            f"still needed: {'a type description; ' if _todo(final.get('description')) else ''}"
            f"descriptions {missing_desc}, labels {missing_labels}")
    new = SchemaDef.from_dict(final)
    wrong = {e.id: new.validation_errors(e.doc, lenient=True) for e in ledger.find(name)}
    wrong = {k: v for k, v in wrong.items() if v}
    if wrong:
        raise SchemaDefinitionError("stored records do not fit the proposed schema (fix them or relax the schema): "
                                    + "; ".join(f"{k}: {', '.join(v)}" for k, v in list(wrong.items())[:10]))
    at = now or format_instant(tz_now(ledger.tz))
    applied = ledger.schemas.register(new)
    with ledger.storage.transaction():
        ledger.storage.record_schema_change({
            "entity_type": name, "version": applied.version, "proposal_id": pid,
            "approved_by": approved_by, "user_answer": user_answer, "applied_at": at,
            "summary": {"registered_type": name, "registered_fields": sorted(new.fields),
                        "required": [f for f, d in new.fields.items() if d.required], "indexed": new.indexed_fields},
        })
    return {"entity_type": name, "version": applied.version, "registered_type": True,
            "registered_fields": sorted(new.fields), "indexed": new.indexed_fields}
