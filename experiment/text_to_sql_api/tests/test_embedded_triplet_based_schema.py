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
from types import SimpleNamespace

import faiss
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from app.core.config import AppSettings
from app.core.logging import LOGGER_NAME
from app.schemas.request import EmbeddedTripletBasedSchemaRequest, LlmBasedGenerateSqlRequest
from app.schemas.triplet import TripletEntity
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import embedded_triplet_based_schema_service as service_module
from app.services import indexing_triplet_service as indexing_module
from app.services.embedded_triplet_based_schema_service import EmbeddedTripletBasedSchemaService
from app.services.embedding_client import create_embedding_client, normalize_embedding_vectors
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/embedded_triplet_based_schema"
QUERY = "최근 조건에 맞는 공간과 건물 정보를 조회해줘"
HISTORY = [
    {"role": "user", "content": "제외할 오래된 질문"},
    {"role": "assistant", "content": "  제외할 오래된 답변  "},
    {"role": "user", "content": "이전 대상"},
    {"role": "assistant", "content": "최근 공간 조회 안내"},
    {"role": "user", "content": "최근 필터 조건"},
    {"role": "assistant", "content": "최근 집계 조건"},
]
# The top four triplets only cover C, A and B. D must be found deeper in the index.
ENTRIES = [
    ("B", "r0", "A", 0.8), ("C", "r1", "C", 0.95), ("B", "r2", "D", 0.7),
    ("C", "r3", "A", 0.9), ("C", "r4", "B", 0.85), ("C", "r5", "C", 0.92),
    ("D", "r6", "E", -0.4),
]


def table(name):
    return {"table_name": name, "schema": f"  CREATE TABLE {name} (id INTEGER);\n", "description": f" {name} 원본 설명\n"}


def make_snapshot(entries=ENTRIES):
    records, values = [], []
    for subject, predicate, target, score in entries:
        triplet = (
            TripletEntity(table_name=subject, description=f"이전 {subject} 설명"), predicate,
            TripletEntity(table_name=target, description=f"이전 {target} 설명"),
        )
        records.append(IndexedTriplet(triplet_id=make_triplet_id(triplet), triplet=triplet).model_dump(mode="json"))
        values.append([score, math.sqrt(max(0, 1 - score ** 2)), 0.0])
    index = faiss.IndexFlatIP(3)
    if values:
        index.add(normalize_embedding_vectors(values, len(values)))
    return index, records


def save_snapshot(settings, entries=ENTRIES):
    store = TripletIndexStore(Path(settings.embedded_triplet))
    index, records = make_snapshot(entries)
    with store.locked():
        store.save(index, records, settings.embedding_model_name)


def snapshot_files(settings):
    root = Path(settings.embedded_triplet)
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def expected_data(names):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


class FakeEmbeddings:
    def __init__(self):
        self.queries = []
        self.documents = []
        self.vector = [9.0, 0.0, 0.0]
        self.error = None
        self.hook = None

    def embed_query(self, query):
        self.queries.append(query)
        if self.hook is not None:
            self.hook()
        if self.error is not None:
            raise self.error
        return self.vector

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps([table(name) for name in ("E", "UNINDEXED", "B", "D", "A", "C")]), encoding="utf-8")
    return AppSettings(
        dataset_path=str(source), embedded_triplet=str(tmp_path / "embedded triplets"),
        embedded_triplet_open_ai=str(tmp_path / "unused-open-ai-index"),
        rdf_triplet=str(tmp_path / "missing-rdf"), embedded_schema=str(tmp_path / "missing-schema-index"),
        embedding_api_base="https://embedding.test/v1", openai_api_key="", llm_api_base="",
    )


