from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from evaluation.src import evaluate_schema_retrieval as evaluation


EMBEDDING = "/retrieve/embedding_based_schema_open_ai"
LLM = "/retrieve/llm_based_schema_open_ai"
ENDPOINT_TOP_K = {
    "/retrieve/embedding_based_schema": 5,
    "/retrieve/embedded_triplet_based_schema": 5,
    "/retrieve/llm_based_schema": None,
    "/retrieve/triplet_based_schema": 5,
    "/retrieve/only_triplet_based_schema": 5,
    "/retrieve/hybrid_only_based_schema": 5,
    "/retrieve/embedding_based_schema_open_ai": 5,
    "/retrieve/embedded_triplet_based_schema_open_ai": 5,
    "/retrieve/llm_based_schema_open_ai": None,
    "/retrieve/triplet_based_schema_open_ai": 5,
    "/retrieve/only_triplet_based_schema_open_ai": 5,
    "/retrieve/hybrid_only_based_schema_open_ai": 5,
    "/retrieve/llm_based_schema_open_router": None,
    "/retrieve/triplet_based_schema_open_router": 5,
    "/retrieve/only_triplet_based_schema_open_router": 5,
}
TRIPLET_ENDPOINTS = (
    "/retrieve/triplet_based_schema", "/retrieve/triplet_based_schema_open_ai",
    "/retrieve/only_triplet_based_schema", "/retrieve/only_triplet_based_schema_open_ai",
    "/retrieve/hybrid_only_based_schema", "/retrieve/hybrid_only_based_schema_open_ai",
    "/retrieve/triplet_based_schema_open_router", "/retrieve/only_triplet_based_schema_open_router",
)
NON_TRIPLET_ENDPOINTS = tuple(endpoint for endpoint in ENDPOINT_TOP_K if endpoint not in TRIPLET_ENDPOINTS)
TOP_K_ENDPOINTS = tuple(endpoint for endpoint, top_k in ENDPOINT_TOP_K.items() if top_k is not None)
ITEMS = [
    {"id": "q0", "question": "  첫 질문  ", "tables": ["A", "B", "A"], "sql": "DO NOT SEND SQL", "history": ["DO NOT SEND HISTORY"]},
    {"id": "q1", "question": "두 번째 질문", "tables": ["C"]},
    {"id": "q2", "question": "마지막 질문", "tables": ["D", "E"]},
]


def response(tables, *, elapsed=0.25):
    return httpx.Response(200, json={
        "status": "success", "data": {
            "table_name": tables, "schema": ["DDL_SHOULD_NOT_BE_STORED"] * len(tables),
            "description": ["DESCRIPTION_SHOULD_NOT_BE_STORED"] * len(tables),
        }, "meta_data": [], "elapsed_time": elapsed,
    })


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "질의 데이터.json"
    path.write_text(json.dumps(ITEMS, ensure_ascii=False), encoding="utf-8")
    return path


def output_path(dataset, endpoint=EMBEDDING, *, top_k=5, max_hop=evaluation.MAX_HOP):
    filename = endpoint.rsplit('/', 1)[-1]
    if endpoint in TOP_K_ENDPOINTS:
        filename = f"{filename}_{top_k}"
    if endpoint in TRIPLET_ENDPOINTS:
        filename = f"{filename}_{max_hop}"
    return dataset.parent / "results" / f"{filename}.json"


def run(dataset, handler, **options):
    options.setdefault("endpoint", EMBEDDING)
    options.setdefault("top_k", 5)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return evaluation.evaluate(dataset_path=dataset, result_dir=dataset.parent / "results",
                                   client=client, progress=lambda message: None, **options)


def test_supported_endpoints_and_top_k_classification_match_api_contract():
    assert evaluation.SUPPORTED_ENDPOINTS == set(ENDPOINT_TOP_K)
    assert evaluation.EMBEDDING_ENDPOINTS == {
        endpoint for endpoint, top_k in ENDPOINT_TOP_K.items()
        if top_k is not None and endpoint not in TRIPLET_ENDPOINTS
    }
    assert evaluation.TRIPLET_ENDPOINTS == set(TRIPLET_ENDPOINTS)


