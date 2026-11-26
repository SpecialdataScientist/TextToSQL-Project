from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy

import httpx
import pytest
from sqlglot import exp
from sqlglot.errors import UnsupportedError

from app.schemas.request import LlmBasedGenerateSqlRequest
from evaluation.src import evaluate_sql_generation as evaluation


OPEN_AI = "/generate_sql/llm_based_generate_sql_open_ai"
LOCAL = "/generate_sql/llm_based_generate_sql"
CATALOG = [
    {"table_name": "A", "schema": "CREATE TABLE A (ID INTEGER, NAME VARCHAR2(50));", "description": "A의 카탈로그 설명"},
    {"table_name": "B", "schema": "CREATE TABLE B (ID INTEGER, LABEL VARCHAR2(50));", "description": "B의 카탈로그 설명"},
    {"table_name": "GOLD_ONLY", "schema": "CREATE TABLE GOLD_ONLY (SECRET INTEGER);", "description": "정답 전용 설명"},
]
ITEMS = [
    {"id": "q0", "category": "joins", "question": "  첫 질문  ", "sql": "SELECT ID FROM A;",
     "tables": ["GOLD_ONLY"], "schema": ["DO NOT SEND GOLD DDL"],
     "history": ["DO NOT SEND HISTORY"], "predicted_schema": [CATALOG[1]["schema"], CATALOG[0]["schema"], CATALOG[1]["schema"]]},
    {"id": "q1", "category": "literal", "question": "두 번째 질문", "sql": "SELECT 'A  B;' AS LABEL FROM B;",
     "predicted_schema": [CATALOG[1]["schema"]]},
    {"id": "q2", "question": "마지막 질문", "sql": "SELECT ID FROM B;", "predicted_schema": [CATALOG[0]["schema"]]},
]


def response(sql, *, metadata=True, elapsed=0.25):
    payload = {"status": "success", "data": sql, "elapsed_time": elapsed}
    if metadata:
        payload["meta_data"] = [{"model_name": "test-model", "question_token": 100, "answer_token": 20}]
    return httpx.Response(200, json=payload)


@pytest.fixture
def files(tmp_path):
    dataset = tmp_path / "질의 데이터.json"
    catalog = tmp_path / "카탈로그.json"
    dataset.write_text(json.dumps(ITEMS, ensure_ascii=False), encoding="utf-8")
    catalog.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
    return dataset, catalog


def output_path(files):
    return files[0].parent / "results" / f"{files[0].stem}.json"


def run(files, handler, **options):
    options.setdefault("endpoint", OPEN_AI)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return evaluation.evaluate(dataset_path=files[0], catalog_path=files[1],
                                   result_dir=files[0].parent / "results", client=client,
                                   progress=lambda message: None, **options)


@pytest.mark.parametrize("endpoint", [OPEN_AI, LOCAL])
def test_endpoint_request_uses_only_question_predicted_ddls_and_catalog_metadata(files, endpoint):
    assert evaluation.SUPPORTED_ENDPOINTS == {OPEN_AI, LOCAL}
    requests = []
    before = [path.read_bytes() for path in files]

    def handle(request):
        index = len(requests)
        requests.append(request)
        payload = json.loads(request.content)
        expected_tables = [next(table for table in CATALOG if table["schema"] == ddl)
                           for ddl in ITEMS[index]["predicted_schema"]]
        assert payload == {
            "query": ITEMS[index]["question"], "history": [],
            "schema": {field: [table[field] for table in expected_tables]
                       for field in ("table_name", "schema", "description")},
        }
        assert LlmBasedGenerateSqlRequest.model_validate(payload)
        assert str(request.url) == "https://server.test/prefix" + endpoint
        assert request.extensions["timeout"] == {"connect": 10.0, "read": 300.0, "write": 300.0, "pool": 300.0}
        return response(ITEMS[index]["sql"])

    report = run(files, handle, endpoint=endpoint, base_url="https://server.test/prefix/")
    assert len(requests) == 3 and report["state"] == "complete"
    assert report["summary"]["exact_match_rate"] == report["summary"]["normalized_exact_match_rate"] == 1
    assert report["summary"]["prediction_parse_success_rate"] == 1
    assert report["results"][0]["input_table_names"] == ["B", "A", "B"]
    assert report["configuration"]["dataset_sha256"] == hashlib.sha256(before[0]).hexdigest()
    assert report["configuration"]["catalog_sha256"] == hashlib.sha256(before[1]).hexdigest()
    saved = output_path(files).read_text()
    assert "CREATE TABLE" not in saved and "DO NOT SEND GOLD DDL" not in saved
    assert "첫 질문" in saved and json.loads(saved) == report
    assert [path.read_bytes() for path in files] == before


