import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from tripops import __version__
from tripops.agents import build_tripops_graph
from tripops.agents.llm import StructuredPlanner, StructuredSupervisor
from tripops.agents.router import ResearcherRouter
from tripops.agents.rule_based import (
    RuleBasedPlanner,
    RuleBasedSupervisor,
    StaticEvidenceResearcher,
)
from tripops.api.schemas import (
    ApprovalRequestBody,
    DisruptionRequest,
    RunStatus,
    RunView,
    StartRunRequest,
    StartRunResponse,
    TraceView,
)
from tripops.api.service import RunConflict, RunNotFound, TripOpsRunService
from tripops.config import get_settings
from tripops.constraints import DeterministicConstraintVerifier
from tripops.context import RunBudget, open_sqlite_checkpointer
from tripops.models import build_chat_model
from tripops.observability import InMemoryTraceSink
from tripops.skills import SkillRegistry, SkillSelectionPolicy


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    trace_sink = InMemoryTraceSink()
    skill_registry = SkillRegistry((Path("skills"),))
    skill_registry.discover()
    skill_selector = SkillSelectionPolicy(skill_registry)
    researcher = StaticEvidenceResearcher(
        "offline_researcher",
        frozenset(
            {
                "general_research",
                "transport_search",
                "weather_search",
                "policy_search",
            }
        ),
    )
    async with open_sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
        supervisor: RuleBasedSupervisor | StructuredSupervisor
        planner: RuleBasedPlanner | StructuredPlanner
        if settings.agent_mode == "llm":
            model = build_chat_model(settings)
            model_budget = RunBudget()
            supervisor = StructuredSupervisor(model, model_budget)
            planner = StructuredPlanner(model, model_budget, skill_registry=skill_registry)
        else:
            supervisor = RuleBasedSupervisor()
            planner = RuleBasedPlanner()
        graph = build_tripops_graph(
            supervisor=supervisor,
            planner=planner,
            researcher_router=ResearcherRouter((researcher,)),
            verifier=DeterministicConstraintVerifier(),
            trace_sink=trace_sink,
            checkpointer=checkpointer,
            skill_selector=skill_selector,
        )
        service = TripOpsRunService(graph, trace_sink)
        app.state.run_service = service
        yield
        await service.shutdown()


app = FastAPI(
    title="TripOps Agent",
    version=__version__,
    description="Constraint-driven travel planning and disruption recovery",
    lifespan=lifespan,
)


def _service(request: Request) -> TripOpsRunService:
    return cast(TripOpsRunService, request.app.state.run_service)


async def _record_or_404(service: TripOpsRunService, run_id: str) -> RunView:
    try:
        return service.view(await service.get(run_id))
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from exc


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post(
    "/v1/runs",
    response_model=StartRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def start_run(body: StartRunRequest, request: Request) -> StartRunResponse:
    record = await _service(request).start(body.request, user_message=body.user_message)
    return StartRunResponse(
        run_id=record.run_id,
        status=record.status,
        status_url=f"/v1/runs/{record.run_id}",
        events_url=f"/v1/runs/{record.run_id}/events",
    )


@app.get("/v1/runs/{run_id}", response_model=RunView, tags=["runs"])
async def get_run(run_id: str, request: Request) -> RunView:
    return await _record_or_404(_service(request), run_id)


@app.get("/v1/runs/{run_id}/trace", response_model=TraceView, tags=["observability"])
async def get_trace(run_id: str, request: Request) -> TraceView:
    service = _service(request)
    await _record_or_404(service, run_id)
    return TraceView(
        run_id=run_id,
        events=tuple(event.model_dump(mode="json") for event in service.events(run_id)),
    )


@app.get("/v1/runs/{run_id}/events", tags=["observability"])
async def stream_events(run_id: str, request: Request) -> EventSourceResponse:
    service = _service(request)
    await _record_or_404(service, run_id)

    async def generate() -> AsyncIterator[dict[str, str]]:
        index = 0
        while True:
            events = service.events(run_id)
            for event in events[index:]:
                yield {
                    "event": event.kind.value,
                    "id": event.event_id,
                    "data": event.model_dump_json(),
                }
            index = len(events)
            record = await service.get(run_id)
            if record.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                yield {"event": "run_status", "data": service.view(record).model_dump_json()}
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.05)

    return EventSourceResponse(generate(), ping=5)


@app.post(
    "/v1/runs/{run_id}/disruptions",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def inject_disruption(
    run_id: str,
    body: DisruptionRequest,
    request: Request,
) -> RunView:
    service = _service(request)
    try:
        return service.view(await service.inject_disruption(run_id, body.event))
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from exc
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/runs/{run_id}/approval",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def resolve_approval(
    run_id: str,
    body: ApprovalRequestBody,
    request: Request,
) -> RunView:
    service = _service(request)
    try:
        return service.view(await service.approve(run_id, body.decision))
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from exc
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run() -> None:
    uvicorn.run("tripops.api.main:app", host="127.0.0.1", port=9900, reload=False)
