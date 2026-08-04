from datetime import date
from decimal import Decimal

import pytest

from tripops.agents import HybridRAGResearcher, ResearchTask
from tripops.domain import PlanStep, Traveler, TripRequest
from tripops.rag import (
    BM25Retriever,
    DocumentChunk,
    HashingEmbeddingProvider,
    HybridRetriever,
    InMemoryDenseRetriever,
    LexicalReranker,
)


@pytest.mark.asyncio
async def test_hybrid_rag_researcher_returns_citation_evidence() -> None:
    chunks = (
        DocumentChunk(
            id="refund",
            title="Kyoto rail refund",
            content="Rail tickets can be refunded before departure.",
            source_uri="https://example.test/refund",
        ),
    )
    retriever = HybridRetriever(
        sparse=BM25Retriever(chunks),
        dense=InMemoryDenseRetriever(chunks, HashingEmbeddingProvider()),
        reranker=LexicalReranker(),
    )
    researcher = HybridRAGResearcher(
        name="policy_researcher",
        capabilities=frozenset({"policy_search"}),
        retriever=retriever,
    )
    request = TripRequest(
        id="trip-1",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3),
        budget=Decimal("5000"),
        travelers=(Traveler(id="u1", display_name="Alice"),),
        raw_requirement="Need the rail refund policy",
    )

    result = await researcher.research(
        ResearchTask(
            request=request,
            step=PlanStep(id="policy", title="rail refund", capability="policy_search"),
            plan_revision=2,
        )
    )

    assert result.success
    assert result.evidence[0].id == "CIT-refund"
    assert result.evidence[0].metadata["plan_revision"] == 2
    assert result.evidence[0].source_uri is not None

