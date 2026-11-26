import asyncio
from dataclasses import dataclass, field
import json
import logging
import time
from uuid import uuid4

from pydantic import ValidationError

from .config import Settings
from .errors import AgentError, ToolInputError, find_agent_error
from .llm import create_llm_client, resolve_profile
from .mcp_client import connect_mcp
from .prompts import FUNCTION_TOOLS, build_messages
from .schemas import AgentResponse, AnswerData, CallArguments, ChatRequest, SearchArguments, ToolTrace


logger = logging.getLogger("text_to_sql_agent")


@dataclass
class RunState:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    model: str = ""
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[ToolTrace] = field(default_factory=list)


def parse_arguments(raw: str) -> dict:
    def reject_constant(value):
        raise ValueError("Non-finite JSON number")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        arguments = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
        if not isinstance(arguments, dict):
            raise ValueError("Expected an object")
        return arguments
    except (ValueError, TypeError, RecursionError):
        raise ToolInputError("함수 인자는 중복 키·NaN이 없는 유효한 JSON 객체여야 합니다.") from None


class AgentService:
    def __init__(self, settings: Settings, *, llm_client_factory=None, mcp_factory=None):
        self.settings = settings
        self.llm_client_factory = llm_client_factory or create_llm_client
        self.mcp_factory = mcp_factory or connect_mcp

    async def chat(self, request: ChatRequest) -> tuple[int, AgentResponse]:
        started = time.perf_counter()
        state = RunState()
        status_code, status, message, answer = 200, "success", "success", None
        logger.info("request_id=%s stage=start provider=%s", state.request_id, request.provider)
        try:
            answer = await asyncio.wait_for(
                self._execute(request, state), timeout=self.settings.request_timeout_seconds,
            )
        except asyncio.CancelledError:
            logger.info("request_id=%s stage=cancelled elapsed=%.3f", state.request_id, time.perf_counter() - started)
            raise
        except asyncio.TimeoutError:
            status_code, status, message = 504, "error", "에이전트 전체 요청 제한 시간을 초과했습니다."
        except Exception as error:
            known = find_agent_error(error)
            status_code = known.status_code if known else 502
            status = "error"
            message = str(known) if known else "LLM 또는 MCP 통신에 실패했습니다. 서버 설정과 연결을 확인하세요."
            # Never log upstream exception strings, authentication headers or tracebacks.
            logger.warning("request_id=%s stage=failed error_type=%s", state.request_id, type(error).__name__)

        response = AgentResponse(
            status=status, msg=message, data=AnswerData(answer=answer, tool_calls=state.tool_calls),
            meta_data=[{
                "request_id": state.request_id, "provider": request.provider, "model_name": state.model,
                "llm_calls": state.llm_calls, "question_token": state.prompt_tokens,
                "answer_token": state.completion_tokens,
            }], elapsed_time=time.perf_counter() - started,
        )
        logger.info("request_id=%s stage=complete http_status=%s elapsed=%.3f llm_calls=%s tool_calls=%s",
                    state.request_id, status_code, response.elapsed_time, state.llm_calls, len(state.tool_calls))
        return status_code, response

    async def _execute(self, request: ChatRequest, state: RunState) -> str:
        profile = resolve_profile(self.settings, request.provider)
        state.model = profile.options["model"]
        mcp_options = {"table_names": request.table_name} if "table_name" in request.model_fields_set else {}
        async with self.llm_client_factory(self.settings, profile) as client:
            async with self.mcp_factory(self.settings, **mcp_options) as gateway:
                messages = build_messages(request)
                for _ in range(self.settings.max_llm_rounds):
                    state.llm_calls += 1
                    logger.info("request_id=%s stage=llm round=%s", state.request_id, state.llm_calls)
                    try:
                        completion = await client.chat.completions.create(
                            messages=messages, tools=FUNCTION_TOOLS, tool_choice="auto", **profile.options,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise AgentError("LLM API 호출에 실패했습니다. 인증·모델·도구 호출 지원 여부를 확인하세요.") from None
                    usage = completion.usage
                    state.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    state.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                    if not completion.choices:
                        raise AgentError("LLM이 응답 선택지를 반환하지 않았습니다.")
                    choice = completion.choices[0]
                    message = choice.message
                    if message.refusal:
                        raise AgentError("LLM이 요청 처리를 거절했습니다.")
                    calls = message.tool_calls or []
                    if choice.finish_reason == "stop" and not calls and message.content and message.content.strip():
                        return message.content
                    if choice.finish_reason != "tool_calls" or not calls:
                        raise AgentError("LLM이 완전한 답변 또는 유효한 도구 호출을 반환하지 않았습니다.")
                    ids = [call.id for call in calls]
                    if len(ids) != len(set(ids)) or any(not call.id or call.type != "function" for call in calls):
                        raise AgentError("LLM 도구 호출 ID 또는 형식이 올바르지 않습니다.")
                    # Retain the exact assistant tool-call message, then answer every call ID.
                    messages.append(message.model_dump(
                        mode="json", include={"role", "content", "tool_calls"}, exclude_none=True,
                    ))
                    for call in calls:
                        if len(state.tool_calls) >= self.settings.max_tool_calls:
                            raise AgentError("에이전트 함수 호출 한도를 초과했습니다.")
                        trace = ToolTrace(id=call.id, function_name=call.function.name, arguments=call.function.arguments)
                        state.tool_calls.append(trace)
                        logger.info("request_id=%s stage=tool call_index=%s function=%s",
                                    state.request_id, len(state.tool_calls), trace.function_name)
                        try:
                            arguments = parse_arguments(call.function.arguments)
                            trace.arguments = arguments
                            if call.function.name == "search_mcp_tools":
                                trace.result = gateway.search(SearchArguments.model_validate(arguments))
                            elif call.function.name == "call_mcp_tool":
                                parsed = CallArguments.model_validate(arguments)
                                trace.mcp_tool_name = parsed.name
                                trace.result, trace.is_error = await gateway.call(parsed)
                            else:
                                raise ToolInputError("사용 가능한 함수는 search_mcp_tools와 call_mcp_tool입니다.")
                        except (ToolInputError, ValidationError) as error:
                            trace.is_error = True
                            trace.result = {"error": str(error) if isinstance(error, ToolInputError)
                                            else "함수 입력 스키마에 맞는 인자를 전달하세요."}
                        except BaseException:
                            trace.is_error = True
                            trace.result = {"error": "MCP 도구 호출이 실패하거나 중단되었습니다."}
                            raise
                        messages.append({
                            "role": "tool", "tool_call_id": call.id,
                            "content": json.dumps({"is_error": trace.is_error, "result": trace.result}, ensure_ascii=False),
                        })
                raise AgentError("에이전트 LLM 응답 횟수 한도를 초과했습니다.")