@pytest.fixture
def indexed_settings(settings):
    save_snapshot(settings)
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(service_module, "create_embedding_client", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def client(indexed_settings, embeddings):
    with TestClient(create_app(indexed_settings)) as client:
        yield client


def test_ranks_by_best_triplet_and_returns_current_original_schemas(client, indexed_settings, embeddings):
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 3})
    assert response.status_code == 200
    body = response.json()
    # Summing scores would put B above A; the highest matching triplet puts A first.
    assert body["data"] == expected_data(["C", "A", "B"])
    assert body["meta_data"] == [{
        "embedding_model": "BAAI/bge-m3", "top_k": 3, "retrieved_count": 3, "unmapped_table_count": 0,
    }]
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.queries == [QUERY] and embeddings.documents == []
    assert snapshot_files(indexed_settings) == before
    assert not Path(indexed_settings.rdf_triplet).exists() and not Path(indexed_settings.embedded_schema).exists()
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize("top_k,names", [
    (1, ["C"]), (4, ["C", "A", "B", "D"]), (5, ["C", "A", "B", "D", "E"]),
    (100, ["C", "A", "B", "D", "E"]), (10 ** 100, ["C", "A", "B", "D", "E"]),
])
def test_top_k_counts_unique_tables_and_searches_past_repeated_endpoints(client, top_k, names):
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": top_k})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    assert response.json()["meta_data"][0]["retrieved_count"] == len(names)


@pytest.mark.parametrize("entries,names", [
    ([("B", "tie", "A", 1.0)], ["A", "B"]),
    ([("C", "self", "C", 1.0)], ["C"]),
    ([("A", "n0", "B", -0.8), ("D", "n1", "C", -0.1), ("E", "n2", "E", -1.0)], ["C", "D", "A", "B", "E"]),
])
def test_ties_self_relations_and_negative_scores(client, indexed_settings, entries, names):
    save_snapshot(indexed_settings, entries)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 10})
    assert result.status_code == 200 and result.json()["data"] == expected_data(names)


def test_missing_catalog_tables_are_skipped_without_dropping_the_other_endpoint(client, indexed_settings):
    save_snapshot(indexed_settings, [
        ("UNKNOWN", "u0", "A", 0.99), ("UNKNOWN", "u1", "B", 0.98),
        ("MISSING", "u2", "UNKNOWN", 0.97), ("D", "u3", "C", 0.5),
    ])
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 10})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["A", "B", "C", "D"])
    assert response.json()["meta_data"][0]["unmapped_table_count"] == 2


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_no_mappable_tables_returns_empty_lists_without_embedding(client, indexed_settings, embeddings, empty_catalog):
    Path(indexed_settings.dataset_path).write_text(json.dumps([] if empty_catalog else [table("UNINDEXED")]))
    indexed_settings.embedding_api_base = ""
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 3})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["unmapped_table_count"] == 5
    assert embeddings.queries == []


def test_valid_empty_index_skips_embedding_and_catalog_loading(client, indexed_settings, embeddings):
    save_snapshot(indexed_settings, [])
    Path(indexed_settings.dataset_path).unlink()
    indexed_settings.embedding_api_base = ""
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 3})
    assert response.status_code == 200 and response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["unmapped_table_count"] == 0
    assert embeddings.queries == []


def test_catalog_changes_are_reflected_without_reembedding_triplets(client, indexed_settings, embeddings):
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 1}).status_code == 200
    before = snapshot_files(indexed_settings)
    updated = table("C")
    updated["schema"] = "CREATE TABLE C (changed_column INTEGER);"
    updated["description"] = "갱신된 원본 설명"
    Path(indexed_settings.dataset_path).write_text(json.dumps([updated]))
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert result.status_code == 200
    assert result.json()["data"] == {key: [updated[key]] for key in ("table_name", "schema", "description")}
    assert result.json()["meta_data"][0]["unmapped_table_count"] == 4
    assert snapshot_files(indexed_settings) == before and embeddings.documents == []


@pytest.mark.parametrize("count", [0, 1, 3, 4, 6])
def test_query_embedding_uses_only_recent_three_messages(client, embeddings, count):
    query = f"  {QUERY}  "
    history = HISTORY[:count]
    response = client.post(ENDPOINT, json={"query": query, "top_k": 2, "history": history})
    assert response.status_code == 200
    expected = "\n\n".join([*(f"{message['role']}: {message['content']}" for message in history[-3:]), f"user: {query}"]) if history else query
    assert embeddings.queries == [expected]


