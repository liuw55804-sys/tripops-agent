from datetime import UTC, datetime
from decimal import Decimal

from tripops.agents.models import ResearchResult, ResearchTask, SupervisorDecision
from tripops.context.state import TripOpsState, WorkflowPhase
from tripops.domain.evidence import Evidence, EvidenceSource
from tripops.domain.plan import PlanStep, TravelPlan
from tripops.domain.violations import Violation


class RuleBasedSupervisor:
    """Deterministic control policy used for offline demos and graph tests."""

    def __init__(self, *, max_revisions: int = 3) -> None:
        self.max_revisions = max_revisions

    async def decide(self, state: TripOpsState) -> SupervisorDecision:
        if state.get("error"):
            return SupervisorDecision(next_phase=WorkflowPhase.FAILED, reason="state has error")
        if "request" not in state:
            return SupervisorDecision(
                next_phase=WorkflowPhase.CLARIFY,
                reason="trip request is incomplete",
                clarification_question="请补充出发地、目的地、日期、预算和出行成员。",
            )
        plan = state.get("plan")
        if plan is None:
            return SupervisorDecision(
                next_phase=WorkflowPhase.PLAN,
                reason="no plan exists",
            )
        if state.get("disruption") is not None and not state.get("repair_scope"):
            return SupervisorDecision(
                next_phase=WorkflowPhase.REPLAN,
                reason="a new disruption requires impact analysis",
            )
        if state.get("violations") and not state.get("verification_complete", False):
            if plan.revision >= self.max_revisions:
                return SupervisorDecision(
                    next_phase=WorkflowPhase.FAILED,
                    reason="maximum plan revisions reached",
                )
            return SupervisorDecision(
                next_phase=WorkflowPhase.REPLAN,
                reason="verifier reported repairable violations",
            )
        if not state.get("verification_complete", False):
            return SupervisorDecision(
                next_phase=WorkflowPhase.VERIFY,
                reason="research completed and plan needs verification",
            )
        if state.get("pending_approval"):
            return SupervisorDecision(
                next_phase=WorkflowPhase.APPROVAL,
                reason="a consequential action requires approval",
            )
        return SupervisorDecision(
            next_phase=WorkflowPhase.FINISH,
            reason="plan is verified",
        )


class RuleBasedPlanner:
    """Small offline planner; production wiring replaces it with a structured-output Agent."""

    async def plan(self, state: TripOpsState) -> TravelPlan:
        request = state["request"]
        previous = state.get("plan")
        revision = 1 if previous is None else previous.revision + 1
        steps = (
            PlanStep(
                id=f"r{revision}-transport",
                title="Research feasible transport options",
                capability="transport_search",
                assigned_agent="transport_researcher",
            ),
            PlanStep(
                id=f"r{revision}-weather",
                title="Research weather risks for travel dates",
                capability="weather_search",
                assigned_agent="weather_researcher",
            ),
            PlanStep(
                id=f"r{revision}-policy",
                title="Research cancellation and disruption policies",
                capability="policy_search",
                assigned_agent="policy_researcher",
            ),
        )
        return TravelPlan(
            trip_id=request.id,
            revision=revision,
            steps=steps,
            estimated_total_cost=previous.estimated_total_cost if previous else Decimal("0"),
            currency=request.currency,
        )


class StaticEvidenceResearcher:
    """Deterministic researcher useful when no model or external provider is configured."""

    def __init__(self, name: str, capabilities: frozenset[str]) -> None:
        self.name = name
        self.capabilities = capabilities

    async def research(self, task: ResearchTask) -> ResearchResult:
        now = datetime.now(UTC)
        evidence = Evidence(
            id=f"ev-{task.step.id}",
            claim=f"Demo evidence for {task.step.title}",
            source_type=EvidenceSource.DERIVED,
            source_name=self.name,
            retrieved_at=now,
            confidence=0.5,
            metadata={"is_mock": True, "capability": task.step.capability},
        )
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=True,
            evidence=(evidence,),
        )


class NoopVerifier:
    async def verify(self, state: TripOpsState) -> tuple[Violation, ...]:
        return ()
