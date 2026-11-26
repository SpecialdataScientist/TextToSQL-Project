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
from app.schemas.request import LlmBasedGenerateSqlRequest, TripletBasedSchemaRequest
from app.services import triplet_based_schema_service as service_module
from app.services.llm_client import create_llm_client
from app.services.rdf_triplet_store import RdfTripletStore
from app.services.triplet_based_schema_service import TripletBasedSchemaService
from main import create_app


ENDPOINT = "/retrieve/triplet_based_schema"
QUERY = "최근 조건을 적용해서 연결된 데이터와 독립 통계를 조회해줘"
TABLES = [
    {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER, total NUMBER);\n",
     "description": f"  {name} 원본 설명\n"}
    for name in ("D", "ISOLATED", "C", "B", "A", "UNRELATED")
]
BY_NAME = {table["table_name"]: table for table in TABLES}
RELATIONS = [
    ("B", "belongs to", "A"),
    ("C", "references", "B"),
    ("D", "references", "C"),
    ("B", "belongs to", "A"),  # Exact duplicate.
    ("A", "references", "B"),  # Directed cycle.
    ("B", "references", "B"),  # Self loop.
]
HISTORY = [
    {"role": "user", "content": "오래된 질문"},
    {"role": "assistant", "content": "오래된 답변"},
    {"role": "user", "content": "두 번째 오래된 질문"},
    {"role": "assistant", "content": "최근 대상 안내"},
    {"role": "user", "content": "최근 필터 조건"},
    {"role": "assistant", "content": "최근 집계 조건"},
]


def completion(names, prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": f"{name} 선정 근거"} for name in names]},
        ))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeLlmClient:
    def __init__(self):
        self.calls = []
        self.closed_count = 0
        self.started = None
        self.gate = None
        self.results = [completion(["A", "ISOLATED"]), completion(["C", "B", "A", "ISOLATED"], 200, 30)]
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed_count += 1

    async def parse(self, **kwargs):
        result = self.results[len(self.calls)]
        self.calls.append(copy.deepcopy(kwargs))
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(result, Exception):
            raise result
        return result


def save_relations(settings, relations=RELATIONS):
    path = Path(settings.rdf_triplet) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # RDF descriptions may be stale; selection must use the current catalog's originals.
    path.write_text(json.dumps([
        [{"table_name": subject, "description": "이전 설명"}, predicate,
         {"table_name": target, "description": "이전 설명"}]
        for subject, predicate, target in relations
    ], ensure_ascii=False), encoding="utf-8")
    return path


def expected_data(names):
    return {key: [BY_NAME[name][key] for name in names] for key in ("table_name", "schema", "description")}


def snapshot(settings):
    return {str(path): path.read_bytes() for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps(TABLES, ensure_ascii=False), encoding="utf-8")
    settings = AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "rdf_triplet"),
        rdf_triplet_open_ai=str(tmp_path / "rdf_triplet_open_ai"),
        embedded_schema=str(tmp_path / "embedded_schema"),
        embedded_schema_open_ai=str(tmp_path / "embedded_schema_open_ai"),
        llm_api_base="https://llm.test/v1", model_name=DEFAULT_LLM_MODEL, openai_api_key="",
    )
    save_relations(settings)
    return settings


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlmClient()
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: fake)
    return fake


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings)) as client:
        yield client


