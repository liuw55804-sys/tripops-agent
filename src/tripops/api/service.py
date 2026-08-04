import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from tripops.agents.graph import GraphState, TripOpsGraph, initial_state
from tripops.api.schemas import RunStatus, RunView
from tripops.context.state import WorkflowPhase
from tripops.domain.approval import ApprovalDecision
from tripops.domain.disruptions import DisruptionEvent
from tripops.domain.trip import TripRequest
from tripops.observability import InMemoryTraceSink, TraceEvent


@dataclass(slots=True)
class RunRecord:
    run_id: str
    status: RunStatus = RunStatus.QUEUED
    state: GraphState | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class RunNotFound(KeyError):
    pass


class RunConflict(RuntimeError):
    pass


class TripOpsRunService:
    """Application service around checkpointed graphs and their observable run lifecycle."""

    def __init__(self, graph: TripOpsGraph, trace_sink: InMemoryTraceSink) -> None:
        self.graph = graph
        self.trace_sink = trace_sink
        self._records: dict[str, RunRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def start(self, request: TripRequest, *, user_message: str | None = None) -> RunRecord:
        state = initial_state(request, user_message=user_message)
        record = RunRecord(run_id=state["run_id"], state=state)
        async with self._lock:
            self._records[record.run_id] = record
        self._spawn(self._execute(record, state))
        return record

    async def get(self, run_id: str) -> RunRecord:
        async with self._lock:
            record = self._records.get(run_id)
        if record is None:
            raise RunNotFound(run_id)
        return record

    async def inject_disruption(self, run_id: str, event: DisruptionEvent) -> RunRecord:
        record = await self.get(run_id)
        self._ensure_idle(record)
        record.status = RunStatus.RUNNING
        record.updated_at = datetime.now(UTC)
        self._spawn(
            self._continue(
                record,
                {
                    "disruption": event,
                    "verification_complete": False,
                    "violations": [],
                },
            )
        )
        return record

    async def approve(self, run_id: str, decision: ApprovalDecision) -> RunRecord:
        record = await self.get(run_id)
        if record.status is not RunStatus.WAITING_APPROVAL:
            raise RunConflict("run is not waiting for approval")
        record.status = RunStatus.RUNNING
        record.updated_at = datetime.now(UTC)
        self._spawn(self._resume(record, decision))
        return record

    def events(self, run_id: str) -> tuple[TraceEvent, ...]:
        return tuple(event for event in self.trace_sink.snapshot() if event.run_id == run_id)

    def view(self, record: RunRecord) -> RunView:
        state = record.state or cast(GraphState, {})
        return RunView(
            run_id=record.run_id,
            status=record.status,
            phase=state.get("phase"),
            created_at=record.created_at,
            updated_at=record.updated_at,
            plan=state.get("plan"),
            evidence=tuple(state.get("evidence", [])),
            violations=tuple(state.get("violations", [])),
            pending_approval=state.get("pending_approval"),
            final_response=state.get("final_response"),
            error=record.error or state.get("error"),
            trace_event_count=len(self.events(record.run_id)),
        )

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, record: RunRecord, state: GraphState) -> None:
        record.status = RunStatus.RUNNING
        record.updated_at = datetime.now(UTC)
        try:
            result = await self.graph.run(state, thread_id=record.run_id)
            self._complete(record, result)
        except Exception as exc:  # noqa: BLE001 - service boundary records failures
            self._fail(record, exc)

    async def _continue(self, record: RunRecord, update: dict[str, Any]) -> None:
        try:
            result = await self.graph.continue_with(thread_id=record.run_id, update=update)
            self._complete(record, result)
        except Exception as exc:  # noqa: BLE001
            self._fail(record, exc)

    async def _resume(self, record: RunRecord, decision: ApprovalDecision) -> None:
        try:
            result = await self.graph.resume(thread_id=record.run_id, decision=decision)
            self._complete(record, result)
        except Exception as exc:  # noqa: BLE001
            self._fail(record, exc)

    def _complete(self, record: RunRecord, result: GraphState) -> None:
        record.state = result
        record.updated_at = datetime.now(UTC)
        if result.get("__interrupt__") or result.get("pending_approval"):
            record.status = RunStatus.WAITING_APPROVAL
        elif result.get("phase") is WorkflowPhase.FAILED or result.get("error"):
            record.status = RunStatus.FAILED
        else:
            record.status = RunStatus.COMPLETED

    @staticmethod
    def _fail(record: RunRecord, error: Exception) -> None:
        record.status = RunStatus.FAILED
        record.error = f"{type(error).__name__}: {error}"
        record.updated_at = datetime.now(UTC)

    @staticmethod
    def _ensure_idle(record: RunRecord) -> None:
        if record.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
            raise RunConflict("run is still active")
        if record.status is RunStatus.WAITING_APPROVAL:
            raise RunConflict("resolve pending approval before injecting a disruption")

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
