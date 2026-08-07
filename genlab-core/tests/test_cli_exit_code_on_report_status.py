"""Pin the pipeline CLI exit-code contract.

Two-generation history:

Gen 1 (2026-07-17): `_exit_code_for_ctx` returned 2 when the run report
status was "failed" — designed to escalate the sports 2-day video_gate
silent-fail via systemd OnFailure alerts.

Gen 2 (2026-08-07 QB-FIX-12): rule #26 supersedes. `status="failed"` in
run_report is EXCLUSIVELY set on zero-blueprints outcomes (data-side —
fetchers had nothing, VideoGate dropped all clips, push rejected all
thin-context stories). Per CLAUDE.md rule #26, data-side signals must
NOT fire systemd_unit_failed CRITICAL because that alarm cascade drowns
real infra signals.

The operator learns about zero-blueprints via:
* run_report's SLO VIOLATION log line (WARNING level, visible in journal)
* Mission Control's `bottleneck_stage` badge (dashboard, per PR #504)
* NOT via a systemd exit-code alarm

Only `ctx.is_aborted=True` (a stage raised an exception → real code
failure) returns non-zero now. This is a sibling of CLAUDE.md rule #26
— exit non-zero only on genuine infrastructure failure, never on
data-side outcomes.
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


def test_report_failed_returns_0_per_rule_26() -> None:
    """QB-FIX-12 (2026-08-07): status=failed is DATA-side (zero blueprints —
    no fetchable clips, empty sourcing pool). Per CLAUDE.md rule #26, this
    must NOT trigger systemd OnFailure. Operator sees the signal via
    run_report SLO VIOLATION warning + Mission Control bottleneck_stage
    badge, not via a systemd exit-code alarm.

    Regression scenario: someone re-adds `return 2` on status=failed
    thinking it's silent-fail elevation. It isn't — the signal IS surfaced,
    just not via systemd cascade. Adding exit=2 back would resurrect the
    movies-pipeline daily CRITICAL that this fix silenced.

    If a future refactor adds a NEW status value (e.g. "failed_infra")
    for genuine code failures, that status should return non-zero.
    """
    from genlab_core.pipeline.cli import _exit_code_for_ctx

    ctx = _make_ctx(is_aborted=False, report_status="failed")
    assert _exit_code_for_ctx(ctx) == 0, (
        "status=failed is data-side (zero blueprints from empty sourcing). "
        "Rule #26 says exit 0 unless a genuine incident. Operator sees "
        "SLO VIOLATION warning + bottleneck badge, not systemd alarm."
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