def test_full_pipeline_preserves_bridge_tables_isolated_table_and_originals(client, settings, llm):
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["C", "B", "A", "ISOLATED"])
    assert body["status"] == body["msg"] == "success"
    assert body["elapsed_time"] >= 0
    assert body["meta_data"] == [{
        "model_name": DEFAULT_LLM_MODEL, "question_token": 300, "answer_token": 50,
        "seed_count": 2, "expanded_count": 4, "retrieved_count": 4, "graph_depth": 2,
    }]
    assert len(llm.calls) == 2 and llm.closed_count == 1
    seed_payload = json.loads(llm.calls[0]["messages"][-1]["content"])
    assert seed_payload == {"query": QUERY, "catalog": [
        {"table_name": table["table_name"], "description": table["description"]} for table in TABLES
    ]}
    final_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final_payload["seed_tables"] == ["A", "ISOLATED"]
    assert final_payload["candidates"] == [BY_NAME[name] for name in ("ISOLATED", "C", "B", "A")]
    # Incoming edges are traversed but keep their original direction; D is three hops away.
    assert final_payload["relations"] == [
        ["B", "belongs to", "A"], ["C", "references", "B"],
        ["A", "references", "B"], ["B", "references", "B"],
    ]
    for call in llm.calls:
        assert call["response_format"] is SchemaTableSelectionOutput
        assert call["model"] == DEFAULT_LLM_MODEL and call["temperature"] == 0
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert snapshot(settings) == before
    assert not Path(settings.embedded_schema).exists()
    assert not Path(settings.embedded_schema_open_ai).exists()
    # The response data can be passed straight to SQL generation without a shape conversion.
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize(("max_hop", "names", "graph_depth"), [
    (None, ["D", "ISOLATED", "C", "B", "A"], 3),
    (0, ["ISOLATED", "A"], 0),
    (1, ["ISOLATED", "B", "A"], 1),
    (2, ["ISOLATED", "C", "B", "A"], 2),
    (3, ["D", "ISOLATED", "C", "B", "A"], 3),
    (-1, ["D", "ISOLATED", "C", "B", "A"], 10),
])
def test_request_max_hop_controls_final_candidates_and_metadata(client, llm, max_hop, names, graph_depth):
    llm.results[1] = completion(names)
    payload = {"query": QUERY}
    if max_hop is not None:
        payload["max_hop"] = max_hop
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(names)
    assert body["meta_data"][0]["graph_depth"] == graph_depth
    assert body["meta_data"][0]["expanded_count"] == len(names)
    assert len(llm.calls) == 2
    final_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final_payload["candidates"] == [BY_NAME[name] for name in names]


def test_max_hop_is_independent_between_requests(client, llm):
    cases = [
        ({"max_hop": 0}, ["ISOLATED", "A"], 0),
        ({"max_hop": -1}, ["D", "ISOLATED", "C", "B", "A"], 10),
        ({}, ["D", "ISOLATED", "C", "B", "A"], 3),
        ({"max_hop": 1}, ["ISOLATED", "B", "A"], 1),
    ]
    llm.results = [result for _, names, _ in cases for result in (
        completion(["A", "ISOLATED"]), completion(names),
    )]
    for fields, names, graph_depth in cases:
        response = client.post(ENDPOINT, json={"query": QUERY, **fields})
        assert response.status_code == 200
        assert response.json()["data"] == expected_data(names)
        assert response.json()["meta_data"][0]["graph_depth"] == graph_depth
    assert len(llm.calls) == 2 * len(cases)


@pytest.mark.parametrize("count", [0, 1, 3, 4, 6])
def test_both_llm_stages_use_only_last_three_history_messages(client, llm, count):
    query = f"  {QUERY}  "
    history = HISTORY[:count]
    response = client.post(ENDPOINT, json={"query": query, "history": history})
    assert response.status_code == 200
    for call in llm.calls:
        assert call["messages"][1:-1] == history[-3:]
        assert json.loads(call["messages"][-1]["content"])["query"] == query


def test_history_does_not_leak_across_requests(client, llm):
    llm.results *= 3
    for history in (HISTORY, None, []):
        payload = {"query": QUERY}
        if history is not None:
            payload["history"] = history
        assert client.post(ENDPOINT, json=payload).status_code == 200
    for call in llm.calls[2:]:
        assert len(call["messages"]) == 2


def test_isolated_table_is_searchable_with_no_relations(client, settings, llm):
    save_relations(settings, [])
    llm.results = [completion(["ISOLATED"]), completion(["ISOLATED"])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["ISOLATED"])
    final_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert final_payload["candidates"] == [BY_NAME["ISOLATED"]] and final_payload["relations"] == []


def test_duplicate_selections_are_removed_in_llm_order(client, llm):
    llm.results = [completion(["A", "A"]), completion(["C", "B", "C", "A", "B"])]
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["C", "B", "A"])
    assert response.json()["meta_data"][0]["seed_count"] == 1


@pytest.mark.parametrize("stage", [0, 1])
def test_empty_selection_returns_empty_parallel_lists(client, llm, stage):
    llm.results[stage] = completion([])
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data([])
    assert len(llm.calls) == stage + 1
    assert response.json()["meta_data"][0]["retrieved_count"] == 0
    if stage == 0:
        assert response.json()["meta_data"][0]["expanded_count"] == 0


