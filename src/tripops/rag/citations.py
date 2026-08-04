from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field

from tripops.domain.evidence import Evidence, EvidenceSource
from tripops.rag.models import RetrievalResult


class Citation(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_uri: AnyHttpUrl
    chunk_id: str = Field(min_length=1)
    retrieved_at: datetime
    valid_until: datetime | None = None
    quote: str = Field(min_length=1)


class CitationBundle(BaseModel):
    citations: tuple[Citation, ...]

    @classmethod
    def from_results(cls, results: tuple[RetrievalResult, ...]) -> "CitationBundle":
        return cls(
            citations=tuple(
                Citation(
                    id=result.citation_id,
                    title=result.chunk.title,
                    source_uri=result.chunk.source_uri,
                    chunk_id=result.chunk.id,
                    retrieved_at=result.chunk.updated_at,
                    valid_until=result.chunk.valid_until,
                    quote=result.chunk.content,
                )
                for result in results
            )
        )

    def to_evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=citation.id,
                claim=citation.quote,
                source_type=EvidenceSource.KNOWLEDGE_BASE,
                source_name=citation.title,
                source_uri=citation.source_uri,
                retrieved_at=citation.retrieved_at,
                expires_at=citation.valid_until,
                metadata={"chunk_id": citation.chunk_id},
            )
            for citation in self.citations
        )


class CitationValidator:
    def validate_ids(
        self,
        cited_ids: tuple[str, ...],
        bundle: CitationBundle,
    ) -> tuple[str, ...]:
        known = {citation.id for citation in bundle.citations}
        return tuple(sorted(set(cited_ids) - known))
