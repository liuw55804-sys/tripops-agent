from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tripops.context import WorkflowPhase
from tripops.domain import (
    Constraint,
    ConstraintKind,
    ConstraintPriority,
    DisruptionEvent,
    DisruptionType,
    Traveler,
    TripRequest,
)
from tripops.skills import SkillInstructionLoader, SkillRegistry, SkillSelectionPolicy


def state(*, disrupted: bool = False) -> dict[str, object]:
    request = TripRequest(
        id="skill-trip",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 3),
        budget=Decimal("1000"),
        travelers=(
            Traveler(id="alice", display_name="Alice"),
            Traveler(id="bob", display_name="Bob"),
        ),
        constraints=(
            Constraint(
                id="diet",
                kind=ConstraintKind.DIETARY,
                priority=ConstraintPriority.HARD,
                description="No peanuts",
                value={"excluded": ["peanut"]},
            ),
        ),
    )
    result: dict[str, object] = {
        "messages": [],
        "phase": WorkflowPhase.PLAN,
        "request": request,
        "evidence": [],
        "violations": [],
        "selected_skills": [],
        "required_capabilities": [],
    }
    if disrupted:
        result["disruption"] = DisruptionEvent(
            id="storm",
            event_type=DisruptionType.SEVERE_WEATHER,
            description="Storm",
            starts_at=datetime(2030, 10, 1, tzinfo=UTC),
        )
    return result


def registry() -> SkillRegistry:
    skill_registry = SkillRegistry((Path("skills"),))
    skill_registry.discover()
    return skill_registry


def test_selector_uses_summary_metadata_for_multi_traveler_constraints() -> None:
    selection = SkillSelectionPolicy(registry()).select_for_planner(
        state()  # type: ignore[arg-type]
    )

    assert set(selection.names) == {"group-negotiation", "itinerary-optimization"}
    assert "group_fairness" in selection.capabilities
    assert "constraint_repair" in selection.capabilities
    assert selection.reasons["group-negotiation"] == ("request has multiple travelers",)


def test_disruption_selects_recovery_skill() -> None:
    selection = SkillSelectionPolicy(registry()).select_for_planner(
        state(disrupted=True)  # type: ignore[arg-type]
    )

    assert "disruption-recovery" in selection.names
    assert "active disruption requires impact analysis" in selection.reasons[
        "disruption-recovery"
    ]


def test_instruction_loader_reads_only_selected_bodies() -> None:
    skill_registry = registry()
    loader = SkillInstructionLoader(skill_registry)

    instructions = loader.load(["itinerary-optimization"])

    assert "Skill: itinerary-optimization" in instructions
    assert "Hard constraints" in instructions
    assert "Group negotiation" not in instructions


def test_instruction_loader_enforces_context_budget() -> None:
    loader = SkillInstructionLoader(registry(), max_chars=20)

    with pytest.raises(ValueError, match="context budget"):
        loader.load(["itinerary-optimization"])
