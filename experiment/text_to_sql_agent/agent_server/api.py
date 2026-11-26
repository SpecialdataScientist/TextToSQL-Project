from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .schemas import AgentResponse, ChatRequest
from .service import AgentService


def create_app(settings: Settings | None = None, *, llm_client_factory=None, mcp_factory=None) -> FastAPI:
    app = FastAPI(title="Text-to-SQL LLM Agent", version="1.0.0")
    app.state.settings = settings if settings is not None else Settings.from_env()
    app.state.agent = AgentService(
        app.state.settings, llm_client_factory=llm_client_factory, mcp_factory=mcp_factory,
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        response = AgentResponse(status="error", msg="요청 형식이 올바르지 않습니다. /docs의 입력 형식을 확인하세요.")
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post(
        "/agent/chat", response_model=AgentResponse,
        summary="선택한 LLM과 MCP로 질문에 답변",
        responses={code: {"model": AgentResponse} for code in (422, 500, 502, 504)},
    )
    async def chat(request: ChatRequest):
        code, response = await app.state.agent.chat(request)
        return JSONResponse(status_code=code, content=response.model_dump(mode="json"))

    return app
