import asyncio
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import shutil

import faiss
from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import AppSettings
from app.core.logging import LOGGER_NAME
from app.schemas.request import HybridOnlyBasedSchemaRequest, LlmBasedGenerateSqlRequest
from app.schemas.response import HybridOnlyBasedSchemaData, HybridOnlyBasedSchemaResponse
from app.schemas.triplet import TripletEntity
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import embedded_triplet_based_schema_service as embedding_module
from app.services import hybrid_only_based_schema_service as service_module
from app.services import triplet_based_schema_service as triplet_module
from app.services.embedding_client import create_embedding_client, normalize_embedding_vectors
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/hybrid_only_based_schema"
QUERY = "  최근 조건에 맞는 테이블을 찾아줘  "
NAMES = ("E", "C", "D", "B", "A", "ISOLATED", "UNINDEXED")
ENTRIES = [
    ("A", "self", "A", 0.95), ("A", "references", "B", 0.9),
    ("B", "reverse", "A", 0.85), ("ISOLATED", "self", "ISOLATED", 0.7),
    ("MISSING", "stale", "A", 0.8), ("UNKNOWN", "stale", "MISSING", 0.6),
    ("B", "references", "C", 0.5),
]
RELATIONS = [("B", "A"), ("C", "B"), ("D", "C"), ("E", "D"), ("B", "A"), ("A", "B"), ("B", "B")]
HISTORY = [
    {"role": "user" if i % 2 == 0 else "assistant", "content": f"  이전 메시지 {i}  "}
    for i in range(6)
]


def table(name):
    return {"table_name": name, "schema": f"  CREATE TABLE {name} (id INT);\n", "description": f" {name} 원본 설명 "}


def save_relations(settings, relations=RELATIONS, *, router=False):
    root = settings.rdf_triplet_open_router if router else settings.rdf_triplet
    path = Path(root) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        [{"table_name": subject, "description": "과거 설명"}, "references",
         {"table_name": target, "description": "과거 설명"}]
        for subject, target in relations
    ], ensure_ascii=False), encoding="utf-8")
    return path


def save_index(settings, entries=ENTRIES):
    records, values = [], []
    for subject, predicate, target, score in entries:
        triplet = (
            TripletEntity(table_name=subject, description="과거 설명"), predicate,
            TripletEntity(table_name=target, description="과거 설명"),
        )
        indexed = IndexedTriplet(triplet_id=make_triplet_id(triplet), triplet=triplet)
        records.append(indexed.model_dump(mode="json"))
        values.append([score, math.sqrt(max(0, 1 - score ** 2)), 0.0])
    index = faiss.IndexFlatIP(3)
    if values:
        index.add(normalize_embedding_vectors(values, len(values)))
    store = TripletIndexStore(Path(settings.embedded_triplet))
    with store.locked():
        store.save(index, records, settings.embedding_model_name)


def expected_data(names):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


def snapshot(settings):
    root = Path(settings.dataset_path).parent
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class FakeEmbeddings:
    def __init__(self):
        self.queries = []
        self.vector = [9.0, 0.0, 0.0]
        self.error = None
        self.hook = None

    def embed_query(self, text):
        self.queries.append(text)
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        return self.vector


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps([table(name) for name in NAMES], ensure_ascii=False), encoding="utf-8")
    settings = AppSettings(
        dataset_path=str(source), embedded_triplet=str(tmp_path / "embedded triplets"),
        rdf_triplet=str(tmp_path / "rdf"), embedding_api_base="https://embedding.test/v1",
        embedded_triplet_open_ai=str(tmp_path / "unused-open-ai-index"),
        rdf_triplet_open_ai=str(tmp_path / "unused-open-ai-rdf"),
        rdf_triplet_open_router=str(tmp_path / "router rdf"),
        llm_api_base="", model_name="", openai_api_key="",
        openrouter_api_key="", openrouter_llm_model_name="",
    )
    save_relations(settings)
    save_index(settings)
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(embedding_module, "create_embedding_client", lambda *args, **kwargs: fake)

    def reject_llm(*args, **kwargs):
        pytest.fail("Hybrid retrieval must not create an LLM client")

    monkeypatch.setattr(triplet_module, "create_llm_client", reject_llm)
    return fake


