"""MCP server, exercised in-process with the SDK's Client."""

import anyio
import pytest
from mcp import Client

from mnemento.demo import open_demo
from mnemento.keeper import Keeper, ScriptedLLM
from mnemento.mcp_server import create_server

EXPECTED_TOOLS = {"record", "query", "get_entity", "history", "list_schemas", "propose_schema",
                  "apply_schema_proposal", "query_log", "purge_query_log", "record_series",
                  "revert_series_batch"}


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


def test_series_over_mcp():
    from bench.series_gen import WEIGHT_SCHEMA

    led = open_demo()
    led.schemas.register(WEIGHT_SCHEMA)
    srv = create_server(Keeper(led, ScriptedLLM()))
    pts = [{"person": "가상인", "measured_on": f"2026-07-0{d}", "kg": 70 + d / 10} for d in range(1, 4)]

    async def go():
        async with Client(srv) as c:
            bad = await c.call_tool("record_series", {"by": "a", "entity_type": "weight", "source": "scale",
                                                      "points": [{"person": "가상인", "kg": 70}]})
            ok = await c.call_tool("record_series", {"by": "a", "entity_type": "weight", "source": "scale",
                                                     "points": pts})
            q = await c.call_tool("query", {"question": "q", "spec": {
                "source": "series", "entity_type": "weight", "mode": "count"}})
            rv = await c.call_tool("revert_series_batch", {"batch_id": ok.structured_content["batch_id"]})
            q2 = await c.call_tool("query", {"question": "q", "spec": {
                "source": "series", "entity_type": "weight", "mode": "count"}})
            again = await c.call_tool("revert_series_batch", {"batch_id": ok.structured_content["batch_id"]})
            return bad, ok, q, rv, q2, again

    bad, ok, q, rv, q2, again = run(go)
    assert bad.structured_content["batch_id"] is None and bad.structured_content["rejected"]
    assert ok.structured_content["inserted"] == 3
    assert q.structured_content["result"]["total"] == 3
    assert rv.structured_content["reverted_points"] == 3
    assert q2.structured_content["result"]["total"] == 0
    assert again.is_error
    led.close()
