import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch

import faiss
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from app.core import config
from app.core.config import AppSettings, PROJECT_ROOT
from app.services import indexing_schema_service as service_module
from app.services.embedding_client import create_embedding_client
from app.services.indexing_schema_service import IndexingSchemaService
from app.services.schema_index_store import SchemaIndexStore
from main import create_app


ENDPOINT = "/retrieve/indexing_schema"


def table(name, description=None):
    return {
        "table_name": name,
        "schema": f"CREATE TABLE {name} (id INTEGER);",
        "description": description if description is not None else f"{name} 테이블 설명",
    }


def write_dataset(settings, records):
    Path(settings.dataset_path).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def snapshot_bytes(settings):
    directory = Path(settings.embedded_schema) / "db_schema"
    return {name: (directory / name).read_bytes() for name in ("index.faiss", "metadata.json", "raw_data.json")}


def read_snapshot(settings):
    directory = Path(settings.embedded_schema) / "db_schema"
    return (
        faiss.read_index(str(directory / "index.faiss")),
        json.loads((directory / "metadata.json").read_text(encoding="utf-8")),
        json.loads((directory / "raw_data.json").read_text(encoding="utf-8")),
    )


class FakeEmbeddings:
    def __init__(self):
        self.calls = []
        self.error = None
        self.vectors = None
        self.on_embed = None

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.on_embed is not None:
            self.on_embed()
        if self.error is not None:
            raise self.error
        if self.vectors is not None:
            return self.vectors
        return [[1.0, float(len(text)), float(sum(text.encode()) % 17 + 1)] for text in texts]


@pytest.fixture
def settings(tmp_path):
    settings = AppSettings(
        dataset_path=str(tmp_path / "db_schema.json"),
        embedded_schema=str(tmp_path / "embedded_schema"),
        embedding_api_base="https://embedding.test/v1",
    )
    write_dataset(settings, [table("A"), table("B")])
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(service_module, "create_embedding_client", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def client(settings, embeddings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_initial_index_embeds_names_and_descriptions_and_preserves_raw_data(client, settings, embeddings):
    originals = [table("A"), table("B")]
    originals[0]["extra_info"] = {"owner": "sample"}
    write_dataset(settings, originals)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"] == {
        "added_count": 2, "skipped_count": 0, "total_count": 2, "added_tables": ["A", "B"],
    }
    assert embeddings.calls == [[
        "table_name: A\ndescription: A 테이블 설명",
        "table_name: B\ndescription: B 테이블 설명",
    ]]
    index, metadata, raw = read_snapshot(settings)
    assert index.ntotal == 2
    assert index.d == 3
    assert index.metric_type == faiss.METRIC_INNER_PRODUCT
    np.testing.assert_allclose(np.linalg.norm(index.reconstruct_n(), axis=1), 1.0, atol=1e-6)
    assert metadata["items"] == [{"index": 0, "table_name": "A"}, {"index": 1, "table_name": "B"}]
    assert metadata["embedding_model"] == "BAAI/bge-m3"
    assert raw == originals
    assert body["meta_data"][0]["dimension"] == 3
    assert body["elapsed_time"] >= 0


def test_unchanged_index_is_not_embedded_or_rewritten(client, settings, embeddings, monkeypatch):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)

    def unexpected_save(*args):
        pytest.fail("An unchanged snapshot must not be rewritten")

    monkeypatch.setattr(SchemaIndexStore, "save", unexpected_save)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 0, "skipped_count": 2, "total_count": 2, "added_tables": [],
    }
    assert len(embeddings.calls) == 1
    assert snapshot_bytes(settings) == before


def test_addition_preserves_existing_vectors_changes_and_removed_tables(client, settings, embeddings):
    client.post(ENDPOINT)
    before_index, _, before_raw = read_snapshot(settings)
    write_dataset(settings, [table("B", "수정된 설명"), table("C")])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 1, "skipped_count": 1, "total_count": 3, "added_tables": ["C"],
    }
    assert embeddings.calls[-1] == ["table_name: C\ndescription: C 테이블 설명"]
    after_index, _, raw = read_snapshot(settings)
    np.testing.assert_array_equal(after_index.reconstruct_n(0, 2), before_index.reconstruct_n())
    assert raw == before_raw + [table("C")]


