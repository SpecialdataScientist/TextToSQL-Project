from typing import Any, Literal

from pydantic import BaseModel, Field, StrictStr

from app.schemas.triplet import RdfTriplet


class IndexingSchemaData(BaseModel):
    added_count: int
    skipped_count: int
    total_count: int
    added_tables: list[str] = Field(default_factory=list)


class IndexingSchemaResponse(BaseModel):
    data: IndexingSchemaData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class IndexingTripletData(BaseModel):
    added_count: int
    skipped_count: int
    total_count: int
    added_triplet_ids: list[str] = Field(default_factory=list)


class IndexingTripletResponse(BaseModel):
    data: IndexingTripletData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class EmbeddingBasedSchemaData(BaseModel):
    table_name: list[str]
    ddl: list[str] = Field(alias="schema")
    description: list[str]


class EmbeddingBasedSchemaResponse(BaseModel):
    data: EmbeddingBasedSchemaData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class EmbeddedTripletBasedSchemaData(EmbeddingBasedSchemaData):
    """Original table schemas ranked by their best matching triplet."""


class EmbeddedTripletBasedSchemaResponse(EmbeddingBasedSchemaResponse):
    data: EmbeddedTripletBasedSchemaData | None = None


class EmbeddedTripletBasedSchemaOpenAiData(EmbeddedTripletBasedSchemaData):
    """Original table schemas ranked by OpenAI triplet embeddings."""


class EmbeddedTripletBasedSchemaOpenAiResponse(EmbeddedTripletBasedSchemaResponse):
    data: EmbeddedTripletBasedSchemaOpenAiData | None = None


class LlmBasedSchemaData(BaseModel):
    table_name: list[str]
    ddl: list[str] = Field(alias="schema")
    description: list[str]


class LlmBasedSchemaResponse(BaseModel):
    data: LlmBasedSchemaData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class LlmBasedSchemaOpenAiData(LlmBasedSchemaData):
    """Original schema records selected through OpenAI."""


class LlmBasedSchemaOpenAiResponse(LlmBasedSchemaResponse):
    data: LlmBasedSchemaOpenAiData | None = None


class LlmBasedSchemaOpenRouterData(LlmBasedSchemaData):
    """Original schema records selected through OpenRouter."""


class LlmBasedSchemaOpenRouterResponse(LlmBasedSchemaResponse):
    data: LlmBasedSchemaOpenRouterData | None = None


class TripletBasedSchemaData(BaseModel):
    table_name: list[str]
    ddl: list[str] = Field(alias="schema")
    description: list[str]


class TripletBasedSchemaResponse(BaseModel):
    data: TripletBasedSchemaData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class TripletBasedSchemaOpenAiData(TripletBasedSchemaData):
    """Original schema records selected through OpenAI triplet retrieval."""


class TripletBasedSchemaOpenAiResponse(TripletBasedSchemaResponse):
    data: TripletBasedSchemaOpenAiData | None = None


class TripletBasedSchemaOpenRouterData(TripletBasedSchemaData):
    """Original schemas selected through OpenRouter after RDF expansion."""


class TripletBasedSchemaOpenRouterResponse(TripletBasedSchemaResponse):
    data: TripletBasedSchemaOpenRouterData | None = None


class OnlyTripletBasedSchemaData(TripletBasedSchemaData):
    """All original schema records reached from the selected seeds."""


class OnlyTripletBasedSchemaResponse(TripletBasedSchemaResponse):
    data: OnlyTripletBasedSchemaData | None = None


class OnlyTripletBasedSchemaOpenAiData(OnlyTripletBasedSchemaData):
    """All original schemas reached from seeds selected through OpenAI."""


class OnlyTripletBasedSchemaOpenAiResponse(OnlyTripletBasedSchemaResponse):
    data: OnlyTripletBasedSchemaOpenAiData | None = None


class OnlyTripletBasedSchemaOpenRouterData(OnlyTripletBasedSchemaData):
    """All original schemas reached from seeds selected through OpenRouter."""


class OnlyTripletBasedSchemaOpenRouterResponse(OnlyTripletBasedSchemaResponse):
    data: OnlyTripletBasedSchemaOpenRouterData | None = None


class HybridOnlyBasedSchemaData(TripletBasedSchemaData):
    """All original schemas reached from seeds found through triplet embeddings."""


class HybridOnlyBasedSchemaResponse(TripletBasedSchemaResponse):
    data: HybridOnlyBasedSchemaData | None = None


class HybridOnlyBasedSchemaOpenAiData(HybridOnlyBasedSchemaData):
    """All original schemas reached from seeds found through OpenAI triplet embeddings."""


class HybridOnlyBasedSchemaOpenAiResponse(HybridOnlyBasedSchemaResponse):
    data: HybridOnlyBasedSchemaOpenAiData | None = None


class LlmBasedGenerateSqlResponse(BaseModel):
    data: StrictStr | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0


class GenerateTripletResponse(BaseModel):
    data: list[RdfTriplet] | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    elapsed_time: float = 0.0
