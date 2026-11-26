import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core import config
from app.core.config import AppSettings, DEFAULT_LLM_MODEL
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import GeneratedTripletsOutput
from app.schemas.triplet import RDF_TRIPLETS
from app.services import generate_triplet_service as service_module
from app.services import rdf_triplet_store as store_module
from app.services.generate_triplet_service import GenerateTripletService
from app.services.llm_client import create_llm_client
from app.services.rdf_triplet_store import RdfTripletStore
from main import create_app


ENDPOINT = "/retrieve/generate_triplet"
TABLES = [
    {"table_name": "ROOM", "schema": "CREATE TABLE ROOM (BUILDING_KEY VARCHAR2);",
     "description": "  건물에 소속된 공간 정보입니다.  "},
    {"table_name": "BUILDING", "schema": "CREATE TABLE BUILDING (BUILDING_KEY VARCHAR2);",
     "description": "건물 정보를 조회합니다."},
]
RELATION = {"subject_table_name": "ROOM", "predicate": "is located in", "object_table_name": "BUILDING"}


def expected_triplets(predicate="is located in"):
    return [[
        {"table_name": "ROOM", "description": TABLES[0]["description"]}, predicate,
        {"table_name": "BUILDING", "description": TABLES[1]["description"]},
    ]]


class FakeLlmClient:
    def __init__(self):
        self.calls = []
        self.closed_count = 0
        self.error = None
        self.started = None
        self.gate = None
        self.result = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                refusal=None, parsed={"triplets": [copy.deepcopy(RELATION)]},
            ))],
            usage=SimpleNamespace(prompt_tokens=150, completion_tokens=30),
        )
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
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps(TABLES, ensure_ascii=False), encoding="utf-8")
    return AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "rdf_triplet"),
        llm_api_base="https://llm.test/v1", model_name=DEFAULT_LLM_MODEL,
        embedded_schema=str(tmp_path / "embedded_schema"), openai_api_key="",
        embedded_schema_open_ai=str(tmp_path / "embedded_schema_open_ai"),
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


def seed_store(settings):
    store = RdfTripletStore(Path(settings.rdf_triplet))
    store.save(RDF_TRIPLETS.validate_python(expected_triplets("belongs to")))
    return store, store.path.read_bytes()


def test_returns_triplets_with_original_descriptions_and_persists_same_data(client, settings, llm):
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_triplets()
    assert body["status"] == body["msg"] == "success"
    assert body["elapsed_time"] >= 0
    store = RdfTripletStore(Path(settings.rdf_triplet))
    assert json.loads(store.path.read_text()) == body["data"]
    assert TABLES[0]["description"] in store.path.read_text()
    assert RDF_TRIPLETS.dump_python(store.load(), mode="json") == body["data"]
    assert body["meta_data"] == [{
        "model_name": DEFAULT_LLM_MODEL, "question_token": 150, "answer_token": 30,
        "table_count": 2, "triplet_count": 1, "triplet_file": str(store.path),
    }]
    assert len(llm.calls) == llm.closed_count == 1
    assert not Path(settings.embedded_schema).exists()
    assert not Path(settings.embedded_schema_open_ai).exists()


def test_prompt_contains_all_tables_ddl_and_descriptions_once(client, llm):
    assert client.post(ENDPOINT).status_code == 200
    call = llm.calls[0]
    assert call["response_format"] is GeneratedTripletsOutput
    assert call["model"] == DEFAULT_LLM_MODEL and call["temperature"] == 0
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert len(call["messages"]) == 2
    assert call["messages"][0]["role"] == "system"
    assert "SUBJECT -> predicate -> OBJECT" in call["messages"][0]["content"]
    assert json.loads(call["messages"][1]["content"]) == {"tables": TABLES}


def test_entire_real_dataset_is_forwarded_without_candidate_filtering(client, settings, llm):
    source = Path(__file__).resolve().parents[1] / "dataset/db_schema.json"
    settings.dataset_path = str(source)
    # The endpoint creates its service with the supplied settings object.
    llm.result.choices[0].message.parsed = {"triplets": []}
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    records = json.loads(source.read_text())
    assert len(records) == response.json()["meta_data"][0]["table_count"]
    assert json.loads(llm.calls[0]["messages"][1]["content"])["tables"] == records


def test_regenerates_and_replaces_previous_snapshot_with_latest_source(client, settings, llm):
    assert client.post(ENDPOINT).status_code == 200
    updated = copy.deepcopy(TABLES)
    updated[0]["description"] = "수정된 공간 설명"
    Path(settings.dataset_path).write_text(json.dumps(updated), encoding="utf-8")
    llm.result.choices[0].message.parsed["triplets"][0]["predicate"] = "belongs to"
    response = client.post(ENDPOINT)
    assert response.status_code == 200 and len(llm.calls) == 2
    assert response.json()["data"][0][0]["description"] == "수정된 공간 설명"
    assert response.json()["data"][0][1] == "belongs to"
    stored = json.loads((Path(settings.rdf_triplet) / "triplets.json").read_text())
    assert stored == response.json()["data"] and len(stored) == 1


