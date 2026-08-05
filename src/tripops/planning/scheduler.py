from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from tripops.constraints import RepairScope
from tripops.domain.constraints import ConstraintKind
from tripops.domain.plan import ItineraryItem
from tripops.domain.trip import TripRequest
from tripops.planning.catalog import ActivityPeriod, CandidateActivity, DemoDestinationCatalog
from tripops.planning.scoring import CandidateScore, GroupPreferenceScorer


class SelectionExplanation(BaseModel):
    item_id: str
    candidate_id: str
    slot_key: str
    score: float
    preference_gain: float
    fairness_gain: float
    required_gain: float
    reasons: tuple[str, ...]


class ScheduleExplanation(BaseModel):
    selected: tuple[SelectionExplanation, ...]
    rejected_candidate_ids: tuple[str, ...]
    excluded_tags: tuple[str, ...]
    required_tags: tuple[str, ...]
    preference_coverage: dict[str, float]
    jain_fairness: float = Field(ge=0, le=1)
    preserved_item_ids: tuple[str, ...]
    total_cost: Decimal = Field(ge=0)
    budget_remaining: Decimal = Field(ge=0)


class ConstraintAwareScheduler:
    """Greedy offline scheduler with deterministic hard filters and explainable scoring."""

    def __init__(self, catalog: DemoDestinationCatalog | None = None) -> None:
        self.catalog = catalog or DemoDestinationCatalog()

    def schedule(
        self,
        request: TripRequest,
        *,
        revision: int,
        previous_items: tuple[ItineraryItem, ...] = (),
        repair_scope: RepairScope | None = None,
        candidates: tuple[CandidateActivity, ...] | None = None,
    ) -> tuple[tuple[ItineraryItem, ...], ScheduleExplanation]:
        available = candidates if candidates is not None else self.catalog.candidates(request)
        excluded_tags = self._excluded_tags(request)
        required_counts = self._required_counts(request)
        eligible, rejected = self._hard_filter(available, excluded_tags)
        if not eligible:
            raise ValueError("no candidate survives hard-constraint filtering")

        scorer = GroupPreferenceScorer(request)
        remaining_required = Counter(required_counts)
        remaining_budget = request.budget
        selected: list[ItineraryItem] = []
        explanations: list[SelectionExplanation] = []
        use_counts: Counter[str] = Counter()
        traveler_ids = tuple(traveler.id for traveler in request.travelers)

        for day, period in self._slots(request.start_date, request.end_date):
            pool = [candidate for candidate in eligible if candidate.period is period]
            if not pool:
                continue
            outstanding = frozenset(tag for tag, count in remaining_required.items() if count > 0)
            ranked = sorted(
                (
                    (
                        scorer.score(
                            candidate,
                            required_tags=outstanding,
                            budget_ratio=float(candidate.cost / max(request.budget, Decimal("1"))),
                        ),
                        candidate,
                    )
                    for candidate in pool
                ),
                key=lambda pair: (
                    -(pair[0].total - use_counts[pair[1].id] * 15),
                    pair[1].cost,
                    pair[1].id,
                ),
            )
            score, candidate = ranked[0]
            slot_key = f"{day.isoformat()}:{period.value}"
            use_counts[candidate.id] += 1
            item_cost = min(candidate.cost, remaining_budget)
            remaining_budget -= item_cost
            starts_at = self._slot_start(day, period)
            ends_at = starts_at + timedelta(minutes=candidate.duration_minutes)
            item_id = f"{candidate.id}-{day.isoformat()}-{period.value}-r{revision}"
            item = ItineraryItem(
                id=item_id,
                title=candidate.title,
                location=candidate.location,
                starts_at=starts_at,
                ends_at=ends_at,
                cost=item_cost,
                category=candidate.category,
                tags=tuple(sorted(candidate.tags)),
                traveler_ids=traveler_ids,
                evidence_ids=(
                    candidate.evidence_ids
                    or (f"ev-r{revision}-{self._step_suffix(candidate)}",)
                ),
                required_transit_minutes=candidate.required_transit_minutes,
                opening_window_start=self._opening_start(day, period),
                opening_window_end=self._opening_end(day, period),
                metadata={
                    "slot_key": slot_key,
                    "candidate_id": candidate.id,
                    "indoor": candidate.indoor,
                    "dietary_risks": sorted(candidate.dietary_risks),
                    "accessibility_features": sorted(candidate.accessibility_features),
                    "selection_score": score.total,
                    "preference_matches": score.traveler_matches,
                },
            )
            selected.append(item)
            scorer.accept(candidate)
            for tag in candidate.tags:
                if remaining_required[tag] > 0:
                    remaining_required[tag] -= 1
            explanations.append(self._explain(item, candidate, score, slot_key))

        selected, preserved_ids = self._merge_preserved(
            tuple(selected), previous_items, repair_scope
        )
        total_cost = sum((item.cost for item in selected), start=Decimal("0"))
        explanation = ScheduleExplanation(
            selected=tuple(explanations),
            rejected_candidate_ids=tuple(sorted(rejected)),
            excluded_tags=tuple(sorted(excluded_tags)),
            required_tags=tuple(sorted(required_counts.elements())),
            preference_coverage=scorer.coverage_by_traveler(),
            jain_fairness=scorer.jain_fairness(),
            preserved_item_ids=preserved_ids,
            total_cost=total_cost,
            budget_remaining=max(Decimal("0"), request.budget - total_cost),
        )
        return tuple(sorted(selected, key=lambda item: (item.starts_at, item.id))), explanation

    @staticmethod
    def _hard_filter(
        candidates: tuple[CandidateActivity, ...],
        excluded_tags: frozenset[str],
    ) -> tuple[tuple[CandidateActivity, ...], set[str]]:
        eligible = []
        rejected = set()
        for candidate in candidates:
            if excluded_tags & candidate.tags:
                rejected.add(candidate.id)
            else:
                eligible.append(candidate)
        return tuple(eligible), rejected

    @staticmethod
    def _excluded_tags(request: TripRequest) -> frozenset[str]:
        tags = {
            _tag(constraint.value)
            for constraint in request.constraints
            if constraint.kind is ConstraintKind.EXCLUDED_ACTIVITY
        }
        return frozenset(tag for tag in tags if tag)

    @staticmethod
    def _required_counts(request: TripRequest) -> Counter[str]:
        counts: Counter[str] = Counter()
        for constraint in request.constraints:
            if constraint.kind is not ConstraintKind.REQUIRED_ACTIVITY:
                continue
            tag = _tag(constraint.value)
            if tag:
                counts[tag] = max(counts[tag], _minimum(constraint.value))
        return counts

    @staticmethod
    def _slots(start_date: date, end_date: date) -> tuple[tuple[date, ActivityPeriod], ...]:
        days = (end_date - start_date).days + 1
        return tuple(
            (start_date + timedelta(days=offset), period)
            for offset in range(days)
            for period in (
                ActivityPeriod.MORNING,
                ActivityPeriod.LUNCH,
                ActivityPeriod.AFTERNOON,
            )
        )

    @staticmethod
    def _slot_start(day: date, period: ActivityPeriod) -> datetime:
        start_times = {
            ActivityPeriod.MORNING: time(9, 0),
            ActivityPeriod.LUNCH: time(12, 0),
            ActivityPeriod.AFTERNOON: time(15, 0),
            ActivityPeriod.EVENING: time(19, 0),
        }
        return datetime.combine(day, start_times[period], tzinfo=UTC)

    @staticmethod
    def _opening_start(day: date, period: ActivityPeriod) -> datetime:
        del period
        return datetime.combine(day, time(8), tzinfo=UTC)

    @staticmethod
    def _opening_end(day: date, period: ActivityPeriod) -> datetime:
        del period
        return datetime.combine(day, time(22), tzinfo=UTC)

    @staticmethod
    def _step_suffix(candidate: CandidateActivity) -> str:
        return {
            "poi_search": "poi",
            "restaurant_search": "restaurant",
            "transport_search": "transport",
            "accommodation_search": "stay",
        }.get(candidate.source_capability, "poi")

    @staticmethod
    def _explain(
        item: ItineraryItem,
        candidate: CandidateActivity,
        score: CandidateScore,
        slot_key: str,
    ) -> SelectionExplanation:
        reasons = []
        if score.required_gain:
            reasons.append("satisfies required activity")
        if score.preference_gain:
            reasons.append("covers traveler preferences")
        if score.fairness_gain:
            reasons.append("improves least-served traveler coverage")
        if not reasons:
            reasons.append("best feasible candidate for the time and budget")
        return SelectionExplanation(
            item_id=item.id,
            candidate_id=candidate.id,
            slot_key=slot_key,
            score=score.total,
            preference_gain=score.preference_gain,
            fairness_gain=score.fairness_gain,
            required_gain=score.required_gain,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _merge_preserved(
        generated: tuple[ItineraryItem, ...],
        previous: tuple[ItineraryItem, ...],
        scope: RepairScope | None,
    ) -> tuple[list[ItineraryItem], tuple[str, ...]]:
        if scope is None or not previous:
            return list(generated), ()
        preserve_ids = set(scope.preserved_item_ids) | set(scope.blocked_locked_item_ids)
        preserved = [item for item in previous if item.id in preserve_ids]
        preserved_slots = {str(item.metadata.get("slot_key")) for item in preserved}
        merged = [
            item for item in generated if str(item.metadata.get("slot_key")) not in preserved_slots
        ]
        merged.extend(preserved)
        return merged, tuple(sorted(item.id for item in preserved))


def _tag(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("tag") or value.get("activity") or "").strip().casefold()
    return str(value).strip().casefold()


def _minimum(value: object) -> int:
    if not isinstance(value, dict):
        return 1
    raw = value.get("minimum", value.get("min_count", 1))
    return max(1, int(raw)) if isinstance(raw, (int, float, str)) else 1
