import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
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
from app.core.config import AppSettings, OPENAI_API_BASE
from app.core.logging import LOGGER_NAME
from app.schemas.triplet import RDF_TRIPLETS
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import indexing_triplet_service as service_module, open_ai_clients
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/indexing_triplet_open_ai"
FAKE_KEY = "test-openai-triplet-embedding-key"
TRIPLETS = [
    [{"table_name": "A", "description": "  A 원본 설명\n"}, "references",
     {"table_name": "B", "description": " B 원본 설명 "}],
    [{"table_name": "B", "description": "  B 원본 설명\n"}, "belongs to",
     {"table_name": "C", "description": " C 원본 설명 "}],
]
LOCAL_TRIPLETS = [
    [{"table_name": "LOCAL_A", "description": "로컬 A"}, "local_only",
     {"table_name": "LOCAL_B", "description": "로컬 B"}],
]


def write_source(settings, values):
    path = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return path


def records(values=TRIPLETS):
    return [
        IndexedTriplet(triplet_id=make_triplet_id(value), triplet=value).model_dump(mode="json")
        for value in RDF_TRIPLETS.validate_python(values)
    ]


def snapshot(directory):
    root = Path(directory) / "triplets"
    return {path.name: path.read_bytes() for path in root.iterdir()} if root.exists() else {}


def read_snapshot(directory):
    root = Path(directory) / "triplets"
    return (
        faiss.read_index(str(root / "index.faiss")),
        json.loads((root / "metadata.json").read_text()),
        json.loads((root / "raw_data.json").read_text()),
    )


def seed_legacy(settings):
    store = TripletIndexStore(Path(settings.embedded_triplet))
    index = faiss.IndexFlatIP(3)
    index.add(np.asarray([[1, 0, 0], [0, 1, 0]], dtype=np.float32))
    with store.locked():
        store.save(index, records(), settings.embedding_model_name)
    return snapshot(settings.embedded_triplet)


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
        return [[1.0, float(len(text)), 2.0, 3.0] for text in texts]


@pytest.fixture
def settings(tmp_path):
    settings = AppSettings(
        rdf_triplet=str(tmp_path / "local RDF"), rdf_triplet_open_ai=str(tmp_path / "open ai RDF"),
        rdf_triplet_open_router=str(tmp_path / "router RDF"),
        embedded_triplet=str(tmp_path / "legacy index"),
        embedded_triplet_open_ai=str(tmp_path / "open ai index"),
        dataset_path=str(tmp_path / "unused-schema.json"), openai_api_key=FAKE_KEY,
        openai_embedding_model_name="text-embedding-3-small", embedding_api_base="invalid-local-url",
        llm_api_base="", openai_llm_model_name="",
    )
    write_source(settings, TRIPLETS)
    other = Path(settings.rdf_triplet) / "triplets.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps(LOCAL_TRIPLETS))
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda *args, **kwargs: fake)

    def reject_local_client(*args, **kwargs):
        raise AssertionError("OpenAI indexing must use the OpenAI embedding client")

    monkeypatch.setattr(service_module, "create_embedding_client", reject_local_client)
    return fake


def invoke(settings):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT)


def test_indexes_open_ai_rdf_into_independent_open_ai_store(settings, embeddings):
    legacy_before = seed_legacy(settings)
    source = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    source_before = source.read_bytes()
    local_source = Path(settings.rdf_triplet) / "triplets.json"
    local_before = local_source.read_bytes()
    response = invoke(settings)
    assert response.status_code == 200
    body = response.json()
    ids = [record["triplet_id"] for record in records()]
    assert body["data"] == {"added_count": 2, "skipped_count": 0, "total_count": 2, "added_triplet_ids": ids}
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.calls == [[
        "subject_table_name: A\nsubject_description:   A 원본 설명\n\npredicate: references\n"
        "object_table_name: B\nobject_description:  B 원본 설명 ",
        "subject_table_name: B\nsubject_description:   B 원본 설명\n\npredicate: belongs to\n"
        "object_table_name: C\nobject_description:  C 원본 설명 ",
    ]]
    index, metadata, raw = read_snapshot(settings.embedded_triplet_open_ai)
    assert index.d == 4 and index.ntotal == 2 and index.metric_type == faiss.METRIC_INNER_PRODUCT
    np.testing.assert_allclose(np.linalg.norm(index.reconstruct_n(), axis=1), 1.0, atol=1e-6)
    assert metadata["embedding_model"] == "text-embedding-3-small" and raw == records()
    assert body["meta_data"] == [{
        "embedding_model": "text-embedding-3-small", "dimension": 4,
        "index_directory": str(Path(settings.embedded_triplet_open_ai) / "triplets"),
    }]
    assert snapshot(settings.embedded_triplet) == legacy_before
    assert source.read_bytes() == source_before and not Path(settings.dataset_path).exists()
    assert local_source.read_bytes() == local_before


