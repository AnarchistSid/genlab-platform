#!/usr/bin/env python3
"""Verify per-platform reward-shaper recovery post-#713 deploy (#586).

Round-3 audit found a 37× disparity in per-platform reward scales
(Facebook max 0.671, YouTube max 0.018) and hypothesized this was a
reward_shaper bug. Round-4 read the code inline and vindicated the
shaper: the disparity is caused by a **feedback death spiral** where
the YouTube reward fetcher was silently dropping ~82% of writes
(unwrapped ``raise_for_status()`` in ``metrics/youtube.py``). Since
percentile-relative calibration in reward_shaper depends on having
enough historical observations to compute a 70th percentile, missing
reward data → uncalibrated percentile → falls back to hardcoded
targets → YouTube reward stays small → bandit picks non-YT → less YT
data → spiral.

PR #713 (deployed 2026-07-08 14:41 UTC) breaks the death spiral by
wrapping the fetcher's ``raise_for_status()`` in try/except. Expected
recovery trajectory:

* Days 1-3 post-deploy: YouTube reward_48h NULL rate should drop from
  ~82% toward ~10% as new fetches complete successfully.
* Days 3-14: Percentile-relative calibration in ``_normalise_with_
  percentile`` starts firing (needs ≥20 samples per niche×platform×
  metric per the docstring). YouTube reward SCALE should climb.
* Day 14+: Per-platform max reward should converge toward parity
  (still expect some legitimate variance, but not 37×).

This script runs the check + emits per-platform status. Runs weekly
via ``genlab-reward-shaper-recovery.timer``. Exit code 0 = all
platforms recovering as expected; exit 3 = regression detected on
one or more platforms.

Pre-deploy baseline (from prod query 2026-07-08 evening):

  Platform    n    median  p90     max     positive
  facebook    96   0.000   0.327   0.671   18/96 (19%)
  instagram   21   0.044   0.089   0.133   17/21 (81%)
  threads     4    0.000   0.093   0.133   2/4  (50%)
  twitter     4    0.010   0.026   0.030   4/4  (100%)
  youtube     18   0.000   0.011   0.018   4/18 (22%)

Post-recovery target (2 weeks post-deploy):

  * YouTube positive-rate ≥ 50% (from 22% pre)
  * Twitter positive-rate maintained (already high)
  * Per-platform max within 5× of each other (was 37× pre)
  * All platforms show ≥40 samples in the recovery window (data
    volume returning)

Usage
-----

::

    # Default: compare last 7 days vs pre-deploy baseline
    uv run --package genlab-core python3 \\
        scripts/verify_reward_shaper_recovery.py

    # JSON output for the timer's runner_healthcheck helper
    uv run --package genlab-core python3 \\
        scripts/verify_reward_shaper_recovery.py --format json

    # Custom window
    uv run --package genlab-core python3 \\
        scripts/verify_reward_shaper_recovery.py --window-days 14

Exit codes
----------

* ``0`` — all platforms recovering or already-recovered per the
  post-recovery targets above
* ``2`` — argparse error
* ``3`` — regression detected on 1+ platforms (positive-rate dropped
  below pre-deploy baseline, OR sample-count-in-window is zero on a
  platform that had samples pre-deploy)
* ``4`` — recovery in progress but not yet complete (informational;
  systemd treats as success; operator sees the state on the runner
  health card)

References
----------

* ``session-2026-07-08-audit-shipping-arc.md`` — full deploy context
* ``audit-round-4-2026-07-08.md`` — vindication of reward_shaper +
  identification of the death spiral
* PR #713 — the reward-fetcher hardening that broke the spiral
* PR #717 — content_pool index (touched same session, unrelated)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

# Deploy timestamp — the exact moment PR #713 (reward-fetcher
# exception hardening) landed on prod. Any pending_feedback row with
# ``created_at >= _DEPLOY_TIMESTAMP`` is a post-fix observation.
_DEPLOY_TIMESTAMP = "2026-07-08 14:41:00 UTC"

# Pre-deploy baseline positive-rates per platform (from the audit's
# 2026-07-08 evening query). Regression = post-deploy positive-rate
# falls below the baseline by more than the ``_REGRESSION_MARGIN``.
_BASELINE_POSITIVE_RATES: dict[str, float] = {
    "facebook": 0.19,  # 18/96
    "instagram": 0.81,  # 17/21
    "threads": 0.50,  # 2/4
    "twitter": 1.00,  # 4/4
    "youtube": 0.22,  # 4/18
}

# Post-recovery targets — reached these = green.
_RECOVERY_TARGETS: dict[str, float] = {
    "facebook": 0.30,  # up from 0.19 — some improvement expected
    "instagram": 0.75,  # near-parity with baseline
    "threads": 0.50,  # maintained
    "twitter": 0.75,  # some retention loss OK; still healthy
    "youtube": 0.50,  # THE headline target (from 0.22 → 0.50+)
}

# If post-deploy positive-rate is below the baseline by more than this
# margin, flag as regression. Small margin because the death spiral
# was chronic — post-fix rates should only improve.
_REGRESSION_MARGIN = 0.05

# Minimum sample count in the window for the report to be considered
# statistically meaningful. Below this the platform is reported as
# ``sparse`` (informational, exit 0).
_MIN_SAMPLE_COUNT = 5


@dataclass
class PlatformStatus:
    """One row of the per-platform recovery report."""

    platform: str
    sample_count: int
    positive_count: int
    median_reward: float
    p90_reward: float
    max_reward: float
    baseline_positive_rate: float
    target_positive_rate: float

    verdict: str = "unknown"  # green | amber | red | sparse
    reason: str = ""

    @property
    def positive_rate(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.positive_count / self.sample_count

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "sample_count": self.sample_count,
            "positive_count": self.positive_count,
            "positive_rate": round(self.positive_rate, 3),
            "median_reward": round(self.median_reward, 4),
            "p90_reward": round(self.p90_reward, 4),
            "max_reward": round(self.max_reward, 4),
            "baseline_positive_rate": self.baseline_positive_rate,
            "target_positive_rate": self.target_positive_rate,
            "verdict": self.verdict,
            "reason": self.reason,
        }


@dataclass
class RecoveryReport:
    """Aggregate recovery status across all platforms."""

    window_days: int
    deploy_timestamp: str = _DEPLOY_TIMESTAMP
    platforms: list[PlatformStatus] = field(default_factory=list)

    def overall_verdict(self) -> str:
        """Overall verdict: red if any regressed; amber if any not
        yet recovered; green if all met target; sparse if too few
        samples across platforms to conclude."""
        verdicts = {p.verdict for p in self.platforms}
        if "red" in verdicts:
            return "red"
        if "amber" in verdicts:
            return "amber"
        if verdicts and verdicts.issubset({"green", "sparse"}):
            return "green"
        return "unknown"

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "deploy_timestamp": self.deploy_timestamp,
            "overall_verdict": self.overall_verdict(),
            "platforms": [p.to_dict() for p in self.platforms],
        }


def _classify_platform(status: PlatformStatus) -> None:
    """Set ``status.verdict`` + ``status.reason`` from the metrics.

    Rules (checked in order):

      0. platform is out of 4-platform focus (rule #23) AND has zero samples
         → ``sparse`` (was previously classified as ``red`` for Twitter,
         causing the systemd service to exit-3 every run despite Twitter
         being explicitly out-of-scope per 2026-07-17 operator directive)
      1. sample_count < _MIN_SAMPLE_COUNT → ``sparse``
      2. positive_rate < baseline - _REGRESSION_MARGIN → ``red``
      3. positive_rate >= target → ``green``
      4. else → ``amber`` (recovering, but not yet at target)
    """
    # Rule 0: out-of-scope platforms with zero samples are informational.
    # Prior behavior classified them as ``red`` because 0.0 positive-rate
    # trivially trips the regression check. CLAUDE.md rule #23 defines the
    # 4-platform focus (YT/FB/IG/Threads); Twitter+TikTok are explicitly
    # out of scope — a zero-sample Twitter row shouldn't trigger a systemd
    # exit-3 recovery-red alert. Applied 2026-07-21.
    _OUT_OF_SCOPE_PLATFORMS = frozenset({"twitter", "tiktok"})
    if status.platform in _OUT_OF_SCOPE_PLATFORMS and status.sample_count == 0:
        status.verdict = "sparse"
        status.reason = (
            f"out-of-scope per rule #23 (4-platform focus is YT/FB/IG/Threads); "
            f"zero samples in window is expected"
        )
        return

    if status.sample_count < _MIN_SAMPLE_COUNT:
        status.verdict = "sparse"
        status.reason = f"only {status.sample_count} sample(s) in window — waiting for more data"
        return

    if status.positive_rate < status.baseline_positive_rate - _REGRESSION_MARGIN:
        status.verdict = "red"
        status.reason = (
            f"positive-rate {status.positive_rate:.2f} dropped below "
            f"baseline {status.baseline_positive_rate:.2f} by more than "
            f"{_REGRESSION_MARGIN:.2f}"
        )
        return

    if status.positive_rate >= status.target_positive_rate:
        status.verdict = "green"
        status.reason = (
            f"positive-rate {status.positive_rate:.2f} met target {status.target_positive_rate:.2f}"
        )
        return

    status.verdict = "amber"
    status.reason = (
        f"positive-rate {status.positive_rate:.2f} above baseline "
        f"{status.baseline_positive_rate:.2f} but below target "
        f"{status.target_positive_rate:.2f} — recovering"
    )


def collect_recovery_report(
    dsn: str,
    window_days: int = 7,
) -> RecoveryReport:
    """Query the DB and classify each platform's recovery state.

    Returns a ``RecoveryReport`` where each platform is classified as
    green/amber/red/sparse. See ``_classify_platform`` for rules.
    """
    from genlab_core.storage.tenant_context import pg_connect

    report = RecoveryReport(window_days=window_days)

    with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    platform,
                    COUNT(*) AS n,
                    COUNT(*) FILTER (WHERE reward_48h > 0) AS positive_n,
                    COALESCE(
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY reward_48h),
                        0
                    )::float AS median_r,
                    COALESCE(
                        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY reward_48h),
                        0
                    )::float AS p90_r,
                    COALESCE(MAX(reward_48h), 0)::float AS max_r
                FROM pending_feedback
                WHERE reward_48h IS NOT NULL
                  AND created_at >= NOW() - (%s::int * INTERVAL '1 day')
                  AND created_at >= %s::timestamp
                GROUP BY platform
                ORDER BY platform
                """,
                (window_days, _DEPLOY_TIMESTAMP),
            )
            rows = cur.fetchall()

    for platform, n, positive_n, median_r, p90_r, max_r in rows:
        status = PlatformStatus(
            platform=platform,
            sample_count=int(n),
            positive_count=int(positive_n),
            median_reward=float(median_r),
            p90_reward=float(p90_r),
            max_reward=float(max_r),
            baseline_positive_rate=_BASELINE_POSITIVE_RATES.get(platform, 0.0),
            target_positive_rate=_RECOVERY_TARGETS.get(platform, 0.5),
        )
        _classify_platform(status)
        report.platforms.append(status)

    # Add empty entries for expected platforms not in results — a
    # missing platform is either "still zero data since deploy" (sparse)
    # or a regression if the baseline had samples.
    seen = {p.platform for p in report.platforms}
    for platform in _BASELINE_POSITIVE_RATES:
        if platform in seen:
            continue
        status = PlatformStatus(
            platform=platform,
            sample_count=0,
            positive_count=0,
            median_reward=0.0,
            p90_reward=0.0,
            max_reward=0.0,
            baseline_positive_rate=_BASELINE_POSITIVE_RATES[platform],
            target_positive_rate=_RECOVERY_TARGETS.get(platform, 0.5),
        )
        # Empty post-deploy AND had baseline samples → regression (fetch
        # broken again). Empty AND zero baseline → sparse.
        # 2026-07-21: honor rule #23 (4-platform focus) in the
        # empty-post-deploy branch too. Prior behavior classified
        # Twitter with historical baseline as ``red``, tripping the
        # systemd service to exit-3 every fire despite Twitter being
        # explicitly out-of-scope. Rule 0 in _classify_platform covers
        # the non-empty case; this branch is the missing symmetric
        # coverage for the zero-samples-with-baseline case.
        _OUT_OF_SCOPE_PLATFORMS = frozenset({"twitter", "tiktok"})
        if platform in _OUT_OF_SCOPE_PLATFORMS:
            status.verdict = "sparse"
            status.reason = (
                f"out-of-scope per rule #23 (4-platform focus is YT/FB/IG/Threads); "
                f"zero samples post-deploy is expected"
            )
        elif status.baseline_positive_rate > 0 and _BASELINE_POSITIVE_RATES[platform] > 0:
            # We know the platform had data pre-deploy from
            # the baseline dict — zero data post-deploy is a
            # regression (fetcher writes broken again).
            status.verdict = "red"
            status.reason = (
                "zero samples in window despite non-zero pre-deploy baseline — "
                "reward-fetcher may have regressed"
            )
        else:
            status.verdict = "sparse"
            status.reason = "zero samples in window"
        report.platforms.append(status)

    return report


