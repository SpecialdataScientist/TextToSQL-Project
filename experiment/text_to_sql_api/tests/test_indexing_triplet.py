import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import hashlib
import json
import logging
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
from app.core.config import AppSettings
from app.core.logging import LOGGER_NAME
from app.schemas.triplet import RDF_TRIPLETS
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import indexing_triplet_service as service_module
from app.services.embedding_client import create_embedding_client
from app.services.indexing_triplet_service import IndexingTripletService
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/indexing_triplet"
DEFAULT_VECTORS = object()


def triplet(subject="A", predicate="references", target="B"):
    return [
        {"table_name": subject, "description": f"  {subject} 원본 설명\n"}, predicate,
        {"table_name": target, "description": f" {target} 원본 설명 "},
    ]


def original_triplets():
    return [triplet(), triplet("B", "belongs to", "C")]


def expected_id(value):
    key = [value[0]["table_name"], value[1].strip(), value[2]["table_name"]]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def write_source(settings, values):
    path = Path(settings.rdf_triplet) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return path


def write_router_source(settings, values):
    path = Path(settings.rdf_triplet_open_router) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return path


def snapshot(settings):
    directory = Path(settings.embedded_triplet) / "triplets"
    return {path.name: path.read_bytes() for path in directory.iterdir()} if directory.exists() else {}


def read_snapshot(settings):
    directory = Path(settings.embedded_triplet) / "triplets"
    return (
        faiss.read_index(str(directory / "index.faiss")),
        json.loads((directory / "metadata.json").read_text()),
        json.loads((directory / "raw_data.json").read_text()),
    )


class FakeEmbeddings:
    def __init__(self):
        self.calls = []
        self.error = None
        self.vectors = DEFAULT_VECTORS
        self.on_embed = None

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.on_embed is not None:
            self.on_embed()
        if self.error is not None:
            raise self.error
        if self.vectors is not DEFAULT_VECTORS:
            return self.vectors
        return [[1.0, float(len(text)), float(sum(text.encode()) % 17 + 1)] for text in texts]


@pytest.fixture
def settings(tmp_path):
    settings = AppSettings(
        rdf_triplet=str(tmp_path / "RDF triplets"), embedded_triplet=str(tmp_path / "embedded triplets"),
        rdf_triplet_open_router=str(tmp_path / "router RDF"),
        dataset_path=str(tmp_path / "unused.json"), embedded_schema=str(tmp_path / "schema index"),
        embedding_api_base="https://embedding.test/v1", openai_api_key="", llm_api_base="",
        openrouter_api_key="", openrouter_llm_model_name="",
    )
    write_source(settings, original_triplets())
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


def test_initial_index_embeds_relation_and_both_descriptions_and_preserves_originals(client, settings, embeddings):
    source = Path(settings.rdf_triplet) / "triplets.json"
    source_before = source.read_bytes()
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    body = response.json()
    ids = [expected_id(value) for value in original_triplets()]
    assert body["data"] == {"added_count": 2, "skipped_count": 0, "total_count": 2, "added_triplet_ids": ids}
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.calls == [[
        "subject_table_name: A\nsubject_description:   A 원본 설명\n\npredicate: references\n"
        "object_table_name: B\nobject_description:  B 원본 설명 ",
        "subject_table_name: B\nsubject_description:   B 원본 설명\n\npredicate: belongs to\n"
        "object_table_name: C\nobject_description:  C 원본 설명 ",
    ]]
    index, metadata, raw = read_snapshot(settings)
    assert index.ntotal == 2 and index.d == 3 and index.metric_type == faiss.METRIC_INNER_PRODUCT
    np.testing.assert_allclose(np.linalg.norm(index.reconstruct_n(), axis=1), 1.0, atol=1e-6)
    assert metadata == {
        "embedding_model": "BAAI/bge-m3", "dimension": 3, "metric": "cosine",
        "items": [{"index": row, "triplet_id": value} for row, value in enumerate(ids)],
    }
    assert raw == [{"triplet_id": value, "triplet": original} for value, original in zip(ids, original_triplets())]
    assert body["meta_data"] == [{
        "embedding_model": "BAAI/bge-m3", "dimension": 3,
        "index_directory": str(Path(settings.embedded_triplet) / "triplets"),
    }]
    assert source.read_bytes() == source_before
    assert not Path(settings.dataset_path).exists() and not Path(settings.embedded_schema).exists()


