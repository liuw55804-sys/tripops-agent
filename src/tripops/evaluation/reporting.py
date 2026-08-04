import json
from pathlib import Path

from tripops.evaluation.models import CaseCategory, EvaluationReport, MetricSummary


def render_markdown(report: EvaluationReport) -> str:
    summary = report.summary
    lines = [
        "# TripOps Offline Evaluation",
        "",
        f"- Suite: `{report.suite_name}`",
        f"- Generated at: `{report.generated_at}`",
        f"- Cases: **{summary.case_count}**",
        "",
        "## Headline metrics",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        *_metric_rows(summary),
        "",
        "## Breakdown",
        "",
        "| Category | Cases | Violation F1 | Citation correctness | "
        "Affected recall | Preservation | Degradation |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category in CaseCategory:
        metric = report.by_category[category]
        lines.append(
            f"| {category.value} | {metric.case_count} | {_pct(metric.violation_f1)} | "
            f"{_pct(metric.citation_correctness)} | {_pct(metric.affected_item_recall)} | "
            f"{_pct(metric.plan_preservation)} | {_pct(metric.degradation_success_rate)} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The suite validates deterministic constraint detection against labelled failures, "
            "citation integrity and freshness, multi-traveler preference fairness, local impact "
            "analysis, and middleware fallback under injected provider faults. The plan "
            "feasibility rate is intentionally not a model-quality claim: labelled cases include "
            "invalid plans so the verifier's precision and recall can be measured.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: EvaluationReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def _metric_rows(summary: MetricSummary) -> list[str]:
    return [
        f"| Labelled violation precision | {_pct(summary.violation_precision)} |",
        f"| Labelled violation recall | {_pct(summary.violation_recall)} |",
        f"| Labelled violation F1 | {_pct(summary.violation_f1)} |",
        f"| Preference coverage | {_pct(summary.preference_coverage)} |",
        f"| Group fairness (Jain index) | {_pct(summary.group_fairness)} |",
        f"| Citation correctness | {_pct(summary.citation_correctness)} |",
        f"| Citation freshness | {_pct(summary.citation_freshness)} |",
        f"| Dynamic affected-item recall | {_pct(summary.affected_item_recall)} |",
        f"| Original-plan preservation | {_pct(summary.plan_preservation)} |",
        f"| Fault degradation success | {_pct(summary.degradation_success_rate)} |",
        f"| Mean local evaluation latency | {summary.average_latency_ms:.2f} ms |",
    ]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"