@pytest.mark.parametrize("gold,prediction,normalized_match,parsed", [
    ("SELECT ID FROM A;", " select id from a -- comment\n", True, True),
    ("SELECT ID FROM A", "SELECT ID FROM A;", True, True),
    ("SELECT 'A  B;' FROM A;", "SELECT 'A  B;' FROM A", True, True),
    ("SELECT 'A  B' FROM A", "SELECT 'A B' FROM A", False, True),
    ("SELECT 'ABC' FROM A", "SELECT 'abc' FROM A", False, True),
    ('SELECT "ID" FROM "A"', 'select "ID" from "A";', True, True),
    ('SELECT "ID" FROM A', 'SELECT "id" FROM A', False, True),
    ('SELECT "ID" FROM A', 'SELECT id FROM A', False, True),
    ("SELECT '-- literal' FROM A", "SELECT '-- literal' FROM A -- comment", True, True),
    ("SELECT ID AS X FROM A", "SELECT ID AS Y FROM A", False, True),
    ("WITH C AS (SELECT ID FROM A) SELECT ID FROM C", "with c as (select id from a) select id from c;", True, True),
    ("SELECT NVL(ID, 0) FROM A", "SELECT COALESCE(id, 0) FROM a", True, True),
    ("SELECT 1 UNION SELECT 2", "SELECT 1 UNION SELECT 3", False, True),
    ("SELECT ID FROM A", "SELECT (", False, False),
    ("SELECT 1", "SELECT 1; SELECT 2", False, False),
    ("SELECT 1", "DELETE FROM A", False, False),
    ("SELECT 1", "VACUUM", False, False),
    ("SELECT 1", "hello", False, False),
])
def test_normalization_preserves_literals_identifiers_and_query_structure(gold, prediction, normalized_match, parsed):
    reference = evaluation.analyze_sql(gold, "oracle")
    item = {"expected_sql": gold, "reference_analysis": reference}
    row = {"predicted_sql": prediction}
    evaluation.score_row(row, item, "postgres")
    assert row["metrics"] == {
        "exact_match": gold == prediction, "normalized_exact_match": normalized_match,
        "prediction_parse_success": parsed,
    }
    assert (row["evaluation_errors"]["prediction"] is None) == parsed


def test_reference_parse_failure_and_bad_generated_sql_use_distinct_denominators(files):
    values = deepcopy(ITEMS)
    values += [
        {"id": "bad-gold", "question": "정답 파싱 불가", "sql": "SELECT (", "predicted_schema": [CATALOG[0]["schema"]]},
        {"id": "api-error", "question": "호출 오류", "sql": "SELECT ID FROM B;", "predicted_schema": [CATALOG[1]["schema"]]},
    ]
    files[0].write_text(json.dumps(values))
    predictions = ["select id from a -- note", values[1]["sql"], "SELECT (", "SELECT ID FROM B;"]
    calls = []

    def handle(request):
        index = len(calls)
        calls.append(request)
        return response(predictions[index]) if index < 4 else httpx.Response(500, json={"msg": "mock failure"})

    report = run(files, handle)
    summary = report["summary"]
    assert summary["success_count"] == 4 and summary["failure_count"] == 1 and summary["success_rate"] == 0.8
    assert summary["exact_match_rate"] == 0.25
    assert summary["normalized_evaluated_count"] == 3 and summary["normalized_exact_match_rate"] == 2 / 3
    assert summary["prediction_parse_success_rate"] == 0.75 and summary["prediction_parse_failure_count"] == 1
    assert summary["reference_parse_failure_count"] == summary["reference_normalization_failure_count"] == 1
    assert summary["mean_api_elapsed_time"] == 0.25 and summary["mean_latency_seconds"] >= 0
    assert summary["token_usage_count"] == 4 and summary["total_question_token"] == 400 and summary["total_answer_token"] == 80
    assert summary["mean_question_token"] == 100 and summary["mean_answer_token"] == 20
    assert report["results"][2]["status"] == "success" and report["results"][2]["metrics"]["normalized_exact_match"] is False
    assert report["results"][3]["metrics"]["normalized_exact_match"] is None
    assert report["results"][3]["evaluation_errors"]["reference"]
    assert report["results"][4]["metrics"] is None and report["results"][4]["error"]["type"] == "http_error"