def test_deduplicates_trimmed_relations_and_preserves_direction(client, llm):
    duplicate = dict(RELATION, predicate="  is located in  ")
    reverse = {"subject_table_name": "BUILDING", "predicate": "contains", "object_table_name": "ROOM"}
    llm.result.choices[0].message.parsed = {"triplets": [RELATION, duplicate, reverse]}
    body = client.post(ENDPOINT).json()
    assert len(body["data"]) == body["meta_data"][0]["triplet_count"] == 2
    assert body["data"][0] == expected_triplets()[0]
    assert body["data"][1] == [body["data"][0][2], "contains", body["data"][0][0]]


def test_no_relations_replaces_previous_snapshot_with_empty_list(client, settings, llm):
    store, _ = seed_store(settings)
    llm.result.choices[0].message.parsed = {"triplets": []}
    llm.result.usage = None
    body = client.post(ENDPOINT).json()
    assert body["status"] == "success" and body["data"] == []
    assert json.loads(store.path.read_text()) == []
    assert body["meta_data"][0]["question_token"] == body["meta_data"][0]["answer_token"] == 0


def test_empty_dataset_saves_empty_list_without_llm_call(client, settings, llm):
    Path(settings.dataset_path).write_text("[]")
    response = client.post(ENDPOINT)
    assert response.status_code == 200 and response.json()["data"] == []
    assert response.json()["meta_data"][0]["table_count"] == 0
    assert json.loads((Path(settings.rdf_triplet) / "triplets.json").read_text()) == []
    assert llm.calls == []


@pytest.mark.parametrize("invalid", [
    None, {}, {"triplets": None}, {"triplets": [{}]},
    {"triplets": [dict(RELATION, predicate=" ")]},
    {"triplets": [dict(RELATION, predicate=42)]},
    {"triplets": [dict(RELATION, subject_table_name="UNKNOWN")]},
    {"triplets": [dict(RELATION, object_table_name="UNKNOWN")]},
    {"triplets": [dict(RELATION, subject_table_name=" ROOM")]},
    {"triplets": [dict(RELATION, description="generated description")]},
    {"triplets": [RELATION], "extra": True},
])
def test_invalid_output_is_502_and_preserves_previous_file(client, settings, llm, invalid):
    store, previous = seed_store(settings)
    llm.result.choices[0].message.parsed = invalid
    response = client.post(ENDPOINT)
    assert response.status_code == 502
    assert response.json()["status"] == "error" and response.json()["data"] is None
    assert store.path.read_bytes() == previous
    assert list(store.root.glob(".triplets-*.tmp")) == []
    assert llm.closed_count == 1


@pytest.mark.parametrize("failure", ["refusal", "length", "no_choices", "timeout", "transport"])
def test_llm_failures_are_502_without_publishing(client, settings, llm, failure):
    store, previous = seed_store(settings)
    if failure == "refusal":
        llm.result.choices[0].message.refusal = "Cannot answer"
    elif failure == "length":
        llm.result.choices[0].finish_reason = "length"
    elif failure == "no_choices":
        llm.result.choices = []
    elif failure == "timeout":
        llm.error = TimeoutError("LLM timed out")
    else:
        llm.error = RuntimeError("upstream unavailable")
    assert client.post(ENDPOINT).status_code == 502
    assert store.path.read_bytes() == previous and llm.closed_count == 1


@pytest.mark.parametrize("invalid", ["{", "{}", '[{"table_name": "A"}]', "duplicate", "missing"])
def test_invalid_dataset_is_500_without_llm_or_file_changes(client, settings, llm, invalid):
    store, previous = seed_store(settings)
    source = Path(settings.dataset_path)
    if invalid == "missing":
        source.unlink()
    else:
        source.write_text(json.dumps(TABLES + [TABLES[0]]) if invalid == "duplicate" else invalid)
    response = client.post(ENDPOINT)
    assert response.status_code == 500 and response.json()["status"] == "error"
    assert store.path.read_bytes() == previous and llm.calls == []


@pytest.mark.parametrize("field,value", [
    ("rdf_triplet", " "), ("llm_api_base", ""), ("llm_api_base", "invalid"), ("model_name", " "),
])
def test_invalid_settings_are_500_and_do_not_call_llm(client, settings, llm, field, value):
    store, previous = seed_store(settings)
    setattr(settings, field, value)
    assert client.post(ENDPOINT).status_code == 500
    assert llm.calls == [] and store.path.read_bytes() == previous