def test_primary_source_takes_priority_over_router(client, settings, embeddings):
    fallback = write_router_source(settings, [triplet("ROUTER_A", "router relation", "ROUTER_B")])
    before = fallback.read_bytes()
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"]["added_triplet_ids"] == [expected_id(value) for value in original_triplets()]
    assert [record["triplet"] for record in read_snapshot(settings)[2]] == original_triplets()
    assert len(embeddings.calls) == 1 and fallback.read_bytes() == before


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("missing_directory", [False, True])
def test_missing_primary_indexes_router_data_in_existing_store(client, settings, embeddings, existing, missing_directory):
    if existing:
        assert client.post(ENDPOINT).status_code == 200
        previous_vectors = read_snapshot(settings)[0].reconstruct_n()
    router_values = [original_triplets()[0], triplet("ROUTER_A", "router relation", "ROUTER_B")]
    fallback = write_router_source(settings, router_values)
    fallback_before = fallback.read_bytes()
    source = Path(settings.rdf_triplet) / "triplets.json"
    source.unlink()
    if missing_directory:
        source.parent.rmdir()
    previous_calls = len(embeddings.calls)

    response = client.post(ENDPOINT)
    assert response.status_code == 200
    additions = router_values[1:] if existing else router_values
    expected_values = original_triplets() + router_values[1:] if existing else router_values
    assert response.json()["data"] == {
        "added_count": len(additions), "skipped_count": 1 if existing else 0,
        "total_count": len(expected_values), "added_triplet_ids": [expected_id(value) for value in additions],
    }
    index, metadata, raw = read_snapshot(settings)
    assert [record["triplet"] for record in raw] == expected_values
    assert index.ntotal == len(expected_values) and index.d == 3
    assert metadata["embedding_model"] == settings.embedding_model_name
    assert response.json()["meta_data"][0]["index_directory"] == str(Path(settings.embedded_triplet) / "triplets")
    assert set(snapshot(settings)) == {"index.faiss", "metadata.json", "raw_data.json"}
    assert len(embeddings.calls) == previous_calls + 1 and len(embeddings.calls[-1]) == len(additions)
    if existing:
        np.testing.assert_array_equal(index.reconstruct_n(0, len(previous_vectors)), previous_vectors)
    assert fallback.read_bytes() == fallback_before and not source.exists()
    assert not (fallback.parent / "triplets").exists()

    before = snapshot(settings)
    repeated = client.post(ENDPOINT)
    assert repeated.status_code == 200
    assert repeated.json()["data"] == {
        "added_count": 0, "skipped_count": len(router_values), "total_count": len(expected_values),
        "added_triplet_ids": [],
    }
    assert len(embeddings.calls) == previous_calls + 1 and snapshot(settings) == before


def test_restored_primary_appends_only_new_relations_after_router_indexing(client, settings, embeddings):
    router_values = [triplet(), triplet("ROUTER_A", "router relation", "ROUTER_B")]
    write_router_source(settings, router_values)
    (Path(settings.rdf_triplet) / "triplets.json").unlink()
    assert client.post(ENDPOINT).status_code == 200
    previous_vectors = read_snapshot(settings)[0].reconstruct_n()

    addition = triplet("RESTORED_A", "restored relation", "RESTORED_B")
    write_source(settings, [triplet(), addition])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 1, "skipped_count": 1, "total_count": 3, "added_triplet_ids": [expected_id(addition)],
    }
    index, _, raw = read_snapshot(settings)
    assert [record["triplet"] for record in raw] == router_values + [addition]
    np.testing.assert_array_equal(index.reconstruct_n(0, len(previous_vectors)), previous_vectors)
    assert [len(call) for call in embeddings.calls] == [2, 1]


def test_repeat_request_skips_embedding_and_snapshot_writes(client, settings, embeddings, monkeypatch):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    settings.embedding_api_base = ""

    def unexpected_save(*args):
        pytest.fail("Unchanged triplets must not rewrite the index")

    monkeypatch.setattr(TripletIndexStore, "save", unexpected_save)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {"added_count": 0, "skipped_count": 2, "total_count": 2, "added_triplet_ids": []}
    assert len(embeddings.calls) == 1 and snapshot(settings) == before


