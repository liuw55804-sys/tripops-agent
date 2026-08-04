from collections.abc import Iterable

from tripops.tools.models import (
    CircuitState,
    ToolCandidate,
    ToolDescriptor,
    ToolSelectionRequest,
)


class ToolRegistry:
    """Select the smallest usable tool set before a model call.

    The registry deliberately does not execute tools. Execution policy belongs to
    middleware, while this class owns discovery and deterministic eligibility.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._circuits: dict[str, CircuitState] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        if descriptor.name in self._descriptors:
            raise ValueError(f"tool already registered: {descriptor.name}")
        self._descriptors[descriptor.name] = descriptor
        self._circuits[descriptor.name] = CircuitState.CLOSED

    def register_many(self, descriptors: Iterable[ToolDescriptor]) -> None:
        for descriptor in descriptors:
            self.register(descriptor)

    def get(self, name: str) -> ToolDescriptor:
        try:
            return self._descriptors[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def set_circuit_state(self, name: str, state: CircuitState) -> None:
        self.get(name)
        self._circuits[name] = state

    def circuit_state(self, name: str) -> CircuitState:
        self.get(name)
        return self._circuits[name]

    def select(self, request: ToolSelectionRequest) -> tuple[ToolCandidate, ...]:
        candidates: list[ToolCandidate] = []

        for descriptor in self._descriptors.values():
            matched = descriptor.capabilities & request.capabilities
            circuit_state = self._circuits[descriptor.name]
            if not matched or not self._is_eligible(descriptor, circuit_state, request):
                continue

            candidates.append(
                ToolCandidate(
                    descriptor=descriptor,
                    matched_capabilities=matched,
                    score=self._score(descriptor, matched, circuit_state),
                    circuit_state=circuit_state,
                )
            )

        candidates.sort(key=lambda item: (-item.score, item.descriptor.name))
        return tuple(candidates[: request.max_tools])

    def fallback_chain(self, name: str) -> tuple[ToolDescriptor, ...]:
        """Return configured fallbacks without recursively expanding their fallbacks."""

        descriptor = self.get(name)
        fallbacks: list[ToolDescriptor] = []
        for fallback_name in descriptor.fallback_tools:
            fallback = self.get(fallback_name)
            if fallback.enabled and self._circuits[fallback.name] is not CircuitState.OPEN:
                fallbacks.append(fallback)
        return tuple(fallbacks)

    @staticmethod
    def _is_eligible(
        descriptor: ToolDescriptor,
        circuit_state: CircuitState,
        request: ToolSelectionRequest,
    ) -> bool:
        if not descriptor.enabled or circuit_state is CircuitState.OPEN:
            return False
        if descriptor.allowed_agents and request.agent_name not in descriptor.allowed_agents:
            return False
        if not descriptor.required_permissions.issubset(request.permissions):
            return False
        if descriptor.risk_level > request.max_risk_level:
            return False
        return not descriptor.requires_approval or request.allow_approval_tools

    @staticmethod
    def _score(
        descriptor: ToolDescriptor,
        matched: frozenset[str],
        circuit_state: CircuitState,
    ) -> float:
        capability_score = float(len(matched) * 100)
        latency_penalty = min(descriptor.estimated_latency_ms / 1000, 20)
        cost_penalty = min(descriptor.estimated_cost_units * 5, 20)
        half_open_penalty = 25 if circuit_state is CircuitState.HALF_OPEN else 0
        return capability_score - latency_penalty - cost_penalty - half_open_penalty
