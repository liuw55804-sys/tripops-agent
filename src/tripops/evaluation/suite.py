from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tripops.domain.constraints import Constraint, ConstraintKind, ConstraintPriority
from tripops.domain.disruptions import DisruptionEvent, DisruptionType
from tripops.domain.evidence import Evidence, EvidenceSource
from tripops.domain.plan import ItineraryItem, PlanStep, TravelPlan
from tripops.domain.trip import Traveler, TripRequest
from tripops.domain.violations import ViolationCode
from tripops.evaluation.models import CaseCategory, EvaluationCase, FaultMode

_BASE_DAY = datetime(2030, 5, 2, tzinfo=UTC)


def build_travelplanner_suite() -> tuple[EvaluationCase, ...]:
    """Build the fixed 80-case resume-grade benchmark described in docs/scope.md."""
    standard = tuple(_standard_case(index) for index in range(50))
    dynamic = tuple(_dynamic_case(index) for index in range(20))
    faults = tuple(_fault_case(index) for index in range(10))
    suite = standard + dynamic + faults
    if len({case.id for case in suite}) != len(suite):
        raise AssertionError("evaluation case ids must be unique")
    return suite


def _standard_case(index: int) -> EvaluationCase:
    variant = index % 10
    request, plan, evidence = _baseline(f"standard-{index:02d}")
    expected: frozenset[ViolationCode] = frozenset()

    if variant == 1:
        request = request.model_copy(update={"budget": Decimal("200")})
        expected = frozenset({ViolationCode.BUDGET_EXCEEDED})
    elif variant == 2:
        plan = _replace_item_times(plan, "lunch", hour=10, duration_hours=2)
        expected = frozenset({ViolationCode.TIME_OVERLAP})
    elif variant == 3:
        plan = _replace_item_times(plan, "lunch", hour=12, duration_hours=1, transit=150)
        expected = frozenset({ViolationCode.TRANSIT_TIME_INSUFFICIENT})
    elif variant == 4:
        item = _item(plan, "museum")
        changed = item.model_copy(
            update={
                "opening_window_start": _BASE_DAY.replace(hour=10),
                "opening_window_end": _BASE_DAY.replace(hour=11),
            }
        )
        plan = _replace_item(plan, changed)
        expected = frozenset({ViolationCode.CLOSED_AT_ARRIVAL})
    elif variant == 5:
        request = _with_constraint(
            request,
            Constraint(
                id="required-hiking",
                kind=ConstraintKind.REQUIRED_ACTIVITY,
                priority=ConstraintPriority.HARD,
                description="Must include hiking",
                value={"tag": "hiking", "minimum": 1},
            ),
        )
        expected = frozenset({ViolationCode.REQUIRED_PREFERENCE_MISSING})
    elif variant == 6:
        request = _with_constraint(
            request,
            Constraint(
                id="exclude-museum",
                kind=ConstraintKind.EXCLUDED_ACTIVITY,
                priority=ConstraintPriority.HARD,
                description="No museums",
                value="museum",
            ),
        )
        expected = frozenset({ViolationCode.EXCLUDED_ACTIVITY_PRESENT})
    elif variant == 7:
        request = _with_constraint(
            request,
            Constraint(
                id="dietary-shellfish",
                kind=ConstraintKind.DIETARY,
                priority=ConstraintPriority.HARD,
                description="Alice cannot eat shellfish",
                value={"excluded": ["shellfish"]},
                traveler_ids=("alice",),
            ),
        )
        lunch = _item(plan, "lunch").model_copy(
            update={"metadata": {"dietary_risks": ["shellfish"]}}
        )
        plan = _replace_item(plan, lunch)
        expected = frozenset({ViolationCode.DIETARY_UNSAFE})
    elif variant == 8:
        request = _with_constraint(
            request,
            Constraint(
                id="access-wheelchair",
                kind=ConstraintKind.ACCESSIBILITY,
                priority=ConstraintPriority.HARD,
                description="Bob needs step-free access",
                value={"required": ["step_free"]},
                traveler_ids=("bob",),
            ),
        )
        museum = _item(plan, "museum").model_copy(
            update={"metadata": {"accessibility_features": ["elevator"]}}
        )
        plan = _replace_item(plan, museum)
        expected = frozenset({ViolationCode.ACCESSIBILITY_UNMET})
    elif variant == 9:
        item = _item(plan, "museum")
        changed = item.model_copy(
            update={
                "starts_at": item.starts_at + timedelta(days=3),
                "ends_at": item.ends_at + timedelta(days=3),
            }
        )
        plan = _replace_item(plan, changed)
        expected = frozenset({ViolationCode.DATE_OUT_OF_RANGE})

    return EvaluationCase(
        id=f"standard-{index:02d}",
        category=CaseCategory.STANDARD,
        description=f"Deterministic constraint scenario variant {variant}",
        request=request,
        plan=plan,
        evidence=evidence,
        expected_violation_codes=expected,
    )


