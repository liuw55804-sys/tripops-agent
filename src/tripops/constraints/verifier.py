from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tripops.context.state import TripOpsState
from tripops.domain.constraints import Constraint, ConstraintKind, ConstraintPriority
from tripops.domain.evidence import Evidence
from tripops.domain.plan import ItineraryItem, TravelPlan
from tripops.domain.trip import TripRequest
from tripops.domain.violations import Violation, ViolationCode, ViolationSeverity


class DeterministicConstraintVerifier:
    """Verify machine-checkable travel constraints without asking an LLM."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))

    async def verify(self, state: TripOpsState) -> tuple[Violation, ...]:
        request = state.get("request")
        plan = state.get("plan")
        if request is None or plan is None:
            return (
                self._violation(
                    ViolationCode.POLICY_VIOLATION,
                    "request and plan are required for verification",
                    capabilities=("itinerary_planning",),
                ),
            )

        evidence = {item.id: item for item in state.get("evidence", [])}
        violations: list[Violation] = []
        violations.extend(self._verify_budget(request, plan))
        violations.extend(self._verify_dates(request, plan))
        violations.extend(self._verify_time_and_transit(request, plan))
        violations.extend(self._verify_opening_windows(plan))
        violations.extend(self._verify_evidence(plan, evidence))
        violations.extend(self._verify_constraints(request, plan))
        return tuple(
            sorted(
                violations,
                key=lambda item: (
                    item.severity.value,
                    item.code.value,
                    item.affected_item_ids,
                    item.constraint_ids,
                ),
            )
        )

    def _verify_budget(self, request: TripRequest, plan: TravelPlan) -> list[Violation]:
        actual_total = sum((item.cost for item in plan.itinerary), start=Decimal("0"))
        violations = []
        unknown_item_ids = tuple(
            item.id for item in plan.itinerary if item.metadata.get("cost_status") == "unknown"
        )
        raw_unpriced = (
            plan.budget_ledger.unpriced_kinds
            if plan.budget_ledger is not None
            else plan.metadata.get("unpriced_capabilities", ())
        )
        unpriced_capabilities = (
            tuple(str(item) for item in raw_unpriced)
            if isinstance(raw_unpriced, (list, tuple, set))
            else ()
        )
        if unknown_item_ids or unpriced_capabilities:
            missing = ", ".join(unpriced_capabilities) or "scheduled items"
            violations.append(
                self._violation(
                    ViolationCode.BUDGET_UNVERIFIED,
                    f"budget cannot be verified until prices are confirmed for: {missing}",
                    severity=ViolationSeverity.WARNING,
                    item_ids=unknown_item_ids,
                    hint="confirm transport, accommodation, meal and ticket prices",
                    capabilities=unpriced_capabilities,
                )
            )
        compared_total = (
            plan.budget_ledger.total_low if plan.budget_ledger is not None else actual_total
        )
        if compared_total > request.budget:
            violations.append(
                self._violation(
                    ViolationCode.BUDGET_EXCEEDED,
                    (
                        f"estimated trip cost {compared_total} exceeds budget "
                        f"{request.budget} {request.currency}"
                    ),
                    item_ids=tuple(item.id for item in plan.itinerary if item.cost > 0),
                    hint="replace high-cost transport, stays or activities",
                    capabilities=(
                        "itinerary_planning",
                        "transport_search",
                        "accommodation_search",
                    ),
                )
            )
        if abs(plan.estimated_total_cost - actual_total) > Decimal("0.01"):
            violations.append(
                self._violation(
                    ViolationCode.COST_MISMATCH,
                    (
                        f"estimated total {plan.estimated_total_cost} does not match "
                        f"item total {actual_total}"
                    ),
                    severity=ViolationSeverity.WARNING,
                    capabilities=("itinerary_planning",),
                )
            )
        return violations

    def _verify_dates(self, request: TripRequest, plan: TravelPlan) -> list[Violation]:
        return [
            self._violation(
                ViolationCode.DATE_OUT_OF_RANGE,
                f"item {item.id} falls outside trip date range",
                item_ids=(item.id,),
                capabilities=("itinerary_planning",),
            )
            for item in plan.itinerary
            if item.starts_at.date() < request.start_date or item.ends_at.date() > request.end_date
        ]

    def _verify_time_and_transit(
        self,
        request: TripRequest,
        plan: TravelPlan,
    ) -> list[Violation]:
        schedule: defaultdict[str, list[ItineraryItem]] = defaultdict(list)
        all_travelers = tuple(traveler.id for traveler in request.travelers)
        for item in plan.itinerary:
            for traveler_id in item.traveler_ids or all_travelers:
                schedule[traveler_id].append(item)

        violations: list[Violation] = []
        seen_overlap_pairs: set[tuple[str, str]] = set()
        seen_transit_pairs: set[tuple[str, str]] = set()
        for items in schedule.values():
            ordered = sorted(items, key=lambda item: (item.starts_at, item.id))
            for previous, current in zip(ordered, ordered[1:], strict=False):
                pair = (previous.id, current.id)
                if current.starts_at < previous.ends_at:
                    if pair not in seen_overlap_pairs:
                        seen_overlap_pairs.add(pair)
                        violations.append(
                            self._violation(
                                ViolationCode.TIME_OVERLAP,
                                f"items {previous.id} and {current.id} overlap",
                                item_ids=pair,
                                capabilities=("itinerary_planning",),
                            )
                        )
                    continue
                available_minutes = (current.starts_at - previous.ends_at).total_seconds() / 60
                if (
                    available_minutes < current.required_transit_minutes
                    and pair not in seen_transit_pairs
                ):
                    seen_transit_pairs.add(pair)
                    violations.append(
                        self._violation(
                            ViolationCode.TRANSIT_TIME_INSUFFICIENT,
                            (
                                f"only {available_minutes:.0f} minutes between {previous.id} and "
                                f"{current.id}; requires {current.required_transit_minutes}"
                            ),
                            item_ids=pair,
                            capabilities=("transport_search", "itinerary_planning"),
                        )
                    )
        return violations

    def _verify_opening_windows(self, plan: TravelPlan) -> list[Violation]:
        violations = []
        for item in plan.itinerary:
            if item.opening_window_start is None or item.opening_window_end is None:
                continue
            if item.starts_at < item.opening_window_start or item.ends_at > item.opening_window_end:
                violations.append(
                    self._violation(
                        ViolationCode.CLOSED_AT_ARRIVAL,
                        f"item {item.id} is scheduled outside its opening window",
                        item_ids=(item.id,),
                        capabilities=("poi_search", "itinerary_planning"),
                    )
                )
        return violations

    def _verify_evidence(
        self,
        plan: TravelPlan,
        evidence: dict[str, Evidence],
    ) -> list[Violation]:
        violations = []
        now = self.clock()
        for item in plan.itinerary:
            if not item.evidence_ids:
                violations.append(
                    self._violation(
                        ViolationCode.EVIDENCE_MISSING,
                        f"item {item.id} has no supporting evidence",
                        item_ids=(item.id,),
                        capabilities=("evidence_synthesis",),
                    )
                )
                continue
            unknown = tuple(
                sorted(
                    evidence_id for evidence_id in item.evidence_ids if evidence_id not in evidence
                )
            )
            if unknown:
                violations.append(
                    self._violation(
                        ViolationCode.EVIDENCE_MISSING,
                        f"item {item.id} references unknown evidence: {', '.join(unknown)}",
                        item_ids=(item.id,),
                        capabilities=("evidence_synthesis",),
                    )
                )
            stale = tuple(
                sorted(
                    evidence_id
                    for evidence_id in item.evidence_ids
                    if evidence_id in evidence and evidence[evidence_id].is_stale(now=now)
                )
            )
            if stale:
                violations.append(
                    self._violation(
                        ViolationCode.STALE_EVIDENCE,
                        f"item {item.id} uses stale evidence: {', '.join(stale)}",
                        item_ids=(item.id,),
                        capabilities=("evidence_synthesis",),
                    )
                )
        return violations

    def _verify_constraints(self, request: TripRequest, plan: TravelPlan) -> list[Violation]:
        violations: list[Violation] = []
        for constraint in request.constraints:
            if constraint.kind is ConstraintKind.REQUIRED_ACTIVITY:
                violations.extend(self._verify_required_activity(constraint, plan))
            elif constraint.kind is ConstraintKind.EXCLUDED_ACTIVITY:
                violations.extend(self._verify_excluded_activity(constraint, plan))
            elif constraint.kind is ConstraintKind.DIETARY:
                violations.extend(self._verify_dietary(constraint, plan))
            elif constraint.kind is ConstraintKind.ACCESSIBILITY:
                violations.extend(self._verify_accessibility(constraint, plan))
        return violations

    def _verify_required_activity(
        self,
        constraint: Constraint,
        plan: TravelPlan,
    ) -> list[Violation]:
        if constraint.priority not in {ConstraintPriority.HARD, ConstraintPriority.REQUIRED}:
            return []
        tag, minimum = self._tag_and_minimum(constraint.value)
        matching = [item for item in plan.itinerary if self._matches_tag(item, tag)]
        if len(matching) >= minimum:
            return []
        return [
            self._violation(
                ViolationCode.REQUIRED_PREFERENCE_MISSING,
                f"required activity '{tag}' appears {len(matching)} times; requires {minimum}",
                constraint_ids=(constraint.id,),
                capabilities=("poi_search", "itinerary_planning"),
            )
        ]

    def _verify_excluded_activity(
        self,
        constraint: Constraint,
        plan: TravelPlan,
    ) -> list[Violation]:
        tag, _ = self._tag_and_minimum(constraint.value)
        matching = [item.id for item in plan.itinerary if self._matches_tag(item, tag)]
        if not matching:
            return []
        return [
            self._violation(
                ViolationCode.EXCLUDED_ACTIVITY_PRESENT,
                f"excluded activity '{tag}' is present",
                item_ids=tuple(matching),
                constraint_ids=(constraint.id,),
                capabilities=("itinerary_planning",),
            )
        ]

    def _verify_dietary(self, constraint: Constraint, plan: TravelPlan) -> list[Violation]:
        excluded = set(self._as_string_list(constraint.value, key="excluded"))
        affected = []
        for item in plan.itinerary:
            if constraint.traveler_ids and not set(constraint.traveler_ids) & set(
                item.traveler_ids
            ):
                continue
            risks = set(self._metadata_strings(item, "dietary_risks"))
            if excluded & risks:
                affected.append(item.id)
        if not affected:
            return []
        return [
            self._violation(
                ViolationCode.DIETARY_UNSAFE,
                f"dietary exclusions are violated by: {', '.join(affected)}",
                item_ids=tuple(affected),
                constraint_ids=(constraint.id,),
                capabilities=("restaurant_search", "itinerary_planning"),
            )
        ]

    def _verify_accessibility(
        self,
        constraint: Constraint,
        plan: TravelPlan,
    ) -> list[Violation]:
        required = set(self._as_string_list(constraint.value, key="required"))
        affected = []
        for item in plan.itinerary:
            if constraint.traveler_ids and not set(constraint.traveler_ids) & set(
                item.traveler_ids
            ):
                continue
            if "accessibility_features" not in item.metadata:
                continue
            features = set(self._metadata_strings(item, "accessibility_features"))
            if not required.issubset(features):
                affected.append(item.id)
        if not affected:
            return []
        return [
            self._violation(
                ViolationCode.ACCESSIBILITY_UNMET,
                f"accessibility requirements are unmet by: {', '.join(affected)}",
                item_ids=tuple(affected),
                constraint_ids=(constraint.id,),
                capabilities=("accessibility_search", "itinerary_planning"),
            )
        ]

    @staticmethod
    def _tag_and_minimum(value: Any) -> tuple[str, int]:
        if isinstance(value, dict):
            tag = str(value.get("tag") or value.get("activity") or "").strip().lower()
            raw_minimum = value.get("minimum", value.get("min_count", 1))
            minimum = int(raw_minimum) if isinstance(raw_minimum, (int, float, str)) else 1
        else:
            tag = str(value).strip().lower()
            minimum = 1
        return tag, max(minimum, 1)

    @staticmethod
    def _matches_tag(item: ItineraryItem, tag: str) -> bool:
        searchable = {item.category.lower(), *(value.lower() for value in item.tags)}
        return tag in searchable or tag in item.title.lower()

    @staticmethod
    def _as_string_list(value: Any, *, key: str) -> tuple[str, ...]:
        raw = value.get(key, []) if isinstance(value, dict) else value
        if isinstance(raw, str):
            return (raw.lower(),)
        if isinstance(raw, (list, tuple, set)):
            return tuple(str(item).lower() for item in raw)
        return ()

    @staticmethod
    def _metadata_strings(item: ItineraryItem, key: str) -> tuple[str, ...]:
        raw = item.metadata.get(key, [])
        if isinstance(raw, str):
            return (raw.lower(),)
        if isinstance(raw, (list, tuple, set)):
            return tuple(str(value).lower() for value in raw)
        return ()

    @staticmethod
    def _violation(
        code: ViolationCode,
        message: str,
        *,
        severity: ViolationSeverity = ViolationSeverity.ERROR,
        item_ids: tuple[str, ...] = (),
        constraint_ids: tuple[str, ...] = (),
        hint: str | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> Violation:
        return Violation(
            code=code,
            severity=severity,
            message=message,
            affected_item_ids=item_ids,
            constraint_ids=constraint_ids,
            repair_hint=hint,
            repair_capabilities=capabilities,
        )