@pytest.fixture
def client(settings, embeddings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_returns_all_rdf_expanded_schemas_without_llm_or_storage_changes(client, settings, embeddings):
    before = snapshot(settings)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == expected_data(["C", "B", "A"])
    assert body["meta_data"] == [{
        "embedding_model": "BAAI/bge-m3", "top_k": 1, "seed_count": 1,
        "expanded_count": 3, "retrieved_count": 3, "graph_depth": 2, "unmapped_table_count": 2,
    }]
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.queries == [QUERY] and snapshot(settings) == before
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


def test_primary_rdf_takes_priority_over_router_rdf(client, settings, embeddings):
    save_relations(settings, [("A", "UNINDEXED")], router=True)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 2})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["C", "B", "A"])
    assert embeddings.queries == [QUERY] and snapshot(settings) == before


@pytest.mark.parametrize("missing_directory", [False, True])
def test_missing_primary_uses_router_rdf_with_local_index_and_catalog(client, settings, embeddings, missing_directory):
    source = Path(settings.rdf_triplet) / "triplets.json"
    source.unlink()
    if missing_directory:
        source.parent.rmdir()
    save_relations(settings, [("A", "UNINDEXED"), ("UNINDEXED", "D")], router=True)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 2})
    assert result.status_code == 200
    assert result.json()["data"] == expected_data(["D", "A", "UNINDEXED"])
    assert result.json()["meta_data"] == [{
        "embedding_model": "BAAI/bge-m3", "top_k": 1, "seed_count": 1,
        "expanded_count": 3, "retrieved_count": 3, "graph_depth": 2, "unmapped_table_count": 2,
    }]
    assert embeddings.queries == [QUERY] and snapshot(settings) == before
    assert not source.exists()
    assert not Path(settings.embedded_triplet_open_ai).exists()


def test_router_rdf_is_reloaded_and_restored_primary_takes_precedence(client, settings, embeddings):
    source = Path(settings.rdf_triplet) / "triplets.json"
    source.unlink()
    for relations, router, expected in [
        ([("A", "UNINDEXED")], True, ["A", "UNINDEXED"]),
        ([("A", "D")], True, ["D", "A"]),
        (RELATIONS, False, ["B", "A"]),
    ]:
        save_relations(settings, relations, router=router)
        before = snapshot(settings)
        result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 1})
        assert result.status_code == 200 and result.json()["data"] == expected_data(expected)
        assert snapshot(settings) == before
    assert embeddings.queries == [QUERY] * 3


@pytest.mark.parametrize("use_router", [False, True])
def test_empty_selected_rdf_returns_only_seeds(client, settings, embeddings, use_router):
    if use_router:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
        save_relations(settings, [], router=True)
    else:
        save_relations(settings, [])
        save_relations(settings, [("A", "UNINDEXED")], router=True)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 2})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["A"])
    metadata = result.json()["meta_data"][0]
    assert metadata["seed_count"] == metadata["expanded_count"] == metadata["retrieved_count"] == 1
    assert embeddings.queries == [QUERY] and snapshot(settings) == before


@pytest.mark.parametrize("missing_primary", [False, True])
def test_blank_router_setting_is_validated_only_when_needed(settings, embeddings, missing_primary):
    settings.rdf_triplet_open_router = ""
    if missing_primary:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
    before = snapshot(settings)
    with TestClient(create_app(settings)) as client:
        result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 2})
    assert snapshot(settings) == before
    if missing_primary:
        assert result.status_code == 500 and result.json()["data"] is None and embeddings.queries == []
    else:
        assert result.status_code == 200 and result.json()["data"] == expected_data(["C", "B", "A"])
        assert embeddings.queries == [QUERY]


@pytest.mark.parametrize("use_router", [False, True])
@pytest.mark.parametrize("read_error", [PermissionError, IsADirectoryError, OSError])
def test_rdf_read_errors_are_not_treated_as_missing(client, settings, embeddings, monkeypatch, use_router, read_error):
    primary = Path(settings.rdf_triplet) / "triplets.json"
    fallback = save_relations(settings, [("A", "UNINDEXED")], router=True)
    failing_source = fallback if use_router else primary
    if use_router:
        primary.unlink()
    original_load = service_module.RdfTripletStore.load

    def fail_selected(store):
        if store.path == failing_source:
            raise read_error("selected RDF read failed")
        return original_load(store)

    monkeypatch.setattr(service_module.RdfTripletStore, "load", fail_selected)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert result.status_code == 500 and "selected RDF read failed" in result.json()["msg"]
    assert result.json()["data"] is None and embeddings.queries == [] and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["empty_file", "invalid_json", "not_array", "invalid_triplet", "unknown_table"])
