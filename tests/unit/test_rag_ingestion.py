from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tripops.rag import (
    ChunkingPolicy,
    DocumentChunker,
    FreshnessPolicy,
    HybridCorpus,
    SourceDocument,
    SourceVolatility,
)

NOW = datetime(2030, 10, 1, tzinfo=UTC)


def document(
    document_id: str,
    content: str,
    *,
    volatility: SourceVolatility = SourceVolatility.STATIC,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        title=f"Document {document_id}",
        content=content,
        source_uri=f"https://example.com/{document_id}",
        fetched_at=NOW,
        volatility=volatility,
        metadata={"locale": "en-US"},
    )


def test_chunker_normalizes_splits_overlaps_and_adds_provenance() -> None:
    policy = ChunkingPolicy(max_chars=120, overlap_chars=20, min_chars=20)
    chunker = DocumentChunker(policy)
    content = "\n\n".join(
        [
            "The museum opens at nine and requires an advance reservation.",
            "Wheelchair visitors should use the accessible east entrance.",
            "Tickets are refundable until twenty four hours before entry.",
            "The final admission time is four thirty in the afternoon.",
        ]
    )

    result = chunker.ingest((document("museum", content),))

    assert len(result.chunks) >= 2
    assert all(len(chunk.content) <= 120 for chunk in result.chunks)
    assert {chunk.metadata["document_id"] for chunk in result.chunks} == {"museum"}
    assert {chunk.metadata["chunk_total"] for chunk in result.chunks} == {
        len(result.chunks)
    }
    assert all(chunk.id.startswith("chunk-") for chunk in result.chunks)


def test_normalized_duplicate_documents_are_not_indexed_twice() -> None:
    chunker = DocumentChunker()
    first = document("first", "Rail pass rules   and prices")
    duplicate = document("duplicate", "Rail pass rules and prices")

    result = chunker.ingest((first, duplicate))

    assert len(result.chunks) == 1
    assert result.duplicate_document_ids == ("duplicate",)


@pytest.mark.parametrize(
    ("volatility", "expected_delta"),
    [
        (SourceVolatility.WEATHER, timedelta(hours=1)),
        (SourceVolatility.AVAILABILITY, timedelta(minutes=30)),
        (SourceVolatility.PRICE, timedelta(hours=6)),
        (SourceVolatility.POLICY, timedelta(days=30)),
        (SourceVolatility.STATIC, timedelta(days=180)),
    ],
)
def test_freshness_policy_sets_expiry_by_volatility(
    volatility: SourceVolatility,
    expected_delta: timedelta,
) -> None:
    source = document("freshness", "A sufficiently detailed source document", volatility=volatility)

    expiry = FreshnessPolicy().expiry_for(source)

    assert expiry - NOW == expected_delta


def test_explicit_valid_until_overrides_default_freshness() -> None:
    expected = NOW + timedelta(minutes=5)
    source = document("override", "A sufficiently detailed source document").model_copy(
        update={"valid_until": expected}
    )

    assert FreshnessPolicy().expiry_for(source) == expected


@pytest.mark.asyncio
async def test_corpus_replace_builds_searchable_versioned_snapshot() -> None:
    corpus = HybridCorpus(embedding_dimensions=64)
    stats = corpus.replace(
        (
            document("rail", "Kyoto rail service requires a reserved seat for this train."),
            document("weather", "Heavy rain is expected in Osaka tomorrow afternoon."),
        )
    )

    results = await corpus.retrieve("Kyoto reserved rail seat", limit=2)

    assert stats.version == 1
    assert stats.document_count == 2
    assert stats.chunk_count == 2
    assert results
    assert results[0].chunk.metadata["document_id"] == "rail"
    assert results[0].citation_id.startswith("CIT-")


@pytest.mark.asyncio
async def test_corpus_upsert_replaces_same_id_and_preserves_other_documents() -> None:
    corpus = HybridCorpus(embedding_dimensions=64)
    corpus.replace(
        (
            document("rail", "Old rail timetable information."),
            document("policy", "Cancellation is allowed before departure."),
        )
    )

    stats = corpus.upsert((document("rail", "New express train timetable."),))
    results = await corpus.retrieve("express train", limit=3)

    assert stats.version == 2
    assert stats.document_count == 2
    assert {chunk.metadata["document_id"] for chunk in corpus.chunks()} == {
        "rail",
        "policy",
    }
    assert results[0].chunk.content == "New express train timetable."


def test_source_document_rejects_inverted_validity_window() -> None:
    with pytest.raises(ValidationError, match="valid_until"):
        SourceDocument(
            id="bad",
            title="Bad",
            content="Invalid window",
            source_uri="https://example.com/bad",
            valid_from=NOW,
            valid_until=NOW - timedelta(seconds=1),
        )


def test_chunking_policy_rejects_overlap_larger_than_chunk() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        ChunkingPolicy(max_chars=100, overlap_chars=100)
