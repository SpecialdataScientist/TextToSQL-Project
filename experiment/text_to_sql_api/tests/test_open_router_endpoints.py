import asyncio
from copy import deepcopy
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
from app.core.config import AppSettings, DEFAULT_OPENROUTER_LLM_MODEL, OPENROUTER_API_BASE
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import GeneratedSqlOutput, GeneratedTripletsOutput, SchemaTableSelectionOutput
from app.schemas.request import (
    LlmBasedGenerateSqlRequest, LlmBasedSchemaOpenRouterRequest, OnlyTripletBasedSchemaOpenRouterRequest,
)
from app.schemas.response import LlmBasedSchemaOpenRouterResponse, OnlyTripletBasedSchemaOpenRouterResponse
from app.schemas.triplet import RDF_TRIPLETS
from app.services import (
    generate_triplet_service, llm_based_generate_sql_service, llm_based_schema_service,
    open_ai_clients, open_router_clients, rdf_triplet_store, triplet_based_schema_service,
)
from app.services.rdf_triplet_store import RdfTripletStore
from main import create_app


GENERATE = "/retrieve/generate_triplet_open_router"
SQL_ENDPOINT = "/generate_sql/llm_based_generate_sql_open_router"
SCHEMA = "/retrieve/llm_based_schema_open_router"
ONLY = "/retrieve/only_triplet_based_schema_open_router"
ENDPOINTS = (GENERATE, SQL_ENDPOINT, SCHEMA, ONLY)
FAKE_KEY = "test-openrouter-private-key"
MODEL = "deepseek/deepseek-v4-flash-0731"
QUERY = "  최근 조건에 맞게 조회해줘  "
SQL = "SELECT id FROM A;"
HISTORY = [{"role": "user" if i % 2 == 0 else "assistant", "content": f" 이전 대화 {i} "} for i in range(6)]
RELATION = {"subject_table_name": "A", "predicate": "references", "object_table_name": "B"}
DEFAULT_OUTPUTS = {
    GeneratedSqlOutput: {"sql": SQL},
    GeneratedTripletsOutput: {"triplets": [RELATION]},
    SchemaTableSelectionOutput: {"tables": [
        {"table_name": "A", "reason": "질의 대상"}, {"table_name": "ISOLATED", "reason": "독립 통계"},
    ]},
}


