from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tripops.constraints import DeterministicConstraintVerifier, ImpactAnalyzer
from tripops.context.state import TripOpsState, WorkflowPhase
from tripops.domain import (
    Constraint,
    ConstraintKind,
    ConstraintPriority,
    DisruptionEvent,
    DisruptionType,
    Evidence,
    EvidenceSource,
    PlanStep,
    Traveler,
    TravelPlan,
    TripRequest,
    Violation,
    ViolationCode,
    ViolationSeverity,
)
from tripops.domain.plan import ItineraryItem

NOW = datetime(2026, 10, 1, 8, tzinfo=UTC)


def trip_request(*constraints: Constraint, budget: str = "1000") -> TripRequest:
    return TripRequest(
        id="trip-1",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3),
        budget=Decimal(budget),
        travelers=(Traveler(id="u1", display_name="Alice"),),
        constraints=constraints,
    )


def item(
    item_id: str,
    start_hour: int,
    end_hour: int,
    *,
    cost: str = "100",
    evidence_ids: tuple[str, ...] = ("ev-1",),
    **updates: object,
) -> ItineraryItem:
    values: dict[str, object] = {
        "id": item_id,
        "title": item_id,
        "location": "Kyoto",
        "starts_at": NOW.replace(hour=start_hour),
        "ends_at": NOW.replace(hour=end_hour),
        "cost": Decimal(cost),
        "traveler_ids": ("u1",),
        "evidence_ids": evidence_ids,
    }
    values.update(updates)
    return ItineraryItem.model_validate(values)


def graph_state(request: TripRequest, plan: TravelPlan, evidence: list[Evidence]) -> TripOpsState:
    return TripOpsState(
        messages=[],
        phase=WorkflowPhase.VERIFY,
        request=request,
        plan=plan,
        evidence=evidence,
        violations=[],
        selected_skills=[],
        required_capabilities=[],
    )


def fresh_evidence() -> Evidence:
    return Evidence(
        id="ev-1",
        claim="verified",
        source_type=EvidenceSource.MCP_TOOL,
        source_name="demo",
        retrieved_at=NOW,
        expires_at=NOW + timedelta(hours=2),
    )


@pytest.mark.asyncio
async def test_valid_plan_has_no_violations() -> None:
    itinerary = (item("temple", 9, 10), item("lunch", 11, 12))
    plan = TravelPlan(
        trip_id="trip-1",
        itinerary=itinerary,
        estimated_total_cost=Decimal("200"),
    )

    violations = await DeterministicConstraintVerifier(clock=lambda: NOW).verify(
        graph_state(trip_request(), plan, [fresh_evidence()])
    )

    assert violations == ()


@pytest.mark.asyncio
async def test_verifier_detects_budget_overlap_opening_and_stale_evidence() -> None:
    stale = fresh_evidence().model_copy(update={"expires_at": NOW - timedelta(minutes=1)})
    itinerary = (
        item("first", 9, 11, cost="700"),
        item(
            "second",
            10,
            12,
            cost="700",
            required_transit_minutes=60,
            opening_window_start=NOW.replace(hour=13),
            opening_window_end=NOW.replace(hour=17),
        ),
    )
    plan = TravelPlan(
        trip_id="trip-1",
        itinerary=itinerary,
        estimated_total_cost=Decimal("1400"),
    )

    violations = await DeterministicConstraintVerifier(clock=lambda: NOW).verify(
        graph_state(trip_request(), plan, [stale])
    )
    codes = {violation.code for violation in violations}

    assert ViolationCode.BUDGET_EXCEEDED in codes
    assert ViolationCode.TIME_OVERLAP in codes
    assert ViolationCode.CLOSED_AT_ARRIVAL in codes
    assert ViolationCode.STALE_EVIDENCE in codes


