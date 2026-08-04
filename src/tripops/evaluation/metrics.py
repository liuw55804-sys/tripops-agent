from collections.abc import Iterable

from tripops.domain.evidence import Evidence
from tripops.domain.plan import TravelPlan
from tripops.domain.trip import TripRequest
from tripops.evaluation.models import EvaluationResult, MetricSummary


def safe_ratio(numerator: int | float, denominator: int | float, *, empty: float = 1.0) -> float:
    return float(numerator / denominator) if denominator else empty


def set_recall(expected: set[str] | frozenset[str], actual: set[str] | frozenset[str]) -> float:
    return safe_ratio(len(set(expected) & set(actual)), len(expected))


def preference_scores(request: TripRequest, plan: TravelPlan) -> tuple[float, float]:
    """Return mean preference coverage and Jain's fairness across travelers."""
    scores: list[float] = []
    for traveler in request.travelers:
        preferences = {value.casefold() for value in traveler.preferences}
        if not preferences:
            scores.append(1.0)
            continue
        matched: set[str] = set()
        for item in plan.itinerary:
            if item.traveler_ids and traveler.id not in item.traveler_ids:
                continue
            item_values = {item.category.casefold(), *(tag.casefold() for tag in item.tags)}
            matched.update(preferences & item_values)
        scores.append(safe_ratio(len(matched), len(preferences)))
    if not scores:
        return 1.0, 1.0
    mean = sum(scores) / len(scores)
    squared_sum = sum(scores) ** 2
    fairness = safe_ratio(squared_sum, len(scores) * sum(score**2 for score in scores))
    return mean, fairness


def citation_scores(plan: TravelPlan, evidence: Iterable[Evidence]) -> tuple[float, float]:
    evidence_by_id = {item.id: item for item in evidence}
    cited = [evidence_id for item in plan.itinerary for evidence_id in item.evidence_ids]
    if not cited:
        return 0.0, 0.0
    known = [evidence_by_id[evidence_id] for evidence_id in cited if evidence_id in evidence_by_id]
    correctness = safe_ratio(len(known), len(cited))
    fresh = sum(not item.is_stale() for item in known)
    freshness = safe_ratio(fresh, len(known), empty=0.0)
    return correctness, freshness


def summarize(results: Iterable[EvaluationResult]) -> MetricSummary:
    items = list(results)
    count = len(items)
    true_positives = sum(item.true_positives for item in items)
    false_positives = sum(item.false_positives for item in items)
    false_negatives = sum(item.false_negatives for item in items)
    precision = safe_ratio(true_positives, true_positives + false_positives)
    recall = safe_ratio(true_positives, true_positives + false_negatives)
    f1 = safe_ratio(2 * precision * recall, precision + recall)

    def average(attribute: str) -> float:
        return safe_ratio(sum(float(getattr(item, attribute)) for item in items), count)

    return MetricSummary(
        case_count=count,
        hard_constraint_pass_rate=average("hard_constraint_pass"),
        violation_precision=precision,
        violation_recall=recall,
        violation_f1=f1,
        preference_coverage=average("preference_coverage"),
        group_fairness=average("group_fairness"),
        citation_correctness=average("citation_correctness"),
        citation_freshness=average("citation_freshness"),
        affected_item_recall=average("affected_item_recall"),
        plan_preservation=average("plan_preservation"),
        degradation_success_rate=average("degradation_success"),
        average_latency_ms=average("latency_ms"),
    )
