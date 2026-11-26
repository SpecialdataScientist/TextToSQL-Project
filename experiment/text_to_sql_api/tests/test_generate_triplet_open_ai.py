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
from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.schemas.llm_output import GeneratedTripletsOutput
from app.schemas.triplet import RDF_TRIPLETS
from app.services import generate_triplet_service as service_module
from app.services import open_ai_clients, rdf_triplet_store as store_module
from app.services.rdf_triplet_store import RdfTripletStore
from main import create_app


ENDPOINT = "/retrieve/generate_triplet_open_ai"
FAKE_KEY = "test-openai-triplet-key"
TABLES = [
    {"table_name": "ROOM", "schema": "CREATE TABLE ROOM (BUILDING_ID INTEGER);",
     "description": "  건물에 소속된 공간 정보  "},
    {"table_name": "BUILDING", "schema": "CREATE TABLE BUILDING (ID INTEGER);",
     "description": "건물 정보"},
]
RELATION = {"subject_table_name": "ROOM", "predicate": "is located in", "object_table_name": "BUILDING"}


def triplets(predicate="is located in"):
    return [[{key: TABLES[0][key] for key in ("table_name", "description")}, predicate,
             {key: TABLES[1][key] for key in ("table_name", "description")}]]


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.closed = 0
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
        self.closed += 1

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
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
        dataset_path=str(source), rdf_triplet=str(tmp_path / "legacy"),
        rdf_triplet_open_ai=str(tmp_path / "open-ai"),
        embedded_schema=str(tmp_path / "embedded-legacy"),
        embedded_schema_open_ai=str(tmp_path / "embedded-open-ai"),
        openai_api_key=FAKE_KEY, openai_llm_model_name="gpt-6-luna",
        llm_api_base="invalid-legacy-url", model_name="unused-legacy-model",
    )


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda settings: fake)
    return fake


def invoke(settings):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT)


def seed(directory, predicate="belongs to"):
    store = RdfTripletStore(Path(directory))
    store.save(RDF_TRIPLETS.validate_python(triplets(predicate)))
    return store, store.path.read_bytes()


def test_generates_and_loads_separate_snapshot_with_original_descriptions(settings, llm):
    legacy, previous = seed(settings.rdf_triplet)
    response = invoke(settings)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == triplets()
    store = RdfTripletStore(Path(settings.rdf_triplet_open_ai))
    assert json.loads(store.path.read_text()) == body["data"]
    assert RDF_TRIPLETS.dump_python(store.load(), mode="json") == body["data"]
    assert legacy.path.read_bytes() == previous
    assert body["meta_data"] == [{
        "model_name": "gpt-6-luna", "question_token": 150, "answer_token": 30,
        "table_count": 2, "triplet_count": 1, "triplet_file": str(store.path),
    }]
    call, = llm.calls
    assert call["response_format"] is GeneratedTripletsOutput
    assert call["model"] == "gpt-6-luna" and call["reasoning_effort"] == "none"
    assert "temperature" not in call and "extra_body" not in call
    assert json.loads(call["messages"][1]["content"]) == {"tables": TABLES}
    assert llm.closed == 1
    assert not Path(settings.embedded_schema).exists()
    assert not Path(settings.embedded_schema_open_ai).exists()


def test_deduplicates_relations_and_replaces_previous_open_ai_snapshot(settings, llm):
    store, _ = seed(settings.rdf_triplet_open_ai)
    duplicate = dict(RELATION, predicate="  is located in  ")
    llm.result.choices[0].message.parsed = {"triplets": [RELATION, duplicate]}
    assert invoke(settings).json()["data"] == triplets()
    assert json.loads(store.path.read_text()) == triplets()


@pytest.mark.parametrize("empty_dataset", [False, True])
def test_empty_results_are_saved_without_touching_legacy(settings, llm, empty_dataset):
    store, _ = seed(settings.rdf_triplet_open_ai)
    legacy, previous = seed(settings.rdf_triplet)
    llm.result.choices[0].message.parsed = {"triplets": []}
    if empty_dataset:
        Path(settings.dataset_path).write_text("[]")
    response = invoke(settings)
    assert response.status_code == 200 and response.json()["data"] == []
    assert json.loads(store.path.read_text()) == []
    assert legacy.path.read_bytes() == previous
    assert len(llm.calls) == (0 if empty_dataset else 1)


@pytest.mark.parametrize("field,value,message", [
    ("openai_api_key", " ", "OPENAI_API_KEY"),
    ("openai_llm_model_name", " ", "OPENAI_LLM_MODEL_NAME"),
    ("rdf_triplet_open_ai", " ", "path"),
    ("rdf_triplet_open_ai", "same", "RDF_TRIPLET_OPEN_AI"),
    ("rdf_triplet_open_ai", "alias", "RDF_TRIPLET_OPEN_AI"),
])
def test_invalid_configuration_is_500_before_generation_or_storage(settings, llm, field, value, message):
    legacy, previous = seed(settings.rdf_triplet)
    if value == "same":
        value = settings.rdf_triplet
    elif value == "alias":
        value = settings.rdf_triplet + "/../legacy"
    setattr(settings, field, value)
    response = invoke(settings)
    assert response.status_code == 500 and message in response.json()["msg"]
    assert legacy.path.read_bytes() == previous and llm.calls == []


def test_missing_key_is_checked_even_for_empty_dataset(settings, llm):
    settings.openai_api_key = ""
    Path(settings.dataset_path).write_text("[]")
    assert invoke(settings).status_code == 500
    assert not Path(settings.rdf_triplet_open_ai).exists() and llm.calls == []


