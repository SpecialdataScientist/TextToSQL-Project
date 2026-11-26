import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import AppSettings, DEFAULT_LLM_MODEL
from app.core.logging import LOGGER_NAME
from app.schemas.request import LlmBasedGenerateSqlRequest, OnlyTripletBasedSchemaRequest
from app.schemas.response import OnlyTripletBasedSchemaData, OnlyTripletBasedSchemaResponse
from app.services import triplet_based_schema_service as service_module
from app.services.llm_client import create_llm_client
from main import create_app


ENDPOINT = "/retrieve/only_triplet_based_schema"
QUERY = "최근 조건으로 연결된 테이블과 독립 통계를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}
    for name in ("D", "ISOLATED", "C", "B", "A", "UNRELATED")
]
RELATIONS = [
    ("B", "belongs to", "A"), ("C", "references", "B"), ("D", "references", "C"),
    ("B", "belongs to", "A"), ("A", "references", "B"), ("B", "references", "B"),
]
HISTORY = [
    {"role": "user", "content": "오래된 질문"},
    {"role": "assistant", "content": "오래된 답변"},
    {"role": "user", "content": "이전 대상"},
    {"role": "assistant", "content": "최근 대상"},
    {"role": "user", "content": "최근 조건"},
    {"role": "assistant", "content": "최근 집계"},
]


def completion(names):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": "질의에 필요"} for name in names]},
        ))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        # A second LLM call has no prepared result, so an accidental final selection fails the request.
        self.results = [completion(["A", "ISOLATED"])]
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


def save_relations(settings, relations=RELATIONS):
    path = Path(settings.rdf_triplet) / "triplets.json"
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
        dataset_path=str(source), rdf_triplet=str(tmp_path / "rdf"),
        rdf_triplet_open_ai=str(tmp_path / "open-ai-rdf"),
        embedded_schema=str(tmp_path / "embedded"), embedded_schema_open_ai=str(tmp_path / "open-ai-embedded"),
        llm_api_base="https://llm.test/v1", model_name=DEFAULT_LLM_MODEL, openai_api_key="",
    )
    save_relations(settings)
    return settings


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: fake)
    return fake


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings)) as client:
        yield client


def test_returns_every_expanded_schema_in_catalog_order_with_one_llm_call(client, settings, llm):
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["D", "ISOLATED", "C", "B", "A"])
    assert body["status"] == body["msg"] == "success"
    assert body["meta_data"] == [{
        "model_name": DEFAULT_LLM_MODEL, "question_token": 100, "answer_token": 20,
        "seed_count": 2, "expanded_count": 5, "retrieved_count": 5, "graph_depth": 3,
    }]
    assert len(llm.calls) == llm.closed == 1
    assert json.loads(llm.calls[0]["messages"][-1]["content"]) == {
        "query": QUERY, "catalog": [{"table_name": t["table_name"], "description": t["description"]} for t in TABLES],
    }
    assert snapshot(settings) == before
    assert not Path(settings.embedded_schema).exists()
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize(("max_hop", "names", "depth"), [
    (0, ["ISOLATED", "A"], 0), (1, ["ISOLATED", "B", "A"], 1),
    (2, ["ISOLATED", "C", "B", "A"], 2), (3, ["D", "ISOLATED", "C", "B", "A"], 3),
    (-1, ["D", "ISOLATED", "C", "B", "A"], 10),
])
def test_requested_hop_limit_returns_all_reached_tables(client, llm, max_hop, names, depth):
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": max_hop})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(names)
    metadata = response.json()["meta_data"][0]
    assert metadata["graph_depth"] == depth
    assert metadata["expanded_count"] == metadata["retrieved_count"] == len(names)
    assert len(llm.calls) == 1


