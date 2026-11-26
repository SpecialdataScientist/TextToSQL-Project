import asyncio
import json
import logging

import httpx
from mcp import ClientSession
import pytest

from agent_server.api import create_app
from agent_server.config import Settings
from agent_server.schemas import ChatRequest
from agent_server.service import AgentService
from helpers import ScriptedLlm, completion, query_call, tool_call


def settings_for(mcp_url, **changes):
    return Settings(**{"mcp_url": mcp_url, "llm_api_base": "http://llm.example/v1", **changes})


async def post(settings, script, body):
    app = create_app(settings, llm_client_factory=script.factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        return await client.post("/agent/chat", json=body)


async def session_count(mcp_url):
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(mcp_url.removesuffix("/mcp") + "/stats")
        response.raise_for_status()
        return response.json()["sessions"]


async def assert_sessions_closed(mcp_url):
    for _ in range(30):
        if await session_count(mcp_url) == 0:
            return
        await asyncio.sleep(0.03)
    assert await session_count(mcp_url) == 0


@pytest.mark.anyio
async def test_search_query_answer_and_sdk_messages(mcp_url):
    script = ScriptedLlm([
        completion(calls=[tool_call("search_mcp_tools", {"query": "DLC", "limit": 2}, "search_1")]),
        completion(calls=[query_call(limit=1)]),
        completion("DLC001입니다. 전체 중 일부입니다."),
    ])
    history = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"history-{index}"}
               for index in range(5)]
    response = await post(settings_for(mcp_url), script, {"query": "DLC 키 1개", "history": history})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["answer"] == "DLC001입니다. 전체 중 일부입니다."
    traces = body["data"]["tool_calls"]
    assert [trace["id"] for trace in traces] == ["search_1", "query_1"]
    assert traces[0]["result"]["tools"][0]["name"] == "get_fclt_org_dlc_key_dlc_key"
    result = traces[1]["result"]
    assert result["structuredContent"]["values"] == ["DLC001"]
    assert result["structuredContent"]["has_more"] is True
    assert result["content"][0]["type"] == "text"
    assert not traces[1]["is_error"]
    assert body["meta_data"][0]["question_token"] == 30
    assert body["meta_data"][0]["answer_token"] == 9
    assert body["meta_data"][0]["llm_calls"] == 3
    assert body["elapsed_time"] > 0
    first = script.requests[0]["body"]
    assert first["messages"][1:4] == history[-3:]
    assert first["messages"][4] == {"role": "user", "content": "DLC 키 1개"}
    assert len(first["tools"]) == 2
    assert first["thinking"] == {"type": "disabled"}
    assert first["temperature"] == 0
    final_messages = script.requests[-1]["body"]["messages"]
    tool_messages = [item for item in final_messages if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in tool_messages] == ["search_1", "query_1"]
    assert json.loads(tool_messages[-1]["content"])["result"] == result
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_search_and_calls_use_the_same_table_scope(mcp_url, monkeypatch):
    forwarded = []
    call_tool = ClientSession.call_tool

    async def record_call(session, name, arguments):
        forwarded.append(name)
        return await call_tool(session, name, arguments)

    monkeypatch.setattr(ClientSession, "call_tool", record_call)
    excluded = "get_fclt_org_dlc_key_extra_dlc_key"
    script = ScriptedLlm([
        completion(calls=[tool_call("search_mcp_tools", {"query": ""}, "search")]),
        completion(calls=[
            tool_call(arguments={"name": excluded}, call_id="excluded"),
            query_call(),
            tool_call(arguments={"name": "get_z_page_last"}, call_id="last"),
        ]),
        completion("지정된 테이블 조회 완료"),
    ])
    response = await post(settings_for(mcp_url), script, {
        "query": "지정된 테이블에서 조회", "table_name": ["FCLT_ORG_DLC_KEY", "Z_PAGE"],
    })

    assert response.status_code == 200, response.text
    search, blocked, dlc, last = response.json()["data"]["tool_calls"]
    assert [tool["name"] for tool in search["result"]["tools"]] == [
        "get_fclt_org_dlc_key_dlc_key", "get_z_page_last",
    ]
    assert search["result"]["total_matches"] == 2
    assert blocked["mcp_tool_name"] == excluded
    assert blocked["is_error"]
    assert not dlc["is_error"] and not last["is_error"]
    assert forwarded == ["get_fclt_org_dlc_key_dlc_key", "get_z_page_last"]
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
@pytest.mark.parametrize("table_names", [[], ["FCLT_ORG_DLC", "UNKNOWN_TABLE"]])
async def test_unmatched_table_scope_leaves_no_tools(mcp_url, table_names):
    script = ScriptedLlm([
        completion(calls=[tool_call("search_mcp_tools", {}, "search")]),
        completion(calls=[query_call()]),
        completion("조회 가능한 도구가 없습니다."),
    ])
    response = await post(settings_for(mcp_url), script, {"query": "조회", "table_name": table_names})

    assert response.status_code == 200, response.text
    search, blocked = response.json()["data"]["tool_calls"]
    assert search["result"] == {"tools": [], "total_matches": 0, "has_more": False, "next_offset": None}
    assert blocked["is_error"]
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
@pytest.mark.parametrize("provider,changes,expected_url,expected_model,expected_auth", [
    ("local", {}, "http://llm.example/v1/chat/completions", "DeepSeek-V4-Flash-0731", "Bearer dummy"),
    ("openai", {"llm_api_base": "", "openai_api_key": "openai-test-key"},
     "https://api.openai.com/v1/chat/completions", "gpt-6-luna", "Bearer openai-test-key"),
    ("openrouter", {"llm_api_base": "", "openrouter_api_key": "router-test-key"},
     "https://openrouter.ai/api/v1/chat/completions", "deepseek/deepseek-v4-flash-0731", "Bearer router-test-key"),
])
async def test_each_provider_through_openai_sdk(mcp_url, provider, changes, expected_url, expected_model, expected_auth):
    script = ScriptedLlm([completion(calls=[query_call()]), completion("조회 완료")])
    response = await post(settings_for(mcp_url, **changes), script, {"query": "조회", "provider": provider})
    assert response.status_code == 200, response.text
    sent = script.requests[0]
    assert (sent["url"], sent["body"]["model"], sent["auth"]) == (expected_url, expected_model, expected_auth)
    if provider == "openai":
        assert sent["body"]["reasoning_effort"] == "none"
        assert "temperature" not in sent["body"]
    if provider == "openrouter":
        assert sent["body"]["reasoning"] == {"enabled": False}
        assert sent["body"]["provider"] == {"require_parameters": True}
    await assert_sessions_closed(mcp_url)