def test_append_preserves_existing_vectors_descriptions_and_removed_relations(client, settings, embeddings):
    assert client.post(ENDPOINT).status_code == 200
    previous_index, _, previous_raw = read_snapshot(settings)
    changed = triplet()
    changed[0]["description"] = "수정된 주체 설명"
    changed[2]["description"] = "수정된 객체 설명"
    new_relation = triplet("C", "contains", "D")
    duplicate_new = copy.deepcopy(new_relation)
    duplicate_new[0]["description"] = "중복 항목의 다른 설명"
    write_source(settings, [changed, new_relation, duplicate_new])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 1, "skipped_count": 2, "total_count": 3, "added_triplet_ids": [expected_id(new_relation)],
    }
    index, _, raw = read_snapshot(settings)
    np.testing.assert_array_equal(index.reconstruct_n(0, 2), previous_index.reconstruct_n())
    assert raw == previous_raw + [{"triplet_id": expected_id(new_relation), "triplet": new_relation}]
    assert len(embeddings.calls[-1]) == 1 and "수정된" not in embeddings.calls[-1][0]


@pytest.mark.parametrize("changed,added", [
    (triplet("B", "references", "A"), 1),
    (triplet("A", "owns", "B"), 1),
    (triplet("a", "references", "B"), 1),
    (triplet("A", "  references\t", "B"), 0),
])
def test_relation_key_respects_direction_predicate_case_and_rdf_whitespace_rules(client, settings, embeddings, changed, added):
    assert client.post(ENDPOINT).status_code == 200
    write_source(settings, [changed])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"]["added_count"] == added
    assert response.json()["data"]["total_count"] == 2 + added
    assert len(embeddings.calls) == 1 + added


def test_duplicate_input_uses_first_occurrence_and_counts_all_skipped_rows(client, settings, embeddings):
    first, second = original_triplets()
    duplicate = copy.deepcopy(first)
    duplicate[0]["description"] = "다른 설명"
    write_source(settings, [first, duplicate, second, second])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"]["added_count"] == 2 and response.json()["data"]["skipped_count"] == 2
    assert len(embeddings.calls[0]) == 2
    assert read_snapshot(settings)[2][0]["triplet"] == first


def test_restart_reuses_persistent_triplet_ids(client, settings, embeddings):
    assert client.post(ENDPOINT).status_code == 200
    with TestClient(create_app(settings)) as restarted:
        result = restarted.post(ENDPOINT)
    assert result.status_code == 200 and result.json()["data"]["added_count"] == 0
    assert len(embeddings.calls) == 1


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("use_router", [False, True])
def test_empty_input_skips_embedding_and_publication(client, settings, embeddings, existing, use_router):
    if existing:
        assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    if use_router:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
        write_router_source(settings, [])
    else:
        write_source(settings, [])
        write_router_source(settings, [triplet("ROUTER_A", "router relation", "ROUTER_B")])
    settings.embedding_api_base = ""
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "added_count": 0, "skipped_count": 0, "total_count": 2 if existing else 0, "added_triplet_ids": [],
    }
    assert len(embeddings.calls) == (1 if existing else 0) and snapshot(settings) == before
    if not existing:
        assert not (Path(settings.embedded_triplet) / "triplets").exists()
        assert response.json()["meta_data"][0]["dimension"] is None


@pytest.mark.parametrize("damage", ["missing", "singular_only", "empty_file", "invalid_json", "not_array", "invalid_triplet", "blank_predicate", "blank_description"])
def test_invalid_source_preserves_snapshot_and_never_uses_singular_filename(client, settings, embeddings, damage):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    source = Path(settings.rdf_triplet) / "triplets.json"
    if damage not in ("missing", "singular_only"):
        write_router_source(settings, [triplet("ROUTER_A", "router relation", "ROUTER_B")])
    if damage == "missing":
        source.unlink()
    elif damage == "singular_only":
        source.rename(source.with_name("triplet.json"))
    elif damage == "empty_file":
        source.write_text("")
    elif damage == "invalid_json":
        source.write_text("{invalid")
    elif damage == "not_array":
        source.write_text("{}")
    elif damage == "invalid_triplet":
        write_source(settings, [triplet("NEW", "refers to", "B"), ["A", "references", "B"]])
    else:
        value = triplet()
        if damage == "blank_predicate":
            value[1] = " \n"
        else:
            value[0]["description"] = " \t"
        write_source(settings, [value])
    response = client.post(ENDPOINT)
    assert response.status_code == 500 and response.json()["data"] is None
    assert len(embeddings.calls) == 1 and snapshot(settings) == before


