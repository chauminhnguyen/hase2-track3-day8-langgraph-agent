"""Report generation helper."""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def render_report_stub(metrics: MetricsReport) -> str:
    """Return a detailed report with metrics table and analysis."""
    rows = []
    for s in metrics.scenario_metrics:
        rows.append(
            f"| {s.scenario_id} | {s.expected_route} | {s.actual_route or 'N/A'} "
            f"| {'✅' if s.success else '❌'} | {s.retry_count} | {s.interrupt_count} |"
        )
    scenario_table = "\n".join(rows)

    failed = [s for s in metrics.scenario_metrics if not s.success]

    return f"""# Day 08 Lab Report

## Metrics summary

- Total scenarios: {metrics.total_scenarios}
- Success rate: {metrics.success_rate:.2%}
- Average nodes visited: {metrics.avg_nodes_visited:.2f}
- Total retries: {metrics.total_retries}
- Total interrupts: {metrics.total_interrupts}

## Scenario results

| Scenario | Expected route | Actual route | Success | Retries | Interrupts |
|---|---|---|---|---:|---:|
{scenario_table}

## Failure analysis

{f'{len(failed)} scenario(s) failed:' if failed else 'All scenarios passed.'}
{chr(10).join(f'- {s.scenario_id}: expected={s.expected_route}, actual={s.actual_route}, errors={s.errors}' for s in failed) if failed else ''}

## Architecture

See reports/lab_report.md for full details.
"""


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report_stub(metrics), encoding="utf-8")
