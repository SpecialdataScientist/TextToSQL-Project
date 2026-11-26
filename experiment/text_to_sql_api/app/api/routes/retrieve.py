from fastapi import APIRouter, Depends

from app.api.deps import (
    get_embedded_triplet_based_schema_open_ai_service,
    get_embedded_triplet_based_schema_service,
    get_embedding_based_schema_open_ai_service,
    get_embedding_based_schema_service,
    get_generate_triplet_open_ai_service,
    get_generate_triplet_open_router_service,
    get_generate_triplet_service,
    get_hybrid_only_based_schema_open_ai_service,
    get_hybrid_only_based_schema_service,
    get_indexing_schema_open_ai_service,
    get_indexing_schema_service,
    get_indexing_triplet_open_ai_service,
    get_indexing_triplet_service,
    get_llm_based_schema_open_ai_service,
    get_llm_based_schema_open_router_service,
    get_llm_based_schema_service,
    get_only_triplet_based_schema_open_ai_service,
    get_only_triplet_based_schema_open_router_service,
    get_only_triplet_based_schema_service,
    get_triplet_based_schema_open_ai_service,
    get_triplet_based_schema_open_router_service,
    get_triplet_based_schema_service,
)
from app.schemas.request import (
    EmbeddedTripletBasedSchemaOpenAiRequest,
    EmbeddedTripletBasedSchemaRequest,
    EmbeddingBasedSchemaRequest,
    HybridOnlyBasedSchemaOpenAiRequest,
    HybridOnlyBasedSchemaRequest,
    LlmBasedSchemaOpenAiRequest,
    LlmBasedSchemaOpenRouterRequest,
    LlmBasedSchemaRequest,
    OnlyTripletBasedSchemaOpenAiRequest,
    OnlyTripletBasedSchemaOpenRouterRequest,
    OnlyTripletBasedSchemaRequest,
    TripletBasedSchemaOpenAiRequest,
    TripletBasedSchemaOpenRouterRequest,
    TripletBasedSchemaRequest,
)
from app.schemas.response import (
    EmbeddedTripletBasedSchemaOpenAiResponse,
    EmbeddedTripletBasedSchemaResponse,
    EmbeddingBasedSchemaResponse,
    GenerateTripletResponse,
    HybridOnlyBasedSchemaOpenAiResponse,
    HybridOnlyBasedSchemaResponse,
    IndexingSchemaResponse,
    IndexingTripletResponse,
    LlmBasedSchemaOpenAiResponse,
    LlmBasedSchemaOpenRouterResponse,
    LlmBasedSchemaResponse,
    OnlyTripletBasedSchemaOpenAiResponse,
    OnlyTripletBasedSchemaOpenRouterResponse,
    OnlyTripletBasedSchemaResponse,
    TripletBasedSchemaOpenAiResponse,
    TripletBasedSchemaOpenRouterResponse,
    TripletBasedSchemaResponse,
)


router = APIRouter(prefix="/retrieve", tags=["retrieve"])


@router.post(
    "/generate_triplet_open_router",
    summary="OpenRouter 모델로 테이블 관계 RDF triplet 전체 생성 및 저장",
    description=(
        "요청 본문 없이 OPENROUTER_API_KEY와 OPENROUTER_LLM_MODEL_NAME으로 전체 카탈로그의 관계를 "
        "생성합니다. RDF_TRIPLET_OPEN_ROUTER/triplets.json을 성공 시 전체 교체하고 실패 시 보존합니다."
    ),
    response_model=GenerateTripletResponse,
    responses={code: {"model": GenerateTripletResponse} for code in (500, 502)},
)
async def generate_triplet_open_router(service=Depends(get_generate_triplet_open_router_service)):
    return await service.generate_triplet()


