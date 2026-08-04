import hashlib
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from tripops.rag.bm25 import BM25Retriever
from tripops.rag.dense import HashingEmbeddingProvider, InMemoryDenseRetriever
from tripops.rag.hybrid import HybridRetriever
from tripops.rag.models import DocumentChunk, RetrievalResult
from tripops.rag.rerank import LexicalReranker, Reranker


class SourceVolatility(StrEnum):
    STATIC = "static"
    POLICY = "policy"
    PRICE = "price"
    AVAILABILITY = "availability"
    WEATHER = "weather"


class SourceDocument(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_uri: AnyHttpUrl
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    volatility: SourceVolatility = SourceVolatility.STATIC
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_validity_window(self) -> "SourceDocument":
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class ChunkingPolicy(BaseModel):
    max_chars: int = Field(default=900, ge=100, le=8_000)
    overlap_chars: int = Field(default=120, ge=0, le=2_000)
    min_chars: int = Field(default=40, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingPolicy":
        if self.overlap_chars >= self.max_chars:
            raise ValueError("chunk overlap must be smaller than max_chars")
        return self


class FreshnessPolicy(BaseModel):
    static_seconds: int = 180 * 24 * 3600
    policy_seconds: int = 30 * 24 * 3600
    price_seconds: int = 6 * 3600
    availability_seconds: int = 30 * 60
    weather_seconds: int = 60 * 60

    def expiry_for(self, document: SourceDocument) -> datetime:
        if document.valid_until is not None:
            return document.valid_until
        seconds = {
            SourceVolatility.STATIC: self.static_seconds,
            SourceVolatility.POLICY: self.policy_seconds,
            SourceVolatility.PRICE: self.price_seconds,
            SourceVolatility.AVAILABILITY: self.availability_seconds,
            SourceVolatility.WEATHER: self.weather_seconds,
        }[document.volatility]
        return document.fetched_at + timedelta(seconds=seconds)


class CorpusStats(BaseModel):
    version: int = Field(ge=0)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    duplicate_documents: int = Field(ge=0)
    generated_at: datetime


class IngestionResult(BaseModel):
    chunks: tuple[DocumentChunk, ...]
    duplicate_document_ids: tuple[str, ...]


class DocumentChunker:
    def __init__(
        self,
        policy: ChunkingPolicy | None = None,
        freshness: FreshnessPolicy | None = None,
    ) -> None:
        self.policy = policy or ChunkingPolicy()
        self.freshness = freshness or FreshnessPolicy()

    def ingest(self, documents: tuple[SourceDocument, ...]) -> IngestionResult:
        chunks: list[DocumentChunk] = []
        duplicate_ids: list[str] = []
        seen_fingerprints: set[str] = set()
        for document in documents:
            normalized = self.normalize(document.content)
            fingerprint = self._fingerprint(normalized)
            if fingerprint in seen_fingerprints:
                duplicate_ids.append(document.id)
                continue
            seen_fingerprints.add(fingerprint)
            pieces = self._split(normalized)
            chunks.extend(
                self._to_chunk(document, piece, index, len(pieces), fingerprint)
                for index, piece in enumerate(pieces)
            )
        return IngestionResult(
            chunks=tuple(chunks),
            duplicate_document_ids=tuple(sorted(duplicate_ids)),
        )

    @staticmethod
    def normalize(content: str) -> str:
        normalized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in content.splitlines()]
        compact = "\n".join(line for line in normalized_lines if line)
        return re.sub(r"\n{3,}", "\n\n", compact).strip()

    def _split(self, content: str) -> tuple[str, ...]:
        if len(content) <= self.policy.max_chars:
            return (content,)
        segments = [
            segment.strip()
            for segment in re.split(r"(?<=[。！？.!?])\s+|\n+", content)
            if segment.strip()
        ]
        chunks: list[str] = []
        current = ""
        for segment in segments:
            if len(segment) > self.policy.max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_long_segment(segment))
                continue
            candidate = f"{current}\n{segment}".strip() if current else segment
            if len(candidate) <= self.policy.max_chars:
                current = candidate
                continue
            chunks.append(current)
            overlap = current[-self.policy.overlap_chars :] if self.policy.overlap_chars else ""
            current = f"{overlap}\n{segment}".strip()
        if current:
            chunks.append(current)
        return tuple(chunk for chunk in chunks if len(chunk) >= self.policy.min_chars)

    def _split_long_segment(self, segment: str) -> list[str]:
        step = self.policy.max_chars - self.policy.overlap_chars
        return [
            segment[start : start + self.policy.max_chars]
            for start in range(0, len(segment), step)
            if len(segment[start : start + self.policy.max_chars]) >= self.policy.min_chars
        ]

    def _to_chunk(
        self,
        document: SourceDocument,
        content: str,
        index: int,
        total: int,
        fingerprint: str,
    ) -> DocumentChunk:
        identity = self._fingerprint(f"{document.id}:{index}:{content}")[:20]
        return DocumentChunk(
            id=f"chunk-{identity}",
            content=content,
            title=document.title,
            source_uri=document.source_uri,
            updated_at=document.fetched_at,
            valid_from=document.valid_from,
            valid_until=self.freshness.expiry_for(document),
            metadata={
                **document.metadata,
                "document_id": document.id,
                "document_fingerprint": fingerprint,
                "chunk_index": index,
                "chunk_total": total,
                "volatility": document.volatility.value,
            },
        )

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HybridCorpus:
    """Versioned immutable retrieval snapshots with atomic corpus replacement."""

    def __init__(
        self,
        *,
        chunker: DocumentChunker | None = None,
        reranker: Reranker | None = None,
        embedding_dimensions: int = 256,
    ) -> None:
        self.chunker = chunker or DocumentChunker()
        self.reranker = reranker or LexicalReranker()
        self.embedding_dimensions = embedding_dimensions
        self._lock = RLock()
        self._version = 0
        self._documents: tuple[SourceDocument, ...] = ()
        self._chunks: tuple[DocumentChunk, ...] = ()
        self._duplicates: tuple[str, ...] = ()
        self._retriever = self._build_retriever(())

    def replace(self, documents: tuple[SourceDocument, ...]) -> CorpusStats:
        result = self.chunker.ingest(documents)
        retriever = self._build_retriever(result.chunks)
        with self._lock:
            self._version += 1
            self._documents = documents
            self._chunks = result.chunks
            self._duplicates = result.duplicate_document_ids
            self._retriever = retriever
            return self.stats()

    def upsert(self, documents: tuple[SourceDocument, ...]) -> CorpusStats:
        with self._lock:
            existing = {document.id: document for document in self._documents}
        existing.update({document.id: document for document in documents})
        return self.replace(tuple(existing[key] for key in sorted(existing)))

    async def retrieve(self, query: str, *, limit: int = 8) -> tuple[RetrievalResult, ...]:
        with self._lock:
            retriever = self._retriever
        return await retriever.retrieve(query, limit=limit)

    def stats(self) -> CorpusStats:
        with self._lock:
            return CorpusStats(
                version=self._version,
                document_count=len(self._documents),
                chunk_count=len(self._chunks),
                duplicate_documents=len(self._duplicates),
                generated_at=datetime.now(UTC),
            )

    def chunks(self) -> tuple[DocumentChunk, ...]:
        with self._lock:
            return self._chunks

    def _build_retriever(self, chunks: tuple[DocumentChunk, ...]) -> HybridRetriever:
        embeddings = HashingEmbeddingProvider(self.embedding_dimensions)
        return HybridRetriever(
            sparse=BM25Retriever(chunks),
            dense=InMemoryDenseRetriever(chunks, embeddings),
            reranker=self.reranker,
        )
