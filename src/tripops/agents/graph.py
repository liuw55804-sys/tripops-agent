import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send, interrupt
from typing_extensions import TypedDict

from tripops.agents.contracts import PlannerAgent, SupervisorAgent, VerifierAgent
from tripops.agents.models import ResearchResult, ResearchTask
from tripops.agents.router import ResearcherRouter
from tripops.constraints import ImpactAnalyzer, RepairScope
from tripops.context.state import TripOpsState, WorkflowPhase
from tripops.domain.approval import ApprovalDecision
from tripops.domain.plan import PlanStep
from tripops.domain.trip import TripRequest
from tripops.domain.violations import Violation, ViolationCode, ViolationSeverity
from tripops.observability import (
    NullTraceSink,
    TraceKind,
    TraceSink,
    TraceStatus,
    emit_trace,
    trace_span,
)
from tripops.skills import SkillSelectionPolicy


class GraphState(TripOpsState, total=False):
    run_id: str
    research_results: Annotated[list[ResearchResult], operator.add]
    supervisor_reason: str
    clarification_question: str
    repair_scope: RepairScope


class ResearchInput(TypedDict):
    run_id: str
    request: TripRequest
    research_task: PlanStep
    plan_revision: int


Finalizer = Callable[[GraphState], str]


@dataclass(frozen=True, slots=True)
class TripOpsGraph:
    graph: CompiledStateGraph[Any, Any, Any, Any]

    async def run(self, state: GraphState, *, thread_id: str = "default") -> GraphState:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = await self.graph.ainvoke(state, config=config)
        return cast(GraphState, result)

    async def resume(
        self,
        *,
        thread_id: str,
        decision: ApprovalDecision,
    ) -> GraphState:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = await self.graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")),
            config=config,
        )
        return cast(GraphState, result)

    async def continue_with(
        self,
        *,
        thread_id: str,
        update: dict[str, Any],
    ) -> GraphState:
        """Merge an external event into a checkpointed thread and re-enter the graph."""
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = await self.graph.ainvoke(update, config=config)
        return cast(GraphState, result)


def initial_state(request: TripRequest, *, user_message: str | None = None) -> GraphState:
    return GraphState(
        messages=[HumanMessage(content=user_message or request.raw_requirement or "Plan my trip")],
        phase=WorkflowPhase.INTAKE,
        request=request,
        evidence=[],
        violations=[],
        selected_skills=[],
        required_capabilities=[],
        research_results=[],
        verification_complete=False,
        run_id=str(uuid4()),
    )


