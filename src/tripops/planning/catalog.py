from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from tripops.domain.trip import TripRequest


class ActivityPeriod(StrEnum):
    MORNING = "morning"
    LUNCH = "lunch"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class CandidateCostStatus(StrEnum):
    QUOTED = "quoted"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CandidateActivity(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = Field(min_length=1)
    category: str = Field(min_length=1)
    tags: frozenset[str] = Field(min_length=1)
    period: ActivityPeriod
    duration_minutes: int = Field(ge=30, le=720)
    cost: Decimal = Field(ge=0)
    cost_status: CandidateCostStatus = CandidateCostStatus.ESTIMATED
    required_transit_minutes: int = Field(default=30, ge=0, le=240)
    indoor: bool = False
    dietary_risks: frozenset[str] = frozenset()
    accessibility_features: frozenset[str] = frozenset(
        {"wheelchair", "step_free", "elevator"}
    )
    source_capability: str = "poi_search"
    evidence_ids: tuple[str, ...] = ()
    fixed_date: date | None = None


class DemoDestinationCatalog:
    """Offline catalog that makes the demo deterministic and API-key free.

    This is deliberately a candidate source, not a truth source. Production wiring
    replaces it with cited RAG/MCP results while retaining the scheduler contract.
    """

    def candidates(self, request: TripRequest) -> tuple[CandidateActivity, ...]:
        destination = request.destinations[0]
        slug = _slug(destination)
        common = (
            CandidateActivity(
                id=f"{slug}-heritage",
                title=f"{destination} Heritage Walk",
                location=destination,
                category="culture",
                tags=frozenset({"culture", "history", "temple", "architecture"}),
                period=ActivityPeriod.MORNING,
                duration_minutes=120,
                cost=Decimal("80"),
                indoor=False,
            ),
            CandidateActivity(
                id=f"{slug}-park",
                title=f"{destination} Riverside Park",
                location=destination,
                category="park",
                tags=frozenset({"park", "nature", "family", "walking"}),
                period=ActivityPeriod.MORNING,
                duration_minutes=120,
                cost=Decimal("20"),
                indoor=False,
            ),
            CandidateActivity(
                id=f"{slug}-museum",
                title=f"{destination} City Museum",
                location=destination,
                category="museum",
                tags=frozenset({"museum", "culture", "history", "family"}),
                period=ActivityPeriod.AFTERNOON,
                duration_minutes=120,
                cost=Decimal("120"),
                indoor=True,
            ),
            CandidateActivity(
                id=f"{slug}-market",
                title=f"{destination} Local Market",
                location=destination,
                category="shopping",
                tags=frozenset({"shopping", "food", "local"}),
                period=ActivityPeriod.AFTERNOON,
                duration_minutes=90,
                cost=Decimal("60"),
                indoor=True,
            ),
            CandidateActivity(
                id=f"{slug}-science",
                title=f"{destination} Science Center",
                location=destination,
                category="family",
                tags=frozenset({"family", "science", "museum", "indoor"}),
                period=ActivityPeriod.AFTERNOON,
                duration_minutes=120,
                cost=Decimal("100"),
                indoor=True,
            ),
            CandidateActivity(
                id=f"{slug}-lunch-local",
                title=f"{destination} Local Lunch",
                location=destination,
                category="food",
                tags=frozenset({"food", "local", "restaurant"}),
                period=ActivityPeriod.LUNCH,
                duration_minutes=75,
                cost=Decimal("90"),
                dietary_risks=frozenset(),
                source_capability="restaurant_search",
            ),
            CandidateActivity(
                id=f"{slug}-lunch-vegetarian",
                title=f"{destination} Vegetarian Lunch",
                location=destination,
                category="food",
                tags=frozenset({"food", "vegetarian", "restaurant"}),
                period=ActivityPeriod.LUNCH,
                duration_minutes=75,
                cost=Decimal("110"),
                dietary_risks=frozenset(),
                source_capability="restaurant_search",
            ),
        )
        return common + self._required_candidates(request, destination, slug, common)

    @staticmethod
    def _required_candidates(
        request: TripRequest,
        destination: str,
        slug: str,
        existing: tuple[CandidateActivity, ...],
    ) -> tuple[CandidateActivity, ...]:
        existing_tags = {tag for item in existing for tag in item.tags}
        required = {
            _constraint_tag(constraint.value)
            for constraint in request.constraints
            if constraint.kind.value == "required_activity"
        }
        return tuple(
            CandidateActivity(
                id=f"{slug}-required-{_slug(tag)}",
                title=f"{destination} {tag.title()} Experience",
                location=destination,
                category=tag,
                tags=frozenset({tag}),
                period=ActivityPeriod.AFTERNOON,
                duration_minutes=120,
                cost=Decimal("80"),
                indoor=False,
            )
            for tag in sorted(required - existing_tags)
            if tag
        )


def _constraint_tag(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("tag") or value.get("activity") or "").strip().casefold()
    return str(value).strip().casefold()


def _slug(value: str) -> str:
    normalized = "-".join(value.casefold().split())
    return "".join(character for character in normalized if character.isalnum() or character == "-")
