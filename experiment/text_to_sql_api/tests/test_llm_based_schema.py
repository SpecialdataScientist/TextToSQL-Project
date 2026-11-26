import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import AppSettings, DEFAULT_LLM_MODEL
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import SchemaTableSelectionOutput
from app.schemas.request import LlmBasedGenerateSqlRequest, LlmBasedSchemaRequest
from app.services import llm_based_schema_service as service_module
from app.services.llm_based_schema_service import LlmBasedSchemaService
from app.services.llm_client import create_llm_client
from main import create_app


ENDPOINT = "/retrieve/llm_based_schema"
QUERY = "그중 사용 중인 공간의 캠퍼스 정보를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}
    for name in ("ROOM", "BUILDING", "CAMPUS")
]
HISTORY = [
    {"role": "user", "content": "제외할 오래된 질문"},
    {"role": "assistant", "content": "제외할 오래된 답변"},
    {"role": "user", "content": "이전 대상"},
    {"role": "assistant", "content": "공간 정보를 조회합니다."},
    {"role": "user", "content": "사용 중인 공간만 보고 싶어"},
    {"role": "assistant", "content": "사용 여부를 필터로 적용합니다."},
]


def completion(names=("CAMPUS", "BUILDING", "ROOM", "CAMPUS")):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": "현재 질의에 필요"} for name in names]},
        ))],
        usage=SimpleNamespace(prompt_tokens=150, completion_tokens=30),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.started = None
        self.gate = None
        self.error = None
        self.result = completion()
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def parse(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.started is not None:
            self.started.set()
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps(TABLES, ensure_ascii=False), encoding="utf-8")
    # Retrieval must work even when unrelated storage, embedding and OpenAI settings are absent.
    return AppSettings(
        dataset_path=str(source), llm_api_base="https://llm.test/v1", model_name=DEFAULT_LLM_MODEL,
        rdf_triplet="", rdf_triplet_open_ai="", embedded_schema="", embedded_schema_open_ai="",
        embedding_api_base="", openai_api_key="",
    )


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: fake)
    return fake


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings)) as client:
        yield client


def snapshot(settings):
    return {str(path): path.read_bytes() for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


def expected_data(names):
    by_name = {table["table_name"]: table for table in TABLES}
    return {key: [by_name[name][key] for name in names] for key in ("table_name", "schema", "description")}


def test_one_llm_call_uses_full_catalog_and_returns_ordered_originals(client, settings, llm):
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["CAMPUS", "BUILDING", "ROOM"])
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert body["meta_data"] == [{
        "model_name": DEFAULT_LLM_MODEL, "question_token": 150, "answer_token": 30,
        "table_count": 3, "retrieved_count": 3,
    }]
    call, = llm.calls
    assert call["response_format"] is SchemaTableSelectionOutput
    assert call["model"] == DEFAULT_LLM_MODEL and call["temperature"] == 0
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert len(call["messages"]) == 2
    assert json.loads(call["messages"][-1]["content"]) == {"query": QUERY, "catalog": TABLES}
    instructions = call["messages"][0]["content"]
    assert "현재 질의에서 조건을 변경하면 현재 질의를 우선" in instructions
    assert "중간 연결 테이블" in instructions
    assert llm.closed == 1 and snapshot(settings) == before
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize("count", [0, 1, 3, 4, 6])
def test_uses_last_three_history_messages_and_current_query_last(client, llm, count):
    query = f"  {QUERY}  "
    history = HISTORY[:count]
    assert client.post(ENDPOINT, json={"query": query, "history": history}).status_code == 200
    messages = llm.calls[0]["messages"]
    assert messages[1:-1] == history[-3:]
    assert messages[-1]["role"] == "user"
    assert json.loads(messages[-1]["content"])["query"] == query


def test_history_does_not_leak_between_requests(client, llm):
    for history in (HISTORY, None, []):
        payload = {"query": QUERY}
        if history is not None:
            payload["history"] = history
        assert client.post(ENDPOINT, json=payload).status_code == 200
    assert len(llm.calls) == 3
    assert all(len(call["messages"]) == 2 for call in llm.calls[1:])


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_empty_results_return_parallel_empty_lists(client, settings, llm, empty_catalog):
    if empty_catalog:
        Path(settings.dataset_path).write_text("[]")
    else:
        llm.result = completion([])
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["retrieved_count"] == 0
    assert len(llm.calls) == (0 if empty_catalog else 1)
    if empty_catalog:
        assert llm.closed == 0 and response.json()["meta_data"][0]["question_token"] == 0


@pytest.mark.parametrize("usage", [None, SimpleNamespace(prompt_tokens=None, completion_tokens=None)])
def test_missing_usage_is_zero(client, llm, usage):
    llm.result.usage = usage
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    meta = response.json()["meta_data"][0]
    assert meta["question_token"] == meta["answer_token"] == 0