def test_auto_mode_does_not_return_tables_beyond_ten_hops(client, settings, llm):
    tables = [{"table_name": f"T{i}", "schema": f"CREATE TABLE T{i} (id INTEGER);", "description": f"T{i}"}
              for i in reversed(range(13))]
    Path(settings.dataset_path).write_text(json.dumps(tables))
    save_relations(settings, [(f"T{i + 1}", "references", f"T{i}") for i in range(12)])
    llm.results = [completion(["T0"])]
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": -1})
    assert response.status_code == 200
    assert response.json()["data"] == {
        key: [table[key] for table in tables[2:]] for key in ("table_name", "schema", "description")
    }
    assert response.json()["meta_data"][0]["retrieved_count"] == 11
    assert len(llm.calls) == 1


@pytest.mark.parametrize("count", [0, 1, 3, 6])
def test_seed_selection_uses_only_last_three_history_messages(client, llm, count):
    query = f"  {QUERY}  "
    history = HISTORY[:count]
    response = client.post(ENDPOINT, json={"query": query, "history": history})
    assert response.status_code == 200
    assert len(llm.calls) == 1
    assert llm.calls[0]["messages"][1:-1] == history[-3:]
    assert json.loads(llm.calls[0]["messages"][-1]["content"])["query"] == query


def test_separate_requests_do_not_share_hop_limits_or_token_totals(client, llm):
    llm.results = [completion(["A", "ISOLATED"]) for _ in range(3)]
    for fields, names, depth in [
        ({"max_hop": 0}, ["ISOLATED", "A"], 0),
        ({"max_hop": -1}, ["D", "ISOLATED", "C", "B", "A"], 10),
        ({}, ["D", "ISOLATED", "C", "B", "A"], 3),
    ]:
        response = client.post(ENDPOINT, json={"query": QUERY, **fields})
        assert response.status_code == 200
        assert response.json()["data"] == expected_data(names)
        metadata = response.json()["meta_data"][0]
        assert metadata["graph_depth"] == depth
        assert metadata["question_token"] == 100 and metadata["answer_token"] == 20
    assert len(llm.calls) == llm.closed == 3


def test_isolated_seeds_and_duplicate_selections_are_preserved_once(client, settings, llm):
    save_relations(settings, [])
    llm.results = [completion(["A", "A", "ISOLATED"])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["ISOLATED", "A"])
    assert response.json()["meta_data"][0]["seed_count"] == 2
    assert len(llm.calls) == 1


