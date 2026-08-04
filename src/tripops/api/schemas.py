from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tripops.context.state import WorkflowPhase
from tripops.domain.approval import ApprovalDecision, ApprovalRequest
from tripops.domain.disruptions import DisruptionEvent
from tripops.domain.evidence import Evidence
from tripops.domain.plan import TravelPlan
from tripops.domain.trip import TripRequest
from tripops.domain.violations import Violation


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class StartRunRequest(BaseModel):
    request: TripRequest
    user_message: str | None = None


class StartRunResponse(BaseModel):
    run_id: str
    status: RunStatus
    status_url: str
    events_url: str


class RunView(BaseModel):
    run_id: str
    status: RunStatus
    phase: WorkflowPhase | None = None
    created_at: datetime
    updated_at: datetime
    plan: TravelPlan | None = None
    evidence: tuple[Evidence, ...] = ()
    violations: tuple[Violation, ...] = ()
    pending_approval: ApprovalRequest | None = None
    final_response: str | None = None
    error: str | None = None
    trace_event_count: int = Field(default=0, ge=0)


class DisruptionRequest(BaseModel):
    event: DisruptionEvent


class ApprovalRequestBody(BaseModel):
    decision: ApprovalDecision


class ApiError(BaseModel):
    detail: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TraceView(BaseModel):
    run_id: str
    events: tuple[dict[str, Any], ...]
