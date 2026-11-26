import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse

from app.core.logging import LOGGER_NAME
from app.prompts.llm_based_generate_sql_prompt import build_sql_generation_messages
from app.schemas.llm_output import GeneratedSqlOutput
from app.schemas.response import LlmBasedGenerateSqlResponse
from app.services.llm_client import create_llm_client, llm_completion_options


logger = logging.getLogger(f"{LOGGER_NAME}.llm_based_generate_sql")


class LlmAPIError(RuntimeError):
    pass


class LlmBasedGenerateSqlService:
    def __init__(self, settings, *, client_factory=None, completion_options_factory=None,
                 configuration_validator=None, error_redactor=None, operation="llm_based_generate_sql"):
        self.settings = settings
        self.client_factory = client_factory
        self.completion_options_factory = completion_options_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation

    async def generate_sql(self, request):
        start_time = time.perf_counter()
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/generate_sql/%s "
                 "history_count=%s used_history_count=%s table_count=%s",
                 self.operation, len(request.history), min(len(request.history), 3), len(request.schema_info.table_name))
        try:
            log.extra["stage"] = "llm_configuration"
            if self.configuration_validator is not None:
                self.configuration_validator(self.settings)
            options = (self.completion_options_factory or llm_completion_options)(self.settings)
            client = (self.client_factory or create_llm_client)(self.settings)

            log.extra["stage"] = "llm_request"
            log.info("SQL 생성 LLM 호출 시작 model=%s dialect=PostgreSQL", options["model"])
            inference_start = time.perf_counter()
            try:
                async with client:
                    completion = await client.chat.completions.parse(
                        messages=build_sql_generation_messages(request),
                        response_format=GeneratedSqlOutput,
                        **options,
                    )
            except Exception as error:
                raise LlmAPIError(f"LLM API error: {error}") from error
            log.info("SQL 생성 LLM 호출 완료 elapsed=%.2fs", time.perf_counter() - inference_start)

            log.extra["stage"] = "sql_validation"
            try:
                if not completion.choices:
                    raise ValueError("LLM returned no choices")
                choice = completion.choices[0]
                if choice.finish_reason != "stop":
                    raise ValueError(f"Incomplete LLM response: finish_reason={choice.finish_reason!r}")
                if choice.message.refusal:
                    raise ValueError(f"LLM refused SQL generation: {choice.message.refusal}")
                output = GeneratedSqlOutput.model_validate(choice.message.parsed)
            except Exception as error:
                raise LlmAPIError(f"Invalid LLM SQL response: {error}") from error
            log.info("SQL 응답 형식 검증 완료 sql_length=%s", len(output.sql))

            usage = completion.usage
            response = LlmBasedGenerateSqlResponse(
                data=output.sql,
                meta_data=[{
                    "model_name": options["model"],
                    "question_token": getattr(usage, "prompt_tokens", 0) or 0,
                    "answer_token": getattr(usage, "completion_tokens", 0) or 0,
                }],
                elapsed_time=time.perf_counter() - start_time,
            )
        except asyncio.CancelledError:
            log.warning("SQL 생성 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            status_code = 502 if isinstance(error, LlmAPIError) else 500
            error = self.error_redactor(error)
            elapsed = time.perf_counter() - start_time
            log.error("요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                      exc_info=(type(error), error, error.__traceback__))
            response = LlmBasedGenerateSqlResponse(status="error", msg=str(error), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))

        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 model=%s sql_length=%s elapsed=%.2fs",
                 options["model"], len(output.sql), response.elapsed_time)
        return response
