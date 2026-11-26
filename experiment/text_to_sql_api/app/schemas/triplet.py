from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, TypeAdapter, field_validator


class TripletEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)

    @field_validator("table_name", "description")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Triplet entity fields must not be blank")
        return value


TripletPredicate = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1)]
RdfTriplet = tuple[TripletEntity, TripletPredicate, TripletEntity]
RDF_TRIPLETS = TypeAdapter(list[RdfTriplet])
