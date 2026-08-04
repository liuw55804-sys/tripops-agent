import math
from collections import Counter

from tripops.rag.models import DocumentChunk, RetrievalHit
from tripops.rag.tokenize import tokenize


class BM25Retriever:
    """Storage-independent BM25 implementation used for keyword recall."""

    def __init__(
        self,
        chunks: tuple[DocumentChunk, ...],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("invalid BM25 parameters")
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._tokens = [tokenize(chunk.content + " " + chunk.title) for chunk in chunks]
        self._frequencies = [Counter(tokens) for tokens in self._tokens]
        self._average_length = (
            sum(len(tokens) for tokens in self._tokens) / len(self._tokens) if chunks else 0
        )
        self._document_frequency: Counter[str] = Counter()
        for tokens in self._tokens:
            self._document_frequency.update(set(tokens))

    async def retrieve(self, query: str, *, limit: int = 10) -> tuple[RetrievalHit, ...]:
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        query_tokens = tokenize(query)
        scored = [
            (index, self._score(query_tokens, index)) for index in range(len(self.chunks))
        ]
        scored = [(index, score) for index, score in scored if score > 0]
        scored.sort(key=lambda item: (-item[1], self.chunks[item[0]].id))
        return tuple(
            RetrievalHit(
                chunk=self.chunks[index],
                channel="bm25",
                channel_score=score,
                channel_rank=rank,
            )
            for rank, (index, score) in enumerate(scored[:limit], start=1)
        )

    def _score(self, query_tokens: list[str], document_index: int) -> float:
        if not self.chunks or not query_tokens:
            return 0
        frequencies = self._frequencies[document_index]
        document_length = len(self._tokens[document_index])
        score = 0.0
        for token in set(query_tokens):
            term_frequency = frequencies[token]
            if not term_frequency:
                continue
            document_frequency = self._document_frequency[token]
            inverse_frequency = math.log(
                1 + (len(self.chunks) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            normalization = 1 - self.b
            if self._average_length:
                normalization += self.b * document_length / self._average_length
            numerator = term_frequency * (self.k1 + 1)
            denominator = term_frequency + self.k1 * normalization
            score += inverse_frequency * numerator / denominator
        return score

