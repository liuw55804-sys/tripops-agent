from datetime import UTC, datetime, timedelta

import pytest

from tripops.rag import (
    BM25Retriever,
    CitationBundle,
    CitationValidator,
    DocumentChunk,
    HashingEmbeddingProvider,
    HybridRetriever,
    InMemoryDenseRetriever,
    LexicalReranker,
    RetrievalHit,
    reciprocal_rank_fusion,
)


def chunks() -> tuple[DocumentChunk, ...]:
    return (
        DocumentChunk(
            id="rail-refund",
            title="Rail refund policy",
            content="Kyoto rail tickets can be refunded before departure with a service fee.",
            source_uri="https://example.test/rail-refund",
        ),
        DocumentChunk(
            id="hotel-policy",
            title="Hotel cancellation",
            content="Hotels allow free cancellation until two days before check-in.",
            source_uri="https://example.test/hotel",
        ),
        DocumentChunk(
            id="temple-hours",
            title="Temple opening hours",
            content="The Kyoto temple opens at 09:00 and closes at 17:00.",
            source_uri="https://example.test/temple",
        ),
    )


@pytest.mark.asyncio
async def test_hybrid_retrieval_fuses_channels_and_adds_citations() -> None:
    corpus = chunks()
    hybrid = HybridRetriever(
        sparse=BM25Retriever(corpus),
        dense=InMemoryDenseRetriever(corpus, HashingEmbeddingProvider()),
        reranker=LexicalReranker(),
    )

    results = await hybrid.retrieve("Kyoto rail ticket refund", limit=2)
    bundle = CitationBundle.from_results(results)

    assert results[0].chunk.id == "rail-refund"
    assert set(results[0].channel_ranks) == {"bm25", "dense"}
    assert results[0].citation_id == "CIT-rail-refund"
    assert bundle.to_evidence()[0].source_uri is not None


def test_rrf_rewards_document_found_by_multiple_channels() -> None:
    corpus = chunks()
    shared = RetrievalHit(
        chunk=corpus[0], channel="bm25", channel_score=5, channel_rank=2
    )
    sparse_only = RetrievalHit(
        chunk=corpus[1], channel="bm25", channel_score=8, channel_rank=1
    )
    dense_shared = RetrievalHit(
        chunk=corpus[0], channel="dense", channel_score=0.8, channel_rank=1
    )

    fused = reciprocal_rank_fusion(((sparse_only, shared), (dense_shared,)))

    assert fused[0].chunk.id == "rail-refund"


def test_citation_validator_reports_unknown_ids() -> None:
    bundle = CitationBundle.from_results(
        reciprocal_rank_fusion(
            (
                (
                    RetrievalHit(
                        chunk=chunks()[0],
                        channel="bm25",
                        channel_score=1,
                        channel_rank=1,
                    ),
                ),
            )
        )
    )

    unknown = CitationValidator().validate_ids(("CIT-rail-refund", "CIT-invented"), bundle)

    assert unknown == ("CIT-invented",)


def test_citation_preserves_validity_window() -> None:
    valid_until = datetime.now(UTC) + timedelta(hours=1)
    chunk = chunks()[0].model_copy(update={"valid_until": valid_until})
    result = reciprocal_rank_fusion(
        ((RetrievalHit(chunk=chunk, channel="bm25", channel_score=1, channel_rank=1),),)
    )[0]

    evidence = CitationBundle.from_results((result,)).to_evidence()[0]

    assert evidence.expires_at == valid_until

