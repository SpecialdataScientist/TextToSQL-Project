import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core import config
from app.core.config import AppSettings, DEFAULT_LLM_MODEL
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import GeneratedSqlOutput
from app.schemas.request import LlmBasedGenerateSqlRequest
from app.services import llm_based_generate_sql_service as service_module
from app.services.llm_based_generate_sql_service import LlmBasedGenerateSqlService
from app.services.llm_client import create_llm_client
from main import create_app


ENDPOINT = "/generate_sql/llm_based_generate_sql"
QUERY = "학과별 수강 인원 합계를 조회해줘"
SQL = (
    "SELECT DEPARTMENT_NAME, SUM(SUBJECT_ENROLLMENT_NUMBER) AS TOTAL_ENROLLMENT "
    "FROM SUBJECT_SUMMARY GROUP BY DEPARTMENT_NAME;"
)
SCHEMA = {
    "table_name": ["SUBJECT_SUMMARY"],
    "schema": ["CREATE TABLE SUBJECT_SUMMARY (DEPARTMENT_NAME VARCHAR2, SUBJECT_ENROLLMENT_NUMBER NUMBER);"],
    "description": ["학과별 교과목 수강 인원 요약 정보"],
}
HISTORY = [
    {"role": "user", "content": "오래된 질문: 이전 대화의 조회 요청"},
    {"role": "assistant", "content": "  교과목의 수강 인원을 확인할 수 있습니다.  "},
    {"role": "user", "content": "학과별로 집계해줘"},
    {"role": "assistant", "content": "학과별 수강 인원 합계를 조회하겠습니다."},
]


def payload():
    return {"query": QUERY, "schema": copy.deepcopy(SCHEMA)}


def completion():
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"sql": SQL},
        ))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=35),
    )


class FakeLlmClient:
    def __init__(self):
        self.calls = []
        self.result = completion()
        self.error = None
        self.closed_count = 0
        self.started = None
        self.gate = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed_count += 1

    async def parse(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def settings(tmp_path):
    return AppSettings(
        llm_api_base="https://llm.test/v1", model_name=DEFAULT_LLM_MODEL,
        dataset_path=str(tmp_path / "db_schema.json"),
        embedded_schema=str(tmp_path / "embedded_schema"),
    )


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlmClient()
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: fake)
    return fake


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings)) as client:
        yield client


def test_returns_sql_string_and_usage_without_requiring_local_indexes(client, settings, llm):
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == SQL
    assert body["meta_data"] == [{"model_name": DEFAULT_LLM_MODEL, "question_token": 120, "answer_token": 35}]
    assert body["status"] == body["msg"] == "success"
    assert body["elapsed_time"] >= 0
    assert llm.closed_count == 1 and len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == DEFAULT_LLM_MODEL and call["temperature"] == 0
    assert call["response_format"] is GeneratedSqlOutput
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert len(call["messages"]) == 2
    assert call["messages"][0]["role"] == "system"
    assert "PostgreSQL" in call["messages"][0]["content"]
    assert "Oracle" in call["messages"][0]["content"]
    assert json.loads(call["messages"][-1]["content"]) == payload()
    assert not Path(settings.dataset_path).exists()
    assert not Path(settings.embedded_schema).exists()


@pytest.mark.parametrize("count,selected", [(0, []), (1, [0]), (3, [0, 1, 2]), (4, [1, 2, 3])])
def test_uses_last_three_history_messages_and_current_query_last(client, llm, count, selected):
    request = payload()
    request["history"] = HISTORY[:count]
    assert client.post(ENDPOINT, json=request).status_code == 200
    messages = llm.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1:-1] == [HISTORY[i] for i in selected]
    assert messages[-1]["role"] == "user"
    assert json.loads(messages[-1]["content"]) == payload()
    assert len(messages) == 2 + len(selected)


def test_history_is_not_retained_between_requests(client, llm):
    first = payload()
    first["history"] = HISTORY
    assert client.post(ENDPOINT, json=first).status_code == 200
    assert client.post(ENDPOINT, json=payload()).status_code == 200
    assert len(llm.calls[0]["messages"]) == 5
    assert len(llm.calls[1]["messages"]) == 2


@pytest.mark.parametrize("request_body", [
    {}, {"query": QUERY}, {"schema": SCHEMA},
    {"query": "", "schema": SCHEMA}, {"query": " \n\t", "schema": SCHEMA},
    {"query": 123, "schema": SCHEMA}, {"query": None, "schema": SCHEMA},
    {"query": QUERY, "schema": None}, {"query": QUERY, "schema": "CREATE TABLE A (id INTEGER);"},
    {"query": QUERY, "schema": [SCHEMA]}, {"query": QUERY, "schema": {}},
    {"query": QUERY, "schema": SCHEMA, "history": None},
    {"query": QUERY, "schema": SCHEMA, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "schema": SCHEMA, "history": [{"role": "user", "content": " "}]},
])
def test_invalid_requests_return_422_without_llm_call(client, llm, request_body):
    assert client.post(ENDPOINT, json=request_body).status_code == 422
    assert llm.calls == [] and llm.closed_count == 0


