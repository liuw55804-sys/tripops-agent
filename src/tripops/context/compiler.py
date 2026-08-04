import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, model_validator

from tripops.context.runtime import RuntimeContext
from tripops.context.state import TripOpsState
from tripops.domain.evidence import Evidence


class ContextSectionName(StrEnum):
    RUNTIME = "runtime"
    REQUEST = "request"
    ACTIVE_PLAN = "active_plan"
    VIOLATIONS = "violations"
    EVIDENCE = "evidence"
    MEMORY = "memory"
    CONVERSATION = "conversation"


class ContextPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class ContextPolicy(BaseModel):
    max_chars: int = Field(default=24_000, ge=1_000, le=200_000)
    max_evidence: int = Field(default=12, ge=1, le=100)
    max_messages: int = Field(default=8, ge=0, le=100)
    max_memory_entries: int = Field(default=12, ge=0, le=100)
    min_evidence_confidence: float = Field(default=0.4, ge=0, le=1)
    include_stale_evidence: bool = False
    section_overhead_chars: int = Field(default=80, ge=0, le=1_000)


class ContextSection(BaseModel):
    name: ContextSectionName
    priority: ContextPriority
    content: str
    original_chars: int = Field(ge=0)
    included_chars: int = Field(ge=0)
    truncated: bool = False
    item_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_lengths(self) -> "ContextSection":
        if self.included_chars > self.original_chars:
            raise ValueError("included characters cannot exceed original characters")
        if self.included_chars != len(self.content):
            raise ValueError("included_chars must match content length")
        return self


class ContextEnvelope(BaseModel):
    text: str
    sections: tuple[ContextSection, ...]
    total_chars: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated_sections: tuple[ContextSectionName, ...]
    omitted_evidence_ids: tuple[str, ...]
    stale_evidence_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]


