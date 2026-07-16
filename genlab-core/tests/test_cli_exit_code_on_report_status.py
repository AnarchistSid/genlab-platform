"""Pin the pipeline CLI exit-code contract (session-2026-07-17, sports video_gate silent-fail).

Prior behaviour: `cli.py:440` returned `1 if ctx.is_aborted else 0`, so a
pipeline that completed all stages with `status=failed` in the run_report
(e.g. sports's 2-day video_gate silent-fail) exited 0. systemd saw a
successful run; no OnFailure alert fired; operator learned about the
outage from the Mission Control dashboard hours later.

Fix: `_exit_code_for_ctx(ctx)` inspects `ctx.run_stats["report"]["status"]`
and returns 2 (distinct from is_aborted's 1) when it reads "failed".

This is a sibling of CLAUDE.md rules #16/17/19 — silent-fail elevation
at a system boundary.
"""

from __future__ import annotations

from types import SimpleNamespace


def _make_ctx(*, is_aborted: bool = False, report_status: str | None = None):
    """Build a minimal ctx-shaped namespace for the exit-code fn."""
    run_stats: dict = {}
    if report_status is not None:
        run_stats["report"] = {"status": report_status}
    return SimpleNamespace(is_aborted=is_aborted, run_stats=run_stats)


def test_aborted_returns_1() -> None:
    """Hard-exception path preserves its historical exit code."""
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=True)
    assert _exit_code_for_ctx(ctx) == 1


def test_report_failed_returns_2() -> None:
    """The new signal: status=failed → non-zero exit → systemd OnFailure fires.

    Regression scenario: someone re-simplifies the CLI to `return 1 if
    ctx.is_aborted else 0` and the sports-style silent-fail returns.
    """
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=False, report_status="failed")
    assert _exit_code_for_ctx(ctx) == 2, (
        "status=failed must escape as non-zero exit so systemd OnFailure "
        "handlers can fire an alert. Was silent 2+ days on sports pipeline "
        "before the 2026-07-17 fix."
    )


def test_report_success_returns_0() -> None:
    """Happy path: clean success → 0."""
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=False, report_status="success")
    assert _exit_code_for_ctx(ctx) == 0


def test_report_partial_returns_0() -> None:
    """`partial` is a soft signal (blueprints WERE pushed, but sub-stages
    had non-zero failure counts). It's surfaced in stage_failures for the
    dashboard's QC banner; systemd shouldn't fire OnFailure on partial
    since the operator's downstream retry logic is designed around
    partial-success runs. Only `failed` (0 blueprints or hard SLO
    violation) escalates."""
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=False, report_status="partial")
    assert _exit_code_for_ctx(ctx) == 0


def test_no_report_returns_0() -> None:
    """RunReport stage may not have completed (pipeline crashed BEFORE the
    stage ran). In that case ``ctx.is_aborted`` should already be True and
    return 1; if it's False AND no report, treat as clean success (no
    signal to escalate on)."""
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=False, report_status=None)
    assert _exit_code_for_ctx(ctx) == 0


def test_aborted_takes_precedence_over_report_status() -> None:
    """If both signals fire, `is_aborted=True` wins with exit 1 (not 2).
    This preserves historical operator muscle-memory that "exit 1 = crash"."""
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=True, report_status="failed")
    assert _exit_code_for_ctx(ctx) == 1
