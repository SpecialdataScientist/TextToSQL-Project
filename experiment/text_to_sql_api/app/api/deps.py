from fastapi import Request


def get_generate_triplet_open_router_service(request: Request):
    return request.app.state.services.generate_triplet_open_router_service


def get_llm_based_generate_sql_open_router_service(request: Request):
    return request.app.state.services.llm_based_generate_sql_open_router_service


def get_llm_based_schema_open_router_service(request: Request):
    return request.app.state.services.llm_based_schema_open_router_service


def get_only_triplet_based_schema_open_router_service(request: Request):
    return request.app.state.services.only_triplet_based_schema_open_router_service


def get_triplet_based_schema_open_router_service(request: Request):
    return request.app.state.services.triplet_based_schema_open_router_service


def get_indexing_schema_service(request: Request):
    return request.app.state.services.indexing_schema_service


def get_indexing_triplet_service(request: Request):
    return request.app.state.services.indexing_triplet_service


def get_indexing_triplet_open_ai_service(request: Request):
    return request.app.state.services.indexing_triplet_open_ai_service


def get_embedding_based_schema_service(request: Request):
    return request.app.state.services.embedding_based_schema_service


def get_embedded_triplet_based_schema_service(request: Request):
    return request.app.state.services.embedded_triplet_based_schema_service


def get_hybrid_only_based_schema_service(request: Request):
    return request.app.state.services.hybrid_only_based_schema_service


def get_hybrid_only_based_schema_open_ai_service(request: Request):
    return request.app.state.services.hybrid_only_based_schema_open_ai_service


def get_embedded_triplet_based_schema_open_ai_service(request: Request):
    return request.app.state.services.embedded_triplet_based_schema_open_ai_service


def get_llm_based_schema_service(request: Request):
    return request.app.state.services.llm_based_schema_service


def get_llm_based_schema_open_ai_service(request: Request):
    return request.app.state.services.llm_based_schema_open_ai_service


def get_triplet_based_schema_service(request: Request):
    return request.app.state.services.triplet_based_schema_service


def get_triplet_based_schema_open_ai_service(request: Request):
    return request.app.state.services.triplet_based_schema_open_ai_service


def get_only_triplet_based_schema_service(request: Request):
    return request.app.state.services.only_triplet_based_schema_service


def get_only_triplet_based_schema_open_ai_service(request: Request):
    return request.app.state.services.only_triplet_based_schema_open_ai_service


def get_llm_based_generate_sql_service(request: Request):
    return request.app.state.services.llm_based_generate_sql_service


def get_indexing_schema_open_ai_service(request: Request):
    return request.app.state.services.indexing_schema_open_ai_service


def get_embedding_based_schema_open_ai_service(request: Request):
    return request.app.state.services.embedding_based_schema_open_ai_service


def get_llm_based_generate_sql_open_ai_service(request: Request):
    return request.app.state.services.llm_based_generate_sql_open_ai_service


def get_generate_triplet_service(request: Request):
    return request.app.state.services.generate_triplet_service


def get_generate_triplet_open_ai_service(request: Request):
    return request.app.state.services.generate_triplet_open_ai_service
