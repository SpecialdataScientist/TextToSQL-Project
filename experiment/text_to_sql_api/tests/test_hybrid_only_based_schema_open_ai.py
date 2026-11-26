import asyncio
import json
import logging
import math
from pathlib import Path
import shutil

import faiss
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from app.core.config import AppSettings
from app.core.logging import LOGGER_NAME
from app.schemas.request import HybridOnlyBasedSchemaOpenAiRequest, LlmBasedGenerateSqlRequest
from app.schemas.response import HybridOnlyBasedSchemaOpenAiData, HybridOnlyBasedSchemaOpenAiResponse
from app.schemas.triplet import RDF_TRIPLETS
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services import embedded_triplet_based_schema_service as embedding_module
from app.services import hybrid_only_based_schema_service as service_module, open_ai_clients
from app.services.triplet_index_store import TripletIndexStore
from main import create_app


ENDPOINT = "/retrieve/hybrid_only_based_schema_open_ai"
FAKE_KEY = "test-openai-hybrid-key"
MODEL = "text-embedding-3-small"
QUERY = "  최근 조건에 맞는 테이블을 찾아줘  "
NAMES = ("E", "UNINDEXED", "B", "D", "A", "C")
ENTRIES = [
    ("B", "r0", "A", 0.8), ("C", "r1", "C", 0.95), ("B", "r2", "D", 0.7),
    ("C", "r3", "A", 0.9), ("C", "r4", "B", 0.85), ("C", "r5", "C", 0.92),
    ("D", "r6", "E", -0.4),
]
RELATIONS = [("B", "C"), ("A", "B"), ("D", "C"), ("E", "D"), ("B", "C"), ("C", "B"), ("C", "C")]
HISTORY = [{"role": "user" if i % 2 == 0 else "assistant", "content": f" 이전 메시지 {i} "} for i in range(6)]


def table(name):
    return {"table_name": name, "schema": f"  CREATE TABLE {name} (id INT);\n", "description": f" {name} 원본 설명 "}


def expected_data(names):
    return {key: [table(name)[key] for name in names] for key in ("table_name", "schema", "description")}


def save_index(settings, entries=ENTRIES, *, local=False):
    records, vectors = [], []
    for subject, predicate, target, score in entries:
        triplet = RDF_TRIPLETS.validate_python([[
            {"table_name": subject, "description": "과거 설명"}, predicate,
            {"table_name": target, "description": "과거 설명"},
        ]])[0]
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


def save_relations(settings, relations=RELATIONS, *, local=False, router=False):
    root = settings.rdf_triplet_open_router if router else settings.rdf_triplet if local else settings.rdf_triplet_open_ai
    path = Path(root) / "triplets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        [{"table_name": subject, "description": "과거 설명"}, "references",
         {"table_name": target, "description": "과거 설명"}]
        for subject, target in relations
    ]))


def snapshot(settings):
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in Path(settings.dataset_path).parent.rglob("*") if path.is_file()}


class FakeEmbeddings:
    def __init__(self):
        self.queries = []
        self.vector = [9.0, 0.0, 0.0]
        self.error = None

    def embed_query(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return self.vector


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "db_schema.json"
    source.write_text(json.dumps([table(name) for name in NAMES]))
    settings = AppSettings(
        dataset_path=str(source), embedded_triplet=str(tmp_path / "local index"),
        embedded_triplet_open_ai=str(tmp_path / "open ai index"),
        rdf_triplet=str(tmp_path / "local rdf"), rdf_triplet_open_ai=str(tmp_path / "open ai rdf"),
        rdf_triplet_open_router=str(tmp_path / "router rdf"),
        embedding_api_base="invalid-unused-local-url", llm_api_base="", model_name="",
        openai_api_key=FAKE_KEY, openai_embedding_model_name=MODEL, openai_llm_model_name="",
    )
    save_index(settings)
    save_relations(settings)
    return settings


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda *args, **kwargs: fake)

    def reject_client(*args, **kwargs):
        pytest.fail("OpenAI hybrid retrieval must only use the OpenAI embedding client")

    monkeypatch.setattr(embedding_module, "create_embedding_client", reject_client)
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", reject_client)
    return fake


