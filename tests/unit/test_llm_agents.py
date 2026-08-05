from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel

from tripops.agents.llm import (
    PlannerDraft,
    ResearchTaskDraft,
    StructuredPlanner,
    StructuredSupervisor,
)
from tripops.agents.models import SupervisorDecision
from tripops.agents.prompts import planner_messages, state_synopsis
from tripops.config import Settings
from tripops.context import RunBudget, WorkflowPhase
from tripops.domain import Traveler, TripRequest
from tripops.models import build_chat_model


class FakeRunnable:
    def __init__(self, value: object) -> None:
        self.value = value
        self.messages: object = None

    async def ainvoke(self, messages: object) -> object:
        self.messages = messages
        return self.value


class FakeStructuredModel:
    def __init__(self, values: dict[type[object], object]) -> None:
        self.values = values
        self.runnables: dict[type[object], FakeRunnable] = {}

    def with_structured_output(self, schema: type[object]) -> FakeRunnable:
        runnable = FakeRunnable(self.values[schema])
        self.runnables[schema] = runnable
        return runnable


def request() -> TripRequest:
    return TripRequest(
        id="llm-trip",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 2),
        budget=Decimal("1200"),
        travelers=(Traveler(id="alice", display_name="Alice", preferences=("culture",)),),
    )


def state() -> dict[str, Any]:
    return {
        "messages": [],
        "phase": WorkflowPhase.INTAKE,
        "request": request(),
        "evidence": [],
        "violations": [],
        "selected_skills": [],
        "required_capabilities": [],
    }


@pytest.mark.asyncio
async def test_structured_supervisor_uses_schema_and_budget() -> None:
    decision = SupervisorDecision(next_phase=WorkflowPhase.PLAN, reason="request is complete")
    fake = FakeStructuredModel({SupervisorDecision: decision})
    budget = RunBudget()
    supervisor = StructuredSupervisor(cast(BaseChatModel, fake), budget)

    result = await supervisor.decide(state())  # type: ignore[arg-type]

    assert result == decision
    assert budget.snapshot()["model_calls"] == 1
    assert fake.runnables[SupervisorDecision].messages is not None


@pytest.mark.asyncio
async def test_structured_planner_augments_required_research_without_scheduling() -> None:
    draft = PlannerDraft(
        tasks=(
            ResearchTaskDraft(
                id="activities",
                title="Research cultural activities",
                capability="poi_search",
                rationale="match culture preference",
            ),
            ResearchTaskDraft(
                id="activities-backup",
                title="Research alternative cultural activities",
                capability="poi_search",
                rationale="provide a weather-safe alternative",
            ),
        ),
        planning_focus=("culture",),
        assumptions=("offline candidate catalog",),
    )
    fake = FakeStructuredModel({PlannerDraft: draft})
    budget = RunBudget()
    planner = StructuredPlanner(cast(BaseChatModel, fake), budget)

    plan = await planner.plan(state())  # type: ignore[arg-type]

    assert {step.capability for step in plan.steps} == {
        "transport_search",
        "weather_search",
        "policy_search",
        "poi_search",
        "restaurant_search",
    }
    assert len(plan.steps) == 5
    assert plan.itinerary == ()
    assert plan.metadata["planner_focus"] == ("culture",)
    assert plan.metadata["planner_assumptions"] == ("offline candidate catalog",)
    assert budget.snapshot()["model_calls"] == 1
    assert budget.snapshot()["cost_units"] == 2


def test_prompt_synopsis_excludes_raw_conversation_and_artifacts() -> None:
    current = state()
    current["request"] = request().model_copy(
        update={"raw_requirement": "Prefer a four-star hotel near public transport."}
    )
    synopsis = state_synopsis(current)  # type: ignore[arg-type]
    messages = planner_messages(current)  # type: ignore[arg-type]

    assert synopsis["request_id"] == "llm-trip"
    assert synopsis["origin"] == "Shanghai"
    assert synopsis["start_date"] == "2030-10-01"
    assert synopsis["end_date"] == "2030-10-02"
    assert synopsis["budget"] == "1200"
    assert synopsis["travelers"][0]["preferences"] == ["culture"]
    assert synopsis["raw_requirement"] == (
        "Prefer a four-star hotel near public transport."
    )
    assert "messages" not in synopsis
    assert "artifact" not in str(synopsis).lower()
    assert len(messages) == 2


def test_model_factory_requires_key_in_llm_mode() -> None:
    settings = Settings(_env_file=None, agent_mode="llm", model_api_key="")

    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        build_chat_model(settings)
