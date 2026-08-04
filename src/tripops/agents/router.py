from tripops.agents.contracts import ResearcherAgent


class ResearcherRouter:
    def __init__(self, researchers: tuple[ResearcherAgent, ...]) -> None:
        if not researchers:
            raise ValueError("at least one researcher is required")
        self.researchers = researchers

    def route(self, capability: str) -> ResearcherAgent:
        candidates = [
            researcher for researcher in self.researchers if capability in researcher.capabilities
        ]
        if not candidates:
            candidates = [
                researcher
                for researcher in self.researchers
                if "general_research" in researcher.capabilities
            ]
        if not candidates:
            raise LookupError(f"no researcher supports capability: {capability}")
        return sorted(candidates, key=lambda researcher: researcher.name)[0]

