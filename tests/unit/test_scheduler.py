from datetime import date
from decimal import Decimal

from tripops.constraints import RepairScope
from tripops.domain import Constraint, ConstraintKind, ConstraintPriority, Traveler, TripRequest
from tripops.planning import ConstraintAwareScheduler, DemoDestinationCatalog
from tripops.planning.scoring import GroupPreferenceScorer


def request(*constraints: Constraint, budget: str = "1200") -> TripRequest:
    return TripRequest(
        id="scheduler-trip",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 2),
        budget=Decimal(budget),
        travelers=(
            Traveler(id="alice", display_name="Alice", preferences=("park", "food")),
            Traveler(id="bob", display_name="Bob", preferences=("museum", "culture")),
        ),
        constraints=constraints,
    )


def test_scheduler_builds_non_overlapping_budgeted_explainable_itinerary() -> None:
    itinerary, explanation = ConstraintAwareScheduler().schedule(request(), revision=1)

    assert len(itinerary) == 6
    assert sum((item.cost for item in itinerary), Decimal("0")) <= Decimal("1200")
    assert all(item.evidence_ids for item in itinerary)
    assert all(item.metadata["slot_key"] for item in itinerary)
    assert all(previous.ends_at <= current.starts_at for previous, current in zip(
        itinerary, itinerary[1:], strict=False
    ))
    assert explanation.preference_coverage == {"alice": 1.0, "bob": 1.0}
    assert explanation.jain_fairness == 1.0


def test_required_activity_is_synthesized_and_excluded_tag_is_filtered() -> None:
    required = Constraint(
        id="required-hiking",
        kind=ConstraintKind.REQUIRED_ACTIVITY,
        priority=ConstraintPriority.HARD,
        description="Include hiking",
        value={"tag": "hiking", "minimum": 1},
    )
    excluded = Constraint(
        id="exclude-museum",
        kind=ConstraintKind.EXCLUDED_ACTIVITY,
        priority=ConstraintPriority.HARD,
        description="Avoid museums",
        value="museum",
    )

    itinerary, explanation = ConstraintAwareScheduler().schedule(
        request(required, excluded), revision=1
    )
    tags = {tag for item in itinerary for tag in item.tags}

    assert "hiking" in tags
    assert "museum" not in tags
    assert explanation.required_tags == ("hiking",)
    assert any("museum" in candidate_id for candidate_id in explanation.rejected_candidate_ids)


def test_tiny_budget_never_produces_budget_violation_by_construction() -> None:
    itinerary, explanation = ConstraintAwareScheduler().schedule(
        request(budget="50"), revision=1
    )

    assert explanation.total_cost == Decimal("50")
    assert explanation.budget_remaining == Decimal("0")
    assert sum((item.cost for item in itinerary), Decimal("0")) == Decimal("50")


def test_local_replan_preserves_unaffected_slots_and_replaces_affected_slot() -> None:
    scheduler = ConstraintAwareScheduler()
    initial, _ = scheduler.schedule(request(), revision=1)
    affected = initial[2]
    preserved = tuple(item.id for item in initial if item.id != affected.id)
    scope = RepairScope(
        affected_item_ids=(affected.id,),
        preserved_item_ids=preserved,
        required_capabilities=("poi_search",),
        local_repair=True,
    )

    revised, explanation = scheduler.schedule(
        request(), revision=2, previous_items=initial, repair_scope=scope
    )

    revised_ids = {item.id for item in revised}
    assert set(preserved).issubset(revised_ids)
    assert affected.id not in revised_ids
    assert set(explanation.preserved_item_ids) == set(preserved)
    assert len(revised) == len(initial)


def test_group_scorer_rewards_least_served_traveler() -> None:
    trip = request()
    candidates = DemoDestinationCatalog().candidates(trip)
    park = next(item for item in candidates if "park" in item.tags)
    museum = next(item for item in candidates if "museum" in item.tags)
    scorer = GroupPreferenceScorer(trip)
    scorer.accept(park)

    museum_score = scorer.score(
        museum,
        required_tags=frozenset(),
        budget_ratio=0.1,
    )

    assert museum_score.fairness_gain > 0
    assert museum_score.traveler_matches["bob"]