@pytest.mark.parametrize("endpoint,expected_top_k", ENDPOINT_TOP_K.items())
def test_supported_endpoints_send_only_question_and_correct_request_fields(dataset, endpoint, expected_top_k):
    requests = []

    def handle(request):
        requests.append(request)
        data = json.loads(request.content)
        expected = {"query": ITEMS[len(requests) - 1]["question"], "history": []}
        if expected_top_k is not None:
            expected["top_k"] = expected_top_k
        if endpoint in TRIPLET_ENDPOINTS:
            expected["max_hop"] = evaluation.MAX_HOP
        assert data == expected
        assert str(request.url) == "https://api.test/prefix" + endpoint
        assert request.extensions["timeout"] == {"connect": 10.0, "read": 300.0, "write": 300.0, "pool": 300.0}
        return response(ITEMS[len(requests) - 1]["tables"])

    report = run(dataset, handle, endpoint=endpoint, base_url="https://api.test/prefix/")
    saved = output_path(dataset, endpoint).read_text()
    assert len(requests) == 3 and report["state"] == "complete"
    assert report["configuration"]["top_k"] == expected_top_k
    assert report["configuration"]["max_hop"] == (evaluation.MAX_HOP if endpoint in TRIPLET_ENDPOINTS else None)
    assert report["configuration"]["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert report["results"][0]["expected_tables"] == ["A", "B"]
    assert report["results"][0]["predicted_tables"] == ["A", "B"]
    assert report["summary"]["macro"] == {"precision": 1, "recall": 1, "f1": 1}
    assert report["summary"]["exact_match_rate"] == 1
    assert "DDL_SHOULD_NOT_BE_STORED" not in saved and "DESCRIPTION_SHOULD_NOT_BE_STORED" not in saved
    assert "첫 질문" in saved and json.loads(saved) == report


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("max_hop", [-1, 0, 1, 3, 11])
def test_triplet_requests_and_saved_configuration_use_selected_max_hop(dataset, endpoint, max_hop):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload == {"query": ITEMS[len(requests)]["question"], "history": [], "max_hop": max_hop, "top_k": -1}
        requests.append(payload)
        return response([])

    report = run(dataset, handle, endpoint=endpoint, max_hop=max_hop, top_k=-1)
    assert len(requests) == len(ITEMS)
    assert report["configuration"]["max_hop"] == max_hop
    assert report["configuration"]["top_k"] == -1
    path = output_path(dataset, endpoint, top_k=-1, max_hop=max_hop)
    assert json.loads(path.read_bytes())["configuration"]["max_hop"] == max_hop
    assert list(path.parent.glob("*.json")) == [path]
    assert path.name == f"{endpoint.rsplit('/', 1)[-1]}_-1_{max_hop}.json"


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("top_k", [-1, 1, 3, 11])
def test_triplet_evaluation_sends_and_saves_top_k_with_max_hop(dataset, endpoint, top_k):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload == {"query": ITEMS[len(requests)]["question"], "history": [], "top_k": top_k, "max_hop": 2}
        requests.append(payload)
        return response([])

    report = run(dataset, handle, endpoint=endpoint, top_k=top_k, max_hop=2)
    assert len(requests) == len(ITEMS)
    assert report["configuration"]["top_k"] == top_k
    assert report["configuration"]["max_hop"] == 2
    path = output_path(dataset, endpoint, top_k=top_k, max_hop=2)
    assert json.loads(path.read_bytes()) == report
    assert path.name == f"{endpoint.rsplit('/', 1)[-1]}_{top_k}_2.json"


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
def test_graph_retrieval_scores_all_expanded_tables_beyond_seed_top_k(dataset, endpoint):
    predicted = ["A", "B", "EXTRA"]

    def handle(request):
        payload = json.loads(request.content)
        assert payload["top_k"] == 1 and payload["max_hop"] == 2
        return response(predicted)

    report = run(dataset, handle, endpoint=endpoint, top_k=1, max_hop=2)
    assert report["summary"]["success_count"] == len(ITEMS)
    assert all(row["predicted_tables"] == predicted for row in report["results"])
    assert report["results"][0]["metrics"] == {
        "tp": 2, "fp": 1, "fn": 0, "precision": 2 / 3, "recall": 1.0, "f1": 0.8, "exact_match": False,
    }
    assert json.loads(output_path(dataset, endpoint, top_k=1, max_hop=2).read_text()) == report


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("top_k", [-2, 0, True, False, "3", 3.0, None, float("inf"), [], {}])
def test_invalid_triplet_top_k_stops_before_http_or_save(dataset, endpoint, top_k):
    with pytest.raises(ValueError, match="TOP_K"):
        run(dataset, lambda request: pytest.fail("Invalid TOP_K must not call the API"),
            endpoint=endpoint, top_k=top_k)
    assert not (dataset.parent / "results").exists()


@pytest.mark.parametrize("endpoint", [
    "/retrieve/embedding_based_schema", "/retrieve/embedding_based_schema_open_ai",
    "/retrieve/embedded_triplet_based_schema", "/retrieve/embedded_triplet_based_schema_open_ai",
])
def test_embedding_retrieval_still_rejects_unlimited_top_k(dataset, endpoint):
    with pytest.raises(ValueError, match="TOP_K"):
        run(dataset, lambda request: pytest.fail("Embedding TOP_K must remain positive"),
            endpoint=endpoint, top_k=-1)
    assert not (dataset.parent / "results").exists()


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("max_hop", [-2, -10, 3.0, 1.5, True, False, "3", None, [], {}])
def test_invalid_triplet_max_hop_stops_before_http_or_save(dataset, endpoint, max_hop):
    with pytest.raises(ValueError, match="MAX_HOP"):
        run(dataset, lambda request: pytest.fail("Invalid MAX_HOP must not call the API"),
            endpoint=endpoint, max_hop=max_hop)
    assert not (dataset.parent / "results").exists()


@pytest.mark.parametrize("endpoint", NON_TRIPLET_ENDPOINTS)
def test_non_triplet_requests_ignore_max_hop_and_reuse_results_when_it_changes(dataset, endpoint):
    def handle(request):
        assert "max_hop" not in json.loads(request.content)
        return response([])

    initial = run(dataset, handle, endpoint=endpoint, max_hop=-1)
    assert initial["configuration"]["max_hop"] is None
    suffix = "_5" if endpoint in TOP_K_ENDPOINTS else ""
    assert output_path(dataset, endpoint).name == f"{endpoint.rsplit('/', 1)[-1]}{suffix}.json"
    assert list(output_path(dataset, endpoint).parent.glob("*.json")) == [output_path(dataset, endpoint)]
    resumed = run(dataset, lambda request: pytest.fail("Unused MAX_HOP must not block resume"),
                  endpoint=endpoint, max_hop="unused")
    assert resumed["configuration"]["max_hop"] is None
    assert resumed["summary"]["success_count"] == len(ITEMS)


@pytest.mark.parametrize("endpoint", [
    "/retrieve/embedding_based_schema", "/retrieve/embedded_triplet_based_schema",
    "/retrieve/llm_based_schema", "/retrieve/triplet_based_schema",
    "/retrieve/only_triplet_based_schema",
    "/retrieve/hybrid_only_based_schema",
])
def test_local_and_open_ai_results_have_independent_checkpoints(dataset, endpoint):
    open_ai_endpoint = endpoint + "_open_ai"
    run(dataset, lambda request: response([]), endpoint=open_ai_endpoint)
    open_ai_path = output_path(dataset, open_ai_endpoint)
    open_ai_before = open_ai_path.read_bytes()
    calls = []

    def local_response(request):
        calls.append(json.loads(request.content)["query"])
        return response(ITEMS[len(calls) - 1]["tables"])

    local_report = run(dataset, local_response, endpoint=endpoint)
    assert len(calls) == len(ITEMS)
    assert local_report["summary"]["exact_match_rate"] == 1
    assert output_path(dataset, endpoint).is_file() and output_path(dataset, endpoint) != open_ai_path
    assert open_ai_path.read_bytes() == open_ai_before

    def reject(request):
        pytest.fail("Existing successful results must be reused for their own endpoint")

    assert run(dataset, reject, endpoint=endpoint)["summary"]["exact_match_rate"] == 1
    assert run(dataset, reject, endpoint=open_ai_endpoint)["summary"]["exact_match_rate"] == 0


@pytest.mark.parametrize("endpoint", [
    "/retrieve/llm_based_schema", "/retrieve/only_triplet_based_schema",
    "/retrieve/triplet_based_schema",
])
def test_open_router_results_have_independent_checkpoints(dataset, endpoint):
    previous_files = {}
    for other_endpoint in (endpoint, endpoint + "_open_ai"):
        run(dataset, lambda request: response([]), endpoint=other_endpoint)
        path = output_path(dataset, other_endpoint)
        previous_files[path] = path.read_bytes()
    open_router_endpoint = endpoint + "_open_router"
    calls = []

    def router_response(request):
        assert request.url.path == open_router_endpoint
        calls.append(json.loads(request.content)["query"])
        return response(ITEMS[len(calls) - 1]["tables"])

    report = run(dataset, router_response, endpoint=open_router_endpoint)
    assert calls == [item["question"] for item in ITEMS]
    assert report["summary"]["exact_match_rate"] == 1
    router_path = output_path(dataset, open_router_endpoint)
    assert router_path.is_file() and router_path not in previous_files
    assert json.loads(router_path.read_bytes()) == report
    assert all(path.read_bytes() == before for path, before in previous_files.items())

    def reject(request):
        pytest.fail("Each provider must reuse only its own successful results")

    assert run(dataset, reject, endpoint=open_router_endpoint)["summary"]["exact_match_rate"] == 1
    for other_endpoint in (endpoint, endpoint + "_open_ai"):
        assert run(dataset, reject, endpoint=other_endpoint)["summary"]["exact_match_rate"] == 0


@pytest.mark.parametrize("gold,predicted,expected", [
    (["A", "B"], ["B", "A"], (2, 0, 0, 1, 1, 1, True)),
    (["A", "B"], ["A", "X"], (1, 1, 1, 0.5, 0.5, 0.5, False)),
    (["A"], ["X"], (0, 1, 1, 0, 0, 0, False)),
    (["A"], [], (0, 0, 1, 0, 0, 0, False)),
    ([], [], (0, 0, 0, 0, 0, 0, True)),
    ([], ["A"], (0, 1, 0, 0, 0, 0, False)),
    (["A", "A"], ["A", "A"], (1, 0, 0, 1, 1, 1, True)),
    (["A"], ["a"], (0, 1, 1, 0, 0, 0, False)),
])
def test_set_metrics_zero_division_and_exact_case(gold, predicted, expected):
    metrics = evaluation.calculate_metrics(gold, predicted)
    assert tuple(metrics[name] for name in ("tp", "fp", "fn", "precision", "recall", "f1", "exact_match")) == expected


def test_macro_micro_exact_match_and_latency_aggregation(dataset):
    predictions = iter((["A", "X"], ["C", "C"], []))
    report = run(dataset, lambda request: response(next(predictions), elapsed=0.75))
    summary = report["summary"]
    assert summary["macro"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5}
    assert summary["micro"] == {"tp": 2, "fp": 1, "fn": 3, "precision": 2 / 3, "recall": 2 / 5, "f1": 0.5}
    assert summary["exact_match_rate"] == 1 / 3
    assert summary["success_rate"] == 1 and summary["pending_count"] == summary["failure_count"] == 0
    assert summary["mean_api_elapsed_time"] == 0.75 and summary["mean_latency_seconds"] >= 0
    assert report["results"][1]["predicted_tables"] == ["C"]


@pytest.mark.parametrize("failure,kind,http_status", [
    ("http", "http_error", 429), ("api", "api_error", 200), ("json", "invalid_response", 200),
    ("timeout", "ReadTimeout", None), ("connection", "ConnectError", None),
])
def test_failures_are_recorded_excluded_from_metrics_and_do_not_stop_evaluation(dataset, failure, kind, http_status):
    seen = []

    def handle(request):
        index = len(seen)
        seen.append(json.loads(request.content))
        if index != 1:
            return response(ITEMS[index]["tables"])
        if failure == "http":
            return httpx.Response(429, json={"detail": "quota exceeded"})
        if failure == "api":
            return httpx.Response(200, json={"status": "error", "msg": "upstream failed", "data": None})
        if failure == "json":
            return httpx.Response(200, content="{invalid")
        if failure == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        raise httpx.ConnectError("mock connection error", request=request)

    report = run(dataset, handle)
    row, summary = report["results"][1], report["summary"]
    assert len(seen) == 3 and report["state"] == "complete"
    assert row["status"] == "error" and row["http_status"] == http_status
    assert row["error"]["type"] == kind and row["predicted_tables"] is row["metrics"] is None
    assert row["latency_seconds"] >= 0
    assert summary["success_count"] == 2 and summary["failure_count"] == 1 and summary["pending_count"] == 0
    assert summary["success_rate"] == 2 / 3 and summary["macro"]["f1"] == summary["exact_match_rate"] == 1
    assert summary["micro"]["fn"] == 0


@pytest.mark.parametrize("payload", [
    None, [], "text", {"status": "success", "data": None},
    {"status": "success", "data": {"table_name": []}},
    {"status": "success", "data": {"table_name": "A", "schema": [], "description": []}},
    {"status": "success", "data": {"table_name": [1], "schema": ["DDL"], "description": ["desc"]}},
    {"status": "success", "data": {"table_name": [" "], "schema": ["DDL"], "description": ["desc"]}},
    {"status": "success", "data": {"table_name": ["A"], "schema": [], "description": ["desc"]}},
    {"status": "success", "data": {"table_name": ["A"], "schema": [None], "description": ["desc"]}},
])
def test_bad_response_shapes_are_failures(dataset, payload):
    report = run(dataset, lambda request: httpx.Response(200, json=payload))
    assert report["summary"]["success_count"] == 0 and report["summary"]["failure_count"] == 3
    assert report["summary"]["macro"] is report["summary"]["micro"] is report["summary"]["exact_match_rate"] is None
    assert report["summary"]["mean_latency_seconds"] is None
    assert all(row["error"]["type"] == "invalid_response" for row in report["results"])


@pytest.mark.parametrize("elapsed", [None, "bad", -1, True])
def test_invalid_optional_server_timing_does_not_invalidate_search_results(dataset, elapsed):
    report = run(dataset, lambda request: response([], elapsed=elapsed))
    assert report["summary"]["success_count"] == 3 and report["summary"]["mean_api_elapsed_time"] is None
    assert all(row["api_elapsed_time"] is None for row in report["results"])


@pytest.mark.parametrize("raw", ["", "{}", "null", "{bad", '[{"question":" ","tables":[]}]',
                                   '[{"question":123,"tables":[]}]', '[{"question":"q"}]',
                                   '[{"question":"q","tables":"A"}]', '[{"question":"q","tables":[null]}]',
                                   '[{"question":"q","tables":[" "]}]', '[{"question":"q","tables":[],"extra":NaN}]'])
def test_invalid_dataset_stops_before_http_or_results_creation(dataset, raw):
    dataset.write_text(raw)

    def reject(request):
        pytest.fail("Invalid dataset must not call the API")

    with pytest.raises(ValueError):
        run(dataset, reject)
    assert not output_path(dataset).exists()


@pytest.mark.parametrize("options", [
    {"endpoint": "/unsupported"}, {"base_url": ""}, {"base_url": "file:///tmp/test"},
    {"base_url": "http://test/?query=1"}, {"base_url": "http://test/#fragment"},
    {"top_k": 0}, {"top_k": True}, {"top_k": "5"}, {"top_k": 1.5},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")}, {"timeout_seconds": True}, {"resume": "yes"},
])
def test_invalid_configuration_stops_before_http_or_save(dataset, options):
    with pytest.raises(ValueError):
        run(dataset, lambda request: pytest.fail("Invalid settings must not call the API"), **options)
    assert not output_path(dataset).exists()


def test_failure_retry_reuses_successes_and_replaces_existing_result(dataset):
    attempts = []

    def initial(request):
        index = len(attempts)
        attempts.append(index)
        return httpx.Response(500, json={"msg": "retry me"}) if index == 1 else response(ITEMS[index]["tables"])

    first = run(dataset, initial)
    assert first["summary"]["failure_count"] == 1
    retried = []

    def retry(request):
        retried.append(json.loads(request.content)["query"])
        return response(["C"])

    second = run(dataset, retry)
    assert retried == [ITEMS[1]["question"]]
    assert second["summary"]["success_count"] == 3 and second["summary"]["failure_count"] == 0
    assert len(second["results"]) == 3 and second["results"][0] == first["results"][0]
    third = run(dataset, lambda request: pytest.fail("All successes must be reused"))
    assert third["summary"] == second["summary"]


def test_interruption_keeps_checkpoint_and_resumes_only_pending_items(dataset):
    calls = []

    def interrupt(request):
        if calls:
            # The preceding completed question was saved before the next call.
            saved = json.loads(output_path(dataset).read_text())
            assert saved["summary"]["evaluated_count"] == 1 and saved["summary"]["pending_count"] == 2
            raise KeyboardInterrupt()
        calls.append(json.loads(request.content)["query"])
        return response(["A", "B"])

    with pytest.raises(KeyboardInterrupt):
        run(dataset, interrupt)
    saved = json.loads(output_path(dataset).read_text())
    assert saved["state"] == "interrupted" and saved["summary"]["success_count"] == 1
    calls.clear()

    def finish(request):
        index = 1 + len(calls)
        calls.append(json.loads(request.content)["query"])
        return response(ITEMS[index]["tables"])

    report = run(dataset, finish)
    assert calls == [item["question"] for item in ITEMS[1:]]
    assert report["state"] == "complete" and report["summary"]["exact_match_rate"] == 1


@pytest.mark.parametrize("change", ["base_url", "top_k", "dataset", "endpoint", "version"])
def test_resume_mismatch_preserves_existing_file_and_never_sends_requests(dataset, change):
    run(dataset, lambda request: response([]))
    path = output_path(dataset)
    options = {}
    if change == "base_url":
        options[change] = "https://different.test/v1"
    elif change == "dataset":
        dataset.write_text(dataset.read_text() + "\n")
    else:
        value = json.loads(path.read_text())
        if change in ("endpoint", "top_k"):
            value["configuration"][change] = LLM if change == "endpoint" else 6
        else:
            value["format_version"] = 999
        path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="RESUME=False"):
        run(dataset, lambda request: pytest.fail("Mismatched results must not call the API"), **options)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["json", "duplicate_index", "wrong_question", "negative_time", "invalid_prediction", "status"])
