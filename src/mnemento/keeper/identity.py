"""Identity resolution v0 (ADR-0006).

Order: ① external identifier — fields marked `identifier` in the schema (stock code, business number), matched
against identifier-like tokens anywhere in the text, so "바이오주(900001)" finds the record with code 900001
② normalized name + alias dictionary.
Anything weaker only produces *candidates* — never an automatic match or merge; the caller must
ask back.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..ledger import Ledger

# legal-form markers removed before comparing names
_LEGAL_FORMS = [
    "주식회사", "유한회사", "유한책임회사", "합자회사", "합명회사", "사단법인", "재단법인",
    "(주)", "㈜", "(유)", "(사)", "(재)",
    "co., ltd.", "co.,ltd.", "co. ltd.", "co ltd", "co.", "ltd.", "ltd", "inc.", "inc", "corp.", "corp",
    "corporation", "company", "llc", "l.l.c.", "gmbh", "plc",
]
_PAREN_ASCII = re.compile(r"\(([^()]*)\)")
_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_name(name: str) -> str:
    """'(주)가상테크(Gasang Tech)' -> '가상테크', 'Example Labs Inc.' -> 'examplelabs'."""
    s = unicodedata.normalize("NFKC", name).strip().lower()
    for form in _LEGAL_FORMS:
        if form.isascii():  # whole words only: "inc" must not eat "princeton"
            s = re.sub(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", " ", s)
        else:
            s = s.replace(form, " ")
    # drop a parenthesised latin-only name when there is other (e.g. Korean) text outside it
    outside = _PAREN_ASCII.sub("", s)
    if _NON_WORD.sub("", outside) and re.search(r"[^\x00-\x7f]", outside):
        s = _PAREN_ASCII.sub(lambda m: "" if m.group(1).isascii() else m.group(1), s)
    return _NON_WORD.sub("", s)


def _similarity(a: str, b: str) -> float:
    # compare Hangul at the jamo level (NFD), so '가상텍' is close to '가상테크'
    return difflib.SequenceMatcher(None, unicodedata.normalize("NFD", a),
                                   unicodedata.normalize("NFD", b)).ratio()


def normalize_business_number(value: str) -> str:
    return re.sub(r"\D", "", value)


_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]|[A-Za-z0-9]")


def _id_key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKC", value).lower()) or None


def identifier_tokens(text: str) -> set[str]:
    """Identifier-like tokens of a text (letters/digits, hyphens ignored): '바이오주(900001)' -> {'900001'},
    '123-45-67890' -> {'1234567890'}. Single digits and short numbers are not identifiers."""
    out = set()
    for tok in _ID_TOKEN.findall(unicodedata.normalize("NFKC", text)):
        key = _id_key(tok)
        if key and len(key) >= 4:
            out.add(key)
    return out


@dataclass
class Resolution:
    query: str
    matches: list[str] = field(default_factory=list)  # certain matches (rule ① or ②)
    rule: str | None = None  # "identifier:<field>" | "business_number" (unmarked schemas) | "normalized_name" | "alias"
    candidates: list[dict[str, Any]] = field(default_factory=list)  # uncertain, ask back

    @property
    def status(self) -> str:
        if len(self.matches) == 1:
            return "match"
        if len(self.matches) > 1:
            return "ambiguous"
        return "candidates" if self.candidates else "none"


class IdentityResolver:
    def __init__(self, ledger: Ledger, entity_type: str = "company"):
        self.ledger = ledger
        self.entity_type = entity_type

    def resolve(self, name_or_number: str) -> Resolution:
        res = Resolution(query=name_or_number)
        schema = self.ledger.schemas.get(self.entity_type)
        if "name" not in schema.fields and "normalized_name" not in schema.fields:
            # records without a name (e.g. postings): exact match on identifier-like fields
            id_fields = [n for n, f in schema.fields.items()
                         if f.indexed and f.type == "string" and not f.enum and not f.format and not f.ref]
            found: list[str] = []
            for fname in id_fields:
                found += [e.id for e in self.ledger.find(self.entity_type, {fname: name_or_number})
                          if e.id not in found]
            res.matches, res.rule = found, ("identifier" if found else None)
            return res
        entities = self.ledger.find(self.entity_type)  # personal scale: in-memory scan is fine
        id_fields = [n for n, f in schema.fields.items() if f.identifier]
        if id_fields:
            tokens = identifier_tokens(name_or_number)
            for fname in id_fields:
                hits = [e.id for e in entities if _id_key(e.doc.get(fname)) in tokens]
                if hits:
                    res.matches, res.rule = hits, f"identifier:{fname}"
                    return res
        else:  # schemas without identifier marks: the business number rule of v0
            digits = normalize_business_number(name_or_number)
            if len(digits) == 10:
                res.matches = [e.id for e in entities
                               if normalize_business_number(e.doc.get("business_number", "")) == digits]
                if res.matches:
                    res.rule = "business_number"
                    return res

        key = normalize_name(name_or_number)
        if not key:
            return res
        by_name = [e.id for e in entities if self._name_keys(e.doc)[0] == key]
        if by_name:
            res.matches, res.rule = by_name, "normalized_name"
            return res
        by_alias = [e.id for e in entities if key in self._name_keys(e.doc)[1]]
        if by_alias:
            res.matches, res.rule = by_alias, "alias"
            return res

        # weak signals -> candidates only (never auto-matched)
        for e in entities:
            primary, aliases = self._name_keys(e.doc)
            best = 0.0
            for k in {primary, *aliases}:
                if not k:
                    continue
                if key in k or k in key:
                    best = max(best, 0.9)
                best = max(best, _similarity(key, k))
            if best >= 0.7:
                res.candidates.append({"id": e.id, "name": e.doc.get("name"), "score": round(best, 2)})
        res.candidates.sort(key=lambda c: -c["score"])
        return res

    @staticmethod
    def _name_keys(doc: dict[str, Any]) -> tuple[str, set[str]]:
        primary = doc.get("normalized_name") or normalize_name(doc.get("name", ""))
        aliases = {normalize_name(a) for a in doc.get("aliases", [])}
        aliases.add(normalize_name(doc.get("name", "")))
        return primary, aliases
