from tripops.agents.models import ResearchResult, ResearchTask
from tripops.rag.citations import CitationBundle
from tripops.rag.hybrid import HybridRetriever


class HybridRAGResearcher:
    """Researcher that turns fused retrieval results into citation-complete Evidence."""

    def __init__(
        self,
        *,
        name: str,
        capabilities: frozenset[str],
        retriever: HybridRetriever,
        result_limit: int = 6,
    ) -> None:
        if result_limit < 1:
            raise ValueError("result limit must be positive")
        self.name = name
        self.capabilities = capabilities
        self.retriever = retriever
        self.result_limit = result_limit

    async def research(self, task: ResearchTask) -> ResearchResult:
        query = self._query(task)
        results = await self.retriever.retrieve(query, limit=self.result_limit)
        bundle = CitationBundle.from_results(results)
        evidence = tuple(
            item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "plan_revision": task.plan_revision,
                        "step_id": task.step.id,
                        "retrieval_channels": ",".join(
                            sorted(results[index].channel_ranks)
                        ),
                    }
                }
            )
            for index, item in enumerate(bundle.to_evidence())
        )
        return ResearchResult(
            step_id=task.step.id,
            plan_revision=task.plan_revision,
            agent_name=self.name,
            success=bool(evidence),
            evidence=evidence,
            error=None if evidence else "hybrid retrieval returned no evidence",
        )

    @staticmethod
    def _query(task: ResearchTask) -> str:
        request = task.request
        return " ".join(
            [
                task.step.title,
                task.step.capability,
                request.origin,
                *request.destinations,
                request.raw_requirement,
            ]
        ).strip()

