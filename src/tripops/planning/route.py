from datetime import date, timedelta

from pydantic import BaseModel

from tripops.domain.trip import TripRequest


class DestinationDay(BaseModel):
    day: date
    destination: str


class RouteTransition(BaseModel):
    day: date
    origin: str
    destination: str


def allocate_destination_days(request: TripRequest) -> tuple[DestinationDay, ...]:
    """Assign contiguous trip days to ordered destinations."""
    total_days = (request.end_date - request.start_date).days + 1
    destination_count = len(request.destinations)
    return tuple(
        DestinationDay(
            day=request.start_date + timedelta(days=offset),
            destination=request.destinations[
                min(offset * destination_count // total_days, destination_count - 1)
            ],
        )
        for offset in range(total_days)
    )


def route_transitions(request: TripRequest) -> tuple[RouteTransition, ...]:
    allocation = allocate_destination_days(request)
    return tuple(
        RouteTransition(
            day=current.day,
            origin=previous.destination,
            destination=current.destination,
        )
        for previous, current in zip(allocation, allocation[1:], strict=False)
        if previous.destination != current.destination
    )
