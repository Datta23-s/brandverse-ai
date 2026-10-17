"""The base of every API contract: strict, immutable, and honest in its output schema."""

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    # Unknown fields are refused. Output schemas list every field as required: the API always
    # emits defaulted fields too (the frontend's zod schemas are checked against them).
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )
