import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

import faiss
from fastapi.testclient import TestClient
import httpx
import pytest

from app.core import config
from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.services import embedding_based_schema_service as search_module
from app.services import indexing_schema_service as indexing_module
from app.services import open_ai_clients
from app.services.embedding_client import normalize_embedding_vectors
from app.services.schema_index_store import SchemaIndexStore
from main import create_app


INDEX = "/retrieve/indexing_schema_open_ai"
SEARCH = "/retrieve/embedding_based_schema_open_ai"
GENERATE = "/generate_sql/llm_based_generate_sql_open_ai"
FAKE_KEY = "test-openai-key-not-a-real-credential"
QUERY = "A 테이블 정보를 조회해줘"
SQL = "SELECT ID FROM A;"
HISTORY = [{"role": role, "content": content} for role, content in (
    ("user", "제외할 오래된 질문"), ("assistant", "최근 답변 하나"),
    ("user", "최근 질문 둘"), ("assistant", "최근 답변 셋"),
)]


def table(name):
    return {"table_name": name, "schema": f"CREATE TABLE {name} (ID NUMBER);", "description": f"{name} 테이블 정보"}


def vector(name="A", dimension=1536):
    values = [0.0] * dimension
    values[:2] = {"A": [1.0, 0.0], "B": [0.6, 0.8], "C": [-1.0, 0.0], "D": [0.8, 0.6]}[name]
    return values


def schema_data(names=("A",)):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


def sql_request(data=None):
    return {"query": QUERY, "history": HISTORY, "schema": data if data is not None else schema_data()}


def files(root):
    root = Path(root)
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def seed_legacy(settings):
    store = SchemaIndexStore(Path(settings.embedded_schema))
    index = faiss.IndexFlatIP(2)
    index.add(normalize_embedding_vectors([vector(name, 2) for name in ("B", "C", "A")], 3))
    with store.locked():
        store.save(index, [table(name) for name in ("B", "C", "A")], settings.embedding_model_name)


class FakeEmbeddings:
    def __init__(self, dimension=1536):
        self.dimension = dimension
        self.documents = []
        self.queries = []
        self.error = None
        self.query_vector = None

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        if self.error:
            raise self.error
        return [vector(text.splitlines()[0].removeprefix("table_name: "), self.dimension) for text in texts]

    def embed_query(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return self.query_vector if self.query_vector is not None else vector(dimension=self.dimension)


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.error = None
        self.closed = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def parse(self, **options):
        self.calls.append(options)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"sql": SQL},
        ))], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


@pytest.fixture
def settings(tmp_path):
    settings = AppSettings(
        dataset_path=str(tmp_path / "db_schema.json"),
        embedded_schema=str(tmp_path / "legacy"),
        embedded_schema_open_ai=str(tmp_path / "open_ai"),
        openai_api_key=FAKE_KEY,
        openai_embedding_model_name="text-embedding-3-small",
        openai_llm_model_name="gpt-6-luna",
    )
    Path(settings.dataset_path).write_text(json.dumps([table(name) for name in ("B", "C", "A")]), encoding="utf-8")
    return settings


@pytest.fixture
def providers(monkeypatch):
    embeddings, llm = FakeEmbeddings(), FakeLlm()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda *args, **kwargs: embeddings)
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda *args, **kwargs: llm)
    return embeddings, llm