def test_persistent_index_is_reused_by_a_new_application(client, settings, embeddings):
    client.post(ENDPOINT)
    with TestClient(create_app(settings)) as restarted:
        response = restarted.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"]["added_count"] == 0
    assert len(embeddings.calls) == 1


def test_supplied_97_tables_are_indexed_once(settings, embeddings):
    settings.dataset_path = str(PROJECT_ROOT / "dataset/db_schema.json")
    with TestClient(create_app(settings)) as client:
        first = client.post(ENDPOINT)
        second = client.post(ENDPOINT)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["added_count"] == 97
    assert second.json()["data"]["added_count"] == 0
    assert second.json()["data"]["skipped_count"] == 97
    assert len(embeddings.calls) == 1
    assert len(embeddings.calls[0]) == 97
    assert read_snapshot(settings)[0].ntotal == 97


@pytest.mark.parametrize("records", [
    {}, [table("A"), table("A")], [{"table_name": "A", "schema": "DDL"}],
    [table(" A")], [table("A", " ")], [{**table("A"), "table_name": 12}],
])
def test_invalid_source_returns_common_error_without_embedding(client, settings, embeddings, records):
    write_dataset(settings, records)
    response = client.post(ENDPOINT)
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert response.json()["data"] is None
    assert "elapsed_time" in response.json()
    assert not embeddings.calls
    assert not (Path(settings.embedded_schema) / "db_schema").exists()


@pytest.mark.parametrize("source", ["missing", "malformed"])
def test_missing_or_malformed_json_preserves_index(client, settings, embeddings, source):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    path = Path(settings.dataset_path)
    if source == "missing":
        path.unlink()
    else:
        path.write_text("{invalid json", encoding="utf-8")
    response = client.post(ENDPOINT)
    assert response.status_code == 500
    assert snapshot_bytes(settings) == before
    assert len(embeddings.calls) == 1


def test_empty_dataset_does_not_call_embedding_or_create_snapshot(client, settings, embeddings):
    write_dataset(settings, [])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 0, "skipped_count": 0, "total_count": 0, "added_tables": [],
    }
    assert not embeddings.calls
    assert not (Path(settings.embedded_schema) / "db_schema").exists()


def test_embedding_api_failure_preserves_snapshot(client, settings, embeddings):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    write_dataset(settings, [table("A"), table("B"), table("C")])
    embeddings.error = RuntimeError("embedding server unavailable")
    response = client.post(ENDPOINT)
    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert snapshot_bytes(settings) == before


@pytest.mark.parametrize("vectors", [
    [[1.0, 2.0]], [[1.0], [1.0, 2.0]], [[0.0], [0.0]],
    [[float("nan")], [1.0]], [[float("inf")], [1.0]], [[], []],
])
def test_invalid_embedding_vectors_are_rejected(client, settings, embeddings, vectors):
    embeddings.vectors = vectors
    response = client.post(ENDPOINT)
    assert response.status_code == 502
    assert not (Path(settings.embedded_schema) / "db_schema").exists()


def test_embedding_dimension_change_preserves_index(client, settings, embeddings):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    write_dataset(settings, [table("C")])
    embeddings.vectors = [[1.0, 2.0]]
    response = client.post(ENDPOINT)
    assert response.status_code == 409
    assert snapshot_bytes(settings) == before


def test_embedding_model_change_is_a_conflict(client, settings, embeddings):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    settings.embedding_model_name = "different-model"
    response = client.post(ENDPOINT)
    assert response.status_code == 409
    assert snapshot_bytes(settings) == before
    assert len(embeddings.calls) == 1


@pytest.mark.parametrize("damage", ["missing_file", "wrong_count", "wrong_order", "invalid_index"])
def test_damaged_snapshot_is_not_overwritten(client, settings, embeddings, damage):
    client.post(ENDPOINT)
    directory = Path(settings.embedded_schema) / "db_schema"
    if damage == "missing_file":
        (directory / "raw_data.json").unlink()
    elif damage == "invalid_index":
        (directory / "index.faiss").write_bytes(b"invalid index")
    else:
        path = directory / "metadata.json"
        metadata = json.loads(path.read_text())
        if damage == "wrong_count":
            metadata["items"].pop()
        else:
            metadata["items"][0]["table_name"] = "B"
        path.write_text(json.dumps(metadata))
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    response = client.post(ENDPOINT)
    assert response.status_code == 409
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert len(embeddings.calls) == 1


