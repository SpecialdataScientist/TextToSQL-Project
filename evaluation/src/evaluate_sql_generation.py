"""Evaluate generated SQL using predicted DDLs; edit the settings below before running."""

from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx
import sqlglot
from sqlglot import Dialect, exp
from sqlglot.errors import ErrorLevel, SqlglotError

if __package__:
    from .evaluate_schema_retrieval import reject_nonfinite_json, save_report, utc_now, valid_seconds
else:
    from evaluate_schema_retrieval import reject_nonfinite_json, save_report, utc_now, valid_seconds


# Select one SQL endpoint and any existing dataset with question/sql/predicted_schema.
ENDPOINT = "/generate_sql/llm_based_generate_sql_open_ai"
DATASET_PATH = "evaluation/dataset/embedding_based_schema_test_set.json"
BASE_URL = "http://localhost:8000"
TIMEOUT_SECONDS = 300.0
RESUME = True
GOLD_SQL_DIALECT = "oracle"
GENERATED_SQL_DIALECT = "postgres"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = PROJECT_ROOT / "dataset/db_schema.json"
RESULT_DIR = PROJECT_ROOT / "evaluation/result"
SUPPORTED_ENDPOINTS = frozenset({
    "/generate_sql/llm_based_generate_sql_open_ai",
    "/generate_sql/llm_based_generate_sql",
})
SUPPORTED_SCHEMA_FIELDS = frozenset({"predicted_schema", "schema"})
CANONICAL_DIALECT = "postgres"
FORMAT_VERSION = 1
METRICS_VERSION = 1
RESUME_FIELDS = (
    "endpoint", "base_url", "dataset_sha256", "catalog_sha256", "gold_sql_dialect",
    "generated_sql_dialect", "canonical_dialect", "metrics_version", "sqlglot_version", "schema_field",
)


@dataclass(frozen=True)
class SqlAnalysis:
    parse_success: bool
    normalized_sql: str | None
    error: str | None


class SqlResponseError(ValueError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def project_path(value):
    if not str(value).strip():
        raise ValueError("A nonempty dataset, catalog or output path is required")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


@lru_cache(maxsize=8192)
def analyze_sql(sql, dialect):
    """Parse one query and serialize it conservatively; this never executes SQL."""
    try:
        expressions = [node for node in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE)
                       if node is not None]
        if len(expressions) != 1 or not isinstance(expressions[0], exp.Query):
            raise ValueError("Expected exactly one SELECT/CTE query")
    except (SqlglotError, ValueError) as error:
        return SqlAnalysis(False, None, f"parse: {error}")
    try:
        normalized = expressions[0].sql(
            dialect=CANONICAL_DIALECT, normalize=True, comments=False, pretty=False,
            unsupported_level=ErrorLevel.RAISE,
        )
    except (SqlglotError, ValueError) as error:
        return SqlAnalysis(True, None, f"normalization: {error}")
    return SqlAnalysis(True, normalized, None)


def validate_schema_field(schema_field):
    if not isinstance(schema_field, str) or schema_field not in SUPPORTED_SCHEMA_FIELDS:
        raise ValueError("schema_field must be predicted_schema or schema")


def load_inputs(dataset_path, catalog_path, gold_sql_dialect, *, schema_field="predicted_schema"):
    validate_schema_field(schema_field)
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes, parse_constant=reject_nonfinite_json)
    if not isinstance(catalog, list):
        raise ValueError("Catalog must be a JSON list")
    by_ddl, names = {}, set()
    for index, table in enumerate(catalog):
        if not isinstance(table, dict) or any(
            not isinstance(table.get(field), str) or not table[field].strip()
            for field in ("table_name", "schema", "description")
        ):
            raise ValueError(f"Invalid catalog table at index {index}")
        if table["table_name"] in names or table["schema"] in by_ddl:
            raise ValueError("Catalog table names and DDLs must each be unique")
        names.add(table["table_name"])
        by_ddl[table["schema"]] = table

    dataset_bytes = dataset_path.read_bytes()
    values = json.loads(dataset_bytes, parse_constant=reject_nonfinite_json)
    if not isinstance(values, list):
        raise ValueError("Dataset must be a JSON list")
    items = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or any(
            not isinstance(value.get(field), str) or not value[field].strip() for field in ("question", "sql")
        ):
            raise ValueError(f"Dataset item {index}: question and sql must be nonblank strings")
        ddls = value.get(schema_field)
        if (not isinstance(ddls, list) or not ddls
                or any(not isinstance(ddl, str) or not ddl.strip() for ddl in ddls)):
            raise ValueError(f"Dataset item {index}: {schema_field} must be a nonempty DDL string list")
        tables = []
        for position, ddl in enumerate(ddls):
            if ddl not in by_ddl:
                raise ValueError(f"Dataset item {index}, {schema_field}[{position}]: DDL not found in catalog")
            tables.append(by_ddl[ddl])
        items.append({
            "id": value.get("id", index), "category": value.get("category"),
            "question": value["question"], "expected_sql": value["sql"],
            "request_schema": {key: [table[key] for table in tables]
                               for key in ("table_name", "schema", "description")},
            "reference_analysis": analyze_sql(value["sql"], gold_sql_dialect),
        })
    return items, hashlib.sha256(dataset_bytes).hexdigest(), hashlib.sha256(catalog_bytes).hexdigest()