def test_generation_normalization_failure_keeps_parse_success(monkeypatch):
    evaluation.analyze_sql.cache_clear()

    def unsupported(*args, **kwargs):
        raise UnsupportedError("mock unsupported conversion")

    with monkeypatch.context() as context:
        context.setattr(exp.Select, "sql", unsupported)
        result = evaluation.analyze_sql("SELECT 123456", "postgres")
    evaluation.analyze_sql.cache_clear()
    assert result.parse_success and result.normalized_sql is None and result.error.startswith("normalization:")


@pytest.mark.parametrize("kind", ["http", "api", "json", "timeout", "connection"])
def test_request_failures_are_recorded_and_remaining_items_continue(files, kind):
    calls = []

    def handle(request):
        index = len(calls)
        calls.append(request)
        if index != 1:
            return response(ITEMS[index]["sql"])
        if kind == "http":
            return httpx.Response(429, json={"detail": "rate limited"})
        if kind == "api":
            return httpx.Response(200, json={"status": "error", "msg": "upstream failed"})
        if kind == "json":
            return httpx.Response(200, content="{invalid")
        if kind == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        raise httpx.ConnectError("mock connection error", request=request)

    report = run(files, handle)
    assert len(calls) == 3 and report["state"] == "complete"
    assert report["summary"]["success_count"] == 2 and report["summary"]["failure_count"] == 1
    assert report["summary"]["exact_match_rate"] == report["summary"]["normalized_exact_match_rate"] == 1
    row = report["results"][1]
    assert row["metrics"] is row["predicted_sql"] is None and row["error"] and row["latency_seconds"] >= 0
    assert row["http_status"] == (None if kind in ("timeout", "connection") else 429 if kind == "http" else 200)


@pytest.mark.parametrize("payload", [None, [], {}, {"status": "success"}, {"status": "success", "data": None},
                                       {"status": "success", "data": []}, {"status": "success", "data": 1},
                                       {"status": "success", "data": " \n"}, {"status": "success", "data": "```sql\nSELECT 1\n```"}])
def test_bad_api_responses_are_not_scored_as_generated_sql(files, payload):
    report = run(files, lambda request: httpx.Response(200, json=payload))
    summary = report["summary"]
    assert summary["success_count"] == 0 and summary["failure_count"] == 3
    assert summary["exact_match_rate"] is summary["normalized_exact_match_rate"] is summary["prediction_parse_success_rate"] is None
    assert summary["normalized_evaluated_count"] == summary["token_usage_count"] == 0
    assert summary["mean_latency_seconds"] is summary["total_question_token"] is None


def test_optional_metadata_can_be_missing_or_invalid_without_affecting_sql_score(files):
    bodies = [
        {"status": "success", "data": "SELECT 1"},
        {"status": "success", "data": "SELECT 1", "elapsed_time": True, "meta_data": "invalid"},
        {"status": "success", "data": "SELECT 1", "elapsed_time": -1,
         "meta_data": [{"question_token": True, "answer_token": "20", "model_name": " "}]},
    ]
    calls = []

    def handle(request):
        index = len(calls)
        calls.append(request)
        return httpx.Response(200, json=bodies[index])

    report = run(files, handle)
    assert report["summary"]["success_count"] == 3 and report["summary"]["token_usage_count"] == 0
    assert report["summary"]["mean_api_elapsed_time"] is None
    assert all(row["question_token"] is row["answer_token"] is row["model_name"] is None for row in report["results"])