@pytest.fixture
def client(settings, embeddings):
    with TestClient(create_app(settings)) as client:
        yield client


def invoke(settings, payload=None):
    with TestClient(create_app(settings)) as client:
        return client.post(ENDPOINT, json=payload if payload is not None else {"query": QUERY, "top_k": 1})


def test_open_ai_index_and_rdf_are_isolated_and_return_all_expanded_originals(client, settings, embeddings):
    save_index(settings, [("E", "local-only", "E", 1.0)], local=True)
    save_relations(settings, [("C", "UNINDEXED")], local=True)
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 1})
    assert result.status_code == 200
    body = result.json()
    assert body["data"] == expected_data(["B", "D", "C"])
    assert body["meta_data"] == [{
        "embedding_model": MODEL, "top_k": 1, "seed_count": 1, "expanded_count": 3,
        "retrieved_count": 3, "graph_depth": 1, "unmapped_table_count": 0,
    }]
    assert body["status"] == body["msg"] == "success" and body["elapsed_time"] >= 0
    assert embeddings.queries == [QUERY] and snapshot(settings) == before
    assert LlmBasedGenerateSqlRequest.model_validate({"query": QUERY, "schema": body["data"]})


@pytest.mark.parametrize("top_k,names", [
    (1, ["C"]), (2, ["A", "C"]), (3, ["B", "A", "C"]), (4, ["B", "D", "A", "C"]),
    (100, ["E", "B", "D", "A", "C"]), (-1, ["E", "B", "D", "A", "C"]),
])
def test_top_k_is_distinct_seed_limit_and_minus_one_is_unlimited(client, top_k, names):
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": top_k, "max_hop": 0})
    assert result.status_code == 200 and result.json()["data"] == expected_data(names)
    assert result.json()["meta_data"][0]["seed_count"] == len(names)


def test_defaults_are_unlimited_seeds_and_three_hops(client):
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["E", "B", "D", "A", "C"])
    meta = result.json()["meta_data"][0]
    assert meta["top_k"] == -1 and meta["seed_count"] == 5 and meta["graph_depth"] == 3


@pytest.mark.parametrize("max_hop,names,depth", [
    (0, ["C"], 0), (1, ["B", "D", "C"], 1), (2, ["E", "B", "D", "A", "C"], 2),
    (-1, ["E", "B", "D", "A", "C"], 10),
])
def test_hop_limits_bidirectional_cycles_and_catalog_order(client, max_hop, names, depth):
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": max_hop})
    assert result.status_code == 200 and result.json()["data"] == expected_data(names)
    meta = result.json()["meta_data"][0]
    assert meta["expanded_count"] == meta["retrieved_count"] == len(names) and meta["graph_depth"] == depth


def test_auto_mode_caps_expansion_at_ten_hops(client, settings):
    Path(settings.dataset_path).write_text(json.dumps([table(f"T{i}") for i in reversed(range(13))]))
    save_index(settings, [("T0", "self", "T0", 1.0)])
    save_relations(settings, [(f"T{i + 1}", f"T{i}") for i in range(12)])
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": -1})
    assert result.status_code == 200
    assert result.json()["data"] == expected_data([f"T{i}" for i in reversed(range(11))])


def test_graph_uses_current_open_ai_rdf_instead_of_indexed_relations(client, settings):
    save_relations(settings, [("C", "UNINDEXED")])
    result = client.post(ENDPOINT, json={"query": QUERY, "top_k": 1, "max_hop": 1})
    assert result.status_code == 200 and result.json()["data"] == expected_data(["UNINDEXED", "C"])


@pytest.mark.parametrize("empty", ["index", "unmapped", "catalog"])
def test_empty_results_skip_embedding(client, settings, embeddings, empty):
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