@pytest.mark.parametrize("payload", [
    {}, {"query": ""}, {"query": " \n"}, {"query": 123}, {"query": True}, {"query": None},
    {"query": QUERY, "top_k": 2}, {"query": QUERY, "schema": {}},
    {"query": QUERY, "history": None}, {"query": QUERY, "history": "invalid"},
    {"query": QUERY, "history": [None]}, {"query": QUERY, "history": [{}]},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "history": [{"role": "user"}]},
    {"query": QUERY, "history": [{"role": "assistant", "content": " "}]},
    {"query": QUERY, "history": [{"role": "user", "content": 123}]},
    {"query": QUERY, "history": [{"role": "system", "content": "old invalid message"}, *HISTORY[-3:]]},
])
def test_invalid_requests_are_422_without_llm(client, llm, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert llm.calls == []


@pytest.mark.parametrize("damage", ["missing", "json", "schema", "duplicate"])
def test_bad_catalog_is_500_before_llm(client, settings, llm, damage):
    source = Path(settings.dataset_path)
    if damage == "missing":
        source.unlink()
    elif damage == "json":
        source.write_text("invalid json")
    elif damage == "schema":
        source.write_text('[{"table_name":"A"}]')
    else:
        source.write_text(json.dumps([TABLES[0], TABLES[0]]))
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 500 and response.json()["data"] is None
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("field,value", [
    ("llm_api_base", ""), ("llm_api_base", "invalid-url"), ("model_name", " "), ("dataset_path", ""),
])
def test_bad_configuration_is_500_before_llm(client, settings, llm, field, value):
    setattr(settings, field, value)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 500 and llm.calls == []


def test_configuration_is_validated_even_with_empty_catalog(client, settings, llm):
    Path(settings.dataset_path).write_text("[]")
    settings.llm_api_base = ""
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 500
    assert llm.calls == []


@pytest.mark.parametrize("failure", [
    "api", "no_choices", "refusal", "length", "no_parsed", "malformed", "unknown_table", "blank_reason", "padded_name",
])
def test_llm_failures_are_502_without_partial_results(client, settings, llm, failure):
    message = llm.result.choices[0].message
    if failure == "api":
        llm.error = TimeoutError("LLM server timed out")
    elif failure == "no_choices":
        llm.result.choices = []
    elif failure == "refusal":
        message.refusal = "refused"
    elif failure == "length":
        llm.result.choices[0].finish_reason = "length"
    elif failure == "no_parsed":
        message.parsed = None
    elif failure == "malformed":
        message.parsed = {"tables": ["ROOM"]}
    elif failure == "unknown_table":
        llm.result = completion(["ROOM", "UNKNOWN"])
    elif failure == "blank_reason":
        message.parsed["tables"][0]["reason"] = " \n"
    else:
        message.parsed["tables"][0]["table_name"] = " ROOM "
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 502
    body = response.json()
    assert body["data"] is None and body["meta_data"] == []
    assert body["status"] == "error" and body["elapsed_time"] >= 0
    assert len(llm.calls) == llm.closed == 1 and snapshot(settings) == before


def test_catalog_is_reloaded_for_each_request(client, settings, llm):
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200
    updated = copy.deepcopy(TABLES)
    updated[0]["schema"] = "CREATE TABLE ROOM (changed_id INTEGER);"
    Path(settings.dataset_path).write_text(json.dumps(updated))
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"]["schema"][-1] == updated[0]["schema"]
    assert json.loads(llm.calls[-1]["messages"][-1]["content"])["catalog"] == updated


def test_complete_large_catalog_is_passed_without_truncating(client, settings, llm):
    tables = [
        {"table_name": f"T{i}", "schema": f"CREATE TABLE T{i} (id INTEGER);", "description": f"테이블 {i}"}
        for i in range(97)
    ]
    Path(settings.dataset_path).write_text(json.dumps(tables))
    llm.result = completion(["T96"])
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"]["schema"] == [tables[-1]["schema"]]
    assert len(llm.calls) == 1
    assert json.loads(llm.calls[0]["messages"][-1]["content"])["catalog"] == tables


def test_cancelled_inference_closes_client_and_preserves_files(settings):
    fake = FakeLlm()
    service = LlmBasedSchemaService(settings, client_factory=lambda settings: fake)
    before = snapshot(settings)

    async def run():
        fake.started, fake.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.llm_based_schema(LlmBasedSchemaRequest(query=QUERY)))
        try:
            await asyncio.wait_for(fake.started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert fake.closed == 1 and snapshot(settings) == before


@pytest.mark.parametrize("status", [200, 400])
def test_real_sdk_uses_configured_llm_url_and_structured_output(settings, monkeypatch, status):
    settings.model_name = "openai/DeepSeek-V4-Flash-0731"
    monkeypatch.setenv("OPENAI_API_KEY", "unused-global-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_LLM_MODEL and body["temperature"] == 0
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(body["messages"][-1]["content"]) == {"query": QUERY, "catalog": TABLES}
        if status != 200:
            return httpx.Response(status, json={"error": {
                "message": "context length exceeded", "type": "invalid_request_error", "code": "context_length_exceeded",
            }})
        return httpx.Response(200, json={
            "id": "chatcmpl-schema", "object": "chat.completion", "created": 1, "model": DEFAULT_LLM_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"tables": [{"table_name": "ROOM", "reason": "필요"}]}),
            }}], "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
        })

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings:
                        create_llm_client(settings, http_client=http_client, max_retries=0))
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY})
    assert len(requests) == 1 and http_client.is_closed
    if status == 200:
        assert response.status_code == 200 and response.json()["data"] == expected_data(["ROOM"])
    else:
        assert response.status_code == 502 and "context length exceeded" in response.json()["msg"]


def test_openapi_exposes_endpoint_specific_models(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedSchemaRequest")
    for code in ("200", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedSchemaResponse")
    assert set(operation["responses"]) == {"200", "422", "500", "502"}
    models = spec["components"]["schemas"]
    request = models["LlmBasedSchemaRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "history"}
    assert "history[-3:]" in request["properties"]["history"]["description"]
    assert set(models["LlmBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}
    assert len(spec["paths"]) == 25


def test_logs_use_one_request_id_and_show_selection_counts(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "llm_based_schema"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"catalog_load", "llm_request", "selection_validation"} <= {r.stage for r in records}
    assert "history_count=6 used_history_count=3" in records[0].getMessage()
    assert "table_count=3 retrieved_count=3" in records[-1].getMessage()