@pytest.mark.parametrize("change", ["root", "question", "sql", "missing_predictions", "empty_predictions", "wrong_type", "blank_ddl", "unknown_ddl"])
def test_invalid_dataset_is_rejected_before_network_or_results_creation(files, change):
    values = deepcopy(ITEMS)
    if change == "root":
        values = {}
    elif change == "question":
        values[2]["question"] = " "
    elif change == "sql":
        values[2]["sql"] = None
    elif change == "missing_predictions":
        del values[2]["predicted_schema"]
    elif change == "empty_predictions":
        values[2]["predicted_schema"] = []
    elif change == "wrong_type":
        values[2]["predicted_schema"] = "DDL"
    elif change == "blank_ddl":
        values[2]["predicted_schema"] = [" "]
    else:
        values[2]["predicted_schema"] = ["CREATE TABLE UNKNOWN (ID INTEGER)"]
    files[0].write_text(json.dumps(values))
    with pytest.raises(ValueError):
        run(files, lambda request: pytest.fail("Invalid inputs must not send requests"))
    assert not output_path(files).exists()


@pytest.mark.parametrize("change", ["root", "description", "duplicate_ddl", "duplicate_name"])
def test_catalog_mapping_must_be_valid_and_unambiguous(files, change):
    values = deepcopy(CATALOG)
    if change == "root":
        values = {}
    elif change == "description":
        values[0]["description"] = ""
    elif change == "duplicate_ddl":
        values.append(dict(values[0], table_name="OTHER_NAME"))
    else:
        values.append(dict(values[0], schema="CREATE TABLE OTHER (ID INTEGER);"))
    files[1].write_text(json.dumps(values))
    with pytest.raises(ValueError):
        run(files, lambda request: pytest.fail("Invalid catalog must not send requests"))
    assert not output_path(files).exists()


@pytest.mark.parametrize("options", [
    {"endpoint": "/retrieve/llm_based_schema"}, {"endpoint": []}, {"base_url": ""},
    {"base_url": "file:///tmp/test"}, {"base_url": "http://server.test/?q=1"},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")}, {"timeout_seconds": True},
    {"resume": "yes"}, {"gold_sql_dialect": "invalid-dialect"}, {"generated_sql_dialect": ""},
])
def test_invalid_configuration_fails_before_any_request_or_output(files, options):
    with pytest.raises(ValueError):
        run(files, lambda request: pytest.fail("Invalid settings must not call the API"), **options)
    assert not output_path(files).exists()


def test_successful_responses_are_reused_even_when_generated_sql_is_wrong_or_unparseable(files):
    calls = []

    def initial(request):
        index = len(calls)
        calls.append(request)
        return httpx.Response(500, json={"msg": "retry"}) if index == 1 else response("SELECT (")

    first = run(files, initial)
    assert first["summary"]["failure_count"] == 1 and first["summary"]["prediction_parse_failure_count"] == 2
    retried = []

    def retry(request):
        retried.append(json.loads(request.content)["query"])
        return response(ITEMS[1]["sql"])

    second = run(files, retry)
    assert retried == [ITEMS[1]["question"]]
    assert len(second["results"]) == 3 and second["summary"]["failure_count"] == 0
    assert second["summary"]["prediction_parse_failure_count"] == 2 and second["summary"]["exact_match_rate"] == 1 / 3
    assert second["results"][0] == first["results"][0]
    third = run(files, lambda request: pytest.fail("All API-successful rows must be reused"))
    assert third["summary"] == second["summary"]


def test_interrupt_saves_finished_rows_and_resumes_pending_questions(files):
    calls = []

    def interrupt(request):
        if calls:
            saved = json.loads(output_path(files).read_text())
            assert saved["summary"]["evaluated_count"] == 1 and saved["summary"]["pending_count"] == 2
            raise KeyboardInterrupt()
        calls.append(request)
        return response(ITEMS[0]["sql"])

    with pytest.raises(KeyboardInterrupt):
        run(files, interrupt)
    saved = json.loads(output_path(files).read_text())
    assert saved["state"] == "interrupted"
    calls.clear()

    def finish(request):
        index = 1 + len(calls)
        calls.append(json.loads(request.content)["query"])
        return response(ITEMS[index]["sql"])

    report = run(files, finish)
    assert calls == [item["question"] for item in ITEMS[1:]]
    assert report["summary"]["exact_match_rate"] == 1 and report["state"] == "complete"


