import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import shutil
import threading

import faiss
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.schemas.request import EmbeddedTripletBasedSchemaOpenAiRequest, LlmBasedGenerateSqlRequest
from app.schemas.response import EmbeddedTripletBasedSchemaOpenAiData, EmbeddedTripletBasedSchemaOpenAiResponse
from app.schemas.triplet import RDF_TRIPLETS
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import embedded_triplet_based_schema_service as service_module, open_ai_clients
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/embedded_triplet_based_schema_open_ai"
FAKE_KEY = "test-openai-triplet-search-key"
MODEL = "text-embedding-3-small"
QUERY = "최근 조건에 맞는 공간 정보를 조회해줘"
HISTORY = [{"role": role, "content": content} for role, content in (
    ("user", "제외할 오래된 질문"), ("assistant", "  오래된 답변  "),
    ("user", "최근 대상"), ("assistant", "최근 조회 안내"),
    ("user", "최근 필터 조건"), ("assistant", "최근 집계 조건"),
)]
# Summing scores puts B before A; the best matching triplet puts A before B.
# The top four triplets cover only C, A and B, so top_k=4 must search deeper.
ENTRIES = [
    ("B", "r0", "A", 0.8), ("C", "r1", "C", 0.95), ("B", "r2", "D", 0.7),
    ("C", "r3", "A", 0.9), ("C", "r4", "B", 0.85), ("C", "r5", "C", 0.92),
    ("D", "r6", "E", -0.4),
]


def table(name):
    return {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명\n"}


def expected_data(names):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


def save_snapshot(settings, entries=ENTRIES, *, local=False):
    records, vectors = [], []
    for subject, predicate, target, score in entries:
        triplet = [{"table_name": subject, "description": "과거 설명"}, predicate,
                   {"table_name": target, "description": "과거 설명"}]
        triplet = RDF_TRIPLETS.validate_python([triplet])[0]
        records.append(IndexedTriplet(triplet_id=make_triplet_id(triplet), triplet=triplet).model_dump(mode="json"))
        vectors.append([score, math.sqrt(max(0, 1 - score ** 2)), 0.0])
    index = faiss.IndexFlatIP(3)
    if vectors:
        index.add(np.asarray(vectors, dtype=np.float32))
    root = settings.embedded_triplet if local else settings.embedded_triplet_open_ai
    model = settings.embedding_model_name if local else settings.openai_embedding_model_name
    store = TripletIndexStore(Path(root))
    with store.locked():
        store.save(index, records, model)


def files(directory):
    root = Path(directory)
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class FakeEmbeddings:
    def __init__(self):
        self.queries, self.documents = [], []
        self.vector, self.error, self.hook = [9.0, 0.0, 0.0], None, None

    def embed_query(self, text):
        self.queries.append(text)
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        return self.vector

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps([table(name) for name in ("E", "UNINDEXED", "B", "D", "A", "C")]))
    return AppSettings(
        dataset_path=str(source), embedded_triplet=str(tmp_path / "local triplets"),
        embedded_triplet_open_ai=str(tmp_path / "open ai triplets"),
        rdf_triplet=str(tmp_path / "missing-rdf"), rdf_triplet_open_ai=str(tmp_path / "missing-open-ai-rdf"),
        embedded_schema=str(tmp_path / "missing-schema-index"),
        embedded_schema_open_ai=str(tmp_path / "missing-open-ai-schema-index"),
        embedding_api_base="invalid-unused-local-url", llm_api_base="", model_name="",
        openai_api_key=FAKE_KEY, openai_embedding_model_name=MODEL, openai_llm_model_name="",
    )


@pytest.fixture
def indexed_settings(settings):
    save_snapshot(settings)
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda *args, **kwargs: fake)

    def reject_local_client(*args, **kwargs):
        raise AssertionError("OpenAI search must use the OpenAI embedding client")

    monkeypatch.setattr(service_module, "create_embedding_client", reject_local_client)
    return fake


def invoke(settings, payload=None):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT, json=payload if payload is not None else {"query": QUERY, "top_k": 3})


