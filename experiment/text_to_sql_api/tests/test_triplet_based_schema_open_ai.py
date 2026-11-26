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
from app.schemas.request import LlmBasedGenerateSqlRequest, TripletBasedSchemaOpenAiRequest
from app.schemas.response import TripletBasedSchemaOpenAiData, TripletBasedSchemaOpenAiResponse
from app.services import open_ai_clients, triplet_based_schema_service as service_module
from main import create_app


ENDPOINT = "/retrieve/triplet_based_schema_open_ai"
FAKE_KEY = "test-openai-schema-key"
QUERY = "그중 사용 중인 공간의 캠퍼스와 독립 통계를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}
    for name in ("ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED")
]
HISTORY = [
    {"role": "user", "content": "제외할 오래된 질문"},
    {"role": "assistant", "content": "공간 정보를 조회합니다."},
    {"role": "user", "content": "사용 중인 공간만 보고 싶어"},
    {"role": "assistant", "content": "사용 여부를 필터로 적용합니다."},
]


def selection(names):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": "현재 질의에 필요"} for name in names]},
        ))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.started = None
        self.gate = None
        self.results = [selection(["ROOM", "ISOLATED"]), selection(["CAMPUS", "BUILDING", "ROOM", "ISOLATED"])]
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def parse(self, **kwargs):
        result = self.results[len(self.calls)]
        self.calls.append(copy.deepcopy(kwargs))
        if self.started is not None:
            self.started.set()
            await self.gate.wait()
        if isinstance(result, Exception):
            raise result
        return result


def save_relations(directory, relations):
    path = Path(directory) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        [{"table_name": subject, "description": "과거 설명"}, predicate,
         {"table_name": target, "description": "과거 설명"}]
        for subject, predicate, target in relations
    ], ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps(TABLES, ensure_ascii=False), encoding="utf-8")
    settings = AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "legacy"),
        rdf_triplet_open_ai=str(tmp_path / "open-ai"),
        embedded_schema=str(tmp_path / "embedded-legacy"),
        embedded_schema_open_ai=str(tmp_path / "embedded-open-ai"),
        openai_api_key=FAKE_KEY, openai_llm_model_name="gpt-6-luna",
        llm_api_base="invalid-legacy-url", model_name="unused-legacy-model",
    )
    save_relations(settings.rdf_triplet_open_ai, [
        ("ROOM", "belongs to", "BUILDING"),
        ("CAMPUS", "contains", "BUILDING"),
        ("CAMPUS", "belongs to", "DISTRICT"),
    ])
    # A different legacy graph makes accidental store substitution observable.
    save_relations(settings.rdf_triplet, [("ROOM", "belongs to", "DISTRICT")])
    return settings


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda settings: fake)

    def reject_legacy_client(*args, **kwargs):
        raise AssertionError("OpenAI retrieval must not use the legacy LLM client")

    monkeypatch.setattr(service_module, "create_llm_client", reject_legacy_client)
    return fake


def invoke(settings, payload=None):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT, json={"query": QUERY} if payload is None else payload)