@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_open_ai_indexing_does_not_require_valid_local_rdf(settings, embeddings, damage):
    local_source = Path(settings.rdf_triplet) / "triplets.json"
    if damage == "missing":
        local_source.unlink()
    else:
        local_source.write_text("{invalid")
    response = invoke(settings)
    assert response.status_code == 200 and response.json()["data"]["added_count"] == 2
    assert read_snapshot(settings.embedded_triplet_open_ai)[2] == records()


def test_repeat_and_restart_skip_embedding_and_writing(settings, embeddings, monkeypatch):
    assert invoke(settings).status_code == 200
    before = snapshot(settings.embedded_triplet_open_ai)

    def reject_save(*args):
        pytest.fail("A repeated request must not rewrite the snapshot")

    monkeypatch.setattr(TripletIndexStore, "save", reject_save)
    result = invoke(settings)
    assert result.status_code == 200
    assert result.json()["data"] == {"added_count": 0, "skipped_count": 2, "total_count": 2, "added_triplet_ids": []}
    assert len(embeddings.calls) == 1 and snapshot(settings.embedded_triplet_open_ai) == before


def test_append_deduplicates_and_preserves_changed_and_removed_relations(settings, embeddings):
    legacy_before = seed_legacy(settings)
    assert invoke(settings).status_code == 200
    previous_index, _, previous_raw = read_snapshot(settings.embedded_triplet_open_ai)
    changed = copy.deepcopy(TRIPLETS[0])
    changed[0]["description"] = "변경된 설명"
    addition = copy.deepcopy(TRIPLETS[0])
    addition[1] = "owns"
    write_source(settings, [changed, addition, addition])
    result = invoke(settings)
    assert result.status_code == 200
    assert result.json()["data"]["added_count"] == 1
    assert result.json()["data"]["skipped_count"] == 2 and result.json()["data"]["total_count"] == 3
    index, _, raw = read_snapshot(settings.embedded_triplet_open_ai)
    np.testing.assert_array_equal(index.reconstruct_n(0, 2), previous_index.reconstruct_n())
    assert raw == previous_raw + records([addition])
    assert len(embeddings.calls[-1]) == 1 and "변경된" not in embeddings.calls[-1][0]
    assert snapshot(settings.embedded_triplet) == legacy_before


@pytest.mark.parametrize("existing", [False, True])
def test_empty_source_skips_embedding_and_publication(settings, embeddings, existing):
    if existing:
        assert invoke(settings).status_code == 200
    before = snapshot(settings.embedded_triplet_open_ai)
    write_source(settings, [])
    result = invoke(settings)
    assert result.status_code == 200
    assert result.json()["data"]["total_count"] == (2 if existing else 0)
    assert result.json()["data"]["added_count"] == 0
    assert len(embeddings.calls) == (1 if existing else 0)
    assert snapshot(settings.embedded_triplet_open_ai) == before


