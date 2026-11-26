import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse
import httpx
import numpy as np

from app.core.config import resolve_embedding_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.schemas.response import EmbeddingBasedSchemaData, EmbeddingBasedSchemaResponse
from app.services.embedding_client import EmbeddingAPIError, create_embedding_client, normalize_embedding_vectors
from app.services.schema_index_store import IndexConflictError, SchemaIndexStore


logger = logging.getLogger(f"{LOGGER_NAME}.embedding_based_schema")


class SchemaIndexNotFoundError(FileNotFoundError):
    pass


class EmbeddingBasedSchemaService:
    def __init__(self, settings, *, client_factory=None, configuration_validator=None,
                 error_redactor=None, operation="embedding_based_schema",
                 indexing_endpoint="/retrieve/indexing_schema"):
        self.settings = settings
        self.client_factory = client_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.indexing_endpoint = indexing_endpoint

    def _search_schema(self, request, log):
        log.extra["stage"] = "storage_configuration"
        store = SchemaIndexStore(resolve_project_path(self.settings.embedded_schema))

        log.extra["stage"] = "storage_lock"
        log.info("저장소 공유 잠금 대기 directory=%s", store.root)
        lock_start = time.perf_counter()
        try:
            with store.locked(shared=True):
                log.info("저장소 공유 잠금 획득 wait=%.2fs", time.perf_counter() - lock_start)
                log.extra["stage"] = "index_load"
                log.info("검색 인덱스 로드 시작 directory=%s", store.directory)
                index, records, _ = store.load(self.settings.embedding_model_name)
        except FileNotFoundError as error:
            raise SchemaIndexNotFoundError(
                f"Schema index was not found. Run {self.indexing_endpoint} first."
            ) from error
        if index is None:
            raise SchemaIndexNotFoundError(
                f"Schema index was not found. Run {self.indexing_endpoint} first."
            )
        log.info("검색 인덱스 로드 완료 total=%s dimension=%s", index.ntotal, index.d)

        # The index and records are an independent in-memory snapshot. Release
        # the shared lock before making network requests or performing search.
        if index.ntotal == 0:
            log.extra["stage"] = "skip"
            log.info("빈 인덱스: 질의 임베딩 및 검색 생략")
            return EmbeddingBasedSchemaData(table_name=[], schema=[], description=[])

        log.extra["stage"] = "embedding_setup"
        embedding_text = request.embedding_text()
        log.info("질의 임베딩 준비 model=%s query_length=%s history_count=%s "
                 "used_history_count=%s embedding_text_length=%s",
                 self.settings.embedding_model_name, len(request.query), len(request.history),
                 min(len(request.history), 3), len(embedding_text))
        resolve_embedding_api_base(self.settings.embedding_api_base)
        with httpx.Client(timeout=60.0) as http_client:
            client = (self.client_factory or create_embedding_client)(self.settings, http_client=http_client)
            log.extra["stage"] = "embedding"
            log.info("질의 임베딩 요청 시작 model=%s", self.settings.embedding_model_name)
            embedding_start = time.perf_counter()
            try:
                value = client.embed_query(embedding_text)
                log.extra["stage"] = "embedding_validation"
                vectors = normalize_embedding_vectors([value], 1)
            except Exception as error:
                raise EmbeddingAPIError(f"Embedding API error: {error}") from error
        if vectors.shape[1] != index.d:
            raise IndexConflictError("Query embedding dimension differs from the stored schema index")
        log.info("질의 임베딩·정규화 완료 dimension=%s elapsed=%.2fs",
                 vectors.shape[1], time.perf_counter() - embedding_start)

        log.extra["stage"] = "index_search"
        count = min(request.top_k, index.ntotal)
        log.info("스키마 검색 시작 top_k=%s effective_top_k=%s total=%s",
                 request.top_k, count, index.ntotal)
        search_start = time.perf_counter()
        scores, rows = index.search(vectors, count)
        if not np.isfinite(scores).all() or ((rows < 0) | (rows >= len(records))).any():
            raise IndexConflictError("Search results do not match the stored schema records")
        selected = [records[int(row)] for row in rows[0]]
        data = EmbeddingBasedSchemaData(
            table_name=[record["table_name"] for record in selected],
            schema=[record["schema"] for record in selected],
            description=[record["description"] for record in selected],
        )
        log.info("스키마 검색 완료 retrieved_count=%s table_names=%s elapsed=%.2fs",
                 len(selected), data.table_name, time.perf_counter() - search_start)
        return data

    async def embedding_based_schema(self, request):
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
            data = await asyncio.to_thread(self._search_schema, request, log)
        except asyncio.CancelledError:
            log.warning("검색 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            if isinstance(error, SchemaIndexNotFoundError):
                status_code = 404
            elif isinstance(error, IndexConflictError):
                status_code = 409
            elif isinstance(error, EmbeddingAPIError):
                status_code = 502
            else:
                status_code = 500
            error = self.error_redactor(error)
            elapsed = time.perf_counter() - start_time
            log.log(
                logging.WARNING if status_code < 500 else logging.ERROR,
                "요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                exc_info=(type(error), error, error.__traceback__) if status_code >= 500 else None,
            )
            response = EmbeddingBasedSchemaResponse(status="error", msg=str(error), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json", by_alias=True))

        response = EmbeddingBasedSchemaResponse(
            data=data,
            meta_data=[{
                "embedding_model": self.settings.embedding_model_name,
                "top_k": request.top_k,
                "retrieved_count": len(data.table_name),
            }],
            elapsed_time=time.perf_counter() - start_time,
        )
        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 top_k=%s retrieved_count=%s elapsed=%.2fs",
                 request.top_k, len(data.table_name), response.elapsed_time)
        return response