def test_corrupt_checkpoint_is_not_silently_reused_or_overwritten(dataset, damage):
    run(dataset, lambda request: response([]))
    path = output_path(dataset)
    value = json.loads(path.read_text())
    if damage == "json":
        path.write_text("{invalid")
    else:
        if damage == "duplicate_index":
            value["results"].append(deepcopy(value["results"][0]))
        elif damage == "wrong_question":
            value["results"][0]["question"] = "changed"
        elif damage == "negative_time":
            value["results"][0]["latency_seconds"] = -1
        elif damage == "invalid_prediction":
            value["results"][0]["predicted_tables"] = None
        else:
            value["results"][0]["status"] = "pending"
        path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="RESUME=False"):
        run(dataset, lambda request: pytest.fail("Corrupt results must not call the API"))
    assert path.read_bytes() == before


def test_fresh_run_overwrites_incompatible_results_and_calls_all_questions(dataset):
    run(dataset, lambda request: response([]), top_k=6)
    path = output_path(dataset, top_k=6)
    path.write_text("{bad old result")
    calls = []

    def fresh(request):
        calls.append(request)
        return response(ITEMS[len(calls) - 1]["tables"])

    report = run(dataset, fresh, resume=False, top_k=6)
    assert len(calls) == 3 and report["configuration"]["top_k"] == 6 and report["summary"]["exact_match_rate"] == 1
    assert json.loads(path.read_bytes()) == report
    assert list(path.parent.glob("*.json")) == [path]


