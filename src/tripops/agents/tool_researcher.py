import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tripops.agents.models import ResearchResult, ResearchTask
from tripops.context import RuntimeContext
from tripops.domain.candidates import CandidateFact
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
        candidate_facts: list[CandidateFact] = []
        errors: list[str] = []
        runtime = replace(self.runtime, run_id=task.run_id, thread_id=task.run_id)
        for candidate in candidates:
            descriptor = candidate.descriptor
            result = await self.engine.execute(
                ToolInvocation(
                    call_id=str(uuid4()),
                    tool_name=descriptor.name,
                    arguments=self._arguments(task),
                    agent_name=self.name,
                    runtime=runtime,
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
            content = (
                self.engine.artifact_store.get_text(result.artifact_id)
                if result.artifact_id
                else result.content
            )
            tool_evidence, tool_candidates = self._parse_result(
                content=content,
                task=task,
                tool_name=result.tool_name,
                retrieved_at=now,
                expires_at=expires_at,
                degraded=result.degraded,
                attempts=result.attempts,
                latency_ms=result.latency_ms,
                artifact_id=result.artifact_id,
                evidence_offset=len(evidence),
            )
            evidence.extend(tool_evidence)
            candidate_facts.extend(tool_candidates)
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=bool(evidence),
            evidence=tuple(evidence),
            candidate_facts=tuple(candidate_facts),
            error="; ".join(errors) if errors and not evidence else None,
        )

    @staticmethod
    def _arguments(task: ResearchTask) -> dict[str, Any]:
        request = task.request
        base: dict[str, Any] = {
            "capability": task.step.capability,
            "query": task.step.title,
            "requirement": request.raw_requirement,
            "origin": request.origin,
            "destinations": list(request.destinations),
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "currency": request.currency,
            "traveler_count": len(request.travelers),
        }
        if task.step.capability == "weather_search":
            return {
                **base,
                "location": request.destinations[0],
            }
        if task.step.capability == "policy_search":
            return {**base, "jurisdiction": request.destinations[0]}
        return base

    @classmethod
    def _parse_result(
        cls,
        *,
        content: str,
        task: ResearchTask,
        tool_name: str,
        retrieved_at: datetime,
        expires_at: datetime | None,
        degraded: bool,
        attempts: int,
        latency_ms: float,
        artifact_id: str | None,
        evidence_offset: int,
    ) -> tuple[list[Evidence], list[CandidateFact]]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = None

        raw_entries = payload.get("evidence") if isinstance(payload, dict) else None
        entries = raw_entries if isinstance(raw_entries, list) and raw_entries else [payload]
        evidence: list[Evidence] = []
        candidates: list[CandidateFact] = []
        for entry in entries:
            item = entry if isinstance(entry, dict) else {}
            evidence_id = cls._evidence_id(task.step.id, evidence_offset + len(evidence))
            claim = cls._entry_claim(item, content)
            evidence.append(
                Evidence(
                    id=evidence_id,
                    claim=claim,
                    source_type=(
                        EvidenceSource.MCP_TOOL
                        if tool_name.startswith("mcp.")
                        else EvidenceSource.LOCAL_TOOL
                    ),
                    source_name=str(item.get("source_name") or tool_name),
                    source_uri=item.get("source_uri"),
                    retrieved_at=retrieved_at,
                    expires_at=expires_at,
                    confidence=float(item.get("confidence", 0.8 if degraded else 0.95)),
                    artifact_id=artifact_id,
                    metadata={
                        "capability": task.step.capability,
                        "step_id": task.step.id,
                        "plan_revision": task.plan_revision,
                        "degraded": degraded,
                        "attempts": attempts,
                        "latency_ms": round(latency_ms, 3),
                    },
                )
            )
            candidate = item.get("candidate")
            if isinstance(candidate, dict):
                candidates.append(
                    CandidateFact.model_validate(
                        {
                            **candidate,
                            "source_capability": task.step.capability,
                            "evidence_id": evidence_id,
                            "source_uri": candidate.get("source_uri")
                            or item.get("source_uri"),
                        }
                    )
                )
        return evidence, candidates

    @staticmethod
    def _evidence_id(step_id: str, index: int) -> str:
        return f"ev-{step_id}" if index == 0 else f"ev-{step_id}-{index + 1}"

    @classmethod
    def _entry_claim(cls, item: dict[str, Any], content: str) -> str:
        for key in ("claim", "summary", "description", "result"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return cls._claim(content)

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