def test_storage_failure_is_500_and_keeps_previous_file(client, settings, monkeypatch):
    store, previous = seed_store(settings)

    def fail_replace(*args):
        raise OSError("cannot replace snapshot")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    response = client.post(ENDPOINT)
    assert response.status_code == 500 and response.json()["data"] is None
    assert store.path.read_bytes() == previous
    assert list(store.root.glob(".triplets-*.tmp")) == []


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_failed_write_cleans_temporary_file_and_preserves_snapshot(settings, monkeypatch, failure):
    store, previous = seed_store(settings)

    def fail(*args, **kwargs):
        raise OSError("write failed")

    if failure == "write":
        monkeypatch.setattr(store_module.json, "dump", fail)
    else:
        monkeypatch.setattr(store_module.os, "fsync", fail)
    with pytest.raises(OSError):
        store.save(RDF_TRIPLETS.validate_python(expected_triplets()))
    assert store.path.read_bytes() == previous
    assert list(store.root.glob(".triplets-*.tmp")) == []


def test_concurrent_writers_and_reader_observe_complete_snapshots(settings):
    store, _ = seed_store(settings)
    candidates = [RDF_TRIPLETS.validate_python(expected_triplets(f"relation {index}")) for index in range(12)]
    possible = {f"relation {index}" for index in range(12)} | {"belongs to"}
    with ThreadPoolExecutor(max_workers=5) as pool:
        writes = [pool.submit(store.save, candidate) for candidate in candidates]
        for _ in range(30):
            snapshot = store.load()
            assert len(snapshot) == 1 and snapshot[0][1] in possible
        for future in writes:
            future.result(timeout=5)
    assert store.load()[0][1] in possible
    assert list(store.root.glob(".triplets-*.tmp")) == []


def test_reading_absent_snapshot_does_not_create_storage(tmp_path):
    store = RdfTripletStore(tmp_path / "absent")
    with pytest.raises(FileNotFoundError):
        store.load()
    assert not store.root.exists()


def test_relative_paths_follow_project_root(settings, llm, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings.dataset_path = "db_schema.json"
    settings.rdf_triplet = "nested/triplets"
    monkeypatch.chdir(tmp_path.parent)
    with TestClient(create_app(settings)) as client:
        response = client.post(ENDPOINT)
    target = tmp_path / "nested/triplets/triplets.json"
    assert response.status_code == 200 and target.is_file()
    assert response.json()["meta_data"][0]["triplet_file"] == str(target)


def test_rdf_configuration_default_dotenv_environment_and_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("RDF_TRIPLET", raising=False)
    assert AppSettings().rdf_triplet == "./rdf_triplet"
    (tmp_path / ".env").write_text("RDF_TRIPLET=./from_dotenv\n")
    assert AppSettings().rdf_triplet == "./from_dotenv"
    monkeypatch.setenv("RDF_TRIPLET", "./from_environment")
    assert AppSettings().rdf_triplet == "./from_environment"
    assert config.parse_args([]).rdf_triplet == "./from_environment"


def test_sdk_sends_configured_url_and_structured_output(settings):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_LLM_MODEL
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert json.loads(body["messages"][1]["content"]) == {"tables": TABLES}
        return httpx.Response(200, json={
            "id": "chatcmpl-triplets", "object": "chat.completion", "created": 0, "model": DEFAULT_LLM_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"triplets": [RELATION]}),
            }}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
        })

    app = create_app(settings)
    app.state.services.generate_triplet_service.client_factory = lambda settings: create_llm_client(
        settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)), max_retries=0,
    )
    with TestClient(app) as client:
        response = client.post(ENDPOINT)
    assert response.status_code == 200 and response.json()["data"] == expected_triplets()
    assert len(requests) == 1


def test_cancelled_inference_closes_client_and_preserves_file(settings, llm):
    store, previous = seed_store(settings)

    async def run():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(GenerateTripletService(settings).generate_triplet())
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert store.path.read_bytes() == previous and llm.closed_count == 1


def test_cancelled_publication_finishes_atomic_save(settings, llm, monkeypatch):
    started, release = Event(), Event()
    original_save = RdfTripletStore.save

    def delayed_save(store, triplets):
        started.set()
        assert release.wait(timeout=5)
        original_save(store, triplets)

    monkeypatch.setattr(RdfTripletStore, "save", delayed_save)

    async def run():
        task = asyncio.create_task(GenerateTripletService(settings).generate_triplet())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert json.loads((Path(settings.rdf_triplet) / "triplets.json").read_text()) == expected_triplets()


def test_logs_include_request_id_and_generation_stages(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "generate_triplet"]
    assert {r.stage for r in records} >= {
        "request", "schema_load", "llm_request", "triplet_validation", "triplet_save", "complete",
    }
    assert len({r.request_id for r in records}) == 1
    assert TABLES[0]["schema"] not in caplog.text
    assert TABLES[0]["description"] not in caplog.text


def test_openapi_registers_bodyless_endpoint_and_tuple_response(client):
    document = client.get("/openapi.json").json()
    operation = document["paths"][ENDPOINT]["post"]
    assert "requestBody" not in operation
    assert {"200", "500", "502"} <= set(operation["responses"])
    assert "GenerateTripletResponse" in document["components"]["schemas"]