def table(name):
    return {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명 "}


def expected_data(names):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


def save_relations(directory, pairs=(("B", "A"), ("C", "B"), ("D", "C"), ("B", "A"), ("A", "B"), ("B", "B"))):
    triplets = [[
        {"table_name": subject, "description": "과거 설명"}, "references",
        {"table_name": target, "description": "과거 설명"},
    ] for subject, target in pairs]
    RdfTripletStore(Path(directory)).save(RDF_TRIPLETS.validate_python(triplets))


def snapshot(settings):
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


def request_body(endpoint):
    body = {"query": QUERY, "history": HISTORY}
    if endpoint == SQL_ENDPOINT:
        body["schema"] = expected_data(["A", "B"])
    elif endpoint == ONLY:
        body.update(top_k=1, max_hop=2)
    return body


def invoke(settings, endpoint, body=None):
    with TestClient(create_app(settings)) as client:
        return client.post(endpoint) if endpoint == GENERATE else client.post(endpoint, json=body if body is not None else request_body(endpoint))


def service_call(app, endpoint):
    if endpoint == GENERATE:
        return app.state.services.generate_triplet_open_router_service.generate_triplet()
    if endpoint == SQL_ENDPOINT:
        return app.state.services.llm_based_generate_sql_open_router_service.generate_sql(
            LlmBasedGenerateSqlRequest.model_validate(request_body(endpoint)),
        )
    if endpoint == SCHEMA:
        return app.state.services.llm_based_schema_open_router_service.llm_based_schema(
            LlmBasedSchemaOpenRouterRequest.model_validate(request_body(endpoint)),
        )
    return app.state.services.only_triplet_based_schema_open_router_service.triplet_based_schema(
        OnlyTripletBasedSchemaOpenRouterRequest.model_validate(request_body(endpoint)),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.outputs = {}
        self.error = None
        self.finish_reason = "stop"
        self.refusal = None
        self.started = None
        self.gate = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.started is not None:
            self.started.set()
            await self.gate.wait()
        if self.error:
            raise self.error
        model = kwargs["response_format"]
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=self.finish_reason, message=SimpleNamespace(
                refusal=self.refusal, parsed=deepcopy(self.outputs.get(model, DEFAULT_OUTPUTS[model])),
            ))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps([table(name) for name in ("D", "C", "B", "A", "ISOLATED")]))
    settings = AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "local rdf"),
        rdf_triplet_open_ai=str(tmp_path / "open ai rdf"), rdf_triplet_open_router=str(tmp_path / "router rdf"),
        llm_api_base="", model_name="", openai_api_key="", openai_llm_model_name="",
        openrouter_api_key=FAKE_KEY, openrouter_llm_model_name=MODEL,
    )
    save_relations(settings.rdf_triplet, [("D", "ISOLATED")])
    save_relations(settings.rdf_triplet_open_ai, [("A", "ISOLATED")])
    save_relations(settings.rdf_triplet_open_router)
    return settings


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(open_router_clients, "create_open_router_llm_client", lambda settings: fake)

    def reject_other_provider(*args, **kwargs):
        pytest.fail("OpenRouter endpoints must use the OpenRouter client")

    for module in (generate_triplet_service, llm_based_generate_sql_service, llm_based_schema_service, triplet_based_schema_service):
        monkeypatch.setattr(module, "create_llm_client", reject_other_provider)
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", reject_other_provider)
    return fake


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_reuses_original_behavior_with_one_router_call_and_expected_options(settings, llm, endpoint):
    before = snapshot(settings)
    result = invoke(settings, endpoint)
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == body["msg"] == "success"
    meta = body["meta_data"][0]
    assert meta["model_name"] == MODEL and meta["question_token"] == 100 and meta["answer_token"] == 20
    call, = llm.calls
    assert llm.closed == 1 and call["model"] == MODEL and call["temperature"] == 0.0
    assert call["extra_body"] == {"reasoning": {"enabled": False}, "provider": {"require_parameters": True}}
    if endpoint == GENERATE:
        expected = [[{key: table("A")[key] for key in ("table_name", "description")}, "references",
                     {key: table("B")[key] for key in ("table_name", "description")}]]
        assert body["data"] == expected
        store = RdfTripletStore(Path(settings.rdf_triplet_open_router))
        assert json.loads(store.path.read_text()) == expected and meta["triplet_file"] == str(store.path)
        after = snapshot(settings)
        assert set(after) == set(before)
        assert all(after[path] == value for path, value in before.items() if path != str(store.path))
    else:
        assert call["messages"][1:-1] == HISTORY[-3:]
        payload = json.loads(call["messages"][-1]["content"])
        assert payload["query"] == QUERY
        assert snapshot(settings) == before
        if endpoint == SQL_ENDPOINT:
            assert body["data"] == SQL and payload["schema"] == expected_data(["A", "B"])
        elif endpoint == SCHEMA:
            assert body["data"] == expected_data(["A", "ISOLATED"])
        else:
            assert body["data"] == expected_data(["C", "B", "A"])
            assert meta["seed_count"] == 1 and meta["expanded_count"] == meta["retrieved_count"] == 3


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("field,expected", [("openrouter_api_key", "OPENROUTER_API_KEY"), ("openrouter_llm_model_name", "OPENROUTER_LLM_MODEL_NAME")])
def test_missing_key_or_model_fails_before_loading_or_writing(settings, llm, endpoint, field, expected):
    setattr(settings, field, " ")
    Path(settings.dataset_path).write_text("[]")
    before = snapshot(settings)
    result = invoke(settings, endpoint)
    assert result.status_code == 500 and expected in result.json()["msg"]
    assert result.json()["data"] is None and llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("endpoint", [GENERATE, ONLY])
@pytest.mark.parametrize("other", ["rdf_triplet", "rdf_triplet_open_ai"])
@pytest.mark.parametrize("mode", ["same", "alias", "symlink"])
def test_router_rdf_must_be_separate_from_both_other_stores(settings, llm, endpoint, other, mode):
    target = Path(getattr(settings, other))
    if mode == "same":
        value = target
    elif mode == "alias":
        value = target / ".." / target.name
    else:
        value = target.with_name("router alias")
        value.symlink_to(target, target_is_directory=True)
    settings.rdf_triplet_open_router = str(value)
    before = snapshot(settings)
    result = invoke(settings, endpoint)
    assert result.status_code == 500 and "RDF_TRIPLET_OPEN_ROUTER" in result.json()["msg"]
    assert llm.calls == [] and snapshot(settings) == before


