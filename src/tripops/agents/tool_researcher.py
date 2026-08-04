import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tripops.agents.models import ResearchResult, ResearchTask
from tripops.context import RuntimeContext
from tripops.domain.evidence import Evidence, EvidenceSource
from tripops.middleware import ToolExecutionEngine, ToolInvocation
from tripops.tools import RiskLevel, ToolRegistry, ToolSelectionRequest


class GovernedToolResearcher:
    """Researcher that discovers tools by capability and executes through governance."""

    def __init__(
        self,
        *,
        name: str,
        capabilities: frozenset[str],
        registry: ToolRegistry,
        engine: ToolExecutionEngine,
        runtime: RuntimeContext,
        max_tools_per_task: int = 2,
    ) -> None:
        if max_tools_per_task < 1:
            raise ValueError("max_tools_per_task must be positive")
        self.name = name
        self.capabilities = capabilities
        self.registry = registry
        self.engine = engine
        self.runtime = runtime
        self.max_tools_per_task = max_tools_per_task

    async def research(self, task: ResearchTask) -> ResearchResult:
        if task.step.capability not in self.capabilities:
            return self._failure(task, f"unsupported capability: {task.step.capability}")
        candidates = self.registry.select(
            ToolSelectionRequest(
                agent_name=self.name,
                capabilities=frozenset({task.step.capability}),
                permissions=self.runtime.permissions,
                max_risk_level=RiskLevel.READ_ONLY,
                max_tools=self.max_tools_per_task,
            )
        )
        if not candidates:
            return self._failure(task, f"no governed tool for {task.step.capability}")

        evidence: list[Evidence] = []
        errors: list[str] = []
        for index, candidate in enumerate(candidates):
            descriptor = candidate.descriptor
            result = await self.engine.execute(
                ToolInvocation(
                    call_id=str(uuid4()),
                    tool_name=descriptor.name,
                    arguments=self._arguments(task),
                    agent_name=self.name,
                    runtime=self.runtime,
                )
            )
            if not result.success:
                errors.append(result.error or f"{descriptor.name} failed")
                continue
            now = datetime.now(UTC)
            expires_at = (
                now + timedelta(seconds=descriptor.freshness_seconds)
                if descriptor.freshness_seconds
                else None
            )
            evidence_id = f"ev-{task.step.id}" if index == 0 else f"ev-{task.step.id}-{index + 1}"
            evidence.append(
                Evidence(
                    id=evidence_id,
                    claim=self._claim(result.content),
                    source_type=EvidenceSource.MCP_TOOL,
                    source_name=result.tool_name,
                    retrieved_at=now,
                    expires_at=expires_at,
                    confidence=0.8 if result.degraded else 0.95,
                    artifact_id=result.artifact_id,
                    metadata={
                        "capability": task.step.capability,
                        "step_id": task.step.id,
                        "plan_revision": task.plan_revision,
                        "degraded": result.degraded,
                        "attempts": result.attempts,
                        "latency_ms": round(result.latency_ms, 3),
                    },
                )
            )
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=bool(evidence),
            evidence=tuple(evidence),
            error="; ".join(errors) if errors and not evidence else None,
        )

    @staticmethod
    def _arguments(task: ResearchTask) -> dict[str, Any]:
        request = task.request
        base: dict[str, Any] = {
            "query": task.step.title,
            "origin": request.origin,
            "destinations": list(request.destinations),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "currency": request.currency,
            "traveler_count": len(request.travelers),
        }
        if task.step.capability == "weather_search":
            return {
                "location": request.destinations[0],
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
            }
        if task.step.capability == "policy_search":
            return {"query": task.step.title, "jurisdiction": request.destinations[0]}
        return base

    @staticmethod
    def _claim(content: str) -> str:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content.strip() or "Tool returned an empty textual result"
        if isinstance(payload, dict):
            for key in ("summary", "claim", "description", "result"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _failure(self, task: ResearchTask, error: str) -> ResearchResult:
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=False,
            error=error,
        )
