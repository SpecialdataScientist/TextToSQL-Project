import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import resolve_llm_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.prompts.triplet_based_schema_prompt import build_final_selection_messages, build_seed_selection_messages
from app.schemas.llm_output import SchemaTableSelectionOutput
from app.schemas.request import SCHEMA_TABLES, validate_unique_tables
from app.schemas.response import TripletBasedSchemaData, TripletBasedSchemaResponse
from app.services.llm_client import create_llm_client, llm_completion_options
from app.services.rdf_triplet_store import RdfTripletStore


logger = logging.getLogger(f"{LOGGER_NAME}.triplet_based_schema")
AUTO_MAX_HOP_LIMIT = 10


def resolve_max_hop(value: int) -> int:
    if type(value) is not int or value < -1:
        raise ValueError("max_hop must be -1 or a nonnegative integer")
    return AUTO_MAX_HOP_LIMIT if value == -1 else value


class TripletNotFoundError(FileNotFoundError):
    pass


class TripletConflictError(ValueError):
    pass


class TripletSchemaLlmAPIError(RuntimeError):
    pass


def load_triplet_snapshot(source, store, generation_endpoint="/retrieve/generate_triplet"):
    tables = SCHEMA_TABLES.validate_json(source.read_bytes())
    validate_unique_tables(tables)
    try:
        triplets = store.load()
    except FileNotFoundError as error:
        raise TripletNotFoundError(
            f"RDF triplets were not found. Run {generation_endpoint} first."
        ) from error
    except ValidationError as error:
        raise TripletConflictError(f"Invalid RDF triplet file: {error}") from error

    names = {table.table_name for table in tables}
    relations = []
    seen = set()
    for subject, predicate, target in triplets:
        relation = (subject.table_name, predicate, target.table_name)
        if subject.table_name not in names or target.table_name not in names:
            raise TripletConflictError(
                f"RDF triplet references a table outside the catalog: {subject.table_name!r} -> "
                f"{target.table_name!r}. Regenerate with {generation_endpoint}."
            )
        if relation not in seen:
            seen.add(relation)
            relations.append(relation)
    return tables, relations


def expand_triplet_graph(seed_names, tables, relations, max_hop):
    graph_depth = resolve_max_hop(max_hop)
    adjacency = {table.table_name: set() for table in tables}
    for subject, _, target in relations:
        adjacency[subject].add(target)
        adjacency[target].add(subject)

    visited = set(seed_names)
    frontier = set(seed_names)
    for _ in range(graph_depth):
        neighbors = {neighbor for name in frontier for neighbor in adjacency[name]} - visited
        visited.update(neighbors)
        frontier = neighbors
        if not frontier:
            break

    # Catalog order is deterministic; traversal is bidirectional but relations retain their direction.
    candidates = [table for table in tables if table.table_name in visited]
    candidate_relations = [relation for relation in relations if relation[0] in visited and relation[2] in visited]
    return candidates, candidate_relations