@pytest.mark.parametrize("field,value,expected", [
    ("openai_api_key", " ", "OPENAI_API_KEY"),
    ("openai_embedding_model_name", " ", "OPENAI_EMBEDDING_MODEL_NAME"),
    ("embedded_triplet_open_ai", " ", "path"),
    ("embedded_triplet_open_ai", "same", "EMBEDDED_TRIPLET_OPEN_AI"),
    ("embedded_triplet_open_ai", "alias", "EMBEDDED_TRIPLET_OPEN_AI"),
    ("embedded_triplet_open_ai", "symlink", "EMBEDDED_TRIPLET_OPEN_AI"),
])
def test_invalid_settings_are_checked_before_filesystem_work(settings, embeddings, monkeypatch, field, value, expected):
    if value == "same":
        value = settings.embedded_triplet
    elif value == "alias":
        value = settings.embedded_triplet + "/../legacy index"
    elif value == "symlink":
        Path(settings.embedded_triplet).mkdir()
        alias = Path(settings.embedded_triplet).with_name("symlink index")
        alias.symlink_to(settings.embedded_triplet, target_is_directory=True)
        value = str(alias)
    setattr(settings, field, value)

    def reject_index(*args):
        raise AssertionError("Configuration must be validated before any indexing work")

    monkeypatch.setattr(service_module.IndexingTripletService, "_index_triplet", reject_index)
    response = invoke(settings)
    assert response.status_code == 500 and expected in response.json()["msg"]
    assert embeddings.calls == []


@pytest.mark.parametrize("existing", [False, True])
def test_key_is_required_even_for_empty_or_unchanged_input(settings, embeddings, existing):
    if existing:
        assert invoke(settings).status_code == 200
    else:
        write_source(settings, [])
    before = snapshot(settings.embedded_triplet_open_ai)
    settings.openai_api_key = ""
    result = invoke(settings)
    assert result.status_code == 500 and "OPENAI_API_KEY" in result.json()["msg"]
    assert len(embeddings.calls) == (1 if existing else 0)
    assert snapshot(settings.embedded_triplet_open_ai) == before


@pytest.mark.parametrize("damage", ["missing", "malformed", "invalid_triplet"])
def test_bad_open_ai_rdf_never_falls_back_to_local_or_router_rdf(settings, embeddings, damage):
    router_source = Path(settings.rdf_triplet_open_router) / "triplets.json"
    router_source.parent.mkdir()
    router_source.write_text(json.dumps(LOCAL_TRIPLETS))
    router_before = router_source.read_bytes()
    source = Path(settings.rdf_triplet_open_ai) / "triplets.json"
    if damage == "missing":
        source.unlink()
    elif damage == "malformed":
        source.write_text("{invalid")
    else:
        source.write_text('["invalid"]')
    response = invoke(settings)
    assert response.status_code == 500 and embeddings.calls == []
    assert not Path(settings.embedded_triplet_open_ai).exists()
    assert router_source.read_bytes() == router_before


@pytest.mark.parametrize("damage", ["model", "dimension", "metadata"])
def test_incompatible_open_ai_index_is_409_without_replacement(settings, embeddings, damage):
    assert invoke(settings).status_code == 200
    if damage == "model":
        settings.openai_embedding_model_name = "different-model"
    elif damage == "dimension":
        addition = copy.deepcopy(TRIPLETS[0])
        addition[1] = "owns"
        write_source(settings, [addition])
        embeddings.vectors = [[1.0, 2.0, 3.0]]
    else:
        path = Path(settings.embedded_triplet_open_ai) / "triplets/metadata.json"
        metadata = json.loads(path.read_text())
        metadata["items"][0]["triplet_id"] = "0" * 64
        path.write_text(json.dumps(metadata))
    before = snapshot(settings.embedded_triplet_open_ai)
    result = invoke(settings)
    assert result.status_code == 409 and snapshot(settings.embedded_triplet_open_ai) == before
    assert len(embeddings.calls) == (2 if damage == "dimension" else 1)


@pytest.mark.parametrize("vectors", [[[0.0], [0.0]], [[1.0, 2.0]], [[float("nan")], [1.0]]])
def test_bad_open_ai_vectors_are_502_and_preserve_both_stores(settings, embeddings, vectors):
    legacy_before = seed_legacy(settings)
    embeddings.vectors = vectors
    result = invoke(settings)
    assert result.status_code == 502 and result.json()["data"] is None
    assert snapshot(settings.embedded_triplet) == legacy_before
    assert snapshot(settings.embedded_triplet_open_ai) == {}