def test_invalid_router_rdf_is_rejected_before_embedding(client, settings, embeddings, damage):
    (Path(settings.rdf_triplet) / "triplets.json").unlink()
    fallback = save_relations(settings, [("A", "UNINDEXED")], router=True)
    if damage == "empty_file":
        fallback.write_text("")
    elif damage == "invalid_json":
        fallback.write_text("{invalid")
    elif damage == "not_array":
        fallback.write_text("{}")
    elif damage == "invalid_triplet":
        fallback.write_text('["invalid"]')
    else:
        save_relations(settings, [("UNKNOWN", "A")], router=True)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert result.status_code == 409 and result.json()["data"] is None
    assert embeddings.queries == [] and snapshot(settings) == before


def test_router_rdf_with_singular_filename_is_not_loaded(client, settings, embeddings):
    (Path(settings.rdf_triplet) / "triplets.json").unlink()
    fallback = save_relations(settings, [("A", "UNINDEXED")], router=True)
    fallback.rename(fallback.with_name("triplet.json"))
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1})
    assert result.status_code == 404 and result.json()["data"] is None
    assert embeddings.queries == [] and snapshot(settings) == before


def test_router_fallback_logs_both_paths_and_reason(client, settings, caplog):
    source = Path(settings.rdf_triplet) / "triplets.json"
    source.unlink()
    fallback = save_relations(settings, [("A", "UNINDEXED")], router=True)
    app_logger = logging.getLogger(LOGGER_NAME)
    app_logger.addHandler(caplog.handler)
    try:
        assert client.post(ENDPOINT, json={"query": QUERY, "top_k": 1}).status_code == 200
    finally:
        app_logger.removeHandler(caplog.handler)
    messages = [record.getMessage() for record in caplog.records
                if getattr(record, "operation", None) == "hybrid_only_based_schema"]
    assert any(f"source={source}" in message and f"fallback={fallback}" in message
               and "reason=file_not_found" in message for message in messages)
    assert any(f"카탈로그·관계 로드 완료 source={fallback}" in message for message in messages)


@pytest.mark.parametrize("top_k,names", [
    (1, ["A"]), (2, ["B", "A"]), (3, ["B", "A", "ISOLATED"]),
    (4, ["C", "B", "A", "ISOLATED"]), (100, ["C", "B", "A", "ISOLATED"]),
    (-1, ["C", "B", "A", "ISOLATED"]),
])
def test_top_k_selects_distinct_ranked_tables_and_minus_one_keeps_all(client, top_k, names):
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": top_k, "max_hop": 0})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    assert response.json()["meta_data"][0]["seed_count"] == len(names)


def test_request_defaults_select_all_indexed_candidates_and_expand_three_hops(client):
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data(["E", "C", "D", "B", "A", "ISOLATED"])
    metadata = response.json()["meta_data"][0]
    assert metadata["top_k"] == -1 and metadata["seed_count"] == 4 and metadata["graph_depth"] == 3


@pytest.mark.parametrize("max_hop,names,depth", [
    (0, ["A"], 0), (1, ["B", "A"], 1), (2, ["C", "B", "A"], 2),
    (3, ["C", "D", "B", "A"], 3), (4, ["E", "C", "D", "B", "A"], 4),
    (-1, ["E", "C", "D", "B", "A"], 10),
])
@pytest.mark.parametrize("use_router", [False, True])
def test_hop_limits_bidirectional_cycles_and_catalog_order(client, settings, max_hop, names, depth, use_router):
    if use_router:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
        save_relations(settings, router=True)
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": max_hop})
    assert response.status_code == 200 and response.json()["data"] == expected_data(names)
    meta = response.json()["meta_data"][0]
    assert meta["graph_depth"] == depth
    assert meta["expanded_count"] == meta["retrieved_count"] == len(names)


