"""Schema proposals (PLAN 5-1 B, minimal). The Keeper proposes; it never applies a schema change
itself — confirmation belongs to the user or the directing agent."""

from __future__ import annotations

from typing import Any

from ..ledger import Ledger
from ..schema.definition import NAME_RE
from ..timeutil import is_calendar_date, is_instant

PROPOSAL_THRESHOLD = 2  # an unknown field must be seen this many times before it is proposed


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


def pending_extensions(ledger: Ledger, entity_type: str | None = None) -> list[dict[str, Any]]:
    """Additive extension proposals for fields that write requests keep using (ADR-0005)."""
    stats = ledger.storage.unknown_field_stats(entity_type)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for s in stats:
        if s["count"] >= PROPOSAL_THRESHOLD and NAME_RE.match(s["field"]):
            by_type.setdefault(s["entity_type"], []).append(s)
    out = []
    for etype, fields in by_type.items():
        try:
            current = ledger.schemas.get(etype)
        except Exception:
            continue
        add = {}
        for s in fields:
            if s["field"] in current.fields:
                continue  # already added since
            fdef = infer_field(s["samples"])
            fdef["description"] = (f"TODO: describe. Seen {s['count']} times in write requests "
                                   f"by {', '.join(s['agents'])}; samples: {s['samples'][:3]}")
            add[s["field"]] = fdef
        if add:
            out.append({
                "kind": "extend_schema",
                "entity_type": etype,
                "from_version": current.version,
                "to_version": current.version + 1,
                "change": "additive (new optional fields)",
                "add_fields": add,
                "status": "proposed — not applied; confirm by registering the new version",
            })
    return out


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
    return {
        "kind": "new_schema",
        "draft": {"name": name, "version": 1, "description": "TODO: describe this record type.",
                  "fields": fields},
        "notes": ["enum_candidates are suggestions: turn them into `enum` or drop them.",
                  "Mark fields used in filters as indexed: true.",
                  "proposed — not applied; confirm by registering it."],
    }
