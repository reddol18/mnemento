"""Schema proposals (PLAN 5-1 B, ADR-0014). Store first, organize later: the Keeper proposes how to
organize drafts (unregistered fields and values that are already stored); the user approves. Nothing is
applied without approved_by and the user's answer."""

from __future__ import annotations

from typing import Any

import hashlib
import json

from ..ledger import Ledger
from ..schema.definition import NAME_RE, SchemaDef
from ..timeutil import is_calendar_date, is_instant
from .kind import judge_kind

def infer_field(samples: list[Any]) -> dict[str, Any]:
    vals = [s for s in samples if s is not None]
    if not vals:
        return {"type": "string"}
    if all(isinstance(v, bool) for v in vals):
        return {"type": "boolean"}
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return {"type": "integer"}
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return {"type": "number"}
    if all(isinstance(v, list) for v in vals):
        items = [i for v in vals for i in v]
        item_type = infer_field(items)["type"] if items else "string"
        return {"type": "array", "items": {"type": item_type if item_type != "array" else "string"}}
    if all(isinstance(v, str) for v in vals):
        if all(is_calendar_date(v) for v in vals):
            return {"type": "string", "format": "date"}
        if all(is_instant(v) for v in vals):
            return {"type": "string", "format": "date-time"}
        distinct = sorted(set(vals))
        out: dict[str, Any] = {"type": "string"}
        if len(vals) >= 3 and len(distinct) <= 5:
            out["enum_candidates"] = distinct
        return out
    return {"type": "string", "note": "mixed value types seen"}


