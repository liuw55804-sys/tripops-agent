from pathlib import Path
from typing import Any

import yaml

from tripops.skills.models import LoadedSkill, SkillSummary


class SkillRegistry:
    """Discover Skill summaries and load full instructions only when selected."""

    def __init__(self, roots: tuple[Path, ...]) -> None:
        self._roots = roots
        self._summaries: dict[str, SkillSummary] = {}

    def discover(self) -> tuple[SkillSummary, ...]:
        discovered: dict[str, SkillSummary] = {}
        for root in self._roots:
            if not root.exists():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                summary = self._read_summary(skill_file)
                if summary.name in discovered:
                    raise ValueError(f"duplicate skill name: {summary.name}")
                discovered[summary.name] = summary
        self._summaries = discovered
        return tuple(discovered[name] for name in sorted(discovered))

    def summaries_for(
        self,
        *,
        agent_name: str,
        capabilities: frozenset[str],
    ) -> tuple[SkillSummary, ...]:
        summaries = self._ensure_discovered()
        return tuple(
            summary
            for summary in summaries
            if summary.capabilities & capabilities
            and (not summary.allowed_agents or agent_name in summary.allowed_agents)
        )

    def load(self, name: str) -> LoadedSkill:
        self._ensure_discovered()
        try:
            summary = self._summaries[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc

        raw = summary.source_path.read_text(encoding="utf-8")
        _, instructions = self._split_frontmatter(raw, summary.source_path)
        if not instructions.strip():
            raise ValueError(f"skill instructions cannot be empty: {summary.source_path}")
        return LoadedSkill(summary=summary, instructions=instructions.strip())

    def _ensure_discovered(self) -> tuple[SkillSummary, ...]:
        if not self._summaries:
            return self.discover()
        return tuple(self._summaries[name] for name in sorted(self._summaries))

    @classmethod
    def _read_summary(cls, path: Path) -> SkillSummary:
        raw = path.read_text(encoding="utf-8")
        metadata, _ = cls._split_frontmatter(raw, path)
        normalized: dict[str, Any] = {
            **metadata,
            "capabilities": frozenset(metadata.get("capabilities", [])),
            "allowed_agents": frozenset(metadata.get("allowed_agents", [])),
            "required_tools": frozenset(metadata.get("required_tools", [])),
            "source_path": path,
        }
        return SkillSummary.model_validate(normalized)

    @staticmethod
    def _split_frontmatter(raw: str, path: Path) -> tuple[dict[str, Any], str]:
        lines = raw.splitlines()
        if len(lines) < 3 or lines[0].strip() != "---":
            raise ValueError(f"skill must start with YAML frontmatter: {path}")
        try:
            closing_index = next(
                index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
            )
        except StopIteration as exc:
            raise ValueError(f"skill frontmatter is not closed: {path}") from exc

        metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
        if not isinstance(metadata, dict):
            raise ValueError(f"skill frontmatter must be a mapping: {path}")
        return metadata, "\n".join(lines[closing_index + 1 :])

