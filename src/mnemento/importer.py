"""Idempotent import of records copied from files the user keeps elsewhere (task 0008 step 3).

The files stay the source of truth for now; the ledger is a copy that can be refreshed any number of times:
- a record is identified by a stable entity id derived from its source key;
- unchanged -> nothing written; changed -> one event whose evidence names the source and the collection time:
  `corrected` when a changed value means the copy was wrong (facts such as trades), `updated` when the thing
  itself changed (state such as buy criteria; dated with the source's own date when it has one);
- never in the source any more -> reported, never deleted or retracted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .ledger import Ledger


def stable_id(prefix: str, source_key: str) -> str:
    """Entity id for a record known by `source_key` (same key -> same id)."""
    return f"{prefix}_{hashlib.sha256(source_key.encode()).hexdigest()[:12]}"


@dataclass
class ImportReport:
    created: dict[str, int] = field(default_factory=dict)
    corrected: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    vanished: list[str] = field(default_factory=list)  # in the ledger (from this importer) but not in the source

    def add(self, what: str, entity_type: str) -> None:
        bucket = getattr(self, what)
        bucket[entity_type] = bucket.get(entity_type, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {"created": self.created, "corrected": self.corrected, "updated": self.updated,
                "unchanged": self.unchanged,
                "vanished": self.vanished}


def upsert(ledger: Ledger, report: ImportReport, *, entity_type: str, entity_id: str, doc: dict[str, Any],
           at: str, at_precision: str, by: str, evidence: str, now: str, change_kind: str = "corrected",
           change_at: str | None = None, change_precision: str = "unknown") -> str:
    """Create the record, or record the change when the source changed. Returns created | corrected | updated |
    unchanged. change_kind "updated": only the changed fields are written (removed ones as None), at `change_at`
    (the source's date of the change) or else `now` with precision `change_precision`."""
    if change_kind not in ("corrected", "updated"):
        raise ValueError("change_kind must be corrected or updated")
    doc = {k: v for k, v in doc.items() if v is not None}
    cur = ledger.get_entity(entity_id)
    if cur is None:
        ledger.record_event(entity_id, "created", doc, at, by, evidence, entity_type=entity_type,
                            at_precision=at_precision)
        what = "created"
    elif cur.doc == doc:
        what = "unchanged"
    elif change_kind == "updated":
        diff = {k: v for k, v in doc.items() if cur.doc.get(k) != v}
        diff.update({k: None for k in cur.doc if k not in doc})
        ledger.record_event(entity_id, "updated", diff, change_at or now, by, f"source changed — {evidence}",
                            at_precision="date" if change_at else change_precision)
        what = "updated"
    else:
        created = next(e for e in ledger.history(entity_id) if e.kind == "created")
        ledger.record_event(entity_id, "corrected", {"target": created.id, "payload": doc}, now, by,
                            f"source changed — {evidence}")
        what = "corrected"
    report.add(what, entity_type)
    return what


def report_vanished(ledger: Ledger, report: ImportReport, entity_types: list[str], seen: set[str], by: str,
                    owns: Callable[[Any], bool] | None = None) -> None:
    """Records this importer created earlier that the source no longer has (reported only). `owns` narrows it to
    the records of this source when several imports write the same types (e.g. by source_key prefix)."""
    for t in entity_types:
        for e in ledger.find(t):
            if e.id in seen or (owns is not None and not owns(e)):
                continue
            if any(ev.kind == "created" and ev.by == by for ev in ledger.history(e.id)):
                report.vanished.append(e.id)
