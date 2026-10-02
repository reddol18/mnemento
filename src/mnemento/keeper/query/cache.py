"""QuerySpec cache keyed by *question pattern* (PLAN 2-1 principle 4).

"10/2 사람인 지원 몇 곳?" and "9/30 사람인 지원 몇 곳?" share the pattern "<DATE> 사람인 지원 몇 곳?".
The spec interpreted for the first is stored as a template whose values coming from the question
(dates, numbers) are replaced by slots; a later question with the same pattern fills the slots
and skips the LLM. The key includes the schema versions, so a schema change invalidates entries.
Cached specs are validated again before use.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

from ...schema.definition import SchemaDef
from ...storage.base import Storage
from ...timeutil import format_instant
from .rules import _ABS_DATE, _resolve_md
from .spec import QuerySpec, validate_spec

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_SPACES = re.compile(r"\s+")


def extract_slots(question: str, now: datetime) -> tuple[str, list[str]]:
    """Return (pattern, slot values) — dates normalised to YYYY-MM-DD, numbers as written."""
    q = unicodedata.normalize("NFKC", question).strip().lower()
    slots: list[tuple[int, str]] = []
    today = now.date()

    def date_sub(m: re.Match) -> str:
        if m.group(2):
            d = _resolve_md(int(m.group(2)), int(m.group(3)), int(m.group(1)) if m.group(1) else None, today)
        else:
            d = _resolve_md(int(m.group(4)), int(m.group(5)), None, today)
        if d is None:
            return m.group(0)
        slots.append((m.start(), d.isoformat()))
        return "\x00DATE\x00"

    q = _ABS_DATE.sub(date_sub, q)

    def num_sub(m: re.Match) -> str:
        slots.append((m.start(), m.group(0)))
        return "\x00NUM\x00"

    q = _NUMBER.sub(num_sub, q)
    q = _SPACES.sub(" ", re.sub(r"[?!.。？！]+$", "", q)).strip()
    # slot order = order of appearance in the original question
    values = [v for _, v in sorted(slots, key=lambda t: t[0])] if slots else []
    return q.replace("\x00DATE\x00", "<DATE>").replace("\x00NUM\x00", "<N>"), values


def _fingerprint(schemas: dict[str, SchemaDef]) -> str:
    return ",".join(f"{n}:{s.version}" for n, s in sorted(schemas.items()))


def cache_key(pattern: str, schemas: dict[str, SchemaDef]) -> str:
    return hashlib.sha256(f"{_fingerprint(schemas)}|{pattern}".encode()).hexdigest()


def _replace_substrings(text: Any, mapping: dict[str, str]) -> Any:
    if not isinstance(text, str):
        return text
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def _replace_values(node: Any, mapping: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: (_replace_substrings(v, mapping) if k == "interpretation" else _replace_values(v, mapping))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_values(v, mapping) for v in node]
    if isinstance(node, str) and node in mapping:
        return mapping[node]
    return node


class PlanCache:
    def __init__(self, storage: Storage):
        self.storage = storage

    def lookup(self, question: str, schemas: dict[str, SchemaDef], now: datetime) -> QuerySpec | None:
        pattern, values = extract_slots(question, now)
        raw = self.storage.get_cached_plan(cache_key(pattern, schemas))
        if raw is None:
            return None
        template = json.loads(raw)
        if template.get("n_slots") != len(values):
            return None
        spec_d = _replace_values(template["spec"], {f"{{{{s{i}}}}}": v for i, v in enumerate(values)})
        try:
            spec = QuerySpec.model_validate(spec_d)
        except Exception:
            return None
        if validate_spec(spec, schemas):
            return None
        return spec

    def store(self, question: str, spec: QuerySpec, schemas: dict[str, SchemaDef], now: datetime) -> bool:
        pattern, values = extract_slots(question, now)
        spec_d = json.loads(spec.model_dump_json())
        mapping = {v: f"{{{{s{i}}}}}" for i, v in enumerate(values)}
        if len(mapping) != len(values):
            return False  # the same value twice: ambiguous slot mapping, do not cache
        templ = _replace_values(spec_d, mapping)
        # every slot must be used, otherwise a different date would silently give the same answer
        dumped = json.dumps(templ)
        if any(f"{{{{s{i}}}}}" not in dumped for i in range(len(values))):
            return False
        self.storage.put_cached_plan(cache_key(pattern, schemas),
                                     json.dumps({"pattern": pattern, "n_slots": len(values), "spec": templ},
                                                ensure_ascii=False),
                                     format_instant(now))
        return True
