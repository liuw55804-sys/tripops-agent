from dataclasses import dataclass

from tripops.context.state import TripOpsState
from tripops.domain.constraints import ConstraintKind
from tripops.skills.registry import SkillRegistry


@dataclass(frozen=True, slots=True)
class SkillSelection:
    names: tuple[str, ...]
    capabilities: frozenset[str]
    reasons: dict[str, tuple[str, ...]]


class SkillSelectionPolicy:
    """Select Skill summaries deterministically before full instruction loading."""

    def __init__(self, registry: SkillRegistry, *, max_skills: int = 4) -> None:
        if max_skills < 1:
            raise ValueError("max_skills must be positive")
        self.registry = registry
        self.max_skills = max_skills

    def select_for_planner(self, state: TripOpsState) -> SkillSelection:
        capabilities, reasons_by_capability = self._planner_capabilities(state)
        summaries = self.registry.summaries_for(
            agent_name="planner",
            capabilities=capabilities,
        )
        if state.get("disruption") is None:
            summaries = tuple(
                summary for summary in summaries if summary.name != "disruption-recovery"
            )
        ranked = sorted(
            summaries,
            key=lambda summary: (
                -len(summary.capabilities & capabilities),
                summary.name,
            ),
        )[: self.max_skills]
        reasons = {
            summary.name: tuple(
                sorted(
                    {
                        reason
                        for capability in summary.capabilities & capabilities
                        for reason in reasons_by_capability.get(capability, ())
                    }
                )
            )
            for summary in ranked
        }
        return SkillSelection(
            names=tuple(summary.name for summary in ranked),
            capabilities=capabilities,
            reasons=reasons,
        )

    @staticmethod
    def _planner_capabilities(
        state: TripOpsState,
    ) -> tuple[frozenset[str], dict[str, list[str]]]:
        capabilities = {"itinerary_planning"}
        reasons: dict[str, list[str]] = {
            "itinerary_planning": ["every plan requires feasible time and budget scheduling"]
        }
        request = state.get("request")
        if request is not None and len(request.travelers) > 1:
            capabilities.add("group_fairness")
            reasons.setdefault("group_fairness", []).append("request has multiple travelers")
        if request is not None:
            for constraint in request.constraints:
                if constraint.kind in {
                    ConstraintKind.ACCESSIBILITY,
                    ConstraintKind.DIETARY,
                    ConstraintKind.REQUIRED_ACTIVITY,
                    ConstraintKind.EXCLUDED_ACTIVITY,
                    ConstraintKind.TIME_WINDOW,
                }:
                    capabilities.add("constraint_repair")
                    reasons.setdefault("constraint_repair", []).append(
                        f"constraint {constraint.id} must remain machine-checkable"
                    )
        if state.get("disruption") is not None:
            capabilities.update({"disruption_analysis", "constraint_repair"})
            reasons.setdefault("disruption_analysis", []).append(
                "active disruption requires impact analysis"
            )
            reasons.setdefault("constraint_repair", []).append(
                "repair must preserve unaffected itinerary items"
            )
        if state.get("violations"):
            capabilities.add("constraint_repair")
            reasons.setdefault("constraint_repair", []).append(
                "verifier returned repairable violations"
            )
        return frozenset(capabilities), reasons


class SkillInstructionLoader:
    """Load only selected Skill bodies and enforce a prompt-size budget."""

    def __init__(self, registry: SkillRegistry, *, max_chars: int = 12_000) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.registry = registry
        self.max_chars = max_chars

    def load(self, names: tuple[str, ...] | list[str]) -> str:
        sections = []
        total = 0
        for name in dict.fromkeys(names):
            skill = self.registry.load(name)
            section = f"## Skill: {skill.summary.name}\n\n{skill.instructions}"
            total += len(section)
            if total > self.max_chars:
                raise ValueError("selected Skill instructions exceed context budget")
            sections.append(section)
        return "\n\n".join(sections)