def test_history_changes_the_query_vector_and_is_not_retained(client, embeddings, monkeypatch):
    def contextual_embedding(text):
        embeddings.queries.append(text)
        return [-1.0, 0.0, 0.0] if HISTORY[-1]["content"] in text else [1.0, 0.0, 0.0]

    monkeypatch.setattr(embeddings, "embed_query", contextual_embedding)
    with_history = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "history": HISTORY})
    without_history = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert with_history.json()["data"] == expected_data(["D"])
    assert without_history.json()["data"] == expected_data(["C"])
    assert embeddings.queries[-1] == QUERY


@pytest.mark.parametrize("payload", [
    {}, {"query": QUERY}, {"top_k": 1}, {"query": "", "top_k": 1}, {"query": " \n", "top_k": 1},
    {"query": 123, "top_k": 1}, {"query": None, "top_k": 1},
    {"query": QUERY, "top_k": 0}, {"query": QUERY, "top_k": -1}, {"query": QUERY, "top_k": "2"},
    {"query": QUERY, "top_k": 2.0}, {"query": QUERY, "top_k": True},
    {"query": QUERY, "top_k": 1, "schema": {}}, {"query": QUERY, "top_k": 1, "history": None},
    {"query": QUERY, "top_k": 1, "history": [{"role": "user", "content": " "}]},
    {"query": QUERY, "top_k": 1, "history": [{"role": "system", "content": "old invalid"}, *HISTORY[-3:]]},
])
def test_bad_requests_are_422_without_embedding(client, embeddings, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422 and embeddings.queries == []


@pytest.mark.parametrize("missing", ["root", "snapshot"])
def test_missing_index_is_404_without_creating_storage_or_using_other_indexes(client, indexed_settings, embeddings, missing):
    other = AppSettings(embedded_triplet=indexed_settings.embedded_triplet_open_ai)
    save_snapshot(other)
    root = Path(indexed_settings.embedded_triplet)
    shutil.rmtree(root if missing == "root" else root / "triplets")
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 3})
    assert response.status_code == 404
    assert "/retrieve/indexing_triplet" in response.json()["msg"] and response.json()["data"] is None
    assert embeddings.queries == []
    if missing == "root":
        assert not root.exists()
    else:
        assert list(root.iterdir()) == []


@pytest.mark.parametrize("damage", ["missing_file", "invalid_index", "wrong_id", "raw_mismatch", "wrong_metric"])
def test_damaged_index_is_409_without_embedding_or_modification(client, indexed_settings, embeddings, damage):
    directory = Path(indexed_settings.embedded_triplet) / "triplets"
    if damage == "missing_file":
        (directory / "raw_data.json").unlink()
    elif damage == "invalid_index":
        (directory / "index.faiss").write_bytes(b"invalid index")
    elif damage == "wrong_metric":
        faiss.write_index(faiss.IndexFlatL2(3), str(directory / "index.faiss"))
    elif damage == "wrong_id":
        path = directory / "metadata.json"
        value = json.loads(path.read_text())
        value["items"][0]["triplet_id"] = "0" * 64
        path.write_text(json.dumps(value))
    else:
        path = directory / "raw_data.json"
        value = json.loads(path.read_text())
        value[0]["triplet"][0]["table_name"] = "CHANGED"
        path.write_text(json.dumps(value))
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 409 and response.json()["data"] is None
    assert embeddings.queries == [] and snapshot_files(indexed_settings) == before


def test_model_mismatch_is_rejected_before_embedding(client, indexed_settings, embeddings):
    indexed_settings.embedding_model_name = "different-model"
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 409
    assert embeddings.queries == []


@pytest.mark.parametrize("damage", ["missing", "json", "schema", "duplicate"])
def test_bad_catalog_is_500_without_embedding(client, indexed_settings, embeddings, damage):
    path = Path(indexed_settings.dataset_path)
    if damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_text("invalid JSON")
    elif damage == "schema":
        path.write_text('[{"table_name":"A"}]')
    else:
        path.write_text(json.dumps([table("A"), table("A")]))
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 500 and embeddings.queries == []


