import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import cast

from tripops.constraints import DeterministicConstraintVerifier, ImpactAnalyzer
from tripops.context.state import TripOpsState, WorkflowPhase
from tripops.domain.violations import ViolationSeverity
from tripops.evaluation.metrics import citation_scores, preference_scores, set_recall, summarize
from tripops.evaluation.models import (
    CaseCategory,
    EvaluationCase,
    EvaluationReport,
    EvaluationResult,
    FaultMode,
)

FaultProbe = Callable[[FaultMode], Awaitable[bool]]


class EvaluationRunner:
    def __init__(
        self,
        *,
        verifier: DeterministicConstraintVerifier | None = None,
        impact_analyzer: ImpactAnalyzer | None = None,
        fault_probe: FaultProbe | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.verifier = verifier or DeterministicConstraintVerifier()
        self.impact_analyzer = impact_analyzer or ImpactAnalyzer()
        self.fault_probe = fault_probe
        self.clock = clock

    async def run(
        self,
        cases: Sequence[EvaluationCase],
        *,
        suite_name: str = "tripops-travelplanner-v1",
    ) -> EvaluationReport:
        if not cases:
            raise ValueError("evaluation suite cannot be empty")
        results = tuple([await self.evaluate_case(case) for case in cases])
        categories = {
            category: summarize(item for item in results if item.category is category)
            for category in CaseCategory
        }
        return EvaluationReport(
            suite_name=suite_name,
            generated_at=datetime.now(UTC).isoformat(),
            summary=summarize(results),
            by_category=categories,
            results=results,
        )

    async def evaluate_case(self, case: EvaluationCase) -> EvaluationResult:
        started_at = self.clock()
        state = cast(
            TripOpsState,
            {
                "messages": [],
                "phase": WorkflowPhase.VERIFY,
                "request": case.request,
                "plan": case.plan,
                "evidence": list(case.evidence),
                "violations": [],
                "selected_skills": [],
                "required_capabilities": [],
            },
        )
        violations = await self.verifier.verify(state)
        actual = frozenset(item.code for item in violations)
        expected = case.expected_violation_codes
        hard_constraint_pass = not any(
            item.severity is ViolationSeverity.ERROR for item in violations
        )
        preference_coverage, fairness = preference_scores(case.request, case.plan)
        citation_correctness, citation_freshness = citation_scores(case.plan, case.evidence)
        scope = self.impact_analyzer.analyze(case.plan, violations, case.disruption)
        affected_recall = set_recall(
            case.expected_affected_item_ids,
            set(scope.affected_item_ids),
        )
        preservation = set_recall(
            case.expected_preserved_item_ids,
            set(scope.preserved_item_ids),
        )
        degradation_success = True
        if case.category is CaseCategory.FAULT:
            degradation_success = bool(
                self.fault_probe and await self.fault_probe(case.fault_mode)
            )
        return EvaluationResult(
            case_id=case.id,
            category=case.category,
            expected_codes=expected,
            actual_codes=actual,
            true_positives=len(expected & actual),
            false_positives=len(actual - expected),
            false_negatives=len(expected - actual),
            hard_constraint_pass=hard_constraint_pass,
            preference_coverage=preference_coverage,
            group_fairness=fairness,
            citation_correctness=citation_correctness,
            citation_freshness=citation_freshness,
            affected_item_recall=affected_recall,
            plan_preservation=preservation,
            degradation_success=degradation_success,
            latency_ms=(self.clock() - started_at) * 1000,
        )