@pytest.mark.parametrize("top_k,names", [
    (1, ["C"]), (3, ["C", "A", "B"]), (4, ["C", "A", "B", "D"]),
    (5, ["C", "A", "B", "D", "E"]), (10 ** 100, ["C", "A", "B", "D", "E"]),
])
def test_ranking_unique_table_count_original_schema_and_store_isolation(indexed_settings, embeddings, top_k, names):
    save_snapshot(indexed_settings, [("E", "local-only", "E", 1.0)], local=True)
    before = (files(indexed_settings.embedded_triplet), files(indexed_settings.embedded_triplet_open_ai))
    response = invoke(indexed_settings, {"query": QUERY, "top_k": top_k})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(names)
    assert body["meta_data"] == [{
        "embedding_model": MODEL, "top_k": top_k, "retrieved_count": len(names), "unmapped_table_count": 0,
    }]
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.queries == [QUERY] and embeddings.documents == []
    assert (files(indexed_settings.embedded_triplet), files(indexed_settings.embedded_triplet_open_ai)) == before
    assert not Path(indexed_settings.rdf_triplet).exists() and not Path(indexed_settings.embedded_schema_open_ai).exists()
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize("entries,names,unmapped", [
    ([("B", "tie", "A", 1.0)], ["A", "B"], 0),
    ([("C", "self", "C", 1.0)], ["C"], 0),
    ([("A", "n0", "B", -0.8), ("D", "n1", "C", -0.1)], ["C", "D", "A", "B"], 0),
    ([("MISSING", "u0", "A", 0.99), ("MISSING", "u1", "UNKNOWN", 0.9),
      ("D", "u2", "C", 0.5)], ["A", "C", "D"], 2),
])
def test_ties_negative_scores_and_unmapped_tables(settings, embeddings, entries, names, unmapped):
    save_snapshot(settings, entries)
    response = invoke(settings, {"query": QUERY, "top_k": 10})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    assert response.json()["meta_data"][0]["unmapped_table_count"] == unmapped


@pytest.mark.parametrize("empty_index", [False, True])
def test_empty_results_skip_api_but_require_valid_open_ai_configuration(settings, embeddings, empty_index):
    save_snapshot(settings, [] if empty_index else ENTRIES)
    source = Path(settings.dataset_path)
    if empty_index:
        source.unlink()
    else:
        source.write_text("[]")
    response = invoke(settings)
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["unmapped_table_count"] == (0 if empty_index else 5)
    settings.openai_api_key = ""
    assert invoke(settings).status_code == 500
    assert embeddings.queries == []


def test_catalog_updates_are_reflected_without_reindexing(indexed_settings, embeddings):
    assert invoke(indexed_settings).status_code == 200
    before = files(indexed_settings.embedded_triplet_open_ai)
    updated = {"table_name": "C", "schema": "CREATE TABLE C (changed INTEGER);", "description": "갱신된 설명"}
    Path(indexed_settings.dataset_path).write_text(json.dumps([updated]))
    response = invoke(indexed_settings)
    assert response.json()["data"] == {key: [value] for key, value in updated.items()}
    assert response.json()["meta_data"][0]["unmapped_table_count"] == 4
    assert files(indexed_settings.embedded_triplet_open_ai) == before


@pytest.mark.parametrize("count", [0, 1, 3, 4, 6])
def test_query_uses_only_recent_three_messages_and_preserves_text(indexed_settings, embeddings, count):
    query, history = f"  {QUERY}  ", HISTORY[:count]
    response = invoke(indexed_settings, {"query": query, "history": history, "top_k": 2})
    expected = "\n\n".join([*(f"{m['role']}: {m['content']}" for m in history[-3:]), f"user: {query}"]) if history else query
    assert response.status_code == 200 and embeddings.queries == [expected]
    assert invoke(indexed_settings).status_code == 200 and embeddings.queries[-1] == QUERY


@pytest.mark.parametrize("payload", [
    {}, {"query": QUERY}, {"top_k": 1}, {"query": " \n", "top_k": 1}, {"query": 1, "top_k": 1},
    {"query": QUERY, "top_k": 0}, {"query": QUERY, "top_k": -1}, {"query": QUERY, "top_k": "2"},
    {"query": QUERY, "top_k": 2.0}, {"query": QUERY, "top_k": True},
    {"query": QUERY, "top_k": 1, "schema": {}}, {"query": QUERY, "top_k": 1, "history": None},
    {"query": QUERY, "top_k": 1, "history": [{"role": "user", "content": " "}]},
    {"query": QUERY, "top_k": 1, "history": [{"role": "system", "content": "invalid old turn"}, *HISTORY[-3:]]},
])
def test_invalid_requests_return_422_without_embedding(settings, embeddings, payload):
    response = invoke(settings, payload)
    assert response.status_code == 422 and "detail" in response.json() and embeddings.queries == []


