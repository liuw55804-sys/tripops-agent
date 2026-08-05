from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class PlanStep(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    status: PlanStepStatus = PlanStepStatus.PENDING
    assigned_agent: str | None = None
    evidence_ids: tuple[str, ...] = ()
    attempt: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def cannot_depend_on_self(self) -> "PlanStep":
        if self.id in self.depends_on:
            raise ValueError("plan step cannot depend on itself")
        return self


class ItineraryItem(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    category: str = "activity"
    tags: tuple[str, ...] = ()
    traveler_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    required_transit_minutes: int = Field(default=0, ge=0, le=1440)
    opening_window_start: datetime | None = None
    opening_window_end: datetime | None = None
    locked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ItineraryItem":
        if self.ends_at <= self.starts_at:
            raise ValueError("itinerary item must end after it starts")
        if (self.opening_window_start is None) != (self.opening_window_end is None):
            raise ValueError("opening window requires both start and end")
        if (
            self.opening_window_start is not None
            and self.opening_window_end is not None
            and self.opening_window_end <= self.opening_window_start
        ):
            raise ValueError("opening window end must be after start")
        return self


class TravelPlan(BaseModel):
    trip_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    steps: tuple[PlanStep, ...] = ()
    itinerary: tuple[ItineraryItem, ...] = ()
    estimated_total_cost: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dag_references(self) -> "TravelPlan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step ids must be unique")
        known = set(step_ids)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(f"step depends on unknown steps: {sorted(unknown)}")
        return self