def snapshot(settings):
    return {str(path): path.read_bytes() for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


def expected_data(names):
    by_name = {table["table_name"]: table for table in TABLES}
    return {key: [by_name[name][key] for name in names] for key in ("table_name", "schema", "description")}


def test_open_ai_pipeline_uses_separate_graph_original_ddl_and_history(settings, llm):
    before = snapshot(settings)
    response = invoke(settings, {"query": QUERY, "history": HISTORY, "max_hop": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["CAMPUS", "BUILDING", "ROOM", "ISOLATED"])
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert body["meta_data"] == [{
        "model_name": "gpt-6-luna", "question_token": 200, "answer_token": 40,
        "seed_count": 2, "expanded_count": 4, "retrieved_count": 4, "graph_depth": 2,
    }]
    assert len(llm.calls) == 2 and llm.closed == 1
    for call in llm.calls:
        assert call["model"] == "gpt-6-luna" and call["reasoning_effort"] == "none"
        assert "temperature" not in call and "extra_body" not in call
        assert call["response_format"] is SchemaTableSelectionOutput
        assert call["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(call["messages"][-1]["content"])["query"] == QUERY
    catalog = json.loads(llm.calls[0]["messages"][-1]["content"])["catalog"]
    assert catalog == [{"table_name": t["table_name"], "description": t["description"]} for t in TABLES]
    final_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final_payload["candidates"] == [t for t in TABLES if t["table_name"] != "DISTRICT"]
    assert final_payload["relations"] == [["ROOM", "belongs to", "BUILDING"], ["CAMPUS", "contains", "BUILDING"]]
    assert snapshot(settings) == before
    assert not Path(settings.embedded_schema).exists() and not Path(settings.embedded_schema_open_ai).exists()
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize(("max_hop", "names", "graph_depth"), [
    (None, ["ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED"], 3),
    (0, ["ROOM", "ISOLATED"], 0),
    (1, ["ROOM", "BUILDING", "ISOLATED"], 1),
    (2, ["ROOM", "BUILDING", "CAMPUS", "ISOLATED"], 2),
    (3, ["ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED"], 3),
    (-1, ["ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED"], 10),
])
def test_open_ai_uses_request_max_hop_for_final_candidates_and_metadata(settings, llm, max_hop, names, graph_depth):
    llm.results[1] = selection(names)
    payload = {"query": QUERY}
    if max_hop is not None:
        payload["max_hop"] = max_hop
    response = invoke(settings, payload)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(names)
    assert body["meta_data"][0]["graph_depth"] == graph_depth
    assert body["meta_data"][0]["expanded_count"] == len(names)
    assert len(llm.calls) == 2
    final_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final_payload["candidates"] == [table for table in TABLES if table["table_name"] in names]


def test_open_ai_max_hop_is_independent_between_requests(settings, llm):
    cases = [
        ({"max_hop": 0}, ["ROOM", "ISOLATED"], 0),
        ({"max_hop": -1}, ["ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED"], 10),
        ({}, ["ROOM", "BUILDING", "CAMPUS", "DISTRICT", "ISOLATED"], 3),
        ({"max_hop": 1}, ["ROOM", "BUILDING", "ISOLATED"], 1),
    ]
    llm.results = [result for _, names, _ in cases for result in (
        selection(["ROOM", "ISOLATED"]), selection(names),
    )]
    with TestClient(create_app(settings)) as client:
        for fields, names, graph_depth in cases:
            response = client.post(ENDPOINT, json={"query": QUERY, **fields})
            assert response.status_code == 200
            assert response.json()["data"] == expected_data(names)
            assert response.json()["meta_data"][0]["graph_depth"] == graph_depth
    assert len(llm.calls) == 2 * len(cases)


def test_service_returns_endpoint_specific_response_types(settings, llm):
    service = create_app(settings).state.services.triplet_based_schema_open_ai_service
    response = asyncio.run(service.triplet_based_schema(TripletBasedSchemaOpenAiRequest(query=QUERY)))
    assert type(response) is TripletBasedSchemaOpenAiResponse
    assert type(response.data) is TripletBasedSchemaOpenAiData


@pytest.mark.parametrize("stage", [0, 1])
def test_empty_selections_return_empty_lists_and_skip_unneeded_calls(settings, llm, stage):
    llm.results[stage] = selection([])
    response = invoke(settings)
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert len(llm.calls) == stage + 1 and llm.closed == 1


def test_empty_catalog_skips_llm_but_still_requires_valid_key(settings, llm):
    Path(settings.dataset_path).write_text("[]")
    save_relations(settings.rdf_triplet_open_ai, [])
    response = invoke(settings)
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["question_token"] == 0
    settings.openai_api_key = ""
    assert invoke(settings).status_code == 500 and llm.calls == []


@pytest.mark.parametrize("field,value,expected", [
    ("openai_api_key", " ", "OPENAI_API_KEY"),
    ("openai_llm_model_name", " ", "OPENAI_LLM_MODEL_NAME"),
    ("rdf_triplet_open_ai", " ", "path"),
    ("rdf_triplet_open_ai", "same", "RDF_TRIPLET_OPEN_AI"),
    ("rdf_triplet_open_ai", "alias", "RDF_TRIPLET_OPEN_AI"),
])
def test_configuration_errors_are_checked_before_file_reads(settings, llm, monkeypatch, field, value, expected):
    if value == "same":
        value = settings.rdf_triplet
    elif value == "alias":
        value = settings.rdf_triplet + "/../legacy"
    setattr(settings, field, value)

    def reject_load(*args):
        raise AssertionError("Configuration must be checked before snapshot loading")

    monkeypatch.setattr(service_module.TripletBasedSchemaService, "_load_snapshot", reject_load)
    response = invoke(settings)
    assert response.status_code == 500 and expected in response.json()["msg"]
    assert llm.calls == []


def test_missing_open_ai_rdf_is_404_without_falling_back_to_legacy(settings, llm):
    path = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    path.unlink()
    path.parent.rmdir()
    before = snapshot(settings)
    response = invoke(settings)
    assert response.status_code == 404
    assert "/retrieve/generate_triplet_open_ai" in response.json()["msg"]
    assert response.json()["data"] is None and llm.calls == []
    assert not path.parent.exists() and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["json", "structure", "unknown_table"])
def test_damaged_rdf_returns_409_with_no_file_changes(settings, llm, damage):
    path = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    if damage == "json":
        path.write_text("invalid json")
    elif damage == "structure":
        path.write_text('[{"subject": "ROOM"}]')
    else:
        save_relations(settings.rdf_triplet_open_ai, [("UNKNOWN", "references", "ROOM")])
    before = snapshot(settings)
    response = invoke(settings)
    assert response.status_code == 409 and response.json()["data"] is None
    if damage == "unknown_table":
        assert "/retrieve/generate_triplet_open_ai" in response.json()["msg"]
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("payload", [
    {}, {"query": ""}, {"query": " \n"}, {"query": 123}, {"query": None},
    {"query": QUERY, "top_k": 0}, {"query": QUERY, "graph_depth": 1},
    {"query": QUERY, "history": None},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "history": [{"role": "assistant", "content": " "}]},
    {"query": QUERY, "history": [{"role": "user", "content": 123}]},
])
def test_invalid_requests_are_422_without_llm(settings, llm, payload):
    assert invoke(settings, payload).status_code == 422
    assert llm.calls == []


@pytest.mark.parametrize("max_hop", [-2, -10, "3", 3.0, 1.5, True, False, None, [], {}])
def test_open_ai_invalid_max_hop_is_422_without_llm(settings, llm, max_hop):
    response = invoke(settings, {"query": QUERY, "max_hop": max_hop})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "max_hop"]
    assert llm.calls == []


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("failure", ["api", "refusal", "length", "malformed", "unknown_table", "outside_candidates"])
def test_llm_failures_return_502_without_partial_results(settings, llm, stage, failure):
    result = selection(["ROOM"])
    if failure == "api":
        result = TimeoutError("OpenAI server timed out")
    elif failure == "refusal":
        result.choices[0].message.refusal = "refused"
    elif failure == "length":
        result.choices[0].finish_reason = "length"
    elif failure == "malformed":
        result.choices[0].message.parsed = {"tables": ["ROOM"]}
    elif failure == "unknown_table":
        result = selection(["UNKNOWN"])
    else:
        result = selection(["DISTRICT"] if stage else ["UNKNOWN"])
    llm.results[stage] = result
    before = snapshot(settings)
    response = invoke(settings, {"query": QUERY, "max_hop": 2})
    assert response.status_code == 502
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(llm.calls) == stage + 1 and llm.closed == 1
    assert snapshot(settings) == before