@pytest.mark.parametrize("field", ["embedded_triplet", "embedding_api_base", "embedding_model_name", "dataset_path"])
def test_bad_configuration_is_500(client, indexed_settings, embeddings, field):
    setattr(indexed_settings, field, "")
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 500
    assert embeddings.queries == []


def test_query_dimension_conflict_is_409(client, embeddings):
    embeddings.vector = [1.0, 2.0]
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 409


@pytest.mark.parametrize("vector", [[], [0.0, 0.0, 0.0], [float("nan"), 1.0, 0.0],
                                     [float("inf"), 1.0, 0.0], None, "invalid", [[1.0], [2.0]]])
def test_invalid_query_vectors_are_502_and_do_not_write(client, indexed_settings, embeddings, vector):
    before = snapshot_files(indexed_settings)
    embeddings.vector = vector
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 502 and snapshot_files(indexed_settings) == before


def test_embedding_failure_returns_common_error(client, embeddings):
    embeddings.error = RuntimeError("embedding server unavailable")
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "error" and body["data"] is None and body["meta_data"] == []
    assert "embedding server unavailable" in body["msg"] and body["elapsed_time"] >= 0


@pytest.mark.parametrize("damage", ["nan", "negative_row", "out_of_range", "duplicate_row", "float_row", "row_shape", "score_shape"])
def test_invalid_faiss_search_results_are_409(client, monkeypatch, damage):
    original = TripletIndexStore.load

    def damaged_load(store, model):
        index, records, metadata = original(store, model)

        def search(vectors, count):
            assert count == index.ntotal
            scores = np.ones((1, count), dtype=np.float32)
            rows = np.arange(count, dtype=np.int64).reshape(1, -1)
            if damage == "nan":
                scores[0, 0] = np.nan
            elif damage == "negative_row":
                rows[0, 0] = -1
            elif damage == "out_of_range":
                rows[0, 0] = count
            elif damage == "duplicate_row":
                rows[0, 0] = rows[0, 1]
            elif damage == "float_row":
                rows = rows.astype(np.float32)
            elif damage == "row_shape":
                rows = rows[:, :-1]
            else:
                scores = scores[:, :-1]
            return scores, rows

        return SimpleNamespace(ntotal=index.ntotal, d=index.d, search=search), records, metadata

    monkeypatch.setattr(TripletIndexStore, "load", damaged_load)
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 409


