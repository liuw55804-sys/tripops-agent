import time
from collections.abc import Callable
from dataclasses import dataclass

from tripops.tools.models import CircuitState
from tripops.tools.registry import ToolRegistry


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if self.failure_threshold < 1 or self.recovery_timeout_seconds <= 0:
            raise ValueError("circuit breaker policy values must be positive")


@dataclass(slots=True)
class _CircuitRecord:
    failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: CircuitBreakerPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.policy = policy or CircuitBreakerPolicy()
        self.clock = clock
        self._records: dict[str, _CircuitRecord] = {}

    def before_call(self, tool_name: str) -> CircuitState:
        state = self.registry.circuit_state(tool_name)
        if state is not CircuitState.OPEN:
            return state

        record = self._record(tool_name)
        if record.opened_at is None:
            raise CircuitOpenError(f"circuit is open: {tool_name}")
        elapsed = self.clock() - record.opened_at
        if elapsed < self.policy.recovery_timeout_seconds:
            raise CircuitOpenError(f"circuit is open: {tool_name}")

        self.registry.set_circuit_state(tool_name, CircuitState.HALF_OPEN)
        return CircuitState.HALF_OPEN

    def record_success(self, tool_name: str) -> None:
        record = self._record(tool_name)
        record.failures = 0
        record.opened_at = None
        self.registry.set_circuit_state(tool_name, CircuitState.CLOSED)

    def record_failure(self, tool_name: str) -> bool:
        record = self._record(tool_name)
        record.failures += 1
        state = self.registry.circuit_state(tool_name)
        should_open = (
            record.failures >= self.policy.failure_threshold or state is CircuitState.HALF_OPEN
        )
        if should_open:
            record.opened_at = self.clock()
            self.registry.set_circuit_state(tool_name, CircuitState.OPEN)
        return should_open

    def _record(self, tool_name: str) -> _CircuitRecord:
        self.registry.get(tool_name)
        return self._records.setdefault(tool_name, _CircuitRecord())