@pytest.mark.parametrize("field,value,expected", [
    ("openai_api_key", "", "OPENAI_API_KEY"),
    ("openai_embedding_model_name", " ", "OPENAI_EMBEDDING_MODEL_NAME"),
    ("embedded_triplet_open_ai", "", "nonempty"), ("rdf_triplet_open_ai", "", "nonempty"),
    *[(field, mode, expected) for field, expected in (
        ("embedded_triplet_open_ai", "EMBEDDED_TRIPLET_OPEN_AI"), ("rdf_triplet_open_ai", "RDF_TRIPLET_OPEN_AI"),
    ) for mode in ("same", "alias", "symlink")],
])
def test_settings_are_validated_before_storage_access(settings, embeddings, monkeypatch, field, value, expected):
    legacy = Path(settings.embedded_triplet if field == "embedded_triplet_open_ai" else settings.rdf_triplet)
    if value == "same":
        value = str(legacy)
    elif value == "alias":
        value = str(legacy / ".." / legacy.name)
    elif value == "symlink":
        legacy.mkdir(exist_ok=True)
        alias = legacy.with_name("legacy alias")
        alias.symlink_to(legacy, target_is_directory=True)
        value = str(alias)
    setattr(settings, field, value)

    def reject_search(*args):
        pytest.fail("Invalid OpenAI configuration must fail before the search worker")

    monkeypatch.setattr(service_module.HybridOnlyBasedSchemaService, "_search_schema", reject_search)
    result = invoke(settings)
    assert result.status_code == 500 and expected in result.json()["msg"] and embeddings.queries == []


@pytest.mark.parametrize("missing", ["root", "snapshot", "rdf"])
def test_missing_open_ai_data_does_not_fall_back_to_local_or_router_files(client, settings, embeddings, missing):
    save_index(settings, local=True)
    save_relations(settings, local=True)
    save_relations(settings, [("C", "UNINDEXED")], router=True)
    if missing == "rdf":
        (Path(settings.rdf_triplet_open_ai) / "triplets.json").unlink()
    else:
        root = Path(settings.embedded_triplet_open_ai)
        shutil.rmtree(root if missing == "root" else root / "triplets")
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 404 and result.json()["data"] is None
    assert ("/retrieve/generate_triplet_open_ai" if missing == "rdf" else "/retrieve/indexing_triplet_open_ai") in result.json()["msg"]
    assert embeddings.queries == [] and snapshot(settings) == before


@pytest.mark.parametrize("damage", ["model", "index", "rdf_json", "rdf_unknown"])
def test_conflicts_return_409_without_embedding_or_writes(client, settings, embeddings, damage):
    if damage == "model":
        client.app.state.services.hybrid_only_based_schema_open_ai_service.settings.embedding_model_name = "different-model"
    elif damage == "index":
        (Path(settings.embedded_triplet_open_ai) / "triplets/index.faiss").write_bytes(b"invalid")
    elif damage == "rdf_json":
        (Path(settings.rdf_triplet_open_ai) / "triplets.json").write_text("not JSON")
    else:
        save_relations(settings, [("MISSING", "C")])
    before = snapshot(settings)
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == 409 and result.json()["data"] is None
    assert embeddings.queries == [] and snapshot(settings) == before


@pytest.mark.parametrize("vector,status", [([1.0, 0.0], 409), ([0.0, 0.0, 0.0], 502)])
def test_invalid_embedding_results_return_no_partial_data(client, embeddings, vector, status):
    embeddings.vector = vector
    result = client.post(ENDPOINT, json={"query": QUERY})
    assert result.status_code == status and result.json()["data"] is None


