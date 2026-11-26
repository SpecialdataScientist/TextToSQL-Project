import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import SchemaTableSelectionOutput
from app.schemas.request import LlmBasedGenerateSqlRequest, LlmBasedSchemaOpenAiRequest
from app.schemas.response import LlmBasedSchemaOpenAiData, LlmBasedSchemaOpenAiResponse
from app.services import llm_based_schema_service as service_module, open_ai_clients
from main import create_app


ENDPOINT = "/retrieve/llm_based_schema_open_ai"
FAKE_KEY = "test-openai-llm-schema-key"
QUERY = "그중 사용 중인 공간의 캠퍼스를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}
    for name in ("ROOM", "BUILDING", "CAMPUS")
]
HISTORY = [
    {"role": "user", "content": "제외할 오래된 질문"},
    {"role": "assistant", "content": "공간 정보를 조회합니다."},
    {"role": "user", "content": "사용 중인 공간만 보고 싶어"},
    {"role": "assistant", "content": "사용 여부를 필터로 적용합니다."},
]


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.error = None
        self.started = None
        self.gate = None
        self.result = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                refusal=None, parsed={"tables": [
                    {"table_name": name, "reason": "현재 질의에 필요"}
                    for name in ("CAMPUS", "BUILDING", "ROOM", "CAMPUS")
                ]},
            ))],
            usage=SimpleNamespace(prompt_tokens=150, completion_tokens=30),
        )
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
    return AppSettings(
        dataset_path=str(source), openai_api_key=FAKE_KEY, openai_llm_model_name="gpt-6-luna",
        llm_api_base="invalid-local-url", model_name="unused-local-model", embedding_api_base="",
        rdf_triplet="", rdf_triplet_open_ai="", embedded_schema="", embedded_schema_open_ai="",
    )


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile: fake)

    def reject_local_client(*args, **kwargs):
        raise AssertionError("OpenAI retrieval must use its dedicated client")

    monkeypatch.setattr(service_module, "create_llm_client", reject_local_client)
    return fake


def invoke(settings, payload=None):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT, json={"query": QUERY} if payload is None else payload)


def snapshot(settings):
    return {str(path): path.read_bytes() for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


@pytest.mark.parametrize("history", [None, [], HISTORY])
def test_single_open_ai_call_selects_from_all_originals_without_indexes(settings, llm, history):
    before = snapshot(settings)
    payload = {"query": QUERY}
    if history is not None:
        payload["history"] = history
    response = invoke(settings, payload)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {key: [table[key] for table in reversed(TABLES)] for key in ("table_name", "schema", "description")}
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert body["meta_data"] == [{
        "model_name": "gpt-6-luna", "question_token": 150, "answer_token": 30,
        "table_count": 3, "retrieved_count": 3,
    }]
    call, = llm.calls
    assert call["response_format"] is SchemaTableSelectionOutput
    assert call["model"] == "gpt-6-luna" and call["reasoning_effort"] == "none"
    assert "temperature" not in call and "extra_body" not in call
    assert call["messages"][1:-1] == (history or [])[-3:]
    assert json.loads(call["messages"][-1]["content"]) == {"query": QUERY, "catalog": TABLES}
    assert llm.closed == 1 and snapshot(settings) == before
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


def test_service_returns_dedicated_response_types(settings, llm):
    service = create_app(settings).state.services.llm_based_schema_open_ai_service
    result = asyncio.run(service.llm_based_schema(LlmBasedSchemaOpenAiRequest(query=QUERY)))
    assert type(result) is LlmBasedSchemaOpenAiResponse
    assert type(result.data) is LlmBasedSchemaOpenAiData


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_empty_results_skip_unneeded_calls(settings, llm, empty_catalog):
    if empty_catalog:
        Path(settings.dataset_path).write_text("[]")
    else:
        llm.result.choices[0].message.parsed = {"tables": []}
    response = invoke(settings)
    assert response.status_code == 200
    assert response.json()["data"] == {"table_name": [], "schema": [], "description": []}
    assert len(llm.calls) == (0 if empty_catalog else 1)
    if empty_catalog:
        assert response.json()["meta_data"][0]["question_token"] == 0


@pytest.mark.parametrize("field", ["openai_api_key", "openai_llm_model_name"])
@pytest.mark.parametrize("empty_catalog", [False, True])
def test_missing_key_or_model_is_checked_before_catalog_read(settings, llm, monkeypatch, field, empty_catalog):
    if empty_catalog:
        Path(settings.dataset_path).write_text("[]")
    setattr(settings, field, " ")

    def reject_load(*args):
        raise AssertionError("Configuration must be checked before loading")

    monkeypatch.setattr(service_module.LlmBasedSchemaService, "_load_tables", staticmethod(reject_load))
    response = invoke(settings)
    assert response.status_code == 500 and field.upper() in response.json()["msg"]
    assert llm.calls == []


@pytest.mark.parametrize("damage", ["missing", "malformed", "duplicate"])
def test_invalid_catalog_is_500_without_llm(settings, llm, damage):
    source = Path(settings.dataset_path)
    if damage == "missing":
        source.unlink()
    elif damage == "malformed":
        source.write_text("{}")
    else:
        source.write_text(json.dumps([TABLES[0], TABLES[0]]))
    assert invoke(settings).status_code == 500 and llm.calls == []


@pytest.mark.parametrize("payload", [
    {}, {"query": ""}, {"query": " \n"}, {"query": 123}, {"query": None},
    {"query": QUERY, "top_k": 2}, {"query": QUERY, "schema": {}},
    {"query": QUERY, "history": None},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "history": [{"role": "user", "content": " "}]},
])
def test_invalid_requests_are_422_without_llm(settings, llm, payload):
    assert invoke(settings, payload).status_code == 422 and llm.calls == []


