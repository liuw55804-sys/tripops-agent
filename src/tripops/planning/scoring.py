from collections import defaultdict
from dataclasses import dataclass

from tripops.domain.trip import TripRequest
from tripops.planning.catalog import CandidateActivity


@dataclass(frozen=True, slots=True)
class CandidateScore:
    candidate_id: str
    total: float
    preference_gain: float
    fairness_gain: float
    required_gain: float
    affordability: float
    traveler_matches: dict[str, tuple[str, ...]]


class GroupPreferenceScorer:
    """Score marginal utility while preventing one traveler from dominating the plan."""

    def __init__(self, request: TripRequest) -> None:
        self.request = request
        self._covered: dict[str, set[str]] = defaultdict(set)

    def score(
        self,
        candidate: CandidateActivity,
        *,
        required_tags: frozenset[str],
        budget_ratio: float,
    ) -> CandidateScore:
        matches: dict[str, tuple[str, ...]] = {}
        marginal: list[float] = []
        coverage_before = self.coverage_by_traveler()
        for traveler in self.request.travelers:
            preferences = {item.casefold() for item in traveler.preferences}
            found = tuple(sorted((preferences & set(candidate.tags)) - self._covered[traveler.id]))
            matches[traveler.id] = found
            marginal.append(float(len(found)))

        preference_gain = sum(marginal)
        least_covered = min(coverage_before.values(), default=0.0)
        fairness_gain = sum(
            gain
            for traveler, gain in zip(self.request.travelers, marginal, strict=True)
            if coverage_before[traveler.id] <= least_covered
        )
        required_gain = float(len(required_tags & set(candidate.tags)))
        affordability = max(0.0, 1.0 - budget_ratio)
        total = preference_gain * 3 + fairness_gain * 2 + required_gain * 10 + affordability
        return CandidateScore(
            candidate_id=candidate.id,
            total=total,
            preference_gain=preference_gain,
            fairness_gain=fairness_gain,
            required_gain=required_gain,
            affordability=affordability,
            traveler_matches=matches,
        )

    def accept(self, candidate: CandidateActivity) -> None:
        for traveler in self.request.travelers:
            preferences = {item.casefold() for item in traveler.preferences}
            self._covered[traveler.id].update(preferences & set(candidate.tags))

    def coverage_by_traveler(self) -> dict[str, float]:
        result = {}
        for traveler in self.request.travelers:
            preferences = {item.casefold() for item in traveler.preferences}
            result[traveler.id] = (
                len(self._covered[traveler.id]) / len(preferences) if preferences else 1.0
            )
        return result

    def jain_fairness(self) -> float:
        values = list(self.coverage_by_traveler().values())
        denominator = len(values) * sum(value**2 for value in values)
        return (sum(values) ** 2 / denominator) if denominator else 1.0