@pytest.mark.parametrize("endpoint", [SQL_ENDPOINT, SCHEMA])
def test_sql_and_catalog_search_do_not_require_rdf_settings(settings, llm, endpoint):
    settings.rdf_triplet = settings.rdf_triplet_open_ai = settings.rdf_triplet_open_router = ""
    assert invoke(settings, endpoint).status_code == 200 and len(llm.calls) == 1


def test_router_generation_allows_unconfigured_unused_rdf_stores(settings, llm):
    settings.rdf_triplet = settings.rdf_triplet_open_ai = ""
    assert invoke(settings, GENERATE).status_code == 200


def test_generated_router_triplets_are_used_by_only_triplet_search(settings, llm):
    llm.outputs[GeneratedTripletsOutput] = {"triplets": [RELATION, dict(RELATION, predicate=" references ")]}
    generated = invoke(settings, GENERATE)
    assert generated.status_code == 200 and len(generated.json()["data"]) == 1
    result = invoke(settings, ONLY)
    assert result.status_code == 200 and result.json()["data"] == expected_data(["B", "A"])
    assert len(llm.calls) == 2


def test_configured_router_model_and_project_relative_rdf_path_are_used(settings, llm, monkeypatch):
    root = Path(settings.dataset_path).parent
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    settings.rdf_triplet_open_router = "./custom-router-rdf"
    settings.openrouter_llm_model_name = "vendor/custom-model"
    result = invoke(settings, GENERATE)
    assert result.status_code == 200 and llm.calls[0]["model"] == "vendor/custom-model"
    assert result.json()["meta_data"][0]["triplet_file"] == str(root / "custom-router-rdf/triplets.json")
    assert (root / "custom-router-rdf/triplets.json").is_file()


@pytest.mark.parametrize("top_k,max_hop,names,depth", [
    (1, 0, ["A"], 0), (1, 1, ["B", "A"], 1), (1, 2, ["C", "B", "A"], 2),
    (1, 3, ["D", "C", "B", "A"], 3), (1, -1, ["D", "C", "B", "A"], 10),
    (-1, 0, ["A", "ISOLATED"], 0), (99, 0, ["A", "ISOLATED"], 0),
])
def test_only_triplet_seed_limit_hops_cycles_and_all_candidate_return(settings, llm, top_k, max_hop, names, depth):
    result = invoke(settings, ONLY, {"query": QUERY, "top_k": top_k, "max_hop": max_hop})
    assert result.status_code == 200 and result.json()["data"] == expected_data(names)
    assert result.json()["meta_data"][0]["graph_depth"] == depth and len(llm.calls) == 1


def test_only_triplet_defaults_and_auto_ten_hop_cap(settings, llm):
    initial = invoke(settings, ONLY, {"query": QUERY})
    assert initial.status_code == 200 and initial.json()["data"] == expected_data(["D", "C", "B", "A", "ISOLATED"])
    assert initial.json()["meta_data"][0]["graph_depth"] == 3
    Path(settings.dataset_path).write_text(json.dumps([table(f"T{i}") for i in reversed(range(13))]))
    save_relations(settings.rdf_triplet_open_router, [(f"T{i + 1}", f"T{i}") for i in range(12)])
    llm.outputs[SchemaTableSelectionOutput] = {"tables": [{"table_name": "T0", "reason": "시작점"}]}
    result = invoke(settings, ONLY, {"query": QUERY, "top_k": 1, "max_hop": -1})
    assert result.status_code == 200 and result.json()["data"] == expected_data([f"T{i}" for i in reversed(range(11))])


@pytest.mark.parametrize("damage,status", [("missing", 404), ("json", 409), ("unknown", 409)])
def test_only_triplet_reads_router_rdf_and_reports_router_generation_guidance(settings, llm, damage, status):
    path = Path(settings.rdf_triplet_open_router) / "triplets.json"
    if damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_text("not JSON")
    else:
        save_relations(settings.rdf_triplet_open_router, [("UNKNOWN", "A")])
    before = snapshot(settings)
    result = invoke(settings, ONLY)
    assert result.status_code == status and result.json()["data"] is None
    assert llm.calls == [] and snapshot(settings) == before
    if status == 404:
        assert "/retrieve/generate_triplet_open_router" in result.json()["msg"]


