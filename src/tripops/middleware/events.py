from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolEventType(StrEnum):
    STARTED = "started"
    CACHE_HIT = "cache_hit"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CIRCUIT_OPENED = "circuit_opened"
    FALLBACK_STARTED = "fallback_started"
    APPROVAL_REQUIRED = "approval_required"


class ToolEvent(BaseModel):
    event_type: ToolEventType
    run_id: str
    tool_call_id: str
    tool_name: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempt: int = Field(default=0, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class EventSink(Protocol):
    def emit(self, event: ToolEvent) -> None: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self._events: list[ToolEvent] = []
        self._lock = Lock()

    def emit(self, event: ToolEvent) -> None:
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> tuple[ToolEvent, ...]:
        with self._lock:
            return tuple(self._events)