@pytest.mark.parametrize("field,value,expected", [
    ("openai_api_key", " ", "OPENAI_API_KEY"),
    ("openai_embedding_model_name", " ", "OPENAI_EMBEDDING_MODEL_NAME"),
    ("openai_embedding_model_name", "openai/", "OPENAI_EMBEDDING_MODEL_NAME"),
    ("embedded_triplet_open_ai", " ", "path"),
    ("embedded_triplet_open_ai", "same", "EMBEDDED_TRIPLET_OPEN_AI"),
    ("embedded_triplet_open_ai", "alias", "EMBEDDED_TRIPLET_OPEN_AI"),
    ("embedded_triplet_open_ai", "symlink", "EMBEDDED_TRIPLET_OPEN_AI"),
])
def test_settings_are_validated_before_search_worker(settings, embeddings, monkeypatch, field, value, expected):
    if value == "same":
        value = settings.embedded_triplet
    elif value == "alias":
        value = settings.embedded_triplet + "/../local triplets"
    elif value == "symlink":
        Path(settings.embedded_triplet).mkdir()
        alias = Path(settings.embedded_triplet).with_name("alias")
        alias.symlink_to(settings.embedded_triplet, target_is_directory=True)
        value = str(alias)
    setattr(settings, field, value)

    def reject_search(*args):
        pytest.fail("Invalid configuration must fail before starting the search worker")

    monkeypatch.setattr(service_module.EmbeddedTripletBasedSchemaService, "_search_schema", reject_search)
    response = invoke(settings)
    assert response.status_code == 500 and expected in response.json()["msg"]
    assert embeddings.queries == []


@pytest.mark.parametrize("missing", ["root", "snapshot"])
def test_missing_index_has_open_ai_guidance_and_never_falls_back(indexed_settings, embeddings, missing):
    save_snapshot(indexed_settings, local=True)
    root = Path(indexed_settings.embedded_triplet_open_ai)
    shutil.rmtree(root if missing == "root" else root / "triplets")
    before = files(indexed_settings.embedded_triplet)
    response = invoke(indexed_settings)
    assert response.status_code == 404 and response.json()["data"] is None
    assert response.json()["msg"] == "Triplet index was not found. Run /retrieve/indexing_triplet_open_ai first."
    assert embeddings.queries == [] and files(indexed_settings.embedded_triplet) == before
    assert not root.exists() if missing == "root" else list(root.iterdir()) == []


@pytest.mark.parametrize("damage", ["model", "metadata", "raw_data", "missing_file"])
def test_incompatible_index_returns_409_before_embedding(indexed_settings, embeddings, damage):
    directory = Path(indexed_settings.embedded_triplet_open_ai) / "triplets"
    if damage == "model":
        indexed_settings.openai_embedding_model_name = "different-model"
    elif damage == "missing_file":
        (directory / "raw_data.json").unlink()
    else:
        (directory / f"{damage}.json").write_text("invalid JSON")
    before = files(directory)
    response = invoke(indexed_settings)
    assert response.status_code == 409 and response.json()["status"] == "error"
    assert embeddings.queries == [] and files(directory) == before


@pytest.mark.parametrize("vector,status", [
    ([1.0, 2.0], 409), ([], 502), ([0.0, 0.0, 0.0], 502),
    ([float("nan"), 1.0, 0.0], 502), ([float("inf"), 1.0, 0.0], 502), (None, 502),
])
def test_bad_query_vectors_preserve_index(indexed_settings, embeddings, vector, status):
    before = files(indexed_settings.embedded_triplet_open_ai)
    embeddings.vector = vector
    response = invoke(indexed_settings)
    assert response.status_code == status and response.json()["data"] is None
    assert files(indexed_settings.embedded_triplet_open_ai) == before