@pytest.mark.parametrize("failure", ["unknown", "refusal", "length", "transport"])
def test_failed_generation_preserves_all_existing_rdf_snapshots(settings, llm, failure):
    if failure == "unknown":
        llm.outputs[GeneratedTripletsOutput] = {"triplets": [dict(RELATION, object_table_name="UNKNOWN")]}
    elif failure == "refusal":
        llm.refusal = "Cannot generate"
    elif failure == "length":
        llm.finish_reason = "length"
    else:
        llm.error = TimeoutError("upstream timed out")
    before = snapshot(settings)
    result = invoke(settings, GENERATE)
    assert result.status_code == 502 and result.json()["data"] is None
    assert snapshot(settings) == before and llm.closed == 1


def test_failed_publication_preserves_router_snapshot(settings, llm, monkeypatch):
    def reject_replace(*args):
        raise OSError("cannot publish RDF")

    monkeypatch.setattr(rdf_triplet_store.os, "replace", reject_replace)
    before = snapshot(settings)
    result = invoke(settings, GENERATE)
    assert result.status_code == 500 and snapshot(settings) == before


@pytest.mark.parametrize("endpoint", [GENERATE, SCHEMA, ONLY])
def test_empty_catalog_skips_provider_calls(settings, llm, endpoint):
    Path(settings.dataset_path).write_text("[]")
    save_relations(settings.rdf_triplet_open_router, [])
    result = invoke(settings, endpoint)
    assert result.status_code == 200 and llm.calls == []
    assert result.json()["data"] == ([] if endpoint == GENERATE else expected_data([]))


@pytest.mark.parametrize("endpoint", [SCHEMA, ONLY])
def test_empty_table_selection_returns_empty_result_with_one_call(settings, llm, endpoint):
    llm.outputs[SchemaTableSelectionOutput] = {"tables": []}
    result = invoke(settings, endpoint)
    assert result.status_code == 200 and result.json()["data"] == expected_data([]) and len(llm.calls) == 1


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("phase", ["factory", "transport", "validation"])
def test_key_is_redacted_from_errors_and_formatted_exception_chains(settings, llm, endpoint, phase, monkeypatch, caplog):
    if phase == "factory":
        def fail(*args):
            try:
                raise ValueError(f"inner {FAKE_KEY}")
            except ValueError as error:
                raise RuntimeError(f"outer {FAKE_KEY}") from error
        monkeypatch.setattr(open_router_clients, "create_open_router_llm_client", fail)
    elif phase == "transport":
        llm.error = RuntimeError(f"upstream rejected {FAKE_KEY}")
    elif endpoint == GENERATE:
        llm.outputs[GeneratedTripletsOutput] = {"triplets": [dict(RELATION, subject_table_name=FAKE_KEY)]}
    elif endpoint == SQL_ENDPOINT:
        llm.outputs[GeneratedSqlOutput] = {"sql": "", "unexpected": FAKE_KEY}
    else:
        llm.outputs[SchemaTableSelectionOutput] = {"tables": [{"table_name": FAKE_KEY, "reason": "unknown"}]}
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(caplog.handler)
    try:
        result = invoke(settings, endpoint)
        assert result.status_code == (500 if phase == "factory" else 502)
        assert FAKE_KEY not in result.text and "[REDACTED]" in result.json()["msg"]
        assert result.json()["data"] is None and result.json()["meta_data"] == []
        assert FAKE_KEY not in caplog.text
        console = next(handler for handler in logger.handlers if handler.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("status", [200, 401, 429])
def test_real_sdk_uses_router_url_key_model_and_strict_json_schema(settings, endpoint, status, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["temperature"] == 0
        assert body["reasoning"] == {"enabled": False} and body["provider"] == {"require_parameters": True}
        assert "thinking" not in body
        schema = body["response_format"]
        assert schema["type"] == "json_schema" and schema["json_schema"]["strict"] is True
        model = next(model for model in DEFAULT_OUTPUTS if model.__name__ == schema["json_schema"]["name"])
        if endpoint != GENERATE:
            assert body["messages"][1:-1] == HISTORY[-3:]
            assert json.loads(body["messages"][-1]["content"])["query"] == QUERY
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"invalid key {FAKE_KEY}", "code": status}})
        return httpx.Response(200, json={
            "id": "router-completion", "object": "chat.completion", "created": 1, "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(DEFAULT_OUTPUTS[model]),
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    client_factory = open_router_clients.create_open_router_llm_client
    monkeypatch.setattr(open_router_clients, "create_open_router_llm_client", lambda profile:
                        client_factory(profile, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)), max_retries=0))
    result = invoke(settings, endpoint)
    assert len(requests) == 1 and FAKE_KEY not in result.text
    assert result.status_code == (200 if status == 200 else 502)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_cancelled_inference_closes_client_without_modifying_storage(settings, llm, endpoint):
    before = snapshot(settings)

    async def run():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service_call(create_app(settings), endpoint))
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert llm.closed == 1 and snapshot(settings) == before