@pytest.mark.parametrize("failure", ["api", "no_choices", "refusal", "length", "malformed", "unknown_table"])
def test_failed_selection_returns_502_without_partial_results(settings, llm, failure):
    message = llm.result.choices[0].message
    if failure == "api":
        llm.error = TimeoutError("OpenAI request timed out")
    elif failure == "no_choices":
        llm.result.choices = []
    elif failure == "refusal":
        message.refusal = "refused"
    elif failure == "length":
        llm.result.choices[0].finish_reason = "length"
    elif failure == "malformed":
        message.parsed = {"tables": ["ROOM"]}
    else:
        message.parsed["tables"].append({"table_name": "UNKNOWN", "reason": "invalid"})
    before = snapshot(settings)
    response = invoke(settings)
    assert response.status_code == 502
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(llm.calls) == llm.closed == 1 and snapshot(settings) == before


@pytest.mark.parametrize("failure", ["api", "validation"])
def test_api_key_is_masked_in_responses_and_formatted_tracebacks(settings, llm, caplog, failure):
    if failure == "api":
        llm.error = RuntimeError(f"Rejected key: {FAKE_KEY}")
    else:
        llm.result.choices[0].message.parsed["tables"][0]["table_name"] = FAKE_KEY
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = invoke(settings)
        assert response.status_code == 502
        assert FAKE_KEY not in response.text and "[REDACTED]" in response.json()["msg"]
        assert FAKE_KEY not in caplog.text
        console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("status", [200, 401, 429])
def test_real_sdk_uses_official_url_key_model_and_schema(settings, monkeypatch, status):
    factory = open_ai_clients.create_open_ai_llm_client
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-global-key")
    settings.openai_llm_model_name = "openai/gpt-6-luna"
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none"
        assert "temperature" not in body and "thinking" not in body
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(body["messages"][-1]["content"]) == {"query": QUERY, "catalog": TABLES}
        if status != 200:
            return httpx.Response(status, json={"error": {
                "message": f"Rejected key: {FAKE_KEY}", "type": "invalid_request_error", "code": "test",
            }})
        return httpx.Response(200, json={
            "id": "chatcmpl-schema", "object": "chat.completion", "created": 1, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"tables": [{"table_name": "ROOM", "reason": "필요"}]}),
            }}], "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
        })

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile: factory(
        profile, http_client=http_client, max_retries=0,
    ))
    response = invoke(settings, {"query": QUERY, "history": HISTORY})
    assert len(requests) == 1 and http_client.is_closed and FAKE_KEY not in response.text
    if status == 200:
        assert response.status_code == 200 and response.json()["data"]["schema"] == [TABLES[0]["schema"]]
    else:
        assert response.status_code == 502 and response.json()["data"] is None


def test_cancelled_inference_closes_client_without_changing_files(settings, llm):
    service = create_app(settings).state.services.llm_based_schema_open_ai_service
    before = snapshot(settings)

    async def run():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.llm_based_schema(LlmBasedSchemaOpenAiRequest(query=QUERY)))
        try:
            await asyncio.wait_for(llm.started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert llm.closed == 1 and snapshot(settings) == before


def test_dedicated_openapi_and_legacy_search_without_open_ai_key(settings, monkeypatch):
    settings.openai_api_key = ""
    settings.llm_api_base = "https://local.test/v1"
    settings.model_name = "local-model"
    fake = FakeLlm()
    monkeypatch.setattr(service_module, "create_llm_client", lambda profile: fake)
    with TestClient(create_app(settings)) as client:
        assert client.post("/retrieve/llm_based_schema", json={"query": QUERY}).status_code == 200
        spec = client.get("/openapi.json").json()
    assert fake.calls[0]["model"] == "local-model" and fake.calls[0]["temperature"] == 0
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedSchemaOpenAiRequest")
    for code in ("200", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedSchemaOpenAiResponse")
    assert set(operation["responses"]) == {"200", "422", "500", "502"}
    models = spec["components"]["schemas"]
    request = models["LlmBasedSchemaOpenAiRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "history"}
    assert set(models["LlmBasedSchemaOpenAiData"]["properties"]) == {"table_name", "schema", "description"}
    assert len(spec["paths"]) == 25


def test_logs_distinguish_open_ai_retrieval(settings, llm, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert invoke(settings, {"query": QUERY, "history": HISTORY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "llm_based_schema_open_ai"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert "history_count=4 used_history_count=3" in records[0].getMessage()