def test_empty_seed_selection_returns_empty_lists(client, llm):
    llm.results = [completion([])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["expanded_count"] == 0
    assert response.json()["meta_data"][0]["retrieved_count"] == 0
    assert len(llm.calls) == 1


def test_empty_catalog_skips_llm(client, settings, llm):
    Path(settings.dataset_path).write_text("[]")
    save_relations(settings, [])
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["question_token"] == 0
    assert llm.calls == [] and llm.closed == 0


@pytest.mark.parametrize("payload", [
    {}, {"query": ""}, {"query": " \n"}, {"query": 123}, {"query": None},
    {"query": QUERY, "history": None}, {"query": QUERY, "top_k": 0}, {"query": QUERY, "graph_depth": 1},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "history": [{"role": "user", "content": " "}]},
    *({"query": QUERY, "max_hop": value} for value in (-2, "3", 3.0, True, False, None)),
])
def test_invalid_requests_are_422_without_llm(client, llm, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert llm.calls == []


@pytest.mark.parametrize(("damage", "status"), [
    ("missing_rdf", 404), ("invalid_rdf", 409), ("unknown_table", 409),
    ("missing_catalog", 500), ("invalid_catalog", 500),
])
def test_catalog_and_rdf_errors_preserve_files_without_llm(client, settings, llm, damage, status):
    rdf = Path(settings.rdf_triplet) / "triplets.json"
    source = Path(settings.dataset_path)
    if damage == "missing_rdf":
        rdf.unlink()
        other = Path(settings.rdf_triplet_open_ai) / "triplets.json"
        other.parent.mkdir()
        other.write_text("[]")
    elif damage == "invalid_rdf":
        rdf.write_text("not json")
    elif damage == "unknown_table":
        save_relations(settings, [("UNKNOWN", "references", "A")])
    elif damage == "missing_catalog":
        source.unlink()
    else:
        source.write_text("not json")
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == status
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize(("field", "value"), [("llm_api_base", ""), ("model_name", ""), ("rdf_triplet", "")])
def test_configuration_errors_return_500_without_llm(client, settings, llm, field, value):
    setattr(settings, field, value)
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 500
    assert llm.calls == []


@pytest.mark.parametrize("failure", ["api", "refusal", "length", "malformed", "unknown_table", "blank_reason"])
def test_invalid_seed_results_return_502_without_partial_schemas(client, settings, llm, failure):
    result = completion(["A"])
    if failure == "api":
        result = RuntimeError("LLM unavailable")
    elif failure == "refusal":
        result.choices[0].message.refusal = "refused"
    elif failure == "length":
        result.choices[0].finish_reason = "length"
    elif failure == "malformed":
        result.choices[0].message.parsed = {"tables": ["A"]}
    elif failure == "unknown_table":
        result = completion(["UNKNOWN"])
    else:
        result.choices[0].message.parsed["tables"][0]["reason"] = " "
    llm.results = [result]
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 502
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(llm.calls) == llm.closed == 1 and snapshot(settings) == before


def test_service_returns_dedicated_response_models(settings, llm):
    service = create_app(settings).state.services.only_triplet_based_schema_service
    response = asyncio.run(service.triplet_based_schema(OnlyTripletBasedSchemaRequest(query=QUERY)))
    assert type(response) is OnlyTripletBasedSchemaResponse
    assert type(response.data) is OnlyTripletBasedSchemaData


def test_openapi_uses_dedicated_request_response_and_data_models(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/OnlyTripletBasedSchemaRequest")
    for code in ("200", "404", "409", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/OnlyTripletBasedSchemaResponse")
    assert "422" in operation["responses"] and len(spec["paths"]) == 25
    models = spec["components"]["schemas"]
    request = models["OnlyTripletBasedSchemaRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"query", "history", "max_hop", "top_k"}
    assert request["properties"]["top_k"]["type"] == "integer"
    assert request["properties"]["top_k"]["default"] == -1
    assert request["properties"]["max_hop"]["default"] == 3
    assert request["properties"]["max_hop"]["minimum"] == -1
    assert "history[-3:]" in request["properties"]["history"]["description"]
    assert set(models["OnlyTripletBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}
    assert "/OnlyTripletBasedSchemaData" in json.dumps(models["OnlyTripletBasedSchemaResponse"])


def test_real_client_calls_configured_llm_once_with_dummy_key(client, settings, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_LLM_MODEL
        return httpx.Response(200, json={
            "id": "chatcmpl-seeds", "object": "chat.completion", "created": 1, "model": DEFAULT_LLM_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"tables": [{"table_name": "A", "reason": "필요"}]}),
            }}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings:
                        create_llm_client(settings, http_client=transport, max_retries=0))
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 2})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["C", "B", "A"])
    assert len(requests) == 1 and transport.is_closed


def test_cancelling_seed_selection_closes_llm_and_preserves_files(settings, llm):
    service = create_app(settings).state.services.only_triplet_based_schema_service
    before = snapshot(settings)

    async def scenario():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.triplet_based_schema(OnlyTripletBasedSchemaRequest(query=QUERY)))
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


def test_logs_use_endpoint_operation_and_skip_final_selection(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if getattr(record, "operation", None) == "only_triplet_based_schema"]
    assert len({record.request_id for record in records}) == 1
    stages = {record.stage for record in records}
    assert {"request", "snapshot_load", "seed_selection", "graph_expansion", "complete"} <= stages
    assert "final_selection" not in stages
