import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from tripops.context import FileArtifactStore, RunBudget, RuntimeContext
from tripops.middleware.cache import TTLCache
from tripops.middleware.circuit_breaker import CircuitBreaker, CircuitOpenError
from tripops.middleware.events import EventSink, ToolEvent, ToolEventType
from tripops.tools.models import RiskLevel, ToolDescriptor
from tripops.tools.registry import ToolRegistry

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
Sleeper = Callable[[float], Awaitable[None]]


class ToolExecutionError(RuntimeError):
    pass


class ToolPolicyDenied(ToolExecutionError):
    pass


class ToolApprovalRequired(ToolExecutionError):
    pass


class ToolInvocation(BaseModel):
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_name: str = Field(min_length=1)
    runtime: RuntimeContext
    approval_granted: bool = False

    model_config = {"arbitrary_types_allowed": True}


class ToolResult(BaseModel):
    call_id: str
    tool_name: str
    content: str
    success: bool
    attempts: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    degraded: bool = False
    cache_hit: bool = False
    artifact_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    max_attempts: int = 3
    base_retry_delay_seconds: float = 0.05
    cache_ttl_seconds: float = 300
    artifact_threshold_chars: int = 8_000
    preview_chars: int = 1_000

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_retry_delay_seconds < 0 or self.cache_ttl_seconds <= 0:
            raise ValueError("retry delay and cache ttl are invalid")
        if self.artifact_threshold_chars < 1 or self.preview_chars < 1:
            raise ValueError("artifact thresholds must be positive")