@pytest.mark.parametrize("damage", ["missing", "json", "duplicate"])
def test_invalid_original_catalog_is_500_without_embedding(indexed_settings, embeddings, damage):
    source = Path(indexed_settings.dataset_path)
    if damage == "missing":
        source.unlink()
    else:
        source.write_text("invalid JSON" if damage == "json" else json.dumps([table("A"), table("A")]))
    assert invoke(indexed_settings).status_code == 500 and embeddings.queries == []


@pytest.mark.parametrize("phase", ["embedding", "factory", "catalog"])
def test_errors_redact_key_in_response_logs_and_exception_chains(indexed_settings, embeddings, monkeypatch, caplog, phase):
    def fail(*args, **kwargs):
        raise RuntimeError(f"OpenAI failure containing key {FAKE_KEY}")

    if phase == "embedding":
        embeddings.error = RuntimeError(f"OpenAI rejected key {FAKE_KEY}")
    elif phase == "factory":
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", fail)
    else:
        monkeypatch.setattr(service_module, "validate_unique_tables", fail)
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = invoke(indexed_settings)
        assert response.status_code == (502 if phase == "embedding" else 500)
        assert FAKE_KEY not in response.text and "[REDACTED]" in response.json()["msg"]
        assert response.json()["meta_data"] == [] and response.json()["status"] == "error"
        assert FAKE_KEY not in caplog.text
        console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(r) for r in caplog.records)
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("status", [200, 401, 429])
@pytest.mark.parametrize("include_history", [False, True])
def test_real_sdk_uses_official_url_bearer_model_and_recent_context(indexed_settings, monkeypatch, status, include_history):
    factory = open_ai_clients.create_open_ai_embedding_client
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-global-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    query, requests = f"  {QUERY}  ", []
    payload = {"query": query, "top_k": 2}
    expected = query
    if include_history:
        payload["history"] = HISTORY
        expected = "\n\n".join([*(f"{m['role']}: {m['content']}" for m in HISTORY[-3:]), f"user: {query}"])

    def handle(request):
        requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/embeddings"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["input"] == [expected]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"Rejected key {FAKE_KEY}", "type": "invalid_request_error"}})
        return httpx.Response(200, json={
            "object": "list", "model": MODEL,
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda profile, **options:
                            factory(profile, http_client=http_client, max_retries=0))
        response = invoke(indexed_settings, payload)
    assert len(requests) == 1 and FAKE_KEY not in response.text
    assert response.status_code == (200 if status == 200 else 502)
    if status == 200:
        assert response.json()["data"] == expected_data(["C", "A"])


def test_open_ai_indexer_produces_a_searchable_snapshot(settings, embeddings):
    source = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    source.parent.mkdir()
    source.write_text(json.dumps([
        [{"table_name": "A", "description": "A"}, "references", {"table_name": "B", "description": "B"}],
        [{"table_name": "B", "description": "B"}, "references", {"table_name": "C", "description": "C"}],
    ]))
    with TestClient(create_app(settings)) as client:
        assert client.post("/retrieve/indexing_triplet_open_ai").status_code == 200
        response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["A", "B"])
    assert len(embeddings.documents) == 1 and embeddings.queries == [QUERY]
    assert not Path(settings.embedded_triplet).exists()
    assert not Path(settings.rdf_triplet).exists()


def test_concurrent_searches_share_the_read_lock(indexed_settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = TripletIndexStore.load

    def synchronized_load(store, model):
        barrier.wait(timeout=10)
        return original(store, model)

    monkeypatch.setattr(TripletIndexStore, "load", synchronized_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke, indexed_settings) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(r.status_code == 200 and r.json()["data"] == expected_data(["C", "A", "B"]) for r in responses)


def test_search_waits_for_writer_to_publish_snapshot(indexed_settings, embeddings, monkeypatch):
    attempted = threading.Event()
    original = TripletIndexStore.locked

    @contextmanager
    def observed_lock(store, *, shared=False):
        if shared:
            attempted.set()
        with original(store, shared=shared):
            yield

    monkeypatch.setattr(TripletIndexStore, "locked", observed_lock)
    store = TripletIndexStore(Path(indexed_settings.embedded_triplet_open_ai))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.locked():
            future = executor.submit(invoke, indexed_settings)
            assert attempted.wait(timeout=10)
            # Save directly under the held writer lock; do not acquire a second lock.
            triplet = [{"table_name": "E", "description": "old"}, "new", {"table_name": "E", "description": "old"}]
            triplet = RDF_TRIPLETS.validate_python([triplet])[0]
            index = faiss.IndexFlatIP(3)
            index.add(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32))
            record = IndexedTriplet(triplet_id=make_triplet_id(triplet), triplet=triplet).model_dump(mode="json")
            store.save(index, [record], MODEL)
        response = future.result(timeout=20)
    assert response.status_code == 200 and response.json()["data"] == expected_data(["E"])


