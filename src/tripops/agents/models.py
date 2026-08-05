from pydantic import BaseModel, Field

from tripops.context.state import WorkflowPhase
from tripops.domain.candidates import CandidateFact
from tripops.domain.evidence import Evidence
from tripops.domain.plan import PlanStep
from tripops.domain.trip import TripRequest


class SupervisorDecision(BaseModel):
    next_phase: WorkflowPhase
    reason: str = Field(min_length=1)
    clarification_question: str | None = None


class ResearchTask(BaseModel):
    request: TripRequest
    step: PlanStep
    plan_revision: int = Field(ge=1)
    run_id: str = Field(default="default", min_length=1)


class ResearchResult(BaseModel):
    step_id: str
    plan_revision: int = Field(ge=1)
    agent_name: str = Field(min_length=1)
    success: bool
    evidence: tuple[Evidence, ...] = ()
    candidate_facts: tuple[CandidateFact, ...] = ()
    error: str | None = None