def test_empty_catalog_skips_llm(client, settings, llm):
    Path(settings.dataset_path).write_text("[]")
    save_relations(settings, [])
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["question_token"] == 0
    assert llm.calls == [] and llm.closed_count == 0


@pytest.mark.parametrize("usage", [None, SimpleNamespace(prompt_tokens=None, completion_tokens=None)])
def test_missing_usage_counts_as_zero(client, llm, usage):
    for result in llm.results:
        result.usage = usage
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    meta = response.json()["meta_data"][0]
    assert meta["question_token"] == meta["answer_token"] == 0


@pytest.mark.parametrize("payload", [
    {}, {"query": None}, {"query": 1}, {"query": True}, {"query": ""}, {"query": " \n\t"},
    {"query": QUERY, "top_k": 0}, {"query": QUERY, "graph_depth": 1},
    {"query": QUERY, "history": None}, {"query": QUERY, "history": {}},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]},
    {"query": QUERY, "history": [{"role": "user", "content": " \n"}]},
    {"query": QUERY, "history": [{"role": "assistant", "content": 3}]},
    {"query": QUERY, "history": [{"role": "user"}]},
    {"query": QUERY, "history": [{"role": "system", "content": "old invalid message"}, *HISTORY[-3:]]},
])
def test_invalid_requests_return_422_without_llm(client, llm, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert llm.calls == []


@pytest.mark.parametrize("max_hop", [-2, -10, "3", 3.0, 1.5, True, False, None, [], {}])
def test_invalid_max_hop_returns_422_without_llm(client, llm, max_hop):
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": max_hop})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "max_hop"]
    assert llm.calls == []


def test_missing_rdf_does_not_use_open_ai_store_or_create_files(client, settings, llm):
    path = Path(settings.rdf_triplet) / "triplets.json"
    path.unlink()
    other = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    other.parent.mkdir()
    other.write_text("[]")
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 404
    assert "/retrieve/generate_triplet" in response.json()["msg"]
    assert response.json()["data"] is None
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["json", "structure", "unknown_table", "padded_name"])
def test_invalid_rdf_returns_409_without_modification(client, settings, llm, damage):
    path = Path(settings.rdf_triplet) / "triplets.json"
    if damage == "json":
        path.write_text("not json")
    elif damage == "structure":
        path.write_text('[{"subject": "A"}]')
    else:
        save_relations(settings, [("MISSING" if damage == "unknown_table" else " A ", "refers to", "B")])
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["missing", "json", "schema", "duplicate"])
def test_invalid_catalog_returns_500_before_llm(client, settings, llm, damage):
    path = Path(settings.dataset_path)
    if damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_text("not json")
    elif damage == "schema":
        path.write_text('[{"table_name": "A"}]')
    else:
        path.write_text(json.dumps([TABLES[0], TABLES[0]]))
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 500
    assert llm.calls == []


@pytest.mark.parametrize("field,value", [
    ("llm_api_base", ""), ("llm_api_base", "not-a-url"), ("model_name", ""),
    ("rdf_triplet", ""), ("dataset_path", ""),
])
def test_invalid_configuration_returns_500(client, settings, llm, field, value):
    setattr(settings, field, value)
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 500
    assert llm.calls == []


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("failure", [
    "server", "no_choices", "refusal", "length", "missing_parsed", "bad_structure", "unknown_table",
    "outside_candidates", "blank_reason", "padded_name",
])
def test_invalid_llm_results_return_502_without_partial_data(client, settings, llm, stage, failure):
    result = completion(["A"])
    message = result.choices[0].message
    if failure == "server":
        result = RuntimeError("llm server unavailable")
    elif failure == "no_choices":
        result.choices = []
    elif failure == "refusal":
        message.refusal = "refused"
    elif failure == "length":
        result.choices[0].finish_reason = "length"
    elif failure == "missing_parsed":
        message.parsed = None
    elif failure == "bad_structure":
        message.parsed = {"tables": ["A"]}
    elif failure == "unknown_table":
        result = completion(["MISSING"])
    elif failure == "outside_candidates":
        # Valid catalog name, but outside the two-hop neighborhood in the final stage.
        result = completion(["D"] if stage else ["MISSING"])
    elif failure == "blank_reason":
        message.parsed["tables"][0]["reason"] = " \n"
    else:
        message.parsed["tables"][0]["table_name"] = " A "
    llm.results[stage] = result
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "max_hop": 2})
    assert response.status_code == 502
    body = response.json()
    assert body["data"] is None and body["meta_data"] == []
    assert body["status"] == "error" and body["elapsed_time"] >= 0
    assert llm.closed_count == 1 and len(llm.calls) == stage + 1
    assert snapshot(settings) == before