def organize_proposals(ledger: Ledger, entity_type: str | None = None) -> list[dict[str, Any]]:
    """Proposals to organize drafts (ADR-0014): register draft fields (type, description, optional index),
    register draft enum values (with labels), and merge look-alike names. Nothing is applied here."""
    from .drafts import draft_dictionary, similar_names

    from .newtype import type_proposal

    out = []
    for etype in ([entity_type] if entity_type else ledger.schemas.names()):
        schema = ledger.schemas.get(etype)
        if schema.is_draft:  # ADR-0021: a new type is registered as a whole
            out.append(type_proposal(ledger, schema))
            continue
        drafts = draft_dictionary(ledger, etype, schema)
        if not drafts["fields"] and not drafts["values"]:
            continue
        fields = {}
        for name, d in drafts["fields"].items():
            typed = infer_field(d["samples"]) if len(d["types"]) == 1 else {"type": "string"}
            fields[name] = {"type": typed["type"], "count": d["count"], "samples": d["samples"],
                            "first_seen": d["first_seen"], "valid_name": d["queryable"]}
        merges = []
        names = [*schema.fields, *drafts["fields"]]
        for name in drafts["fields"]:
            for other in similar_names(name, names):
                if other in schema.fields or {"field": other, "into": name} not in merges:
                    merges.append({"field": name, "into": other})
        core = {"entity_type": etype, "from_version": schema.version, "fields": sorted(fields),
                "values": {f: sorted(map(str, v)) for f, v in drafts["values"].items()}}
        pid = hashlib.sha1(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        parts = [f"{n} ({f['type']}, {f['count']} record(s))" for n, f in fields.items()]
        parts += [f"{f} value '{v}' ({n} record(s))" for f, vs in drafts["values"].items() for v, n in vs.items()]
        out.append({
            "id": pid,
            "kind": "organize_schema",
            "entity_type": etype,
            "from_version": schema.version,
            "to_version": schema.version + 1,
            "register_fields": fields,
            "register_values": {f: {str(v): n for v, n in vs.items()} for f, vs in drafts["values"].items()},
            "merge_suggestions": merges,
            "question": f"Register in {etype}: " + "; ".join(parts) + "? Each field needs a description and "
                        "each value a label (e.g. remember -> 리멤버).",
            "status": "proposed — the data is already stored and queryable; apply only after the user explicitly "
                      "agrees (apply_schema_proposal)",
        })
    return out


def apply_proposal(ledger: Ledger, proposal_id: str, *, approved_by: str, user_answer: str,
                   descriptions: dict[str, str] | None = None, labels: dict[str, dict[str, list[str]]] | None = None,
                   merges: dict[str, str] | None = None, index: list[str] | None = None,
                   now: str | None = None, type_description: str | None = None) -> dict[str, Any]:
    """Apply an organize proposal with the user's explicit consent (ADR-0014). Merges move values with
    `migrated` events (history kept, nothing deleted); registration is an additive schema version.
    A new type's proposal (ADR-0021, id type_...) registers the type itself."""
    from ..errors import SchemaDefinitionError
    from ..timeutil import format_instant, now as tz_now
    from .newtype import PROPOSAL_PREFIX, apply_type_proposal

    if proposal_id.startswith(PROPOSAL_PREFIX):
        if merges:
            raise SchemaDefinitionError("merges are for organizing registered types; for a new type, record the "
                                        "values under the field you want first")
        return apply_type_proposal(ledger, proposal_id, approved_by=approved_by, user_answer=user_answer,
                                   descriptions=descriptions, labels=labels, index=index,
                                   description=type_description, now=now)

    if not (approved_by or "").strip() or not (user_answer or "").strip():
        raise SchemaDefinitionError("apply needs approved_by and the user's answer (explicit consent)")
    proposal = next((p for p in organize_proposals(ledger) if p["id"] == proposal_id), None)
    if proposal is None:
        raise SchemaDefinitionError("unknown or outdated proposal id (drafts changed?) — call propose_schema again")
    descriptions, labels, merges, index = descriptions or {}, labels or {}, merges or {}, index or []
    etype = proposal["entity_type"]
    current = ledger.schemas.get(etype)
    at = now or format_instant(tz_now(ledger.tz))
    evidence = f"schema organize {proposal_id} approved by {approved_by}: '{user_answer}'"

    register = {f: d for f, d in proposal["register_fields"].items() if f not in merges}
    for src, dst in merges.items():
        if src not in proposal["register_fields"]:
            raise SchemaDefinitionError(f"merge source {src!r} is not a draft field of this proposal")
        if not NAME_RE.match(dst):
            raise SchemaDefinitionError(f"merge target {dst!r} is not a valid field name")
        if dst not in current.fields:
            register[dst] = proposal["register_fields"].get(dst) or proposal["register_fields"][src]
    missing_desc = [f for f in register if not (descriptions.get(f) or "").strip()]
    missing_labels = [f"{f}={v}" for f, vs in proposal["register_values"].items() for v in vs
                      if not labels.get(f, {}).get(v)]
    bad_names = [f for f in register if not NAME_RE.match(f)]
    if missing_desc or missing_labels or bad_names:
        raise SchemaDefinitionError(f"missing descriptions {missing_desc}, labels {missing_labels}; "
                                    f"invalid names {bad_names} (merge them into a valid name)")
    new = current.to_dict()
    new["version"] = current.version + 1
    for f, d in register.items():
        new["fields"][f] = {"type": d["type"], "description": descriptions[f]}
    for f in index:
        if f in new["fields"]:
            new["fields"][f]["indexed"] = True
    for f, vs in proposal["register_values"].items():
        fd = new["fields"][f]
        for v in vs:
            fd["enum"].append(v)
            fd.setdefault("labels", {})[v] = list(labels[f][v])
    tmp = SchemaDef.from_dict(new)

    # 1) merges: move values into the target field with `migrated` events (no overwrite of other values)
    moved, conflicts = 0, []
    for src, dst in merges.items():
        for e in ledger.find(etype):
            if src not in e.doc:
                continue
            if dst in e.doc and e.doc[dst] != e.doc[src]:
                conflicts.append(e.id)
                continue
            ledger.record_event(e.id, "migrated", {dst: e.doc[src], src: None}, at, approved_by, evidence)
            moved += 1
    # 2) every stored value of a registered field must fit its declared type
    wrong = [e.id for e in ledger.find(etype) if tmp.validation_errors(e.doc, lenient=True)]
    if wrong:
        raise SchemaDefinitionError(f"records whose values do not fit the declared types: {wrong[:10]}")
    applied = ledger.schemas.register(new)
    with ledger.storage.transaction():
        ledger.storage.record_schema_change({
            "entity_type": etype, "version": applied.version, "proposal_id": proposal_id,
            "approved_by": approved_by, "user_answer": user_answer, "applied_at": at,
            "summary": {"registered_fields": sorted(register), "registered_values": proposal["register_values"],
                        "merged": merges, "indexed": index, "moved": moved, "conflicts": conflicts},
        })
    return {"entity_type": etype, "version": applied.version, "registered_fields": sorted(register),
            "registered_values": proposal["register_values"], "merged": merges, "moved_values": moved,
            "merge_conflicts": conflicts, "indexed": index}


def draft_schema(name: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    """A first schema draft for a new record type, inferred from example records."""
    if not NAME_RE.match(name):
        raise ValueError(f"invalid schema name {name!r}")
    if not samples or not all(isinstance(s, dict) for s in samples):
        raise ValueError("samples must be a non-empty list of objects")
    names: list[str] = []
    for s in samples:
        for k in s:
            if k not in names:
                names.append(k)
    fields = {}
    for k in names:
        if not NAME_RE.match(k):
            continue
        vals = [s.get(k) for s in samples]
        f = infer_field(vals)
        f["description"] = "TODO: describe this field."
        if all(k in s and s[k] is not None for s in samples):
            f["required"] = True
        fields[k] = f
    draft = {"name": name, "version": 1, "description": "TODO: describe this record type.", "fields": fields}
    judged = judge_kind(samples)  # ADR-0016: entity or series, with the signals; the user confirms
    if judged["kind"] == "series":
        draft.update({"kind": "series", **judged["series"]})
    notes = ["enum_candidates are suggestions: turn them into `enum` or drop them.",
             "Mark fields used in filters as indexed: true.",
             "proposed — not applied; confirm by registering it."]
    if judged["question"]:
        notes.insert(0, "kind undecided — ask the user: " + judged["question"])
    return {"kind": "new_schema", "draft": draft, "record_kind": judged, "notes": notes}
