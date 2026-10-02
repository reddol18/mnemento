"""MCP server exposing the Keeper (MCP Python SDK v2, `MCPServer`).

Configuration (environment variables):
  MNEMENTO_DB       SQLite file (default: ~/.mnemento/mnemento.db)
  MNEMENTO_SCHEMAS  directory of schema definitions to register at start (default: ./schemas of
                    this repository, if present)
  MNEMENTO_LLM      "claude-cli" (default) or "none" (fast path + structured queries only)
  MNEMENTO_MODEL    model alias for the LLM adapter (default: haiku)
  MNEMENTO_EFFORT   CLI effort level (low|medium|high...; default: CLI default)
  MNEMENTO_THINKING_TOKENS  thinking budget for the LLM (0 disables thinking)
  MNEMENTO_TZ       user time zone (default: Asia/Seoul)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .keeper import ClaudeCLIAdapter, Keeper
from .ledger import Ledger
from .timeutil import DEFAULT_TZ

_REPO_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"

INSTRUCTIONS = """Mnemento is a shared, structured record book. Use it instead of memory files for facts.
- query: ask in natural language (or pass a QuerySpec). Answers carry evidence (record ids) and warnings.
- record: write a fact, either structured (record type + fields) or as a short text. If the Keeper is
  unsure (which record? unknown company?), it stores nothing and returns status "clarify" with options:
  ask the user, then call record again.
- list_schemas shows which record types and fields exist. Fields outside the schemas are never stored."""


def _int_env(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else None


def build_keeper() -> Keeper:
    db = os.environ.get("MNEMENTO_DB") or str(Path.home() / ".mnemento" / "mnemento.db")
    tz = os.environ.get("MNEMENTO_TZ", DEFAULT_TZ)
    ledger = Ledger.open(db, tz=tz)
    schema_dir = os.environ.get("MNEMENTO_SCHEMAS") or (str(_REPO_SCHEMAS) if _REPO_SCHEMAS.is_dir() else "")
    if schema_dir:
        ledger.schemas.load_dir(schema_dir)
    llm = None
    if os.environ.get("MNEMENTO_LLM", "claude-cli") == "claude-cli":
        llm = ClaudeCLIAdapter(model=os.environ.get("MNEMENTO_MODEL", "haiku"),
                               effort=os.environ.get("MNEMENTO_EFFORT") or None,
                               max_thinking_tokens=_int_env("MNEMENTO_THINKING_TOKENS"))
    return Keeper(ledger, llm)


def create_server(keeper: Keeper) -> MCPServer:
    mcp = MCPServer("mnemento", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool()
    def query(question: str, spec: dict[str, Any] | None = None, narrate: bool = False) -> dict[str, Any]:
        """Answer a question from the record book with evidence.

        question: natural-language question (e.g. "10/2 사람인 지원 몇 곳?").
        spec: optional structured QuerySpec; when given, no LLM is used.
        narrate: also write a prose answer with the LLM (it only sees aggregates).
        """
        return keeper.ask(question, spec=spec, narrate=narrate).to_dict()

    @mcp.tool()
    def record(
        by: str,
        entity_type: str | None = None,
        kind: str | None = None,
        payload: dict[str, Any] | None = None,
        entity_id: str | None = None,
        match: dict[str, Any] | None = None,
        at: str | None = None,
        text: str | None = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        """Record a fact. Either structured or free text.

        Structured: entity_type + kind (created | updated | status_changed | corrected | retracted)
        + payload, and the target by entity_id or match (field values, e.g. {"platform": "saramin",
        "posting_id": "10000001"}). Reference fields may hold a name (e.g. a company name).
        Free text: text="사람인 10000001 가상테크 열람" — the Keeper structures it.
        at: when it happened (ISO 8601 with offset); defaults to now. by: your agent name.
        evidence: why you believe it (quote, mail subject...).
        Returns status recorded | clarify (ask the user, nothing stored) | rejected | error.
        """
        if text:
            return keeper.record_text(text, by=by, evidence=evidence).to_dict()
        if not entity_type or not kind:
            raise ToolError("give either text, or entity_type and kind")
        req = {"entity_type": entity_type, "kind": kind, "payload": payload or {},
               "entity_id": entity_id, "match": match or {}, "at": at}
        return keeper.record({k: v for k, v in req.items() if v is not None}, by=by, evidence=evidence).to_dict()

    @mcp.tool()
    def get_entity(entity_id: str) -> dict[str, Any]:
        """Current state of one record (after corrections/retractions)."""
        e = keeper.get_entity(entity_id)
        if e is None:
            raise ToolError(f"no such record: {entity_id}")
        return e

    @mcp.tool()
    def history(entity_id: str) -> dict[str, Any]:
        """Every event of one record, including corrections and retractions, in recording order."""
        events = keeper.history(entity_id)
        if not events:
            raise ToolError(f"no such record: {entity_id}")
        return {"entity_id": entity_id, "events": events}

    @mcp.tool()
    def list_schemas(name: str | None = None) -> dict[str, Any]:
        """Record types with their fields, descriptions and allowed values."""
        return {"schemas": keeper.list_schemas(name)}

    @mcp.tool()
    def propose_schema(entity_type: str | None = None, samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Schema proposals (never applied automatically).

        Without samples: additive extensions for fields that write requests keep using.
        With samples and a new entity_type: a first schema draft for a new kind of record.
        """
        try:
            return {"proposals": keeper.propose_schema(entity_type, samples)}
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return mcp


def main() -> None:
    create_server(build_keeper()).run("stdio")


if __name__ == "__main__":
    main()
