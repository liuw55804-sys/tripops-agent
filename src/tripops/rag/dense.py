import hashlib
import math
from typing import Protocol

from tripops.rag.models import DocumentChunk, RetrievalHit
from tripops.rag.tokenize import tokenize


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """Deterministic offline embedding for tests; production can inject any provider."""

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 8:
            raise ValueError("embedding dimensions must be at least 8")
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            position = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[position] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return vector if norm == 0 else [value / norm for value in vector]


class InMemoryDenseRetriever:
    def __init__(
        self,
        chunks: tuple[DocumentChunk, ...],
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.chunks = chunks
        self.embedding_provider = embedding_provider
        texts = [chunk.title + "\n" + chunk.content for chunk in chunks]
        self._vectors = embedding_provider.embed(texts)

    async def retrieve(self, query: str, *, limit: int = 10) -> tuple[RetrievalHit, ...]:
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        query_vector = self.embedding_provider.embed([query])[0]
        scored = [
            (index, self._cosine(query_vector, vector))
            for index, vector in enumerate(self._vectors)
        ]
        scored = [(index, score) for index, score in scored if score > 0]
        scored.sort(key=lambda item: (-item[1], self.chunks[item[0]].id))
        return tuple(
            RetrievalHit(
                chunk=self.chunks[index],
                channel="dense",
                channel_score=score,
                channel_rank=rank,
            )
            for rank, (index, score) in enumerate(scored[:limit], start=1)
        )

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("embedding dimensions do not match")
        return sum(a * b for a, b in zip(left, right, strict=True))

