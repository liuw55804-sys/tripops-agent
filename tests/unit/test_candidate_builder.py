from collections import Counter
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


def test_candidate_builder_supplements_thin_periods_for_multi_day_trip() -> None:
    three_day_request = request().model_copy(update={"end_date": date(2030, 10, 3)})
    facts = (
        fact("harbour", "morning"),
        fact("lunch", "lunch", category="restaurant"),
        fact("museum", "afternoon", category="museum"),
    )

    built = EvidenceCandidateBuilder().build(
        three_day_request,
        (result(*facts),),
        revision=1,
    )

    assert built.source_mode == "mixed"
    assert built.fallback_count > 0
    counts = Counter(item.period for item in built.candidates)
    assert counts["morning"] >= 3
    assert counts["afternoon"] >= 3


def test_multi_city_schedule_is_contiguous_and_reserves_transition_slot() -> None:
    multi_city = request().model_copy(
        update={
            "destinations": ("Sydney", "Melbourne"),
            "end_date": date(2030, 10, 4),
        }
    )
    built = EvidenceCandidateBuilder().build(multi_city, (), revision=1)

    itinerary, explanation = ConstraintAwareScheduler().schedule(
        multi_city,
        revision=1,
        candidates=built.candidates,
    )

    by_day = {
        day: {
            item.metadata["scheduled_destination"]
            for item in itinerary
            if item.starts_at.date() == day
        }
        for day in (date(2030, 10, 1), date(2030, 10, 2), date(2030, 10, 3), date(2030, 10, 4))
    }
    assert by_day[date(2030, 10, 1)] == {"Sydney"}
    assert by_day[date(2030, 10, 2)] == {"Sydney"}
    assert by_day[date(2030, 10, 3)] == {"Melbourne"}
    assert by_day[date(2030, 10, 4)] == {"Melbourne"}
    transfer = next(item for item in itinerary if item.category == "transport")
    assert transfer.starts_at.date() == date(2030, 10, 3)
    assert "Sydney → Melbourne" in transfer.title
    assert transfer.metadata["cost_status"] == "unknown"
    assert transfer.id in explanation.unknown_cost_item_ids