@pytest.mark.parametrize("field,value", [
    ("table_name", []), ("schema", []), ("description", []),
    ("table_name", ["A", "B"]), ("schema", ["DDL", "DDL"]),
    ("description", ["description", "description"]),
    ("table_name", [" "]), ("schema", ["\n"]), ("description", [""]),
    ("table_name", [123]), ("schema", [None]), ("description", [False]),
])
def test_invalid_schema_lists_return_422(client, llm, field, value):
    request = payload()
    request["schema"][field] = value
    response = client.post(ENDPOINT, json=request)
    assert response.status_code == 422
    assert llm.calls == []


def test_multiple_tables_keep_names_ddl_and_descriptions_aligned(client, llm):
    request = payload()
    additions = {"table_name": "ACADEMIC_TERMS", "schema": "CREATE TABLE ACADEMIC_TERMS (TERM_CODE VARCHAR2);",
                 "description": "학기 정보를 조회하는 테이블"}
    for key, value in additions.items():
        request["schema"][key].append(value)
    assert client.post(ENDPOINT, json=request).status_code == 200
    assert json.loads(llm.calls[0]["messages"][-1]["content"])["schema"] == request["schema"]


def test_trims_outer_sql_whitespace_and_handles_missing_usage(client, llm):
    llm.result.choices[0].message.parsed = {"sql": f" \n{SQL}\n "}
    llm.result.usage = None
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 200
    assert response.json()["data"] == SQL
    assert response.json()["meta_data"][0]["question_token"] == 0
    assert response.json()["meta_data"][0]["answer_token"] == 0


@pytest.mark.parametrize("parsed", [None, {}, {"sql": ""}, {"sql": " \n"}, {"sql": 123},
                                    {"sql": "```sql\nSELECT 1;\n```"}, {"sql": SQL, "extra": "unexpected"}])
def test_invalid_structured_output_is_502_and_closes_client(client, llm, parsed):
    llm.result.choices[0].message.parsed = parsed
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 502
    assert response.json()["status"] == "error" and response.json()["data"] is None
    assert llm.closed_count == 1


@pytest.mark.parametrize("failure", ["refusal", "length", "no_choices", "transport"])
def test_llm_failures_return_common_502_response(client, llm, failure):
    if failure == "refusal":
        llm.result.choices[0].message.refusal = "Cannot generate SQL"
    elif failure == "length":
        llm.result.choices[0].finish_reason = "length"
    elif failure == "no_choices":
        llm.result.choices = []
    else:
        llm.error = RuntimeError("LLM server unavailable")
    response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 502
    body = response.json()
    assert body["data"] is None and body["meta_data"] == [] and body["status"] == "error"
    assert body["msg"] and body["elapsed_time"] >= 0
    assert llm.closed_count == 1


@pytest.mark.parametrize("field,value,expected", [
    ("llm_api_base", "", "LLM_API_BASE"),
    ("llm_api_base", "ftp://llm.test/v1", "LLM_API_BASE"),
    ("llm_api_base", "invalid-url", "LLM_API_BASE"),
    ("model_name", "", "LLM_MODEL_NAME"), ("model_name", "openai/", "LLM_MODEL_NAME"),
])
def test_invalid_llm_configuration_is_500(settings, field, value, expected):
    setattr(settings, field, value)
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT, json=payload())
    assert response.status_code == 500
    assert response.json()["data"] is None
    assert expected in response.json()["msg"]


def test_other_endpoints_start_without_llm_configuration(settings, monkeypatch):
    settings.llm_api_base = settings.model_name = ""
    Path(settings.dataset_path).write_text("[]")

    def unexpected_client(*args, **kwargs):
        pytest.fail("An unrelated endpoint must not construct an LLM client")

    monkeypatch.setattr(service_module, "create_llm_client", unexpected_client)
    with TestClient(create_app(settings)) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.post("/retrieve/indexing_schema").status_code == 200
        assert client.post("/retrieve/embedding_based_schema", json={"query": QUERY, "top_k": 1}).status_code == 404


def test_sql_logs_include_request_id_and_generation_stages(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        request = payload()
        request["history"] = HISTORY
        assert client.post(ENDPOINT, json=request).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "llm_based_generate_sql"]
    assert len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"llm_request", "sql_validation"} <= {r.stage for r in records}
    assert "history_count=4 used_history_count=3 table_count=1" in records[0].getMessage()


