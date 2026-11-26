import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.prompts.triplet_based_schema_prompt import SEED_SELECTION_INSTRUCTIONS
from app.schemas.request import LlmBasedGenerateSqlRequest, OnlyTripletBasedSchemaOpenAiRequest
from app.schemas.response import OnlyTripletBasedSchemaOpenAiData, OnlyTripletBasedSchemaOpenAiResponse
from app.services import open_ai_clients, triplet_based_schema_service as service_module
from main import create_app


ENDPOINT = "/retrieve/only_triplet_based_schema_open_ai"
FAKE_KEY = "test-only-triplet-openai-key"
QUERY = "그중 사용 중인 공간의 캠퍼스와 독립 통계를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}
    for name in ("DISTRICT", "ISOLATED", "CAMPUS", "BUILDING", "ROOM", "UNRELATED")
]
RELATIONS = [
    ("ROOM", "belongs to", "BUILDING"), ("CAMPUS", "contains", "BUILDING"),
    ("CAMPUS", "belongs to", "DISTRICT"), ("BUILDING", "references", "ROOM"),
    ("BUILDING", "references", "BUILDING"), ("ROOM", "belongs to", "BUILDING"),
]
HISTORY = [
    {"role": "user", "content": "오래된 질문"}, {"role": "assistant", "content": "오래된 답변"},
    {"role": "user", "content": "공간 정보를 조회해줘"}, {"role": "assistant", "content": "공간 조회입니다"},
    {"role": "user", "content": "사용 중인 것만"}, {"role": "assistant", "content": "사용 여부 조건을 적용합니다"},
]


def selection(names):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": "질의에 필요"} for name in names]},
        ))], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.results = [selection(["ROOM", "ISOLATED"])]
        self.started = None
        self.gate = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def parse(self, **kwargs):
        result = self.results[len(self.calls)]
        self.calls.append(kwargs)
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


def expected_data(names):
    by_name = {table["table_name"]: table for table in TABLES}
    return {key: [by_name[name][key] for name in names] for key in ("table_name", "schema", "description")}


def snapshot(settings):
    return {str(path): path.read_bytes() for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps(TABLES, ensure_ascii=False), encoding="utf-8")
    settings = AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "legacy"),
        rdf_triplet_open_ai=str(tmp_path / "open-ai"),
        embedded_schema=str(tmp_path / "embedded-legacy"), embedded_schema_open_ai=str(tmp_path / "embedded-open-ai"),
        openai_api_key=FAKE_KEY, openai_llm_model_name="gpt-6-luna",
        llm_api_base="invalid-legacy-url", model_name="unused-legacy-model",
    )
    save_relations(settings.rdf_triplet_open_ai, RELATIONS)
    save_relations(settings.rdf_triplet, [("ROOM", "references", "UNRELATED")])
    return settings


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda settings: fake)
    monkeypatch.setattr(service_module, "create_llm_client", lambda *args, **kwargs:
                        pytest.fail("OpenAI retrieval must not use the legacy LLM client"))
    return fake


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings)) as client:
        yield client