@pytest.mark.parametrize("endpoint", [
    "/retrieve/llm_based_schema", LLM, "/retrieve/llm_based_schema_open_router",
])
def test_reused_metrics_are_recomputed_and_unused_top_k_does_not_block_resume(dataset, endpoint):
    run(dataset, lambda request: response([]), endpoint=endpoint)
    path = output_path(dataset, endpoint)
    value = json.loads(path.read_text())
    value["results"][0]["metrics"] = {"precision": 99}
    value["summary"]["exact_match_rate"] = 99
    path.write_text(json.dumps(value))
    report = run(dataset, lambda request: pytest.fail("Successful LLM queries must be reused"),
                 endpoint=endpoint, top_k=999, timeout_seconds=600)
    assert report["configuration"]["top_k"] is None and report["configuration"]["timeout_seconds"] == 600
    assert report["summary"]["macro"]["precision"] == report["summary"]["exact_match_rate"] == 0
    assert report["results"][0]["metrics"]["fn"] == 2


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("top_k", [-1, 1])
def test_triplet_resume_rejects_top_k_inconsistent_with_filename(dataset, endpoint, top_k):
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=top_k)
    resumed = run(dataset, lambda request: pytest.fail("Matching TOP_K must reuse successful rows"),
                  endpoint=endpoint, top_k=top_k)
    assert resumed["summary"]["success_count"] == len(ITEMS)
    path = output_path(dataset, endpoint, top_k=top_k)
    checkpoint = json.loads(path.read_bytes())
    checkpoint["configuration"]["top_k"] = 2
    path.write_text(json.dumps(checkpoint))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="top_k.*RESUME=False"):
        run(dataset, lambda request: pytest.fail("Inconsistent checkpoint must not call the API"),
            endpoint=endpoint, top_k=top_k)
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
def test_legacy_triplet_null_top_k_resumes_only_as_unlimited(dataset, endpoint):
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=-1)
    path = output_path(dataset, endpoint, top_k=-1)
    checkpoint = json.loads(path.read_bytes())
    checkpoint["configuration"]["top_k"] = None
    path.write_text(json.dumps(checkpoint))
    before = path.read_bytes()
    limited_path = output_path(dataset, endpoint, top_k=5)
    limited_path.write_bytes(before)
    with pytest.raises(ValueError, match="top_k.*RESUME=False"):
        run(dataset, lambda request: pytest.fail("Legacy unlimited rows must not become limited rows"),
            endpoint=endpoint, top_k=5)
    assert path.read_bytes() == before
    assert limited_path.read_bytes() == before
    resumed = run(dataset, lambda request: pytest.fail("Legacy unlimited successes must be reused for -1"),
                  endpoint=endpoint, top_k=-1)
    assert resumed["summary"]["success_count"] == len(ITEMS)
    assert resumed["configuration"]["top_k"] == -1


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
def test_legacy_unlimited_triplet_failure_retry_sends_explicit_minus_one(dataset, endpoint):
    initial_calls = []

    def initial(request):
        index = len(initial_calls)
        initial_calls.append(request)
        return httpx.Response(500) if index == 1 else response(ITEMS[index]["tables"])

    run(dataset, initial, endpoint=endpoint, top_k=-1)
    path = output_path(dataset, endpoint, top_k=-1)
    checkpoint = json.loads(path.read_bytes())
    checkpoint["configuration"]["top_k"] = None
    path.write_text(json.dumps(checkpoint))
    retried = []

    def retry(request):
        payload = json.loads(request.content)
        assert payload["top_k"] == -1 and payload["max_hop"] == evaluation.MAX_HOP
        retried.append(payload["query"])
        return response(["C"])

    report = run(dataset, retry, endpoint=endpoint, top_k=-1)
    assert retried == [ITEMS[1]["question"]]
    assert report["summary"]["success_count"] == len(ITEMS)
    assert report["configuration"]["top_k"] == -1


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("stored_value", [True, 1.0, "1"])
def test_triplet_checkpoint_top_k_must_be_an_integer(dataset, endpoint, stored_value):
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=1)
    path = output_path(dataset, endpoint, top_k=1)
    checkpoint = json.loads(path.read_bytes())
    checkpoint["configuration"]["top_k"] = stored_value
    path.write_text(json.dumps(checkpoint))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="top_k.*RESUME=False"):
        run(dataset, lambda request: pytest.fail("Invalid checkpoint TOP_K must not be reused"),
            endpoint=endpoint, top_k=1)
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", TOP_K_ENDPOINTS)
def test_top_k_values_have_independent_files_and_resume_their_own_results(dataset, endpoint):
    top_k = -1 if endpoint in TRIPLET_ENDPOINTS else 1
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=top_k)
    path = output_path(dataset, endpoint, top_k=top_k)
    before = path.read_bytes()
    calls = []

    def different_top_k(request):
        payload = json.loads(request.content)
        assert payload["top_k"] == 2
        prediction = ITEMS[len(calls)]["tables"]
        calls.append(payload)
        return response(prediction)

    other = run(dataset, different_top_k, endpoint=endpoint, top_k=2)
    other_path = output_path(dataset, endpoint, top_k=2)
    assert len(calls) == len(ITEMS)
    assert path != other_path and other_path.is_file()
    assert path.read_bytes() == before
    assert other["summary"]["exact_match_rate"] == 1
    other_before = other_path.read_bytes()

    def reject(request):
        pytest.fail("Successful results must be reused from their own top_k file")

    resumed = run(dataset, reject, endpoint=endpoint, top_k=top_k)
    assert resumed["summary"]["exact_match_rate"] == 0
    assert other_path.read_bytes() == other_before
    before = path.read_bytes()
    assert run(dataset, reject, endpoint=endpoint, top_k=2)["summary"]["exact_match_rate"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", TOP_K_ENDPOINTS)
def test_fresh_run_updates_only_the_selected_top_k_file(dataset, endpoint):
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=1)
    other_path = output_path(dataset, endpoint, top_k=1)
    other_before = other_path.read_bytes()
    run(dataset, lambda request: response([]), endpoint=endpoint, top_k=2)
    requests = []

    def fresh(request):
        payload = json.loads(request.content)
        assert payload["top_k"] == 2
        prediction = ITEMS[len(requests)]["tables"]
        requests.append(payload)
        return response(prediction)

    report = run(dataset, fresh, endpoint=endpoint, top_k=2, resume=False)
    assert len(requests) == len(ITEMS)
    assert report["configuration"]["top_k"] == 2
    assert report["summary"]["exact_match_rate"] == 1
    assert json.loads(output_path(dataset, endpoint, top_k=2).read_bytes()) == report
    assert other_path.read_bytes() == other_before


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("max_hop", [-1, 0, 3])
def test_triplet_depths_have_independent_files_and_resume_their_own_results(dataset, endpoint, max_hop):
    run(dataset, lambda request: response([]), endpoint=endpoint, max_hop=max_hop)
    path = output_path(dataset, endpoint, max_hop=max_hop)
    before = path.read_bytes()
    calls = []

    def different_depth(request):
        payload = json.loads(request.content)
        assert payload["max_hop"] == 1
        prediction = ITEMS[len(calls)]["tables"]
        calls.append(payload)
        return response(prediction)

    other = run(dataset, different_depth, endpoint=endpoint, max_hop=1)
    other_path = output_path(dataset, endpoint, max_hop=1)
    assert len(calls) == len(ITEMS)
    assert path != other_path and other_path.is_file()
    assert path.read_bytes() == before
    assert other["summary"]["exact_match_rate"] == 1
    other_before = other_path.read_bytes()

    def reject(request):
        pytest.fail("Successful results must be reused from their own depth's file")

    resumed = run(dataset, reject, endpoint=endpoint, max_hop=max_hop)
    assert resumed["summary"]["success_count"] == len(ITEMS)
    assert resumed["summary"]["exact_match_rate"] == 0
    assert other_path.read_bytes() == other_before
    before = path.read_bytes()
    assert run(dataset, reject, endpoint=endpoint, max_hop=1)["summary"]["exact_match_rate"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("max_hop", [-1, 0, 3])
def test_triplet_resume_rejects_max_hop_inconsistent_with_filename(dataset, endpoint, max_hop):
    run(dataset, lambda request: response([]), endpoint=endpoint, max_hop=max_hop)
    path = output_path(dataset, endpoint, max_hop=max_hop)
    checkpoint = json.loads(path.read_bytes())
    checkpoint["configuration"]["max_hop"] = 1
    path.write_text(json.dumps(checkpoint))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="max_hop.*RESUME=False"):
        run(dataset, lambda request: pytest.fail("Inconsistent checkpoint must not call the API"),
            endpoint=endpoint, max_hop=max_hop)
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
@pytest.mark.parametrize("saved_value", ["missing", None, False, 0.0])
def test_triplet_resume_rejects_missing_or_invalid_max_hop(dataset, endpoint, saved_value):
    run(dataset, lambda request: response([]), endpoint=endpoint, max_hop=0)
    path = output_path(dataset, endpoint, max_hop=0)
    checkpoint = json.loads(path.read_bytes())
    if saved_value == "missing":
        del checkpoint["configuration"]["max_hop"]
    else:
        checkpoint["configuration"]["max_hop"] = saved_value
    path.write_text(json.dumps(checkpoint))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="max_hop.*RESUME=False"):
        run(dataset, lambda request: pytest.fail("Unknown MAX_HOP must not call the API"),
            endpoint=endpoint, max_hop=0)
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint", NON_TRIPLET_ENDPOINTS)
def test_legacy_non_triplet_results_without_max_hop_remain_resumable(dataset, endpoint):
    run(dataset, lambda request: response([]), endpoint=endpoint)
    path = output_path(dataset, endpoint)
    checkpoint = json.loads(path.read_bytes())
    del checkpoint["configuration"]["max_hop"]
    path.write_text(json.dumps(checkpoint))
    resumed = run(dataset, lambda request: pytest.fail("Legacy non-triplet results must be reused"),
                  endpoint=endpoint, max_hop=7)
    assert resumed["summary"]["success_count"] == len(ITEMS)
    assert resumed["configuration"]["max_hop"] is None


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
def test_fresh_triplet_run_updates_only_the_selected_depth_file(dataset, endpoint):
    run(dataset, lambda request: response([]), endpoint=endpoint, max_hop=0)
    other_path = output_path(dataset, endpoint, max_hop=0)
    other_before = other_path.read_bytes()
    run(dataset, lambda request: response([]), endpoint=endpoint, max_hop=3)
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload["max_hop"] == 3
        prediction = ITEMS[len(requests)]["tables"]
        requests.append(payload)
        return response(prediction)

    report = run(dataset, handle, endpoint=endpoint, max_hop=3, resume=False)
    assert len(requests) == len(ITEMS)
    assert report["configuration"]["max_hop"] == 3
    assert report["summary"]["exact_match_rate"] == 1
    assert json.loads(output_path(dataset, endpoint, max_hop=3).read_bytes()) == report
    assert other_path.read_bytes() == other_before


