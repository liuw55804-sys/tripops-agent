from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from tripops.context import RunBudget, RunBudgetLimits, RuntimeContext
from tripops.middleware import (
    DynamicToolSelectionMiddleware,
    ModelBudgetMiddleware,
    ProgressiveSkillsMiddleware,
)
from tripops.skills import SkillRegistry
from tripops.tools import RiskLevel, ToolDescriptor, ToolRegistry


def runtime() -> RuntimeContext:
    return RuntimeContext(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        max_tool_risk=RiskLevel.READ_ONLY,
    )


def model_request(
    *,
    state: dict[str, Any],
    system_prompt: str = "base prompt",
) -> ModelRequest[RuntimeContext]:
    return ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[],
        system_prompt=system_prompt,
        state=cast(Any, state),
        runtime=cast(Any, SimpleNamespace(context=runtime())),
    )


@pytest.mark.asyncio
async def test_skills_are_loaded_only_after_selection(tmp_path: Path) -> None:
    skill_dir = tmp_path / "sample-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: sample-skill
description: Sample
capabilities: [planning]
---
SECRET FULL INSTRUCTIONS
""",
        encoding="utf-8",
    )
    middleware = ProgressiveSkillsMiddleware(SkillRegistry((tmp_path,)))
    prompts: list[str] = []

    async def handler(request: ModelRequest[RuntimeContext]) -> ModelResponse[Any]:
        prompts.append(request.system_prompt or "")
        return ModelResponse(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(model_request(state={"selected_skills": []}), handler)
    await middleware.awrap_model_call(
        model_request(state={"selected_skills": ["sample-skill"]}),
        handler,
    )

    assert "SECRET FULL INSTRUCTIONS" not in prompts[0]
    assert "SECRET FULL INSTRUCTIONS" in prompts[1]


@pytest.mark.asyncio
async def test_dynamic_tool_hook_exposes_only_matching_tool() -> None:
    registry = ToolRegistry()
    registry.register_many(
        [
            ToolDescriptor(
                name="weather.search",
                description="weather",
                capabilities=frozenset({"weather_search"}),
                allowed_agents=frozenset({"researcher"}),
            ),
            ToolDescriptor(
                name="hotel.search",
                description="hotel",
                capabilities=frozenset({"hotel_search"}),
                allowed_agents=frozenset({"researcher"}),
            ),
        ]
    )

    def stub(city: str) -> str:
        return city

    weather_tool = StructuredTool.from_function(
        func=stub,
        name="weather.search",
        description="weather",
    )
    hotel_tool = StructuredTool.from_function(
        func=stub,
        name="hotel.search",
        description="hotel",
    )
    middleware = DynamicToolSelectionMiddleware(
        registry,
        {"weather.search": weather_tool, "hotel.search": hotel_tool},
        agent_name="researcher",
    )
    exposed: list[str] = []

    async def handler(request: ModelRequest[RuntimeContext]) -> ModelResponse[Any]:
        exposed.extend(cast(Any, tool).name for tool in request.tools or [])
        return ModelResponse(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(
        model_request(state={"required_capabilities": ["weather_search"]}),
        handler,
    )

    assert exposed == ["weather.search"]


@pytest.mark.asyncio
async def test_model_budget_hook_enforces_limit() -> None:
    budget = RunBudget(RunBudgetLimits(model_calls=1, tool_calls=1, cost_units=10))
    middleware = ModelBudgetMiddleware(budget)

    async def handler(_: ModelRequest[RuntimeContext]) -> ModelResponse[Any]:
        return ModelResponse(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(model_request(state={}), handler)

    with pytest.raises(RuntimeError, match="model_calls budget exceeded"):
        await middleware.awrap_model_call(model_request(state={}), handler)

