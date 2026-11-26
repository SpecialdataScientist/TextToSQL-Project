import asyncio
import logging
import time
from uuid import uuid4

import faiss
from fastapi.responses import JSONResponse
import httpx

from app.core.config import resolve_embedding_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.schemas.request import SCHEMA_TABLES, validate_unique_tables
from app.schemas.response import IndexingSchemaData, IndexingSchemaResponse
from app.services.embedding_client import EmbeddingAPIError, create_embedding_client, normalize_embedding_vectors
from app.services.schema_index_store import IndexConflictError, SchemaIndexStore


logger = logging.getLogger(f"{LOGGER_NAME}.indexing_schema")


class IndexingSchemaService:
    def __init__(self, settings, *, client_factory=None, configuration_validator=None,
                 error_redactor=None, operation="indexing_schema"):
        self.settings = settings
        self.client_factory = client_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation

    def _index_schema(self, log):
        log.extra["stage"] = "schema_load"
        source = resolve_project_path(self.settings.dataset_path)
        log.info("스키마 로드·검증 시작 source=%s", source)
        load_start = time.perf_counter()
        tables = SCHEMA_TABLES.validate_json(source.read_bytes())
        validate_unique_tables(tables)
        log.info("스키마 로드·검증 완료 table_count=%s elapsed=%.2fs",
                 len(tables), time.perf_counter() - load_start)

        log.extra["stage"] = "storage_configuration"
        store = SchemaIndexStore(resolve_project_path(self.settings.embedded_schema))

        # The network request runs in this worker thread while holding the lock.
        # Waiting requests re-read the committed index before selecting new names.
        log.extra["stage"] = "storage_lock"
        log.info("저장소 잠금 대기 directory=%s", store.root)
        lock_start = time.perf_counter()
        with store.locked():
            log.info("저장소 잠금 획득 wait=%.2fs", time.perf_counter() - lock_start)

            log.extra["stage"] = "index_load"
            log.info("기존 인덱스 로드 시작 directory=%s", store.directory)
            index, records, metadata = store.load(self.settings.embedding_model_name)
            if index is None:
                log.info("기존 인덱스 없음: 신규 인덱스 사용 existing_count=0 dimension=None")
            else:
                log.info("기존 인덱스 로드 완료 existing_count=%s dimension=%s", len(records), index.d)

            log.extra["stage"] = "table_selection"
            existing_names = {record["table_name"] for record in records}
            additions = [table for table in tables if table.table_name not in existing_names]
            log.info("신규 테이블 선별 완료 added=%s skipped=%s added_tables=%s",
                     len(additions), len(tables) - len(additions),
                     [table.table_name for table in additions])
            if additions:
                log.extra["stage"] = "embedding_setup"
                log.info("임베딩 클라이언트 준비 model=%s target_count=%s",
                         self.settings.embedding_model_name, len(additions))
                resolve_embedding_api_base(self.settings.embedding_api_base)
                with httpx.Client(timeout=60.0) as http_client:
                    client = (self.client_factory or create_embedding_client)(self.settings, http_client=http_client)
                    log.extra["stage"] = "embedding"
                    log.info("임베딩 요청 시작 model=%s target_count=%s",
                             self.settings.embedding_model_name, len(additions))
                    embedding_start = time.perf_counter()
                    try:
                        values = client.embed_documents([table.embedding_text() for table in additions])
                        log.extra["stage"] = "embedding_validation"
                        vectors = normalize_embedding_vectors(values, len(additions))
                    except Exception as error:
                        raise EmbeddingAPIError(f"Embedding API error: {error}") from error
                log.info("임베딩·정규화 완료 vector_count=%s dimension=%s elapsed=%.2fs",
                         len(vectors), vectors.shape[1], time.perf_counter() - embedding_start)

                log.extra["stage"] = "index_append"
                log.info("인덱스 추가 시작 existing_count=%s added=%s", len(records), len(additions))
                if index is not None and index.d != vectors.shape[1]:
                    raise IndexConflictError("Embedding dimension differs from the existing schema index")
                if index is None:
                    index = faiss.IndexFlatIP(vectors.shape[1])
                index.add(vectors)
                records.extend(table.model_dump(mode="json", by_alias=True) for table in additions)
                log.info("인덱스 추가 완료 total=%s", len(records))

                log.extra["stage"] = "index_save"
                log.info("인덱스·메타데이터·원본 저장 시작 directory=%s total=%s",
                         store.directory, len(records))
                save_start = time.perf_counter()
                metadata = store.save(index, records, self.settings.embedding_model_name)
                log.info("인덱스·메타데이터·원본 저장 완료 directory=%s total=%s elapsed=%.2fs",
                         store.directory, len(records), time.perf_counter() - save_start)
            else:
                log.extra["stage"] = "skip"
                log.info("신규 테이블 없음: 임베딩 및 인덱스 저장 생략 total=%s", len(records))

            data = IndexingSchemaData(
                added_count=len(additions),
                skipped_count=len(tables) - len(additions),
                total_count=len(records),
                added_tables=[table.table_name for table in additions],
            )
            details = {
                "embedding_model": self.settings.embedding_model_name,
                "dimension": metadata["dimension"] if metadata else None,
                "index_directory": str(store.directory),
            }
            return data, details

    def _log_failure(self, log, error, elapsed):
        status_code = 409 if isinstance(error, IndexConflictError) else (
            502 if isinstance(error, EmbeddingAPIError) else 500
        )
        error = self.error_redactor(error)
        log.log(
            logging.WARNING if status_code == 409 else logging.ERROR,
            "요청 실패 http_status=%s error=%s elapsed=%.2fs",
            status_code, error, elapsed,
            exc_info=(type(error), error, error.__traceback__) if status_code >= 500 else None,
        )
        return status_code

    async def index_schema(self):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s", self.operation)
        # Shield the worker so cancellation cannot release a lock mid-transaction.
        indexing = None
        try:
            if self.configuration_validator is not None:
                log.extra["stage"] = "configuration"
                self.configuration_validator(self.settings)
            indexing = asyncio.create_task(asyncio.to_thread(self._index_schema, log))
            data, metadata = await asyncio.shield(indexing)
        except asyncio.CancelledError:
            if indexing is None:
                raise
            log.warning("요청 취소 감지: 진행 중인 인덱싱 작업 종료 대기 elapsed=%.2fs",
                        time.perf_counter() - start_time)
            outcome, = await asyncio.gather(indexing, return_exceptions=True)
            if isinstance(outcome, BaseException):
                self._log_failure(log, outcome, time.perf_counter() - start_time)
            else:
                data, _ = outcome
                log.warning("요청 취소 후 인덱싱 작업 완료 added=%s skipped=%s total=%s elapsed=%.2fs",
                            data.added_count, data.skipped_count, data.total_count,
                            time.perf_counter() - start_time)
            raise
        except Exception as error:
            elapsed = time.perf_counter() - start_time
            status_code = self._log_failure(log, error, elapsed)
            response = IndexingSchemaResponse(
                status="error", msg=str(self.error_redactor(error)), elapsed_time=elapsed,
            )
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))

        response = IndexingSchemaResponse(
            data=data, meta_data=[metadata], elapsed_time=time.perf_counter() - start_time,
        )
        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 added=%s skipped=%s total=%s elapsed=%.2fs",
                 data.added_count, data.skipped_count, data.total_count, response.elapsed_time)
        return response