@router.post(
    "/llm_based_schema_open_router",
    summary="OpenRouter와 전체 카탈로그를 활용한 테이블 스키마 검색",
    description=(
        "OPENROUTER_API_KEY와 OPENROUTER_LLM_MODEL_NAME을 사용합니다. history[-3:]과 현재 query, "
        "전체 테이블의 이름·설명·DDL을 한 번 전달하여 필요한 원본 스키마를 반환합니다."
    ),
    response_model=LlmBasedSchemaOpenRouterResponse,
    responses={code: {"model": LlmBasedSchemaOpenRouterResponse} for code in (500, 502)},
)
async def llm_based_schema_open_router(
    request: LlmBasedSchemaOpenRouterRequest,
    service=Depends(get_llm_based_schema_open_router_service),
):
    return await service.llm_based_schema(request)


@router.post(
    "/only_triplet_based_schema_open_router",
    summary="OpenRouter로 시작점을 선택하고 RDF 관계로 확장한 전체 스키마 반환",
    description=(
        "history[-3:]과 현재 query로 시작 테이블을 선정할 때 OpenRouter를 한 번 호출합니다. "
        "RDF_TRIPLET_OPEN_ROUTER/triplets.json 관계를 양방향 확장하고 카탈로그 순서대로 전체 원본 "
        "스키마를 반환합니다. top_k 기본값 -1은 시작점 무제한이며 양수는 시작점 수의 상한입니다. "
        "max_hop 기본값은 3, 0은 시작점만, -1은 재방문을 제외하고 최대 10단계 탐색합니다. "
        "최종 LLM 재선별과 최종 반환 수 제한은 적용하지 않습니다."
    ),
    response_model=OnlyTripletBasedSchemaOpenRouterResponse,
    responses={code: {"model": OnlyTripletBasedSchemaOpenRouterResponse} for code in (404, 409, 500, 502)},
)
async def only_triplet_based_schema_open_router(
    request: OnlyTripletBasedSchemaOpenRouterRequest,
    service=Depends(get_only_triplet_based_schema_open_router_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/triplet_based_schema_open_router",
    summary="OpenRouter와 RDF 확장·최종 재선별 기반 테이블 스키마 검색",
    description=(
        "OPENROUTER_API_KEY와 OPENROUTER_LLM_MODEL_NAME으로 history[-3:]과 현재 query를 반영하여 "
        "시작 테이블을 선택합니다. RDF_TRIPLET_OPEN_ROUTER/triplets.json을 max_hop만큼 양방향 "
        "확장하고 후보의 원본 DDL·설명과 관계를 OpenRouter에 다시 전달해 최종 스키마를 선택합니다. "
        "top_k 기본값 -1은 시작점 무제한이며 양수는 시작점 수의 상한입니다. 최종 반환 수에는 "
        "적용하지 않습니다. max_hop 기본값은 3, 0은 시작점만, -1은 재방문을 제외하고 최대 10단계입니다."
    ),
    response_model=TripletBasedSchemaOpenRouterResponse,
    responses={code: {"model": TripletBasedSchemaOpenRouterResponse} for code in (404, 409, 500, 502)},
)
async def triplet_based_schema_open_router(
    request: TripletBasedSchemaOpenRouterRequest,
    service=Depends(get_triplet_based_schema_open_router_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/indexing_schema",
    summary="신규 테이블 스키마 증분 임베딩 및 FAISS 인덱스 저장",
    response_model=IndexingSchemaResponse,
    responses={code: {"model": IndexingSchemaResponse} for code in (409, 500, 502)},
)
async def indexing_schema(service=Depends(get_indexing_schema_service)):
    return await service.index_schema()


@router.post(
    "/indexing_triplet",
    summary="신규 RDF triplet 증분 임베딩 및 FAISS 인덱스 저장",
    description=(
        "요청 본문 없이 RDF_TRIPLET/triplets.json을 읽습니다. 파일이 없을 때만 "
        "RDF_TRIPLET_OPEN_ROUTER/triplets.json을 대신 사용하며, 빈 목록이나 잘못된 파일은 대체하지 않습니다. "
        "주체 테이블명·관계·대상 테이블명 기준으로 "
        "아직 저장되지 않은 관계만 임베딩합니다. EMBEDDING_API_BASE 서버를 사용하며 "
        "EMBEDDED_TRIPLET/triplets 아래 인덱스·메타데이터·원본을 저장합니다."
    ),
    response_model=IndexingTripletResponse,
    responses={code: {"model": IndexingTripletResponse} for code in (409, 500, 502)},
)
async def indexing_triplet(service=Depends(get_indexing_triplet_service)):
    return await service.index_triplet()


@router.post(
    "/indexing_triplet_open_ai",
    summary="OpenAI 임베딩으로 신규 RDF triplet 증분 인덱싱",
    description=(
        "요청 본문 없이 RDF_TRIPLET_OPEN_AI/triplets.json을 읽고 새 관계만 임베딩합니다. "
        "OPENAI_API_KEY와 OPENAI_EMBEDDING_MODEL_NAME을 사용하며 "
        "EMBEDDED_TRIPLET_OPEN_AI/triplets 아래 인덱스·메타데이터·원본을 별도로 저장합니다."
    ),
    response_model=IndexingTripletResponse,
    responses={code: {"model": IndexingTripletResponse} for code in (409, 500, 502)},
)
async def indexing_triplet_open_ai(service=Depends(get_indexing_triplet_open_ai_service)):
    return await service.index_triplet()


@router.post(
    "/embedding_based_schema",
    summary="자연어 질의의 임베딩 기반 Top-k 테이블 스키마 검색",
    description=(
        "history의 최근 3개 메시지(history[-3:])와 현재 query를 결합하여 검색합니다. "
        "메시지 하나를 1 turn으로 계산하며, history를 생략하거나 빈 리스트로 보내면 query만 사용합니다."
    ),
    response_model=EmbeddingBasedSchemaResponse,
    responses={code: {"model": EmbeddingBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def embedding_based_schema(
    request: EmbeddingBasedSchemaRequest,
    service=Depends(get_embedding_based_schema_service),
):
    return await service.embedding_based_schema(request)


@router.post(
    "/embedded_triplet_based_schema",
    summary="Triplet 임베딩 검색으로 중복 없는 Top-k 테이블 스키마 반환",
    description=(
        "EMBEDDED_TRIPLET의 인덱스를 history[-3:]과 현재 query로 검색합니다. "
        "테이블별 최고 triplet 유사도를 집계해 top_k개를 선택하고 원본 카탈로그의 DDL·설명을 반환합니다. "
        "동점은 테이블명 오름차순이며 원본이 없는 테이블은 제외합니다. 후보가 부족하면 가능한 수만 반환합니다."
    ),
    response_model=EmbeddedTripletBasedSchemaResponse,
    responses={code: {"model": EmbeddedTripletBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def embedded_triplet_based_schema(
    request: EmbeddedTripletBasedSchemaRequest,
    service=Depends(get_embedded_triplet_based_schema_service),
):
    return await service.embedded_triplet_based_schema(request)


@router.post(
    "/embedded_triplet_based_schema_open_ai",
    summary="OpenAI Triplet 임베딩 검색으로 중복 없는 Top-k 테이블 스키마 반환",
    description=(
        "OPENAI_API_KEY와 OPENAI_EMBEDDING_MODEL_NAME으로 history[-3:]과 현재 query를 임베딩하고 "
        "EMBEDDED_TRIPLET_OPEN_AI의 인덱스를 검색합니다. 테이블별 최고 triplet 유사도를 집계해 "
        "top_k개를 선택하고 원본 카탈로그의 DDL·설명을 반환합니다. 동점은 테이블명 오름차순이며 "
        "원본이 없는 테이블은 제외합니다. 후보가 부족하면 가능한 수만 반환합니다."
    ),
    response_model=EmbeddedTripletBasedSchemaOpenAiResponse,
    responses={code: {"model": EmbeddedTripletBasedSchemaOpenAiResponse} for code in (404, 409, 500, 502)},
)
async def embedded_triplet_based_schema_open_ai(
    request: EmbeddedTripletBasedSchemaOpenAiRequest,
    service=Depends(get_embedded_triplet_based_schema_open_ai_service),
):
    return await service.embedded_triplet_based_schema(request)


@router.post(
    "/llm_based_schema",
    summary="전체 테이블 카탈로그와 LLM을 활용한 스키마 검색",
    description=(
        "전체 테이블의 이름·설명·원본 DDL과 history[-3:], 현재 query를 LLM에 한 번 전달하여 "
        "필요한 테이블을 선택하고 원본 스키마를 반환합니다. top_k는 받지 않으며 "
        "LLM_API_BASE의 LLM을 사용합니다. RDF나 임베딩 인덱스는 필요하지 않습니다."
    ),
    response_model=LlmBasedSchemaResponse,
    responses={code: {"model": LlmBasedSchemaResponse} for code in (500, 502)},
)
async def llm_based_schema(
    request: LlmBasedSchemaRequest,
    service=Depends(get_llm_based_schema_service),
):
    return await service.llm_based_schema(request)


@router.post(
    "/llm_based_schema_open_ai",
    summary="OpenAI와 전체 테이블 카탈로그를 활용한 스키마 검색",
    description=(
        "전체 테이블의 이름·설명·원본 DDL과 history[-3:], 현재 query를 OpenAI에 한 번 전달하여 "
        "필요한 테이블을 선택하고 원본 스키마를 반환합니다. top_k는 받지 않으며 "
        "OPENAI_API_KEY와 OPENAI_LLM_MODEL_NAME을 사용합니다. RDF나 임베딩 인덱스는 필요하지 않습니다."
    ),
    response_model=LlmBasedSchemaOpenAiResponse,
    responses={code: {"model": LlmBasedSchemaOpenAiResponse} for code in (500, 502)},
)
async def llm_based_schema_open_ai(
    request: LlmBasedSchemaOpenAiRequest,
    service=Depends(get_llm_based_schema_open_ai_service),
):
    return await service.llm_based_schema(request)


@router.post(
    "/triplet_based_schema",
    summary="RDF triplet 관계 확장과 LLM 재선별 기반 테이블 스키마 검색",
    description=(
        "history[-3:]과 현재 query로 전체 카탈로그에서 시작 후보를 선택하고 요청의 max_hop만큼 관계를 양방향 확장합니다. "
        "기본값은 3이며 -1은 재방문 테이블을 제외하고 최대 10단계 탐색합니다. "
        "top_k는 시작 테이블 수의 상한이며 기본값 -1은 무제한입니다. "
        "후보의 원본 DDL과 관계로 LLM이 재선별하며 최종 반환 수에는 top_k를 적용하지 않습니다. "
        "RDF_TRIPLET/triplets.json 및 LLM_API_BASE의 LLM을 사용합니다."
    ),
    response_model=TripletBasedSchemaResponse,
    responses={code: {"model": TripletBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def triplet_based_schema(
    request: TripletBasedSchemaRequest,
    service=Depends(get_triplet_based_schema_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/only_triplet_based_schema",
    summary="시작 테이블에서 RDF 관계를 확장한 전체 스키마 반환",
    description=(
        "history[-3:]과 현재 query로 시작 테이블을 선정할 때 LLM_API_BASE의 LLM을 한 번 호출합니다. "
        "RDF_TRIPLET/triplets.json 관계를 max_hop만큼 양방향 확장하고 최종 LLM 재선별 없이 "
        "시작 테이블을 포함한 전체 후보의 원본 스키마를 카탈로그 순서대로 반환합니다. "
        "max_hop 기본값은 3, 0은 시작 테이블만, -1은 재방문을 제외하고 최대 10단계 탐색합니다. "
        "top_k는 시작 테이블 수의 상한이며 기본값 -1은 무제한입니다. 최종 반환 수에는 적용하지 않습니다."
    ),
    response_model=OnlyTripletBasedSchemaResponse,
    responses={code: {"model": OnlyTripletBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def only_triplet_based_schema(
    request: OnlyTripletBasedSchemaRequest,
    service=Depends(get_only_triplet_based_schema_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/hybrid_only_based_schema",
    summary="Triplet 임베딩 시작점과 RDF multi-hop 확장의 전체 스키마 반환",
    description=(
        "EMBEDDED_TRIPLET의 인덱스를 history[-3:]과 현재 query로 검색하여 시작 테이블을 선택합니다. "
        "EMBEDDING_API_BASE의 임베딩 모델을 사용하며 테이블별 최고 triplet 유사도 순으로 "
        "중복 없이 최대 top_k개를 선택합니다. 동점은 테이블명 오름차순이며 기본값 -1은 무제한입니다. "
        "RDF_TRIPLET/triplets.json을 우선 읽고 파일이 없을 때만 "
        "RDF_TRIPLET_OPEN_ROUTER/triplets.json을 대신 사용합니다. 빈 목록이나 잘못된 파일은 대체하지 않습니다. "
        "선택한 파일의 관계를 양방향 확장하여 시작점 포함 전체 원본 스키마를 "
        "카탈로그 순서대로 반환합니다. max_hop 기본값은 3, 0은 시작점만, -1은 재방문을 제외하고 "
        "최대 10단계 탐색합니다. 최종 반환 수에는 top_k를 적용하지 않으며 LLM은 호출하지 않습니다."
    ),
    response_model=HybridOnlyBasedSchemaResponse,
    responses={code: {"model": HybridOnlyBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def hybrid_only_based_schema(
    request: HybridOnlyBasedSchemaRequest,
    service=Depends(get_hybrid_only_based_schema_service),
):
    return await service.hybrid_only_based_schema(request)


@router.post(
    "/hybrid_only_based_schema_open_ai",
    summary="OpenAI Triplet 임베딩 시작점과 RDF multi-hop 확장의 전체 스키마 반환",
    description=(
        "OPENAI_API_KEY와 OPENAI_EMBEDDING_MODEL_NAME으로 history[-3:]과 현재 query를 임베딩하고 "
        "EMBEDDED_TRIPLET_OPEN_AI 인덱스에서 시작 테이블을 검색합니다. 테이블별 최고 triplet "
        "유사도 순으로 중복 없이 최대 top_k개를 선택하며 동점은 테이블명 오름차순입니다. "
        "top_k 기본값 -1은 시작점 무제한입니다. RDF_TRIPLET_OPEN_AI/triplets.json 관계를 "
        "max_hop만큼 양방향 확장하고 시작점 포함 전체 원본 스키마를 카탈로그 순서대로 반환합니다. "
        "max_hop 기본값은 3, 0은 시작점만, -1은 재방문을 제외하고 최대 10단계 탐색합니다. "
        "최종 반환 수에는 top_k를 적용하지 않으며 LLM은 호출하지 않습니다."
    ),
    response_model=HybridOnlyBasedSchemaOpenAiResponse,
    responses={code: {"model": HybridOnlyBasedSchemaOpenAiResponse} for code in (404, 409, 500, 502)},
)
async def hybrid_only_based_schema_open_ai(
    request: HybridOnlyBasedSchemaOpenAiRequest,
    service=Depends(get_hybrid_only_based_schema_open_ai_service),
):
    return await service.hybrid_only_based_schema(request)


@router.post(
    "/triplet_based_schema_open_ai",
    summary="OpenAI와 RDF triplet 관계를 활용한 테이블 스키마 검색",
    description=(
        "history[-3:]과 현재 query로 전체 카탈로그에서 시작 후보를 선택하고 요청의 max_hop만큼 관계를 양방향 확장합니다. "
        "기본값은 3이며 -1은 재방문 테이블을 제외하고 최대 10단계 탐색합니다. "
        "top_k는 시작 테이블 수의 상한이며 기본값 -1은 무제한입니다. "
        "후보의 원본 DDL과 관계로 재선별하며 최종 반환 수에는 top_k를 적용하지 않습니다. "
        "OPENAI_API_KEY, OPENAI_LLM_MODEL_NAME 및 RDF_TRIPLET_OPEN_AI/triplets.json을 사용합니다."
    ),
    response_model=TripletBasedSchemaOpenAiResponse,
    responses={code: {"model": TripletBasedSchemaOpenAiResponse} for code in (404, 409, 500, 502)},
)
async def triplet_based_schema_open_ai(
    request: TripletBasedSchemaOpenAiRequest,
    service=Depends(get_triplet_based_schema_open_ai_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/only_triplet_based_schema_open_ai",
    summary="OpenAI로 선정한 시작 테이블에서 RDF 관계를 확장한 전체 스키마 반환",
    description=(
        "history[-3:]과 현재 query로 시작 테이블을 선정할 때 OpenAI LLM을 한 번 호출합니다. "
        "RDF_TRIPLET_OPEN_AI/triplets.json 관계를 max_hop만큼 양방향 확장하고 최종 LLM 재선별 없이 "
        "시작 테이블을 포함한 전체 후보의 원본 스키마를 카탈로그 순서대로 반환합니다. "
        "max_hop 기본값은 3, 0은 시작 테이블만, -1은 재방문을 제외하고 최대 10단계 탐색합니다. "
        "top_k는 시작 테이블 수의 상한이며 기본값 -1은 무제한입니다. 확장 후 반환 수에는 제한하지 않습니다. "
        "OPENAI_API_KEY와 OPENAI_LLM_MODEL_NAME을 사용합니다."
    ),
    response_model=OnlyTripletBasedSchemaOpenAiResponse,
    responses={code: {"model": OnlyTripletBasedSchemaOpenAiResponse} for code in (404, 409, 500, 502)},
)
async def only_triplet_based_schema_open_ai(
    request: OnlyTripletBasedSchemaOpenAiRequest,
    service=Depends(get_only_triplet_based_schema_open_ai_service),
):
    return await service.triplet_based_schema(request)


@router.post(
    "/indexing_schema_open_ai",
    summary="OpenAI 임베딩으로 신규 테이블 스키마 증분 인덱싱",
    description="OPENAI_API_KEY를 사용하고 EMBEDDED_SCHEMA_OPEN_AI 경로에 인덱스를 저장합니다.",
    response_model=IndexingSchemaResponse,
    responses={code: {"model": IndexingSchemaResponse} for code in (409, 500, 502)},
)
async def indexing_schema_open_ai(service=Depends(get_indexing_schema_open_ai_service)):
    return await service.index_schema()


@router.post(
    "/embedding_based_schema_open_ai",
    summary="OpenAI 임베딩 기반 Top-k 테이블 스키마 검색",
    description="최근 history[-3:]과 현재 query를 사용하여 OpenAI 전용 인덱스를 검색합니다.",
    response_model=EmbeddingBasedSchemaResponse,
    responses={code: {"model": EmbeddingBasedSchemaResponse} for code in (404, 409, 500, 502)},
)
async def embedding_based_schema_open_ai(
    request: EmbeddingBasedSchemaRequest,
    service=Depends(get_embedding_based_schema_open_ai_service),
):
    return await service.embedding_based_schema(request)


@router.post(
    "/generate_triplet",
    summary="LLM 기반 테이블 관계 RDF triplet 전체 생성 및 저장",
    description=(
        "요청 본문 없이 전체 스키마와 설명으로 관계를 추출합니다. "
        "data의 각 항목은 [주체 테이블 정보, 영문 관계 표현, 객체 테이블 정보]입니다. "
        "RDF_TRIPLET/triplets.json을 성공 시 전체 교체하고, 실패 시 기존 파일을 유지합니다."
    ),
    response_model=GenerateTripletResponse,
    responses={code: {"model": GenerateTripletResponse} for code in (500, 502)},
)
async def generate_triplet(service=Depends(get_generate_triplet_service)):
    return await service.generate_triplet()


@router.post(
    "/generate_triplet_open_ai",
    summary="OpenAI 모델로 테이블 관계 RDF triplet 전체 생성 및 저장",
    description=(
        "요청 본문 없이 OPENAI_API_KEY와 OPENAI_LLM_MODEL_NAME으로 전체 스키마의 관계를 추출합니다. "
        "data의 각 항목은 [주체 테이블 정보, 영문 관계 표현, 객체 테이블 정보]입니다. "
        "RDF_TRIPLET_OPEN_AI/triplets.json을 성공 시 전체 교체하고, 실패 시 기존 파일을 유지합니다."
    ),
    response_model=GenerateTripletResponse,
    responses={code: {"model": GenerateTripletResponse} for code in (500, 502)},
)
async def generate_triplet_open_ai(service=Depends(get_generate_triplet_open_ai_service)):
    return await service.generate_triplet()
