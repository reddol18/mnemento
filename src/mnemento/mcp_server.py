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
  MNEMENTO_QUERY_LOG  off | on (default) | <max rows> — local log of every question (ADR-0015)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .keeper import ClaudeCLIAdapter, Keeper
from .ledger import Ledger
from .timeutil import DEFAULT_TZ

_REPO_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"

INSTRUCTIONS = """Mnemento is a shared, structured record book. Use it instead of memory files for facts.
- query: ask in natural language (or pass a QuerySpec). Answers carry evidence (record ids) and warnings,
  including how many records lack a field the question uses.
- record: write a fact, either structured (record type + fields) or as a short text. Fields and values the
  dictionary does not know yet are stored as given and can be queried right away (unregistered "drafts").
  If the Keeper is unsure which record is meant (several matches, an unknown or similar company name, a
  contradiction with the current state), it stores nothing and returns "clarify" with options: ask the user,
  then call record again. A reply may also carry `questions` ("is applicant_count the same as applicants?").
- list_schemas shows the registered fields and the drafts. Organizing drafts (descriptions, labels, indexes,
  merging look-alike names) is done with propose_schema and then apply_schema_proposal — the latter only after
  the user explicitly agrees.
- query_log looks back at earlier questions: the SQL that answered them, warnings, errors, and questions whose
  interpretation changed between askings (diverging). It is kept in the local database only."""


def _int_env(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else None


def _client_name(ctx: Context | None) -> str | None:
    try:
        info = ctx.request_context.session.client_params.client_info  # type: ignore[union-attr]
        return f"{info.name}/{info.version}" if getattr(info, "version", None) else info.name
    except Exception:  # noqa: BLE001 — no request context, or the client did not say
        return None


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
    def query(question: str, spec: dict[str, Any] | None = None, narrate: bool = False,
              by: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """Answer a question from the record book with evidence.

        question: natural-language question (e.g. "10/2 사람인 지원 몇 곳?").
        spec: optional structured QuerySpec; when given, no LLM is used.
        narrate: also write a prose answer with the LLM (it only sees aggregates).
        by: your agent name (optional; kept in the local query log).
        """
        return keeper.ask(question, spec=spec, narrate=narrate, caller=by or _client_name(ctx)).to_dict()

    @mcp.tool()
    def query_log(view: str = "recent", n: int = 20, since: str | None = None,
                  path: str | None = None) -> dict[str, Any]:
        """Look back at earlier questions: interpretation, QuerySpec, SQL and parameters, result count,
        evidence ids, warnings, stage times, tokens and cost.

        view: recent (last n) | warnings (answers that carried warnings) | errors |
              diverging (the same question pattern answered with different SQL — the interpretation moved) |
              path (only questions answered by `path`: fast | cache | llm | structured | entity | none).
        since: only entries asked at or after this time (ISO 8601).
        """
        try:
            return keeper.query_log(view, n=n, since=since, path=path)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def purge_query_log(before: str | None = None) -> dict[str, Any]:
        """Delete query log entries asked before `before` (ISO 8601), or all of them when omitted.
        Only when the user asks for it. Recorded facts are not touched."""
        return {"deleted": keeper.purge_query_log(before)}

    @mcp.tool()
    def record(
        by: str,
        entity_type: str | None = None,
        kind: str | None = None,
        payload: dict[str, Any] | None = None,
        entity_id: str | None = None,
        match: dict[str, Any] | None = None,
        at: str | None = None,
        at_precision: str = "time",
        text: str | None = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        """Record a fact. Either structured or free text.

        Structured: entity_type + kind (created | updated | status_changed | corrected | retracted)
        + payload, and the target by entity_id or match (field values, e.g. {"platform": "saramin",
        "posting_id": "10000001"}). Reference fields may hold a name (e.g. a company name).
        Free text: text="사람인 10000001 가상테크 열람" — the Keeper structures it.
        at: when it happened (ISO 8601 with offset); defaults to now. at_precision: time | date (only the
        day is known) | unknown. by: your agent name.
        evidence: why you believe it (quote, mail subject...).
        Fields or values the dictionary does not know are stored as given (drafts, queryable at once); the
        reply lists them under `drafts` and may ask under `questions` whether a new name means an existing one.
        Returns status:
          recorded — stored (check `drafts` and `questions`);
          clarify  — nothing stored: the target is ambiguous (several matches), a referenced name (e.g. a
                     company) is unknown or only similar, the record looks like a duplicate, or it contradicts
                     the current state; ask the user and call again;
          rejected — nothing stored: a registered field is violated (required field missing, wrong type, bad
                     date) or the target was looked up by an unregistered field;
          error    — free text could not be structured (no LLM configured or the call failed).
        """
        if text:
            return keeper.record_text(text, by=by, evidence=evidence).to_dict()
        if not entity_type or not kind:
            raise ToolError("give either text, or entity_type and kind")
        req = {"entity_type": entity_type, "kind": kind, "payload": payload or {},
               "entity_id": entity_id, "match": match or {}, "at": at, "at_precision": at_precision}
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
        """Record types with their fields, descriptions and allowed values, plus drafts (unregistered fields
        and values already in use) and the history of organize approvals."""
        return {"schemas": keeper.list_schemas(name)}

    @mcp.tool()
    def apply_schema_proposal(
        proposal_id: str,
        approved_by: str,
        user_answer: str,
        descriptions: dict[str, str] | None = None,
        labels: dict[str, dict[str, list[str]]] | None = None,
        merges: dict[str, str] | None = None,
        index: list[str] | None = None,
    ) -> dict[str, Any]:
        """Organize unregistered (draft) fields and values — ONLY with the user's explicit consent.

        Never call this on your own initiative. First show the user the proposal from propose_schema (its
        `question`), ask whether to apply it, and call this only after they answer yes. Pass who approved
        (approved_by) and the user's own words (user_answer); both are kept in the schema history.
        descriptions: {field: description} for every field being registered (required).
        labels: {field: {value: [natural-language names]}} for every new enum value (required, e.g.
        {"platform": {"remember": ["리멤버"]}}).
        merges: {draft_field: target_field} to fold look-alike names together (values move with history kept).
        index: fields to index. The data itself was already stored when it was written; this only organizes it.
        """
        try:
            return keeper.apply_schema_proposal(proposal_id, approved_by=approved_by, user_answer=user_answer,
                                                descriptions=descriptions, labels=labels, merges=merges, index=index)
        except Exception as exc:  # SchemaDefinitionError, BreakingSchemaChangeError
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def propose_schema(entity_type: str | None = None, samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Schema proposals (never applied automatically).

        Without samples: how to organize drafts — fields and enum values that records already use but the
        dictionary has not registered (they are stored and queryable; organizing adds descriptions, labels,
        indexes, and merges look-alike names). Ask the user before apply_schema_proposal.
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
