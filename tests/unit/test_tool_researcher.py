from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tripops.agents.models import ResearchTask
from tripops.agents.tool_researcher import GovernedToolResearcher
from tripops.context import FileArtifactStore, RunBudget, RuntimeContext
from tripops.domain import PlanStep, Traveler, TripRequest
from tripops.middleware import CircuitBreaker, InMemoryEventSink, ToolExecutionEngine
from tripops.tools import ToolDescriptor, ToolRegistry


def request() -> TripRequest:
    return TripRequest(
        id="tool-trip",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 3),
        budget=Decimal("1000"),
        travelers=(Traveler(id="alice", display_name="Alice"),),
    )


def runtime() -> RuntimeContext:
    return RuntimeContext(
        run_id="run-tool",
        thread_id="thread-tool",
        user_id="alice",
    )


def build_researcher(
    tmp_path: Path,
    descriptors: tuple[ToolDescriptor, ...],
) -> tuple[GovernedToolResearcher, ToolExecutionEngine]:
    registry = ToolRegistry()
    registry.register_many(descriptors)
    engine = ToolExecutionEngine(
        registry=registry,
        budget=RunBudget(),
        circuit_breaker=CircuitBreaker(registry),
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        event_sink=InMemoryEventSink(),
    )
    researcher = GovernedToolResearcher(
        name="weather_researcher",
        capabilities=frozenset({"weather_search"}),
        registry=registry,
        engine=engine,
        runtime=runtime(),
    )
    return researcher, engine


def descriptor(name: str, **updates: Any) -> ToolDescriptor:
    values: dict[str, Any] = {
        "name": name,
        "description": "Weather lookup",
        "capabilities": frozenset({"weather_search"}),
        "allowed_agents": frozenset({"weather_researcher"}),
        "freshness_seconds": 3600,
    }
    values.update(updates)
    return ToolDescriptor.model_validate(values)


def task(capability: str = "weather_search") -> ResearchTask:
    return ResearchTask(
        request=request(),
        step=PlanStep(id="r1-weather", title="Kyoto weather", capability=capability),
        plan_revision=1,
        run_id="active-run",
    )


@pytest.mark.asyncio
async def test_researcher_selects_governed_tool_and_returns_fresh_evidence(
    tmp_path: Path,
) -> None:
    researcher, engine = build_researcher(tmp_path, (descriptor("weather.primary"),))
    captured: dict[str, Any] = {}

    async def handler(arguments: dict[str, Any]) -> dict[str, str]:
        captured.update(arguments)
        return {"summary": "Rain after 16:00"}

    engine.register_handler("weather.primary", handler)

    result = await researcher.research(task())

    assert result.success
    assert result.evidence[0].id == "ev-r1-weather"
    assert result.evidence[0].claim == "Rain after 16:00"
    assert result.evidence[0].source_name == "weather.primary"
    assert result.evidence[0].expires_at is not None
    assert captured["location"] == "Kyoto"
    assert captured["start_date"] == "2030-10-01"
    assert captured["end_date"] == "2030-10-03"
    assert captured["capability"] == "weather_search"
    assert captured["destinations"] == ["Kyoto"]


@pytest.mark.asyncio
async def test_researcher_parses_cited_candidate_facts_and_uses_active_run_id(
    tmp_path: Path,
) -> None:
    researcher, engine = build_researcher(tmp_path, (descriptor("weather.primary"),))

    async def handler(_: dict[str, Any]) -> dict[str, object]:
        return {
            "evidence": [
                {
                    "claim": "Kyoto Botanical Garden has documented public access.",
                    "source_name": "provider",
                    "source_uri": "https://example.com/garden",
                    "confidence": 0.87,
                    "candidate": {
                        "id": "garden",
                        "title": "Kyoto Botanical Garden",
                        "location": "Kyoto",
                        "category": "park",
                        "tags": ["park", "nature"],
                        "preferred_period": "morning",
                    },
                }
            ]
        }

    engine.register_handler("weather.primary", handler)

    result = await researcher.research(task())
    events = engine.event_sink.snapshot()

    assert result.evidence[0].source_uri is not None
    assert result.evidence[0].source_name == "provider"
    assert result.candidate_facts[0].evidence_id == result.evidence[0].id
    assert result.candidate_facts[0].source_capability == "weather_search"
    assert {event.run_id for event in events} == {"active-run"}


@pytest.mark.asyncio
async def test_researcher_exposes_engine_fallback_as_degraded_evidence(
    tmp_path: Path,
) -> None:
    primary = descriptor("weather.primary", fallback_tools=("weather.snapshot",))
    backup = descriptor("weather.snapshot")
    researcher, engine = build_researcher(tmp_path, (primary, backup))

    async def broken(_: dict[str, Any]) -> str:
        raise ConnectionError("provider down")

    async def fallback(_: dict[str, Any]) -> str:
        return "Last known forecast"

    engine.register_handler("weather.primary", broken)
    engine.register_handler("weather.snapshot", fallback)

    result = await researcher.research(task())

    assert result.success
    assert result.evidence[0].source_name == "weather.snapshot"
    assert result.evidence[0].confidence == 0.8
    assert result.evidence[0].metadata["degraded"] is True


@pytest.mark.asyncio
async def test_researcher_fails_without_eligible_capability(tmp_path: Path) -> None:
    researcher, _ = build_researcher(tmp_path, (descriptor("weather.primary"),))

    result = await researcher.research(task("policy_search"))

    assert not result.success
    assert result.error == "unsupported capability: policy_search"
