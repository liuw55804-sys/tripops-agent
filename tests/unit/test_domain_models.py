from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tripops.domain import (
    Constraint,
    ConstraintKind,
    ConstraintPriority,
    Evidence,
    EvidenceSource,
    PlanStep,
    Traveler,
    TravelPlan,
    TripRequest,
)


def test_trip_request_accepts_known_traveler_constraint() -> None:
    traveler = Traveler(id="u1", display_name="Alice")
    constraint = Constraint(
        id="c1",
        kind=ConstraintKind.DIETARY,
        priority=ConstraintPriority.HARD,
        description="Alice does not eat peanuts",
        value={"excluded": ["peanut"]},
        traveler_ids=("u1",),
    )

    request = TripRequest(
        id="trip-1",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 5),
        budget=Decimal("12000"),
        travelers=(traveler,),
        constraints=(constraint,),
    )

    assert request.travelers[0].id == "u1"


def test_trip_request_splits_ordered_multi_destination_route() -> None:
    request = TripRequest(
        id="multi-city",
        origin="上海",
        destinations=("悉尼、墨尔本",),
        start_date=date(2026, 9, 30),
        end_date=date(2026, 10, 8),
        budget=Decimal("21000"),
        travelers=(Traveler(id="u1", display_name="Alice"),),
    )

    assert request.destinations == ("悉尼", "墨尔本")


def test_trip_request_rejects_unknown_traveler_constraint() -> None:
    constraint = Constraint(
        id="c1",
        kind=ConstraintKind.PREFERENCE,
        priority=ConstraintPriority.PREFERRED,
        description="Unknown traveler preference",
        value="museum",
        traveler_ids=("missing",),
    )

    with pytest.raises(ValidationError, match="unknown travelers"):
        TripRequest(
            id="trip-1",
            origin="Shanghai",
            destinations=("Kyoto",),
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 5),
            budget=Decimal("12000"),
            travelers=(Traveler(id="u1", display_name="Alice"),),
            constraints=(constraint,),
        )


def test_plan_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown steps"):
        TravelPlan(
            trip_id="trip-1",
            steps=(
                PlanStep(
                    id="plan",
                    title="Build plan",
                    capability="planning",
                    depends_on=("research-missing",),
                ),
            ),
        )


def test_evidence_expiration_is_time_aware() -> None:
    now = datetime.now(UTC)
    evidence = Evidence(
        id="ev-1",
        claim="The museum is closed on Monday",
        source_type=EvidenceSource.MCP_TOOL,
        source_name="poi-search",
        expires_at=now - timedelta(seconds=1),
    )

    assert evidence.is_stale(now=now)
