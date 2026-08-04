from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ConstraintKind(StrEnum):
    BUDGET = "budget"
    DATE_RANGE = "date_range"
    TIME_WINDOW = "time_window"
    ACCESSIBILITY = "accessibility"
    DIETARY = "dietary"
    REQUIRED_ACTIVITY = "required_activity"
    EXCLUDED_ACTIVITY = "excluded_activity"
    TRANSPORT = "transport"
    ACCOMMODATION = "accommodation"
    PREFERENCE = "preference"


class ConstraintPriority(StrEnum):
    HARD = "hard"
    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class Constraint(BaseModel):
    """A normalized, traceable requirement used by planning and verification."""

    id: str = Field(min_length=1)
    kind: ConstraintKind
    priority: ConstraintPriority
    description: str = Field(min_length=1)
    value: Any
    traveler_ids: tuple[str, ...] = ()
    source_turn_id: str | None = None
    supersedes: str | None = None

    @model_validator(mode="after")
    def hard_constraints_must_be_machine_readable(self) -> "Constraint":
        if self.priority is ConstraintPriority.HARD and isinstance(self.value, str):
            if not self.value.strip():
                raise ValueError("hard constraint value cannot be blank")
        return self