def test_staging_write_failure_keeps_previous_snapshot(client, settings, monkeypatch):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    write_dataset(settings, [table("C")])
    original = SchemaIndexStore._write_json

    def fail_raw_data(path, value):
        if path.name == "raw_data.json":
            raise OSError("simulated disk write failure")
        return original(path, value)

    monkeypatch.setattr(SchemaIndexStore, "_write_json", staticmethod(fail_raw_data))
    assert client.post(ENDPOINT).status_code == 500
    assert snapshot_bytes(settings) == before
    assert not list(Path(settings.embedded_schema).glob(".schema-*"))


def test_publish_failure_restores_previous_snapshot(client, settings, monkeypatch):
    client.post(ENDPOINT)
    before = snapshot_bytes(settings)
    write_dataset(settings, [table("C")])
    original = Path.rename

    def fail_publish(path, target):
        if path.name.startswith(".schema-staging-"):
            raise OSError("simulated publish failure")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    assert client.post(ENDPOINT).status_code == 500
    assert snapshot_bytes(settings) == before
    assert not list(Path(settings.embedded_schema).glob(".schema-*"))


def test_concurrent_application_instances_do_not_duplicate_tables(settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = SchemaIndexStore.locked

    @contextmanager
    def synchronized_lock(store):
        barrier.wait(timeout=10)
        with original(store):
            yield

    monkeypatch.setattr(SchemaIndexStore, "locked", synchronized_lock)

    def request():
        with TestClient(create_app(settings)) as client:
            return client.post(ENDPOINT)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["data"]["added_count"] for response in responses) == [0, 2]
    assert len(embeddings.calls) == 1
    assert read_snapshot(settings)[0].ntotal == 2


def test_cancellation_finishes_transaction_before_returning(settings, embeddings):
    entered = threading.Event()
    release = threading.Event()

    def wait_for_release():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release the embedding request")

    embeddings.on_embed = wait_for_release

    async def run():
        service = IndexingSchemaService(settings)
        task = asyncio.create_task(service.index_schema())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        response = await service.index_schema()
        assert response.data.added_count == 0
        assert response.data.total_count == 2

    asyncio.run(run())
    assert len(embeddings.calls) == 1


def test_real_embedding_client_sends_raw_strings_to_configured_base(settings, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "BAAI/bge-m3"
        assert payload["input"] == [
            "table_name: A\ndescription: A 테이블 설명",
            "table_name: B\ndescription: B 테이블 설명",
        ]
        return httpx.Response(200, json={
            "object": "list", "model": "BAAI/bge-m3",
            "data": [
                {"object": "embedding", "index": row, "embedding": [1.0, 2.0, 3.0]}
                for row in range(len(payload["input"]))
            ],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport_client:
        monkeypatch.setattr(service_module, "create_embedding_client", lambda settings, **options:
                            create_embedding_client(settings, http_client=transport_client))
        with TestClient(create_app(settings)) as client:
            response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert len(requests) == 1
    assert str(requests[0].url) == "https://embedding.test/v1/embeddings"


def test_env_loading_precedence_and_paths_are_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "EMBEDDED_SCHEMA=./custom_indexes\nEMBEDDING_API_BASE=https://file.test/v1\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with patch.dict(os.environ, {"EMBEDDING_API_BASE": "https://process.test/v1"}, clear=True):
        settings = AppSettings()
    assert settings.embedding_api_base == "https://process.test/v1"
    assert settings.embedded_schema == "./custom_indexes"
    assert config.resolve_project_path(settings.embedded_schema) == tmp_path / "custom_indexes"
    assert config.resolve_project_path("./dataset/db_schema.json") == tmp_path / "dataset/db_schema.json"


def test_missing_embedding_url_is_configuration_error(client, settings, embeddings):
    settings.embedding_api_base = ""
    response = client.post(ENDPOINT)
    assert response.status_code == 500
    assert not embeddings.calls


def test_openapi_has_bodyless_post_and_documented_errors(client):
    operation = client.get("/openapi.json").json()["paths"][ENDPOINT]["post"]
    assert "requestBody" not in operation
    assert {"200", "409", "500", "502"} <= set(operation["responses"])
