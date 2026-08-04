import asyncio
from pathlib import Path
from typing import Any

import pytest

from tripops.context import FileArtifactStore, RunBudget, RunBudgetLimits, RuntimeContext
from tripops.middleware import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    InMemoryEventSink,
    ToolEventType,
    ToolExecutionEngine,
    ToolExecutionPolicy,
    ToolInvocation,
)
from tripops.middleware.tool_execution import ToolApprovalRequired
from tripops.tools import RiskLevel, ToolDescriptor, ToolRegistry


def make_runtime(*, risk: RiskLevel = RiskLevel.READ_ONLY) -> RuntimeContext:
    return RuntimeContext(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        permissions=frozenset({"booking:write"}),
        max_tool_risk=risk,
    )


def make_engine(
    tmp_path: Path,
    descriptors: list[ToolDescriptor],
    *,
    failure_threshold: int = 10,
    max_attempts: int = 3,
    artifact_threshold: int = 8_000,
) -> tuple[ToolExecutionEngine, ToolRegistry, InMemoryEventSink]:
    registry = ToolRegistry()
    registry.register_many(descriptors)
    sink = InMemoryEventSink()
    breaker = CircuitBreaker(
        registry,
        CircuitBreakerPolicy(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=30,
        ),
    )

    async def no_sleep(_: float) -> None:
        return None

    engine = ToolExecutionEngine(
        registry=registry,
        budget=RunBudget(RunBudgetLimits(tool_calls=20, model_calls=5, cost_units=20)),
        circuit_breaker=breaker,
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        event_sink=sink,
        policy=ToolExecutionPolicy(
            max_attempts=max_attempts,
            base_retry_delay_seconds=0,
            artifact_threshold_chars=artifact_threshold,
            preview_chars=20,
        ),
        sleeper=no_sleep,
    )
    return engine, registry, sink


def tool(name: str, **values: Any) -> ToolDescriptor:
    data: dict[str, Any] = {
        "name": name,
        "description": name,
        "capabilities": frozenset({"weather_search"}),
        "allowed_agents": frozenset({"researcher"}),
        "timeout_seconds": 1,
    }
    data.update(values)
    return ToolDescriptor.model_validate(data)


def invocation(
    name: str,
    *,
    approval: bool = False,
    risk: RiskLevel = RiskLevel.READ_ONLY,
) -> ToolInvocation:
    return ToolInvocation(
        call_id="call-1",
        tool_name=name,
        arguments={"city": "Kyoto"},
        agent_name="researcher",
        runtime=make_runtime(risk=risk),
        approval_granted=approval,
    )


@pytest.mark.asyncio
async def test_retry_then_cache_hit(tmp_path: Path) -> None:
    engine, _, sink = make_engine(tmp_path, [tool("weather.primary")])
    calls = 0

    async def flaky(_: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return "sunny"

    engine.register_handler("weather.primary", flaky)

    first = await engine.execute(invocation("weather.primary"))
    second = await engine.execute(invocation("weather.primary"))

    assert first.success and first.attempts == 2
    assert second.success and second.cache_hit
    assert calls == 2
    assert ToolEventType.RETRYING in {event.event_type for event in sink.snapshot()}


@pytest.mark.asyncio
async def test_open_circuit_falls_back(tmp_path: Path) -> None:
    primary = tool("weather.primary", fallback_tools=("weather.backup",))
    backup = tool("weather.backup")
    engine, registry, _ = make_engine(
        tmp_path,
        [primary, backup],
        failure_threshold=1,
        max_attempts=1,
    )

    async def broken(_: dict[str, Any]) -> str:
        raise ConnectionError("provider down")

    async def working(_: dict[str, Any]) -> str:
        return "cloudy"

    engine.register_handler("weather.primary", broken)
    engine.register_handler("weather.backup", working)

    result = await engine.execute(invocation("weather.primary"))

    assert result.success and result.degraded
    assert result.tool_name == "weather.backup"
    assert registry.circuit_state("weather.primary").value == "open"


@pytest.mark.asyncio
async def test_timeout_is_normalized_and_falls_back(tmp_path: Path) -> None:
    primary = tool("weather.slow", timeout_seconds=0.001, fallback_tools=("weather.backup",))
    backup = tool("weather.backup")
    engine, _, _ = make_engine(tmp_path, [primary, backup], max_attempts=1)

    async def slow(_: dict[str, Any]) -> str:
        await asyncio.sleep(0.02)
        return "late"

    async def working(_: dict[str, Any]) -> str:
        return "rain"

    engine.register_handler("weather.slow", slow)
    engine.register_handler("weather.backup", working)

    result = await engine.execute(invocation("weather.slow"))

    assert result.success and result.degraded
    assert result.content == "rain"


@pytest.mark.asyncio
async def test_high_risk_tool_requires_approval(tmp_path: Path) -> None:
    booking = tool(
        "booking.change",
        capabilities=frozenset({"booking_change"}),
        risk_level=RiskLevel.FINANCIAL,
        requires_approval=True,
        required_permissions=frozenset({"booking:write"}),
    )
    engine, _, _ = make_engine(tmp_path, [booking])

    async def handler(_: dict[str, Any]) -> str:
        return "changed"

    engine.register_handler("booking.change", handler)

    with pytest.raises(ToolApprovalRequired):
        await engine.execute(invocation("booking.change", risk=RiskLevel.FINANCIAL))

    result = await engine.execute(
        invocation("booking.change", approval=True, risk=RiskLevel.FINANCIAL)
    )
    assert result.success


@pytest.mark.asyncio
async def test_large_result_is_externalized(tmp_path: Path) -> None:
    engine, _, _ = make_engine(
        tmp_path,
        [tool("weather.primary")],
        artifact_threshold=10,
    )

    async def large(_: dict[str, Any]) -> str:
        return "x" * 100

    engine.register_handler("weather.primary", large)

    result = await engine.execute(invocation("weather.primary"))

    assert result.artifact_id is not None
    assert f"artifact://{result.artifact_id}" in result.content
