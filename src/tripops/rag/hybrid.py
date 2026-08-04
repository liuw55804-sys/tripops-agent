import asyncio
from typing import Protocol

from tripops.rag.models import RetrievalHit, RetrievalResult
from tripops.rag.rerank import Reranker
from tripops.rag.rrf import reciprocal_rank_fusion


class Retriever(Protocol):
    async def retrieve(self, query: str, *, limit: int = 10) -> tuple[RetrievalHit, ...]: ...


class HybridRetriever:
    def __init__(
        self,
        *,
        sparse: Retriever,
        dense: Retriever,
        reranker: Reranker,
        channel_limit: int = 20,
        fusion_limit: int = 30,
        rrf_weights: dict[str, float] | None = None,
    ) -> None:
        self.sparse = sparse
        self.dense = dense
        self.reranker = reranker
        self.channel_limit = channel_limit
        self.fusion_limit = fusion_limit
        self.rrf_weights = rrf_weights

    async def retrieve(self, query: str, *, limit: int = 8) -> tuple[RetrievalResult, ...]:
        sparse_hits, dense_hits = await asyncio.gather(
            self.sparse.retrieve(query, limit=self.channel_limit),
            self.dense.retrieve(query, limit=self.channel_limit),
        )
        fused = reciprocal_rank_fusion(
            (sparse_hits, dense_hits),
            weights=self.rrf_weights,
            limit=self.fusion_limit,
        )
        return self.reranker.rerank(query, fused, limit=limit)

