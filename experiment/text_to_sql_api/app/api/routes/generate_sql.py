from fastapi import APIRouter, Depends

from app.api.deps import (
    get_llm_based_generate_sql_open_ai_service,
    get_llm_based_generate_sql_open_router_service,
    get_llm_based_generate_sql_service,
)
from app.schemas.request import LlmBasedGenerateSqlRequest
from app.schemas.response import LlmBasedGenerateSqlResponse


router = APIRouter(prefix="/generate_sql", tags=["generate_sql"])


@router.post(
    "/llm_based_generate_sql_open_router",
    summary="OpenRouter로 대화·스키마 기반 PostgreSQL SQL 생성",
    description=(
        "OPENROUTER_API_KEY와 OPENROUTER_LLM_MODEL_NAME을 사용합니다. query와 history[-3:], "
        "입력 schema를 반영해 생성한 PostgreSQL SQL 문자열을 data에 반환합니다."
    ),
    response_model=LlmBasedGenerateSqlResponse,
    responses={code: {"model": LlmBasedGenerateSqlResponse} for code in (500, 502)},
)
async def llm_based_generate_sql_open_router(
    request: LlmBasedGenerateSqlRequest,
    service=Depends(get_llm_based_generate_sql_open_router_service),
):
    return await service.generate_sql(request)


@router.post(
    "/llm_based_generate_sql",
    summary="대화 히스토리와 스키마를 기반으로 PostgreSQL SQL 생성",
    description=(
        "검색 응답의 data를 schema로 전달합니다. history[-3:]과 현재 query를 사용하고, "
        "생성한 PostgreSQL SQL 문자열을 data에 반환합니다."
    ),
    response_model=LlmBasedGenerateSqlResponse,
    responses={code: {"model": LlmBasedGenerateSqlResponse} for code in (500, 502)},
)
async def llm_based_generate_sql(
    request: LlmBasedGenerateSqlRequest,
    service=Depends(get_llm_based_generate_sql_service),
):
    return await service.generate_sql(request)


@router.post(
    "/llm_based_generate_sql_open_ai",
    summary="OpenAI 모델로 대화·스키마 기반 PostgreSQL SQL 생성",
    description="OPENAI_API_KEY와 OPENAI_LLM_MODEL_NAME을 사용하며 최근 history[-3:]을 반영합니다.",
    response_model=LlmBasedGenerateSqlResponse,
    responses={code: {"model": LlmBasedGenerateSqlResponse} for code in (500, 502)},
)
async def llm_based_generate_sql_open_ai(
    request: LlmBasedGenerateSqlRequest,
    service=Depends(get_llm_based_generate_sql_open_ai_service),
):
    return await service.generate_sql(request)
