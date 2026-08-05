import asyncio
from datetime import date
from decimal import Decimal

import pytest

from tripops.agents import (
    ResearchResult,
    ResearchTask,
    SupervisorDecision,
    build_tripops_graph,
    initial_state,
)
from tripops.agents.graph import GraphState
from tripops.agents.router import ResearcherRouter
from tripops.agents.rule_based import RuleBasedSupervisor
from tripops.context import WorkflowPhase
from tripops.domain import (
    CandidateFact,
    Evidence,
    EvidenceSource,
    PlanStep,
    Traveler,
    TravelPlan,
    TripRequest,
    Violation,
    ViolationCode,
    ViolationSeverity,
)
from tripops.observability import InMemoryTraceSink, TraceKind, TraceStatus


def request() -> TripRequest:
    return TripRequest(
        id="trip-1",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 5),
        budget=Decimal("12000"),
        travelers=(Traveler(id="u1", display_name="Alice"),),
    )


class TwoStepPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, state: GraphState) -> TravelPlan:
        self.calls += 1
        return TravelPlan(
            trip_id=state["request"].id,
            revision=self.calls,
            steps=(
                PlanStep(id=f"r{self.calls}-weather", title="Weather", capability="weather"),
                PlanStep(id=f"r{self.calls}-rail", title="Rail", capability="rail"),
            ),
        )


class ConcurrentResearcher:
    capabilities = frozenset({"weather", "rail"})
    name = "researcher"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def research(self, task: ResearchTask) -> ResearchResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        evidence = Evidence(
            id=f"ev-{task.step.id}",
            claim=task.step.title,
            source_type=EvidenceSource.DERIVED,
            source_name=self.name,
        )
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=True,
            evidence=(evidence,),
        )


class CitedCandidateResearcher(ConcurrentResearcher):
    async def research(self, task: ResearchTask) -> ResearchResult:
        result = await super().research(task)
        if task.step.capability != "weather":
            return result
        evidence_id = result.evidence[0].id
        facts = tuple(
            CandidateFact(
                id=f"cited-{period}",
                title=f"Cited {period}",
                location="Kyoto",
                category="restaurant" if period == "lunch" else "landmark",
                tags=frozenset({"food" if period == "lunch" else "culture"}),
                source_capability="weather",
                evidence_id=evidence_id,
                preferred_period=period,
            )
            for period in ("morning", "lunch", "afternoon")
        )
        return result.model_copy(update={"candidate_facts": facts})


class PassVerifier:
    async def verify(self, _: GraphState) -> tuple[Violation, ...]:
        return ()


class InvalidIntakeSupervisor:
    async def decide(self, _: GraphState) -> SupervisorDecision:
        return SupervisorDecision(
            next_phase=WorkflowPhase.INTAKE,
            reason="incorrectly asks for fields already present",
        )


@pytest.mark.asyncio
async def test_graph_runs_researchers_in_parallel_and_finishes() -> None:
    planner = TwoStepPlanner()
    researcher = ConcurrentResearcher()
    trip_graph = build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=planner,
        researcher_router=ResearcherRouter((researcher,)),
        verifier=PassVerifier(),
    )

    result = await trip_graph.run(initial_state(request()))

    assert result["phase"] is WorkflowPhase.FINISH
    assert len(result["evidence"]) == 2
    assert researcher.max_active == 2
    assert "Verification: `passed`" in result["final_response"]


@pytest.mark.asyncio
async def test_graph_corrects_invalid_llm_supervisor_transition() -> None:
    traces = InMemoryTraceSink()
    trip_graph = build_tripops_graph(
        supervisor=InvalidIntakeSupervisor(),
        planner=TwoStepPlanner(),
        researcher_router=ResearcherRouter((ConcurrentResearcher(),)),
        verifier=PassVerifier(),
        trace_sink=traces,
    )

    result = await trip_graph.run(initial_state(request()))
    guard_events = [
        event for event in traces.snapshot() if event.name == "supervisor_transition_guard"
    ]

    assert result["phase"] is WorkflowPhase.FINISH
    assert result["plan"].revision == 1
    assert guard_events
    assert guard_events[0].attributes["proposed_phase"] == "intake"
    assert guard_events[0].attributes["selected_phase"] == "plan"


