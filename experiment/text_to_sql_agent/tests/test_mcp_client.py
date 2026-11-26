from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp.types import Tool
import pytest

from agent_server import mcp_client
from agent_server.config import Settings
from agent_server.errors import AgentError, ToolInputError
from agent_server.mcp_client import McpGateway, connect_mcp
from agent_server.schemas import CallArguments, SearchArguments


@pytest.mark.anyio
async def test_real_paginated_catalog_search_and_last_page_call(mcp_url):
    async with connect_mcp(Settings(mcp_url=mcp_url)) as gateway:
        assert len(gateway.tools) == 163
        first = gateway.search(SearchArguments(query="sample", limit=2))
        assert [tool["name"] for tool in first["tools"]] == ["get_sample_000", "get_sample_001"]
        assert first["total_matches"] == 160
        assert first["has_more"] and first["next_offset"] == 2
        second = gateway.search(SearchArguments(query="SAMPLE", limit=2, offset=2))
        assert [tool["name"] for tool in second["tools"]] == ["get_sample_002", "get_sample_003"]
        empty = gateway.search(SearchArguments(query="아무것도일치하지않음"))
        assert empty == {"tools": [], "total_matches": 0, "has_more": False, "next_offset": None}
        assert gateway.search(SearchArguments(query=""))["total_matches"] == 163
        assert gateway.search(SearchArguments(query="DLC"))["tools"][0]["name"] == "get_fclt_org_dlc_key_dlc_key"
        result, is_error = await gateway.call(CallArguments(name="get_z_page_last", arguments={"value": "last-page"}))
        assert not is_error
        assert result["content"][0]["text"] == "last-page"


@pytest.mark.anyio
async def test_schema_rejection_never_calls_mcp():
    class NeverCalled:
        async def call_tool(self, *_args):
            pytest.fail("invalid input must not be forwarded")

    gateway = McpGateway(NeverCalled(), [Tool(
        name="registered", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1}},
                                      "additionalProperties": False},
    )])
    for name, arguments in [("unknown", {}), ("registered", {"limit": "1"}), ("registered", {"limit": 0})]:
        with pytest.raises(ToolInputError):
            await gateway.call(CallArguments(name=name, arguments=arguments))


@pytest.mark.anyio
async def test_repeated_cursor_is_rejected(monkeypatch):
    class FakeSession:
        def __init__(self, *_args, **_kwargs):
            self.cursors = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def initialize(self):
            return None

        async def list_tools(self, cursor=None):
            self.cursors.append(cursor)
            return SimpleNamespace(tools=[], nextCursor="repeat")

    @asynccontextmanager
    async def fake_transport(*_args, **_kwargs):
        yield (None, None, None)

    monkeypatch.setattr(mcp_client, "ClientSession", FakeSession)
    monkeypatch.setattr(mcp_client, "streamable_http_client", fake_transport)
    with pytest.raises(AgentError, match="커서"):
        async with connect_mcp(Settings()):
            pytest.fail("repeated cursors must fail during listing")