@pytest.mark.parametrize("change", ["base_url", "dataset", "catalog", "gold_sql_dialect", "generated_sql_dialect", "sqlglot_version", "metrics_version"])
def test_resume_rejects_changed_inputs_or_evaluation_settings_without_overwriting(files, change):
    run(files, lambda request: response("SELECT 1"))
    path = output_path(files)
    options = {}
    if change == "base_url":
        options[change] = "https://other.test"
    elif change in ("gold_sql_dialect", "generated_sql_dialect"):
        options[change] = "mysql"
    elif change in ("dataset", "catalog"):
        changed_path = files[0] if change == "dataset" else files[1]
        changed_path.write_text(changed_path.read_text() + "\n")
    else:
        saved = json.loads(path.read_text())
        saved["configuration"][change] = "changed"
        path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="RESUME=False"):
        run(files, lambda request: pytest.fail("Mismatched results must not call the API"), **options)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["json", "type", "duplicate_index", "question", "expected_sql", "input_tables", "negative_latency", "sql", "token"])
def test_corrupt_checkpoint_is_preserved_and_rejected(files, damage):
    run(files, lambda request: response("SELECT 1"))
    path = output_path(files)
    saved = json.loads(path.read_text())
    row = saved["results"][0]
    if damage == "json":
        path.write_text("{bad")
    else:
        if damage == "type":
            saved["evaluation_type"] = "schema_retrieval"
        elif damage == "duplicate_index":
            saved["results"].append(deepcopy(row))
        elif damage == "question":
            row["question"] = "changed"
        elif damage == "expected_sql":
            row["expected_sql"] = "SELECT 999"
        elif damage == "input_tables":
            row["input_table_names"] = ["GOLD_ONLY"]
        elif damage == "negative_latency":
            row["latency_seconds"] = -1
        elif damage == "sql":
            row["predicted_sql"] = None
        else:
            row["question_token"] = True
        path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="RESUME=False"):
        run(files, lambda request: pytest.fail("Corrupt results must not call the API"))
    assert path.read_bytes() == before


def test_reused_sql_is_rescored_without_trusting_cached_metrics(files):
    run(files, lambda request: response("SELECT 1"))
    path = output_path(files)
    saved = json.loads(path.read_text())
    saved["results"][0]["metrics"] = {"exact_match": 999}
    saved["results"][0]["normalized_predicted_sql"] = "forged"
    path.write_text(json.dumps(saved))
    report = run(files, lambda request: pytest.fail("Successful SQL should be scored locally"), timeout_seconds=600)
    assert report["summary"]["exact_match_rate"] == 0
    assert report["results"][0]["normalized_predicted_sql"] == "SELECT 1"
    assert report["configuration"]["timeout_seconds"] == 600


@pytest.mark.parametrize("name", ["other_dataset.json", "experiment.v2.json"])
def test_datasets_with_different_names_keep_independent_results_and_checkpoints(files, name):
    run(files, lambda request: response("SELECT 1"))
    original = output_path(files).read_bytes()
    other_dataset = files[0].with_name(name)
    other_dataset.write_bytes(files[0].read_bytes())
    other_files = (other_dataset, files[1])
    calls = []

    def other_response(request):
        calls.append(request)
        return response("SELECT 2")

    run(other_files, other_response)
    assert len(calls) == len(ITEMS)
    assert output_path(other_files) == files[0].parent / "results" / name
    assert output_path(other_files).is_file() and output_path(files).read_bytes() == original

    def reject(request):
        pytest.fail("Each dataset's completed results must be reused")

    first = run(files, reject)
    other = run(other_files, reject)
    assert first["results"][0]["predicted_sql"] == "SELECT 1"
    assert other["results"][0]["predicted_sql"] == "SELECT 2"


def test_same_dataset_endpoint_change_requires_explicit_fresh_run(files):
    run(files, lambda request: response("SELECT 1"))
    original = output_path(files).read_bytes()
    with pytest.raises(ValueError, match="endpoint.*RESUME=False"):
        run(files, lambda request: pytest.fail("A different endpoint must not reuse results"), endpoint=LOCAL)
    assert output_path(files).read_bytes() == original
    calls = []

    def local_response(request):
        calls.append(request)
        assert request.url.path == LOCAL
        return response("SELECT 2")

    local = run(files, local_response, endpoint=LOCAL, resume=False)
    assert len(calls) == len(ITEMS) and local["configuration"]["endpoint"] == LOCAL
    assert json.loads(output_path(files).read_text()) == local


def test_legacy_endpoint_named_result_is_not_moved_overwritten_or_reused(files):
    legacy = files[0].parent / "results/llm_based_generate_sql_open_ai.json"
    legacy.parent.mkdir()
    legacy.write_text("previous endpoint-named result")
    calls = []

    def fresh_response(request):
        calls.append(request)
        return response("SELECT 1")

    run(files, fresh_response)
    assert len(calls) == len(ITEMS) and legacy.read_text() == "previous endpoint-named result"
    assert output_path(files).name == "질의 데이터.json" and output_path(files).is_file()


def test_fresh_run_replaces_old_result(files):
    run(files, lambda request: response("SELECT 1"))
    output_path(files).write_text("bad old report")
    calls = []

    def fresh(request):
        calls.append(request)
        return response(ITEMS[len(calls) - 1]["sql"])

    report = run(files, fresh, resume=False)
    assert len(calls) == 3 and report["summary"]["exact_match_rate"] == 1


def test_empty_dataset_and_missing_dataset_do_not_call_api(files):
    files[0].write_text("[]")
    report = run(files, lambda request: pytest.fail("No input questions"))
    assert report["results"] == [] and report["state"] == "complete"
    assert report["summary"]["total_count"] == 0 and report["summary"]["exact_match_rate"] is None
    files[0].unlink()
    before = output_path(files).read_bytes()
    with pytest.raises(FileNotFoundError):
        run(files, lambda request: pytest.fail("Missing input must not call API"))
    assert output_path(files).read_bytes() == before


def test_project_relative_paths_work_outside_project_and_owned_client_is_closed(files, monkeypatch):
    monkeypatch.setattr(evaluation, "PROJECT_ROOT", files[0].parent)
    monkeypatch.chdir(files[0].parent.parent)
    real_client = httpx.Client
    created = []
    calls = []
    messages = []

    def handle(request):
        calls.append(request)
        return response(ITEMS[len(calls) - 1]["sql"])

    def client_factory(**options):
        client = real_client(transport=httpx.MockTransport(handle), **options)
        created.append(client)
        return client

    monkeypatch.setattr(evaluation.httpx, "Client", client_factory)
    report = evaluation.evaluate(endpoint=OPEN_AI, dataset_path=files[0].name, catalog_path=files[1].name,
                                 result_dir="results", progress=messages.append)
    assert report["state"] == "complete" and len(calls) == 3 and created[0].is_closed
    assert "SQL 평가 결과 저장" in messages[-2] and "normalized_exact_match_rate" in messages[-1]
    assert output_path(files).is_file()


def test_direct_script_import_mode_and_main_settings(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(evaluation.__file__).parent))
    namespace = runpy.run_path(evaluation.__file__, run_name="sql_evaluation_import_check")
    assert callable(namespace["main"]) and namespace["ENDPOINT"] in {OPEN_AI, LOCAL}


@pytest.mark.parametrize("outcome,code", [("success", 0), ("failure", 1), ("interrupt", 130), ("invalid", 1)])
def test_main_reads_top_level_settings_and_returns_exit_status(monkeypatch, outcome, code):
    monkeypatch.setattr(evaluation, "ENDPOINT", LOCAL)
    monkeypatch.setattr(evaluation, "DATASET_PATH", "evaluation/dataset/custom.json")
    calls = []

    def fake_evaluate(**options):
        calls.append(options)
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        if outcome == "invalid":
            raise ValueError("invalid configuration")
        return {"summary": {"failure_count": int(outcome == "failure")}}

    monkeypatch.setattr(evaluation, "evaluate", fake_evaluate)
    assert evaluation.main() == code
    assert calls[0]["endpoint"] == LOCAL and calls[0]["dataset_path"] == "evaluation/dataset/custom.json"
