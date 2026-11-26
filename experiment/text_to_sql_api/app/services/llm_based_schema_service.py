import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse

from app.core.config import resolve_llm_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.prompts.llm_based_schema_prompt import build_schema_selection_messages
from app.schemas.llm_output import SchemaTableSelectionOutput
from app.schemas.request import SCHEMA_TABLES, validate_unique_tables
from app.schemas.response import LlmBasedSchemaData, LlmBasedSchemaResponse
from app.services.llm_client import create_llm_client, llm_completion_options


logger = logging.getLogger(f"{LOGGER_NAME}.llm_based_schema")


class LlmSchemaAPIError(RuntimeError):
    pass


class LlmBasedSchemaService:
    def __init__(self, settings, *, client_factory=None, completion_options_factory=None,
                 configuration_validator=None, error_redactor=None, operation="llm_based_schema",
                 response_model=LlmBasedSchemaResponse, data_model=LlmBasedSchemaData):
        self.settings = settings
        self.client_factory = client_factory
        self.completion_options_factory = completion_options_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation
        self.response_model = response_model
        self.data_model = data_model

    @staticmethod
    def _load_tables(source):
        tables = SCHEMA_TABLES.validate_json(source.read_bytes())
        validate_unique_tables(tables)
        return tables

    @staticmethod
    def _validate_selection(completion, tables):
        try:
            if not completion.choices:
                raise ValueError("LLM returned no choices")
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError(f"Incomplete LLM response: finish_reason={choice.finish_reason!r}")
            if choice.message.refusal:
                raise ValueError("LLM refused table selection")
            output = SchemaTableSelectionOutput.model_validate(choice.message.parsed)
            by_name = {table.table_name: table for table in tables}
            selected = []
            seen = set()
            for table in output.tables:
                if table.table_name not in by_name:
                    raise ValueError(f"LLM selected a table outside the catalog: {table.table_name!r}")
                if table.table_name not in seen:
                    seen.add(table.table_name)
                    selected.append(by_name[table.table_name])
            return selected
        except Exception as error:
            raise LlmSchemaAPIError(f"Invalid LLM table selection response: {error}") from error

    async def llm_based_schema(self, request):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s history_count=%s used_history_count=%s",
                 self.operation, len(request.history), min(len(request.history), 3))
        try:
            log.extra["stage"] = "configuration"
            if self.configuration_validator is not None:
                self.configuration_validator(self.settings)
            options = (self.completion_options_factory or llm_completion_options)(self.settings)
            resolve_llm_api_base(self.settings.llm_api_base)
            source = resolve_project_path(self.settings.dataset_path)

            log.extra["stage"] = "catalog_load"
            tables = await asyncio.to_thread(self._load_tables, source)
            log.info("원본 카탈로그 로드·검증 완료 table_count=%s", len(tables))

            selected = []
            usage = None
            if tables:
                log.extra["stage"] = "llm_configuration"
                client = (self.client_factory or create_llm_client)(self.settings)
                messages = build_schema_selection_messages(request, tables)
                log.extra["stage"] = "llm_request"
                log.info("전체 카탈로그 테이블 선택 LLM 호출 시작 model=%s table_count=%s", options["model"], len(tables))
                inference_start = time.perf_counter()
                try:
                    async with client:
                        completion = await client.chat.completions.parse(
                            messages=messages, response_format=SchemaTableSelectionOutput, **options,
                        )
                except Exception as error:
                    raise LlmSchemaAPIError(f"LLM API error: {error}") from error
                log.info("테이블 선택 LLM 호출 완료 elapsed=%.2fs", time.perf_counter() - inference_start)

                log.extra["stage"] = "selection_validation"
                selected = self._validate_selection(completion, tables)
                usage = getattr(completion, "usage", None)
                log.info("테이블 선택 검증·원본 매핑 완료 retrieved_count=%s", len(selected))
            else:
                log.info("빈 카탈로그: LLM 호출 생략")

            response = self.response_model(
                data=self.data_model(
                    table_name=[table.table_name for table in selected],
                    schema=[table.ddl for table in selected],
                    description=[table.description for table in selected],
                ),
                meta_data=[{
                    "model_name": options["model"],
                    "question_token": getattr(usage, "prompt_tokens", 0) or 0,
                    "answer_token": getattr(usage, "completion_tokens", 0) or 0,
                    "table_count": len(tables),
                    "retrieved_count": len(selected),
                }],
                elapsed_time=time.perf_counter() - start_time,
            )
        except asyncio.CancelledError:
            log.warning("검색 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            status_code = 502 if isinstance(error, LlmSchemaAPIError) else 500
            error = self.error_redactor(error)
            elapsed = time.perf_counter() - start_time
            log.error("요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                      exc_info=(type(error), error, error.__traceback__))
            response = self.response_model(status="error", msg=str(error), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json", by_alias=True))

        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 table_count=%s retrieved_count=%s elapsed=%.2fs",
                 len(tables), len(selected), response.elapsed_time)
        return response