@pytest.mark.parametrize("include_history", [False, True])
def test_real_embedding_client_sends_query_context_to_configured_url(indexed_settings, monkeypatch, include_history):
    requests = []
    query = f"  {QUERY}  "
    payload = {"query": query, "top_k": 2}
    expected = query
    if include_history:
        payload["history"] = HISTORY
        expected = "\n\n".join([*(f"{m['role']}: {m['content']}" for m in HISTORY[-3:]), f"user: {query}"])

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://embedding.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == "BAAI/bge-m3" and body["input"] == [expected]
        return httpx.Response(200, json={
            "object": "list", "model": "BAAI/bge-m3",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        monkeypatch.setattr(service_module, "create_embedding_client", lambda settings, **options:
                            create_embedding_client(settings, http_client=http_client))
        with TestClient(create_app(indexed_settings)) as client:
            response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 200 and response.json()["data"] == expected_data(["C", "A"])
    assert len(requests) == 1


def test_indexing_endpoint_produces_a_searchable_triplet_snapshot(settings, embeddings, monkeypatch):
    monkeypatch.setattr(indexing_module, "create_embedding_client", lambda *args, **kwargs: embeddings)
    source = Path(settings.rdf_triplet) / "triplets.json"
    source.parent.mkdir()
    source.write_text(json.dumps([
        [{"table_name": "A", "description": "A"}, "references", {"table_name": "B", "description": "B"}],
        [{"table_name": "B", "description": "B"}, "references", {"table_name": "C", "description": "C"}],
    ]))
    with TestClient(create_app(settings)) as client:
        indexed = client.post("/retrieve/indexing_triplet")
        response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert indexed.status_code == response.status_code == 200
    assert response.json()["data"] == expected_data(["A", "B"])
    assert len(embeddings.documents) == 1 and embeddings.queries == [QUERY]


def test_concurrent_searches_share_the_read_lock(indexed_settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = TripletIndexStore.load

    def synchronized_load(store, model):
        barrier.wait(timeout=10)
        return original(store, model)

    monkeypatch.setattr(TripletIndexStore, "load", synchronized_load)

    def request():
        with TestClient(create_app(indexed_settings)) as client:
            return client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["data"] == expected_data(["C", "A"]) for response in responses)


def test_search_waits_for_writer_and_uses_committed_snapshot(indexed_settings, embeddings, monkeypatch):
    attempted = threading.Event()
    original = TripletIndexStore.locked

    @contextmanager
    def observed_lock(store, *, shared=False):
        if shared:
            attempted.set()
        with original(store, shared=shared):
            yield

    monkeypatch.setattr(TripletIndexStore, "locked", observed_lock)
    store = TripletIndexStore(Path(indexed_settings.embedded_triplet))

    def request():
        with TestClient(create_app(indexed_settings)) as client:
            return client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})

    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.locked():
            future = executor.submit(request)
            assert attempted.wait(timeout=10)
            index, records = make_snapshot([("D", "new", "E", 1.0)])
            store.save(index, records, indexed_settings.embedding_model_name)
        response = future.result(timeout=20)
    assert response.status_code == 200 and response.json()["data"] == expected_data(["D"])


def test_network_call_releases_lock_and_keeps_loaded_snapshot(client, indexed_settings, embeddings):
    store = TripletIndexStore(Path(indexed_settings.embedded_triplet))

    def publish_new_snapshot():
        descriptor = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            index, records = make_snapshot([("E", "new", "E", 1.0)])
            store.save(index, records, indexed_settings.embedding_model_name)
        finally:
            os.close(descriptor)

    embeddings.hook = publish_new_snapshot
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["C"])
    assert faiss.read_index(str(store.directory / "index.faiss")).ntotal == 1


def test_cancellation_leaves_no_read_lock_or_file_changes(indexed_settings, embeddings):
    entered, release = threading.Event(), threading.Event()
    before = snapshot_files(indexed_settings)

    def hold_embedding():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("embedding was not released")

    embeddings.hook = hold_embedding

    async def run():
        service = EmbeddedTripletBasedSchemaService(indexed_settings, client_factory=lambda *args, **kwargs: embeddings)
        task = asyncio.create_task(service.embedded_triplet_based_schema(EmbeddedTripletBasedSchemaRequest(query=QUERY, top_k=2)))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            descriptor = os.open(indexed_settings.embedded_triplet, os.O_RDONLY | os.O_DIRECTORY)
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
    assert snapshot_files(indexed_settings) == before


def test_openapi_exposes_endpoint_specific_models(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddedTripletBasedSchemaRequest")
    for status in ("200", "404", "409", "500", "502"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddedTripletBasedSchemaResponse")
    assert "422" in operation["responses"]
    models = spec["components"]["schemas"]
    request = models["EmbeddedTripletBasedSchemaRequest"]
    assert set(request["required"]) == {"query", "top_k"}
    assert set(request["properties"]) == {"query", "top_k", "history"} and request["additionalProperties"] is False
    assert "history[-3:]" in request["properties"]["history"]["description"]
    assert set(models["EmbeddedTripletBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}
    assert len(spec["paths"]) == 25


def test_logs_show_request_context_and_table_counts(client, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 3, "history": HISTORY}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "embedded_triplet_based_schema"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"storage_lock", "index_load", "catalog_load", "embedding_setup", "index_search"} <= {r.stage for r in records}
    assert "history_count=6 used_history_count=3" in records[0].getMessage()
    assert "retrieved_count=3 unmapped_table_count=0" in records[-1].getMessage()