def build_tripops_graph(
    *,
    supervisor: SupervisorAgent,
    planner: PlannerAgent,
    researcher_router: ResearcherRouter,
    verifier: VerifierAgent,
    finalizer: Finalizer | None = None,
    impact_analyzer: ImpactAnalyzer | None = None,
    trace_sink: TraceSink | None = None,
    checkpointer: Any | None = None,
    skill_selector: SkillSelectionPolicy | None = None,
) -> TripOpsGraph:
    render_final = finalizer or _default_finalizer
    analyzer = impact_analyzer or ImpactAnalyzer()
    traces = trace_sink or NullTraceSink()

    async def supervisor_node(state: GraphState) -> dict[str, Any]:
        trace_attributes: dict[str, Any] = {}
        with trace_span(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.AGENT,
            name="supervisor",
            attributes=trace_attributes,
        ):
            decision = await supervisor.decide(cast(TripOpsState, state))
            guarded_phase = _guard_supervisor_transition(state, decision.next_phase)
            trace_attributes.update(
                {
                    "proposed_phase": decision.next_phase.value,
                    "next_phase": guarded_phase.value,
                    "reason": decision.reason,
                    "transition_corrected": guarded_phase is not decision.next_phase,
                }
            )
            if guarded_phase is not decision.next_phase:
                emit_trace(
                    traces,
                    run_id=state["run_id"],
                    kind=TraceKind.DEGRADATION,
                    name="supervisor_transition_guard",
                    status=TraceStatus.DEGRADED,
                    attributes={
                        "proposed_phase": decision.next_phase.value,
                        "selected_phase": guarded_phase.value,
                        "reason": decision.reason,
                    },
                )
        update: dict[str, Any] = {
            "phase": guarded_phase,
            "supervisor_reason": decision.reason,
        }
        if decision.clarification_question:
            update["clarification_question"] = decision.clarification_question
        return update

    async def planner_node(state: GraphState) -> dict[str, Any]:
        selected_skills = list(state.get("selected_skills", []))
        planning_state = state
        if skill_selector is not None:
            selection = skill_selector.select_for_planner(cast(TripOpsState, state))
            selected_skills = list(selection.names)
            planning_state = cast(
                GraphState,
                {**state, "selected_skills": selected_skills},
            )
        with trace_span(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.AGENT,
            name="planner",
        ):
            plan = await planner.plan(cast(TripOpsState, planning_state))
        if not plan.steps:
            return {"phase": WorkflowPhase.FAILED, "error": "planner returned no steps"}
        for step in plan.steps:
            emit_trace(
                traces,
                run_id=state["run_id"],
                kind=TraceKind.PLAN_STEP,
                name=step.id,
                status=TraceStatus.PENDING,
                attributes={"capability": step.capability, "revision": plan.revision},
            )
        return {
            "plan": plan,
            "phase": WorkflowPhase.RESEARCH,
            "violations": [],
            "verification_complete": False,
            "selected_skills": selected_skills,
            "required_capabilities": sorted({step.capability for step in plan.steps}),
        }

    async def researcher_node(state: ResearchInput) -> dict[str, Any]:
        task = ResearchTask(
            request=state["request"],
            step=state["research_task"],
            plan_revision=state["plan_revision"],
        )
        with trace_span(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.AGENT,
            name=f"researcher:{task.step.capability}",
        ):
            try:
                researcher = researcher_router.route(task.step.capability)
                result = await researcher.research(task)
            except Exception as exc:  # noqa: BLE001 - branch failure is data
                result = ResearchResult(
                    step_id=task.step.id,
                    plan_revision=task.plan_revision,
                    agent_name="unassigned",
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
        for item in result.evidence:
            emit_trace(
                traces,
                run_id=state["run_id"],
                kind=TraceKind.CITATION,
                name=item.id,
                status=TraceStatus.SUCCEEDED,
                attributes={"step_id": task.step.id, "source": item.source_name},
            )
        return {
            "research_results": [result],
            "evidence": list(result.evidence),
        }

    async def verifier_node(state: GraphState) -> dict[str, Any]:
        with trace_span(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.AGENT,
            name="verifier",
        ):
            violations = await verifier.verify(cast(TripOpsState, state))
        for violation in violations:
            emit_trace(
                traces,
                run_id=state["run_id"],
                kind=TraceKind.VIOLATION,
                name=violation.code.value,
                status=(
                    TraceStatus.FAILED
                    if violation.severity is ViolationSeverity.ERROR
                    else TraceStatus.PENDING
                ),
                attributes={"message": violation.message},
            )
        has_errors = any(
            violation.severity is ViolationSeverity.ERROR for violation in violations
        )
        return {
            "violations": list(violations),
            "verification_complete": not has_errors,
            "phase": WorkflowPhase.VERIFY,
        }

    async def impact_node(state: GraphState) -> dict[str, Any]:
        plan = state.get("plan")
        if plan is None:
            return {"phase": WorkflowPhase.FAILED, "error": "impact analysis requires plan"}
        with trace_span(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.GRAPH_NODE,
            name="impact_analyzer",
        ):
            scope = analyzer.analyze(
                plan,
                tuple(state.get("violations", [])),
                state.get("disruption"),
            )
        return {"repair_scope": scope, "phase": WorkflowPhase.REPLAN}

    async def finalizer_node(state: GraphState) -> dict[str, Any]:
        return {
            "final_response": render_final(state),
            "phase": WorkflowPhase.FINISH,
        }

    async def approval_node(state: GraphState) -> dict[str, Any]:
        request = state.get("pending_approval")
        if request is None:
            return {"phase": WorkflowPhase.FINISH}
        emit_trace(
            traces,
            run_id=state["run_id"],
            kind=TraceKind.APPROVAL,
            name=request.id,
            status=TraceStatus.PENDING,
            attributes={"action": request.action, "tool_name": request.tool_name},
        )
        raw_decision = interrupt(
            {
                "type": "approval_required",
                "request": request.model_dump(mode="json"),
            }
        )
        decision = ApprovalDecision.model_validate(raw_decision)
        if decision.approved:
            return {
                "approval_decision": decision,
                "pending_approval": None,
                "phase": WorkflowPhase.FINISH,
            }
        rejection = Violation(
            code=ViolationCode.POLICY_VIOLATION,
            severity=ViolationSeverity.ERROR,
            message=decision.reason or "user rejected the proposed action",
            repair_hint="generate a non-consequential alternative",
            repair_capabilities=("itinerary_planning",),
        )
        return {
            "approval_decision": decision,
            "pending_approval": None,
            "violations": [rejection],
            "verification_complete": False,
            "phase": WorkflowPhase.REPLAN,
        }

    def route_supervisor(state: GraphState) -> str:
        phase = state["phase"]
        if phase is WorkflowPhase.PLAN:
            return "planner"
        if phase is WorkflowPhase.REPLAN:
            return "impact"
        if phase is WorkflowPhase.VERIFY:
            return "verifier"
        if phase is WorkflowPhase.FINISH:
            return "finalizer"
        if phase is WorkflowPhase.APPROVAL:
            return "approval"
        return "terminal"

    def fan_out_research(state: GraphState) -> list[Send] | str:
        if state.get("error") or "plan" not in state:
            return "terminal"
        plan = state["plan"]
        steps = plan.steps
        repair_scope = state.get("repair_scope")
        if (
            plan.revision > 1
            and repair_scope
            and repair_scope.local_repair
            and repair_scope.required_capabilities
        ):
            known_evidence_ids = {item.id for item in state.get("evidence", [])}
            referenced_evidence_ids = {
                evidence_id for item in plan.itinerary for evidence_id in item.evidence_ids
            }
            missing_evidence_ids = referenced_evidence_ids - known_evidence_ids
            local_steps = tuple(
                step
                for step in steps
                if step.capability in set(repair_scope.required_capabilities)
                or f"ev-{step.id}" in missing_evidence_ids
            )
            if local_steps:
                steps = local_steps
        return [
            Send(
                "researcher",
                ResearchInput(
                    run_id=state["run_id"],
                    request=state["request"],
                    research_task=step,
                    plan_revision=plan.revision,
                ),
            )
            for step in steps
        ]

    builder = StateGraph(GraphState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("planner", planner_node)
    builder.add_node("researcher", researcher_node, input_schema=ResearchInput)
    builder.add_node("verifier", verifier_node)
    builder.add_node("impact", impact_node)
    builder.add_node("finalizer", finalizer_node)
    builder.add_node("approval", approval_node)
    builder.add_node("terminal", lambda _: {})

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "planner": "planner",
            "impact": "impact",
            "verifier": "verifier",
            "finalizer": "finalizer",
            "approval": "approval",
            "terminal": "terminal",
        },
    )
    builder.add_conditional_edges("planner", fan_out_research)
    builder.add_edge("impact", "planner")
    builder.add_edge("researcher", "supervisor")
    builder.add_edge("verifier", "supervisor")
    builder.add_edge("finalizer", END)
    builder.add_edge("approval", "supervisor")
    builder.add_edge("terminal", END)

    return TripOpsGraph(builder.compile(checkpointer=checkpointer))