def test_open_ai_pipeline_reuses_contracts_and_keeps_legacy_index_separate(settings, providers, monkeypatch):
    embeddings, llm = providers
    seed_legacy(settings)
    before = files(settings.embedded_schema)
    legacy = FakeEmbeddings(dimension=2)
    monkeypatch.setattr(indexing_module, "create_embedding_client", lambda *args, **kwargs: legacy)
    monkeypatch.setattr(search_module, "create_embedding_client", lambda *args, **kwargs: legacy)
    with TestClient(create_app(settings)) as client:
        assert client.post("/retrieve/indexing_schema").json()["data"]["added_count"] == 0
        first = client.post(INDEX)
        assert first.status_code == 200 and first.json()["data"]["added_count"] == 3
        assert client.post(INDEX).json()["data"]["skipped_count"] == 3
        result = client.post(SEARCH, json={"query": QUERY, "top_k": 2, "history": HISTORY})
        assert result.status_code == 200 and result.json()["data"] == schema_data(("A", "B"))
        assert client.post(SEARCH, json={"query": QUERY, "top_k": 100}).json()["data"] == schema_data(("A", "B", "C"))
        generated = client.post(GENERATE, json=sql_request(result.json()["data"]))
        assert generated.status_code == 200 and generated.json()["data"] == SQL
        assert generated.json()["meta_data"] == [{"model_name": "gpt-6-luna", "question_token": 100, "answer_token": 20}]
        legacy_result = client.post("/retrieve/embedding_based_schema", json={"query": QUERY, "top_k": 1})
        assert legacy_result.status_code == 200 and legacy_result.json()["data"] == schema_data()
    assert len(embeddings.documents) == 1 and legacy.documents == []
    assert embeddings.queries[0] == "assistant: 최근 답변 하나\n\nuser: 최근 질문 둘\n\nassistant: 최근 답변 셋\n\nuser: " + QUERY
    assert llm.calls[0]["messages"][1:-1] == HISTORY[-3:]
    assert json.loads(llm.calls[0]["messages"][-1]["content"])["schema"] == result.json()["data"]
    assert llm.calls[0]["model"] == "gpt-6-luna" and llm.calls[0]["reasoning_effort"] == "none"
    assert "temperature" not in llm.calls[0] and "extra_body" not in llm.calls[0]
    assert files(settings.embedded_schema) == before
    directory = Path(settings.embedded_schema_open_ai) / "db_schema"
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["embedding_model"] == "text-embedding-3-small" and metadata["dimension"] == 1536
    assert faiss.read_index(str(directory / "index.faiss")).ntotal == 3
    assert first.json()["meta_data"][0]["index_directory"] == str(directory)
    assert FAKE_KEY not in repr(settings)


def test_open_ai_index_only_embeds_additions(settings, providers):
    embeddings, _ = providers
    with TestClient(create_app(settings)) as client:
        assert client.post(INDEX).status_code == 200
        Path(settings.dataset_path).write_text(json.dumps([table(name) for name in ("B", "C", "A", "D")]))
        response = client.post(INDEX)
    assert response.status_code == 200
    assert response.json()["data"] == {"added_count": 1, "skipped_count": 3, "total_count": 4, "added_tables": ["D"]}
    assert embeddings.documents[-1] == ["table_name: D\ndescription: D 테이블 정보"]


@pytest.mark.parametrize("endpoint,body", [(INDEX, None), (SEARCH, {"query": QUERY, "top_k": 1}), (GENERATE, sql_request())])
def test_missing_open_ai_key_fails_before_storage_or_client_calls(settings, providers, endpoint, body):
    settings.openai_api_key = ""
    embeddings, llm = providers
    with TestClient(create_app(settings)) as client:
        assert client.get("/openapi.json").status_code == 200
        response = client.post(endpoint, json=body)
    assert response.status_code == 500 and "OPENAI_API_KEY" in response.json()["msg"]
    assert response.json()["data"] is None
    assert embeddings.documents == embeddings.queries == llm.calls == []
    assert not Path(settings.embedded_schema_open_ai).exists()


@pytest.mark.parametrize("endpoint,body", [(INDEX, None), (SEARCH, {"query": QUERY, "top_k": 1})])
def test_open_ai_storage_cannot_use_legacy_directory(settings, providers, endpoint, body):
    settings.embedded_schema_open_ai = settings.embedded_schema
    with TestClient(create_app(settings)) as client:
        response = client.post(endpoint, json=body)
    assert response.status_code == 500 and "must differ" in response.json()["msg"]
    assert not Path(settings.embedded_schema).exists()


