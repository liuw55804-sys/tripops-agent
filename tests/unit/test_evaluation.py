from collections import Counter

import pytest

from tripops.evaluation import EvaluationRunner, build_travelplanner_suite
from tripops.evaluation.faults import middleware_fault_probe
from tripops.evaluation.metrics import preference_scores, safe_ratio
from tripops.evaluation.models import CaseCategory
from tripops.evaluation.reporting import render_markdown, write_report


def test_suite_has_documented_distribution_and_unique_ids() -> None:
    suite = build_travelplanner_suite()

    assert len(suite) == 80
    assert len({case.id for case in suite}) == 80
    assert Counter(case.category for case in suite) == {
        CaseCategory.STANDARD: 50,
        CaseCategory.DYNAMIC: 20,
        CaseCategory.FAULT: 10,
    }


@pytest.mark.asyncio
async def test_baseline_evaluation_detects_expected_violations_and_degrades() -> None:
    report = await EvaluationRunner(fault_probe=middleware_fault_probe).run(
        build_travelplanner_suite()
    )

    assert report.summary.case_count == 80
    assert report.summary.violation_precision == 1
    assert report.summary.violation_recall == 1
    assert report.summary.violation_f1 == 1
    assert report.summary.citation_correctness == 1
    assert report.summary.citation_freshness == 1
    assert report.summary.affected_item_recall == 1
    assert report.summary.plan_preservation == 1
    assert report.summary.degradation_success_rate == 1


@pytest.mark.asyncio
async def test_overlaps_shared_by_travelers_do_not_create_transit_false_positive() -> None:
    case = build_travelplanner_suite()[2]

    result = await EvaluationRunner().evaluate_case(case)

    assert result.actual_codes == result.expected_codes


def test_preference_metrics_penalize_unequal_coverage() -> None:
    case = build_travelplanner_suite()[0]
    museum_only = case.plan.model_copy(
        update={"itinerary": tuple(item for item in case.plan.itinerary if item.id == "museum")}
    )

    coverage, fairness = preference_scores(case.request, museum_only)

    assert coverage == 0.25
    assert fairness == 0.5
    assert safe_ratio(0, 0) == 1


@pytest.mark.asyncio
async def test_report_is_written_as_json_and_markdown(tmp_path) -> None:
    report = await EvaluationRunner(fault_probe=middleware_fault_probe).run(
        build_travelplanner_suite()
    )
    json_path = tmp_path / "baseline.json"
    markdown_path = tmp_path / "report.md"

    write_report(report, json_path=json_path, markdown_path=markdown_path)

    assert '"case_count": 80' in json_path.read_text(encoding="utf-8")
    assert "Labelled violation F1 | 100.0%" in render_markdown(report)
    assert markdown_path.read_text(encoding="utf-8").startswith("# TripOps")