@pytest.mark.parametrize("endpoint", TOP_K_ENDPOINTS)
def test_evaluation_preserves_and_does_not_resume_previous_filenames(dataset, endpoint):
    filename = endpoint.rsplit('/', 1)[-1]
    legacy_names = [f"{filename}.json"]
    if endpoint in TRIPLET_ENDPOINTS:
        legacy_names.append(f"{filename}_3.json")
    directory = dataset.parent / "results"
    directory.mkdir()
    previous_files = {}
    for name in legacy_names:
        legacy_path = directory / name
        legacy_path.write_text('{"legacy": "원본 평가 결과"}', encoding="utf-8")
        previous_files[legacy_path] = legacy_path.read_bytes()
    calls = []

    def handle(request):
        calls.append(request)
        return response([])

    run(dataset, handle, endpoint=endpoint, max_hop=3)
    assert len(calls) == len(ITEMS)
    assert all(path.read_bytes() == before for path, before in previous_files.items())
    assert output_path(dataset, endpoint, max_hop=3).is_file()
    assert len(list(directory.glob("*.json"))) == len(previous_files) + 1


@pytest.mark.parametrize("endpoint", TRIPLET_ENDPOINTS)
def test_interrupted_triplet_evaluation_resumes_from_the_depth_specific_file(dataset, endpoint):
    calls = []

    def interrupt(request):
        if calls:
            raise KeyboardInterrupt()
        calls.append(request)
        return response([])

    with pytest.raises(KeyboardInterrupt):
        run(dataset, interrupt, endpoint=endpoint, max_hop=-1)
    path = output_path(dataset, endpoint, max_hop=-1)
    checkpoint = json.loads(path.read_bytes())
    assert checkpoint["state"] == "interrupted"
    assert checkpoint["summary"]["success_count"] == 1
    resumed_queries = []

    def remaining(request):
        payload = json.loads(request.content)
        assert payload["max_hop"] == -1
        resumed_queries.append(payload["query"])
        return response([])

    report = run(dataset, remaining, endpoint=endpoint, max_hop=-1)
    assert resumed_queries == [item["question"] for item in ITEMS[1:]]
    assert report["state"] == "complete"
    assert report["summary"]["success_count"] == len(ITEMS)
    assert json.loads(path.read_bytes()) == report


