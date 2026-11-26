"""Evaluate SQL generation using the dataset's gold schema DDLs."""

import sys

if __package__:
    from . import evaluate_sql_generation as sql_evaluation
else:
    import evaluate_sql_generation as sql_evaluation


# Edit the endpoint and dataset before running. The API server reads model credentials.
ENDPOINT = "/generate_sql/llm_based_generate_sql_open_ai"
DATASET_PATH = "evaluation/dataset/gold_schema_test_set.json"
BASE_URL = "http://localhost:8000"
TIMEOUT_SECONDS = 300.0
RESUME = True
GOLD_SQL_DIALECT = "oracle"
GENERATED_SQL_DIALECT = "postgres"

CATALOG_PATH = sql_evaluation.CATALOG_PATH
RESULT_DIR = sql_evaluation.RESULT_DIR


def evaluate(
    *, endpoint=ENDPOINT, dataset_path=DATASET_PATH, base_url=BASE_URL, timeout_seconds=TIMEOUT_SECONDS,
    resume=RESUME, gold_sql_dialect=GOLD_SQL_DIALECT, generated_sql_dialect=GENERATED_SQL_DIALECT,
    catalog_path=CATALOG_PATH, result_dir=RESULT_DIR, client=None, progress=print,
):
    return sql_evaluation.evaluate(
        endpoint=endpoint, dataset_path=dataset_path, base_url=base_url, timeout_seconds=timeout_seconds,
        resume=resume, gold_sql_dialect=gold_sql_dialect, generated_sql_dialect=generated_sql_dialect,
        catalog_path=catalog_path, result_dir=result_dir, client=client, progress=progress,
        schema_field="schema",
    )


def main():
    try:
        report = evaluate(endpoint=ENDPOINT, dataset_path=DATASET_PATH, base_url=BASE_URL,
                          timeout_seconds=TIMEOUT_SECONDS, resume=RESUME,
                          gold_sql_dialect=GOLD_SQL_DIALECT, generated_sql_dialect=GENERATED_SQL_DIALECT,
                          catalog_path=CATALOG_PATH, result_dir=RESULT_DIR)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as error:
        print(f"정답 스키마 SQL 평가 실행 실패: {error}", file=sys.stderr)
        return 1
    return 1 if report["summary"]["failure_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
