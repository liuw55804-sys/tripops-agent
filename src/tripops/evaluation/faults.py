import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn

from tripops.context import FileArtifactStore, RunBudget, RuntimeContext
from tripops.evaluation.models import FaultMode
from tripops.middleware import (
    CircuitBreaker,
    InMemoryEventSink,
    ToolExecutionEngine,
    ToolExecutionPolicy,
    ToolInvocation,
)
from tripops.tools import ToolDescriptor, ToolRegistry


async def middleware_fault_probe(mode: FaultMode) -> bool:
    """Inject a primary-tool failure and require the governed fallback to succeed."""
    registry = ToolRegistry()
    registry.register_many(
        (
            ToolDescriptor(
                name="remote_search",
                description="Fault-injected primary provider",
                capabilities=frozenset({"poi_search"}),
                timeout_seconds=0.01,
                fallback_tools=("local_snapshot",),
            ),
            ToolDescriptor(
                name="local_snapshot",
                description="Offline degraded data source",
                capabilities=frozenset({"poi_search"}),
                timeout_seconds=1,
            ),
        )
    )
    with TemporaryDirectory(prefix="tripops-eval-") as temporary:
        engine = ToolExecutionEngine(
            registry=registry,
            budget=RunBudget(),
            circuit_breaker=CircuitBreaker(registry),
            artifact_store=FileArtifactStore(Path(temporary)),
            event_sink=InMemoryEventSink(),
            policy=ToolExecutionPolicy(max_attempts=1),
        )

        async def fail(_: dict[str, object]) -> NoReturn:
            if mode is FaultMode.TIMEOUT:
                await asyncio.sleep(0.02)
                raise TimeoutError
            errors: dict[FaultMode, Exception] = {
                FaultMode.PROVIDER_ERROR: ConnectionError("provider unavailable"),
                FaultMode.MALFORMED_RESPONSE: ValueError("invalid provider payload"),
                FaultMode.RATE_LIMIT: RuntimeError("429 rate limit"),
                FaultMode.PRIMARY_UNAVAILABLE: LookupError("provider not configured"),
            }
            raise errors[mode]

        async def fallback(_: dict[str, object]) -> dict[str, object]:
            return {"source": "local_snapshot", "degraded": True, "items": []}

        engine.register_handler("remote_search", fail)
        engine.register_handler("local_snapshot", fallback)
        result = await engine.execute(
            ToolInvocation(
                call_id=f"fault-{mode.value}",
                tool_name="remote_search",
                arguments={"query": "West Lake"},
                agent_name="Researcher",
                runtime=RuntimeContext(
                    run_id="evaluation",
                    thread_id="evaluation",
                    user_id="evaluation",
                ),
            )
        )
        return result.success and result.degraded and result.tool_name == "local_snapshot"
