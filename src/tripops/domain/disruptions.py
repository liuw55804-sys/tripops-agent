from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DisruptionType(StrEnum):
    TRANSPORT_CANCELLED = "transport_cancelled"
    SEVERE_WEATHER = "severe_weather"
    VENUE_CLOSED = "venue_closed"
    USER_CONSTRAINT_CHANGED = "user_constraint_changed"
    PRICE_CHANGED = "price_changed"


class DisruptionEvent(BaseModel):
    id: str = Field(min_length=1)
    event_type: DisruptionType
    description: str = Field(min_length=1)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    locations: tuple[str, ...] = ()
    affected_item_ids: tuple[str, ...] = ()
    affected_step_ids: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_window(self) -> "DisruptionEvent":
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("disruption end must be after start")
        return self
