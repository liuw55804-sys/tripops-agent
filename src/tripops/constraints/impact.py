from datetime import timedelta

from pydantic import BaseModel

from tripops.domain.disruptions import DisruptionEvent
from tripops.domain.plan import ItineraryItem, TravelPlan
from tripops.domain.violations import Violation


class RepairScope(BaseModel):
    affected_step_ids: tuple[str, ...] = ()
    affected_item_ids: tuple[str, ...] = ()
    preserved_item_ids: tuple[str, ...] = ()
    blocked_locked_item_ids: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    local_repair: bool = True


class ImpactAnalyzer:
    """Find the smallest plan subgraph affected by violations or a disruption."""

    def __init__(self, *, propagation_horizon_hours: int = 12) -> None:
        if propagation_horizon_hours < 1:
            raise ValueError("propagation horizon must be positive")
        self.horizon = timedelta(hours=propagation_horizon_hours)

    def analyze(
        self,
        plan: TravelPlan,
        violations: tuple[Violation, ...],
        disruption: DisruptionEvent | None = None,
    ) -> RepairScope:
        affected_items = {
            item_id for violation in violations for item_id in violation.affected_item_ids
        }
        affected_steps: set[str] = set()
        capabilities = {
            capability for violation in violations for capability in violation.repair_capabilities
        }
        reasons = [f"{violation.code.value}: {violation.message}" for violation in violations]

        if disruption is not None:
            affected_items.update(disruption.affected_item_ids)
            affected_steps.update(disruption.affected_step_ids)
            capabilities.update(disruption.required_capabilities)
            reasons.append(f"{disruption.event_type.value}: {disruption.description}")
            affected_items.update(self._items_matching_event(plan, disruption))

        affected_items.update(self._propagate_itinerary(plan, affected_items))
        affected_steps.update(self._steps_for_capabilities(plan, capabilities))
        affected_steps.update(self._dependent_steps(plan, affected_steps))

        locked = {item.id for item in plan.itinerary if item.id in affected_items and item.locked}
        affected_items -= locked
        all_items = {item.id for item in plan.itinerary}
        preserved = all_items - affected_items
        return RepairScope(
            affected_step_ids=tuple(sorted(affected_steps)),
            affected_item_ids=tuple(sorted(affected_items)),
            preserved_item_ids=tuple(sorted(preserved)),
            blocked_locked_item_ids=tuple(sorted(locked)),
            required_capabilities=tuple(sorted(capabilities)),
            reasons=tuple(reasons),
            local_repair=(
                bool(affected_items or affected_steps)
                and (not all_items or len(affected_items) < len(all_items))
            ),
        )

    def _items_matching_event(
        self,
        plan: TravelPlan,
        disruption: DisruptionEvent,
    ) -> set[str]:
        matched = set()
        locations = {location.lower() for location in disruption.locations}
        for item in plan.itinerary:
            location_matches = not locations or item.location.lower() in locations
            if location_matches and self._time_overlaps(item, disruption):
                matched.add(item.id)
        return matched

    def _propagate_itinerary(
        self,
        plan: TravelPlan,
        initially_affected: set[str],
    ) -> set[str]:
        affected = set(initially_affected)
        by_id = {item.id: item for item in plan.itinerary}
        for item_id in tuple(initially_affected):
            source = by_id.get(item_id)
            if source is None:
                continue
            source_travelers = set(source.traveler_ids)
            for candidate in plan.itinerary:
                if candidate.starts_at < source.starts_at:
                    continue
                if candidate.starts_at - source.ends_at > self.horizon:
                    continue
                shares_traveler = (
                    not source_travelers
                    or not candidate.traveler_ids
                    or bool(source_travelers & set(candidate.traveler_ids))
                )
                if shares_traveler:
                    affected.add(candidate.id)
        return affected

    @staticmethod
    def _steps_for_capabilities(plan: TravelPlan, capabilities: set[str]) -> set[str]:
        return {step.id for step in plan.steps if step.capability in capabilities}

    @staticmethod
    def _dependent_steps(plan: TravelPlan, initial: set[str]) -> set[str]:
        affected = set(initial)
        changed = True
        while changed:
            changed = False
            for step in plan.steps:
                if step.id not in affected and set(step.depends_on) & affected:
                    affected.add(step.id)
                    changed = True
        return affected

    @staticmethod
    def _time_overlaps(item: ItineraryItem, disruption: DisruptionEvent) -> bool:
        if disruption.starts_at is None and disruption.ends_at is None:
            return True
        starts = disruption.starts_at or item.starts_at
        ends = disruption.ends_at or item.ends_at
        return item.starts_at < ends and starts < item.ends_at
