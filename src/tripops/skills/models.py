from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class SkillSummary(BaseModel):
    """Small metadata object safe to include in the Supervisor context."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1)
    capabilities: frozenset[str] = Field(min_length=1)
    allowed_agents: frozenset[str] = Field(default_factory=frozenset)
    required_tools: frozenset[str] = Field(default_factory=frozenset)
    version: str = Field(default="1.0.0", min_length=1)
    source_path: Path

    @field_validator("capabilities", "allowed_agents", "required_tools")
    @classmethod
    def reject_blank_items(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not item.strip() for item in value):
            raise ValueError("skill metadata cannot contain blank values")
        return value


class LoadedSkill(BaseModel):
    summary: SkillSummary
    instructions: str = Field(min_length=1)