def test_cancellation_closes_async_llm_client(settings, llm):
    async def run():
        llm.started = asyncio.Event()
        llm.gate = asyncio.Event()
        task = asyncio.create_task(LlmBasedGenerateSqlService(settings).generate_sql(
            LlmBasedGenerateSqlRequest.model_validate(payload()),
        ))
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert llm.closed_count == 1


def sdk_response():
    return {
        "id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": DEFAULT_LLM_MODEL,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "refusal": None, "content": json.dumps({"sql": SQL}),
        }}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 35, "total_tokens": 155},
    }


@pytest.mark.parametrize("model_name", [DEFAULT_LLM_MODEL, f"openai/{DEFAULT_LLM_MODEL}", "other-sql-model"])
def test_actual_sdk_sends_messages_structured_format_and_provider_options(settings, monkeypatch, model_name):
    settings.model_name = model_name
    request_body = payload()
    request_body["history"] = HISTORY
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        expected_model = model_name.removeprefix("openai/")
        assert body["model"] == expected_model
        assert body["temperature"] == 0
        if expected_model.lower().startswith("deepseek"):
            assert body["thinking"] == {"type": "disabled"}
        else:
            assert "thinking" not in body
        assert body["response_format"]["type"] == "json_schema"
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
        assert schema["schema"]["required"] == ["sql"]
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(body["messages"][-1]["content"]) == payload()
        return httpx.Response(200, json=sdk_response())

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings:
                        create_llm_client(settings, http_client=transport, max_retries=0))
    try:
        with TestClient(create_app(settings)) as client:
            response = client.post(ENDPOINT, json=request_body)
        assert response.status_code == 200
        assert response.json()["data"] == SQL
        assert response.json()["meta_data"][0]["model_name"] == model_name.removeprefix("openai/")
        assert len(requests) == 1 and str(requests[0].url) == "https://llm.test/v1/chat/completions"
        assert transport.is_closed
    finally:
        asyncio.run(transport.aclose())


@pytest.mark.parametrize("failure", ["invalid_json", "empty_sql", "refusal", "length", "http_error", "timeout"])
def test_actual_sdk_errors_are_502(settings, monkeypatch, failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("LLM request timed out", request=request)
        if failure == "http_error":
            return httpx.Response(503, json={"error": {"message": "LLM unavailable", "type": "server_error"}})
        body = sdk_response()
        message = body["choices"][0]["message"]
        if failure == "invalid_json":
            message["content"] = "not JSON"
        elif failure == "empty_sql":
            message["content"] = json.dumps({"sql": " "})
        elif failure == "refusal":
            message.update(content=None, refusal="Cannot answer")
        else:
            body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings:
                        create_llm_client(settings, http_client=transport, max_retries=0))
    try:
        with TestClient(create_app(settings)) as client:
            response = client.post(ENDPOINT, json=payload())
        assert response.status_code == 502
        assert response.json()["status"] == "error" and response.json()["data"] is None
        assert transport.is_closed
    finally:
        asyncio.run(transport.aclose())


def test_llm_configuration_precedence_and_default_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("LLM_API_BASE=https://file.test/v1\nLLM_MODEL_NAME=file-model\n")
    with patch.dict(os.environ, {}, clear=True):
        assert config.AppSettings().model_name == "file-model"
        assert config.AppSettings().llm_api_base == "https://file.test/v1"
    with patch.dict(os.environ, {"LLM_API_BASE": "https://env.test/v1", "LLM_MODEL_NAME": "env-model"}, clear=True):
        settings = config.AppSettings()
        assert settings.llm_api_base == "https://env.test/v1" and settings.model_name == "env-model"
        settings = config.parse_args(["--llm_api_base", "https://cli.test/v1", "--model_name", "cli-model"])
        assert settings.llm_api_base == "https://cli.test/v1" and settings.model_name == "cli-model"
    (tmp_path / ".env").write_text("LLM_API_BASE=https://file.test/v1\n")
    with patch.dict(os.environ, {}, clear=True):
        assert config.AppSettings().model_name == DEFAULT_LLM_MODEL


def test_openapi_has_dedicated_sql_request_response_and_schema_alias(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedGenerateSqlRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/LlmBasedGenerateSqlResponse")
    assert {"200", "422", "500", "502"} <= set(operation["responses"])
    models = spec["components"]["schemas"]
    request = models["LlmBasedGenerateSqlRequest"]
    assert set(request["required"]) == {"query", "schema"}
    assert request["properties"]["schema"]["$ref"].endswith("/SqlGenerationSchema")
    assert "history[-3:]" in request["properties"]["history"]["description"]
    assert set(models["SqlGenerationSchema"]["properties"]) == {"table_name", "schema", "description"}
    assert models["SqlGenerationSchema"]["properties"]["schema"]["minItems"] == 1
