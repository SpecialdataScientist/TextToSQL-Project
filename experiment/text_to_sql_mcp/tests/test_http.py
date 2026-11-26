"""Exercise real HTTP processes using the SDK client, including MCP sessions."""

import asyncio
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest

from column_mcp.config import DEFAULT_ALLOWED_HOSTS, DEFAULT_ALLOWED_ORIGINS, PROJECT_ROOT


@asynccontextmanager
async def http_transport(url):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10, read=30), follow_redirects=True, trust_env=False,
    ) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            yield streams


@contextmanager
def running_http_server(script):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {name: value for name, value in os.environ.items() if not name.startswith(("DB_", "MCP_"))}
    env.update({
        "DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "", "DB_USER": "", "DB_PASSWORD": "",
        "DB_SCHEMA": "public", "DB_IDENTIFIER_CASE": "lower", "DB_SSLMODE": "prefer",
        "DB_CONNECT_TIMEOUT": "10", "DB_STATEMENT_TIMEOUT_MS": "30000",
        "MCP_HOST": "127.0.0.1", "MCP_PORT": str(port),
        "MCP_ALLOWED_HOSTS": ",".join(DEFAULT_ALLOWED_HOSTS),
        "MCP_ALLOWED_ORIGINS": ",".join(DEFAULT_ALLOWED_ORIGINS),
        "PYTHONPATH": str(PROJECT_ROOT),
    })
    with tempfile.TemporaryFile(mode="w+t") as log:
        process = subprocess.Popen(
            [sys.executable, str(script)], cwd=PROJECT_ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("HTTP server did not become ready within 20 seconds")
            if process.poll() is not None:
                log.seek(0)
                pytest.fail("HTTP server exited during startup:\n" + log.read())
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.fixture(scope="module")
def http_servers():
    with ExitStack() as stack:
        yield {
            "real": stack.enter_context(running_http_server(PROJECT_ROOT / "server.py")),
            "fixture": stack.enter_context(running_http_server(PROJECT_ROOT / "tests/http_fixture_server.py")),
        }


@pytest.mark.anyio
async def test_http_initialization_listing_and_missing_credentials(http_servers):
    async with http_transport(http_servers["real"]) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "PostgreSQL Column Query"
            assert get_session_id()
            listed = await session.list_tools()
            assert len(listed.tools) == 1530
            response = await session.call_tool("get_fclt_org_dlc_key_dlc_key", {})
            assert response.isError
            assert "DB_NAME" in response.content[0].text
            invalid_filter = await session.call_tool("get_fclt_org_dlc_key_dlc_key", {"filters": {"UNKNOWN": 1}})
            assert invalid_filter.isError
            assert "등록된 컬럼명" in invalid_filter.content[0].text
            invalid_input = await session.call_tool("get_fclt_org_dlc_key_dlc_key", {"limit": 0})
            assert invalid_input.isError
            assert "limit" in invalid_input.content[0].text


@pytest.mark.anyio
async def test_http_successful_structured_tool_call(http_servers):
    async with http_transport(http_servers["fixture"]) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, table, column in [
                ("get_fclt_org_dlc_key_dlc_key", "FCLT_ORG_DLC_KEY", "DLC_KEY"),
                ("get_space_supervisor_usage_sqft", "SPACE_SUPERVISOR_USAGE", "SQFT"),
            ]:
                result = await session.call_tool(name, {"filters": {column: "selected"}, "limit": 3, "distinct": True})
                assert not result.isError
                assert result.structuredContent == {
                    "table": table, "column": column, "values": ["selected"], "returned_count": 1,
                    "limit": 3, "offset": 0, "distinct": True, "has_more": False,
                }


@pytest.mark.anyio
async def test_http_clients_have_independent_sessions(http_servers):
    async with AsyncExitStack() as stack:
        sessions = []
        session_ids = []
        for _ in range(2):
            read, write, get_session_id = await stack.enter_async_context(
                http_transport(http_servers["fixture"]),
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions.append(session)
            session_ids.append(get_session_id())
        assert all(session_ids) and session_ids[0] != session_ids[1]
        results = await asyncio.gather(*(
            session.call_tool("get_fclt_org_dlc_key_dlc_key", {"filters": {"DLC_KEY": value}})
            for session, value in zip(sessions, ("first-client", "second-client"))
        ))
        assert [result.structuredContent["values"] for result in results] == [["first-client"], ["second-client"]]


@pytest.mark.anyio
@pytest.mark.parametrize("headers,status", [
    ({}, 200),
    ({"Host": "mcp-server:8000"}, 200),
    ({"Host": "untrusted.internal:8000"}, 421),
    ({"Origin": "http://localhost:3000"}, 200),
    ({"Origin": "http://127.0.0.1:3000"}, 200),
    ({"Origin": "https://untrusted.internal"}, 403),
    ({"Host": "mcp-server:8000", "Origin": "https://untrusted.internal"}, 403),
])
async def test_http_host_and_origin_validation(http_servers, headers, status):
    headers = {"Accept": "application/json, text/event-stream", **headers}
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.post(http_servers["fixture"], headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        assert response.status_code == status
        if status == 200:
            await client.delete(http_servers["fixture"], headers={
                **headers, "Mcp-Session-Id": response.headers["Mcp-Session-Id"],
            })
