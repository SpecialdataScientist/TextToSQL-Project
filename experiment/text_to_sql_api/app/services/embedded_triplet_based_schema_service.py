import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse
import httpx
import numpy as np

from app.core.config import resolve_embedding_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.schemas.request import SCHEMA_TABLES, validate_unique_tables
from app.schemas.response import EmbeddedTripletBasedSchemaData, EmbeddedTripletBasedSchemaResponse
from app.services.embedding_client import EmbeddingAPIError, create_embedding_client, normalize_embedding_vectors
from app.services.schema_index_store import IndexConflictError
from app.services.triplet_index_store import TripletIndexStore


logger = logging.getLogger(f"{LOGGER_NAME}.embedded_triplet_based_schema")


class TripletIndexNotFoundError(FileNotFoundError):
    pass


def load_triplet_index(settings, log, indexing_endpoint="/retrieve/indexing_triplet"):
    log.extra["stage"] = "storage_configuration"
    if not settings.embedding_model_name.strip():
        raise ValueError("embedding_model_name must not be blank")
    store = TripletIndexStore(resolve_project_path(settings.embedded_triplet))
    log.extra["stage"] = "storage_lock"
    log.info("Triplet 저장소 공유 잠금 대기 directory=%s", store.root)
    lock_start = time.perf_counter()
    try:
        with store.locked(shared=True):
            log.info("Triplet 저장소 공유 잠금 획득 wait=%.2fs", time.perf_counter() - lock_start)
            log.extra["stage"] = "index_load"
            index, records, _ = store.load(settings.embedding_model_name)
    except FileNotFoundError as error:
        raise TripletIndexNotFoundError(
            f"Triplet index was not found. Run {indexing_endpoint} first."
        ) from error
    if index is None:
        raise TripletIndexNotFoundError(f"Triplet index was not found. Run {indexing_endpoint} first.")
    log.info("Triplet 인덱스 로드 완료 triplet_count=%s dimension=%s", index.ntotal, index.d)
    return index, records


def embed_triplet_query(settings, request, index, log, client_factory=None):
    log.extra["stage"] = "embedding_setup"
    embedding_text = request.embedding_text()
    log.info("질의 임베딩 준비 model=%s query_length=%s history_count=%s used_history_count=%s embedding_text_length=%s",
             settings.embedding_model_name, len(request.query), len(request.history),
             min(len(request.history), 3), len(embedding_text))
    resolve_embedding_api_base(settings.embedding_api_base)
    with httpx.Client(timeout=60.0) as http_client:
        client = (client_factory or create_embedding_client)(settings, http_client=http_client)
        log.extra["stage"] = "embedding"
        embedding_start = time.perf_counter()
        try:
            vector = client.embed_query(embedding_text)
            log.extra["stage"] = "embedding_validation"
            vectors = normalize_embedding_vectors([vector], 1)
        except Exception as error:
            raise EmbeddingAPIError(f"Embedding API error: {error}") from error
    if vectors.shape[1] != index.d:
        raise IndexConflictError("Query embedding dimension differs from the stored triplet index")
    log.info("질의 임베딩·정규화 완료 dimension=%s elapsed=%.2fs",
             vectors.shape[1], time.perf_counter() - embedding_start)
    return vectors


def rank_triplet_tables(index, records, vectors, allowed_names):
    # Search every triplet so repeated endpoints cannot underfill the requested number of tables.
    scores, rows = index.search(vectors, index.ntotal)
    if (scores.shape != (1, index.ntotal) or rows.shape != (1, index.ntotal)
            or not np.isfinite(scores).all() or not np.issubdtype(rows.dtype, np.integer)
            or ((rows < 0) | (rows >= len(records))).any() or len(set(rows[0].tolist())) != index.ntotal):
        raise IndexConflictError("Search results do not match the stored triplet records")
    table_scores = {}
    for score, row in zip(scores[0], rows[0]):
        triplet = records[int(row)]["triplet"]
        for position in (0, 2):
            name = triplet[position]["table_name"]
            if name in allowed_names:
                table_scores[name] = max(table_scores.get(name, float("-inf")), float(score))
    return sorted(table_scores, key=lambda name: (-table_scores[name], name))