def _guard_supervisor_transition(
    state: GraphState,
    proposed: WorkflowPhase,
) -> WorkflowPhase:
    """Keep LLM routing inside the deterministic workflow state machine."""
    if state.get("error"):
        expected = WorkflowPhase.FAILED
    elif "request" not in state:
        expected = WorkflowPhase.CLARIFY
    elif state.get("plan") is None:
        expected = WorkflowPhase.PLAN
    elif state.get("disruption") is not None and not state.get("repair_scope"):
        expected = WorkflowPhase.REPLAN
    elif state.get("violations") and not state.get("verification_complete", False):
        expected = WorkflowPhase.REPLAN
    elif not state.get("verification_complete", False):
        expected = WorkflowPhase.VERIFY
    elif state.get("pending_approval"):
        expected = WorkflowPhase.APPROVAL
    else:
        expected = WorkflowPhase.FINISH
    if proposed is WorkflowPhase.FAILED:
        return proposed
    return proposed if proposed is expected else expected


def _default_finalizer(state: GraphState) -> str:
    plan = state.get("plan")
    evidence = state.get("evidence", [])
    if plan is None:
        return "Trip planning did not produce a plan."
    successful = sum(
        1
        for result in state.get("research_results", [])
        if result.plan_revision == plan.revision and result.success
    )
    return (
        f"# TripOps plan\n\n"
        f"- Trip: `{plan.trip_id}`\n"
        f"- Revision: `{plan.revision}`\n"
        f"- Research steps completed: `{successful}/{len(plan.steps)}`\n"
        f"- Evidence records: `{len(evidence)}`\n"
        f"- Verification: `passed`\n"
    )
