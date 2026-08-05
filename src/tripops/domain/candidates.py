from decimal import Decimal

from pydantic import AnyHttpUrl, BaseModel, Field, field_serializer


class CandidateFact(BaseModel):
    """Provider-neutral candidate observation produced by a Researcher tool."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = Field(min_length=1)
    category: str = Field(min_length=1)
    tags: frozenset[str] = Field(min_length=1)
    source_capability: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    source_uri: AnyHttpUrl | None = None
    preferred_period: str | None = None
    duration_minutes: int | None = Field(default=None, ge=30, le=720)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    required_transit_minutes: int | None = Field(default=None, ge=0, le=240)
    indoor: bool | None = None

    @field_serializer("source_uri")
    def serialize_source_uri(self, value: AnyHttpUrl | None) -> str | None:
        return str(value) if value is not None else None
