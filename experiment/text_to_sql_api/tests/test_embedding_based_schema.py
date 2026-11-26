from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import threading

import faiss
from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import AppSettings
from app.core.logging import LOGGER_NAME
from app.services import embedding_based_schema_service as service_module
from app.services import indexing_schema_service as indexing_module
from app.services.embedding_client import create_embedding_client, normalize_embedding_vectors
from app.services.schema_index_store import SchemaIndexStore
from main import create_app


ENDPOINT = "/retrieve/embedding_based_schema"
QUERY = "학기별 과목과 학과 정보를 조회하고 싶어"
HISTORY_MESSAGES = [
    {"role": "user", "content": "첫 질문: 수강신청 테이블을 찾아줘"},
    {"role": "assistant", "content": "  첫 답변: 수강신청 정보를 찾았습니다.\n학기 조건도 사용할 수 있습니다.  "},
    {"role": "user", "content": "두 번째 질문: 학과별로 보고 싶어"},
    {"role": "assistant", "content": "두 번째 답변: 학과 정보를 함께 조회합니다."},
    {"role": "user", "content": "세 번째 질문: 과목 목록도 필요해"},
    {"role": "assistant", "content": "세 번째 답변: 과목 테이블을 찾았습니다."},
]
VECTORS = {"A": [2.0, 0.0, 0.0], "B": [0.6, 0.8, 0.0],
           "C": [-1.0, 0.0, 0.0], "D": [0.8, 0.6, 0.0]}


def record(name):
    return {"table_name": name, "schema": f"CREATE TABLE {name} (id INTEGER);",
            "description": f"{name}의 테이블 설명", "extra_info": "preserved original"}


def make_snapshot(names=("B", "C", "A")):
    index = faiss.IndexFlatIP(3)
    if names:
        index.add(normalize_embedding_vectors([VECTORS[name] for name in names], len(names)))
    return index, [record(name) for name in names]


def save_snapshot(settings, names=("B", "C", "A")):
    store = SchemaIndexStore(Path(settings.embedded_schema))
    index, records = make_snapshot(names)
    with store.locked():
        store.save(index, records, settings.embedding_model_name)


def snapshot_files(settings):
    root = Path(settings.embedded_schema)
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def expected_data(names):
    return {key: [record(name)[key] for name in names] for key in ("table_name", "schema", "description")}


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
        return [VECTORS[text.splitlines()[0].removeprefix("table_name: ")] for text in texts]


@pytest.fixture
def settings(tmp_path):
    return AppSettings(dataset_path=str(tmp_path / "unused-db_schema.json"),
                       embedded_schema=str(tmp_path / "embedded_schema"),
                       embedding_api_base="https://embedding.test/v1")


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


def test_returns_ranked_parallel_lists_from_stored_originals(client, indexed_settings, embeddings):
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["A", "B"])
    assert body["meta_data"] == [{"embedding_model": "BAAI/bge-m3", "top_k": 2, "retrieved_count": 2}]
    assert body["status"] == body["msg"] == "success"
    assert body["elapsed_time"] >= 0
    assert embeddings.queries == [QUERY]
    assert embeddings.documents == []
    assert not Path(indexed_settings.dataset_path).exists()
    assert snapshot_files(indexed_settings) == before


@pytest.mark.parametrize("count,selected", [
    (0, []), (1, [0]), (2, [0, 1]), (3, [0, 1, 2]), (4, [1, 2, 3]), (6, [3, 4, 5]),
])
def test_history_uses_last_three_messages_in_order_before_current_query(client, embeddings, count, selected):
    history = HISTORY_MESSAGES[:count]
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2, "history": history})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["A", "B"])
    if selected:
        expected = "\n\n".join([
            *(f"{HISTORY_MESSAGES[i]['role']}: {HISTORY_MESSAGES[i]['content']}" for i in selected),
            f"user: {QUERY}",
        ])
    else:
        expected = QUERY
    assert embeddings.queries == [expected]
    assert embeddings.documents == []
    for i in set(range(count)) - set(selected):
        assert HISTORY_MESSAGES[i]["content"] not in embeddings.queries[0]


def test_history_context_changes_the_vector_used_for_search(client, indexed_settings, embeddings, monkeypatch):
    history = [
        {"role": "user", "content": "제외해야 하는 오래된 질문"},
        {"role": "assistant", "content": "최근 답변 하나"},
        {"role": "user", "content": "최근 질문 둘"},
        {"role": "assistant", "content": "최근 답변 셋"},
    ]
    expected_context = (
        "assistant: 최근 답변 하나\n\nuser: 최근 질문 둘\n\n"
        f"assistant: 최근 답변 셋\n\nuser: {QUERY}"
    )

    def contextual_embedding(text):
        embeddings.queries.append(text)
        return [-1.0, 0.0, 0.0] if text == expected_context else [1.0, 0.0, 0.0]

    monkeypatch.setattr(embeddings, "embed_query", contextual_embedding)
    before = snapshot_files(indexed_settings)
    with_history = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "history": history})
    without_history = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert with_history.status_code == without_history.status_code == 200
    assert with_history.json()["data"] == expected_data(["C"])
    assert without_history.json()["data"] == expected_data(["A"])
    assert embeddings.queries == [expected_context, QUERY]
    assert snapshot_files(indexed_settings) == before


def test_history_is_not_retained_between_requests(client, embeddings):
    for payload in (
        {"query": QUERY, "top_k": 1, "history": HISTORY_MESSAGES[:1]},
        {"query": QUERY, "top_k": 1},
        {"query": QUERY, "top_k": 1, "history": []},
    ):
        assert client.post(ENDPOINT, json=payload).status_code == 200
    assert embeddings.queries == [
        f"user: {HISTORY_MESSAGES[0]['content']}\n\nuser: {QUERY}", QUERY, QUERY,
    ]


@pytest.mark.parametrize("history", [
    None, "invalid", {}, [None], ["user: question"], [{}],
    [{"role": "system", "content": "unsupported role"}],
    [{"role": "user"}], [{"content": "missing role"}],
    [{"role": "user", "content": ""}], [{"role": "assistant", "content": " \n\t"}],
    [{"role": "user", "content": 123}], [{"role": "assistant", "content": None}],
    [{"role": "system", "content": "old invalid message"}, *HISTORY_MESSAGES[-3:]],
])
def test_invalid_history_returns_422_without_embedding(client, embeddings, history):
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2, "history": history})
    assert response.status_code == 422
    assert any(error["loc"][:2] == ["body", "history"] for error in response.json()["detail"])
    assert embeddings.queries == []


@pytest.mark.parametrize("top_k,names", [
    (1, ["A"]), (3, ["A", "B", "C"]), (100, ["A", "B", "C"]), (10 ** 100, ["A", "B", "C"]),
])
def test_top_k_is_capped_without_filtering_negative_similarities(client, top_k, names):
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": top_k})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(names)
    assert response.json()["meta_data"][0]["retrieved_count"] == len(names)