def make_configuration(endpoint, base_url, timeout_seconds, gold_sql_dialect, generated_sql_dialect,
                       *, schema_field="predicted_schema"):
    validate_schema_field(schema_field)
    if not isinstance(endpoint, str) or endpoint not in SUPPORTED_ENDPOINTS:
        raise ValueError(f"Unsupported endpoint: {endpoint}. Choose one of {sorted(SUPPORTED_ENDPOINTS)}")
    try:
        url = httpx.URL(base_url.strip())
    except (AttributeError, httpx.InvalidURL) as error:
        raise ValueError("BASE_URL must be an HTTP(S) server URL") from error
    if url.scheme not in ("http", "https") or not url.host or url.query or url.fragment:
        raise ValueError("BASE_URL must be an HTTP(S) server URL without a query or fragment")
    if not valid_seconds(timeout_seconds) or timeout_seconds == 0:
        raise ValueError("TIMEOUT_SECONDS must be positive and finite")
    for dialect in (gold_sql_dialect, generated_sql_dialect):
        if not isinstance(dialect, str) or not dialect.strip():
            raise ValueError("SQL dialects must be nonblank names")
        Dialect.get_or_raise(dialect)
    return {
        "endpoint": endpoint, "base_url": str(url).rstrip("/"), "timeout_seconds": timeout_seconds,
        "gold_sql_dialect": gold_sql_dialect, "generated_sql_dialect": generated_sql_dialect,
        "canonical_dialect": CANONICAL_DIALECT, "metrics_version": METRICS_VERSION,
        "sqlglot_version": sqlglot.__version__, "schema_field": schema_field,
    }


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def valid_sql_text(value):
    return isinstance(value, str) and bool(value.strip()) and not value.lstrip().startswith("```")


