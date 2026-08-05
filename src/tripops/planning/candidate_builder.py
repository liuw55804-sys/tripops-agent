from collections import Counter
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from tripops.agents.models import ResearchResult
from tripops.domain.candidates import CandidateFact
from tripops.domain.trip import TripRequest
from tripops.planning.catalog import (
    ActivityPeriod,
    CandidateActivity,
    CandidateCostStatus,
    DemoDestinationCatalog,
)
from tripops.planning.route import allocate_destination_days, route_transitions


class CandidateBuildResult(BaseModel):
    candidates: tuple[CandidateActivity, ...]
    source_mode: str
    fact_count: int
    fallback_count: int
    unpriced_capabilities: tuple[str, ...] = ()


class CandidateBuilder(Protocol):
    def build(
        self,
        request: TripRequest,
        results: tuple[ResearchResult, ...],
        *,
        revision: int,
    ) -> CandidateBuildResult: ...


class EvidenceCandidateBuilder:
    """Normalize cited Researcher facts and fill only missing schedule periods."""

    def __init__(self, fallback_catalog: DemoDestinationCatalog | None = None) -> None:
        self.fallback_catalog = fallback_catalog or DemoDestinationCatalog()

    def build(
        self,
        request: TripRequest,
        results: tuple[ResearchResult, ...],
        *,
        revision: int,
    ) -> CandidateBuildResult:
        facts = tuple(
            fact
            for result in results
            if result.plan_revision == revision and result.success
            for fact in result.candidate_facts
        )
        real = self._deduplicate(tuple(self._to_candidate(fact) for fact in facts))
        latest_evidence_ids = self._latest_evidence_ids(results, revision=revision)
        fallback = self._attach_fallback_evidence(
            self._fallback_candidates(request),
            latest_evidence_ids,
        )
        transitions = self._transition_candidates(
            request,
            revision=revision,
            transport_evidence_id=latest_evidence_ids.get("transport_search"),
        )
        unpriced_capabilities = self._unpriced_capabilities(results, facts, revision)
        if not real:
            return CandidateBuildResult(
                candidates=(*fallback, *transitions),
                source_mode="fallback",
                fact_count=0,
                fallback_count=len(fallback),
                unpriced_capabilities=unpriced_capabilities,
            )

        period_counts = Counter(
            (candidate.location.casefold(), candidate.period) for candidate in real
        )
        target_by_destination = Counter(
            item.destination.casefold() for item in allocate_destination_days(request)
        )
        supplements_list: list[CandidateActivity] = []
        for candidate in fallback:
            key = (candidate.location.casefold(), candidate.period)
            if period_counts[key] >= target_by_destination[candidate.location.casefold()]:
                continue
            supplements_list.append(candidate)
            period_counts[key] += 1
        supplements = tuple(supplements_list)
        combined = self._deduplicate((*real, *supplements, *transitions))
        return CandidateBuildResult(
            candidates=combined,
            source_mode="real" if not supplements else "mixed",
            fact_count=len(facts),
            fallback_count=len(supplements),
            unpriced_capabilities=unpriced_capabilities,
        )

    def _fallback_candidates(
        self,
        request: TripRequest,
    ) -> tuple[CandidateActivity, ...]:
        return tuple(
            candidate
            for destination in request.destinations
            for candidate in self.fallback_catalog.candidates(
                request.model_copy(update={"destinations": (destination,)})
            )
        )

    @staticmethod
    def _attach_fallback_evidence(
        candidates: tuple[CandidateActivity, ...],
        latest_by_capability: dict[str, str],
    ) -> tuple[CandidateActivity, ...]:
        return tuple(
            candidate.model_copy(
                update={
                    "evidence_ids": (latest_by_capability[candidate.source_capability],)
                }
            )
            if candidate.source_capability in latest_by_capability
            else candidate
            for candidate in candidates
        )

    @staticmethod
    def _latest_evidence_ids(
        results: tuple[ResearchResult, ...],
        *,
        revision: int,
    ) -> dict[str, str]:
        latest: dict[str, tuple[int, str]] = {}
        for result in results:
            if not result.success or result.plan_revision > revision:
                continue
            for evidence in result.evidence:
                capability = evidence.metadata.get("capability")
                if not isinstance(capability, str):
                    continue
                current = latest.get(capability)
                if current is None or result.plan_revision > current[0]:
                    latest[capability] = (result.plan_revision, evidence.id)
        return {capability: item[1] for capability, item in latest.items()}

    @staticmethod
    def _transition_candidates(
        request: TripRequest,
        *,
        revision: int,
        transport_evidence_id: str | None,
    ) -> tuple[CandidateActivity, ...]:
        evidence_id = transport_evidence_id or f"ev-r{revision}-transport"
        return tuple(
            CandidateActivity(
                id=f"route-transfer-{index}-r{revision}",
                title=f"{transition.origin} → {transition.destination} 城际转场（班次待确认）",
                location=transition.destination,
                category="transport",
                tags=frozenset({"transport", "intercity", "route-transition"}),
                period=ActivityPeriod.MORNING,
                duration_minutes=150,
                cost=Decimal("0"),
                cost_status=CandidateCostStatus.UNKNOWN,
                required_transit_minutes=0,
                source_capability="transport_search",
                evidence_ids=(evidence_id,),
                fixed_date=transition.day,
            )
            for index, transition in enumerate(route_transitions(request), start=1)
        )

    @staticmethod
    def _unpriced_capabilities(
        results: tuple[ResearchResult, ...],
        facts: tuple[CandidateFact, ...],
        revision: int,
    ) -> tuple[str, ...]:
        cost_capabilities = {
            "accommodation_search",
            "restaurant_search",
            "transport_search",
        }
        researched = {
            str(evidence.metadata.get("capability"))
            for result in results
            if result.plan_revision == revision and result.success
            for evidence in result.evidence
            if evidence.metadata.get("capability") in cost_capabilities
        }
        priced = {
            fact.source_capability
            for fact in facts
            if fact.estimated_cost is not None
        }
        return tuple(sorted(researched - priced))

    @staticmethod
    def _to_candidate(fact: CandidateFact) -> CandidateActivity:
        period = EvidenceCandidateBuilder._period(fact)
        food = fact.category.casefold() in {"food", "restaurant", "cafe"}
        cost_known = fact.estimated_cost is not None
        return CandidateActivity(
            id=fact.id,
            title=fact.title,
            location=fact.location,
            category=fact.category,
            tags=fact.tags | frozenset({fact.category.casefold()}),
            period=period,
            duration_minutes=fact.duration_minutes or (75 if food else 120),
            cost=fact.estimated_cost or Decimal("0"),
            cost_status=(
                CandidateCostStatus.ESTIMATED
                if cost_known
                else CandidateCostStatus.UNKNOWN
            ),
            required_transit_minutes=fact.required_transit_minutes or 30,
            indoor=fact.indoor if fact.indoor is not None else food,
            source_capability=fact.source_capability,
            evidence_ids=(fact.evidence_id,),
        )

    @staticmethod
    def _period(fact: CandidateFact) -> ActivityPeriod:
        if fact.preferred_period:
            try:
                return ActivityPeriod(fact.preferred_period)
            except ValueError:
                pass
        category = fact.category.casefold()
        if category in {"food", "restaurant", "cafe"}:
            return ActivityPeriod.LUNCH
        if category in {"park", "nature", "beach", "hike", "landmark"}:
            return ActivityPeriod.MORNING
        return ActivityPeriod.AFTERNOON

    @staticmethod
    def _deduplicate(
        candidates: tuple[CandidateActivity, ...],
    ) -> tuple[CandidateActivity, ...]:
        unique: dict[tuple[str, str, ActivityPeriod], CandidateActivity] = {}
        for candidate in candidates:
            key = (
                candidate.title.casefold(),
                candidate.location.casefold(),
                candidate.period,
            )
            unique.setdefault(key, candidate)
        return tuple(unique.values())
