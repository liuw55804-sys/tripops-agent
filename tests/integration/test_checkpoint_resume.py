from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from tripops.agents import build_tripops_graph, initial_state
from tripops.agents.router import ResearcherRouter
from tripops.agents.rule_based import (
    NoopVerifier,
    RuleBasedPlanner,
    RuleBasedSupervisor,
    StaticEvidenceResearcher,
)
from tripops.context import WorkflowPhase, open_sqlite_checkpointer
from tripops.domain import ApprovalDecision, ApprovalRequest, Traveler, TravelPlan, TripRequest
from tripops.tools import RiskLevel


def request() -> TripRequest:
    return TripRequest(
        id="trip-approval",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3),
        budget=Decimal("5000"),
        travelers=(Traveler(id="u1", display_name="Alice"),),
    )


def build_graph(checkpointer: Any):
    general = StaticEvidenceResearcher(
        "general_researcher",
        frozenset({"general_research"}),
    )
    return build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=RuleBasedPlanner(),
        researcher_router=ResearcherRouter((general,)),
        verifier=NoopVerifier(),
        checkpointer=checkpointer,
    )


@pytest.mark.asyncio
async def test_approval_interrupt_survives_checkpointer_reopen(tmp_path: Path) -> None:
    database = tmp_path / "checkpoints.sqlite"
    state = initial_state(request())
    state["plan"] = TravelPlan(trip_id="trip-approval", revision=1)
    state["verification_complete"] = True
    state["pending_approval"] = ApprovalRequest(
        id="approve-1",
        action="rebook rail",
        tool_name="mcp.transport.propose_rebooking",
        summary="Change to rail option rail-2 for CNY 240",
        arguments={"booking_id": "booking-1", "option_id": "rail-2"},
        risk_level=RiskLevel.FINANCIAL,
        monetary_impact=Decimal("240"),
        currency="CNY",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )

    async with open_sqlite_checkpointer(database) as first_saver:
        interrupted = await build_graph(first_saver).run(state, thread_id="approval-thread")
        raw_interrupted = cast(dict[str, Any], interrupted)
        assert raw_interrupted["__interrupt__"]

    assert database.exists()

    async with open_sqlite_checkpointer(database) as reopened_saver:
        resumed = await build_graph(reopened_saver).resume(
            thread_id="approval-thread",
            decision=ApprovalDecision(approved=True, decided_by="user-1"),
        )

    assert resumed["phase"] is WorkflowPhase.FINISH
    assert resumed["approval_decision"].approved
    assert "TripOps plan" in resumed["final_response"]

