import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from tripops.middleware.events import EventSink, ToolEvent, ToolEventType


class TraceKind(StrEnum):
    GRAPH_NODE = "graph_node"
    AGENT = "agent"
    PLAN_STEP = "plan_step"
    TOOL_CALL = "tool_call"
    CITATION = "citation"
    VIOLATION = "violation"
    APPROVAL = "approval"
    DEGRADATION = "degradation"


class TraceStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PENDING = "pending"
    RETRYING = "retrying"
    DEGRADED = "degraded"


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = Field(min_length=1)
    span_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_span_id: str | None = None
    kind: TraceKind
    name: str = Field(min_length=1)
    status: TraceStatus
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class NullTraceSink:
    def emit(self, event: TraceEvent) -> None:
        del event


class InMemoryTraceSink:
    def __init__(self) -> None:
        self._events: list[TraceEvent] = []
        self._lock = Lock()

    def emit(self, event: TraceEvent) -> None:
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(self._events)


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def emit(self, event: TraceEvent) -> None:
        serialized = event.model_dump_json() + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)

    def read(self) -> tuple[TraceEvent, ...]:
        if not self.path.exists():
            return ()
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return tuple(TraceEvent.model_validate_json(line) for line in lines if line)


def emit_trace(
    sink: TraceSink,
    *,
    run_id: str,
    kind: TraceKind,
    name: str,
    status: TraceStatus,
    attributes: dict[str, Any] | None = None,
    parent_span_id: str | None = None,
) -> TraceEvent:
    event = TraceEvent(
        run_id=run_id,
        kind=kind,
        name=name,
        status=status,
        attributes=attributes or {},
        parent_span_id=parent_span_id,
    )
    sink.emit(event)
    return event


@contextmanager
def trace_span(
    sink: TraceSink,
    *,
    run_id: str,
    kind: TraceKind,
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[str]:
    span_id = str(uuid4())
    started = time.monotonic()
    sink.emit(
        TraceEvent(
            run_id=run_id,
            span_id=span_id,
            kind=kind,
            name=name,
            status=TraceStatus.STARTED,
            attributes=attributes or {},
        )
    )
    try:
        yield span_id
    except Exception as exc:
        sink.emit(
            TraceEvent(
                run_id=run_id,
                span_id=span_id,
                kind=kind,
                name=name,
                status=TraceStatus.FAILED,
                duration_ms=(time.monotonic() - started) * 1000,
                attributes={"error": f"{type(exc).__name__}: {exc}"},
            )
        )
        raise
    else:
        sink.emit(
            TraceEvent(
                run_id=run_id,
                span_id=span_id,
                kind=kind,
                name=name,
                status=TraceStatus.SUCCEEDED,
                duration_ms=(time.monotonic() - started) * 1000,
                attributes=attributes or {},
            )
        )


class ToolTraceAdapter(EventSink):
    """Map reliability-engine events into the unified trace schema."""

    def __init__(self, sink: TraceSink) -> None:
        self.sink = sink

    def emit(self, event: ToolEvent) -> None:
        status = {
            ToolEventType.STARTED: TraceStatus.STARTED,
            ToolEventType.CACHE_HIT: TraceStatus.SUCCEEDED,
            ToolEventType.RETRYING: TraceStatus.RETRYING,
            ToolEventType.SUCCEEDED: TraceStatus.SUCCEEDED,
            ToolEventType.FAILED: TraceStatus.FAILED,
            ToolEventType.CIRCUIT_OPENED: TraceStatus.DEGRADED,
            ToolEventType.FALLBACK_STARTED: TraceStatus.DEGRADED,
            ToolEventType.APPROVAL_REQUIRED: TraceStatus.PENDING,
        }[event.event_type]
        kind = (
            TraceKind.DEGRADATION
            if status in {TraceStatus.RETRYING, TraceStatus.DEGRADED}
            else TraceKind.TOOL_CALL
        )
        self.sink.emit(
            TraceEvent(
                run_id=event.run_id,
                span_id=event.tool_call_id,
                kind=kind,
                name=event.tool_name,
                status=status,
                duration_ms=event.latency_ms,
                occurred_at=event.occurred_at,
                attributes={
                    "attempt": event.attempt,
                    "tool_event_type": event.event_type.value,
                    **event.details,
                },
            )
        )

