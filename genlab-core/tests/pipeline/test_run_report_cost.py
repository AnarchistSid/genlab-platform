"""R-27: ``RunReport`` stage stamps the active cost accumulator's
summary into ``run_report.json`` and turns budget-overruns into SLO
violations.

Before R-27 the audit found ``cost_accumulator`` was structurally
dead end-to-end (``set_accumulator()`` never called, RunReport never
read it, dashboard cost fields were empty). The fix is two-sided:
the runner installs the accumulator (pinned in
``test_generic_pipeline_runner.py::test_r27_runner_installs_cost_accumulator...``),
and the RunReport stage reads + serializes it (pinned here).
"""

from __future__ import annotations

import json
from pathlib import Path

from genlab_core.intelligence.cost_accumulator import (
    CostAccumulator,
    reset_accumulator,
    set_accumulator,
)
from genlab_core.pipeline.stages.run_report import RunReport


def _make_ctx(tmp_path: Path) -> dict:
    """Build the minimal context RunReport expects."""
    run_dir = tmp_path / "test_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": "test_run",
        "niche_id": "gaming",
        "run_dir": str(run_dir),
        "niche_config": {},
        "stories": [{"story_id": "s1"}],
        "run_stats": {
            "backlog_push": {"blueprints_pushed": 1},
            "_stage_timings": {"StageA": 1.0},
        },
    }


def _report(ctx: dict) -> dict:
    """Execute the stage and read back the JSON it wrote."""
    RunReport().execute(ctx)
    return json.loads(Path(ctx["run_stats"]["report_path"]).read_text())


def test_r27_run_report_carries_cost_when_accumulator_active(tmp_path) -> None:
    """With an accumulator installed, the JSON report's ``cost`` key
    carries the per-run summary."""
    acc = CostAccumulator(run_id="test_run")
    acc.record_llm("claude-haiku-4-5-20251001", 1000, 250)
    token = set_accumulator(acc)
    try:
        report = _report(_make_ctx(tmp_path))
    finally:
        reset_accumulator(token)

    cost = report.get("cost")
    assert cost, "R-27 regression: ``cost`` key missing from run_report.json"
    assert cost["total_usd"] > 0
    assert cost["entry_count"] == 1
    assert "llm" in cost["by_category"]


def test_r27_run_report_cost_is_empty_dict_when_no_accumulator(tmp_path) -> None:
    """If no accumulator is installed (a standalone test invocation
    or a future entry point that hasn't been wired) the ``cost`` key
    is an empty dict — distinct from ``total_usd=0.0`` which means
    "accumulator active, no LLM calls made". The shape distinction
    matters for ops dashboards."""
    report = _report(_make_ctx(tmp_path))
    assert report.get("cost") == {}, (
        "R-27: with no accumulator, ``cost`` must be {} (not absent, "
        "not None) so JSON consumers know to skip the section vs. "
        "show $0.00 spent."
    )


def test_r27_run_report_budget_overrun_appended_to_slo_violations(tmp_path) -> None:
    """A run that exceeds the daily $5 LLM budget must surface a SLO
    violation so ops sees the overrun in the existing slo_violations
    pipeline, not buried inside a cost subsection."""
    acc = CostAccumulator(run_id="test_run")
    # Cheat the accumulator straight to a number above budget — the
    # math-from-tokens path is already tested in test_cost_accumulator.py.
    from genlab_core.intelligence.cost_accumulator import CostEntry

    acc.entries.append(CostEntry(category="llm", model="claude-haiku-4-5-20251001", cost_usd=7.50))
    token = set_accumulator(acc)
    try:
        report = _report(_make_ctx(tmp_path))
    finally:
        reset_accumulator(token)

    violations = report.get("slo_violations", [])
    assert any("LLM cost" in v and "budget" in v for v in violations), (
        f"R-27 regression: budget-overrun didn't reach slo_violations. Got: {violations!r}"
    )


def test_r27_run_report_budget_respects_niche_override(tmp_path) -> None:
    """``error_budgets.daily_cost_usd`` in niche.yaml must override the
    $5 default — niches with paid Sonnet/Opus calls may legitimately
    sit above the default and shouldn't flag every run."""
    ctx = _make_ctx(tmp_path)
    ctx["niche_config"] = {"error_budgets": {"daily_cost_usd": 20.0}}

    acc = CostAccumulator(run_id="test_run")
    from genlab_core.intelligence.cost_accumulator import CostEntry

    # Sits above the default $5 but under the niche's $20 — no violation.
    acc.entries.append(CostEntry(category="llm", model="claude-sonnet-4-6", cost_usd=10.0))
    token = set_accumulator(acc)
    try:
        report = _report(ctx)
    finally:
        reset_accumulator(token)

    cost_violations = [v for v in report["slo_violations"] if "LLM cost" in v]
    assert not cost_violations, (
        "R-27 regression: niche-level ``error_budgets.daily_cost_usd`` "
        "override is being ignored — the default $5 cap fired anyway."
    )
