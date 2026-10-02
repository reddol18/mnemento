"""Keeper — the single entry point other agents talk to (ADR-0002)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..ledger import Ledger
from .llm import LLMAdapter
from .proposals import draft_schema, pending_extensions
from .query.pipeline import KeeperAnswer, QueryPipeline
from .query.spec import QuerySpec
from .record import Recorder, RecordRequest, RecordResult


class Keeper:
    def __init__(self, ledger: Ledger, llm: LLMAdapter | None = None):
        self.ledger = ledger
        self.llm = llm
        self.pipeline = QueryPipeline(ledger, llm)
        self.recorder = Recorder(ledger, llm)

    # read
    def ask(self, question: str, *, spec: QuerySpec | dict | None = None, now: datetime | None = None,
            narrate: bool = False) -> KeeperAnswer:
        return self.pipeline.ask(question, spec=spec, now=now, narrate=narrate)

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        e = self.ledger.get_entity(entity_id)
        if e is None:
            return None
        return {"entity": e.as_json(), "schema_version": e.schema_version, "retracted": e.retracted,
                "created_at": e.created_at, "updated_at": e.updated_at, "applied_events": e.event_ids}

    def history(self, entity_id: str) -> list[dict[str, Any]]:
        return [
            {"id": e.id, "seq": e.seq, "kind": e.kind, "payload": e.payload, "at": e.at,
             "recorded_at": e.recorded_at, "by": e.by, "evidence": e.evidence,
             "target_event_id": e.target_event_id, "schema_version": e.schema_version}
            for e in self.ledger.history(entity_id)
        ]

    def list_schemas(self, name: str | None = None) -> list[dict[str, Any]]:
        names = [name] if name else self.ledger.schemas.names()
        return [self.ledger.schemas.get(n).to_dict() for n in names]

    # write
    def record(self, request: RecordRequest | dict[str, Any], *, by: str, evidence: str | None = None,
               now: datetime | None = None) -> RecordResult:
        return self.recorder.record(request, by=by, evidence=evidence, now=now)

    def record_text(self, text: str, *, by: str, evidence: str | None = None,
                    now: datetime | None = None) -> RecordResult:
        return self.recorder.record_text(text, by=by, evidence=evidence, now=now)

    # schema design (proposals only)
    def propose_schema(self, entity_type: str | None = None,
                       samples: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if samples:
            if not entity_type:
                raise ValueError("entity_type (the new type's name) is required with samples")
            if entity_type in self.ledger.schemas.names():
                raise ValueError(f"{entity_type} already exists; omit samples to see extension proposals")
            return [draft_schema(entity_type, samples)]
        return pending_extensions(self.ledger, entity_type)
