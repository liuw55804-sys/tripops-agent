import asyncio
import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

import yaml
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, Field, model_validator

from tripops.middleware.tool_execution import ToolExecutionEngine
from tripops.tools.models import RiskLevel, ToolDescriptor
from tripops.tools.registry import ToolRegistry


class MCPTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerConfig(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    transport: MCPTransport
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    discovery_timeout_seconds: float = Field(default=10, gt=0, le=120)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerConfig":
        if self.transport is MCPTransport.STDIO and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport is MCPTransport.STREAMABLE_HTTP and not self.url:
            raise ValueError("streamable_http MCP server requires url")
        return self

    def connection(self) -> dict[str, Any]:
        if self.transport is MCPTransport.STDIO:
            return {
                "transport": "stdio",
                "command": self.command,
                "args": list(self.args),
            }
        return {
            "transport": "streamable_http",
            "url": self.url,
            "headers": self.headers,
        }


class MCPToolPolicy(BaseModel):
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


class MCPConfigFile(BaseModel):
    servers: tuple[MCPServerConfig, ...]
    tool_policies: dict[str, MCPToolPolicy] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "MCPConfigFile":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


class MCPClientProtocol(Protocol):
    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]: ...


class MCPToolBinding(BaseModel):
    server_name: str
    original_name: str
    descriptor: ToolDescriptor
    tool: BaseTool

    model_config = {"arbitrary_types_allowed": True}


class MCPDiscoveryFailure(BaseModel):
    server_name: str
    error: str


class MCPDiscoveryReport(BaseModel):
    bindings: tuple[MCPToolBinding, ...] = ()
    failures: tuple[MCPDiscoveryFailure, ...] = ()

    model_config = {"arbitrary_types_allowed": True}


ClientFactory = Callable[[dict[str, dict[str, Any]]], MCPClientProtocol]


class MCPManager:
    """Discover MCP tools per server without making one failed server fatal."""

    def __init__(
        self,
        config: MCPConfigFile,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config
        self.client_factory = client_factory or self._default_client_factory
        self._client: MCPClientProtocol | None = None

    async def discover(self) -> MCPDiscoveryReport:
        enabled = tuple(server for server in self.config.servers if server.enabled)
        connections = {server.name: server.connection() for server in enabled}
        if not connections:
            return MCPDiscoveryReport()

        client = self.client_factory(connections)
        self._client = client
        tasks = [self._discover_server(client, server) for server in enabled]
        results = await asyncio.gather(*tasks)

        bindings: list[MCPToolBinding] = []
        failures: list[MCPDiscoveryFailure] = []
        for server_bindings, failure in results:
            bindings.extend(server_bindings)
            if failure is not None:
                failures.append(failure)
        return MCPDiscoveryReport(bindings=tuple(bindings), failures=tuple(failures))

    async def install(
        self,
        registry: ToolRegistry,
        execution_engine: ToolExecutionEngine,
    ) -> MCPDiscoveryReport:
        report = await self.discover()
        for binding in report.bindings:
            registry.register(binding.descriptor)

            async def handler(
                arguments: dict[str, Any],
                *,
                bound_tool: BaseTool = binding.tool,
            ) -> Any:
                return await bound_tool.ainvoke(arguments)

            execution_engine.register_handler(binding.descriptor.name, handler)
        return report

    async def _discover_server(
        self,
        client: MCPClientProtocol,
        server: MCPServerConfig,
    ) -> tuple[list[MCPToolBinding], MCPDiscoveryFailure | None]:
        try:
            async with asyncio.timeout(server.discovery_timeout_seconds):
                tools = await client.get_tools(server_name=server.name)
        except Exception as exc:  # noqa: BLE001 - discovery isolates provider failures
            return [], MCPDiscoveryFailure(
                server_name=server.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        bindings = [self._binding(server.name, tool) for tool in tools]
        return bindings, None

    def _binding(self, server_name: str, tool: BaseTool) -> MCPToolBinding:
        policy_key = f"{server_name}.{tool.name}"
        policy = self.config.tool_policies.get(policy_key)
        if policy is None:
            policy = MCPToolPolicy(capabilities=frozenset({policy_key}))
        governed_name = self._governed_name(server_name, tool.name)
        fallback_names = tuple(
            name if name.startswith("mcp.") else self._normalize_name(name)
            for name in policy.fallback_tools
        )
        descriptor = ToolDescriptor(
            name=governed_name,
            description=tool.description or f"MCP tool {policy_key}",
            capabilities=policy.capabilities,
            allowed_agents=policy.allowed_agents,
            required_permissions=policy.required_permissions,
            risk_level=policy.risk_level,
            requires_approval=policy.requires_approval,
            timeout_seconds=policy.timeout_seconds,
            estimated_latency_ms=policy.estimated_latency_ms,
            estimated_cost_units=policy.estimated_cost_units,
            freshness_seconds=policy.freshness_seconds,
            fallback_tools=fallback_names,
        )
        return MCPToolBinding(
            server_name=server_name,
            original_name=tool.name,
            descriptor=descriptor,
            tool=tool,
        )

    @classmethod
    def _governed_name(cls, server_name: str, tool_name: str) -> str:
        return cls._normalize_name(f"mcp.{server_name}.{tool_name}")

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-.")
        if not normalized or not normalized[0].isalpha():
            normalized = f"tool.{normalized}"
        return normalized

    @staticmethod
    def _default_client_factory(
        connections: dict[str, dict[str, Any]],
    ) -> MCPClientProtocol:
        return cast(
            MCPClientProtocol,
            MultiServerMCPClient(
                cast(Any, connections),
                tool_name_prefix=False,
                handle_tool_errors=False,
            ),
        )

