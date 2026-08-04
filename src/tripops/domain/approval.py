from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from tripops.tools.models import RiskLevel


class ApprovalRequest(BaseModel):
    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)
    risk_level: RiskLevel
    monetary_impact: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1)
    reason: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