@pytest.mark.parametrize("phase", ["embedding", "factory", "snapshot"])
def test_error_responses_and_formatted_logs_redact_key(settings, embeddings, monkeypatch, caplog, phase):
    def fail(*args, **kwargs):
        try:
            raise ValueError(f"inner error containing {FAKE_KEY}")
        except ValueError as error:
            raise RuntimeError(f"outer error containing {FAKE_KEY}") from error

    if phase == "embedding":
        embeddings.error = RuntimeError(f"OpenAI rejected key {FAKE_KEY}")
    elif phase == "factory":
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", fail)
    else:
        monkeypatch.setattr(service_module, "load_triplet_snapshot", fail)
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(caplog.handler)
    try:
        result = invoke(settings)
        assert result.status_code == (502 if phase == "embedding" else 500)
        assert FAKE_KEY not in result.text and "[REDACTED]" in result.json()["msg"]
        assert result.json()["data"] is None and result.json()["meta_data"] == []
        assert FAKE_KEY not in caplog.text
        console = next(handler for handler in logger.handlers if handler.get_name() == "text_to_sql_api.console")
        assert all(FAKE_KEY not in console.formatter.format(record) for record in caplog.records)
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.parametrize("status", [200, 401, 429])
@pytest.mark.parametrize("include_history", [False, True])
def test_real_open_ai_embedding_client_uses_official_url_auth_model_and_context(settings, monkeypatch, status, include_history):
    monkeypatch.setenv("OPENAI_API_KEY", "unused-global-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.test/v1")
    requests = []
    payload = {"query": QUERY, "top_k": 1, "max_hop": 0}
    expected = QUERY
    if include_history:
        payload["history"] = HISTORY
        expected = "\n\n".join([*(f"{m['role']}: {m['content']}" for m in HISTORY[-3:]), f"user: {QUERY}"])

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.openai.com/v1/embeddings"
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["input"] == [expected]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"error with {FAKE_KEY}", "type": "mock_error"}})
        return httpx.Response(200, json={
            "object": "list", "model": MODEL,
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })

    client_factory = open_ai_clients.create_open_ai_embedding_client
    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        monkeypatch.setattr(open_ai_clients, "create_open_ai_embedding_client", lambda profile, **options:
                            client_factory(profile, http_client=http_client, max_retries=0))
        result = invoke(settings, payload)
    assert len(requests) == 1 and FAKE_KEY not in result.text
    if status == 200:
        assert result.status_code == 200 and result.json()["data"] == expected_data(["C"])
    else:
        assert result.status_code == 502 and result.json()["data"] is None


def test_openapi_and_service_use_dedicated_models(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ENDPOINT]["post"]
    assert len(spec["paths"]) == 25 and sum(path.endswith("_open_ai") for path in spec["paths"]) == 10
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/HybridOnlyBasedSchemaOpenAiRequest")
    for status in ("200", "404", "409", "500", "502"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/HybridOnlyBasedSchemaOpenAiResponse")
    request = spec["components"]["schemas"]["HybridOnlyBasedSchemaOpenAiRequest"]
    assert request["required"] == ["query"] and request["additionalProperties"] is False
    assert request["properties"]["top_k"]["default"] == -1 and request["properties"]["max_hop"]["default"] == 3
    result = asyncio.run(client.app.state.services.hybrid_only_based_schema_open_ai_service.hybrid_only_based_schema(
        HybridOnlyBasedSchemaOpenAiRequest(query=QUERY),
    ))
    assert type(result) is HybridOnlyBasedSchemaOpenAiResponse and type(result.data) is HybridOnlyBasedSchemaOpenAiData


@pytest.mark.parametrize("field,value", [("top_k", 0), ("top_k", True), ("max_hop", -2), ("max_hop", "2"), ("history", None)])
def test_invalid_api_requests_are_422_without_embedding(client, embeddings, field, value):
    result = client.post(ENDPOINT, json={"query": QUERY, field: value})
    assert result.status_code == 422 and embeddings.queries == []


def test_operation_logs_identify_open_ai_search(client, caplog):
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(caplog.handler)
    try:
        result = client.post(ENDPOINT, json={"query": QUERY, "history": HISTORY, "top_k": 1, "max_hop": 1})
        assert result.status_code == 200
    finally:
        logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if getattr(record, "operation", None) == "hybrid_only_based_schema_open_ai"]
    assert records and len({record.request_id for record in records}) == 1
    assert records[0].stage == "request" and records[-1].stage == "complete"
    assert "history_count=6 used_history_count=3" in records[0].getMessage()
    assert "seed_count=1 expanded_count=3 retrieved_count=3" in records[-1].getMessage()
