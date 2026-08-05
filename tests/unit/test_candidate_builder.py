from datetime import date
from decimal import Decimal

from tripops.agents.models import ResearchResult
from tripops.domain import CandidateFact, Traveler, TripRequest
from tripops.planning import ConstraintAwareScheduler, EvidenceCandidateBuilder


def request() -> TripRequest:
    return TripRequest(
        id="candidate-trip",
        origin="Shanghai",
        destinations=("Sydney",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 1),
        budget=Decimal("3000"),
        travelers=(Traveler(id="alice", display_name="Alice"),),
    )


def fact(
    identifier: str,
    period: str,
    *,
    category: str = "landmark",
) -> CandidateFact:
    return CandidateFact(
        id=identifier,
        title=f"Cited {identifier}",
        location="Sydney",
        category=category,
        tags=frozenset({category}),
        source_capability="poi_search",
        evidence_id=f"ev-{identifier}",
        source_uri=f"https://example.com/{identifier}",
        preferred_period=period,
        duration_minutes=90,
        estimated_cost=Decimal("25"),
    )


def result(*facts: CandidateFact) -> ResearchResult:
    return ResearchResult(
        step_id="r1-poi",
        plan_revision=1,
        agent_name="live_researcher",
        success=True,
        candidate_facts=facts,
    )


def test_candidate_builder_uses_full_fallback_when_no_facts_exist() -> None:
    built = EvidenceCandidateBuilder().build(request(), (), revision=1)

    assert built.source_mode == "fallback"
    assert built.fact_count == 0
    assert built.fallback_count == len(built.candidates)


def test_candidate_builder_supplements_only_missing_periods() -> None:
    built = EvidenceCandidateBuilder().build(
        request(),
        (result(fact("harbour", "morning"), fact("museum", "afternoon")),),
        revision=1,
    )

    cited = {item.id: item for item in built.candidates if item.id in {"harbour", "museum"}}
    assert built.source_mode == "mixed"
    assert built.fact_count == 2
    assert cited["harbour"].evidence_ids == ("ev-harbour",)
    assert {item.period.value for item in built.candidates} == {
        "morning",
        "lunch",
        "afternoon",
    }


def test_scheduler_preserves_candidate_citations() -> None:
    facts = (
        fact("harbour", "morning"),
        fact("lunch", "lunch", category="restaurant"),
        fact("museum", "afternoon", category="museum"),
    )
    built = EvidenceCandidateBuilder().build(request(), (result(*facts),), revision=1)

    itinerary, _ = ConstraintAwareScheduler().schedule(
        request(),
        revision=1,
        candidates=built.candidates,
    )

    assert built.source_mode == "real"
    assert len(itinerary) == 3
    assert {item.evidence_ids[0] for item in itinerary} == {
        "ev-harbour",
        "ev-lunch",
        "ev-museum",
    }
