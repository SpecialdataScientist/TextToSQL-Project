from dataclasses import dataclass, replace
from functools import partial

from app.core.config import OPENAI_API_BASE, OPENROUTER_API_BASE
from app.schemas.response import (
    EmbeddedTripletBasedSchemaOpenAiData,
    EmbeddedTripletBasedSchemaOpenAiResponse,
    HybridOnlyBasedSchemaOpenAiData,
    HybridOnlyBasedSchemaOpenAiResponse,
    LlmBasedSchemaOpenAiData,
    LlmBasedSchemaOpenAiResponse,
    LlmBasedSchemaOpenRouterData,
    LlmBasedSchemaOpenRouterResponse,
    OnlyTripletBasedSchemaData,
    OnlyTripletBasedSchemaOpenAiData,
    OnlyTripletBasedSchemaOpenAiResponse,
    OnlyTripletBasedSchemaOpenRouterData,
    OnlyTripletBasedSchemaOpenRouterResponse,
    OnlyTripletBasedSchemaResponse,
    TripletBasedSchemaOpenAiData,
    TripletBasedSchemaOpenAiResponse,
    TripletBasedSchemaOpenRouterData,
    TripletBasedSchemaOpenRouterResponse,
)
from app.services import open_ai_clients, open_router_clients
from app.services.embedding_based_schema_service import EmbeddingBasedSchemaService
from app.services.embedded_triplet_based_schema_service import EmbeddedTripletBasedSchemaService
from app.services.generate_triplet_service import GenerateTripletService
from app.services.hybrid_only_based_schema_service import HybridOnlyBasedSchemaService
from app.services.indexing_schema_service import IndexingSchemaService
from app.services.indexing_triplet_service import IndexingTripletService
from app.services.llm_based_generate_sql_service import LlmBasedGenerateSqlService
from app.services.llm_based_schema_service import LlmBasedSchemaService
from app.services.triplet_based_schema_service import TripletBasedSchemaService


@dataclass
class ApplicationServices:
    indexing_schema_service: IndexingSchemaService
    embedding_based_schema_service: EmbeddingBasedSchemaService
    llm_based_generate_sql_service: LlmBasedGenerateSqlService
    indexing_schema_open_ai_service: IndexingSchemaService
    embedding_based_schema_open_ai_service: EmbeddingBasedSchemaService
    llm_based_generate_sql_open_ai_service: LlmBasedGenerateSqlService
    generate_triplet_service: GenerateTripletService
    generate_triplet_open_ai_service: GenerateTripletService
    triplet_based_schema_service: TripletBasedSchemaService
    triplet_based_schema_open_ai_service: TripletBasedSchemaService
    only_triplet_based_schema_service: TripletBasedSchemaService
    only_triplet_based_schema_open_ai_service: TripletBasedSchemaService
    llm_based_schema_service: LlmBasedSchemaService
    llm_based_schema_open_ai_service: LlmBasedSchemaService
    indexing_triplet_service: IndexingTripletService
    indexing_triplet_open_ai_service: IndexingTripletService
    embedded_triplet_based_schema_service: EmbeddedTripletBasedSchemaService
    embedded_triplet_based_schema_open_ai_service: EmbeddedTripletBasedSchemaService
    hybrid_only_based_schema_service: HybridOnlyBasedSchemaService
    hybrid_only_based_schema_open_ai_service: HybridOnlyBasedSchemaService
    generate_triplet_open_router_service: GenerateTripletService
    llm_based_generate_sql_open_router_service: LlmBasedGenerateSqlService
    llm_based_schema_open_router_service: LlmBasedSchemaService
    only_triplet_based_schema_open_router_service: TripletBasedSchemaService
    triplet_based_schema_open_router_service: TripletBasedSchemaService


