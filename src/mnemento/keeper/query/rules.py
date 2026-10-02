"""Rule-based fast path: well-formed questions are turned into a QuerySpec without any LLM call
(PLAN 2-1 principle 3).

It is deliberately conservative. Everything in the question must be explained by the schema
dictionary (record-type keywords, enum labels, dates) or by a small set of filler words; if any
word is left over, the parser gives up and the question goes to the LLM interpreter.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Callable

from ...schema.definition import SchemaDef
from .spec import Filter, QuerySpec

# Korean particles/endings that may trail a recognised word
_PARTICLES = ("에서", "으로", "에게", "까지", "부터", "에는", "은", "는", "이", "가", "을", "를",
              "에", "의", "로", "도", "만", "한", "했", "된", "됐", "중")
_FILLER = {
    "몇", "곳", "건", "개", "번", "회", "몇곳", "몇건", "몇개", "몇번", "몇회", "개수", "수", "총", "모두", "전체", "했나", "했어",
    "했지", "했는지", "됐나", "된", "인가", "있나", "나", "요", "how", "many", "count", "the", "of",
    "on", "in", "were", "was", "did", "i", "we", "list", "목록", "어디", "어느", "어떤", "곳은",
    "곳이", "데", "알려줘", "보여줘", "줘", "is", "are", "which", "what",
    "예전에", "예전", "전에", "이전에", "적", "있나", "있어", "있었나", "언제", "ever", "before",
}
_COUNT_WORDS = ("몇", "개수", "how many", "count", "총")
_LIST_WORDS = ("목록", "어디", "어느", "어떤", "list", "which", "보여줘", "있나", "있었나", "적 있", "했나", "ever")

_ABS_DATE = re.compile(r"(?:(\d{4})[-./])?(\d{1,2})[/.-](\d{1,2})(?!\d)|(\d{1,2})월\s*(\d{1,2})일")
_REL_DATE = {"오늘": 0, "today": 0, "어제": -1, "yesterday": -1, "그제": -2, "그저께": -2}


def _strip_particles(token: str) -> str:
    for p in sorted(_PARTICLES, key=len, reverse=True):
        if token.endswith(p) and len(token) > len(p):
            return token[: -len(p)]
    return token


def _resolve_md(month: int, day: int, year: int | None, today: date) -> date | None:
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d > today + timedelta(days=1):  # "12/30" asked in January -> last year
        d = d.replace(year=d.year - 1)
    return d


def _label_words(schema: SchemaDef) -> list[str]:
    words = []
    for fd in schema.fields.values():
        for value in fd.enum or ():
            words += [str(value), *(fd.labels or {}).get(value, ())]
    return [w.lower() for w in words if w]


def parse_simple(
    question: str,
    schemas: dict[str, SchemaDef],
    now: datetime,
    resolve_name: Callable[[str, str], bool] | None = None,
) -> QuerySpec | None:
    """`resolve_name(ref_type, text)` returns True only for a *certain* identity match; with it,
    a leftover phrase such as a company name becomes a name_is filter."""
    q = question.strip().lower()
    if not q:
        return None
    today = now.date()
    consumed: list[tuple[int, int]] = []

    def take(m_start: int, m_end: int) -> None:
        consumed.append((m_start, m_end))

    # 1) record type from schema keywords: exactly one type must be named
    hits: dict[str, list[tuple[int, int]]] = {}
    for name, s in schemas.items():
        for kw in s.keywords:
            for m in re.finditer(re.escape(kw.lower()), q):
                hits.setdefault(name, []).append(m.span())
    if not hits:
        # no type keyword: infer it from enum labels when exactly one type owns the matched words
        scores = {n: sum(1 for w in set(_label_words(s)) if w in q) for n, s in schemas.items()}
        best = max(scores.values(), default=0)
        owners = [n for n, v in scores.items() if v == best and v > 0]
        if len(owners) != 1:
            return None
        hits = {owners[0]: []}
    if len(hits) != 1:
        return None
    etype = next(iter(hits))
    schema = schemas[etype]
    for span in hits[etype]:
        take(*span)

    # 2) enum values through their labels (and the raw enum value)
    # longest words first across all fields/values, so "불합격" (rejected) is not also read as "합격" (passed)
    candidates = []
    for fname, fd in schema.fields.items():
        for value in fd.enum or ():
            for w in {str(value).lower(), *[w.lower() for w in (fd.labels or {}).get(value, ())]}:
                candidates.append((w, fname, value))
    candidates.sort(key=lambda c: -len(c[0]))
    by_field: dict[str, list[str]] = {}
    for w, fname, value in candidates:
        for m in re.finditer(re.escape(w), q):
            if any(m.start() < b and a < m.end() for a, b in consumed):
                continue
            take(*m.span())
            if value not in by_field.setdefault(fname, []):
                by_field[fname].append(value)
    filters = [Filter(field=f, op="eq", value=v[0]) if len(v) == 1 else Filter(field=f, op="in", value=v)
               for f, v in by_field.items()]

    # 3) one date, applied to the schema's default date field
    dates: list[date] = []
    for m in _ABS_DATE.finditer(q):
        if m.group(2):
            d = _resolve_md(int(m.group(2)), int(m.group(3)), int(m.group(1)) if m.group(1) else None, today)
        else:
            d = _resolve_md(int(m.group(4)), int(m.group(5)), None, today)
        if d is None:
            return None
        dates.append(d)
        take(*m.span())
    for word, delta in _REL_DATE.items():
        for m in re.finditer(re.escape(word), q):
            dates.append(today + timedelta(days=delta))
            take(*m.span())
    if len(dates) > 1:
        return None
    if dates:
        if not schema.default_date_field:
            return None
        # "viewed today" means the view date, not the default date: leave dated status questions to the LLM
        if any(schema.fields[f].enum and f == "status" for f in by_field):
            return None
        filters.append(Filter(field=schema.default_date_field, op="eq", value=dates[0].isoformat()))

    # 4) intent
    if any(w in q for w in _COUNT_WORDS):
        mode = "count"
    elif any(w in q for w in _LIST_WORDS):
        mode = "list"
    else:
        return None

    # 5) everything else must be filler
    chars = list(q)
    for a, b in consumed:
        for i in range(a, b):
            chars[i] = " "
    leftover = re.split(r"[\s?!.,·~]+", "".join(chars))
    unexplained = [tok for tok in leftover
                   if tok and not (tok in _FILLER or _strip_particles(tok) in _FILLER or tok in _PARTICLES)]
    if unexplained:
        # one contiguous unexplained phrase may name a referenced record (e.g. a company)
        if resolve_name is None or not _contiguous(unexplained, leftover):
            return None
        phrase = " ".join(unexplained[:-1] + [_strip_particles(unexplained[-1])])
        refs = [(n, fd.ref) for n, fd in schema.fields.items() if fd.ref]
        matched = [(n, ref) for n, ref in refs if resolve_name(ref, phrase)]
        if len(matched) != 1:
            return None
        filters.append(Filter(field=matched[0][0], op="name_is", value=phrase))

    parts = [f"{f.field}={f.value}" for f in filters]
    return QuerySpec(
        entity_type=etype,
        mode=mode,
        filters=filters,
        list_fields=[f for f in schema.fields if schema.fields[f].indexed][:6] if mode == "list" else [],
        interpretation=f"{etype} {mode}" + (f" where {', '.join(parts)}" if parts else ""),
    )


def _contiguous(unexplained: list[str], tokens: list[str]) -> bool:
    toks = [t for t in tokens if t]
    idx = [i for i, t in enumerate(toks) if t in unexplained]
    return bool(idx) and idx == list(range(idx[0], idx[0] + len(idx)))
