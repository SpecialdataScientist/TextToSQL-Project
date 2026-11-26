from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class GeneratedSqlOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: StrictStr = Field(min_length=1, description="One PostgreSQL SQL statement without explanation or Markdown")

    @field_validator("sql")
    @classmethod
    def require_sql_text(cls, value: str) -> str:
        value = value.strip()
        if not value or value.startswith("```"):
            raise ValueError("sql must contain SQL text without Markdown fences")
        return value


class GeneratedTripletRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_table_name: StrictStr = Field(min_length=1)
    predicate: StrictStr = Field(min_length=1, description="A short English phrase describing the directed relation")
    object_table_name: StrictStr = Field(min_length=1)

    @field_validator("subject_table_name", "object_table_name")
    @classmethod
    def require_exact_table_name(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Table names must be nonblank and have no surrounding whitespace")
        return value

    @field_validator("predicate")
    @classmethod
    def require_predicate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("predicate must not be blank")
        return value


class GeneratedTripletsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triplets: list[GeneratedTripletRelation]


class SelectedSchemaTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1, description="Why this table is needed to answer the current query")

    @field_validator("table_name", "reason")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Selection fields must not be blank")
        return value

    @field_validator("table_name")
    @classmethod
    def require_exact_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("table_name must not have surrounding whitespace")
        return value


class SchemaTableSelectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[SelectedSchemaTable]