@pytest.mark.parametrize("endpoint,body", [
    (SCHEMA, {"query": " "}), (SCHEMA, {"query": QUERY, "top_k": 1}),
    (ONLY, {"query": QUERY, "top_k": 0}), (ONLY, {"query": QUERY, "max_hop": -2}),
    (ONLY, {"query": QUERY, "history": [{"role": "system", "content": "invalid"}]}),
    (SQL_ENDPOINT, {"query": QUERY, "schema": {"table_name": ["A"], "schema": [], "description": []}}),
])
def test_invalid_requests_are_rejected_before_provider_call(settings, llm, endpoint, body):
    assert invoke(settings, endpoint, body).status_code == 422 and llm.calls == []


def test_openapi_contains_registered_router_endpoints(settings, llm):
    with TestClient(create_app(settings)) as client:
        spec = client.get("/openapi.json").json()
    assert len(spec["paths"]) == 25
    assert {path for path in spec["paths"] if path.endswith("_open_router")} == set(ENDPOINTS) | {
        "/retrieve/triplet_based_schema_open_router",
    }
    for endpoint, request, response in (
        (SCHEMA, "LlmBasedSchemaOpenRouterRequest", "LlmBasedSchemaOpenRouterResponse"),
        (ONLY, "OnlyTripletBasedSchemaOpenRouterRequest", "OnlyTripletBasedSchemaOpenRouterResponse"),
    ):
        operation = spec["paths"][endpoint]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith('/' + request)
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith('/' + response)
        assert "422" in operation["responses"]
    app = create_app(settings)
    assert type(asyncio.run(service_call(app, SCHEMA))) is LlmBasedSchemaOpenRouterResponse
    assert type(asyncio.run(service_call(app, ONLY))) is OnlyTripletBasedSchemaOpenRouterResponse


def test_config_defaults_dotenv_environment_precedence_and_secret_repr(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        defaults = AppSettings()
        assert defaults.openrouter_api_key == "" and defaults.openrouter_llm_model_name == DEFAULT_OPENROUTER_LLM_MODEL == MODEL
        assert defaults.rdf_triplet_open_router == "./rdf_triplet_open_router"
        (tmp_path / '.env').write_text(f'OPENROUTER_API_KEY={FAKE_KEY}\nOPENROUTER_LLM_MODEL_NAME=vendor/custom-model\nRDF_TRIPLET_OPEN_ROUTER=./router-store\n')
        settings = AppSettings()
        assert settings.openrouter_api_key == FAKE_KEY and settings.openrouter_llm_model_name == "vendor/custom-model"
        assert FAKE_KEY not in repr(settings)
        assert config.resolve_project_path(settings.rdf_triplet_open_router) == tmp_path / "router-store"
        os.environ['OPENROUTER_LLM_MODEL_NAME'] = MODEL
        os.environ['RDF_TRIPLET_OPEN_ROUTER'] = './override'
        assert AppSettings().openrouter_llm_model_name == MODEL
        assert AppSettings().rdf_triplet_open_router == './override'


def test_router_client_forces_url_and_key_and_keeps_existing_timeout_defaults(settings):
    client = open_router_clients.create_open_router_llm_client(settings, base_url="https://unused.test/v1", api_key="unused")
    try:
        assert str(client.base_url).rstrip('/') == OPENROUTER_API_BASE
        assert client.api_key == FAKE_KEY and client.timeout == 60.0 and client.max_retries == 2
    finally:
        asyncio.run(client.close())


def test_server_can_start_without_router_key(settings):
    settings.openrouter_api_key = ""
    with TestClient(create_app(settings)) as client:
        assert client.get('/openapi.json').status_code == 200