def test_network_request_releases_read_lock_but_keeps_snapshot(indexed_settings, embeddings):
    def publish_after_lock_probe():
        descriptor = os.open(indexed_settings.embedded_triplet_open_ai, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        save_snapshot(indexed_settings, [("E", "new", "E", 1.0)])

    embeddings.hook = publish_after_lock_probe
    response = invoke(indexed_settings)
    assert response.status_code == 200 and response.json()["data"] == expected_data(["C", "A", "B"])
    embeddings.hook = None
    assert invoke(indexed_settings).json()["data"] == expected_data(["E"])


def test_cancellation_leaves_no_read_lock_or_file_changes(indexed_settings, embeddings):
    entered, release = threading.Event(), threading.Event()
    before = files(indexed_settings.embedded_triplet_open_ai)

    def hold_embedding():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("embedding was not released")

    embeddings.hook = hold_embedding
    service = create_app(indexed_settings).state.services.embedded_triplet_based_schema_open_ai_service

    async def run():
        task = asyncio.create_task(service.embedded_triplet_based_schema(
            EmbeddedTripletBasedSchemaOpenAiRequest(query=QUERY, top_k=2)))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            descriptor = os.open(indexed_settings.embedded_triplet_open_ai, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert files(indexed_settings.embedded_triplet_open_ai) == before


def test_local_endpoint_works_without_open_ai_settings(settings, monkeypatch):
    save_snapshot(settings, local=True)
    settings.openai_api_key, settings.openai_embedding_model_name = "", ""
    settings.embedded_triplet_open_ai = settings.embedded_triplet
    settings.embedding_api_base = "https://local.test/v1"
    fake = FakeEmbeddings()
    monkeypatch.setattr(service_module, "create_embedding_client", lambda *args, **kwargs: fake)
    with TestClient(create_app(settings)) as client:
        response = client.post("/retrieve/embedded_triplet_based_schema", json={"query": QUERY, "top_k": 2})
        assert response.status_code == 200 and response.json()["data"] == expected_data(["C", "A"])
        assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 500


def test_openapi_exposes_dedicated_models_and_logs_identify_endpoint(indexed_settings, embeddings, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        app = create_app(indexed_settings)
        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2, "history": HISTORY})
        assert response.status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddedTripletBasedSchemaOpenAiRequest")
    for status in ("200", "404", "409", "500", "502"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddedTripletBasedSchemaOpenAiResponse")
    models = spec["components"]["schemas"]
    request = models["EmbeddedTripletBasedSchemaOpenAiRequest"]
    assert set(request["required"]) == {"query", "top_k"}
    assert set(request["properties"]) == {"query", "top_k", "history"} and request["additionalProperties"] is False
    assert set(models["EmbeddedTripletBasedSchemaOpenAiData"]["properties"]) == {"table_name", "schema", "description"}
    assert len(spec["paths"]) == 25
    records = [r for r in caplog.records if getattr(r, "operation", None) == "embedded_triplet_based_schema_open_ai"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert ENDPOINT in records[0].getMessage() and "history_count=6 used_history_count=3" in records[0].getMessage()
    assert {"index_load", "catalog_load", "embedding_setup", "index_search"} <= {r.stage for r in records}
    service_response = asyncio.run(app.state.services.embedded_triplet_based_schema_open_ai_service.embedded_triplet_based_schema(
        EmbeddedTripletBasedSchemaOpenAiRequest(query=QUERY, top_k=1)))
    assert isinstance(service_response, EmbeddedTripletBasedSchemaOpenAiResponse)
    assert isinstance(service_response.data, EmbeddedTripletBasedSchemaOpenAiData)