@pytest.mark.parametrize("field,endpoint,body,env_name", [
    ("openai_embedding_model_name", INDEX, None, "OPENAI_EMBEDDING_MODEL_NAME"),
    ("openai_llm_model_name", GENERATE, sql_request(), "OPENAI_LLM_MODEL_NAME"),
    ("embedded_schema_open_ai", INDEX, None, "path"),
])
def test_invalid_open_ai_settings_are_local_configuration_errors(settings, providers, field, endpoint, body, env_name):
    setattr(settings, field, "")
    with TestClient(create_app(settings)) as client:
        response = client.post(endpoint, json=body)
    assert response.status_code == 500 and env_name in response.json()["msg"]


def test_open_ai_search_does_not_fall_back_to_existing_legacy_index(settings, providers):
    seed_legacy(settings)
    with TestClient(create_app(settings)) as client:
        response = client.post(SEARCH, json={"query": QUERY, "top_k": 1})
    assert response.status_code == 404
    assert "/retrieve/indexing_schema_open_ai" in response.json()["msg"]
    assert not Path(settings.embedded_schema_open_ai).exists()
    assert providers[0].queries == []


def test_open_ai_model_and_dimension_conflicts_preserve_stored_index(settings, providers):
    embeddings, _ = providers
    with TestClient(create_app(settings)) as client:
        assert client.post(INDEX).status_code == 200
        before = files(settings.embedded_schema_open_ai)
        embeddings.query_vector = [1.0, 0.0]
        assert client.post(SEARCH, json={"query": QUERY, "top_k": 1}).status_code == 409
    settings.openai_embedding_model_name = "text-embedding-3-large"
    with TestClient(create_app(settings)) as client:
        assert client.post(INDEX).status_code == 409
        assert client.post(SEARCH, json={"query": QUERY, "top_k": 1}).status_code == 409
    assert len(embeddings.documents) == 1
    assert files(settings.embedded_schema_open_ai) == before


def test_concurrent_open_ai_indexing_does_not_duplicate_tables(settings, providers, monkeypatch):
    barrier = threading.Barrier(2)
    original = SchemaIndexStore.locked

    @contextmanager
    def synchronized_lock(store, *, shared=False):
        barrier.wait(timeout=10)
        with original(store, shared=shared):
            yield

    monkeypatch.setattr(SchemaIndexStore, "locked", synchronized_lock)

    def request():
        with TestClient(create_app(settings)) as client:
            return client.post(INDEX)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["data"]["added_count"] for response in responses) == [0, 3]
    assert len(providers[0].documents) == 1


def test_provider_errors_hide_api_key_from_responses_and_tracebacks(settings, providers, caplog):
    embeddings, llm = providers
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        with TestClient(create_app(settings)) as client:
            assert client.post(INDEX).status_code == 200
            embeddings.error = RuntimeError(f"Rejected credential: {FAKE_KEY}")
            llm.error = RuntimeError(f"Rejected credential: {FAKE_KEY}")
            for endpoint, body in [(INDEX, None), (SEARCH, {"query": QUERY, "top_k": 1}), (GENERATE, sql_request())]:
                if endpoint == INDEX:
                    Path(settings.dataset_path).write_text(json.dumps([table("D")]))
                response = client.post(endpoint, json=body)
                assert response.status_code == 502
                assert FAKE_KEY not in response.text and "[REDACTED]" in response.json()["msg"]
        assert FAKE_KEY not in caplog.text
        console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        app_logger.removeHandler(caplog.handler)


