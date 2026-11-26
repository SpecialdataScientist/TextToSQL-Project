from copy import deepcopy
import json
from pathlib import Path
import runpy

import httpx
import pytest

from app.schemas.request import LlmBasedGenerateSqlRequest
from evaluation.src import evaluate_gold_schema_sql_generation as gold_evaluation
from evaluation.src import evaluate_sql_generation as evaluation


OPEN_AI = "/generate_sql/llm_based_generate_sql_open_ai"
LOCAL = "/generate_sql/llm_based_generate_sql"
CATALOG = [
    {"table_name": "A", "schema": "CREATE TABLE A (ID INTEGER);", "description": "A 테이블 설명"},
    {"table_name": "B", "schema": "CREATE TABLE B (ID INTEGER);", "description": "B 테이블 설명"},
]
ITEMS = [
    {"id": "gold0", "category": "join", "question": "  정답 스키마 질의  ", "sql": "SELECT ID FROM A;",
     "tables": ["IGNORE_THIS_LABEL"], "schema": [CATALOG[1]["schema"], CATALOG[0]["schema"], CATALOG[1]["schema"]],
     "predicted_schema": [CATALOG[0]["schema"]], "history": ["DO NOT SEND HISTORY"]},
    {"id": "gold1", "question": "두 번째 질의", "sql": "SELECT ID FROM B;",
     "tables": ["IGNORE_THIS_LABEL"], "schema": [CATALOG[1]["schema"]], "predicted_schema": [CATALOG[0]["schema"]]},
]


def response(sql):
    return httpx.Response(200, json={
        "status": "success", "data": sql, "elapsed_time": 0.5,
        "meta_data": [{"model_name": "test-model", "question_token": 50, "answer_token": 10}],
    })


@pytest.fixture
def files(tmp_path):
    dataset = tmp_path / "gold_schema_test_set.json"
    catalog = tmp_path / "catalog.json"
    dataset.write_text(json.dumps(ITEMS, ensure_ascii=False), encoding="utf-8")
    catalog.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
    return dataset, catalog


def output_path(files, directory="results"):
    return files[0].parent / directory / f"{files[0].stem}.json"


def run(files, handler, *, evaluator=gold_evaluation, directory="results", **options):
    options.setdefault("endpoint", OPEN_AI)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return evaluator.evaluate(dataset_path=files[0], catalog_path=files[1],
                                  result_dir=files[0].parent / directory, client=client,
                                  progress=lambda message: None, **options)


@pytest.mark.parametrize("endpoint", [OPEN_AI, LOCAL])
def test_gold_requests_use_schema_ddls_catalog_metadata_and_preserve_order_and_duplicates(files, endpoint):
    calls = []
    before = [path.read_bytes() for path in files]

    def handle(request):
        index = len(calls)
        calls.append(request)
        tables = [next(table for table in CATALOG if table["schema"] == ddl) for ddl in ITEMS[index]["schema"]]
        payload = json.loads(request.content)
        assert payload == {
            "query": ITEMS[index]["question"], "history": [],
            "schema": {field: [table[field] for table in tables]
                       for field in ("table_name", "schema", "description")},
        }
        assert LlmBasedGenerateSqlRequest.model_validate(payload)
        assert str(request.url) == "https://server.test/prefix" + endpoint
        assert request.extensions["timeout"]["read"] == 300.0
        return response(ITEMS[index]["sql"])

    report = run(files, handle, endpoint=endpoint, base_url="https://server.test/prefix/")
    assert len(calls) == 2 and report["configuration"]["schema_field"] == "schema"
    assert report["results"][0]["input_table_names"] == ["B", "A", "B"]
    assert report["summary"]["exact_match_rate"] == report["summary"]["normalized_exact_match_rate"] == 1
    assert report["summary"]["prediction_parse_success_rate"] == 1
    assert report["summary"]["total_question_token"] == 100 and report["summary"]["total_answer_token"] == 20
    assert output_path(files).name == "gold_schema_test_set.json"
    saved = output_path(files).read_text()
    assert json.loads(saved) == report and "CREATE TABLE" not in saved
    assert [path.read_bytes() for path in files] == before


def test_gold_evaluation_ignores_invalid_predicted_schema(files):
    values = deepcopy(ITEMS)
    for item in values:
        item["predicted_schema"] = ["NOT IN THE CATALOG"]
    files[0].write_text(json.dumps(values))
    report = run(files, lambda request: response("SELECT 1"))
    assert report["summary"]["success_count"] == 2
    assert report["configuration"]["schema_field"] == "schema"


def test_same_dataset_can_explicitly_select_either_schema_field(files):
    for evaluator, field, directory in ((gold_evaluation, "schema", "gold results"),
                                        (evaluation, "predicted_schema", "predicted results")):
        calls = []

        def handle(request):
            index = len(calls)
            calls.append(request)
            assert json.loads(request.content)["schema"]["schema"] == ITEMS[index][field]
            return response(ITEMS[index]["sql"])

        report = run(files, handle, evaluator=evaluator, directory=directory)
        assert report["configuration"]["schema_field"] == field and len(calls) == 2
        assert output_path(files, directory).is_file()