@pytest.mark.parametrize("phase", ["embedding", "save"])
def test_errors_mask_key_in_response_and_formatted_exception_chains(settings, embeddings, monkeypatch, caplog, phase):
    if phase == "embedding":
        embeddings.error = RuntimeError(f"OpenAI rejected credential {FAKE_KEY}")
    else:
        def fail_save(*args):
            raise OSError(f"save failure: {FAKE_KEY}")
        monkeypatch.setattr(TripletIndexStore, "save", fail_save)
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        response = invoke(settings)
        assert response.status_code == (502 if phase == "embedding" else 500)
        assert FAKE_KEY not in response.text and "[REDACTED]" in response.json()["msg"]
        assert FAKE_KEY not in caplog.text
        console = next(h for h in app_logger.handlers if h.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        app_logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("failure", ["write", "publish"])
def test_failed_save_preserves_existing_open_ai_and_legacy_snapshots(settings, embeddings, monkeypatch, failure):
    legacy_before = seed_legacy(settings)
    assert invoke(settings).status_code == 200
    before = snapshot(settings.embedded_triplet_open_ai)
    addition = copy.deepcopy(TRIPLETS[0])
    addition[1] = "owns"
    write_source(settings, [addition])
    if failure == "write":
        original = TripletIndexStore._write_json

        def fail_write(path, value):
            if path.name == "raw_data.json":
                raise OSError("disk write failure")
            return original(path, value)

        monkeypatch.setattr(TripletIndexStore, "_write_json", staticmethod(fail_write))
    else:
        original = Path.rename

        def fail_publish(path, target):
            if path.name.startswith(".triplet-staging-"):
                raise OSError("publish failure")
            return original(path, target)

        monkeypatch.setattr(Path, "rename", fail_publish)
    assert invoke(settings).status_code == 500
    assert snapshot(settings.embedded_triplet_open_ai) == before
    assert snapshot(settings.embedded_triplet) == legacy_before
    assert not list(Path(settings.embedded_triplet_open_ai).glob(".triplet-*"))


def test_concurrent_open_ai_indexers_do_not_embed_the_same_relation_twice(settings, embeddings, monkeypatch):
    barrier = threading.Barrier(2)
    original = TripletIndexStore.locked

    @contextmanager
    def synchronized_lock(store):
        barrier.wait(timeout=10)
        with original(store):
            yield

    monkeypatch.setattr(TripletIndexStore, "locked", synchronized_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke, settings) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["data"]["added_count"] for response in responses) == [0, 2]
    assert len(embeddings.calls) == 1 and read_snapshot(settings.embedded_triplet_open_ai)[0].ntotal == 2


@pytest.mark.parametrize("fail_embedding", [False, True])
def test_cancelled_request_finishes_work_and_releases_open_ai_store_lock(settings, embeddings, fail_embedding):
    entered, release = threading.Event(), threading.Event()

    def wait_for_release():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release embedding request")

    embeddings.on_embed = wait_for_release
    if fail_embedding:
        embeddings.error = RuntimeError("upstream failed while cancelling")
    service = create_app(settings).state.services.indexing_triplet_open_ai_service

    async def run():
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
        embeddings.error, embeddings.on_embed = None, None
        result = await service.index_triplet()
        assert result.data.added_count == (2 if fail_embedding else 0)
        assert result.data.total_count == 2

    asyncio.run(run())
    assert len(embeddings.calls) == (2 if fail_embedding else 1)