def _dynamic_case(index: int) -> EvaluationCase:
    case_id = f"dynamic-{index:02d}"
    request, plan, evidence = _baseline(case_id)
    event_types = (
        DisruptionType.TRANSPORT_CANCELLED,
        DisruptionType.SEVERE_WEATHER,
        DisruptionType.VENUE_CLOSED,
        DisruptionType.USER_CONSTRAINT_CHANGED,
        DisruptionType.PRICE_CHANGED,
    )
    disruption = DisruptionEvent(
        id=f"event-{index:02d}",
        event_type=event_types[index % len(event_types)],
        description="Mid-trip change invalidates lunch and downstream activities",
        starts_at=_BASE_DAY.replace(hour=11, minute=30),
        ends_at=_BASE_DAY.replace(hour=13, minute=30),
        affected_item_ids=("lunch",),
        required_capabilities=("restaurant_search", "itinerary_planning"),
    )
    return EvaluationCase(
        id=case_id,
        category=CaseCategory.DYNAMIC,
        description=f"Local replan scenario for {disruption.event_type.value}",
        request=request,
        plan=plan,
        evidence=evidence,
        disruption=disruption,
        expected_affected_item_ids=frozenset({"lunch", "museum"}),
        expected_preserved_item_ids=frozenset({"park"}),
    )


def _fault_case(index: int) -> EvaluationCase:
    case_id = f"fault-{index:02d}"
    request, plan, evidence = _baseline(case_id)
    modes = (
        FaultMode.TIMEOUT,
        FaultMode.PROVIDER_ERROR,
        FaultMode.MALFORMED_RESPONSE,
        FaultMode.RATE_LIMIT,
        FaultMode.PRIMARY_UNAVAILABLE,
    )
    mode = modes[index % len(modes)]
    return EvaluationCase(
        id=case_id,
        category=CaseCategory.FAULT,
        description=f"Tool middleware degradation scenario: {mode.value}",
        request=request,
        plan=plan,
        evidence=evidence,
        fault_mode=mode,
    )


def _baseline(case_id: str) -> tuple[TripRequest, TravelPlan, tuple[Evidence, ...]]:
    travelers = (
        Traveler(id="alice", display_name="Alice", preferences=("park", "food")),
        Traveler(id="bob", display_name="Bob", preferences=("museum", "food")),
    )
    request = TripRequest(
        id=case_id,
        origin="Shanghai",
        destinations=("Hangzhou",),
        start_date=date(2030, 5, 1),
        end_date=date(2030, 5, 3),
        budget=Decimal("1000"),
        travelers=travelers,
    )
    evidence = tuple(
        Evidence(
            id=f"evidence-{item_id}",
            claim=f"Verified details for {item_id}",
            source_type=EvidenceSource.LOCAL_TOOL,
            source_name="offline-fixture",
            retrieved_at=datetime(2030, 5, 1, tzinfo=UTC),
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            confidence=0.95,
        )
        for item_id in ("park", "lunch", "museum")
    )
    itinerary = (
        ItineraryItem(
            id="park",
            title="West Lake Park Walk",
            location="Hangzhou",
            starts_at=_BASE_DAY.replace(hour=9),
            ends_at=_BASE_DAY.replace(hour=11),
            cost=Decimal("50"),
            category="park",
            tags=("park", "nature"),
            traveler_ids=("alice", "bob"),
            evidence_ids=("evidence-park",),
        ),
        ItineraryItem(
            id="lunch",
            title="Local Lunch",
            location="Hangzhou",
            starts_at=_BASE_DAY.replace(hour=12),
            ends_at=_BASE_DAY.replace(hour=13),
            cost=Decimal("150"),
            category="food",
            tags=("food",),
            traveler_ids=("alice", "bob"),
            evidence_ids=("evidence-lunch",),
            required_transit_minutes=30,
        ),
        ItineraryItem(
            id="museum",
            title="City Museum",
            location="Hangzhou",
            starts_at=_BASE_DAY.replace(hour=15),
            ends_at=_BASE_DAY.replace(hour=17),
            cost=Decimal("100"),
            category="museum",
            tags=("museum", "culture"),
            traveler_ids=("alice", "bob"),
            evidence_ids=("evidence-museum",),
            required_transit_minutes=30,
        ),
    )
    steps = (
        PlanStep(id="research", title="Research", capability="restaurant_search"),
        PlanStep(
            id="plan",
            title="Plan itinerary",
            capability="itinerary_planning",
            depends_on=("research",),
        ),
    )
    plan = TravelPlan(
        trip_id=case_id,
        steps=steps,
        itinerary=itinerary,
        estimated_total_cost=Decimal("300"),
    )
    return request, plan, evidence


def _with_constraint(request: TripRequest, constraint: Constraint) -> TripRequest:
    return request.model_copy(update={"constraints": request.constraints + (constraint,)})


def _item(plan: TravelPlan, item_id: str) -> ItineraryItem:
    return next(item for item in plan.itinerary if item.id == item_id)


def _replace_item(plan: TravelPlan, replacement: ItineraryItem) -> TravelPlan:
    itinerary = tuple(
        replacement if item.id == replacement.id else item for item in plan.itinerary
    )
    return plan.model_copy(update={"itinerary": itinerary})


def _replace_item_times(
    plan: TravelPlan,
    item_id: str,
    *,
    hour: int,
    duration_hours: int,
    transit: int | None = None,
) -> TravelPlan:
    item = _item(plan, item_id)
    changed = item.model_copy(
        update={
            "starts_at": _BASE_DAY.replace(hour=hour),
            "ends_at": _BASE_DAY.replace(hour=hour) + timedelta(hours=duration_hours),
            "required_transit_minutes": (
                item.required_transit_minutes if transit is None else transit
            ),
        }
    )
    return _replace_item(plan, changed)