def test_auto_hop_does_not_reach_eleventh_hop(client, settings):
    names = [f"T{i}" for i in reversed(range(13))]
    Path(settings.dataset_path).write_text(json.dumps([table(name) for name in names]))
    save_index(settings, [("T0", "self", "T0", 1.0)])
    save_relations(settings, [(f"T{i + 1}", f"T{i}") for i in range(12)])
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": -1})
    assert response.status_code == 200
    assert response.json()["data"] == expected_data([f"T{i}" for i in reversed(range(11))])


@pytest.mark.parametrize("entries,expected", [
    ([("B", "tie", "A", 1.0)], ["A"]),
    ([("B", "low", "A", -0.8), ("D", "high", "C", -0.1)], ["C"]),
])
def test_ties_and_negative_scores_use_existing_triplet_ranking(client, settings, entries, expected):
    save_index(settings, entries)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 0})
    assert result.status_code == 200 and result.json()["data"] == expected_data(expected)


def test_expansion_reads_fresh_rdf_instead_of_embedded_relations(client, settings):
    save_relations(settings, [("UNINDEXED", "A")])
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 1})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["A", "UNINDEXED"])
    save_relations(settings, [])
    response = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 1})
    assert response.status_code == 200 and response.json()["data"] == expected_data(["A"])


def test_uses_only_recent_history_and_does_not_retain_it(client, embeddings):
    response = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY, "top_k": 1})
    assert response.status_code == 200
    expected = "\n\n".join([*(f"{m['role']}: {m['content']}" for m in HISTORY[-3:]), f"user: {QUERY}"])
    assert embeddings.queries == [expected]
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200
    assert embeddings.queries[-1] == QUERY


@pytest.mark.parametrize("use_router", [False, True])
def test_catalog_snapshot_is_shared_by_seed_mapping_and_expansion(client, settings, embeddings, use_router):
    if use_router:
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
        save_relations(settings, router=True)
    updated = [table(name) | {"schema": f"new DDL {name}"} for name in NAMES]
    embeddings.hook = lambda: Path(settings.dataset_path).write_text(json.dumps(updated))
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 0})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["A"])
    embeddings.hook = None
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 0})
    assert result.status_code == 200 and result.json()["data"]["schema"] == ["new DDL A"]


def test_releases_index_lock_before_embedding(client, settings, embeddings):
    def exclusive_lock():
        descriptor = os.open(settings.embedded_triplet, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)

    embeddings.hook = exclusive_lock
    assert client.post(ENDPOINT, json={"query": QUERY}).status_code == 200


@pytest.mark.parametrize("empty", ["index", "unmapped", "catalog"])
def test_no_seeds_returns_empty_data_without_embedding(client, settings, embeddings, empty):
    if empty == "index":
        save_index(settings, [])
    elif empty == "unmapped":
        save_index(settings, [("UNKNOWN", "self", "UNKNOWN", 1.0)])
    else:
        Path(settings.dataset_path).write_text("[]")
        save_relations(settings, [])
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 200 and result.json()["data"] == expected_data([])
    meta = result.json()["meta_data"][0]
    assert meta["seed_count"] == meta["expanded_count"] == meta["retrieved_count"] == 0
    assert embeddings.queries == []


@pytest.mark.parametrize("payload", [
    {}, {"query": " "}, {"query": None}, {"query": 1}, {"query": QUERY, "top_k": 0},
    {"query": QUERY, "top_k": -2}, {"query": QUERY, "top_k": True},
    {"query": QUERY, "top_k": "2"}, {"query": QUERY, "top_k": 2.0},
    {"query": QUERY, "max_hop": -2}, {"query": QUERY, "max_hop": True},
    {"query": QUERY, "max_hop": "2"}, {"query": QUERY, "max_hop": None},
    {"query": QUERY, "history": None}, {"query": QUERY, "schema": {}},
    {"query": QUERY, "history": [{"role": "system", "content": "invalid"}, *HISTORY[-3:]]},
    {"query": QUERY, "history": [{"role": "user", "content": " "}]},
])
def test_invalid_requests_are_rejected_before_search(client, embeddings, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert embeddings.queries == []


@pytest.mark.parametrize("missing", ["root", "snapshot", "rdf"])
def test_missing_inputs_return_404_without_creating_or_changing_files(client, settings, embeddings, missing):
    if missing == "rdf":
        (Path(settings.rdf_triplet) / "triplets.json").unlink()
    else:
        root = Path(settings.embedded_triplet)
        shutil.rmtree(root if missing == "root" else root / "triplets")
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 404 and result.json()["data"] is None
    assert ("/retrieve/generate_triplet" if missing == "rdf" else "/retrieve/indexing_triplet") in result.json()["msg"]
    assert embeddings.queries == [] and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["index", "metadata", "model", "rdf_json", "rdf_unknown"])