@pytest.mark.parametrize("stage", [0, 1])
def test_errors_mask_keys_in_response_and_formatted_traceback(settings, llm, caplog, stage):
    llm.results[stage] = RuntimeError(f"OpenAI rejected key {FAKE_KEY}")
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
def test_real_sdk_uses_official_open_ai_url_authentication_and_options(settings, monkeypatch, status):
    factory = open_ai_clients.create_open_ai_llm_client
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-global-key")
    settings.openai_llm_model_name = "openai/gpt-6-luna"
    requests = []

    def handle(request):
        index = len(requests)
        requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none"
        assert "temperature" not in body and "thinking" not in body
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(body["messages"][-1]["content"])["query"] == QUERY
        if status != 200:
            return httpx.Response(status, json={"error": {
                "message": f"OpenAI rejected {FAKE_KEY}", "type": "invalid_request_error", "code": "test",
            }})
        return httpx.Response(200, json={
            "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 1, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "tables": [{"table_name": "ROOM" if index == 0 else "CAMPUS", "reason": "현재 질의에 필요"}],
                }, ensure_ascii=False),
            }}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile: factory(
        profile, http_client=http_client, max_retries=0,
    ))
    response = invoke(settings, {"query": QUERY, "history": HISTORY})
    assert FAKE_KEY not in response.text and http_client.is_closed
    if status == 200:
        assert response.status_code == 200 and response.json()["data"] == expected_data(["CAMPUS"])
        assert len(requests) == 2
    else:
        assert response.status_code == 502 and len(requests) == 1


