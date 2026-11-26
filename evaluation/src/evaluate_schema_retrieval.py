"""Evaluate schema retrieval against question/tables pairs; configure the constants below."""

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import time

import httpx


# Edit these values before running this file. Provider API keys are read by the server.
ENDPOINT = "/retrieve/hybrid_only_based_schema"
BASE_URL = "http://localhost:8000"
TOP_K = 2
MAX_HOP = 2  # Triplet/hybrid retrieval only; -1 allows up to 10 hops.
TIMEOUT_SECONDS = 300.0
RESUME = True

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "evaluation/dataset/test_set.json"
RESULT_DIR = PROJECT_ROOT / "evaluation/result"

EMBEDDING_ENDPOINTS = frozenset({
    "/retrieve/embedding_based_schema",
    "/retrieve/embedded_triplet_based_schema",
    "/retrieve/embedding_based_schema_open_ai",
    "/retrieve/embedded_triplet_based_schema_open_ai",
})
TRIPLET_ENDPOINTS = frozenset({
    "/retrieve/triplet_based_schema",
    "/retrieve/triplet_based_schema_open_ai",
    "/retrieve/triplet_based_schema_open_router",
    "/retrieve/only_triplet_based_schema",
    "/retrieve/only_triplet_based_schema_open_ai",
    "/retrieve/only_triplet_based_schema_open_router",
    "/retrieve/hybrid_only_based_schema",
    "/retrieve/hybrid_only_based_schema_open_ai",
})
SUPPORTED_ENDPOINTS = EMBEDDING_ENDPOINTS | TRIPLET_ENDPOINTS | {
    "/retrieve/llm_based_schema",
    "/retrieve/llm_based_schema_open_ai",
    "/retrieve/llm_based_schema_open_router",
}
FORMAT_VERSION = 1
RESUME_FIELDS = ("endpoint", "base_url", "top_k", "max_hop", "dataset_sha256")


