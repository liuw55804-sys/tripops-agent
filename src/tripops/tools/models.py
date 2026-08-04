from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field, field_validator


class RiskLevel(IntEnum):
    READ_ONLY = 0
    LOW_IMPACT = 1
    HIGH_IMPACT = 2
    FINANCIAL = 3


class CircuitState(StrEnum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class ToolDescriptor(BaseModel):
    """Governance metadata kept separately from a LangChain tool implementation."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]*$")
    description: str = Field(min_length=1)
    capabilities: frozenset[str] = Field(min_length=1)
    allowed_agents: frozenset[str] = Field(default_factory=frozenset)
    required_permissions: frozenset[str] = Field(default_factory=frozenset)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    requires_approval: bool = False
    timeout_seconds: float = Field(default=15, gt=0, le=300)
    estimated_latency_ms: int = Field(default=500, ge=0)
    estimated_cost_units: float = Field(default=0, ge=0)
    freshness_seconds: int | None = Field(default=None, gt=0)
    fallback_tools: tuple[str, ...] = ()
    enabled: bool = True

    @field_validator("capabilities", "allowed_agents", "required_permissions")
    @classmethod
    def reject_blank_set_items(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not item.strip() for item in value):
            raise ValueError("metadata sets cannot contain blank values")
        return value


class ToolSelectionRequest(BaseModel):
    agent_name: str = Field(min_length=1)
    capabilities: frozenset[str] = Field(min_length=1)
    permissions: frozenset[str] = Field(default_factory=frozenset)
    max_risk_level: RiskLevel = RiskLevel.READ_ONLY
    allow_approval_tools: bool = False
    max_tools: int = Field(default=6, ge=1, le=20)


class ToolCandidate(BaseModel):
    descriptor: ToolDescriptor
    matched_capabilities: frozenset[str]
    score: float
    circuit_state: CircuitState

