import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse

from app.core.config import resolve_project_path
from app.core.logging import LOGGER_NAME
from app.schemas.response import HybridOnlyBasedSchemaData, HybridOnlyBasedSchemaResponse
from app.services.embedded_triplet_based_schema_service import (
    TripletIndexNotFoundError,
    embed_triplet_query,
    load_triplet_index,
    rank_triplet_tables,
)
from app.services.embedding_client import EmbeddingAPIError
from app.services.rdf_triplet_store import RdfTripletStore
from app.services.schema_index_store import IndexConflictError
from app.services.triplet_based_schema_service import (
    TripletConflictError,
    TripletNotFoundError,
    expand_triplet_graph,
    load_triplet_snapshot,
    resolve_max_hop,
)


logger = logging.getLogger(f"{LOGGER_NAME}.hybrid_only_based_schema")


class HybridOnlyBasedSchemaService:
    def __init__(self, settings, *, client_factory=None, configuration_validator=None, error_redactor=None,
                 operation="hybrid_only_based_schema", indexing_endpoint="/retrieve/indexing_triplet",
                 generation_endpoint="/retrieve/generate_triplet",
                 response_model=HybridOnlyBasedSchemaResponse, data_model=HybridOnlyBasedSchemaData,
                 fallback_rdf_directory=None):
        self.settings = settings
        self.client_factory = client_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.indexing_endpoint = indexing_endpoint
        self.generation_endpoint = generation_endpoint
        self.response_model = response_model
        self.data_model = data_model
        self.fallback_rdf_directory = fallback_rdf_directory

    def _search_schema(self, request, log):
        graph_depth = resolve_max_hop(request.max_hop)
        index, records = load_triplet_index(self.settings, log, self.indexing_endpoint)

        # The same catalog snapshot maps both embedding seeds and expanded RDF tables.
        log.extra["stage"] = "snapshot_load"
        source = resolve_project_path(self.settings.dataset_path)
        store = RdfTripletStore(resolve_project_path(self.settings.rdf_triplet))
        log.info("RDF 관계 로드·검증 시작 source=%s", store.path)
        try:
            tables, relations = load_triplet_snapshot(source, store, self.generation_endpoint)
        except TripletNotFoundError:
            if self.fallback_rdf_directory is None:
                raise
            fallback = RdfTripletStore(resolve_project_path(self.fallback_rdf_directory))
            log.info("기본 RDF 파일 없음: 대체 경로 사용 source=%s fallback=%s reason=file_not_found",
                     store.path, fallback.path)
            store = fallback
            tables, relations = load_triplet_snapshot(source, store, self.generation_endpoint)
        by_name = {table.table_name: table for table in tables}
        indexed_names = {record["triplet"][position]["table_name"] for record in records for position in (0, 2)}
        unmapped_count = len(indexed_names - by_name.keys())
        log.info("카탈로그·관계 로드 완료 source=%s table_count=%s relation_count=%s unmapped_table_count=%s",
                 store.path, len(tables), len(relations), unmapped_count)

        seed_names, candidates = [], []
        if index.ntotal and indexed_names.intersection(by_name):
            vectors = embed_triplet_query(self.settings, request, index, log, self.client_factory)
            log.extra["stage"] = "seed_selection"
            ranked_names = rank_triplet_tables(index, records, vectors, by_name)
            seed_names = ranked_names if request.top_k == -1 else ranked_names[:request.top_k]
            log.info("임베딩 시작 테이블 선택 완료 top_k=%s seed_count=%s table_names=%s",
                     request.top_k, len(seed_names), seed_names)
            log.extra["stage"] = "graph_expansion"
            candidates, _ = expand_triplet_graph(seed_names, tables, relations, request.max_hop)
            log.info("RDF 관계 확장 완료 max_hop=%s depth=%s expanded_count=%s",
                     request.max_hop, graph_depth, len(candidates))
        else:
            log.extra["stage"] = "skip"
            log.info("검색 가능한 시작 테이블 없음: 질의 임베딩·관계 확장 생략")

        log.extra["stage"] = "schema_mapping"
        data = self.data_model(
            table_name=[table.table_name for table in candidates],
            schema=[table.ddl for table in candidates],
            description=[table.description for table in candidates],
        )
        metadata = {
            "embedding_model": self.settings.embedding_model_name,
            "top_k": request.top_k,
            "seed_count": len(seed_names),
            "expanded_count": len(candidates),
            "retrieved_count": len(candidates),
            "graph_depth": graph_depth,
            "unmapped_table_count": unmapped_count,
        }
        return data, metadata

    async def hybrid_only_based_schema(self, request):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s top_k=%s max_hop=%s "
                 "history_count=%s used_history_count=%s",
                 self.operation, request.top_k, request.max_hop, len(request.history), min(len(request.history), 3))
        try:
            if self.configuration_validator is not None:
                log.extra["stage"] = "configuration"
                self.configuration_validator(self.settings)
            data, metadata = await asyncio.to_thread(self._search_schema, request, log)
        except asyncio.CancelledError:
            log.warning("검색 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            if isinstance(error, (TripletIndexNotFoundError, TripletNotFoundError)):
                status_code = 404
            elif isinstance(error, (IndexConflictError, TripletConflictError)):
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
            data=data, meta_data=[metadata], elapsed_time=time.perf_counter() - start_time,
        )
        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 seed_count=%s expanded_count=%s retrieved_count=%s elapsed=%.2fs",
                 metadata["seed_count"], metadata["expanded_count"], metadata["retrieved_count"], response.elapsed_time)
        return response
