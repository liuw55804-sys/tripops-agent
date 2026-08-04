from pathlib import Path

import pytest

from tripops.skills import SkillRegistry


def write_skill(root: Path, name: str, *, allowed_agent: str = "planner") -> None:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: Test skill {name}
version: 1.0.0
capabilities:
  - planning
allowed_agents:
  - {allowed_agent}
required_tools: []
---

# Instructions

Return a structured plan.
""",
        encoding="utf-8",
    )


def test_discovery_and_progressive_loading(tmp_path: Path) -> None:
    write_skill(tmp_path, "planning-skill")
    registry = SkillRegistry((tmp_path,))

    summaries = registry.discover()
    loaded = registry.load("planning-skill")

    assert summaries[0].description == "Test skill planning-skill"
    assert "Return a structured plan" in loaded.instructions


def test_summary_filtering_respects_agent(tmp_path: Path) -> None:
    write_skill(tmp_path, "planner-skill", allowed_agent="planner")
    write_skill(tmp_path, "research-skill", allowed_agent="researcher")
    registry = SkillRegistry((tmp_path,))

    summaries = registry.summaries_for(
        agent_name="planner",
        capabilities=frozenset({"planning"}),
    )

    assert [summary.name for summary in summaries] == ["planner-skill"]


def test_duplicate_skill_names_are_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_skill(first, "same-skill")
    write_skill(second, "same-skill")

    with pytest.raises(ValueError, match="duplicate skill"):
        SkillRegistry((first, second)).discover()