def build_services(settings) -> ApplicationServices:
    open_router_settings = replace(
        settings, llm_api_base=OPENROUTER_API_BASE,
        model_name=settings.openrouter_llm_model_name, rdf_triplet=settings.rdf_triplet_open_router,
    )
    router_triplet_validator = partial(
        open_router_clients.validate_open_router_triplet_settings,
        legacy_directory=settings.rdf_triplet, open_ai_directory=settings.rdf_triplet_open_ai,
    )
    router_error_redactor = partial(
        open_router_clients.redact_open_router_error, api_key=open_router_settings.openrouter_api_key.strip(),
    )
    open_ai_settings = replace(
        settings,
        embedded_schema=settings.embedded_schema_open_ai,
        embedding_model_name=settings.openai_embedding_model_name,
        embedding_api_base=OPENAI_API_BASE,
        model_name=settings.openai_llm_model_name,
        llm_api_base=OPENAI_API_BASE,
        rdf_triplet=settings.rdf_triplet_open_ai,
    )
    # OpenAI triplet indexing uses its own RDF source and embedding output directory.
    open_ai_triplet_index_settings = replace(
        open_ai_settings, embedded_triplet=settings.embedded_triplet_open_ai,
    )
    embedding_validator = partial(
        open_ai_clients.validate_open_ai_embedding_settings, legacy_directory=settings.embedded_schema,
    )
    triplet_validator = partial(
        open_ai_clients.validate_open_ai_triplet_settings, legacy_directory=settings.rdf_triplet,
    )
    triplet_embedding_validator = partial(
        open_ai_clients.validate_open_ai_triplet_embedding_settings, legacy_directory=settings.embedded_triplet,
    )
    hybrid_validator = partial(
        open_ai_clients.validate_open_ai_hybrid_settings,
        legacy_embedding_directory=settings.embedded_triplet, legacy_rdf_directory=settings.rdf_triplet,
    )
    error_redactor = partial(open_ai_clients.redact_open_ai_error, api_key=open_ai_settings.openai_api_key.strip())
    return ApplicationServices(
        generate_triplet_open_router_service=GenerateTripletService(
            open_router_settings, client_factory=open_router_clients.create_open_router_llm_client,
            completion_options_factory=open_router_clients.open_router_llm_completion_options,
            configuration_validator=router_triplet_validator, error_redactor=router_error_redactor,
            operation="generate_triplet_open_router",
        ),
        llm_based_generate_sql_open_router_service=LlmBasedGenerateSqlService(
            open_router_settings, client_factory=open_router_clients.create_open_router_llm_client,
            completion_options_factory=open_router_clients.open_router_llm_completion_options,
            configuration_validator=open_router_clients.validate_open_router_llm_settings,
            error_redactor=router_error_redactor, operation="llm_based_generate_sql_open_router",
        ),
        llm_based_schema_open_router_service=LlmBasedSchemaService(
            open_router_settings, client_factory=open_router_clients.create_open_router_llm_client,
            completion_options_factory=open_router_clients.open_router_llm_completion_options,
            configuration_validator=open_router_clients.validate_open_router_llm_settings,
            error_redactor=router_error_redactor, operation="llm_based_schema_open_router",
            response_model=LlmBasedSchemaOpenRouterResponse, data_model=LlmBasedSchemaOpenRouterData,
        ),
        only_triplet_based_schema_open_router_service=TripletBasedSchemaService(
            open_router_settings, client_factory=open_router_clients.create_open_router_llm_client,
            completion_options_factory=open_router_clients.open_router_llm_completion_options,
            configuration_validator=router_triplet_validator, error_redactor=router_error_redactor,
            operation="only_triplet_based_schema_open_router", enable_final_selection=False,
            generation_endpoint="/retrieve/generate_triplet_open_router",
            response_model=OnlyTripletBasedSchemaOpenRouterResponse, data_model=OnlyTripletBasedSchemaOpenRouterData,
        ),
        triplet_based_schema_open_router_service=TripletBasedSchemaService(
            open_router_settings, client_factory=open_router_clients.create_open_router_llm_client,
            completion_options_factory=open_router_clients.open_router_llm_completion_options,
            configuration_validator=router_triplet_validator, error_redactor=router_error_redactor,
            operation="triplet_based_schema_open_router", enable_final_selection=True,
            generation_endpoint="/retrieve/generate_triplet_open_router",
            response_model=TripletBasedSchemaOpenRouterResponse, data_model=TripletBasedSchemaOpenRouterData,
        ),
        indexing_schema_service=IndexingSchemaService(settings),
        indexing_triplet_service=IndexingTripletService(
            settings, fallback_rdf_directory=settings.rdf_triplet_open_router,
        ),
        indexing_triplet_open_ai_service=IndexingTripletService(
            open_ai_triplet_index_settings, client_factory=open_ai_clients.create_open_ai_embedding_client,
            configuration_validator=triplet_embedding_validator, error_redactor=error_redactor,
            operation="indexing_triplet_open_ai",
        ),
        embedding_based_schema_service=EmbeddingBasedSchemaService(settings),
        embedded_triplet_based_schema_service=EmbeddedTripletBasedSchemaService(settings),
        hybrid_only_based_schema_service=HybridOnlyBasedSchemaService(
            settings, fallback_rdf_directory=settings.rdf_triplet_open_router,
        ),
        hybrid_only_based_schema_open_ai_service=HybridOnlyBasedSchemaService(
            open_ai_triplet_index_settings, client_factory=open_ai_clients.create_open_ai_embedding_client,
            configuration_validator=hybrid_validator, error_redactor=error_redactor,
            operation="hybrid_only_based_schema_open_ai",
            indexing_endpoint="/retrieve/indexing_triplet_open_ai",
            generation_endpoint="/retrieve/generate_triplet_open_ai",
            response_model=HybridOnlyBasedSchemaOpenAiResponse, data_model=HybridOnlyBasedSchemaOpenAiData,
        ),
        embedded_triplet_based_schema_open_ai_service=EmbeddedTripletBasedSchemaService(
            open_ai_triplet_index_settings, client_factory=open_ai_clients.create_open_ai_embedding_client,
            configuration_validator=triplet_embedding_validator, error_redactor=error_redactor,
            operation="embedded_triplet_based_schema_open_ai",
            indexing_endpoint="/retrieve/indexing_triplet_open_ai",
            response_model=EmbeddedTripletBasedSchemaOpenAiResponse,
            data_model=EmbeddedTripletBasedSchemaOpenAiData,
        ),
        llm_based_generate_sql_service=LlmBasedGenerateSqlService(settings),
        generate_triplet_service=GenerateTripletService(settings),
        triplet_based_schema_service=TripletBasedSchemaService(settings),
        only_triplet_based_schema_service=TripletBasedSchemaService(
            settings, operation="only_triplet_based_schema", enable_final_selection=False,
            response_model=OnlyTripletBasedSchemaResponse, data_model=OnlyTripletBasedSchemaData,
        ),
        llm_based_schema_service=LlmBasedSchemaService(settings),
        llm_based_schema_open_ai_service=LlmBasedSchemaService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_llm_client,
            completion_options_factory=open_ai_clients.open_ai_llm_completion_options,
            configuration_validator=open_ai_clients.validate_open_ai_llm_settings,
            error_redactor=error_redactor, operation="llm_based_schema_open_ai",
            response_model=LlmBasedSchemaOpenAiResponse, data_model=LlmBasedSchemaOpenAiData,
        ),
        triplet_based_schema_open_ai_service=TripletBasedSchemaService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_llm_client,
            completion_options_factory=open_ai_clients.open_ai_llm_completion_options,
            configuration_validator=triplet_validator, error_redactor=error_redactor,
            operation="triplet_based_schema_open_ai",
            generation_endpoint="/retrieve/generate_triplet_open_ai",
            response_model=TripletBasedSchemaOpenAiResponse, data_model=TripletBasedSchemaOpenAiData,
        ),
        only_triplet_based_schema_open_ai_service=TripletBasedSchemaService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_llm_client,
            completion_options_factory=open_ai_clients.open_ai_llm_completion_options,
            configuration_validator=triplet_validator, error_redactor=error_redactor,
            operation="only_triplet_based_schema_open_ai", enable_final_selection=False,
            generation_endpoint="/retrieve/generate_triplet_open_ai",
            response_model=OnlyTripletBasedSchemaOpenAiResponse, data_model=OnlyTripletBasedSchemaOpenAiData,
        ),
        generate_triplet_open_ai_service=GenerateTripletService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_llm_client,
            completion_options_factory=open_ai_clients.open_ai_llm_completion_options,
            configuration_validator=triplet_validator, error_redactor=error_redactor,
            operation="generate_triplet_open_ai",
        ),
        indexing_schema_open_ai_service=IndexingSchemaService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_embedding_client,
            configuration_validator=embedding_validator, error_redactor=error_redactor,
            operation="indexing_schema_open_ai",
        ),
        embedding_based_schema_open_ai_service=EmbeddingBasedSchemaService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_embedding_client,
            configuration_validator=embedding_validator, error_redactor=error_redactor,
            operation="embedding_based_schema_open_ai", indexing_endpoint="/retrieve/indexing_schema_open_ai",
        ),
        llm_based_generate_sql_open_ai_service=LlmBasedGenerateSqlService(
            open_ai_settings, client_factory=open_ai_clients.create_open_ai_llm_client,
            completion_options_factory=open_ai_clients.open_ai_llm_completion_options,
            configuration_validator=open_ai_clients.validate_open_ai_llm_settings,
            error_redactor=error_redactor, operation="llm_based_generate_sql_open_ai",
        ),
    )
