import pytest

from tripops.tools import (
    CircuitState,
    RiskLevel,
    ToolDescriptor,
    ToolRegistry,
    ToolSelectionRequest,
)


def descriptor(name: str, **overrides: object) -> ToolDescriptor:
    values: dict[str, object] = {
        "name": name,
        "description": f"Tool {name}",
        "capabilities": frozenset({"weather_search"}),
        "allowed_agents": frozenset({"weather_researcher"}),
    }
    values.update(overrides)
    return ToolDescriptor.model_validate(values)


def test_select_filters_by_agent_permission_risk_and_approval() -> None:
    registry = ToolRegistry()
    registry.register_many(
        [
            descriptor("weather.primary", estimated_latency_ms=800),
            descriptor("weather.fast", estimated_latency_ms=100),
            descriptor(
                "booking.change",
                capabilities=frozenset({"booking_change"}),
                required_permissions=frozenset({"booking:write"}),
                risk_level=RiskLevel.FINANCIAL,
                requires_approval=True,
            ),
        ]
    )

    selected = registry.select(
        ToolSelectionRequest(
            agent_name="weather_researcher",
            capabilities=frozenset({"weather_search", "booking_change"}),
        )
    )

    assert [candidate.descriptor.name for candidate in selected] == [
        "weather.fast",
        "weather.primary",
    ]


def test_open_circuit_removes_tool_and_exposes_fallback() -> None:
    registry = ToolRegistry()
    registry.register(
        descriptor("weather.primary", fallback_tools=("weather.fallback",))
    )
    registry.register(descriptor("weather.fallback"))
    registry.set_circuit_state("weather.primary", CircuitState.OPEN)

    selected = registry.select(
        ToolSelectionRequest(
            agent_name="weather_researcher",
            capabilities=frozenset({"weather_search"}),
        )
    )

    assert [candidate.descriptor.name for candidate in selected] == ["weather.fallback"]
    assert registry.fallback_chain("weather.primary")[0].name == "weather.fallback"


def test_duplicate_tool_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(descriptor("weather.primary"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(descriptor("weather.primary"))

