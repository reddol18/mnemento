"""MCP server, exercised in-process with the SDK's Client."""

import anyio
import pytest
from mcp import Client

from mnemento.demo import open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.mcp_server import create_server

EXPECTED_TOOLS = {"record", "query", "get_entity", "history", "list_schemas", "propose_schema",
                  "apply_schema_proposal", "query_log", "purge_query_log"}


@pytest.fixture
def server():
    led = open_demo()
    yield create_server(Keeper(led, ScriptedLLM()))
    led.close()


def run(coro_fn):
    return anyio.run(coro_fn)


def test_tools_listed(server):
    async def go():
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert EXPECTED_TOOLS <= run(go)


def test_query_and_record_over_mcp(server):
    async def go():
        async with Client(server) as c:
            q = await c.call_tool("query", {"question": "app_o02"})
            r = await c.call_tool("record", {
                "by": "agent_a", "entity_type": "application", "kind": "status_changed",
                "entity_id": "app_o05", "payload": {"to": "viewed"}, "at": "2026-10-03T10:00:00+09:00"})
            h = await c.call_tool("history", {"entity_id": "app_o05"})
            e = await c.call_tool("get_entity", {"entity_id": "nope"})
            s = await c.call_tool("list_schemas", {"name": "company"})
            return q, r, h, e, s

    q, r, h, e, s = run(go)
    assert q.structured_content["status"] == "answered"
    assert q.structured_content["result"]["entity"]["company_id"] == "co_gasangtech"
    assert r.structured_content["status"] == "recorded"
    assert [ev["kind"] for ev in h.structured_content["events"]][-1] == "status_changed"
    assert e.is_error
    assert s.structured_content["schemas"][0]["name"] == "company"
