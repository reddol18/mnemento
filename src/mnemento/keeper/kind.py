"""Entity or series? (ADR-0016) — a proposal from sample rows, with the signals behind it. The user confirms;
when the signals disagree the answer is a question, not a guess.

Signals (counted on the samples):
  time      a date/date-time field in every row, with several time points per key
  numeric   most value fields (neither key nor time) are numbers
  per_key   rows per key — series have many (median >= 5)
  entity    a per-row identifier, a reference to another record outside the key, or a categorical field that
            changes within one key over time (side, mood) — signs of one-by-one records with their own lives.
            A per-row identifier or a changing field named like a status decides "entity" on its own; the other
            entity signs next to all series signs make a question.
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import defaultdict
from typing import Any

from ..timeutil import is_calendar_date, is_instant

MIN_ROWS_PER_KEY = 5
STATUS_NAME = re.compile(r"(^|_)(status|state|stage|phase)($|_)|상태")


def _is_time(v: Any) -> bool:
    return isinstance(v, str) and (is_calendar_date(v) or is_instant(v))


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def judge_kind(samples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in samples if isinstance(r, dict)]
    fields = list(dict.fromkeys(k for r in rows for k in r))
    n = len(rows)
    out: dict[str, Any] = {"kind": None, "signals": {}, "reasons": [], "question": None, "series": None}
    time_fields = [f for f in fields if all(_is_time(r.get(f)) for r in rows)]
    if not time_fields:
        out.update(kind="entity", reasons=["no field holds a date or time in every row: not measurements over time"])
        out["signals"]["time"] = None
        return out
    tf = time_fields[0]
    strings = [f for f in fields if f != tf and all(isinstance(r.get(f), str) for r in rows if r.get(f) is not None)
               and not all(_is_time(r.get(f)) for r in rows)]
    # per-row identifiers: a string field whose values are all different (and not the time)
    row_ids = [f for f in strings if n >= 3 and len({r.get(f) for r in rows}) == n]
    # the series key: the smallest set of the other string fields that, with the time field, identifies each row
    key: list[str] = []
    candidates = [f for f in strings if f not in row_ids]
    for size in range(1 if candidates else 0, min(3, len(candidates)) + 1):  # a key of at least one field
        found = next((list(c) for c in itertools.combinations(candidates, size)
                      if len({(tuple(r.get(f) for f in c), r.get(tf)) for r in rows}) == n), None)
        if found is not None:
            key = found
            break
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(f) for f in key)].append(r)
    per_key = statistics.median(len(g) for g in groups.values()) if groups else 0
    values = [f for f in fields if f not in key and f != tf and f not in row_ids]
    numeric = [f for f in values if all(_is_num(r.get(f)) for r in rows if r.get(f) is not None)]
    categorical = [f for f in values if f in strings and len({r.get(f) for r in rows}) <= max(5, n // 5)]
    changing = [f for f in categorical if any(len({r.get(f) for r in g}) > 1 for g in groups.values())]
    status_like = [f for f in changing if STATUS_NAME.search(f)]
    refs = [f for f in values if f in strings and (f.endswith("_id") or f.endswith("_ids"))]
    share = len(numeric) / len(values) if values else 0.0
    out["signals"] = {"time_field": tf, "key": key, "rows": n, "median_rows_per_key": per_key,
                      "numeric_share": round(share, 2), "numeric": numeric, "row_identifiers": row_ids,
                      "references": refs, "changing_categories": changing, "status_fields": status_like}
    series_votes = {
        "time": per_key >= 2,
        "numeric": share >= 0.5 and bool(numeric),
        "per_key": per_key >= MIN_ROWS_PER_KEY,
    }
    entity_votes = {"row_identifier": bool(row_ids), "reference": bool(refs), "changing_category": bool(changing)}
    for name, ok in series_votes.items():
        out["reasons"].append(f"{'series' if ok else 'entity'} sign: {name}"
                              + {"time": f" — {per_key:g} time points per key on {tf}",
                                 "numeric": f" — {len(numeric)}/{len(values)} value fields are numbers",
                                 "per_key": f" — median {per_key:g} rows per key (series needs >= {MIN_ROWS_PER_KEY})"}[name])
    for name, hit in entity_votes.items():
        if hit:
            out["reasons"].append(f"entity sign: {name} — " + {
                "row_identifier": f"{row_ids} is different in every row (each row is its own record)",
                "reference": f"{refs} points at other records",
                "changing_category": f"{changing} changes within one key (a status or kind of event, not a measurement)"}[name])
    if row_ids or status_like:  # strong signs: every row is its own record, or records move through statuses
        out["kind"] = "entity"
        if status_like:
            out["reasons"].append(f"entity sign: status — {status_like} is a status that changes over time")
    elif all(series_votes.values()) and not any(entity_votes.values()):
        out["kind"] = "series"
        gran = "day" if all(is_calendar_date(r[tf]) for r in rows) else "instant"
        out["series"] = {"series_key": key, "time_field": tf, "granularity": gran, "measures": numeric}
    elif not any(series_votes.values()) or (not series_votes["per_key"] and any(entity_votes.values())):
        out["kind"] = "entity"
    elif any(entity_votes.values()) and not all(series_votes.values()):
        out["kind"] = "entity"
    else:
        out["question"] = (f"These rows have a time field ({tf}) and numbers, but also "
                           + ("; ".join(r.split(" — ")[0] for r in out["reasons"] if r.startswith("entity")) or
                              "too few points per key")
                           + ". Is this a value that piles up over time (like a price or a weight), or are these "
                             "records you manage one by one (each can be corrected or cancelled on its own)?")
    return out