@pytest.mark.parametrize("payload", [
    {}, {"query": QUERY}, {"top_k": 1}, {"query": "", "top_k": 1},
    {"query": " \n\t", "top_k": 1}, {"query": 123, "top_k": 1},
    {"query": None, "top_k": 1}, {"query": QUERY, "top_k": 0},
    {"query": QUERY, "top_k": -1}, {"query": QUERY, "top_k": "2"},
    {"query": QUERY, "top_k": 2.0}, {"query": QUERY, "top_k": True},
])
def test_invalid_requests_return_422_without_embedding(client, embeddings, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert embeddings.queries == []


@pytest.mark.parametrize("missing", ["root", "snapshot"])
def test_absent_index_returns_404_without_creating_storage(client, indexed_settings, embeddings, missing):
    root = Path(indexed_settings.embedded_schema)
    shutil.rmtree(root if missing == "root" else root / "db_schema")
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 404
    assert response.json()["status"] == "error"
    assert response.json()["data"] is None
    assert embeddings.queries == []
    if missing == "root":
        assert not root.exists()
    else:
        assert list(root.iterdir()) == []


def test_empty_index_returns_empty_lists_without_embedding(client, indexed_settings, embeddings):
    save_snapshot(indexed_settings, ())
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data([])
    assert response.json()["meta_data"][0]["retrieved_count"] == 0
    assert embeddings.queries == []
    assert snapshot_files(indexed_settings) == before


@pytest.mark.parametrize("damage", ["missing_file", "invalid_index", "wrong_order", "wrong_dimension", "wrong_metric"])
def test_damaged_index_returns_409_without_modifying_files(client, indexed_settings, embeddings, damage):
    directory = Path(indexed_settings.embedded_schema) / "db_schema"
    if damage == "missing_file":
        (directory / "raw_data.json").unlink()
    elif damage == "invalid_index":
        (directory / "index.faiss").write_bytes(b"invalid index")
    elif damage == "wrong_metric":
        faiss.write_index(faiss.IndexFlatL2(3), str(directory / "index.faiss"))
    else:
        path = directory / "metadata.json"
        metadata = json.loads(path.read_text())
        if damage == "wrong_order":
            metadata["items"][0]["table_name"] = "A"
        else:
            metadata["dimension"] = 99
        path.write_text(json.dumps(metadata))
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert embeddings.queries == []
    assert snapshot_files(indexed_settings) == before


def test_model_mismatch_is_rejected_before_query_embedding(client, indexed_settings, embeddings):
    indexed_settings.embedding_model_name = "different-model"
    assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 2}).status_code == 409
    assert embeddings.queries == []


def test_query_dimension_mismatch_is_409(client, embeddings):
    embeddings.vector = [1.0, 2.0]
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 409
    assert response.json()["data"] is None


@pytest.mark.parametrize("vector", [[], [0.0, 0.0, 0.0], [float("nan"), 1.0, 0.0],
                                     [float("inf"), 1.0, 0.0], None, "invalid", [[1.0], [2.0]]])
def test_invalid_query_vectors_return_502(client, indexed_settings, embeddings, vector):
    embeddings.vector = vector
    before = snapshot_files(indexed_settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert snapshot_files(indexed_settings) == before


def test_embedding_server_error_returns_common_error(client, embeddings):
    embeddings.error = RuntimeError("embedding server unavailable")
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 502
    body = response.json()
    assert body["data"] is None and body["meta_data"] == []
    assert body["status"] == "error"
    assert "embedding server unavailable" in body["msg"]
    assert body["elapsed_time"] >= 0


@pytest.mark.parametrize("field", ["embedded_schema", "embedding_api_base"])
def test_invalid_configuration_returns_500(client, indexed_settings, embeddings, field):
    setattr(indexed_settings, field, "")
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 500
    assert embeddings.queries == []


@pytest.mark.parametrize("include_history", [False, True])
def test_real_embedding_client_sends_query_context_to_configured_url(indexed_settings, monkeypatch, include_history):
    requests = []
    query = "  원문 질의를 그대로 전송  "
    payload = {"query": query, "top_k": 1}
    expected_text = query
    if include_history:
        payload["history"] = HISTORY_MESSAGES
        expected_text = "\n\n".join([
            f"assistant: {HISTORY_MESSAGES[3]['content']}",
            f"user: {HISTORY_MESSAGES[4]['content']}",
            f"assistant: {HISTORY_MESSAGES[5]['content']}",
            f"user: {query}",
        ])

    def handle(request):
        requests.append(request)
        request_body = json.loads(request.content)
        assert request_body["input"] == [expected_text]
        assert request_body["model"] == "BAAI/bge-m3"
        return httpx.Response(200, json={
            "object": "list", "model": "BAAI/bge-m3",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport_client:
        monkeypatch.setattr(service_module, "create_embedding_client", lambda settings, **options:
                            create_embedding_client(settings, http_client=transport_client))
        with TestClient(create_app(indexed_settings)) as client:
            response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["A"])
    assert len(requests) == 1
    assert str(requests[0].url) == "https://embedding.test/v1/embeddings"


def test_public_indexing_endpoint_creates_searchable_snapshot(settings, embeddings, monkeypatch):
    Path(settings.dataset_path).write_text(json.dumps([record(name) for name in ("B", "C", "A")]), encoding="utf-8")
    monkeypatch.setattr(indexing_module, "create_embedding_client", lambda *args, **kwargs: embeddings)
    with TestClient(create_app(settings)) as client:
        indexed = client.post("/retrieve/indexing_schema")
        result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert indexed.status_code == result.status_code == 200
    assert indexed.json()["data"]["added_count"] == 3
    assert result.json()["data"] == expected_data(["A", "B"])
    assert len(embeddings.documents) == 1 and embeddings.queries == [QUERY]


def test_concurrent_searches_share_the_read_lock(indexed_settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = SchemaIndexStore.load

    def synchronized_load(store, model):
        barrier.wait(timeout=10)
        return original(store, model)

    monkeypatch.setattr(SchemaIndexStore, "load", synchronized_load)

    def request():
        with TestClient(create_app(indexed_settings)) as client:
            return client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["data"] == expected_data(["A", "B"]) for response in responses)
    assert len(embeddings.queries) == 2


def test_search_waits_for_writer_and_loads_committed_snapshot(indexed_settings, embeddings, monkeypatch):
    attempted = threading.Event()
    original = SchemaIndexStore.locked

    @contextmanager
    def observed_lock(store, *, shared=False):
        if shared:
            attempted.set()
        with original(store, shared=shared):
            yield

    monkeypatch.setattr(SchemaIndexStore, "locked", observed_lock)
    store = SchemaIndexStore(Path(indexed_settings.embedded_schema))

    def request():
        with TestClient(create_app(indexed_settings)) as client:
            return client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})

    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.locked():
            future = executor.submit(request)
            assert attempted.wait(timeout=10)
            index, records = make_snapshot(("B", "C", "A", "D"))
            store.save(index, records, indexed_settings.embedding_model_name)
        response = future.result(timeout=20)
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["A", "D"])


