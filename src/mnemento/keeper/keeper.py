"""Keeper — the single entry point other agents talk to (ADR-0002)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..ledger import Ledger
from .llm import LLMAdapter
from .drafts import draft_dictionary
from .proposals import apply_proposal, draft_schema, organize_proposals
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
            narrate: bool = False, hint: str | None = None, caller: str | None = None) -> KeeperAnswer:
        return self.pipeline.ask(question, spec=spec, now=now, narrate=narrate, hint=hint, caller=caller)

    def query_log(self, view: str = "recent", *, n: int = 20, since: str | None = None,
                  path: str | None = None) -> dict[str, Any]:
        """Look back at earlier questions (ADR-0015)."""
        if self.pipeline.query_log is None:
            return {"view": view, "disabled": True, "entries": []}
        return self.pipeline.query_log.find(view, n=n, since=since, path=path)

    def purge_query_log(self, before: str | None = None) -> int:
        return self.pipeline.query_log.purge(before) if self.pipeline.query_log is not None else 0

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        e = self.ledger.get_entity(entity_id)
        if e is None:
            return None
        return {"entity": e.as_json(), "schema_version": e.schema_version, "retracted": e.retracted,
                "reached": e.reached,
                "created_at": e.created_at, "updated_at": e.updated_at, "applied_events": e.event_ids}

    def history(self, entity_id: str) -> list[dict[str, Any]]:
        return [
            {"id": e.id, "seq": e.seq, "kind": e.kind, "payload": e.payload, "at": e.at,
             "recorded_at": e.recorded_at, "by": e.by, "evidence": e.evidence,
             "target_event_id": e.target_event_id, "schema_version": e.schema_version}
            for e in self.ledger.history(entity_id)
        ]

    def list_schemas(self, name: str | None = None) -> list[dict[str, Any]]:
        """Registered dictionary + drafts (stored but not yet organized) + organize approvals (ADR-0014)."""
        names = [name] if name else self.ledger.schemas.names()
        out = []
        for n in names:
            d = self.ledger.schemas.get(n).to_dict()
            d["drafts"] = draft_dictionary(self.ledger, n)
            d["organize_history"] = self.ledger.storage.schema_changes(n)
            out.append(d)
        return out

    # write
    def record(self, request: RecordRequest | dict[str, Any], *, by: str, evidence: str | None = None,
               now: datetime | None = None) -> RecordResult:
        return self.recorder.record(request, by=by, evidence=evidence, now=now)

    def record_text(self, text: str, *, by: str, evidence: str | None = None,
                    now: datetime | None = None) -> RecordResult:
        return self.recorder.record_text(text, by=by, evidence=evidence, now=now)

    def record_series(self, entity_type: str, points: list[dict[str, Any]], *, by: str,
                      source: str) -> dict[str, Any]:
        """ADR-0016: one batch of series points (all or nothing; a point already stored is updated)."""
        from ..series import ingest

        return ingest(self.ledger, entity_type, points, by=by, source=source).to_dict()

    def revert_series_batch(self, batch_id: str) -> dict[str, Any]:
        from ..series import revert_batch

        return revert_batch(self.ledger, batch_id)

    # schema design (proposals only)
    def propose_schema(self, entity_type: str | None = None,
                       samples: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if samples:
            if not entity_type:
                raise ValueError("entity_type (the new type's name) is required with samples")
            if entity_type in self.ledger.schemas.names():
                raise ValueError(f"{entity_type} already exists; omit samples to see extension proposals")
            return [draft_schema(entity_type, samples)]
        return organize_proposals(self.ledger, entity_type)

    def apply_schema_proposal(self, proposal_id: str, *, approved_by: str, user_answer: str,
                              descriptions: dict[str, str] | None = None,
                              labels: dict[str, dict[str, list[str]]] | None = None,
                              merges: dict[str, str] | None = None, index: list[str] | None = None,
                              type_description: str | None = None, items: list[str] | None = None) -> dict[str, Any]:
        """Organize drafts, register a new type or take repository dictionary improvements — only with the user's
        explicit consent (approved_by + their answer are recorded)."""
        return apply_proposal(self.ledger, proposal_id, approved_by=approved_by, user_answer=user_answer,
                              descriptions=descriptions, labels=labels, merges=merges, index=index,
                              type_description=type_description, items=items)
