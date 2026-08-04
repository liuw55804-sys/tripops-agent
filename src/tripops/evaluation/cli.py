import argparse
import asyncio
from pathlib import Path

from tripops.evaluation.evaluator import EvaluationRunner
from tripops.evaluation.faults import middleware_fault_probe
from tripops.evaluation.reporting import write_report
from tripops.evaluation.suite import build_travelplanner_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic TripOps benchmark")
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("evals/results/baseline.json"),
        help="JSON result path",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("docs/evaluation-report.md"),
        help="Markdown report path",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    report = await EvaluationRunner(fault_probe=middleware_fault_probe).run(
        build_travelplanner_suite()
    )
    write_report(report, json_path=args.json, markdown_path=args.markdown)
    print(  # noqa: T201 - CLI result
        f"evaluated {report.summary.case_count} cases; "
        f"violation_f1={report.summary.violation_f1:.3f}; "
        f"degradation={report.summary.degradation_success_rate:.3f}"
    )


def main() -> None:
    asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    main()
