from decimal import Decimal
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, model_validator

from tripops.agents.models import SupervisorDecision
from tripops.agents.prompts import planner_messages, supervisor_messages
from tripops.context import RunBudget
from tripops.context.state import TripOpsState
from tripops.domain.plan import PlanStep, TravelPlan
from tripops.planning import ConstraintAwareScheduler
from tripops.skills import SkillInstructionLoader, SkillRegistry

ALLOWED_CAPABILITIES = frozenset(
    {
        "transport_search",
        "weather_search",
        "policy_search",
        "poi_search",
        "restaurant_search",
        "accommodation_search",
        "accessibility_search",
    }
)


class ResearchTaskDraft(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=120)
    capability: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=300)


class PlannerDraft(BaseModel):
    tasks: tuple[ResearchTaskDraft, ...] = Field(min_length=1, max_length=12)
    planning_focus: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_task_graph(self) -> "PlannerDraft":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("planner task ids must be unique")
        known = set(ids)
        for task in self.tasks:
            if task.capability not in ALLOWED_CAPABILITIES:
                raise ValueError(f"unsupported capability: {task.capability}")
            unknown = set(task.depends_on) - known
            if unknown:
                raise ValueError(f"task depends on unknown ids: {sorted(unknown)}")
            if task.id in task.depends_on:
                raise ValueError("task cannot depend on itself")
        return self


class StructuredSupervisor:
    """LLM routing adapter whose output remains constrained by SupervisorDecision."""

    def __init__(self, model: BaseChatModel, budget: RunBudget) -> None:
        self.model = model
        self.budget = budget
        self.runnable = cast(
            Runnable[Any, SupervisorDecision],
            model.with_structured_output(SupervisorDecision),
        )

    async def decide(self, state: TripOpsState) -> SupervisorDecision:
        self.budget.consume_model_call(cost_units=1)
        result = await self.runnable.ainvoke(supervisor_messages(state))
        return SupervisorDecision.model_validate(result)


class StructuredPlanner:
    """LLM proposes the research DAG; deterministic scheduler owns hard constraints."""

    def __init__(
        self,
        model: BaseChatModel,
        budget: RunBudget,
        scheduler: ConstraintAwareScheduler | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.model = model
        self.budget = budget
        del scheduler
        self.skill_loader = SkillInstructionLoader(skill_registry) if skill_registry else None
        self.runnable = cast(
            Runnable[Any, PlannerDraft],
            model.with_structured_output(PlannerDraft),
        )

    async def plan(self, state: TripOpsState) -> TravelPlan:
        self.budget.consume_model_call(cost_units=2)
        selected_skills = tuple(state.get("selected_skills", []))
        skill_instructions = (
            self.skill_loader.load(selected_skills) if self.skill_loader else ""
        )
        raw = await self.runnable.ainvoke(
            planner_messages(state, skill_instructions=skill_instructions)
        )
        draft = PlannerDraft.model_validate(raw)
        tasks = self._ensure_required_tasks(draft.tasks)
        request = state["request"]
        previous = state.get("plan")
        revision = 1 if previous is None else previous.revision + 1
        id_by_draft_id = {task.id: self._step_suffix(task.capability) for task in tasks}
        steps = tuple(
            PlanStep(
                id=f"r{revision}-{self._step_suffix(task.capability)}",
                title=task.title,
                capability=task.capability,
                depends_on=tuple(
                    f"r{revision}-{id_by_draft_id[dependency]}"
                    for dependency in task.depends_on
                    if dependency in id_by_draft_id
                ),
                assigned_agent=f"{task.capability}_researcher",
            )
            for task in tasks
        )
        return TravelPlan(
            trip_id=request.id,
            revision=revision,
            steps=steps,
            itinerary=previous.itinerary if previous else (),
            estimated_total_cost=(
                previous.estimated_total_cost if previous else Decimal("0")
            ),
            currency=request.currency,
            metadata={
                "planner_focus": draft.planning_focus,
                "planner_assumptions": draft.assumptions,
            },
        )

    @staticmethod
    def _ensure_required_tasks(
        tasks: tuple[ResearchTaskDraft, ...],
    ) -> tuple[ResearchTaskDraft, ...]:
        by_capability = {task.capability: task for task in tasks}
        mandatory = {
            "transport_search": "Research feasible transport",
            "weather_search": "Research weather risk",
            "policy_search": "Research cancellation policy",
            "poi_search": "Research activities and opening windows",
            "restaurant_search": "Research dietary-safe restaurants",
        }
        for capability, title in mandatory.items():
            by_capability.setdefault(
                capability,
                ResearchTaskDraft(
                    id=StructuredPlanner._step_suffix(capability),
                    title=title,
                    capability=capability,
                    rationale="required evidence for deterministic itinerary verification",
                ),
            )
        return tuple(by_capability.values())

    @staticmethod
    def _step_suffix(capability: str) -> str:
        return {
            "transport_search": "transport",
            "weather_search": "weather",
            "policy_search": "policy",
            "poi_search": "poi",
            "restaurant_search": "restaurant",
            "accommodation_search": "stay",
            "accessibility_search": "accessibility",
        }[capability]
