import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from tripops.context import FileArtifactStore, RunBudget
from tripops.middleware import CircuitBreaker, InMemoryEventSink, ToolExecutionEngine
from tripops.tools import ToolRegistry
from tripops.tools.mcp import MCPConfigFile, MCPManager


class FakeMCPClient:
    def __init__(
        self,
        tools: dict[str, list[BaseTool]],
        failures: frozenset[str] = frozenset(),
    ) -> None:
        self.tools = tools
        self.failures = failures

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        assert server_name is not None
        if server_name in self.failures:
            raise ConnectionError("server unavailable")
        return self.tools.get(server_name, [])


def make_tool() -> BaseTool:
    async def forecast(city: str) -> str:
        return f"sunny in {city}"

    return StructuredTool.from_function(
        coroutine=forecast,
        name="Get Forecast",
        description="Get a city forecast",
    )


def config() -> MCPConfigFile:
    return MCPConfigFile.model_validate(
        {
            "servers": [
                {"name": "weather", "transport": "stdio", "command": "python"},
                {"name": "broken", "transport": "stdio", "command": "python"},
            ],
            "tool_policies": {
                "weather.Get Forecast": {
                    "capabilities": ["weather_search"],
                    "allowed_agents": ["researcher"],
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_discovery_isolates_failed_server() -> None:
    client = FakeMCPClient(
        {"weather": [make_tool()]},
        failures=frozenset({"broken"}),
    )
    manager = MCPManager(config(), client_factory=lambda _: client)

    report = await manager.discover()

    assert report.bindings[0].descriptor.name == "mcp.weather.get-forecast"
    assert report.bindings[0].descriptor.capabilities == frozenset({"weather_search"})
    assert report.failures[0].server_name == "broken"


@pytest.mark.asyncio
async def test_install_binds_langchain_tool_to_execution_engine(tmp_path: Path) -> None:
    client = FakeMCPClient({"weather": [make_tool()], "broken": []})
    manager = MCPManager(config(), client_factory=lambda _: client)
    registry = ToolRegistry()
    engine = ToolExecutionEngine(
        registry=registry,
        budget=RunBudget(),
        circuit_breaker=CircuitBreaker(registry),
        artifact_store=FileArtifactStore(tmp_path),
        event_sink=InMemoryEventSink(),
    )

    report = await manager.install(registry, engine)

    assert len(report.bindings) == 1
    assert registry.get("mcp.weather.get-forecast").description == "Get a city forecast"


@pytest.mark.asyncio
async def test_discovery_timeout_becomes_failure() -> None:
    class SlowClient(FakeMCPClient):
        async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
            await asyncio.sleep(0.02)
            return []

    data: dict[str, Any] = {
        "servers": [
            {
                "name": "slow",
                "transport": "stdio",
                "command": "python",
                "discovery_timeout_seconds": 0.001,
            }
        ]
    }
    manager = MCPManager(
        MCPConfigFile.model_validate(data),
        client_factory=lambda _: SlowClient({}),
    )

    report = await manager.discover()

    assert report.failures[0].server_name == "slow"
    assert "TimeoutError" in report.failures[0].error


def test_repository_mcp_config_is_valid() -> None:
    project_root = Path(__file__).parents[2]

    parsed = MCPConfigFile.load(project_root / "config" / "mcp_servers.yaml")

    assert {server.name for server in parsed.servers} == {"weather", "transport", "policy"}