def test_actual_open_ai_clients_use_official_url_and_configured_bearer_key(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-global-key")
    embedding_requests, llm_requests = [], []
    real_embedding_factory = open_ai_clients.create_open_ai_embedding_client
    real_llm_factory = open_ai_clients.create_open_ai_llm_client

    def embed_handler(request):
        embedding_requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/embeddings"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-3-small"
        values = [vector(text.splitlines()[0].removeprefix("table_name: ")) if text.startswith("table_name:")
                  else vector() for text in body["input"]]
        return httpx.Response(200, json={"object": "list", "model": "text-embedding-3-small", "data": [
            {"object": "embedding", "index": i, "embedding": value} for i, value in enumerate(values)
        ], "usage": {"prompt_tokens": 10, "total_tokens": 10}})

    def llm_handler(request):
        llm_requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none"
        assert "thinking" not in body and "temperature" not in body and "top_p" not in body
        assert body["response_format"]["type"] == "json_schema"
        assert body["messages"][1:-1] == HISTORY[-3:]
        assert "PostgreSQL" in body["messages"][0]["content"]
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"sql": SQL}), "refusal": None,
            }}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    async_http = httpx.AsyncClient(transport=httpx.MockTransport(llm_handler))
    try:
        with httpx.Client(transport=httpx.MockTransport(embed_handler)) as sync_http:
            def embedding_factory(profile, **options):
                options.update(http_client=sync_http, max_retries=0)
                return real_embedding_factory(profile, **options)

            monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", embedding_factory)
            monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda profile:
                                real_llm_factory(profile, http_client=async_http, max_retries=0))
            with TestClient(create_app(settings)) as client:
                assert client.post(INDEX).status_code == 200
                result = client.post(SEARCH, json={"query": QUERY, "top_k": 2, "history": HISTORY})
                assert result.status_code == 200 and result.json()["data"] == schema_data(("A", "B"))
                result = client.post(GENERATE, json=sql_request(result.json()["data"]))
                assert result.status_code == 200 and result.json()["data"] == SQL
        assert len(embedding_requests) == 2 and len(llm_requests) == 1
        assert async_http.is_closed
    finally:
        asyncio.run(async_http.aclose())


@pytest.mark.parametrize("status", [401, 429])
def test_actual_open_ai_authentication_and_quota_errors_are_safe_502(settings, monkeypatch, status):
    actual_factory = open_ai_clients.create_open_ai_embedding_client

    def handle(request):
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        return httpx.Response(status, json={"error": {"message": f"Invalid key or quota for {FAKE_KEY}",
                                                    "type": "invalid_request_error", "code": "test_error"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        def factory(profile, **options):
            options.update(http_client=transport, max_retries=0)
            return actual_factory(profile, **options)
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", factory)
        with TestClient(create_app(settings)) as client:
            response = client.post(INDEX)
    assert response.status_code == 502 and FAKE_KEY not in response.text
    assert not (Path(settings.embedded_schema_open_ai) / "db_schema").exists()


def test_open_ai_environment_loading_precedence_defaults_and_secret_repr(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=file-key\nOPENAI_EMBEDDING_MODEL_NAME=file-embedding\n"
                                   "OPENAI_LLM_MODEL_NAME=file-model\nEMBEDDED_SCHEMA_OPEN_AI=./custom-openai\n")
    with patch.dict(os.environ, {}, clear=True):
        settings = AppSettings()
        assert settings.openai_api_key == "file-key" and "file-key" not in repr(settings)
        assert settings.openai_embedding_model_name == "file-embedding"
        assert settings.openai_llm_model_name == "file-model"
        assert config.resolve_project_path(settings.embedded_schema_open_ai) == tmp_path / "custom-openai"
    with patch.dict(os.environ, {"OPENAI_API_KEY": "process-key", "OPENAI_LLM_MODEL_NAME": "process-model"}, clear=True):
        settings = AppSettings()
        assert settings.openai_api_key == "process-key" and settings.openai_llm_model_name == "process-model"
    (tmp_path / ".env").write_text("")
    with patch.dict(os.environ, {}, clear=True):
        settings = AppSettings()
        assert settings.openai_api_key == ""
        assert settings.openai_embedding_model_name == "text-embedding-3-small"
        assert settings.openai_llm_model_name == "gpt-6-luna"
        assert settings.embedded_schema_open_ai == "./embedded_schema_open_ai"


def test_open_ai_openapi_reuses_existing_request_and_response_contracts(settings, providers):
    with TestClient(create_app(settings)) as client:
        paths = client.get("/openapi.json").json()["paths"]
    for original, open_ai in [("/retrieve/indexing_schema", INDEX), ("/retrieve/embedding_based_schema", SEARCH),
                             ("/generate_sql/llm_based_generate_sql", GENERATE)]:
        old, new = paths[original]["post"], paths[open_ai]["post"]
        assert old.get("requestBody") == new.get("requestBody")
        assert old["responses"] == new["responses"]
