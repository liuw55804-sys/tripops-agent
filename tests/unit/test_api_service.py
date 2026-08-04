from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from tripops.agents.graph import GraphState, TripOpsGraph
from tripops.api.schemas import RunStatus
from tripops.api.service import TripOpsRunService
from tripops.domain import Traveler, TripRequest
from tripops.observability import InMemoryTraceSink


class EarlyExitGraph:
    async def run(self, state: GraphState, *, thread_id: str = "default") -> GraphState:
        del thread_id
        return state


@pytest.mark.asyncio
async def test_unfinished_graph_exit_is_reported_as_failure() -> None:
    service = TripOpsRunService(
        cast(TripOpsGraph, EarlyExitGraph()),
        InMemoryTraceSink(),
    )
    request = TripRequest(
        id="early-exit",
        origin="Shanghai",
        destinations=("Sydney",),
        start_date=date(2030, 1, 1),
        end_date=date(2030, 1, 5),
        budget=Decimal("12000"),
        travelers=(Traveler(id="u1", display_name="Alice"),),
    )

    record = await service.start(request)
    await service.shutdown()

    assert record.status is RunStatus.FAILED
    assert record.error == "workflow terminated without a finished plan (phase=intake)"