@pytest.mark.parametrize("read_error", [PermissionError, IsADirectoryError, OSError])
def test_primary_read_errors_do_not_use_router_source(client, settings, embeddings, monkeypatch, read_error):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    write_router_source(settings, [triplet("ROUTER_A", "router relation", "ROUTER_B")])
    source = Path(settings.rdf_triplet) / "triplets.json"
    original_load = service_module.RdfTripletStore.load

    def fail_primary(store):
        if store.path == source:
            raise read_error("primary RDF read failed")
        return original_load(store)

    monkeypatch.setattr(service_module.RdfTripletStore, "load", fail_primary)
    response = client.post(ENDPOINT)
    assert response.status_code == 500 and "primary RDF read failed" in response.json()["msg"]
    assert response.json()["data"] is None
    assert len(embeddings.calls) == 1 and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["missing", "singular_only", "empty_file", "invalid_json", "not_array", "invalid_triplet"])
def test_invalid_router_source_preserves_existing_index(client, settings, embeddings, damage):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    (Path(settings.rdf_triplet) / "triplets.json").unlink()
    fallback = write_router_source(settings, [triplet("ROUTER_A", "router relation", "ROUTER_B")])
    if damage == "missing":
        fallback.unlink()
    elif damage == "singular_only":
        fallback.rename(fallback.with_name("triplet.json"))
    elif damage == "empty_file":
        fallback.write_text("")
    elif damage == "invalid_json":
        fallback.write_text("{invalid")
    elif damage == "not_array":
        fallback.write_text("{}")
    else:
        fallback.write_text('["invalid"]')
    response = client.post(ENDPOINT)
    assert response.status_code == 500 and response.json()["status"] == "error"
    assert response.json()["data"] is None and response.json()["meta_data"] == []
    assert len(embeddings.calls) == 1 and snapshot(settings) == before


@pytest.mark.parametrize("field,value", [
    ("rdf_triplet", ""), ("embedded_triplet", ""), ("embedding_api_base", ""),
    ("embedding_api_base", "invalid-url"), ("embedding_model_name", " "),
])
def test_invalid_settings_are_500_without_embedding(client, settings, embeddings, field, value):
    setattr(settings, field, value)
    response = client.post(ENDPOINT)
    assert response.status_code == 500
    assert response.json()["status"] == "error" and response.json()["data"] is None
    assert embeddings.calls == []


def test_api_failure_preserves_committed_snapshot(client, settings, embeddings):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    write_source(settings, [triplet("NEW", "refers to", "B")])
    embeddings.error = RuntimeError("embedding server unavailable")
    response = client.post(ENDPOINT)
    assert response.status_code == 502
    assert response.json()["meta_data"] == [] and "embedding server unavailable" in response.json()["msg"]
    assert snapshot(settings) == before


@pytest.mark.parametrize("vectors", [
    [[1.0, 2.0]], [[1.0], [1.0, 2.0]], [[0.0], [0.0]],
    [[float("nan")], [1.0]], [[float("inf")], [1.0]], [[], []],
    None, [1.0, 2.0], [["not a number"], ["not a number"]],
])
def test_bad_vectors_are_502_without_overwriting_index(client, settings, embeddings, vectors):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    write_source(settings, [triplet("D", "refers to", "E"), triplet("E", "refers to", "F")])
    embeddings.vectors = vectors
    response = client.post(ENDPOINT)
    assert response.status_code == 502 and snapshot(settings) == before


def test_dimension_conflict_keeps_previous_vectors(client, settings, embeddings):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    write_source(settings, [triplet("D", "refers to", "E")])
    embeddings.vectors = [[1.0, 2.0]]
    assert client.post(ENDPOINT).status_code == 409
    assert snapshot(settings) == before


def test_model_change_is_rejected_before_embedding_even_without_additions(client, settings, embeddings):
    assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    settings.embedding_model_name = "different-model"
    assert client.post(ENDPOINT).status_code == 409
    assert len(embeddings.calls) == 1 and snapshot(settings) == before