def test_embedding_releases_lock_and_uses_consistent_loaded_snapshot(client, indexed_settings, embeddings):
    store = SchemaIndexStore(Path(indexed_settings.embedded_schema))

    def publish_new_snapshot():
        descriptor = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            # Fail immediately if the search still holds its shared lock.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            index, records = make_snapshot(("B", "C", "A", "D"))
            store.save(index, records, indexed_settings.embedding_model_name)
        finally:
            os.close(descriptor)

    embeddings.hook = publish_new_snapshot
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 2})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["A", "B"])
    assert faiss.read_index(str(store.directory / "index.faiss")).ntotal == 4


def test_openapi_defines_endpoint_specific_request_and_response(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddingBasedSchemaRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/EmbeddingBasedSchemaResponse")
    assert {"200", "404", "409", "422", "500", "502"} <= set(operation["responses"])
    models = spec["components"]["schemas"]
    assert set(models["EmbeddingBasedSchemaRequest"]["required"]) == {"query", "top_k"}
    history = models["EmbeddingBasedSchemaRequest"]["properties"]["history"]
    assert history["type"] == "array"
    assert history["items"]["$ref"].endswith("/ConversationHistoryMessage")
    assert "history[-3:]" in history["description"]
    message = models["ConversationHistoryMessage"]
    assert set(message["required"]) == {"role", "content"}
    assert message["properties"]["role"]["enum"] == ["user", "assistant"]
    assert message["properties"]["content"]["minLength"] == 1
    assert set(models["EmbeddingBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}


@pytest.mark.parametrize("history_count", [0, 4])
def test_search_logs_are_distinguished_from_indexing(client, caplog, history_count):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = client.post(ENDPOINT, json={
            "query": QUERY, "top_k": 2, "history": HISTORY_MESSAGES[:history_count],
        })
        assert response.status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "embedding_based_schema"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    preparation = next(r.getMessage() for r in records if r.stage == "embedding_setup")
    assert f"history_count={history_count}" in preparation
    assert f"used_history_count={min(history_count, 3)}" in preparation
    assert "embedding_text_length=" in preparation
    console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
    assert "[embedding_based_schema]" in console.formatter.format(records[-1])