def test_incompatible_data_returns_409_before_embedding(client, settings, embeddings, damage):
    save_relations(settings, [("A", "UNINDEXED")], router=True)
    if damage == "index":
        (Path(settings.embedded_triplet) / "triplets/index.faiss").write_bytes(b"invalid")
    elif damage == "metadata":
        (Path(settings.embedded_triplet) / "triplets/raw_data.json").unlink()
    elif damage == "model":
        settings.embedding_model_name = "different-model"
    elif damage == "rdf_json":
        (Path(settings.rdf_triplet) / "triplets.json").write_text("not json")
    else:
        save_relations(settings, [("UNKNOWN", "A")])
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 409 and result.json()["data"] is None
    assert embeddings.queries == [] and snapshot(settings) == before


@pytest.mark.parametrize("vector,status", [
    ([1.0, 0.0], 409), ([0.0, 0.0, 0.0], 502), ([float("nan"), 0.0, 0.0], 502),
])
def test_bad_embedding_vectors_return_errors_without_partial_data(client, embeddings, vector, status):
    embeddings.vector = vector
    response = client.post(ENDPOINT, json={"query": QUERY})
    assert response.status_code == status and response.json()["data"] is None


def test_embedding_api_failure_returns_502(client, embeddings):
    embeddings.error = RuntimeError("embedding server unavailable")
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 502 and result.json()["data"] is None


@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_catalog_errors_do_not_use_router_rdf(client, settings, embeddings, damage):
    save_relations(settings, [("A", "UNINDEXED")], router=True)
    (Path(settings.rdf_triplet) / "triplets.json").unlink()
    catalog = Path(settings.dataset_path)
    if damage == "missing":
        catalog.unlink()
    else:
        catalog.write_text("{invalid")
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 500 and result.json()["data"] is None and embeddings.queries == []
    assert snapshot(settings) == before


def test_real_embedding_client_uses_configured_url_model_and_recent_context(settings):
    requests = []
    expected = HybridOnlyBasedSchemaRequest(query=QUERY, history=HISTORY).embedding_text()

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://embedding.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer dummy"
        body = json.loads(request.content)
        assert body["model"] == settings.embedding_model_name and body["input"] == [expected]
        return httpx.Response(200, json={
            "object": "list", "model": settings.embedding_model_name,
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        app = create_app(settings)
        app.state.services.hybrid_only_based_schema_service.client_factory = lambda settings, **kwargs: create_embedding_client(
            settings, http_client=http_client,
        )
        with TestClient(app) as client:
            result = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY, "top_k": 1, "max_hop": 0})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["A"])
    assert len(requests) == 1


def test_specific_request_response_and_openapi_models(client):
    operation = client.get("/openapi.json").json()["paths"][ENDPOINT]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/HybridOnlyBasedSchemaRequest")
    for status in ("200", "404", "409", "500", "502"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/HybridOnlyBasedSchemaResponse")
    models = client.get("/openapi.json").json()["components"]["schemas"]
    request = models["HybridOnlyBasedSchemaRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert request["properties"]["top_k"]["default"] == -1 and request["properties"]["max_hop"]["default"] == 3
    assert set(models["HybridOnlyBasedSchemaData"]["properties"]) == {"table_name", "schema", "description"}
    response = asyncio.run(client.app.state.services.hybrid_only_based_schema_service.hybrid_only_based_schema(
        HybridOnlyBasedSchemaRequest(query=QUERY),
    ))
    assert type(response) is HybridOnlyBasedSchemaResponse and type(response.data) is HybridOnlyBasedSchemaData