@pytest.mark.parametrize("failure", ["unknown_table", "blank_relation", "refusal", "length", "timeout"])
def test_failed_generation_preserves_both_snapshots(settings, llm, failure):
    store, previous = seed(settings.rdf_triplet_open_ai)
    legacy, legacy_before = seed(settings.rdf_triplet)
    if failure == "unknown_table":
        llm.result.choices[0].message.parsed = {"triplets": [dict(RELATION, object_table_name="UNKNOWN")]}
    elif failure == "blank_relation":
        llm.result.choices[0].message.parsed = {"triplets": [dict(RELATION, predicate=" ")]}
    elif failure == "refusal":
        llm.result.choices[0].message.refusal = "Cannot answer"
    elif failure == "length":
        llm.result.choices[0].finish_reason = "length"
    else:
        llm.error = TimeoutError("upstream timed out")
    response = invoke(settings)
    assert response.status_code == 502 and response.json()["data"] is None
    assert store.path.read_bytes() == previous and legacy.path.read_bytes() == legacy_before
    assert not list(store.root.glob(".triplets-*.tmp")) and llm.closed == 1


def test_invalid_dataset_and_failed_save_preserve_existing_file(settings, llm, monkeypatch):
    store, previous = seed(settings.rdf_triplet_open_ai)
    Path(settings.dataset_path).write_text("{}")
    assert invoke(settings).status_code == 500 and llm.calls == []
    Path(settings.dataset_path).write_text(json.dumps(TABLES))

    def fail_replace(*args):
        raise OSError("cannot replace snapshot")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    assert invoke(settings).status_code == 500
    assert store.path.read_bytes() == previous and not list(store.root.glob(".triplets-*.tmp"))


def test_errors_mask_api_key_in_response_and_log_traceback(settings, llm, caplog):
    llm.error = RuntimeError(f"Rejected credential: {FAKE_KEY}")
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = invoke(settings)
        assert response.status_code == 502
        assert FAKE_KEY not in response.text and "[REDACTED]" in response.json()["msg"]
        assert FAKE_KEY not in caplog.text
        console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
        records = [r for r in caplog.records if getattr(r, "operation", None) == "generate_triplet_open_ai"]
        assert records and len({r.request_id for r in records}) == 1
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("status", [200, 401, 429])
def test_real_sdk_uses_official_url_key_model_and_structured_output(settings, monkeypatch, status):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-global-key")
    factory = open_ai_clients.create_open_ai_llm_client
    requests = []
    store, previous = seed(settings.rdf_triplet_open_ai)

    def handle(request):
        requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none"
        assert "temperature" not in body and "thinking" not in body
        assert body["response_format"]["json_schema"]["strict"] is True
        assert json.loads(body["messages"][1]["content"]) == {"tables": TABLES}
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"Rejected: {FAKE_KEY}",
                                                        "type": "invalid_request_error", "code": "test"}})
        return httpx.Response(200, json={
            "id": "chatcmpl-triplet", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"triplets": [RELATION]}), "refusal": None,
            }}], "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
        })

    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile: factory(
        profile, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)), max_retries=0,
    ))
    response = invoke(settings)
    assert len(requests) == 1 and FAKE_KEY not in response.text
    if status == 200:
        assert response.status_code == 200 and response.json()["data"] == triplets()
    else:
        assert response.status_code == 502 and store.path.read_bytes() == previous


def test_cancelled_open_ai_inference_preserves_snapshot_and_closes_client(settings, llm):
    store, previous = seed(settings.rdf_triplet_open_ai)
    service = create_app(settings).state.services.generate_triplet_open_ai_service

    async def run():
        llm.started, llm.gate = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(service.generate_triplet())
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert store.path.read_bytes() == previous and llm.closed == 1


def test_dotenv_storage_configuration_defaults_precedence_and_project_relative_path(monkeypatch, tmp_path, llm):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        assert AppSettings().rdf_triplet_open_ai == "./rdf_triplet_open_ai"
        (tmp_path / ".env").write_text(
            f"OPENAI_API_KEY={FAKE_KEY}\nOPENAI_LLM_MODEL_NAME=gpt-6-luna\n"
            "RDF_TRIPLET_OPEN_AI=./custom-open-ai\nRDF_TRIPLET=./legacy\n",
        )
        settings = config.parse_args([])
        settings.dataset_path = "db_schema.json"
        assert settings.openai_api_key == FAKE_KEY and FAKE_KEY not in repr(settings)
        assert settings.rdf_triplet_open_ai == "./custom-open-ai"
        (tmp_path / "db_schema.json").write_text(json.dumps(TABLES))
        monkeypatch.chdir(tmp_path.parent)
        assert invoke(settings).status_code == 200
        store = RdfTripletStore(tmp_path / "custom-open-ai")
        assert RDF_TRIPLETS.dump_python(store.load(), mode="json") == triplets()
        monkeypatch.setenv("RDF_TRIPLET_OPEN_AI", "./override")
        assert config.parse_args([]).rdf_triplet_open_ai == "./override"


def test_legacy_generation_and_openapi_are_compatible_without_open_ai_key(settings, monkeypatch):
    settings.openai_api_key = ""
    settings.llm_api_base = "https://legacy.test/v1"
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: FakeLlm())
    with TestClient(create_app(settings)) as client:
        assert client.post("/retrieve/generate_triplet").status_code == 200
        document = client.get("/openapi.json").json()
    old = document["paths"]["/retrieve/generate_triplet"]["post"]
    new = document["paths"][ENDPOINT]["post"]
    assert "requestBody" not in new and old["responses"] == new["responses"]
    assert len(document["paths"]) == 25
    assert not Path(settings.rdf_triplet_open_ai).exists()