def _format_text(report: RecoveryReport) -> str:
    """Human-friendly text output — matches verify_intelligent_transform.py
    formatting so operator's eye pattern-matches."""
    lines = []
    lines.append(
        f"Reward-shaper recovery — last {report.window_days} days "
        f"(post-deploy {report.deploy_timestamp})"
    )
    lines.append(
        f"  {'platform':<12} {'n':<6} {'pos%':<6} {'med':<8} {'p90':<8} {'max':<8} verdict"
    )
    for p in report.platforms:
        pos_pct = f"{p.positive_rate * 100:.0f}%"
        lines.append(
            f"  {p.platform:<12} {p.sample_count:<6} {pos_pct:<6} "
            f"{p.median_reward:<8.3f} {p.p90_reward:<8.3f} "
            f"{p.max_reward:<8.3f} {p.verdict}"
        )
        if p.verdict in ("red", "amber"):
            lines.append(f"    → {p.reason}")
    lines.append("")
    lines.append(f"Overall: {report.overall_verdict()}")
    return "\n".join(lines)


def _exit_code_for(report: RecoveryReport) -> int:
    """Systemd-friendly exit code.

    * 0 — green (all platforms recovering or recovered)
    * 3 — red (at least one platform regressed)
    * 4 — amber (still recovering; informational, not a failure)
    """
    verdict = report.overall_verdict()
    if verdict == "red":
        return 3
    if verdict == "amber":
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify per-platform reward-shaper recovery post-#713 deploy.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Days of history to query (default: 7)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print(
            "ERROR: DATABASE_URL not set — cannot query prod",
            file=sys.stderr,
        )
        return 2

    try:
        report = collect_recovery_report(dsn=dsn, window_days=args.window_days)
    except Exception as exc:
        print(
            f"ERROR: recovery report failed: {exc}",
            file=sys.stderr,
        )
        # DB errors → exit 3 so systemd surfaces the failure. Alternative
        # (exit 2) would say "check the script"; exit 3 says "check the
        # DB or the reward wire itself."
        return 3

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_format_text(report))

    return _exit_code_for(report)


if __name__ == "__main__":
    sys.exit(main())
