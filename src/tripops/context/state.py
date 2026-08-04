import operator
from enum import StrEnum
from typing import Annotated, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from tripops.domain.approval import ApprovalDecision, ApprovalRequest
from tripops.domain.disruptions import DisruptionEvent
from tripops.domain.evidence import Evidence
from tripops.domain.plan import TravelPlan
from tripops.domain.trip import TripRequest
from tripops.domain.violations import Violation


class WorkflowPhase(StrEnum):
    INTAKE = "intake"
    CLARIFY = "clarify"
    PLAN = "plan"
    RESEARCH = "research"
    VERIFY = "verify"
    APPROVAL = "approval"
    REPLAN = "replan"
    FINISH = "finish"
    FAILED = "failed"


class TripOpsState(TypedDict):
    """Durable graph state. Large raw artifacts and runtime policy stay outside it."""

    messages: Annotated[list[AnyMessage], add_messages]
    phase: WorkflowPhase
    request: NotRequired[TripRequest]
    plan: NotRequired[TravelPlan]
    evidence: Annotated[list[Evidence], operator.add]
    violations: list[Violation]
    selected_skills: list[str]
    required_capabilities: list[str]
    active_agent: NotRequired[str]
    pending_approval: NotRequired[ApprovalRequest | None]
    approval_decision: NotRequired[ApprovalDecision]
    disruption: NotRequired[DisruptionEvent]
    final_response: NotRequired[str]
    verification_complete: NotRequired[bool]
    error: NotRequired[str]