class EmbeddedTripletBasedSchemaService:
    def __init__(
        self, settings, *, client_factory=None, configuration_validator=None, error_redactor=None,
        operation="embedded_triplet_based_schema", indexing_endpoint="/retrieve/indexing_triplet",
        response_model=EmbeddedTripletBasedSchemaResponse, data_model=EmbeddedTripletBasedSchemaData,
    ):
        self.settings = settings
        self.client_factory = client_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.indexing_endpoint = indexing_endpoint
        self.response_model = response_model
        self.data_model = data_model

    def _search_schema(self, request, log):
        index, records = load_triplet_index(self.settings, log, self.indexing_endpoint)

        # Work with a complete in-memory snapshot; release the read lock before network requests.
        if index.ntotal == 0:
            log.extra["stage"] = "skip"
            log.info("빈 triplet 인덱스: 질의 임베딩·검색 생략")
            return self.data_model(table_name=[], schema=[], description=[]), 0

        log.extra["stage"] = "catalog_load"
        source = resolve_project_path(self.settings.dataset_path)
        tables = SCHEMA_TABLES.validate_json(source.read_bytes())
        validate_unique_tables(tables)
        by_name = {table.table_name: table for table in tables}
        indexed_names = {record["triplet"][position]["table_name"] for record in records for position in (0, 2)}
        unmapped_count = len(indexed_names - by_name.keys())
        log.info("원본 카탈로그 매핑 완료 table_count=%s indexed_table_count=%s unmapped_table_count=%s",
                 len(tables), len(indexed_names), unmapped_count)
        if not indexed_names.intersection(by_name):
            log.extra["stage"] = "skip"
            log.info("원본 스키마를 매핑할 테이블 없음: 질의 임베딩·검색 생략")
            return self.data_model(table_name=[], schema=[], description=[]), unmapped_count

        vectors = embed_triplet_query(self.settings, request, index, log, self.client_factory)

        log.extra["stage"] = "index_search"
        search_start = time.perf_counter()
        ranked_names = rank_triplet_tables(index, records, vectors, by_name)
        count = min(request.top_k, len(ranked_names))
        selected = [by_name[name] for name in ranked_names[:count]]
        data = self.data_model(
            table_name=[table.table_name for table in selected],
            schema=[table.ddl for table in selected],
            description=[table.description for table in selected],
        )
        log.info("Triplet 검색·테이블 순위 집계 완료 triplet_count=%s candidate_table_count=%s retrieved_count=%s "
                 "table_names=%s elapsed=%.2fs", index.ntotal, len(ranked_names), len(selected),
                 data.table_name, time.perf_counter() - search_start)
        return data, unmapped_count

    async def embedded_triplet_based_schema(self, request):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s top_k=%s "
                 "history_count=%s used_history_count=%s",
                 self.operation, request.top_k, len(request.history), min(len(request.history), 3))
        try:
            if self.configuration_validator is not None:
                log.extra["stage"] = "configuration"
                self.configuration_validator(self.settings)
            data, unmapped_count = await asyncio.to_thread(self._search_schema, request, log)
        except asyncio.CancelledError:
            log.warning("검색 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            if isinstance(error, TripletIndexNotFoundError):
                status_code = 404
            elif isinstance(error, IndexConflictError):
                status_code = 409
            elif isinstance(error, EmbeddingAPIError):
                status_code = 502
            else:
                status_code = 500
            error = self.error_redactor(error)
            elapsed = time.perf_counter() - start_time
            log.log(logging.WARNING if status_code < 500 else logging.ERROR,
                    "요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                    exc_info=(type(error), error, error.__traceback__) if status_code >= 500 else None)
            response = self.response_model(status="error", msg=str(error), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json", by_alias=True))

        response = self.response_model(
            data=data,
            meta_data=[{
                "embedding_model": self.settings.embedding_model_name,
                "top_k": request.top_k,
                "retrieved_count": len(data.table_name),
                "unmapped_table_count": unmapped_count,
            }],
            elapsed_time=time.perf_counter() - start_time,
        )
        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 top_k=%s retrieved_count=%s unmapped_table_count=%s elapsed=%.2fs",
                 request.top_k, len(data.table_name), unmapped_count, response.elapsed_time)
        return response