class TripletBasedSchemaService:
    def __init__(self, settings, *, client_factory=None, completion_options_factory=None,
                 configuration_validator=None, error_redactor=None, operation="triplet_based_schema",
                 generation_endpoint="/retrieve/generate_triplet",
                 response_model=TripletBasedSchemaResponse, data_model=TripletBasedSchemaData,
                 enable_final_selection=True):
        self.settings = settings
        self.client_factory = client_factory
        self.completion_options_factory = completion_options_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.generation_endpoint = generation_endpoint
        self.response_model = response_model
        self.data_model = data_model
        self.enable_final_selection = enable_final_selection

    def _load_snapshot(self, source, store):
        return load_triplet_snapshot(source, store, self.generation_endpoint)

    @staticmethod
    def _expand_graph(seed_names, tables, relations, max_hop):
        return expand_triplet_graph(seed_names, tables, relations, max_hop)

    @staticmethod
    async def _select_tables(client, options, messages, allowed_names, log):
        inference_start = time.perf_counter()
        log.info("테이블 선택 LLM 호출 시작 model=%s allowed_count=%s", options["model"], len(allowed_names))
        try:
            completion = await client.chat.completions.parse(
                messages=messages, response_format=SchemaTableSelectionOutput, **options,
            )
        except Exception as error:
            raise TripletSchemaLlmAPIError(f"LLM API error: {error}") from error
        try:
            if not completion.choices:
                raise ValueError("LLM returned no choices")
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError(f"Incomplete LLM response: finish_reason={choice.finish_reason!r}")
            if choice.message.refusal:
                raise ValueError("LLM refused table selection")
            output = SchemaTableSelectionOutput.model_validate(choice.message.parsed)
            selected = []
            seen = set()
            for table in output.tables:
                if table.table_name not in allowed_names:
                    raise ValueError(f"LLM selected a table outside the allowed candidates: {table.table_name!r}")
                if table.table_name not in seen:
                    seen.add(table.table_name)
                    selected.append(table.table_name)
        except Exception as error:
            raise TripletSchemaLlmAPIError(f"Invalid LLM table selection response: {error}") from error

        usage = getattr(completion, "usage", None)
        log.info("테이블 선택·검증 완료 selected_count=%s elapsed=%.2fs",
                 len(selected), time.perf_counter() - inference_start)
        return selected, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0

    async def triplet_based_schema(self, request):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s history_count=%s used_history_count=%s",
                 self.operation, len(request.history), min(len(request.history), 3))
        try:
            log.extra["stage"] = "configuration"
            max_hop = request.max_hop
            seed_top_k = getattr(request, "top_k", -1)
            graph_depth = resolve_max_hop(max_hop)
            if self.configuration_validator is not None:
                self.configuration_validator(self.settings)
            options = (self.completion_options_factory or llm_completion_options)(self.settings)
            resolve_llm_api_base(self.settings.llm_api_base)
            source = resolve_project_path(self.settings.dataset_path)
            store = RdfTripletStore(resolve_project_path(self.settings.rdf_triplet))

            log.extra["stage"] = "snapshot_load"
            tables, relations = await asyncio.to_thread(self._load_snapshot, source, store)
            log.info("카탈로그·관계 로드 완료 table_count=%s relation_count=%s", len(tables), len(relations))
            by_name = {table.table_name: table for table in tables}
            seed_names, candidates, selected_names = [], [], []
            question_token = answer_token = 0
            if tables:
                log.extra["stage"] = "llm_configuration"
                client = (self.client_factory or create_llm_client)(self.settings)
                try:
                    async with client:
                        log.extra["stage"] = "seed_selection"
                        seed_names, prompt_tokens, completion_tokens = await self._select_tables(
                            client, options, build_seed_selection_messages(request, tables), set(by_name), log,
                        )
                        if seed_top_k > 0:
                            seed_names = seed_names[:seed_top_k]
                            log.info("시작 테이블 수 제한 top_k=%s seed_count=%s", seed_top_k, len(seed_names))
                        question_token += prompt_tokens
                        answer_token += completion_tokens
                        if seed_names:
                            log.extra["stage"] = "graph_expansion"
                            candidates, candidate_relations = self._expand_graph(seed_names, tables, relations, max_hop)
                            log.info("관계 확장 완료 max_hop=%s depth=%s seed_count=%s expanded_count=%s relation_count=%s",
                                     max_hop, graph_depth, len(seed_names), len(candidates), len(candidate_relations))
                            if self.enable_final_selection:
                                log.extra["stage"] = "final_selection"
                                selected_names, prompt_tokens, completion_tokens = await self._select_tables(
                                    client, options,
                                    build_final_selection_messages(request, candidates, seed_names, candidate_relations),
                                    {table.table_name for table in candidates}, log,
                                )
                                question_token += prompt_tokens
                                answer_token += completion_tokens
                            else:
                                selected_names = [table.table_name for table in candidates]
                                log.info("최종 LLM 재선별 생략: 전체 확장 후보 %s개 반환", len(selected_names))
                        else:
                            log.info("시작 후보 없음: 그래프 확장·최종 LLM 재선별 생략")
                except TripletSchemaLlmAPIError:
                    raise
                except Exception as error:
                    raise TripletSchemaLlmAPIError(f"LLM API error: {error}") from error
            else:
                log.info("빈 카탈로그: LLM 호출 생략")

            log.extra["stage"] = "schema_mapping"
            selected = [by_name[name] for name in selected_names]
            response = self.response_model(
                data=self.data_model(
                    table_name=[table.table_name for table in selected],
                    schema=[table.ddl for table in selected],
                    description=[table.description for table in selected],
                ),
                meta_data=[{
                    "model_name": options["model"],
                    "question_token": question_token,
                    "answer_token": answer_token,
                    "seed_count": len(seed_names),
                    "expanded_count": len(candidates),
                    "retrieved_count": len(selected),
                    "graph_depth": graph_depth,
                }],
                elapsed_time=time.perf_counter() - start_time,
            )
        except asyncio.CancelledError:
            log.warning("검색 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            if isinstance(error, TripletNotFoundError):
                status_code = 404
            elif isinstance(error, TripletConflictError):
                status_code = 409
            elif isinstance(error, TripletSchemaLlmAPIError):
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

        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 seed_count=%s expanded_count=%s retrieved_count=%s elapsed=%.2fs",
                 len(seed_names), len(candidates), len(selected), response.elapsed_time)
        return response