def test_requests_read_fresh_catalog_and_relations(client, settings, llm):
    llm.results = [completion(["A"]), completion(["C"]), completion(["A"]), completion(["D"])]
    assert client.post(ENDPOINT, json={"query": QUERY}).json()["data"] == expected_data(["C"])
    save_relations(settings, [("A", "references", "D")])
    updated_tables = copy.deepcopy(TABLES)
    updated_tables[0]["schema"] = "CREATE TABLE D (changed_id INTEGER);"
    Path(settings.dataset_path).write_text(json.dumps(updated_tables))
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"]["schema"] == [updated_tables[0]["schema"]]
    final_payload = json.loads(llm.calls[-1]["messages"][-1]["content"])
    assert {t["table_name"] for t in final_payload["candidates"]} == {"A", "D"}


@pytest.mark.parametrize("stage", [0, 1])
def test_cancellation_closes_client_and_preserves_files(settings, stage):
    fake = FakeLlmClient()
    before = snapshot(settings)

    async def scenario():
        fake.started = asyncio.Event()
        fake.gate = asyncio.Event()
        original_parse = fake.parse

        async def parse(**kwargs):
            if len(fake.calls) < stage:
                fake.gate.set()
                result = await original_parse(**kwargs)
                fake.gate.clear()
                fake.started.clear()
                return result
            return await original_parse(**kwargs)

        fake.chat.completions.parse = parse
        service = TripletBasedSchemaService(settings, client_factory=lambda settings: fake)
        task = asyncio.create_task(service.triplet_based_schema(TripletBasedSchemaRequest(query=QUERY)))
        try:
            await asyncio.wait_for(fake.started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert fake.closed_count == 1 and len(fake.calls) == stage + 1
    assert snapshot(settings) == before


def test_real_client_sends_both_stages_to_configured_server_without_open_ai_key(settings, monkeypatch):
    requests = []
    settings.openai_api_key = "unused-openai-key"
    settings.model_name = "openai/DeepSeek-V4-Flash-0731"

    def handle(request):
        index = len(requests)
        requests.append(request)
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_LLM_MODEL
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"]["type"] == "json_schema"
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert json.loads(body["messages"][-1]["content"])["query"] == QUERY
        return httpx.Response(200, json={
            "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 1, "model": DEFAULT_LLM_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "tables": [{"table_name": "A" if index == 0 else "C", "reason": "선정 근거"}],
                }, ensure_ascii=False),
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings:
                        create_llm_client(settings, http_client=async_client))
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["C"])
    assert response.json()["meta_data"][0]["question_token"] == 200
    assert len(requests) == 2 and async_client.is_closed


def test_openapi_has_endpoint_specific_models_with_seed_top_k(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/TripletBasedSchemaRequest")
    for code in ("200", "404", "409", "500", "502"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/TripletBasedSchemaResponse")
    assert "422" in operation["responses"]
    models = spec["components"]["schemas"]
    request_model = models["TripletBasedSchemaRequest"]
    assert request_model["required"] == ["query"]
    assert set(request_model["properties"]) == {"query", "history", "max_hop", "top_k"}
    assert request_model["properties"]["top_k"]["type"] == "integer"
    assert request_model["properties"]["top_k"]["default"] == -1
    assert request_model["properties"]["max_hop"]["type"] == "integer"
    assert request_model["properties"]["max_hop"]["default"] == 3
    assert request_model["properties"]["max_hop"]["minimum"] == -1
    assert request_model["additionalProperties"] is False
    assert "history[-3:]" in request_model["properties"]["history"]["description"]
    assert set(models["TripletBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}


def test_stage_logs_use_one_request_id(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if getattr(record, "operation", None) == "triplet_based_schema"]
    assert len({record.request_id for record in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"snapshot_load", "seed_selection", "graph_expansion", "final_selection"} <= {r.stage for r in records}
    assert "history_count=6 used_history_count=3" in records[0].getMessage()


def test_generator_store_format_is_readable_by_retrieval(client, settings):
    store = RdfTripletStore(Path(settings.rdf_triplet))
    # Re-publish through the same validation and atomic writer used by generate_triplet.
    store.save(store.load())
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200