@pytest.mark.parametrize("status", [200, 401, 429])
def test_real_sdk_uses_official_embedding_url_key_model_and_raw_text(settings, monkeypatch, status):
    factory = open_ai_clients.create_open_ai_embedding_client
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-global-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == OPENAI_API_BASE + "/embeddings"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-3-small"
        assert body["input"] == [
            "subject_table_name: A\nsubject_description:   A 원본 설명\n\npredicate: references\n"
            "object_table_name: B\nobject_description:  B 원본 설명 ",
            "subject_table_name: B\nsubject_description:   B 원본 설명\n\npredicate: belongs to\n"
            "object_table_name: C\nobject_description:  C 원본 설명 ",
        ]
        if status != 200:
            return httpx.Response(status, json={"error": {
                "message": f"Rejected key {FAKE_KEY}", "type": "invalid_request_error", "code": "test",
            }})
        return httpx.Response(200, json={
            "object": "list", "model": "text-embedding-3-small",
            "data": [{"object": "embedding", "index": i, "embedding": [1.0, 2.0, 3.0, 4.0]}
                     for i in range(len(body["input"]))],
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda profile, **options:
                            factory(profile, http_client=http_client, max_retries=0))
        response = invoke(settings)
    assert len(requests) == 1 and FAKE_KEY not in response.text
    if status == 200:
        assert response.status_code == 200 and response.json()["data"]["added_count"] == 2
    else:
        assert response.status_code == 502 and snapshot(settings.embedded_triplet_open_ai) == {}


def test_dotenv_default_precedence_and_project_relative_storage(tmp_path, monkeypatch, embeddings):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        assert AppSettings().embedded_triplet_open_ai == "./embedded_triplet_open_ai"
        (tmp_path / ".env").write_text(
            f"OPENAI_API_KEY={FAKE_KEY}\nOPENAI_EMBEDDING_MODEL_NAME=text-embedding-3-small\n"
            "EMBEDDED_TRIPLET_OPEN_AI=./open-ai-index\nEMBEDDED_TRIPLET=./local-index\n"
            "RDF_TRIPLET=./local-rdf\nRDF_TRIPLET_OPEN_AI=./open-ai-rdf\n",
        )
        settings = config.parse_args([])
        assert settings.embedded_triplet_open_ai == "./open-ai-index"
        assert settings.rdf_triplet_open_ai == "./open-ai-rdf"
        source = tmp_path / "open-ai-rdf"
        source.mkdir()
        (source / "triplets.json").write_text(json.dumps(TRIPLETS))
        monkeypatch.chdir(tmp_path.parent)
        assert invoke(settings).status_code == 200
        assert (tmp_path / "open-ai-index/triplets/index.faiss").is_file()
        assert not (tmp_path / "local-index").exists()
        assert not (tmp_path / "local-rdf").exists()
        monkeypatch.setenv("EMBEDDED_TRIPLET_OPEN_AI", "./override")
        assert AppSettings().embedded_triplet_open_ai == "./override"
        monkeypatch.setenv("RDF_TRIPLET_OPEN_AI", "./override-rdf")
        assert AppSettings().rdf_triplet_open_ai == "./override-rdf"


def test_local_indexing_works_without_key_and_openapi_contracts_match(settings, monkeypatch):
    settings.openai_api_key = ""
    settings.embedding_api_base = "https://local.test/v1"
    fake = FakeEmbeddings()
    monkeypatch.setattr(service_module, "create_embedding_client", lambda *args, **kwargs: fake)
    with TestClient(create_app(settings)) as client:
        response = client.post("/retrieve/indexing_triplet")
        assert response.status_code == 200 and response.json()["data"]["added_count"] == 1
        assert read_snapshot(settings.embedded_triplet)[2] == records(LOCAL_TRIPLETS)
        spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert "RDF_TRIPLET_OPEN_AI/triplets.json" in operation["description"]
    assert "requestBody" not in operation
    assert operation["responses"] == spec["paths"]["/retrieve/indexing_triplet"]["post"]["responses"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/IndexingTripletResponse")
    assert len(spec["paths"]) == 25
    assert len(fake.calls) == 1 and not Path(settings.embedded_triplet_open_ai).exists()


def test_open_ai_indexing_logs_use_distinct_operation_and_request_id(settings, embeddings, caplog):
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert invoke(settings).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    records = [r for r in caplog.records if getattr(r, "operation", None) == "indexing_triplet_open_ai"]
    assert records and len({r.request_id for r in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert "added=2 skipped=0 total=2" in records[-1].getMessage()