class ContextCompiler:
    """Compile layered state into a bounded, auditable model context."""

    def __init__(
        self,
        policy: ContextPolicy | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy = policy or ContextPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def compile(
        self,
        state: TripOpsState,
        runtime: RuntimeContext,
        *,
        memory: dict[str, Any] | None = None,
    ) -> ContextEnvelope:
        evidence, omitted, stale = self._select_evidence(state.get("evidence", []))
        candidates = self._sections(state, runtime, evidence, memory or {})
        included = self._fit(candidates)
        text = "\n\n".join(
            f"## {section.name.value}\n{section.content}"
            for section in included
            if section.content
        )
        artifacts = tuple(
            sorted(
                {
                    item.artifact_id
                    for item in evidence
                    if item.artifact_id is not None
                }
            )
        )
        return ContextEnvelope(
            text=text,
            sections=tuple(included),
            total_chars=len(text),
            estimated_tokens=max(1, (len(text) + 3) // 4) if text else 0,
            truncated_sections=tuple(
                section.name for section in included if section.truncated
            ),
            omitted_evidence_ids=tuple(sorted(omitted)),
            stale_evidence_ids=tuple(sorted(stale)),
            artifact_ids=artifacts,
        )

    def _sections(
        self,
        state: TripOpsState,
        runtime: RuntimeContext,
        evidence: tuple[Evidence, ...],
        memory: dict[str, Any],
    ) -> list[ContextSection]:
        request = state.get("request")
        plan = state.get("plan")
        messages = state.get("messages", [])[-self.policy.max_messages :]
        memory_items = dict(list(sorted(memory.items()))[: self.policy.max_memory_entries])
        raw_sections = [
            (
                ContextSectionName.RUNTIME,
                ContextPriority.HIGH,
                {
                    "run_id": runtime.run_id,
                    "user_id": runtime.user_id,
                    "locale": runtime.locale,
                    "timezone": runtime.timezone,
                    "permissions": sorted(runtime.permissions),
                },
                1,
            ),
            (
                ContextSectionName.REQUEST,
                ContextPriority.CRITICAL,
                request.model_dump(mode="json") if request else {"missing": True},
                1 if request else 0,
            ),
            (
                ContextSectionName.VIOLATIONS,
                ContextPriority.CRITICAL,
                [item.model_dump(mode="json") for item in state.get("violations", [])],
                len(state.get("violations", [])),
            ),
            (
                ContextSectionName.ACTIVE_PLAN,
                ContextPriority.HIGH,
                self._plan_summary(plan),
                len(plan.itinerary) if plan else 0,
            ),
            (
                ContextSectionName.EVIDENCE,
                ContextPriority.NORMAL,
                [self._evidence_summary(item) for item in evidence],
                len(evidence),
            ),
            (
                ContextSectionName.MEMORY,
                ContextPriority.NORMAL,
                memory_items,
                len(memory_items),
            ),
            (
                ContextSectionName.CONVERSATION,
                ContextPriority.LOW,
                [self._message_summary(message) for message in messages],
                len(messages),
            ),
        ]
        return [
            self._section(name, priority, payload, item_count)
            for name, priority, payload, item_count in raw_sections
        ]

    def _fit(self, sections: list[ContextSection]) -> list[ContextSection]:
        remaining = self.policy.max_chars
        selected: list[ContextSection] = []
        critical_order = {
            ContextSectionName.VIOLATIONS: 0,
            ContextSectionName.REQUEST: 1,
        }
        for section in sorted(
            sections,
            key=lambda item: (
                item.priority,
                critical_order.get(item.name, 10),
                item.name.value,
            ),
        ):
            overhead = self.policy.section_overhead_chars
            available = max(0, remaining - overhead)
            if available >= section.original_chars:
                included = section
            else:
                content = self._truncate(section.content, available)
                included = section.model_copy(
                    update={
                        "content": content,
                        "included_chars": len(content),
                        "truncated": len(content) < section.original_chars,
                    }
                )
            selected.append(included)
            remaining -= min(remaining, included.included_chars + overhead)
        order = {name: index for index, name in enumerate(ContextSectionName)}
        return sorted(selected, key=lambda item: order[item.name])

    def _select_evidence(
        self,
        evidence: list[Evidence],
    ) -> tuple[tuple[Evidence, ...], set[str], set[str]]:
        now = self.clock()
        stale = {item.id for item in evidence if item.is_stale(now=now)}
        eligible = [
            item
            for item in evidence
            if item.confidence >= self.policy.min_evidence_confidence
            and (self.policy.include_stale_evidence or item.id not in stale)
        ]
        eligible.sort(key=lambda item: (-item.confidence, -item.retrieved_at.timestamp(), item.id))
        selected = tuple(eligible[: self.policy.max_evidence])
        selected_ids = {item.id for item in selected}
        omitted = {item.id for item in evidence if item.id not in selected_ids}
        return selected, omitted, stale

    @staticmethod
    def _plan_summary(plan: Any) -> dict[str, Any]:
        if plan is None:
            return {"missing": True}
        return {
            "trip_id": plan.trip_id,
            "revision": plan.revision,
            "estimated_total_cost": str(plan.estimated_total_cost),
            "currency": plan.currency,
            "steps": [
                {
                    "id": step.id,
                    "capability": step.capability,
                    "status": step.status.value,
                }
                for step in plan.steps
            ],
            "itinerary": [
                {
                    "id": item.id,
                    "title": item.title,
                    "starts_at": item.starts_at.isoformat(),
                    "ends_at": item.ends_at.isoformat(),
                    "cost": str(item.cost),
                    "locked": item.locked,
                    "evidence_ids": item.evidence_ids,
                }
                for item in plan.itinerary
            ],
        }

    @staticmethod
    def _evidence_summary(item: Evidence) -> dict[str, Any]:
        return {
            "id": item.id,
            "claim": item.claim,
            "source": item.source_name,
            "source_uri": str(item.source_uri) if item.source_uri else None,
            "retrieved_at": item.retrieved_at.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "confidence": item.confidence,
            "artifact_ref": f"artifact://{item.artifact_id}" if item.artifact_id else None,
        }

    @staticmethod
    def _message_summary(message: BaseMessage) -> dict[str, str]:
        content = message.content if isinstance(message.content, str) else str(message.content)
        return {"type": message.type, "content": content}

    @staticmethod
    def _section(
        name: ContextSectionName,
        priority: ContextPriority,
        payload: Any,
        item_count: int,
    ) -> ContextSection:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return ContextSection(
            name=name,
            priority=priority,
            content=content,
            original_chars=len(content),
            included_chars=len(content),
            item_count=item_count,
        )

    @staticmethod
    def _truncate(content: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(content) <= limit:
            return content
        marker = "…[truncated]"
        if limit <= len(marker):
            return marker[:limit]
        return content[: limit - len(marker)] + marker