@pytest.mark.asyncio
async def test_graph_builds_and_schedules_cited_candidates_after_research() -> None:
    traces = InMemoryTraceSink()
    trip_graph = build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=TwoStepPlanner(),
        researcher_router=ResearcherRouter((CitedCandidateResearcher(),)),
        verifier=PassVerifier(),
        trace_sink=traces,
    )

    result = await trip_graph.run(initial_state(request()))

    assert result["plan"].metadata["candidate_source_mode"] == "mixed"
    assert len(result["plan"].itinerary) == 15
    cited_items = [
        item for item in result["plan"].itinerary if item.title.startswith("Cited")
    ]
    assert cited_items
    assert all(
        item.evidence_ids == ("ev-r1-weather",) for item in cited_items
    )
    summary = next(
        event for event in traces.snapshot() if event.name == "candidate_build_summary"
    )
    assert summary.status is TraceStatus.DEGRADED
    assert summary.attributes["fact_count"] == 3


@pytest.mark.asyncio
async def test_graph_replans_after_violation() -> None:
    planner = TwoStepPlanner()
    researcher = ConcurrentResearcher()

    class FailOnceVerifier:
        def __init__(self) -> None:
            self.calls = 0

        async def verify(self, _: GraphState) -> tuple[Violation, ...]:
            self.calls += 1
            if self.calls == 1:
                return (
                    Violation(
                        code=ViolationCode.BUDGET_EXCEEDED,
                        severity=ViolationSeverity.ERROR,
                        message="over budget",
                        repair_hint="choose cheaper rail",
                    ),
                )
            return ()

    verifier = FailOnceVerifier()
    trip_graph = build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=planner,
        researcher_router=ResearcherRouter((researcher,)),
        verifier=verifier,
    )

    result = await trip_graph.run(initial_state(request()))

    assert result["plan"].revision == 2
    assert planner.calls == 2
    assert verifier.calls == 2
    assert len(result["research_results"]) == 4


@pytest.mark.asyncio
async def test_graph_researches_only_impacted_capability_on_replan() -> None:
    planner = TwoStepPlanner()
    researcher = ConcurrentResearcher()

    class WeatherFailOnceVerifier:
        def __init__(self) -> None:
            self.calls = 0

        async def verify(self, _: GraphState) -> tuple[Violation, ...]:
            self.calls += 1
            if self.calls == 1:
                return (
                    Violation(
                        code=ViolationCode.STALE_EVIDENCE,
                        severity=ViolationSeverity.ERROR,
                        message="weather evidence expired",
                        repair_capabilities=("weather",),
                    ),
                )
            return ()

    trip_graph = build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=planner,
        researcher_router=ResearcherRouter((researcher,)),
        verifier=WeatherFailOnceVerifier(),
    )

    result = await trip_graph.run(initial_state(request()))
    current_revision_results = [
        research_result
        for research_result in result["research_results"]
        if research_result.plan_revision == 2
    ]

    assert len(result["research_results"]) == 3
    assert [research_result.step_id for research_result in current_revision_results] == [
        "r2-weather"
    ]
    assert result["repair_scope"].required_capabilities == ("weather",)


@pytest.mark.asyncio
async def test_graph_emits_agent_plan_and_citation_trace_events() -> None:
    planner = TwoStepPlanner()
    researcher = ConcurrentResearcher()
    traces = InMemoryTraceSink()
    trip_graph = build_tripops_graph(
        supervisor=RuleBasedSupervisor(),
        planner=planner,
        researcher_router=ResearcherRouter((researcher,)),
        verifier=PassVerifier(),
        trace_sink=traces,
    )

    result = await trip_graph.run(initial_state(request()))
    events = traces.snapshot()

    assert {event.run_id for event in events} == {result["run_id"]}
    assert {event.kind for event in events}.issuperset(
        {TraceKind.AGENT, TraceKind.PLAN_STEP, TraceKind.CITATION}
    )
    assert any(
        event.name == "verifier" and event.status is TraceStatus.SUCCEEDED
        for event in events
    )
