import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from tripops.constraints import RepairScope
from tripops.context.state import TripOpsState

SUPERVISOR_SYSTEM_PROMPT = """You are TripOps Supervisor, the control-plane agent.
Choose exactly one next workflow phase from the structured schema. Never invent facts,
search destinations, or write an itinerary. Prefer deterministic verification after
research. Replan only when a disruption or ERROR violation invalidates the current plan.
Ask one concise clarification only when required request fields are absent. Consequential
actions must route to approval. Explain the routing decision in one sentence."""

PLANNER_SYSTEM_PROMPT = """You are TripOps Planner. Produce a small research task DAG,
not a prose itinerary. Each task must have a stable capability and explicit dependencies.
Facts are collected later by Researcher agents; do not fabricate prices, schedules,
opening hours, or policies. Hard constraints are authoritative. During repair, limit tasks
to the supplied affected scope and preserve unaffected items. The deterministic scheduler
will turn verified candidate intents into time slots after your response."""


def supervisor_messages(state: TripOpsState) -> list[BaseMessage]:
    return [
        SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(state_synopsis(state), ensure_ascii=False, default=str)),
    ]


def planner_messages(
    state: TripOpsState,
    *,
    skill_instructions: str = "",
) -> list[BaseMessage]:
    request = state["request"]
    payload = {
        "trip_request": request.model_dump(mode="json"),
        "current_plan": (
            state["plan"].model_dump(mode="json") if state.get("plan") is not None else None
        ),
        "repair_scope": _repair_scope(state),
        "violations": [item.model_dump(mode="json") for item in state.get("violations", [])],
        "available_capabilities": [
            "transport_search",
            "weather_search",
            "policy_search",
            "poi_search",
            "restaurant_search",
            "accommodation_search",
            "accessibility_search",
        ],
    }
    system_prompt = PLANNER_SYSTEM_PROMPT
    if skill_instructions:
        system_prompt += f"\n\n# Selected domain skills\n\n{skill_instructions}"
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]


def state_synopsis(state: TripOpsState) -> dict[str, Any]:
    request = state.get("request")
    plan = state.get("plan")
    evidence = state.get("evidence", [])
    return {
        "phase": state["phase"].value,
        "has_request": request is not None,
        "request_id": request.id if request else None,
        "destinations": list(request.destinations) if request else [],
        "traveler_count": len(request.travelers) if request else 0,
        "constraint_count": len(request.constraints) if request else 0,
        "plan_revision": plan.revision if plan else None,
        "plan_step_count": len(plan.steps) if plan else 0,
        "itinerary_item_count": len(plan.itinerary) if plan else 0,
        "evidence_count": len(evidence),
        "evidence_sources": sorted({item.source_name for item in evidence}),
        "violations": [
            {"code": item.code.value, "severity": item.severity.value}
            for item in state.get("violations", [])
        ],
        "verification_complete": state.get("verification_complete", False),
        "has_pending_approval": state.get("pending_approval") is not None,
        "has_disruption": state.get("disruption") is not None,
        "repair_scope": _repair_scope(state),
        "error": state.get("error"),
    }


def _repair_scope(state: TripOpsState) -> dict[str, Any] | None:
    raw = dict(state).get("repair_scope")
    if isinstance(raw, RepairScope):
        return raw.model_dump(mode="json")
    return None
