from importlib import import_module
from typing import Any, Protocol

from tripops.rag.models import RetrievalResult
from tripops.rag.tokenize import tokenize


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        results: tuple[RetrievalResult, ...],
        *,
        limit: int,
    ) -> tuple[RetrievalResult, ...]: ...


class LexicalReranker:
    """Cheap deterministic fallback when a cross-encoder is unavailable."""

    def rerank(
        self,
        query: str,
        results: tuple[RetrievalResult, ...],
        *,
        limit: int,
    ) -> tuple[RetrievalResult, ...]:
        query_terms = set(tokenize(query))
        scored = []
        for result in results:
            document_terms = set(tokenize(result.chunk.title + " " + result.chunk.content))
            union = query_terms | document_terms
            similarity = len(query_terms & document_terms) / len(union) if union else 0
            scored.append((result, similarity))
        scored.sort(key=lambda item: (-item[1], -item[0].fused_score, item[0].chunk.id))
        return tuple(
            result.model_copy(update={"rerank_score": score, "rank": rank})
            for rank, (result, score) in enumerate(scored[:limit], start=1)
        )


class SentenceTransformerReranker:
    """Lazy cross-encoder adapter; the heavy dependency is optional under `rag`."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        try:
            module = import_module("sentence_transformers")
        except ImportError as exc:
            raise RuntimeError("install TripOps with the 'rag' extra to use CrossEncoder") from exc
        cross_encoder: Any = module.CrossEncoder
        self._model: Any = cross_encoder(model_name)

    def rerank(
        self,
        query: str,
        results: tuple[RetrievalResult, ...],
        *,
        limit: int,
    ) -> tuple[RetrievalResult, ...]:
        pairs = [(query, result.chunk.content) for result in results]
        predictions = self._model.predict(pairs)
        scored = list(zip(results, (float(value) for value in predictions), strict=True))
        scored.sort(key=lambda item: (-item[1], -item[0].fused_score, item[0].chunk.id))
        return tuple(
            result.model_copy(update={"rerank_score": score, "rank": rank})
            for rank, (result, score) in enumerate(scored[:limit], start=1)
        )
