"""Shared health-check helper for scheduled Intervention runners.

Every runner invoked by a systemd timer should express its health as
an EXIT CODE, not just a completion signal. Without that, a runner
can process N items, error on 100% of them, exit 0, and let systemd
report SUCCESS. The Mission Control alert path is wired via
``OnFailure=systemd-service-failure-alert@%n.service`` on every
genlab-*.service, so nonzero exit is the only signal that reaches
the operator via the standard channel.

This helper encodes the pattern established in PR #656 (fix for the
7-week silent no-op of Intervention 1's late-reward runner — see
``[[late-reward-sql-bug-2026-07-02]]``) so every future runner can
adopt it in a single line.

## Usage

At the end of a runner's ``main()``:

    from genlab_core.runner_healthcheck import exit_code_from_health

    counters = process_stuff()  # returns dict-like with total/errors
    return exit_code_from_health(
        total=counters["scanned"],
        errors=counters["errors"],
    )

## When NOT to use this

Single-shot compute runners (no per-item loop) — this helper's
"error rate over N items" model doesn't apply. Examples that stay
DIY health-check territory:

  * ``run_counterfactual_replay.py`` — computes one report per
    invocation; health is "does the report have arms?"
  * ``refit_cross_niche_priors.py`` — computes one priors dict per
    invocation; health is "did any style get a prior?"

Those two are candidates for a separate ``exit_code_from_artifact``
helper (not shipped in this module — waiting for a second concrete
site to reveal the right shape).
"""

from __future__ import annotations

# Standard defaults. Runners can override any of them per-call. These
# match the boundary chosen for recompute_late_rewards.py in PR #656
# and are documented there.
DEFAULT_MIN_TOTAL_FOR_CHECK = 3
DEFAULT_MAX_ERROR_RATE = 0.5
DEFAULT_HIGH_ERROR_EXIT_CODE = 2


def exit_code_from_health(
    total: int,
    errors: int,
    *,
    min_total_for_check: int = DEFAULT_MIN_TOTAL_FOR_CHECK,
    max_error_rate: float = DEFAULT_MAX_ERROR_RATE,
    high_error_exit_code: int = DEFAULT_HIGH_ERROR_EXIT_CODE,
) -> int:
    """Return an exit code based on runner health signals.

    Contract:

      * Returns ``0`` (healthy) when EITHER:
          - ``total < min_total_for_check`` (sample too small to be
            meaningfully "systemic")
          - OR ``errors / total <= max_error_rate``

      * Returns ``high_error_exit_code`` when:
          - ``total >= min_total_for_check`` AND
          - ``errors / total > max_error_rate``

    Args:
        total: Number of items the runner attempted to process.
            0 always returns 0 (empty work window is legit).
        errors: Number of items that hit an error path.
        min_total_for_check: Below this ``total``, always return 0.
            Avoids flapping on tiny samples (a day with 1-2 items
            both erroring may be transient, not systemic).
        max_error_rate: Threshold above which the runner is
            considered unhealthy. Default 0.5 — at 50%+ errors, the
            runner isn't producing useful telemetry.
        high_error_exit_code: The value returned when unhealthy.
            Default 2, distinct from POSIX 1 (which most runners
            reserve for pre-flight failures like missing env vars).

    Examples:
        >>> exit_code_from_health(0, 0)  # empty window
        0
        >>> exit_code_from_health(10, 1)  # 10% error rate
        0
        >>> exit_code_from_health(10, 6)  # 60% error rate
        2
        >>> exit_code_from_health(2, 2)  # tiny sample, ignored
        0
    """
    if total < min_total_for_check:
        return 0
    if total == 0:  # defence-in-depth against min_total_for_check <= 0
        return 0
    if errors / total > max_error_rate:
        return high_error_exit_code
    return 0