def parse_response(response):
    if not response.is_success:
        raise SqlResponseError("http_error", f"HTTP {response.status_code}: {response.text}")
    try:
        payload = response.json(parse_constant=reject_nonfinite_json)
    except ValueError as error:
        raise SqlResponseError("invalid_response", f"Invalid response JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SqlResponseError("invalid_response", "Response must be a JSON object")
    if payload.get("status") != "success":
        raise SqlResponseError("api_error", str(payload.get("msg") or "API status is not success"))
    if not valid_sql_text(payload.get("data")):
        raise SqlResponseError("invalid_response", "Response data must be a nonblank SQL string without Markdown fences")
    metadata = payload.get("meta_data")
    metadata = next((entry for entry in metadata if isinstance(entry, dict)), {}) if isinstance(metadata, list) else {}
    model = metadata.get("model_name")
    elapsed = payload.get("elapsed_time")
    return {
        "predicted_sql": payload["data"],
        "model_name": model if isinstance(model, str) and model.strip() else None,
        "question_token": metadata.get("question_token") if nonnegative_integer(metadata.get("question_token")) else None,
        "answer_token": metadata.get("answer_token") if nonnegative_integer(metadata.get("answer_token")) else None,
        "api_elapsed_time": elapsed if valid_seconds(elapsed) else None,
    }


def score_row(row, item, generated_sql_dialect):
    reference = item["reference_analysis"]
    prediction = analyze_sql(row["predicted_sql"], generated_sql_dialect)
    row.update(
        normalized_expected_sql=reference.normalized_sql,
        normalized_predicted_sql=prediction.normalized_sql,
        metrics={
            "exact_match": item["expected_sql"] == row["predicted_sql"],
            "normalized_exact_match": (reference.normalized_sql == prediction.normalized_sql
                                       if reference.normalized_sql is not None else None),
            "prediction_parse_success": prediction.parse_success,
        },
        evaluation_errors={"reference": reference.error, "prediction": prediction.error},
    )


def evaluate_item(client, url, item, index, timeout, generated_sql_dialect):
    row = {
        "index": index, "id": item["id"], "category": item["category"], "question": item["question"],
        "expected_sql": item["expected_sql"], "predicted_sql": None,
        "input_table_names": item["request_schema"]["table_name"],
        "normalized_expected_sql": item["reference_analysis"].normalized_sql, "normalized_predicted_sql": None,
        "evaluation_errors": {"reference": item["reference_analysis"].error, "prediction": None},
        "status": "error", "http_status": None, "metrics": None, "error": None,
        "model_name": None, "question_token": None, "answer_token": None, "api_elapsed_time": None,
    }
    request = {"query": item["question"], "history": [], "schema": item["request_schema"]}
    start = time.perf_counter()
    try:
        response = client.post(url, json=request, timeout=timeout)
        row["http_status"] = response.status_code
        row.update(parse_response(response))
        row["status"] = "success"
    except SqlResponseError as error:
        row["error"] = {"type": error.kind, "message": str(error)}
    except httpx.RequestError as error:
        row["error"] = {"type": type(error).__name__, "message": str(error) or type(error).__name__}
    row["latency_seconds"] = time.perf_counter() - start
    if row["status"] == "success":
        score_row(row, item, generated_sql_dialect)
    row["completed_at"] = utc_now()
    return row


def load_checkpoint(path, configuration, items):
    try:
        report = json.loads(path.read_bytes(), parse_constant=reject_nonfinite_json)
        if (not isinstance(report, dict) or report.get("evaluation_type") != "sql_generation"
                or report.get("format_version") != FORMAT_VERSION):
            raise ValueError("Unsupported SQL evaluation result format")
        previous = report.get("configuration")
        if not isinstance(previous, dict):
            raise ValueError("Missing result configuration")
        # Results written before schema selection was introduced used predicted_schema.
        previous = {"schema_field": "predicted_schema", **previous}
        mismatches = [field for field in RESUME_FIELDS if previous.get(field) != configuration[field]]
        if mismatches:
            raise ValueError(f"Result configuration differs: {', '.join(mismatches)}")
        if not isinstance(report.get("results"), list):
            raise ValueError("Result entries must be a list")
        rows = {}
        for row in report["results"]:
            if not isinstance(row, dict):
                raise ValueError("Invalid result entry")
            index = row.get("index")
            if type(index) is not int or not 0 <= index < len(items) or index in rows:
                raise ValueError("Invalid or duplicate result index")
            item = items[index]
            if (row.get("id") != item["id"] or row.get("question") != item["question"]
                    or row.get("category") != item["category"] or row.get("expected_sql") != item["expected_sql"]
                    or row.get("input_table_names") != item["request_schema"]["table_name"]):
                raise ValueError(f"Result item {index} differs from the dataset or catalog")
            if row.get("status") not in ("success", "error") or not valid_seconds(row.get("latency_seconds")):
                raise ValueError(f"Invalid result status or timing at item {index}")
            if (row.get("api_elapsed_time") is not None and not valid_seconds(row["api_elapsed_time"])):
                raise ValueError(f"Invalid server timing at item {index}")
            for field in ("question_token", "answer_token"):
                if field not in row or (row[field] is not None and not nonnegative_integer(row[field])):
                    raise ValueError(f"Invalid {field} at item {index}")
            row.setdefault("api_elapsed_time", None)
            if row["status"] == "success":
                if not valid_sql_text(row.get("predicted_sql")):
                    raise ValueError(f"Invalid cached SQL at item {index}")
                score_row(row, item, configuration["generated_sql_dialect"])
                row["error"] = None
            else:
                if not isinstance(row.get("error"), dict):
                    raise ValueError(f"Missing error information at item {index}")
                row["predicted_sql"], row["metrics"] = None, None
            rows[index] = row
        return rows
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f"Cannot resume {path}: {error}. Set RESUME=False to start a new evaluation.") from error


def summarize(items, rows):
    successful = [row for row in rows.values() if row["status"] == "success"]
    count = len(successful)
    normalized = [row for row in successful if row["metrics"]["normalized_exact_match"] is not None]
    server_times = [row["api_elapsed_time"] for row in successful if row["api_elapsed_time"] is not None]
    usage = [row for row in successful if row["question_token"] is not None and row["answer_token"] is not None]
    return {
        "total_count": len(items), "evaluated_count": len(rows), "success_count": count,
        "failure_count": len(rows) - count, "pending_count": len(items) - len(rows),
        "success_rate": count / len(rows) if rows else None,
        "exact_match_rate": sum(row["metrics"]["exact_match"] for row in successful) / count if count else None,
        "normalized_evaluated_count": len(normalized),
        "normalized_exact_match_rate": (sum(row["metrics"]["normalized_exact_match"] for row in normalized)
                                        / len(normalized)) if normalized else None,
        "prediction_parse_success_rate": (sum(row["metrics"]["prediction_parse_success"] for row in successful)
                                          / count) if count else None,
        "prediction_parse_failure_count": sum(not row["metrics"]["prediction_parse_success"] for row in successful),
        "reference_parse_failure_count": sum(not item["reference_analysis"].parse_success for item in items),
        "reference_normalization_failure_count": sum(item["reference_analysis"].normalized_sql is None for item in items),
        "mean_latency_seconds": sum(row["latency_seconds"] for row in successful) / count if count else None,
        "mean_api_elapsed_time": sum(server_times) / len(server_times) if server_times else None,
        "token_usage_count": len(usage),
        "total_question_token": sum(row["question_token"] for row in usage) if usage else None,
        "total_answer_token": sum(row["answer_token"] for row in usage) if usage else None,
        "mean_question_token": sum(row["question_token"] for row in usage) / len(usage) if usage else None,
        "mean_answer_token": sum(row["answer_token"] for row in usage) / len(usage) if usage else None,
    }