class ToolExecutionEngine:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        budget: RunBudget,
        circuit_breaker: CircuitBreaker,
        artifact_store: FileArtifactStore,
        event_sink: EventSink,
        policy: ToolExecutionPolicy | None = None,
        sleeper: Sleeper = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.budget = budget
        self.circuit_breaker = circuit_breaker
        self.artifact_store = artifact_store
        self.event_sink = event_sink
        self.policy = policy or ToolExecutionPolicy()
        self.sleeper = sleeper
        self.clock = clock
        self.cache: TTLCache[ToolResult] = TTLCache(clock=clock)
        self._handlers: dict[str, ToolHandler] = {}

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        self.registry.get(tool_name)
        if tool_name in self._handlers:
            raise ValueError(f"handler already registered: {tool_name}")
        self._handlers[tool_name] = handler

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        descriptor = self.registry.get(invocation.tool_name)
        self._authorize(descriptor, invocation)
        cache_key = self._cache_key(invocation)

        if descriptor.risk_level is RiskLevel.READ_ONLY:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._emit(ToolEventType.CACHE_HIT, invocation, descriptor.name)
                return cached.model_copy(update={"cache_hit": True})

        chain = (descriptor, *self.registry.fallback_chain(descriptor.name))
        errors: list[str] = []
        total_attempts = 0
        started_at = self.clock()

        for index, candidate in enumerate(chain):
            self._authorize(candidate, invocation)
            if index > 0:
                self._emit(
                    ToolEventType.FALLBACK_STARTED,
                    invocation,
                    candidate.name,
                    details={"primary_tool": descriptor.name},
                )
            try:
                result, attempts = await self._execute_candidate(candidate, invocation)
                total_attempts += attempts
                result = result.model_copy(
                    update={
                        "attempts": total_attempts,
                        "latency_ms": (self.clock() - started_at) * 1000,
                        "degraded": index > 0,
                    }
                )
                if descriptor.risk_level is RiskLevel.READ_ONLY:
                    self.cache.put(cache_key, result, ttl_seconds=self.policy.cache_ttl_seconds)
                return result
            except (CircuitOpenError, ToolExecutionError) as exc:
                errors.append(f"{candidate.name}: {exc}")

        latency_ms = (self.clock() - started_at) * 1000
        error = "; ".join(errors) or "no usable tool handler"
        self._emit(
            ToolEventType.FAILED,
            invocation,
            descriptor.name,
            latency_ms=latency_ms,
            details={"error": error},
        )
        return ToolResult(
            call_id=invocation.call_id,
            tool_name=descriptor.name,
            content="",
            success=False,
            attempts=total_attempts,
            latency_ms=latency_ms,
            error=error,
        )

    async def _execute_candidate(
        self,
        descriptor: ToolDescriptor,
        invocation: ToolInvocation,
    ) -> tuple[ToolResult, int]:
        self.circuit_breaker.before_call(descriptor.name)
        handler = self._handlers.get(descriptor.name)
        if handler is None:
            raise ToolExecutionError(f"no handler registered for {descriptor.name}")

        for attempt in range(1, self.policy.max_attempts + 1):
            started_at = self.clock()
            self.budget.consume_tool_call(cost_units=descriptor.estimated_cost_units)
            self._emit(ToolEventType.STARTED, invocation, descriptor.name, attempt=attempt)
            try:
                async with asyncio.timeout(descriptor.timeout_seconds):
                    raw_result = await handler(invocation.arguments)
                self.circuit_breaker.record_success(descriptor.name)
                content = self._serialize(raw_result)
                content, artifact_id = self._externalize(content, descriptor.name)
                latency_ms = (self.clock() - started_at) * 1000
                self._emit(
                    ToolEventType.SUCCEEDED,
                    invocation,
                    descriptor.name,
                    attempt=attempt,
                    latency_ms=latency_ms,
                )
                return (
                    ToolResult(
                        call_id=invocation.call_id,
                        tool_name=descriptor.name,
                        content=content,
                        success=True,
                        attempts=attempt,
                        latency_ms=latency_ms,
                        artifact_id=artifact_id,
                    ),
                    attempt,
                )
            except Exception as exc:  # noqa: BLE001 - boundary normalizes tool failures
                opened = self.circuit_breaker.record_failure(descriptor.name)
                if opened:
                    self._emit(
                        ToolEventType.CIRCUIT_OPENED,
                        invocation,
                        descriptor.name,
                        attempt=attempt,
                        details={"error": self._error_text(exc)},
                    )
                if attempt >= self.policy.max_attempts or opened:
                    raise ToolExecutionError(self._error_text(exc)) from exc
                self._emit(
                    ToolEventType.RETRYING,
                    invocation,
                    descriptor.name,
                    attempt=attempt,
                    details={"error": self._error_text(exc)},
                )
                delay = self.policy.base_retry_delay_seconds * (2 ** (attempt - 1))
                await self.sleeper(delay)

        raise AssertionError("unreachable")

    def _authorize(self, descriptor: ToolDescriptor, invocation: ToolInvocation) -> None:
        if not descriptor.enabled:
            raise ToolPolicyDenied(f"tool is disabled: {descriptor.name}")
        if descriptor.allowed_agents and invocation.agent_name not in descriptor.allowed_agents:
            raise ToolPolicyDenied(f"agent cannot use tool: {descriptor.name}")
        if not descriptor.required_permissions.issubset(invocation.runtime.permissions):
            raise ToolPolicyDenied(f"missing tool permission: {descriptor.name}")
        if descriptor.risk_level > invocation.runtime.max_tool_risk:
            raise ToolPolicyDenied(f"tool exceeds run risk level: {descriptor.name}")
        if descriptor.requires_approval and not invocation.approval_granted:
            self._emit(ToolEventType.APPROVAL_REQUIRED, invocation, descriptor.name)
            raise ToolApprovalRequired(f"approval required: {descriptor.name}")

    def _externalize(self, content: str, tool_name: str) -> tuple[str, str | None]:
        if len(content) <= self.policy.artifact_threshold_chars:
            return content, None
        record = self.artifact_store.put_text(content, source=tool_name)
        preview = content[: self.policy.preview_chars]
        return f"{preview}\n\n[full result: artifact://{record.id}]", record.id

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _error_text(error: Exception) -> str:
        if isinstance(error, TimeoutError):
            return "tool call timed out"
        return f"{type(error).__name__}: {error}"

    @staticmethod
    def _cache_key(invocation: ToolInvocation) -> str:
        payload = json.dumps(
            {"tool": invocation.tool_name, "arguments": invocation.arguments},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _emit(
        self,
        event_type: ToolEventType,
        invocation: ToolInvocation,
        tool_name: str,
        *,
        attempt: int = 0,
        latency_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.event_sink.emit(
            ToolEvent(
                event_type=event_type,
                run_id=invocation.runtime.run_id,
                tool_call_id=invocation.call_id,
                tool_name=tool_name,
                attempt=attempt,
                latency_ms=latency_ms,
                details=details or {},
            )
        )