def test_one_open_ai_call_returns_all_candidates_from_the_open_ai_graph(client, settings, llm):
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == body["msg"] == "success"
    assert body["data"] == expected_data(["DISTRICT", "ISOLATED", "CAMPUS", "BUILDING", "ROOM"])
    assert body["meta_data"] == [{
        "model_name": "gpt-6-luna", "question_token": 100, "answer_token": 20,
        "seed_count": 2, "expanded_count": 5, "retrieved_count": 5, "graph_depth": 3,
    }]
    assert len(llm.calls) == llm.closed == 1
    call = llm.calls[0]
    assert call["model"] == "gpt-6-luna" and call["reasoning_effort"] == "none"
    assert "temperature" not in call and "extra_body" not in call
    assert call["messages"][1:-1] == HISTORY[-3:]
    assert json.loads(call["messages"][-1]["content"]) == {
        "query": QUERY, "catalog": [{"table_name": t["table_name"], "description": t["description"]} for t in TABLES],
    }
    assert snapshot(settings) == before
    assert not Path(settings.embedded_schema).exists() and not Path(settings.embedded_schema_open_ai).exists()
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize(("top_k", "seeds", "names"), [
    (1, ["ROOM", "DISTRICT", "ISOLATED"], ["ROOM"]),
    (2, ["ROOM", "ROOM", "ISOLATED", "DISTRICT"], ["ISOLATED", "ROOM"]),
    (3, ["ROOM"], ["ROOM"]),
    (10, ["ROOM", "ISOLATED"], ["ISOLATED", "ROOM"]),
    (1, [], []),
    (-1, ["ROOM", "ISOLATED", "DISTRICT"], ["DISTRICT", "ISOLATED", "ROOM"]),
])
def test_top_k_limits_valid_unique_seeds_in_llm_order(client, llm, top_k, seeds, names):
    llm.results = [selection(seeds)]
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 0, "top_k": top_k})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    metadata = response.json()["meta_data"][0]
    assert metadata["seed_count"] == metadata["expanded_count"] == metadata["retrieved_count"] == len(names)
    assert len(llm.calls) == llm.closed == 1
    system_message = llm.calls[0]["messages"][0]["content"]
    payload = json.loads(llm.calls[0]["messages"][-1]["content"])
    if top_k > 0:
        assert f"최대 {top_k}개" in system_message
        assert "관련도가 높은 순서" in system_message
        assert "선택 개수에 제한은 없습니다." not in system_message
        assert payload["top_k"] == top_k
    else:
        assert system_message == SEED_SELECTION_INSTRUCTIONS
        assert "top_k" not in payload


def test_top_k_limits_seeds_but_returns_all_schemas_after_expansion(client, llm):
    llm.results = [selection(["ROOM", "ISOLATED", "DISTRICT"])]
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 2, "top_k": 1})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["CAMPUS", "BUILDING", "ROOM"])
    metadata = response.json()["meta_data"][0]
    assert metadata["seed_count"] == 1
    assert metadata["expanded_count"] == metadata["retrieved_count"] == 3
    assert len(llm.calls) == 1


def test_unknown_seed_beyond_limit_is_still_rejected(client, llm):
    llm.results = [selection(["ROOM", "UNKNOWN"])]
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 0, "top_k": 1})
    assert response.status_code == 502
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(llm.calls) == 1


def test_top_k_is_independent_between_requests_and_omission_is_unlimited(client, llm):
    cases = [
        ({"top_k": 1}, ["ROOM"]),
        ({"top_k": -1}, ["DISTRICT", "ISOLATED", "ROOM"]),
        ({"top_k": 2}, ["ISOLATED", "ROOM"]),
        ({}, ["DISTRICT", "ISOLATED", "ROOM"]),
    ]
    llm.results = [selection(["ROOM", "ISOLATED", "DISTRICT"]) for _ in cases]
    for fields, names in cases:
        response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 0, **fields})
        assert response.status_code == 200 and response.json()["data"] == expected_data(names)
        assert response.json()["meta_data"][0]["seed_count"] == len(names)
    assert len(llm.calls) == len(cases)


@pytest.mark.parametrize("top_k", [0, -2, -10, "2", 2.0, True, False, None, [], {}])
def test_invalid_seed_top_k_returns_422_without_open_ai(client, llm, top_k):
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": top_k})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "top_k"]
    assert llm.calls == []


@pytest.mark.parametrize(("max_hop", "names", "depth"), [
    (0, ["ISOLATED", "ROOM"], 0), (1, ["ISOLATED", "BUILDING", "ROOM"], 1),
    (2, ["ISOLATED", "CAMPUS", "BUILDING", "ROOM"], 2),
    (3, ["DISTRICT", "ISOLATED", "CAMPUS", "BUILDING", "ROOM"], 3),
    (-1, ["DISTRICT", "ISOLATED", "CAMPUS", "BUILDING", "ROOM"], 10),
])
def test_request_hop_limits_return_all_reached_schemas(client, llm, max_hop, names, depth):
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": max_hop})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    metadata = response.json()["meta_data"][0]
    assert metadata["graph_depth"] == depth
    assert metadata["expanded_count"] == metadata["retrieved_count"] == len(names)
    assert len(llm.calls) == 1