class RetrievalResponseError(ValueError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def reject_nonfinite_json(value):
    raise ValueError(f"Nonfinite JSON value is not allowed: {value}")


def table_names(value, label):
    if not isinstance(value, list) or any(not isinstance(name, str) or not name.strip() for name in value):
        raise ValueError(f"{label} must be a list of nonblank table-name strings")
    # Preserve the original order for inspection; metrics compare sets.
    return list(dict.fromkeys(value))


def load_dataset(path):
    raw = path.read_bytes()
    values = json.loads(raw, parse_constant=reject_nonfinite_json)
    if not isinstance(values, list):
        raise ValueError("Dataset must be a JSON list")
    items = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or not isinstance(value.get("question"), str) or not value["question"].strip():
            raise ValueError(f"Dataset item {index}: question must be a nonblank string")
        expected = table_names(value.get("tables"), f"Dataset item {index}: tables")
        items.append({"id": value.get("id", index), "question": value["question"], "tables": expected})
    return items, hashlib.sha256(raw).hexdigest()


def ratios(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def calculate_metrics(expected, predicted):
    gold, found = set(expected), set(predicted)
    tp, fp, fn = len(gold & found), len(found - gold), len(gold - found)
    return {"tp": tp, "fp": fp, "fn": fn, **ratios(tp, fp, fn), "exact_match": gold == found}


def valid_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def parse_response(response):
    if not response.is_success:
        raise RetrievalResponseError("http_error", f"HTTP {response.status_code}: {response.text}")
    try:
        payload = response.json(parse_constant=reject_nonfinite_json)
    except ValueError as error:
        raise RetrievalResponseError("invalid_response", f"Invalid response JSON: {error}") from error
    if not isinstance(payload, dict):
        raise RetrievalResponseError("invalid_response", "Response must be a JSON object")
    if payload.get("status") != "success":
        raise RetrievalResponseError("api_error", str(payload.get("msg") or "API status is not success"))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RetrievalResponseError("invalid_response", "Response data must be an object")
    fields = (data.get("table_name"), data.get("schema"), data.get("description"))
    if (any(not isinstance(values, list) for values in fields)
            or any(not isinstance(value, str) for values in fields for value in values)
            or len({len(values) for values in fields}) != 1):
        raise RetrievalResponseError("invalid_response", "table_name, schema and description must be equal-length string lists")
    try:
        predicted = table_names(data["table_name"], "Response table_name")
    except ValueError as error:
        raise RetrievalResponseError("invalid_response", str(error)) from error
    elapsed = payload.get("elapsed_time")
    return predicted, elapsed if valid_seconds(elapsed) else None


def make_configuration(endpoint, base_url, top_k, timeout_seconds, dataset_path, dataset_sha256,
                       *, max_hop=MAX_HOP):
    if not isinstance(endpoint, str) or endpoint not in SUPPORTED_ENDPOINTS:
        raise ValueError(f"Unsupported endpoint: {endpoint}. Choose one of {sorted(SUPPORTED_ENDPOINTS)}")
    try:
        url = httpx.URL(base_url.strip())
    except (AttributeError, httpx.InvalidURL) as error:
        raise ValueError("BASE_URL must be an HTTP(S) server URL") from error
    if url.scheme not in ("http", "https") or not url.host or url.query or url.fragment:
        raise ValueError("BASE_URL must be an HTTP(S) server URL without a query or fragment")
    if endpoint in EMBEDDING_ENDPOINTS and (type(top_k) is not int or top_k < 1):
        raise ValueError("TOP_K must be a positive integer for embedding retrieval")
    if endpoint in TRIPLET_ENDPOINTS and (type(top_k) is not int or top_k < -1 or top_k == 0):
        raise ValueError("TOP_K must be -1 or a positive integer for triplet retrieval")
    if endpoint in TRIPLET_ENDPOINTS and (type(max_hop) is not int or max_hop < -1):
        raise ValueError("MAX_HOP must be -1 or a nonnegative integer for triplet retrieval")
    if not valid_seconds(timeout_seconds) or timeout_seconds == 0:
        raise ValueError("TIMEOUT_SECONDS must be positive and finite")
    return {
        "endpoint": endpoint,
        "base_url": str(url).rstrip("/"),
        "top_k": top_k if endpoint in (EMBEDDING_ENDPOINTS | TRIPLET_ENDPOINTS) else None,
        "max_hop": max_hop if endpoint in TRIPLET_ENDPOINTS else None,
        "timeout_seconds": timeout_seconds,
        "dataset_path": str(dataset_path),
        "dataset_sha256": dataset_sha256,
    }


def load_checkpoint(path, configuration, items):
    """Refuse incompatible or damaged results before modifying the file or sending requests."""
    hint = "Set RESUME=False to start a new evaluation."
    try:
        report = json.loads(path.read_bytes(), parse_constant=reject_nonfinite_json)
        if not isinstance(report, dict) or report.get("format_version") != FORMAT_VERSION:
            raise ValueError("Unsupported result format")
        previous = report.get("configuration")
        if not isinstance(previous, dict):
            raise ValueError("Missing result configuration")
        # Earlier triplet evaluations omitted top_k, which means unlimited seeds.
        if configuration["endpoint"] in TRIPLET_ENDPOINTS and previous.get("top_k") is None:
            previous = {**previous, "top_k": -1}
        if configuration["top_k"] is not None and type(previous.get("top_k")) is not int:
            raise ValueError("Missing or invalid result top_k")
        if configuration["max_hop"] is not None and type(previous.get("max_hop")) is not int:
            raise ValueError("Missing or invalid result max_hop")
        mismatches = [field for field in RESUME_FIELDS if previous.get(field) != configuration[field]]
        if mismatches:
            raise ValueError(f"Result configuration differs: {', '.join(mismatches)}")
        results = report.get("results")
        if not isinstance(results, list):
            raise ValueError("Result entries must be a list")
        rows = {}
        for row in results:
            if not isinstance(row, dict):
                raise ValueError("Invalid result entry")
            index = row.get("index")
            if type(index) is not int or not 0 <= index < len(items) or index in rows:
                raise ValueError("Invalid or duplicate result index")
            item = items[index]
            if (row.get("id") != item["id"] or row.get("question") != item["question"]
                    or row.get("expected_tables") != item["tables"]):
                raise ValueError(f"Result item {index} differs from the dataset")
            if (row.get("status") not in ("success", "error") or not valid_seconds(row.get("latency_seconds"))
                    or (row.get("api_elapsed_time") is not None and not valid_seconds(row["api_elapsed_time"]))):
                raise ValueError(f"Invalid result status or timing at item {index}")
            if row["status"] == "success":
                row["predicted_tables"] = table_names(row.get("predicted_tables"), f"Result item {index}")
                row["metrics"] = calculate_metrics(item["tables"], row["predicted_tables"])
                row["error"] = None
            else:
                if not isinstance(row.get("error"), dict):
                    raise ValueError(f"Missing error information at item {index}")
                row["predicted_tables"], row["metrics"] = None, None
            rows[index] = row
        return rows
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Cannot resume {path}: {error}. {hint}") from error


def summarize(items, rows):
    successful = [row for row in rows.values() if row["status"] == "success"]
    count = len(successful)
    totals = {name: sum(row["metrics"][name] for row in successful) for name in ("tp", "fp", "fn")}
    server_times = [row["api_elapsed_time"] for row in successful if row["api_elapsed_time"] is not None]
    return {
        "total_count": len(items),
        "evaluated_count": len(rows),
        "success_count": count,
        "failure_count": len(rows) - count,
        "pending_count": len(items) - len(rows),
        "success_rate": count / len(rows) if rows else None,
        "macro": {name: sum(row["metrics"][name] for row in successful) / count
                  for name in ("precision", "recall", "f1")} if count else None,
        "micro": {**totals, **ratios(**totals)} if count else None,
        "exact_match_rate": sum(row["metrics"]["exact_match"] for row in successful) / count if count else None,
        "mean_latency_seconds": sum(row["latency_seconds"] for row in successful) / count if count else None,
        "mean_api_elapsed_time": sum(server_times) / len(server_times) if server_times else None,
    }


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def evaluate_item(client, url, item, index, top_k, timeout, *, max_hop=None):
    request = {"query": item["question"], "history": []}
    if top_k is not None:
        request["top_k"] = top_k
    if max_hop is not None:
        request["max_hop"] = max_hop
    row = {
        "index": index, "id": item["id"], "question": item["question"],
        "expected_tables": item["tables"], "predicted_tables": None,
        "status": "error", "http_status": None, "metrics": None,
        "api_elapsed_time": None, "error": None,
    }
    start = time.perf_counter()
    try:
        response = client.post(url, json=request, timeout=timeout)
        row["http_status"] = response.status_code
        predicted, api_elapsed = parse_response(response)
        row.update(status="success", predicted_tables=predicted, api_elapsed_time=api_elapsed,
                   metrics=calculate_metrics(item["tables"], predicted))
    except RetrievalResponseError as error:
        row["error"] = {"type": error.kind, "message": str(error)}
    except httpx.RequestError as error:
        row["error"] = {"type": type(error).__name__, "message": str(error) or type(error).__name__}
    row["latency_seconds"] = time.perf_counter() - start
    row["completed_at"] = utc_now()
    return row


def evaluate(
    *, endpoint=ENDPOINT, base_url=BASE_URL, top_k=TOP_K, max_hop=MAX_HOP, timeout_seconds=TIMEOUT_SECONDS,
    resume=RESUME, dataset_path=DATASET_PATH, result_dir=RESULT_DIR, client=None, progress=print,
):
    """Run sequential requests; an injected httpx.Client permits offline testing."""
    if type(resume) is not bool:
        raise ValueError("RESUME must be True or False")
    dataset_path = Path(dataset_path).resolve()
    items, digest = load_dataset(dataset_path)
    configuration = make_configuration(endpoint, base_url, top_k, timeout_seconds, dataset_path, digest,
                                       max_hop=max_hop)
    filename = endpoint.rsplit('/', 1)[-1]
    if configuration["top_k"] is not None:
        filename = f"{filename}_{configuration['top_k']}"
    if configuration["max_hop"] is not None:
        filename = f"{filename}_{configuration['max_hop']}"
    output = Path(result_dir).resolve() / f"{filename}.json"
    rows = load_checkpoint(output, configuration, items) if resume and output.exists() else {}
    report = {"format_version": FORMAT_VERSION, "configuration": configuration, "started_at": utc_now()}

    def checkpoint(state):
        report.update(state=state, updated_at=utc_now(), summary=summarize(items, rows),
                      results=[rows[index] for index in sorted(rows)])
        save_report(output, report)

    pending = [index for index in range(len(items)) if index not in rows or rows[index]["status"] != "success"]
    progress(f"평가 대상: {endpoint}, 전체 {len(items)}개, 성공 결과 재사용 {len(items) - len(pending)}개, 호출 예정 {len(pending)}개")
    checkpoint("running")
    timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
    try:
        if pending:
            with (httpx.Client(timeout=timeout) if client is None else nullcontext(client)) as http_client:
                url = configuration["base_url"] + endpoint
                for index in pending:
                    row = evaluate_item(http_client, url, items[index], index, configuration["top_k"], timeout,
                                        max_hop=configuration["max_hop"])
                    rows[index] = row
                    checkpoint("running")
                    detail = (f"P={row['metrics']['precision']:.4f} R={row['metrics']['recall']:.4f} "
                              f"F1={row['metrics']['f1']:.4f} exact={row['metrics']['exact_match']}"
                              if row["status"] == "success" else f"{row['error']['type']}: {row['error']['message']}")
                    progress(f"[{index + 1}/{len(items)}] {row['id']} {row['status']} {detail}")
    except KeyboardInterrupt:
        checkpoint("interrupted")
        progress(f"평가 중단. 저장된 결과로 이어서 실행할 수 있습니다: {output}")
        raise
    checkpoint("complete")
    progress(f"평가 결과 저장: {output}")
    progress(json.dumps(report["summary"], ensure_ascii=False, indent=2, allow_nan=False))
    return report


def main():
    try:
        report = evaluate(endpoint=ENDPOINT, base_url=BASE_URL, top_k=TOP_K, max_hop=MAX_HOP,
                          timeout_seconds=TIMEOUT_SECONDS, resume=RESUME,
                          dataset_path=DATASET_PATH, result_dir=RESULT_DIR)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as error:
        print(f"평가 실행 실패: {error}", file=sys.stderr)
        return 1
    return 1 if report["summary"]["failure_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