@pytest.mark.parametrize("damage", [
    "missing_file", "invalid_index", "wrong_index_type", "metadata_type", "wrong_count", "wrong_order",
    "wrong_id", "wrong_dimension", "wrong_metric", "raw_id_mismatch", "duplicate_id", "float_row",
])
def test_damaged_snapshots_are_not_overwritten(client, settings, embeddings, damage):
    assert client.post(ENDPOINT).status_code == 200
    directory = Path(settings.embedded_triplet) / "triplets"
    if damage == "missing_file":
        (directory / "raw_data.json").unlink()
    elif damage == "invalid_index":
        (directory / "index.faiss").write_bytes(b"invalid index")
    elif damage == "wrong_index_type":
        index = faiss.IndexFlatL2(3)
        index.add(np.ones((2, 3), dtype=np.float32))
        faiss.write_index(index, str(directory / "index.faiss"))
    else:
        metadata_path, raw_path = directory / "metadata.json", directory / "raw_data.json"
        metadata, raw = json.loads(metadata_path.read_text()), json.loads(raw_path.read_text())
        if damage == "metadata_type":
            metadata = []
        elif damage == "wrong_count":
            metadata["items"].pop()
        elif damage == "wrong_order":
            metadata["items"].reverse()
        elif damage == "wrong_id":
            metadata["items"][0]["triplet_id"] = "0" * 64
        elif damage == "wrong_dimension":
            metadata["dimension"] = 99
        elif damage == "wrong_metric":
            metadata["metric"] = "L2"
        elif damage == "raw_id_mismatch":
            raw[0]["triplet"][0]["table_name"] = "CHANGED"
        elif damage == "duplicate_id":
            raw[1] = copy.deepcopy(raw[0])
            metadata["items"][1]["triplet_id"] = raw[0]["triplet_id"]
        else:
            metadata["items"][0]["index"] = 0.0
        metadata_path.write_text(json.dumps(metadata))
        raw_path.write_text(json.dumps(raw))
    before = snapshot(settings)
    response = client.post(ENDPOINT)
    assert response.status_code == 409 and snapshot(settings) == before
    assert len(embeddings.calls) == 1


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["write", "publish"])
def test_failed_publication_restores_snapshot_and_cleans_temporary_files(client, settings, monkeypatch, existing, failure):
    if existing:
        assert client.post(ENDPOINT).status_code == 200
    before = snapshot(settings)
    write_source(settings, [triplet("NEW", "references", "B")])
    if failure == "write":
        original = TripletIndexStore._write_json

        def fail_raw(path, value):
            if path.name == "raw_data.json":
                raise OSError("simulated disk write failure")
            return original(path, value)

        monkeypatch.setattr(TripletIndexStore, "_write_json", staticmethod(fail_raw))
    else:
        original = Path.rename

        def fail_publish(path, target):
            if path.name.startswith(".triplet-staging-"):
                raise OSError("simulated publish failure")
            return original(path, target)

        monkeypatch.setattr(Path, "rename", fail_publish)
    assert client.post(ENDPOINT).status_code == 500
    assert snapshot(settings) == before
    assert not list(Path(settings.embedded_triplet).glob(".triplet-*"))


def test_concurrent_instances_do_not_double_embed_relations(settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = TripletIndexStore.locked

    @contextmanager
    def synchronized_lock(store):
        barrier.wait(timeout=10)
        with original(store):
            yield

    monkeypatch.setattr(TripletIndexStore, "locked", synchronized_lock)

    def request():
        with TestClient(create_app(settings)) as client:
            return client.post(ENDPOINT)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["data"]["added_count"] for response in responses) == [0, 2]
    assert len(embeddings.calls) == 1 and read_snapshot(settings)[0].ntotal == 2


@pytest.mark.parametrize("fail_embedding", [False, True])
def test_cancellation_waits_for_transaction_cleanup_and_releases_lock(settings, embeddings, fail_embedding):
    entered, release = threading.Event(), threading.Event()

    def wait_for_release():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release embedding request")

    embeddings.on_embed = wait_for_release
    if fail_embedding:
        embeddings.error = RuntimeError("cancelled upstream failure")

    async def run():
        service = IndexingTripletService(settings)
        task = asyncio.create_task(service.index_triplet())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert bool(snapshot(settings)) is not fail_embedding
        embeddings.error, embeddings.on_embed = None, None
        response = await service.index_triplet()
        assert response.data.added_count == (2 if fail_embedding else 0)
        assert response.data.total_count == 2

    asyncio.run(run())
    assert len(embeddings.calls) == (2 if fail_embedding else 1)