def test_auto_mode_stops_at_ten_hops(client, settings, llm):
    tables = [{"table_name": f"T{i}", "schema": f"CREATE TABLE T{i} (id INTEGER);", "description": f"T{i}"}
              for i in range(13)]
    Path(settings.dataset_path).write_text(json.dumps(tables))
    save_relations(settings.rdf_triplet_open_ai, [(f"T{i + 1}", "references", f"T{i}") for i in range(12)])
    llm.results = [selection(["T0"])]
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": -1})
    assert response.status_code == 200
    assert response.json()["data"] == {
        key: [table[key] for table in tables[:11]] for key in ("table_name", "schema", "description")
    }
    assert len(llm.calls) == 1


@pytest.mark.parametrize("count", [0, 1, 3, 6])
def test_seed_call_uses_only_last_three_history_messages(client, llm, count):
    history = HISTORY[:count]
    response = client.post(ENDPOINT, json={"query": QUERY, "history": history})
    assert response.status_code == 200
    assert llm.calls[0]["messages"][1:-1] == history[-3:]
    assert len(llm.calls) == 1


def test_empty_relations_preserve_deduplicated_isolated_seeds(client, settings, llm):
    save_relations(settings.rdf_triplet_open_ai, [])
    llm.results = [selection(["ROOM", "ROOM", "ISOLATED"])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["ISOLATED", "ROOM"])
    assert response.json()["meta_data"][0]["seed_count"] == 2
    assert len(llm.calls) == 1


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_empty_seeds_or_catalog_return_empty_parallel_lists(client, settings, llm, empty_catalog):
    if empty_catalog:
        Path(settings.dataset_path).write_text("[]")
        save_relations(settings.rdf_triplet_open_ai, [])
    else:
        llm.results = [selection([])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["expanded_count"] == response.json()["meta_data"][0]["retrieved_count"] == 0
    assert len(llm.calls) == int(not empty_catalog)


@pytest.mark.parametrize("payload", [
    {}, {"query": " "}, {"query": 123}, {"query": None},
    {"query": QUERY, "history": None}, {"query": QUERY, "graph_depth": 2},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    *({"query": QUERY, "max_hop": value} for value in (-2, "3", 3.0, True, False, None)),
])
def test_invalid_requests_return_422_before_open_ai(client, llm, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422 and llm.calls == []


@pytest.mark.parametrize(("field", "value", "expected"), [
    ("openai_api_key", " ", "OPENAI_API_KEY"), ("openai_llm_model_name", " ", "OPENAI_LLM_MODEL_NAME"),
    ("rdf_triplet_open_ai", " ", "path"), ("rdf_triplet_open_ai", "same", "RDF_TRIPLET_OPEN_AI"),
    ("rdf_triplet_open_ai", "alias", "RDF_TRIPLET_OPEN_AI"),
])
def test_invalid_open_ai_configuration_is_rejected_before_file_reads(settings, llm, monkeypatch, field, value, expected):
    if value == "same":
        value = settings.rdf_triplet
    elif value == "alias":
        value = settings.rdf_triplet + "/../legacy"
    setattr(settings, field, value)
    monkeypatch.setattr(service_module.TripletBasedSchemaService, "_load_snapshot", lambda *args:
                        pytest.fail("Configuration must be validated before reading files"))
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 500 and expected in response.json()["msg"]
    assert llm.calls == []


def test_missing_open_ai_rdf_does_not_fall_back_to_local_graph(client, settings, llm):
    path = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    path.unlink()
    path.parent.rmdir()
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 404 and "/retrieve/generate_triplet_open_ai" in response.json()["msg"]
    assert response.json()["data"] is None and llm.calls == []
    assert not path.parent.exists() and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["invalid_json", "unknown_table", "catalog"])
def test_invalid_rdf_or_catalog_returns_error_without_modification(client, settings, llm, damage):
    if damage == "invalid_json":
        (Path(settings.rdf_triplet_open_ai) / "triplets.json").write_text("not json")
    elif damage == "unknown_table":
        save_relations(settings.rdf_triplet_open_ai, [("UNKNOWN", "references", "ROOM")])
    else:
        Path(settings.dataset_path).write_text("not json")
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == (500 if damage == "catalog" else 409)
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("failure", ["api", "refusal", "length", "malformed", "unknown_table"])
def test_seed_call_failures_return_502_without_partial_schemas(client, llm, failure):
    result = selection(["ROOM"])
    if failure == "api":
        result = RuntimeError("OpenAI unavailable")
    elif failure == "refusal":
        result.choices[0].message.refusal = "refused"
    elif failure == "length":
        result.choices[0].finish_reason = "length"
    elif failure == "malformed":
        result.choices[0].message.parsed = {"tables": ["ROOM"]}
    else:
        result = selection(["UNKNOWN"])
    llm.results = [result]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 502
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(llm.calls) == llm.closed == 1


def test_errors_redact_keys_in_response_logs_and_formatted_traceback(client, llm, caplog):
    llm.results = [RuntimeError(f"OpenAI rejected {FAKE_KEY}")]
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = client.post(ENDPOINT, json={"query": QUERY})
        assert response.status_code == 502 and "[REDACTED]" in response.json()["msg"]
        assert FAKE_KEY not in response.text and FAKE_KEY not in caplog.text
        console = next(handler for handler in app_logger.handlers if handler.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("status", [200, 401, 429])
def test_sdk_uses_one_official_open_ai_request_with_correct_auth_and_options(settings, monkeypatch, status):
    factory = open_ai_clients.create_open_ai_llm_client
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-global-key")
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
        assert json.loads(body["messages"][-1]["content"])["top_k"] == 1
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"Rejected {FAKE_KEY}", "type": "test"}})
        return httpx.Response(200, json={
            "id": "chatcmpl-seeds", "object": "chat.completion", "created": 1, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"tables": [{"table_name": "ROOM", "reason": "필요"}]}),
            }}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile:
                        factory(profile, http_client=transport, max_retries=0))
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY, "top_k": 1})
    assert len(requests) == 1 and transport.is_closed and FAKE_KEY not in response.text
    if status == 200:
        assert response.status_code == 200
        assert response.json()["data"] == expected_data(["DISTRICT", "CAMPUS", "BUILDING", "ROOM"])
    else:
        assert response.status_code == 502


