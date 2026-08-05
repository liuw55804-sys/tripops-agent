import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from tripops.domain.constraints import Constraint


class Traveler(BaseModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    preferences: tuple[str, ...] = ()
    accessibility_needs: tuple[str, ...] = ()
    dietary_restrictions: tuple[str, ...] = ()


class TripRequest(BaseModel):
    id: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    destinations: tuple[str, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    budget: Decimal = Field(gt=0)
    travelers: tuple[Traveler, ...] = Field(min_length=1)
    constraints: tuple[Constraint, ...] = ()
    raw_requirement: str = ""

    @field_validator("destinations", mode="before")
    @classmethod
    def split_destination_route(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        destinations = []
        for item in value:
            if not isinstance(item, str):
                destinations.append(item)
                continue
            destinations.extend(
                part.strip()
                for part in re.split(r"\s*(?:、|，|;|→|->|\n)\s*", item)
                if part.strip()
            )
        return tuple(dict.fromkeys(destinations))

    @model_validator(mode="after")
    def validate_dates_and_travelers(self) -> "TripRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be earlier than start_date")
        trip_days = (self.end_date - self.start_date).days + 1
        if len(self.destinations) > trip_days:
            raise ValueError("trip must have at least one day per destination")
        traveler_ids = [traveler.id for traveler in self.travelers]
        if len(traveler_ids) != len(set(traveler_ids)):
            raise ValueError("traveler ids must be unique")
        known = set(traveler_ids)
        for constraint in self.constraints:
            unknown = set(constraint.traveler_ids) - known
            if unknown:
                raise ValueError(f"constraint references unknown travelers: {sorted(unknown)}")
        return self