@pytest.mark.parametrize("use_router", [False, True])
def test_real_embedding_client_uses_configured_url_model_and_raw_triplet_text(settings, monkeypatch, use_router):
    if use_router:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
        write_router_source(settings, original_triplets())
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://embedding.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer dummy"
        payload = json.loads(request.content)
        assert payload["model"] == "BAAI/bge-m3"
        assert payload["input"] == [
            "subject_table_name: A\nsubject_description:   A 원본 설명\n\npredicate: references\n"
            "object_table_name: B\nobject_description:  B 원본 설명 ",
            "subject_table_name: B\nsubject_description:   B 원본 설명\n\npredicate: belongs to\n"
            "object_table_name: C\nobject_description:  C 원본 설명 ",
        ]
        return httpx.Response(200, json={
            "object": "list", "model": "BAAI/bge-m3",
            "data": [{"object": "embedding", "index": row, "embedding": [1.0, 2.0, 3.0]}
                     for row in range(len(payload["input"]))],
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport_client:
        monkeypatch.setattr(service_module, "create_embedding_client", lambda settings, **options:
                            create_embedding_client(settings, http_client=transport_client))
        with TestClient(create_app(settings)) as client:
            result = client.post(ENDPOINT)
    assert result.status_code == 200 and len(requests) == 1


@pytest.mark.parametrize("use_router", [False, True])
def test_dotenv_default_precedence_and_project_relative_paths(tmp_path, monkeypatch, embeddings, use_router):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        assert AppSettings().embedded_triplet == "./embedded_triplet"
        (tmp_path / ".env").write_text(
            "EMBEDDED_TRIPLET=./configured-index\nRDF_TRIPLET=./source\nRDF_TRIPLET_OPEN_ROUTER=./router-source\n"
            "EMBEDDING_API_BASE=https://file.test/v1\n",
        )
        settings = config.parse_args([])
        assert settings.embedded_triplet == "./configured-index"
        source = tmp_path / ("router-source" if use_router else "source")
        source.mkdir()
        (source / "triplets.json").write_text(json.dumps(original_triplets()))
        monkeypatch.chdir(tmp_path.parent)
        with TestClient(create_app(settings)) as client:
            result = client.post(ENDPOINT)
        assert result.status_code == 200
        assert (tmp_path / "configured-index/triplets/index.faiss").is_file()
        monkeypatch.setenv("EMBEDDED_TRIPLET", "./override-index")
        monkeypatch.setenv("EMBEDDING_API_BASE", "https://process.test/v1")
        overridden = AppSettings()
        assert overridden.embedded_triplet == "./override-index"
        assert overridden.embedding_api_base == "https://process.test/v1"


def test_triplet_id_is_stable_excludes_descriptions_and_avoids_delimiter_ambiguity():
    first = RDF_TRIPLETS.validate_python([triplet("주체|A", "contains", "B")])[0]
    changed = copy.deepcopy(first)
    changed[0].description = "새 설명"
    assert make_triplet_id(first) == make_triplet_id(changed)
    other = RDF_TRIPLETS.validate_python([triplet("주체", "A|contains", "B")])[0]
    assert make_triplet_id(first) != make_triplet_id(other)
    assert len(make_triplet_id(first)) == 64
    with pytest.raises(ValueError):
        IndexedTriplet(triplet_id="0" * 64, triplet=first)


def test_openapi_defines_bodyless_post_and_dedicated_response(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert "requestBody" not in operation
    for status in ("200", "409", "500", "502"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/IndexingTripletResponse")
    assert set(spec["components"]["schemas"]["IndexingTripletData"]["properties"]) == {
        "added_count", "skipped_count", "total_count", "added_triplet_ids",
    }
    assert len(spec["paths"]) == 25


@pytest.mark.parametrize("use_router", [False, True])
def test_indexing_triplet_logs_have_stage_counts_and_request_id(client, settings, caplog, use_router):
    source = Path(settings.rdf_triplet) / "triplets.json"
    if use_router:
        source.unlink()
        source = write_router_source(settings, original_triplets())
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if getattr(record, "operation", None) == "indexing_triplet"]
    assert records and len({record.request_id for record in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert {"triplet_load", "storage_lock", "index_load", "triplet_selection", "embedding", "index_save"} <= {r.stage for r in records}
    assert "added=2 skipped=0 total=2" in records[-1].getMessage()
    assert any(f"로드·검증 완료 source={source}" in record.getMessage() for record in records)
    assert any("reason=file_not_found" in record.getMessage() for record in records) is use_router