def test_service_and_openapi_use_dedicated_models(settings, llm):
    app = create_app(settings)
    service = app.state.services.only_triplet_based_schema_open_ai_service
    response = asyncio.run(service.triplet_based_schema(OnlyTripletBasedSchemaOpenAiRequest(query=QUERY)))
    assert type(response) is OnlyTripletBasedSchemaOpenAiResponse
    assert type(response.data) is OnlyTripletBasedSchemaOpenAiData
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/OnlyTripletBasedSchemaOpenAiRequest")
    for code in ("200", "404", "409", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/OnlyTripletBasedSchemaOpenAiResponse")
    assert "422" in operation["responses"] and len(spec["paths"]) == 25
    models = spec["components"]["schemas"]
    request = models["OnlyTripletBasedSchemaOpenAiRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "history", "max_hop", "top_k"}
    assert request["properties"]["top_k"]["type"] == "integer"
    assert request["properties"]["top_k"]["default"] == -1
    assert OnlyTripletBasedSchemaOpenAiRequest(query=QUERY).top_k == -1
    assert request["properties"]["max_hop"]["default"] == 3
    assert request["properties"]["max_hop"]["minimum"] == -1
    assert set(models["OnlyTripletBasedSchemaOpenAiData"]["properties"]) == {"table_name", "schema", "description"}


def test_cancelled_seed_selection_closes_open_ai_client_and_preserves_files(settings, llm):
    service = create_app(settings).state.services.only_triplet_based_schema_open_ai_service
    before = snapshot(settings)

    async def scenario():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.triplet_based_schema(OnlyTripletBasedSchemaOpenAiRequest(query=QUERY)))
        try:
            await asyncio.wait_for(llm.started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert len(llm.calls) == llm.closed == 1 and snapshot(settings) == before


def test_logs_identify_open_ai_endpoint_without_final_selection(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if getattr(record, "operation", None) == "only_triplet_based_schema_open_ai"]
    assert len({record.request_id for record in records}) == 1
    stages = {record.stage for record in records}
    assert {"request", "seed_selection", "graph_expansion", "complete"} <= stages
    assert "final_selection" not in stages
