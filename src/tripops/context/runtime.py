from dataclasses import dataclass, field
from typing import Any

from tripops.tools.models import RiskLevel


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Immutable per-run values injected through LangGraph runtime context."""

    run_id: str
    thread_id: str
    user_id: str
    tenant_id: str = "default"
    permissions: frozenset[str] = field(default_factory=frozenset)
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    model_name: str = "qwen-plus"
    max_tool_risk: RiskLevel = RiskLevel.READ_ONLY
    metadata: dict[str, Any] = field(default_factory=dict)

