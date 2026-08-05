from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, Field, field_serializer


class EvidenceSource(StrEnum):
    MCP_TOOL = "mcp_tool"
    LOCAL_TOOL = "local_tool"
    KNOWLEDGE_BASE = "knowledge_base"
    USER = "user"
    DERIVED = "derived"


class Evidence(BaseModel):
    """A fact with provenance. Researchers return evidence, not prose reports."""

    id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    source_type: EvidenceSource
    source_name: str = Field(min_length=1)
    source_uri: AnyHttpUrl | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_at: datetime | None = None
    expires_at: datetime | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    artifact_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_serializer("source_uri")
    def serialize_source_uri(self, value: AnyHttpUrl | None) -> str | None:
        return str(value) if value is not None else None

    def is_stale(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.expires_at is not None and self.expires_at <= current