@pytest.mark.parametrize("value", [None, [], "DDL", [None], [" "], ["CREATE TABLE UNKNOWN (ID INTEGER);"]])
def test_invalid_or_missing_gold_schema_never_falls_back_to_valid_predictions(files, value):
    values = deepcopy(ITEMS)
    if value is None:
        del values[1]["schema"]
    else:
        values[1]["schema"] = value
    files[0].write_text(json.dumps(values))
    with pytest.raises(ValueError, match="schema"):
        run(files, lambda request: pytest.fail("Invalid gold DDLs must fail before requests"))
    assert not output_path(files).exists()


@pytest.mark.parametrize("field", [None, [], False, "sql", "tables"])
def test_core_rejects_unsupported_schema_fields_before_loading_inputs(files, field):
    with pytest.raises(ValueError, match="schema_field"):
        run(files, lambda request: pytest.fail("Invalid schema selector must not send requests"),
            evaluator=evaluation, schema_field=field)
    assert not output_path(files).exists()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("same_ddls", [False, True])
def test_gold_and_predicted_checkpoints_cannot_be_mixed_even_if_ddls_are_identical(files, legacy, same_ddls):
    if same_ddls:
        values = deepcopy(ITEMS)
        for item in values:
            item["schema"] = item["predicted_schema"]
        files[0].write_text(json.dumps(values))
    run(files, lambda request: response("SELECT 1"), evaluator=evaluation)
    path = output_path(files)
    if legacy:
        saved = json.loads(path.read_text())
        del saved["configuration"]["schema_field"]
        path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema_field.*RESUME=False"):
        run(files, lambda request: pytest.fail("Gold evaluation must not reuse predicted results"))
    assert path.read_bytes() == before
    gold = run(files, lambda request: response("SELECT 2"), resume=False)
    assert gold["configuration"]["schema_field"] == "schema"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema_field.*RESUME=False"):
        run(files, lambda request: pytest.fail("Predicted evaluation must not reuse gold results"), evaluator=evaluation)
    assert path.read_bytes() == before


def test_legacy_predicted_results_resume_without_api_and_gain_explicit_schema_field(files):
    initial = run(files, lambda request: response("SELECT 1"), evaluator=evaluation)
    path = output_path(files)
    saved = json.loads(path.read_text())
    del saved["configuration"]["schema_field"]
    path.write_text(json.dumps(saved))
    resumed = run(files, lambda request: pytest.fail("Legacy predicted successes should be reused"), evaluator=evaluation)
    assert resumed["configuration"]["schema_field"] == "predicted_schema"
    assert resumed["summary"] == initial["summary"] and resumed["results"] == initial["results"]
    assert json.loads(path.read_text())["configuration"]["schema_field"] == "predicted_schema"


def test_gold_evaluation_retries_api_failures_and_reuses_successful_sql(files):
    calls = []

    def initial(request):
        index = len(calls)
        calls.append(request)
        return response(ITEMS[0]["sql"]) if index == 0 else httpx.Response(429, json={"msg": "mock rate limit"})

    first = run(files, initial)
    assert first["summary"]["success_count"] == first["summary"]["failure_count"] == 1
    retried = []

    def retry(request):
        retried.append(json.loads(request.content)["query"])
        return response(ITEMS[1]["sql"])

    resumed = run(files, retry)
    assert retried == [ITEMS[1]["question"]]
    assert resumed["summary"]["failure_count"] == 0 and resumed["summary"]["exact_match_rate"] == 1
    assert resumed["results"][0] == first["results"][0]


def test_direct_script_import_uses_shared_evaluator_without_running_requests(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(gold_evaluation.__file__).parent))
    namespace = runpy.run_path(gold_evaluation.__file__, run_name="gold_sql_import_check")
    assert callable(namespace["evaluate"]) and callable(namespace["main"])
    assert namespace["ENDPOINT"] in {OPEN_AI, LOCAL}


@pytest.mark.parametrize("outcome,code", [("success", 0), ("failure", 1), ("interrupt", 130), ("invalid", 1)])
def test_gold_main_uses_editable_settings_and_shared_exit_conventions(monkeypatch, outcome, code):
    monkeypatch.setattr(gold_evaluation, "ENDPOINT", LOCAL)
    monkeypatch.setattr(gold_evaluation, "DATASET_PATH", "evaluation/dataset/custom_gold.json")
    monkeypatch.setattr(gold_evaluation, "BASE_URL", "http://local.test:8001")
    monkeypatch.setattr(gold_evaluation, "RESUME", False)
    calls = []

    def fake_evaluate(**options):
        calls.append(options)
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        if outcome == "invalid":
            raise ValueError("invalid gold schema")
        return {"summary": {"failure_count": int(outcome == "failure")}}

    monkeypatch.setattr(gold_evaluation, "evaluate", fake_evaluate)
    assert gold_evaluation.main() == code
    assert calls[0]["endpoint"] == LOCAL and calls[0]["dataset_path"] == "evaluation/dataset/custom_gold.json"
    assert calls[0]["base_url"] == "http://local.test:8001" and calls[0]["resume"] is False