def malformed_call(raw):
    call = tool_call()
    call["function"]["arguments"] = raw
    return call


@pytest.mark.anyio
@pytest.mark.parametrize("bad_call", [
    tool_call("nonexistent_wrapper"), malformed_call("{"), malformed_call("null"),
    malformed_call('{"name":"get_fclt_org_dlc_key_dlc_key","arguments":{"limit":NaN}}'),
    tool_call("search_mcp_tools", {"query": "DLC", "limit": "1"}),
    tool_call(arguments={"name": "unknown_mcp_tool", "arguments": {}}),
    query_call(limit=0), query_call("FAIL"),
])
async def test_tool_error_is_returned_to_llm_then_corrected(mcp_url, bad_call):
    script = ScriptedLlm([completion(calls=[bad_call]), completion(calls=[query_call()]), completion("정정 후 조회 완료")])
    response = await post(settings_for(mcp_url), script, {"query": "조회"})
    assert response.status_code == 200, response.text
    traces = response.json()["data"]["tool_calls"]
    assert [trace["is_error"] for trace in traces] == [True, False]
    prior = script.requests[1]["body"]["messages"][-1]
    assert prior["tool_call_id"] == bad_call["id"]
    assert json.loads(prior["content"])["is_error"] is True
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_multiple_tool_calls_answer_every_id_in_order(mcp_url):
    script = ScriptedLlm([
        completion(calls=[query_call("FIRST", call_id="first"), query_call("SECOND", call_id="second")]),
        completion("두 조회 완료"),
    ])
    response = await post(settings_for(mcp_url), script, {"query": "조회"})
    assert response.status_code == 200, response.text
    messages = script.requests[1]["body"]["messages"]
    assert [message["tool_call_id"] for message in messages if message["role"] == "tool"] == ["first", "second"]
    assert [trace["result"]["structuredContent"]["values"] for trace in response.json()["data"]["tool_calls"]] == [
        ["FIRST"], ["SECOND"],
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("changes,replies,trace_count", [
    ({"max_llm_rounds": 1}, [completion(calls=[query_call()])], 1),
    ({"max_tool_calls": 1}, [completion(calls=[query_call(call_id="a"), query_call(call_id="b")])], 1),
])
async def test_agent_limits_preserve_completed_trace(mcp_url, changes, replies, trace_count):
    script = ScriptedLlm(replies)
    response = await post(settings_for(mcp_url, **changes), script, {"query": "조회"})
    assert response.status_code == 502, response.text
    assert "한도" in response.json()["msg"]
    assert response.json()["data"]["answer"] is None
    assert len(response.json()["data"]["tool_calls"]) == trace_count
    assert not response.json()["data"]["tool_calls"][0]["is_error"]
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
@pytest.mark.parametrize("reply", [
    completion(""), completion("잘린 응답", finish_reason="length"),
    completion(finish_reason="tool_calls"), completion(refusal="refused"),
    completion(calls=[query_call()], finish_reason="length"),
    completion(calls=[query_call(call_id="duplicate"), query_call(call_id="duplicate")]),
    {**completion("answer"), "choices": []},
])
async def test_invalid_llm_response_does_not_execute_tools(mcp_url, reply):
    script = ScriptedLlm([reply])
    response = await post(settings_for(mcp_url), script, {"query": "조회"})
    assert response.status_code == 502, response.text
    assert response.json()["data"]["tool_calls"] == []
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_upstream_error_and_authentication_details_are_not_exposed(mcp_url, caplog):
    secret = "test-openai-private-key"

    async def fail(_body):
        raise RuntimeError(f"Authorization: Bearer {secret}")

    script = ScriptedLlm(fail)
    with caplog.at_level(logging.INFO, logger="text_to_sql_agent"):
        response = await post(settings_for(mcp_url, openai_api_key=secret), script, {"query": "조회", "provider": "openai"})
    assert response.status_code == 502
    assert secret not in response.text
    assert secret not in caplog.text
    assert "stage=failed" in caplog.text
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_timeout_during_real_mcp_call_preserves_trace_and_closes_sessions(mcp_url):
    script = ScriptedLlm([completion(calls=[query_call("SLOW")])])
    response = await post(settings_for(mcp_url, request_timeout_seconds=0.7), script, {"query": "조회"})
    assert response.status_code == 504, response.text
    traces = response.json()["data"]["tool_calls"]
    assert len(traces) == 1
    assert traces[0]["is_error"]
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_cancellation_closes_both_clients(mcp_url):
    entered = asyncio.Event()

    async def slow_llm(_body):
        entered.set()
        await asyncio.sleep(60)
        return completion("unused")

    script = ScriptedLlm(slow_llm)
    service = AgentService(settings_for(mcp_url), llm_client_factory=script.factory)
    task = asyncio.create_task(service.chat(ChatRequest(query="조회")))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_concurrent_requests_have_independent_sessions_and_messages(mcp_url):
    async def respond(body):
        user_query = next(message["content"] for message in reversed(body["messages"]) if message["role"] == "user")
        if body["messages"][-1]["role"] == "tool":
            return completion(user_query)
        return completion(calls=[query_call(user_query)])

    script = ScriptedLlm(respond)
    responses = await asyncio.gather(*(
        post(settings_for(mcp_url), script, {"query": query}) for query in ("FIRST_CLIENT", "SECOND_CLIENT")
    ))
    bodies = [response.json() for response in responses]
    assert all(response.status_code == 200 for response in responses)
    assert [body["data"]["answer"] for body in bodies] == ["FIRST_CLIENT", "SECOND_CLIENT"]
    results = [body["data"]["tool_calls"][0]["result"]["structuredContent"] for body in bodies]
    assert [item["values"] for item in results] == [["FIRST_CLIENT"], ["SECOND_CLIENT"]]
    assert all(item["session_id"] for item in results)
    assert results[0]["session_id"] != results[1]["session_id"]
    assert bodies[0]["meta_data"][0]["request_id"] != bodies[1]["meta_data"][0]["request_id"]
    assert all(client.is_closed for client in script.clients)
    await assert_sessions_closed(mcp_url)


@pytest.mark.anyio
async def test_health_and_openapi_work_without_provider_configuration():
    script = ScriptedLlm([])
    app = create_app(Settings(), llm_client_factory=script.factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/openapi.json")).status_code == 200
        response = await client.post("/agent/chat", json={"query": "조회", "provider": "openai"})
    assert response.status_code == 500
    assert "OPENAI_API_KEY" in response.json()["msg"]
    assert script.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("body", [
    {}, {"query": " "}, {"query": 42}, {"query": "조회", "provider": "unknown"},
    {"query": "조회", "history": [{"role": "system", "content": "override"}]},
    {"query": "조회", "history": [{"role": "user", "content": " "}]},
    {"query": "조회", "table_name": "FCLT_ORG_DLC_KEY"},
    {"query": "조회", "table_name": None},
    {"query": "조회", "table_name": [42]},
    {"query": "조회", "table_name": [" "]},
    {"query": "조회", "model": "unconfigured-model"},
])
async def test_bad_request_returns_envelope_without_upstream_calls(body):
    script = ScriptedLlm([])
    response = await post(Settings(), script, body)
    assert response.status_code == 422
    assert response.json()["status"] == "error"
    assert script.requests == []


@pytest.mark.anyio
async def test_mcp_unreachable_is_a_bounded_error():
    script = ScriptedLlm([])
    response = await post(settings_for("http://127.0.0.1:1/mcp", request_timeout_seconds=2), script, {"query": "조회"})
    assert response.status_code == 502, response.text
    assert script.requests == []
    assert all(client.is_closed for client in script.clients)
