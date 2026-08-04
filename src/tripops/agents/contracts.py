from typing import Protocol

from tripops.agents.models import ResearchResult, ResearchTask, SupervisorDecision
from tripops.context.state import TripOpsState
from tripops.domain.plan import TravelPlan
from tripops.domain.violations import Violation


class SupervisorAgent(Protocol):
    async def decide(self, state: TripOpsState) -> SupervisorDecision: ...


class PlannerAgent(Protocol):
    async def plan(self, state: TripOpsState) -> TravelPlan: ...


class ResearcherAgent(Protocol):
    name: str
    capabilities: frozenset[str]

    async def research(self, task: ResearchTask) -> ResearchResult: ...


class VerifierAgent(Protocol):
    async def verify(self, state: TripOpsState) -> tuple[Violation, ...]: ...

