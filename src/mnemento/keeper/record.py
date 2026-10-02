"""Write path (ADR-0002): structured requests are validated and stored by code; free text is first
structured by the LLM and then goes through the very same code path. Whenever the target or a
referenced record is uncertain, nothing is stored and the Keeper asks back."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import events as ev
from ..errors import (ConflictError, DocumentValidationError, EntityExistsError, EntityNotFoundError,
                      EntityRetractedError, InvalidEventError, InvalidTimeError,
                      SchemaNotFoundError, UnknownFieldError)
from ..ledger import ENTITY_ID_RE, Ledger
from ..timeutil import format_instant, now as tz_now
from .identity import IdentityResolver
from .llm import LLMAdapter, LLMError
from .proposals import pending_extensions
from .query.interpret import render_dictionary, select_schemas
from .trace import Trace

Scalar = str | int | float | bool


class RecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: str = Field(description="Record type (schema name).")
    kind: Literal["created", "updated", "status_changed", "corrected", "retracted"]
    entity_id: str | None = Field(default=None, description="Target id, if known.")
    match: dict[str, Scalar] = Field(
        default_factory=dict,
        description="Find the target by field values instead of id, e.g. {platform, posting_id}. "
                    "Reference fields may be given by name.")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="created/updated: field values (reference fields may be names); "
                    "status_changed: {to, from?}; corrected: {target, payload}; retracted: {} or {target}.")
    at: str | None = Field(default=None, description="When it happened, ISO 8601 with offset. "
                                                     "Defaults to now.")


@dataclass
class RecordResult:
    status: str  # "recorded" | "clarify" | "rejected" | "error"
    message: str
    entity_id: str | None = None
    event_id: str | None = None
    entity: dict[str, Any] | None = None
    options: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    request: dict[str, Any] | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Recorder:
    def __init__(self, ledger: Ledger, llm: LLMAdapter | None = None):
        self.ledger = ledger
        self.llm = llm

    # ---- structured path ------------------------------------------------------------------

    def record(self, request: RecordRequest | dict[str, Any], *, by: str, evidence: str | None = None,
               now: datetime | None = None, trace: Trace | None = None) -> RecordResult:
        trace = trace or Trace()
        if not trace.path:
            trace.path = "structured"
        with trace.stage("record"):
            res = self._record(request, by=by, evidence=evidence, now=now)
        res.trace = trace.finish().to_dict()
        return res

    def _record(self, request, *, by, evidence, now) -> RecordResult:
        try:
            req = request if isinstance(request, RecordRequest) else RecordRequest.model_validate(request)
        except ValidationError as exc:
            return RecordResult("rejected", "Malformed record request.",
                                errors=[f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()])
        reqd = req.model_dump(exclude_defaults=True)
        try:
            schema = self.ledger.schemas.get(req.entity_type)
        except SchemaNotFoundError:
            return RecordResult("rejected", f"Unknown record type {req.entity_type!r}.",
                                errors=[f"known types: {self.ledger.schemas.names()}"], request=reqd)
        now = now or tz_now(self.ledger.tz)
        at = req.at or format_instant(now)
        payload = dict(req.payload)

        # unknown fields: never stored, but remembered for schema proposals (PLAN 5-1 B)
        if req.kind in (ev.CREATED, ev.UPDATED):
            unknown = {k: v for k, v in payload.items() if k not in schema.fields}
            unknown_match = {k: v for k, v in req.match.items() if k not in schema.fields}
            if unknown or unknown_match:
                with self.ledger.storage.transaction():
                    self.ledger.storage.note_unknown_fields(req.entity_type, {**unknown, **unknown_match},
                                                            format_instant(now), by)
                return RecordResult(
                    "rejected",
                    f"{req.entity_type} has no field(s) {sorted({**unknown, **unknown_match})}. Nothing was stored. "
                    "Remove them, or ask for a schema extension.",
                    errors=[f"unknown field: {k}" for k in sorted({**unknown, **unknown_match})],
                    proposals=pending_extensions(self.ledger, req.entity_type), request=reqd)

        # reference fields given by name -> ids (ADR-0006: certain matches only)
        for container in (payload, req.match):
            for fname, value in list(container.items()):
                fd = schema.fields.get(fname)
                if not (fd and fd.ref and isinstance(value, str)) or self._is_entity(value, fd.ref):
                    continue
                res = IdentityResolver(self.ledger, fd.ref).resolve(value)
                if res.status == "match":
                    container[fname] = res.matches[0]
                elif res.status == "ambiguous":
                    return RecordResult("clarify", f"'{value}' matches several {fd.ref} records. Which one?",
                                        options=res.matches, request=reqd)
                else:
                    opts = [f"{c['id']} ({c['name']})" for c in res.candidates[:5]]
                    return RecordResult(
                        "clarify",
                        f"No {fd.ref} is recorded as '{value}'."
                        + (" Is it one of these?" if opts else f" Create a new {fd.ref} first?"),
                        options=opts + [f"create new {fd.ref} '{value}'"], request=reqd)

        # target entity
        if req.kind == ev.CREATED:
            entity_id = req.entity_id or self._new_id(req.entity_type, payload)
            dup = self._duplicate_of(req.entity_type, payload)
            if dup:
                return RecordResult("clarify",
                                    f"This looks like an existing {req.entity_type}: {dup}. Update it instead?",
                                    entity_id=dup, options=[f"update {dup}", "create anyway with a distinct name"],
                                    request=reqd)
        else:
            entity_id, clar = self._find_target(req)
            if clar:
                clar.request = reqd
                return clar

        try:
            event = self.ledger.record_event(entity_id, req.kind, payload, at, by, evidence,
                                             entity_type=req.entity_type)
        except ConflictError as exc:
            current = self.ledger.get_entity(entity_id)
            status = current.doc.get("status") if current else None
            return RecordResult(
                "clarify",
                f"This contradicts the current record ({exc}). Current status: {status!r}. Nothing was stored.",
                entity_id=entity_id,
                options=[f"record it as a change from {status!r} (drop 'from')",
                         "correct the earlier event instead (corrected)", "cancel"],
                request=reqd)
        except DocumentValidationError as exc:
            return RecordResult("rejected", "The record does not satisfy the schema. Nothing was stored.",
                                entity_id=entity_id, errors=exc.errors, request=reqd)
        except (InvalidEventError, InvalidTimeError, EntityExistsError, EntityNotFoundError,
                EntityRetractedError, UnknownFieldError) as exc:
            return RecordResult("rejected", str(exc), entity_id=entity_id, errors=[str(exc)], request=reqd)
        state = self.ledger.get_entity(entity_id)
        note = "" if req.at else " (time of occurrence not given; recorded as now)"
        return RecordResult("recorded", f"Recorded {req.kind} on {entity_id}{note}.", entity_id=entity_id,
                            event_id=event.id, entity=state.as_json() if state else None, request=reqd)

    def _is_entity(self, value: str, etype: str) -> bool:
        e = self.ledger.get_entity(value) if ENTITY_ID_RE.match(value) else None
        return e is not None and e.type == etype

    def _new_id(self, etype: str, payload: dict[str, Any]) -> str:
        return f"{etype}_{uuid.uuid4().hex[:10]}"

    def _duplicate_of(self, etype: str, payload: dict[str, Any]) -> str | None:
        name = payload.get("name")
        if etype == "company" or ("normalized_name" in self.ledger.schemas.get(etype).fields and name):
            for probe in filter(None, [payload.get("business_number"), name, payload.get("normalized_name")]):
                res = IdentityResolver(self.ledger, etype).resolve(str(probe))
                if res.status == "match":
                    return res.matches[0]
        return None

    def _find_target(self, req: RecordRequest) -> tuple[str | None, RecordResult | None]:
        if req.entity_id:
            return req.entity_id, None
        if not req.match:
            return None, RecordResult("rejected", "Give entity_id or match fields to find the target.")
        try:
            found = self.ledger.find(req.entity_type, dict(req.match))
        except UnknownFieldError as exc:
            return None, RecordResult("rejected", str(exc), errors=[str(exc)])
        if len(found) == 1:
            return found[0].id, None
        if not found:
            return None, RecordResult("clarify", f"No {req.entity_type} matches {req.match}. "
                                                 "Record it as new, or give another identifier?",
                                      options=[f"create new {req.entity_type}", "give entity_id"])
        return None, RecordResult("clarify", f"{len(found)} {req.entity_type} records match {req.match}. Which one?",
                                  options=[f"{e.id} ({_short(e.doc)})" for e in found[:10]])

    # ---- free-text path -------------------------------------------------------------------

    def record_text(self, text: str, *, by: str, evidence: str | None = None,
                    now: datetime | None = None) -> RecordResult:
        trace = Trace(path="llm")
        if self.llm is None:
            return RecordResult("error", "Free-text recording needs an LLM; send a structured request instead.",
                                trace=trace.finish().to_dict())
        now = now or tz_now(self.ledger.tz)
        schemas = {n: self.ledger.schemas.get(n) for n in self.ledger.schemas.names()}
        with trace.stage("structure"):
            dictionary = render_dictionary(select_schemas(text, schemas), {})
            prompt = (f"Now: {now.isoformat()} ({self.ledger.tz}).\n\nDictionary:\n{dictionary}\n\n"
                      f"Message to record: {text}")
            try:
                r = self.llm.complete_json(system=RECORD_SYSTEM, prompt=prompt,
                                           schema=RecordDraft.model_json_schema(), stage="structure")
            except LLMError as exc:
                return RecordResult("error", f"LLM call failed: {exc}", trace=trace.finish().to_dict())
            trace.add_llm(r.usage)
            try:
                draft = RecordDraft.model_validate(r.data)
            except ValidationError as exc:
                return RecordResult("error", "The model returned an unusable draft.",
                                    errors=[str(e["msg"]) for e in exc.errors()], trace=trace.finish().to_dict())
        if draft.kind == "clarify" or draft.request is None:
            return RecordResult("clarify", draft.clarify_question or "What exactly should be recorded?",
                                options=draft.options, trace=trace.finish().to_dict())
        return self.record(draft.request, by=by, evidence=evidence or f"text: {text}", now=now, trace=trace)


class RecordDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["record", "clarify"]
    request: RecordRequest | None = None
    clarify_question: str | None = None
    options: list[str] = Field(default_factory=list)


RECORD_SYSTEM = """You turn a short message into a structured record request for a record book.
Rules:
- Use only record types and fields from the dictionary. Enum values must be allowed values (use labels).
- Reference fields ("ref") may be filled with the referenced record's name; the system resolves it.
- To change an existing record, set kind (updated / status_changed) and identify it with `match`
  using identifying fields (e.g. platform + posting number) instead of guessing an id.
- status_changed payload is {"to": <status>} (add "from" only if the message states the old status).
- Dates are YYYY-MM-DD; `at` is ISO 8601 with offset when the message says when it happened.
- If the message is ambiguous (which record? what happened?), return kind=clarify with options."""


def _short(doc: dict[str, Any]) -> str:
    keys = [k for k in ("name", "title", "platform", "posting_id", "status", "applied_at") if k in doc]
    return ", ".join(f"{k}={doc[k]}" for k in keys[:4])
