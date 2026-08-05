from collections import Counter
from decimal import Decimal
from math import ceil
from typing import Protocol

from pydantic import BaseModel

from tripops.agents.models import ResearchResult
from tripops.domain.candidates import CandidateFact
from tripops.domain.trip import TripRequest
from tripops.planning.catalog import (
    ActivityPeriod,
    CandidateActivity,
    DemoDestinationCatalog,
)


class CandidateBuildResult(BaseModel):
    candidates: tuple[CandidateActivity, ...]
    source_mode: str
    fact_count: int
    fallback_count: int


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
        fallback = self._attach_fallback_evidence(
            self._fallback_candidates(request),
            results,
            revision=revision,
        )
        if not real:
            return CandidateBuildResult(
                candidates=fallback,
                source_mode="fallback",
                fact_count=0,
                fallback_count=len(fallback),
            )

        period_counts = Counter(
            (candidate.location.casefold(), candidate.period) for candidate in real
        )
        days = (request.end_date - request.start_date).days + 1
        target_per_period = max(1, ceil(days / len(request.destinations)))
        supplements_list: list[CandidateActivity] = []
        for candidate in fallback:
            key = (candidate.location.casefold(), candidate.period)
            if period_counts[key] >= target_per_period:
                continue
            supplements_list.append(candidate)
            period_counts[key] += 1
        supplements = tuple(supplements_list)
        combined = self._deduplicate((*real, *supplements))
        return CandidateBuildResult(
            candidates=combined,
            source_mode="real" if not supplements else "mixed",
            fact_count=len(facts),
            fallback_count=len(supplements),
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
        results: tuple[ResearchResult, ...],
        *,
        revision: int,
    ) -> tuple[CandidateActivity, ...]:
        latest_by_capability: dict[str, tuple[int, str]] = {}
        for result in results:
            if not result.success or result.plan_revision > revision:
                continue
            for evidence in result.evidence:
                capability = evidence.metadata.get("capability")
                if not isinstance(capability, str):
                    continue
                current = latest_by_capability.get(capability)
                if current is None or result.plan_revision > current[0]:
                    latest_by_capability[capability] = (
                        result.plan_revision,
                        evidence.id,
                    )
        return tuple(
            candidate.model_copy(
                update={
                    "evidence_ids": (
                        latest_by_capability[candidate.source_capability][1],
                    )
                }
            )
            if candidate.source_capability in latest_by_capability
            else candidate
            for candidate in candidates
        )

    @staticmethod
    def _to_candidate(fact: CandidateFact) -> CandidateActivity:
        period = EvidenceCandidateBuilder._period(fact)
        food = fact.category.casefold() in {"food", "restaurant", "cafe"}
        return CandidateActivity(
            id=fact.id,
            title=fact.title,
            location=fact.location,
            category=fact.category,
            tags=fact.tags | frozenset({fact.category.casefold()}),
            period=period,
            duration_minutes=fact.duration_minutes or (75 if food else 120),
            cost=fact.estimated_cost or Decimal("0"),
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