def evaluate(
    *, endpoint=ENDPOINT, dataset_path=DATASET_PATH, base_url=BASE_URL, timeout_seconds=TIMEOUT_SECONDS,
    resume=RESUME, gold_sql_dialect=GOLD_SQL_DIALECT, generated_sql_dialect=GENERATED_SQL_DIALECT,
    catalog_path=CATALOG_PATH, result_dir=RESULT_DIR, client=None, progress=print,
    schema_field="predicted_schema",
):
    """Run the chosen dataset against one API endpoint; injected clients enable offline tests."""
    if type(resume) is not bool:
        raise ValueError("RESUME must be True or False")
    configuration = make_configuration(endpoint, base_url, timeout_seconds, gold_sql_dialect, generated_sql_dialect,
                                       schema_field=schema_field)
    dataset_path, catalog_path = project_path(dataset_path), project_path(catalog_path)
    items, dataset_digest, catalog_digest = load_inputs(dataset_path, catalog_path, gold_sql_dialect,
                                                      schema_field=schema_field)
    configuration.update(dataset_path=str(dataset_path), catalog_path=str(catalog_path),
                         dataset_sha256=dataset_digest, catalog_sha256=catalog_digest)
    output = project_path(result_dir) / f"{dataset_path.stem}.json"
    rows = load_checkpoint(output, configuration, items) if resume and output.exists() else {}
    report = {"format_version": FORMAT_VERSION, "evaluation_type": "sql_generation",
              "configuration": configuration, "started_at": utc_now()}

    def checkpoint(state):
        report.update(state=state, updated_at=utc_now(), summary=summarize(items, rows),
                      results=[rows[index] for index in sorted(rows)])
        save_report(output, report)

    pending = [index for index in range(len(items)) if index not in rows or rows[index]["status"] != "success"]
    progress(f"SQL 평가: {endpoint}, 전체 {len(items)}개, 성공 결과 재사용 {len(items) - len(pending)}개, 호출 예정 {len(pending)}개")
    checkpoint("running")
    timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
    try:
        if pending:
            with (httpx.Client(timeout=timeout) if client is None else nullcontext(client)) as http_client:
                for index in pending:
                    row = evaluate_item(http_client, configuration["base_url"] + endpoint, items[index], index,
                                        timeout, generated_sql_dialect)
                    rows[index] = row
                    checkpoint("running")
                    detail = (f"exact={row['metrics']['exact_match']} normalized={row['metrics']['normalized_exact_match']} "
                              f"parsed={row['metrics']['prediction_parse_success']}" if row["status"] == "success"
                              else f"{row['error']['type']}: {row['error']['message']}")
                    progress(f"[{index + 1}/{len(items)}] {row['id']} {row['status']} {detail}")
    except KeyboardInterrupt:
        checkpoint("interrupted")
        progress(f"SQL 평가 중단. 저장된 결과로 이어서 실행할 수 있습니다: {output}")
        raise
    checkpoint("complete")
    progress(f"SQL 평가 결과 저장: {output}")
    progress(json.dumps(report["summary"], ensure_ascii=False, indent=2, allow_nan=False))
    return report


def main():
    try:
        report = evaluate(endpoint=ENDPOINT, dataset_path=DATASET_PATH, base_url=BASE_URL,
                          timeout_seconds=TIMEOUT_SECONDS, resume=RESUME,
                          gold_sql_dialect=GOLD_SQL_DIALECT, generated_sql_dialect=GENERATED_SQL_DIALECT,
                          catalog_path=CATALOG_PATH, result_dir=RESULT_DIR)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as error:
        print(f"SQL 평가 실행 실패: {error}", file=sys.stderr)
        return 1
    return 1 if report["summary"]["failure_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