def test_atomic_save_keeps_previous_result_and_cleans_temporary_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    evaluation.save_report(target, {"old": "성공"})
    before = target.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(evaluation.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        evaluation.save_report(target, {"new": "result"})
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_empty_dataset_has_no_requests_and_no_fabricated_metrics(dataset):
    dataset.write_text("[]")
    report = run(dataset, lambda request: pytest.fail("Empty dataset must not call the API"))
    assert report["state"] == "complete" and report["results"] == []
    assert report["summary"]["total_count"] == report["summary"]["evaluated_count"] == 0
    assert report["summary"]["success_rate"] is report["summary"]["macro"] is None


def test_paths_are_anchored_to_project_and_injected_client_stays_open(dataset, monkeypatch):
    monkeypatch.chdir(dataset.parent)
    assert evaluation.DATASET_PATH.is_absolute() and evaluation.DATASET_PATH.name == "test_set.json"
    assert evaluation.DATASET_PATH.parent.parent == evaluation.RESULT_DIR.parent
    with httpx.Client(transport=httpx.MockTransport(lambda request: response([]))) as client:
        evaluation.evaluate(endpoint=EMBEDDING, dataset_path=dataset, result_dir="relative results",
                            client=client, progress=lambda message: None)
        assert not client.is_closed
    assert (dataset.parent / f"relative results/embedding_based_schema_open_ai_{evaluation.TOP_K}.json").is_file()


@pytest.mark.parametrize("outcome,expected_code", [("success", 0), ("failure", 1), ("interrupt", 130), ("invalid", 1)])
def test_main_uses_top_level_settings_and_exit_codes(monkeypatch, outcome, expected_code):
    monkeypatch.setattr(evaluation, "ENDPOINT", LLM)
    monkeypatch.setattr(evaluation, "TOP_K", 9)
    monkeypatch.setattr(evaluation, "MAX_HOP", -1)
    calls = []

    def fake_evaluate(**options):
        calls.append(options)
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        if outcome == "invalid":
            raise ValueError("invalid setup")
        return {"summary": {"failure_count": int(outcome == "failure")}}

    monkeypatch.setattr(evaluation, "evaluate", fake_evaluate)
    assert evaluation.main() == expected_code
    assert calls[0]["endpoint"] == LLM and calls[0]["top_k"] == 9
    assert calls[0]["max_hop"] == -1
