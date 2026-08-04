from dataclasses import dataclass
from threading import Lock


class BudgetExceeded(RuntimeError):
    def __init__(self, dimension: str, used: float, limit: float) -> None:
        self.dimension = dimension
        self.used = used
        self.limit = limit
        super().__init__(f"{dimension} budget exceeded: used={used}, limit={limit}")


@dataclass(frozen=True, slots=True)
class RunBudgetLimits:
    model_calls: int = 20
    tool_calls: int = 24
    cost_units: float = 100

    def __post_init__(self) -> None:
        if self.model_calls < 1 or self.tool_calls < 1 or self.cost_units <= 0:
            raise ValueError("all run budget limits must be positive")


class RunBudget:
    """Thread-safe budget shared by parallel researcher branches."""

    def __init__(self, limits: RunBudgetLimits | None = None) -> None:
        self.limits = limits or RunBudgetLimits()
        self._model_calls = 0
        self._tool_calls = 0
        self._cost_units = 0.0
        self._lock = Lock()

    def consume_model_call(self, *, cost_units: float = 0) -> None:
        if cost_units < 0:
            raise ValueError("cost_units cannot be negative")
        with self._lock:
            next_calls = self._model_calls + 1
            next_cost = self._cost_units + cost_units
            self._check("model_calls", next_calls, self.limits.model_calls)
            self._check("cost_units", next_cost, self.limits.cost_units)
            self._model_calls = next_calls
            self._cost_units = next_cost

    def consume_tool_call(self, *, cost_units: float = 0) -> None:
        if cost_units < 0:
            raise ValueError("cost_units cannot be negative")
        with self._lock:
            next_calls = self._tool_calls + 1
            next_cost = self._cost_units + cost_units
            self._check("tool_calls", next_calls, self.limits.tool_calls)
            self._check("cost_units", next_cost, self.limits.cost_units)
            self._tool_calls = next_calls
            self._cost_units = next_cost

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "model_calls": self._model_calls,
                "tool_calls": self._tool_calls,
                "cost_units": self._cost_units,
            }

    @staticmethod
    def _check(dimension: str, used: float, limit: float) -> None:
        if used > limit:
            raise BudgetExceeded(dimension, used, limit)

