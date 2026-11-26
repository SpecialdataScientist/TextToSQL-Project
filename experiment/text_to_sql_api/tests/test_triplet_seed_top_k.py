import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import AppSettings
from app.prompts.triplet_based_schema_prompt import SEED_SELECTION_INSTRUCTIONS
from app.services import open_ai_clients, triplet_based_schema_service as service_module
from main import create_app


TABLES = [
    {"table_name": name, "schema": f"CREATE TABLE {name} (id INTEGER);", "description": name}
    for name in ("ISOLATED", "C", "B", "A", "UNRELATED")
]
ENDPOINT_CASES = [
    ("/retrieve/triplet_based_schema", True),
    ("/retrieve/triplet_based_schema_open_ai", True),
    ("/retrieve/only_triplet_based_schema", False),
    ("/retrieve/only_triplet_based_schema_open_ai", False),
]


def selection(names):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, parsed={"tables": [{"table_name": name, "reason": "질의에 필요"} for name in names]},
        ))], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.results = [selection(["A"]), selection(["A"])]

        async def parse(**kwargs):
            result = self.results[len(self.calls)]
            self.calls.append(kwargs)
            return result

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture(params=ENDPOINT_CASES, ids=[endpoint.rsplit("/", 1)[-1] for endpoint, _ in ENDPOINT_CASES])
def api(request, tmp_path, monkeypatch):
    endpoint, has_final_selection = request.param
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(TABLES), encoding="utf-8")
    settings = AppSettings(
        dataset_path=str(source), rdf_triplet=str(tmp_path / "rdf"), rdf_triplet_open_ai=str(tmp_path / "open-ai-rdf"),
        llm_api_base="https://llm.test/v1", model_name="local-model", openai_api_key="test-seed-key",
        openai_llm_model_name="gpt-6-luna",
    )
    relations = [
        [{"table_name": subject, "description": subject}, "references", {"table_name": target, "description": target}]
        for subject, target in (("B", "A"), ("C", "B"))
    ]
    for directory in (settings.rdf_triplet, settings.rdf_triplet_open_ai):
        path = Path(directory) / "triplets.json"
        path.parent.mkdir()
        path.write_text(json.dumps(relations), encoding="utf-8")
    llm = FakeLlm()
    monkeypatch.setattr(service_module, "create_llm_client", lambda settings: llm)
    monkeypatch.setattr(open_ai_clients, "create_open_ai_llm_client", lambda settings: llm)
    with TestClient(create_app(settings)) as client:
        yield SimpleNamespace(client=client, endpoint=endpoint, llm=llm, has_final_selection=has_final_selection)


@pytest.mark.parametrize(("fields", "seeds"), [
    ({}, ["A", "ISOLATED", "UNRELATED"]),
    ({"top_k": -1}, ["A", "ISOLATED", "UNRELATED"]),
    ({"top_k": 1}, ["A"]),
    ({"top_k": 2}, ["A", "ISOLATED"]),
    ({"top_k": 7}, ["A", "ISOLATED", "UNRELATED"]),
])
def test_seed_limit_applies_after_validation_and_deduplication(api, fields, seeds):
    api.llm.results = [selection(["A", "A", "ISOLATED", "UNRELATED"]), selection(["A"])]
    response = api.client.post(api.endpoint, json={"query": "테이블을 찾아줘", "max_hop": 0, **fields})
    assert response.status_code == 200
    body = response.json()
    assert body["meta_data"][0]["seed_count"] == len(seeds)
    assert body["meta_data"][0]["expanded_count"] == len(seeds)
    candidates = [table["table_name"] for table in TABLES if table["table_name"] in seeds]
    assert body["data"]["table_name"] == (["A"] if api.has_final_selection else candidates)
    assert len(api.llm.calls) == (2 if api.has_final_selection else 1)
    seed_payload = json.loads(api.llm.calls[0]["messages"][-1]["content"])
    instructions = api.llm.calls[0]["messages"][0]["content"]
    if fields.get("top_k", -1) > 0:
        assert seed_payload["top_k"] == fields["top_k"]
        assert f"최대 {fields['top_k']}개" in instructions
        assert "선택 개수에 제한은 없습니다." not in instructions
    else:
        assert "top_k" not in seed_payload
        assert instructions == SEED_SELECTION_INSTRUCTIONS
    if api.has_final_selection:
        final_payload = json.loads(api.llm.calls[1]["messages"][-1]["content"])
        assert final_payload["seed_tables"] == seeds
        assert [table["table_name"] for table in final_payload["candidates"]] == candidates


def test_top_k_does_not_limit_expanded_or_final_table_count(api):
    api.llm.results = [selection(["A", "ISOLATED"]), selection(["C", "B", "A"])]
    response = api.client.post(api.endpoint, json={"query": "테이블을 찾아줘", "max_hop": 2, "top_k": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["table_name"] == ["C", "B", "A"]
    assert body["meta_data"][0]["seed_count"] == 1
    assert body["meta_data"][0]["expanded_count"] == body["meta_data"][0]["retrieved_count"] == 3
    assert len(api.llm.calls) == (2 if api.has_final_selection else 1)
    if api.has_final_selection:
        final_payload = json.loads(api.llm.calls[1]["messages"][-1]["content"])
        assert final_payload["seed_tables"] == ["A"]
        assert "top_k" not in final_payload


@pytest.mark.parametrize("seeds", [["A"], []])
def test_fewer_relevant_seeds_than_top_k_is_allowed(api, seeds):
    api.llm.results = [selection(seeds), selection(seeds)]
    response = api.client.post(api.endpoint, json={"query": "테이블을 찾아줘", "max_hop": 0, "top_k": 5})
    assert response.status_code == 200
    assert response.json()["meta_data"][0]["seed_count"] == len(seeds)
    assert response.json()["data"]["table_name"] == seeds
    assert len(api.llm.calls) == (2 if seeds and api.has_final_selection else 1)


def test_unknown_seed_after_requested_limit_is_not_hidden(api):
    api.llm.results = [selection(["A", "UNKNOWN"])]
    response = api.client.post(api.endpoint, json={"query": "테이블을 찾아줘", "max_hop": 0, "top_k": 1})
    assert response.status_code == 502 and response.json()["data"] is None
    assert len(api.llm.calls) == 1


def test_zero_top_k_is_rejected_before_any_llm_call(api):
    response = api.client.post(api.endpoint, json={"query": "테이블을 찾아줘", "top_k": 0})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "top_k"]
    assert api.llm.calls == []
