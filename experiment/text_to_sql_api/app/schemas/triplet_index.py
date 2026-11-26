import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, StrictStr, TypeAdapter, model_validator

from app.schemas.triplet import RdfTriplet


def make_triplet_id(triplet: RdfTriplet) -> str:
    subject, predicate, target = triplet
    key = json.dumps([subject.table_name, predicate, target.table_name], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class IndexedTriplet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triplet_id: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    triplet: RdfTriplet

    @model_validator(mode="after")
    def require_matching_id(self):
        if self.triplet_id != make_triplet_id(self.triplet):
            raise ValueError("triplet_id must match the subject, predicate and object names")
        return self

    def embedding_text(self) -> str:
        subject, predicate, target = self.triplet
        return (
            f"subject_table_name: {subject.table_name}\n"
            f"subject_description: {subject.description}\n"
            f"predicate: {predicate}\n"
            f"object_table_name: {target.table_name}\n"
            f"object_description: {target.description}"
        )


INDEXED_TRIPLETS = TypeAdapter(list[IndexedTriplet])
