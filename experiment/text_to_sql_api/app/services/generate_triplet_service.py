import asyncio
import logging
import time
from uuid import uuid4

from fastapi.responses import JSONResponse

from app.core.config import resolve_llm_api_base, resolve_project_path
from app.core.logging import LOGGER_NAME
from app.prompts.generate_triplet_prompt import build_triplet_generation_messages
from app.schemas.llm_output import GeneratedTripletsOutput
from app.schemas.request import SCHEMA_TABLES, validate_unique_tables
from app.schemas.response import GenerateTripletResponse
from app.schemas.triplet import TripletEntity
from app.services.llm_client import create_llm_client, llm_completion_options
from app.services.rdf_triplet_store import RdfTripletStore


logger = logging.getLogger(f"{LOGGER_NAME}.generate_triplet")


class TripletLlmAPIError(RuntimeError):
    pass


class GenerateTripletService:
    def __init__(self, settings, *, client_factory=None, completion_options_factory=None,
                 configuration_validator=None, error_redactor=None, operation="generate_triplet"):
        self.settings = settings
        self.client_factory = client_factory
        self.completion_options_factory = completion_options_factory
        self.configuration_validator = configuration_validator
        self.error_redactor = error_redactor or (lambda error: error)
        self.operation = operation

    @staticmethod
    def _load_tables(source):
        tables = SCHEMA_TABLES.validate_json(source.read_bytes())
        validate_unique_tables(tables)
        return tables

    @staticmethod
    def _validate_triplets(completion, tables):
        try:
            if not completion.choices:
                raise ValueError("LLM returned no choices")
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError(f"Incomplete LLM response: finish_reason={choice.finish_reason!r}")
            if choice.message.refusal:
                raise ValueError("LLM refused triplet generation")
            output = GeneratedTripletsOutput.model_validate(choice.message.parsed)
            entities = {
                table.table_name: TripletEntity(table_name=table.table_name, description=table.description)
                for table in tables
            }
            triplets = []
            seen = set()
            for relation in output.triplets:
                subject, predicate, target = (
                    relation.subject_table_name, relation.predicate, relation.object_table_name,
                )
                if subject not in entities or target not in entities:
                    raise ValueError(f"LLM returned an unknown table: {subject!r} -> {target!r}")
                key = (subject, predicate, target)
                if key not in seen:
                    seen.add(key)
                    triplets.append((entities[subject], predicate, entities[target]))
            return triplets
        except Exception as error:
            raise TripletLlmAPIError(f"Invalid LLM triplet response: {error}") from error

    async def generate_triplet(self):
        start_time = time.perf_counter()
        saving = None
        log = logging.LoggerAdapter(logger, {
            "request_id": uuid4().hex, "stage": "request", "operation": self.operation,
        })
        log.info("요청 시작 method=POST path=/retrieve/%s", self.operation)
        try:
            log.extra["stage"] = "configuration"
            if self.configuration_validator is not None:
                self.configuration_validator(self.settings)
            options = (self.completion_options_factory or llm_completion_options)(self.settings)
            resolve_llm_api_base(self.settings.llm_api_base)
            source = resolve_project_path(self.settings.dataset_path)
            store = RdfTripletStore(resolve_project_path(self.settings.rdf_triplet))

            log.extra["stage"] = "schema_load"
            log.info("스키마 로드·검증 시작 source=%s", source)
            tables = await asyncio.to_thread(self._load_tables, source)
            log.info("스키마 로드·검증 완료 table_count=%s", len(tables))

            usage = None
            triplets = []
            if tables:
                log.extra["stage"] = "llm_configuration"
                client = (self.client_factory or create_llm_client)(self.settings)
                messages = build_triplet_generation_messages(tables)
                log.extra["stage"] = "llm_request"
                log.info("triplet 생성 LLM 호출 시작 model=%s table_count=%s", options["model"], len(tables))
                inference_start = time.perf_counter()
                try:
                    async with client:
                        completion = await client.chat.completions.parse(
                            messages=messages, response_format=GeneratedTripletsOutput, **options,
                        )
                except Exception as error:
                    raise TripletLlmAPIError(f"LLM API error: {error}") from error
                log.info("triplet 생성 LLM 호출 완료 elapsed=%.2fs", time.perf_counter() - inference_start)

                log.extra["stage"] = "triplet_validation"
                triplets = self._validate_triplets(completion, tables)
                usage = getattr(completion, "usage", None)
                log.info("triplet 검증·원본 설명 복원·중복 제거 완료 triplet_count=%s", len(triplets))
            else:
                log.info("입력 테이블 없음: LLM 호출 생략, 빈 triplet 목록 저장")

            response = GenerateTripletResponse(data=triplets, meta_data=[{
                "model_name": options["model"],
                "question_token": getattr(usage, "prompt_tokens", 0) or 0,
                "answer_token": getattr(usage, "completion_tokens", 0) or 0,
                "table_count": len(tables),
                "triplet_count": len(triplets),
                "triplet_file": str(store.path),
            }])
            log.extra["stage"] = "triplet_save"
            log.info("triplet 전체 저장·교체 시작 file=%s triplet_count=%s", store.path, len(triplets))
            saving = asyncio.create_task(asyncio.to_thread(store.save, triplets))
            # Once publishing begins, finish the atomic write even if the caller disconnects.
            await asyncio.shield(saving)
            log.info("triplet 저장·교체 완료 file=%s", store.path)
            response.elapsed_time = time.perf_counter() - start_time
        except asyncio.CancelledError:
            if saving is not None:
                outcome, = await asyncio.gather(saving, return_exceptions=True)
                if isinstance(outcome, BaseException):
                    log.error("요청 취소 후 저장 실패 error=%s", self.error_redactor(outcome))
                else:
                    log.warning("요청 취소 후 진행 중인 원자적 저장 완료")
            log.warning("triplet 생성 요청 취소 elapsed=%.2fs", time.perf_counter() - start_time)
            raise
        except Exception as error:
            status_code = 502 if isinstance(error, TripletLlmAPIError) else 500
            error = self.error_redactor(error)
            elapsed = time.perf_counter() - start_time
            log.error("요청 실패 http_status=%s error=%s elapsed=%.2fs", status_code, error, elapsed,
                      exc_info=(type(error), error, error.__traceback__))
            response = GenerateTripletResponse(status="error", msg=str(error), elapsed_time=elapsed)
            return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))

        log.extra["stage"] = "complete"
        log.info("요청 완료 http_status=200 model=%s table_count=%s triplet_count=%s elapsed=%.2fs",
                 options["model"], len(tables), len(triplets), response.elapsed_time)
        return response
