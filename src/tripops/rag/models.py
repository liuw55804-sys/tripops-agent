from datetime import UTC, datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field


class DocumentChunk(BaseModel):
    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_uri: AnyHttpUrl
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalHit(BaseModel):
    chunk: DocumentChunk
    channel: str = Field(min_length=1)
    channel_score: float
    channel_rank: int = Field(ge=1)


class RetrievalResult(BaseModel):
    chunk: DocumentChunk
    fused_score: float = Field(ge=0)
    rerank_score: float | None = None
    rank: int = Field(ge=1)
    citation_id: str = Field(min_length=1)
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    channel_scores: dict[str, float] = Field(default_factory=dict)
