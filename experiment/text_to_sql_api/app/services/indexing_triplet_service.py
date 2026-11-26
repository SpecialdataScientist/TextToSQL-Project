import asyncio
import logging
import time
from uuid import uuid4

import faiss
from fastapi.responses import JSONResponse
import httpx

from app.core.config import resolve_embedding_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.schemas.response import IndexingTripletData, IndexingTripletResponse
from app.schemas.triplet_index import IndexedTriplet, make_triplet_id
from app.services.embedding_client import EmbeddingAPIError, create_embedding_client, normalize_embedding_vectors
from app.services.rdf_triplet_store import RdfTripletStore
from app.services.schema_index_store import IndexConflictError
from app.services.triplet_index_store import TripletIndexStore


logger = logging.getLogger(f"{LOGGER_NAME}.indexing_triplet")


class IndexingTripletService:
    def __init__(self, settings, *, client_factory=None, configuration_validator=None,
                 error_redactor=None, operation="indexing_triplet", fallback_rdf_directory=None):
        self.settings = settings
        self.client_factory = client_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.fallback_rdf_directory = fallback_rdf_directory

    def _index_triplet(self, log):
        log.extra["stage"] = "triplet_load"
        if not self.settings.embedding_model_name.strip():
            raise ValueError("embedding_model_name must not be blank")
        source = RdfTripletStore(resolve_project_path(self.settings.rdf_triplet))
        log.info("RDF triplet 로드·검증 시작 source=%s", source.path)
        try:
            triplets = source.load()
        except FileNotFoundError:
            if self.fallback_rdf_directory is None:
                raise
            fallback = RdfTripletStore(resolve_project_path(self.fallback_rdf_directory))
            log.info("기본 RDF 파일 없음: 대체 경로 사용 source=%s fallback=%s reason=file_not_found",
                     source.path, fallback.path)
            source = fallback
            triplets = source.load()
        log.info("RDF triplet 로드·검증 완료 source=%s triplet_count=%s", source.path, len(triplets))

        log.extra["stage"] = "storage_configuration"
        store = TripletIndexStore(resolve_project_path(self.settings.embedded_triplet))
        log.extra["stage"] = "storage_lock"
        log.info("저장소 잠금 대기 directory=%s", store.root)
        lock_start = time.perf_counter()
        with store.locked():
            log.info("저장소 잠금 획득 wait=%.2fs", time.perf_counter() - lock_start)
            log.extra["stage"] = "index_load"
            index, records, metadata = store.load(self.settings.embedding_model_name)
            log.info("기존 triplet 인덱스 로드 완료 existing_count=%s", len(records))

            log.extra["stage"] = "triplet_selection"
            seen = {record["triplet_id"] for record in records}
            additions = []
            for triplet in triplets:
                triplet_id = make_triplet_id(triplet)
                if triplet_id not in seen:
                    seen.add(triplet_id)
                    additions.append(IndexedTriplet(triplet_id=triplet_id, triplet=triplet))
            log.info("신규 triplet 선별 완료 added=%s skipped=%s", len(additions), len(triplets) - len(additions))

            if additions:
                log.extra["stage"] = "embedding_setup"
                resolve_embedding_api_base(self.settings.embedding_api_base)
                with httpx.Client(timeout=60.0) as http_client:
                    client = (self.client_factory or create_embedding_client)(self.settings, http_client=http_client)
                    log.extra["stage"] = "embedding"
                    log.info("Triplet 임베딩 시작 model=%s target_count=%s",
                             self.settings.embedding_model_name, len(additions))
                    embedding_start = time.perf_counter()
                    try:
                        values = client.embed_documents([triplet.embedding_text() for triplet in additions])
                        log.extra["stage"] = "embedding_validation"
                        vectors = normalize_embedding_vectors(values, len(additions))
                    except Exception as error:
                        raise EmbeddingAPIError(f"Embedding API error: {error}") from error
                log.info("Triplet 임베딩·정규화 완료 dimension=%s elapsed=%.2fs",
                         vectors.shape[1], time.perf_counter() - embedding_start)

                log.extra["stage"] = "index_append"
                if index is not None and index.d != vectors.shape[1]:
                    raise IndexConflictError("Embedding dimension differs from the existing triplet index")
                if index is None:
                    index = faiss.IndexFlatIP(vectors.shape[1])
                index.add(vectors)
                records.extend(triplet.model_dump(mode="json") for triplet in additions)

                log.extra["stage"] = "index_save"
                log.info("Triplet 인덱스·메타데이터·원본 저장 시작 directory=%s total=%s", store.directory, len(records))
                metadata = store.save(index, records, self.settings.embedding_model_name)
                log.info("Triplet 인덱스 저장 완료 total=%s", len(records))
            else:
                log.extra["stage"] = "skip"
                log.info("신규 triplet 없음: 임베딩·저장 생략 total=%s", len(records))

            data = IndexingTripletData(
                added_count=len(additions), skipped_count=len(triplets) - len(additions),
                total_count=len(records), added_triplet_ids=[triplet.triplet_id for triplet in additions],
            )
            return data, {
                "embedding_model": self.settings.embedding_model_name,
                "dimension": metadata["dimension"] if metadata else None,
                "index_directory": str(store.directory),
            }

    def _log_failure(self, log, error, elapsed):
        status_code = 409 if isinstance(error, IndexConflictError) else (
            502 if isinstance(error, EmbeddingAPIError) else 500
        )
        error = self.error_redactor(error)
        log.log(logging.WARNING if status_code == 409 else logging.ERROR,
                "요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                exc_info=(type(error), error, error.__traceback__) if status_code >= 500 else None)
        return status_code

    async def index_triplet(self):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s", self.operation)
        indexing = None
        try:
            if self.configuration_validator is not None:
                log.extra["stage"] = "configuration"
                self.configuration_validator(self.settings)
            indexing = asyncio.create_task(asyncio.to_thread(self._index_triplet, log))
            data, metadata = await asyncio.shield(indexing)
        except asyncio.CancelledError:
            if indexing is not None:
                log.warning("요청 취소 감지: 진행 중인 인덱싱 작업 종료 대기")
                outcome, = await asyncio.gather(indexing, return_exceptions=True)
                if isinstance(outcome, BaseException):
                    self._log_failure(log, outcome, time.perf_counter() - start_time)
                else:
                    data, _ = outcome
                    log.warning("요청 취소 후 인덱싱 완료 added=%s skipped=%s total=%s",
                                data.added_count, data.skipped_count, data.total_count)
            raise
        except Exception as error:
            elapsed = time.perf_counter() - start_time
            status_code = self._log_failure(log, error, elapsed)
            response = IndexingTripletResponse(status="error", msg=str(self.error_redactor(error)), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))

        response = IndexingTripletResponse(data=data, meta_data=[metadata], elapsed_time=time.perf_counter() - start_time)
        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 added=%s skipped=%s total=%s elapsed=%.2fs",
                 data.added_count, data.skipped_count, data.total_count, response.elapsed_time)
        return response