@pytest.mark.asyncio
async def test_verifier_checks_domain_constraints() -> None:
    constraints = (
        Constraint(
            id="required-temple",
            kind=ConstraintKind.REQUIRED_ACTIVITY,
            priority=ConstraintPriority.HARD,
            description="visit a temple",
            value={"tag": "temple", "minimum": 1},
        ),
        Constraint(
            id="exclude-bar",
            kind=ConstraintKind.EXCLUDED_ACTIVITY,
            priority=ConstraintPriority.HARD,
            description="no bars",
            value="bar",
        ),
        Constraint(
            id="diet",
            kind=ConstraintKind.DIETARY,
            priority=ConstraintPriority.HARD,
            description="no peanuts",
            value={"excluded": ["peanut"]},
            traveler_ids=("u1",),
        ),
        Constraint(
            id="access",
            kind=ConstraintKind.ACCESSIBILITY,
            priority=ConstraintPriority.HARD,
            description="wheelchair access",
            value={"required": ["wheelchair"]},
            traveler_ids=("u1",),
        ),
    )
    unsafe = item(
        "bar-stop",
        9,
        10,
        tags=("bar",),
        metadata={"dietary_risks": ["peanut"], "accessibility_features": []},
    )
    plan = TravelPlan(
        trip_id="trip-1",
        itinerary=(unsafe,),
        estimated_total_cost=Decimal("100"),
    )

    violations = await DeterministicConstraintVerifier(clock=lambda: NOW).verify(
        graph_state(trip_request(*constraints), plan, [fresh_evidence()])
    )
    codes = {violation.code for violation in violations}

    assert {
        ViolationCode.REQUIRED_PREFERENCE_MISSING,
        ViolationCode.EXCLUDED_ACTIVITY_PRESENT,
        ViolationCode.DIETARY_UNSAFE,
        ViolationCode.ACCESSIBILITY_UNMET,
    }.issubset(codes)


def test_impact_analyzer_preserves_unaffected_items() -> None:
    plan = TravelPlan(
        trip_id="trip-1",
        steps=(
            PlanStep(id="weather", title="weather", capability="weather_search"),
            PlanStep(
                id="schedule",
                title="schedule",
                capability="itinerary_planning",
                depends_on=("weather",),
            ),
        ),
        itinerary=(
            item("morning", 9, 10),
            item("afternoon", 15, 16),
            item("locked-dinner", 18, 19, locked=True),
        ),
        estimated_total_cost=Decimal("300"),
    )
    disruption = DisruptionEvent(
        id="storm",
        event_type=DisruptionType.SEVERE_WEATHER,
        description="heavy rain in the morning",
        starts_at=NOW.replace(hour=8),
        ends_at=NOW.replace(hour=11),
        locations=("Kyoto",),
        required_capabilities=("weather_search",),
    )

    scope = ImpactAnalyzer(propagation_horizon_hours=4).analyze(plan, (), disruption)

    assert scope.affected_item_ids == ("morning",)
    assert scope.affected_step_ids == ("schedule", "weather")
    assert set(scope.preserved_item_ids) == {"afternoon", "locked-dinner"}
    assert scope.local_repair


def test_impact_analyzer_uses_violation_capabilities() -> None:
    plan = TravelPlan(
        trip_id="trip-1",
        steps=(PlanStep(id="rail", title="rail", capability="transport_search"),),
        itinerary=(item("rail-trip", 9, 10), item("museum", 12, 13)),
        estimated_total_cost=Decimal("200"),
    )
    violation = Violation(
        code=ViolationCode.TRANSIT_TIME_INSUFFICIENT,
        severity=ViolationSeverity.ERROR,
        message="not enough transit time",
        affected_item_ids=("rail-trip",),
        repair_capabilities=("transport_search",),
    )

    scope = ImpactAnalyzer(propagation_horizon_hours=1).analyze(plan, (violation,))

    assert scope.affected_step_ids == ("rail",)
    assert scope.required_capabilities == ("transport_search",)

