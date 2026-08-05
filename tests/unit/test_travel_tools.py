from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tripops.context import FileArtifactStore, RunBudget, RuntimeContext
from tripops.middleware import (
    CircuitBreaker,
    InMemoryEventSink,
    ToolExecutionEngine,
    ToolInvocation,
)
from tripops.tools import ToolRegistry
from tripops.tools.travel import install_live_travel_tools


def response(request: httpx.Request) -> httpx.Response:
    if request.url.host == "geocoding-api.open-meteo.com":
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": "Sydney", "latitude": -33.87, "longitude": 151.21}
                ]
            },
        )
    if request.url.host == "api.open-meteo.com":
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": ["2030-01-01"],
                    "temperature_2m_max": [25],
                    "temperature_2m_min": [17],
                    "precipitation_probability_max": [30],
                }
            },
        )
    if request.url.host == "en.wikipedia.org":
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": {
                        "1": {
                            "index": 1,
                            "title": "Royal Botanic Garden, Sydney",
                            "extract": "A public botanical garden beside Sydney Harbour.",
                            "fullurl": "https://en.wikipedia.org/wiki/Royal_Botanic_Garden,_Sydney",
                        },
                        "2": {
                            "index": 2,
                            "title": "1999 Sydney hailstorm",
                            "extract": "A severe weather event that damaged buildings.",
                            "fullurl": "https://en.wikipedia.org/wiki/1999_Sydney_hailstorm",
                        }
                    }
                }
            },
        )
    raise AssertionError(f"unexpected request: {request.url}")


def engine(tmp_path: Path, registry: ToolRegistry) -> ToolExecutionEngine:
    return ToolExecutionEngine(
        registry=registry,
        budget=RunBudget(),
        circuit_breaker=CircuitBreaker(registry),
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        event_sink=InMemoryEventSink(),
    )


async def invoke(
    tool_engine: ToolExecutionEngine,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await tool_engine.execute(
        ToolInvocation(
            call_id=f"call-{tool_name}",
            tool_name=tool_name,
            arguments=arguments,
            agent_name="live_researcher",
            runtime=RuntimeContext(run_id="run", thread_id="run", user_id="test"),
        )
    )
    assert result.success
    return httpx.Response(200, text=result.content).json()


@pytest.mark.asyncio
async def test_open_meteo_and_wikipedia_tools_return_cited_provider_data(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    tool_engine = engine(tmp_path, registry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        capabilities = install_live_travel_tools(registry, tool_engine, client)
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        weather = await invoke(
            tool_engine,
            "live.open_meteo.forecast",
            {
                "location": "Sydney",
                "start_date": tomorrow.isoformat(),
                "end_date": tomorrow.isoformat(),
            },
        )
        places = await invoke(
            tool_engine,
            "live.wikipedia.nearby_places",
            {"destinations": ["Sydney"]},
        )

    assert {"weather_search", "poi_search", "accessibility_search"} <= capabilities
    assert weather["evidence"][0]["source_name"] == "open-meteo"
    assert places["evidence"][0]["candidate"]["title"] == (
        "Royal Botanic Garden, Sydney"
    )
    assert len(places["evidence"]) == 1
    assert places["evidence"][0]["source_uri"].startswith("https://en.wikipedia.org/")


@pytest.mark.asyncio
async def test_tavily_tool_is_registered_only_when_key_is_configured(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    tool_engine = engine(tmp_path, registry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        capabilities = install_live_travel_tools(
            registry,
            tool_engine,
            client,
            tavily_api_key="configured-for-test",
        )

    assert "transport_search" in capabilities
    assert registry.get("live.tavily.search").enabled
