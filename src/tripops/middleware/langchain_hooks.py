from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from tripops.context import RunBudget, RuntimeContext
from tripops.middleware.tool_execution import ToolExecutionEngine, ToolInvocation
from tripops.skills import SkillRegistry
from tripops.tools import ToolRegistry, ToolSelectionRequest

ModelHandler = Callable[[ModelRequest[RuntimeContext]], Awaitable[ModelResponse[Any]]]
ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]


class ProgressiveSkillsMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Inject full Skill instructions only after the graph selected their names."""

    def __init__(self, registry: SkillRegistry, *, max_instruction_chars: int = 12_000) -> None:
        self.registry = registry
        self.max_instruction_chars = max_instruction_chars

    async def awrap_model_call(
        self,
        request: ModelRequest[RuntimeContext],
        handler: ModelHandler,
    ) -> ModelResponse[Any] | AIMessage:
        state = cast(dict[str, Any], request.state or {})
        selected = tuple(dict.fromkeys(state.get("selected_skills", [])))
        if not selected:
            return await handler(request)

        sections: list[str] = []
        total_chars = 0
        for name in selected:
            loaded = self.registry.load(str(name))
            section = f"## Skill: {loaded.summary.name}\n\n{loaded.instructions}"
            total_chars += len(section)
            if total_chars > self.max_instruction_chars:
                raise ValueError("selected Skill instructions exceed context budget")
            sections.append(section)

        base_prompt = request.system_prompt or ""
        enriched = f"{base_prompt}\n\n# Selected skills\n\n" + "\n\n".join(sections)
        return await handler(
            request.override(system_message=SystemMessage(content=enriched.strip()))
        )


class DynamicToolSelectionMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Replace the model-visible tool list with Registry-approved candidates."""

    def __init__(
        self,
        registry: ToolRegistry,
        tools_by_name: dict[str, BaseTool],
        *,
        agent_name: str,
        max_tools: int = 6,
        allow_approval_tools: bool = False,
    ) -> None:
        self.registry = registry
        self.tools_by_name = tools_by_name
        self.agent_name = agent_name
        self.max_tools = max_tools
        self.allow_approval_tools = allow_approval_tools

    async def awrap_model_call(
        self,
        request: ModelRequest[RuntimeContext],
        handler: ModelHandler,
    ) -> ModelResponse[Any] | AIMessage:
        state = cast(dict[str, Any], request.state or {})
        capabilities = frozenset(str(item) for item in state.get("required_capabilities", []))
        if not capabilities:
            return await handler(request.override(tools=[]))
        if request.runtime is None:
            raise ValueError("dynamic tool selection requires RuntimeContext")

        runtime = request.runtime.context
        candidates = self.registry.select(
            ToolSelectionRequest(
                agent_name=self.agent_name,
                capabilities=capabilities,
                permissions=runtime.permissions,
                max_risk_level=runtime.max_tool_risk,
                allow_approval_tools=self.allow_approval_tools,
                max_tools=self.max_tools,
            )
        )
        tools: list[BaseTool | dict[str, Any]] = [
            self.tools_by_name[candidate.descriptor.name]
            for candidate in candidates
            if candidate.descriptor.name in self.tools_by_name
        ]
        return await handler(request.override(tools=tools))


class ModelBudgetMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    def __init__(self, budget: RunBudget, *, estimated_cost_units: float = 0) -> None:
        self.budget = budget
        self.estimated_cost_units = estimated_cost_units

    async def awrap_model_call(
        self,
        request: ModelRequest[RuntimeContext],
        handler: ModelHandler,
    ) -> ModelResponse[Any] | AIMessage:
        self.budget.consume_model_call(cost_units=self.estimated_cost_units)
        return await handler(request)


class GovernedToolExecutionMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Route LangChain tool calls through the TripOps reliability engine."""

    def __init__(self, engine: ToolExecutionEngine, *, agent_name: str) -> None:
        self.engine = engine
        self.agent_name = agent_name

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolMessage | Command[Any]:
        runtime_context = request.runtime.context
        if not isinstance(runtime_context, RuntimeContext):
            raise TypeError("governed tool execution requires RuntimeContext")

        state = cast(dict[str, Any], request.state or {})
        approval_granted = bool(state.get("approval_granted", False))
        result = await self.engine.execute(
            ToolInvocation(
                call_id=str(request.tool_call["id"]),
                tool_name=str(request.tool_call["name"]),
                arguments=dict(request.tool_call.get("args", {})),
                agent_name=self.agent_name,
                runtime=runtime_context,
                approval_granted=approval_granted,
            )
        )
        return ToolMessage(
            content=result.content if result.success else (result.error or "tool execution failed"),
            tool_call_id=result.call_id,
            name=result.tool_name,
            status="success" if result.success else "error",
            artifact=result.model_dump(mode="json"),
        )