def test_cancelled_open_ai_retrieval_closes_client_and_preserves_files(settings, llm):
    service = create_app(settings).state.services.triplet_based_schema_open_ai_service
    before = snapshot(settings)

    async def run():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.triplet_based_schema(TripletBasedSchemaOpenAiRequest(query=QUERY)))
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


def test_openapi_exposes_dedicated_models_and_legacy_search_still_works_without_key(settings, monkeypatch):
    settings.openai_api_key = ""
    settings.llm_api_base = "https://legacy.test/v1"
    settings.model_name = "legacy-model"
    fake = FakeLlm()
    fake.results = [selection(["ROOM"]), selection(["DISTRICT"])]
    monkeypatch.setattr(service_module, "create_llm_client", lambda profile: fake)
    with TestClient(create_app(settings)) as client:
        response = client.post("/retrieve/triplet_based_schema", json={"query": QUERY})
        spec = client.get("/openapi.json").json()
    assert response.status_code == 200 and response.json()["data"] == expected_data(["DISTRICT"])
    assert fake.calls[0]["model"] == "legacy-model"
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/TripletBasedSchemaOpenAiRequest")
    for code in ("200", "404", "409", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/TripletBasedSchemaOpenAiResponse")
    assert "422" in operation["responses"]
    models = spec["components"]["schemas"]
    request = models["TripletBasedSchemaOpenAiRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "history", "max_hop", "top_k"}
    assert request["properties"]["top_k"]["type"] == "integer"
    assert request["properties"]["top_k"]["default"] == -1
    assert request["properties"]["max_hop"]["type"] == "integer"
    assert request["properties"]["max_hop"]["default"] == 3
    assert request["properties"]["max_hop"]["minimum"] == -1
    assert set(models["TripletBasedSchemaOpenAiData"]["properties"]) == {"table_name", "schema", "description"}
    assert len(spec["paths"]) == 25


def test_logs_distinguish_open_ai_retrieval_with_one_request_id(settings, llm, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert invoke(settings, {"query": QUERY, "history": HISTORY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "triplet_based_schema_open_ai"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"seed_selection", "graph_expansion", "final_selection"} <= {r.stage for r in records}
    assert "history_count=4 used_history_count=3" in records[0].getMessage()
