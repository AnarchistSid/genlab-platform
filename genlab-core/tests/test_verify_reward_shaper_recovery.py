"""Pin tests for verify_reward_shaper_recovery.py classification logic (#586).

The DB-query side is exercised in real-DB integration in
``test-storage`` CI job when the check runs against the actual
``pending_feedback`` table. This file locks in the pure classification
logic — verdict = green/amber/red/sparse — which is where a
subtle-but-important regression could sneak in.

Live-fire baselines from the audit's 2026-07-08 evening query:
YouTube 22% positive (18 samples), Facebook 19% (96 samples). If
someone tweaks these baselines without updating this test, we want
to know before shipping.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Import the script directly (it lives in scripts/, not the package).
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_recovery = importlib.import_module("verify_reward_shaper_recovery")

PlatformStatus = _recovery.PlatformStatus
RecoveryReport = _recovery.RecoveryReport
_classify_platform = _recovery._classify_platform
_BASELINE = _recovery._BASELINE_POSITIVE_RATES
_TARGETS = _recovery._RECOVERY_TARGETS


def _mk_status(platform: str, n: int, pos: int) -> PlatformStatus:
    """Build a PlatformStatus with the baseline/target dicts wired."""
    return PlatformStatus(
        platform=platform,
        sample_count=n,
        positive_count=pos,
        median_reward=0.0,
        p90_reward=0.0,
        max_reward=0.0,
        baseline_positive_rate=_BASELINE.get(platform, 0.0),
        target_positive_rate=_TARGETS.get(platform, 0.5),
    )


class TestClassifyPlatform:
    """The interpretation matrix: sparse | red | green | amber."""

    def test_below_min_samples_is_sparse(self):
        # 4 samples is below _MIN_SAMPLE_COUNT=5 → sparse regardless of ratio.
        s = _mk_status("youtube", n=4, pos=4)
        _classify_platform(s)
        assert s.verdict == "sparse"
        assert "waiting" in s.reason.lower() or "sample" in s.reason.lower()

    def test_zero_samples_is_sparse(self):
        s = _mk_status("youtube", n=0, pos=0)
        _classify_platform(s)
        assert s.verdict == "sparse"

    def test_regression_below_baseline_is_red(self):
        # YouTube baseline is 0.22. Post-deploy 0.10 = below by 0.12 > margin 0.05.
        # Regression → red.
        s = _mk_status("youtube", n=20, pos=2)  # 10% positive
        _classify_platform(s)
        assert s.verdict == "red"
        assert "baseline" in s.reason.lower()
        assert "0.10" in s.reason or "0.1" in s.reason

    def test_at_target_is_green(self):
        # YouTube target is 0.50. Post-deploy 0.55 = at/above target → green.
        s = _mk_status("youtube", n=20, pos=11)  # 55% positive
        _classify_platform(s)
        assert s.verdict == "green"
        assert "target" in s.reason.lower()

    def test_above_baseline_below_target_is_amber(self):
        # YouTube baseline 0.22, target 0.50. Post-deploy 0.35 → recovering.
        s = _mk_status("youtube", n=20, pos=7)  # 35% positive
        _classify_platform(s)
        assert s.verdict == "amber"
        assert "recovering" in s.reason.lower()

    def test_exactly_at_baseline_is_amber(self):
        # Facebook baseline 0.19 (from live-fire), target 0.30. 0.19 exactly
        # is NOT below baseline+margin (0.14), and NOT above target (0.30).
        s = _mk_status("facebook", n=100, pos=19)  # exactly 19%
        _classify_platform(s)
        assert s.verdict == "amber"

    def test_slight_dip_within_margin_is_not_regression(self):
        # Instagram baseline 0.81, target 0.75, margin 0.05. Post-deploy 0.77
        # is above target → green (dip within margin, but target still met).
        s = _mk_status("instagram", n=100, pos=77)
        _classify_platform(s)
        assert s.verdict == "green"

    def test_dip_below_baseline_minus_margin_is_red(self):
        # Instagram baseline 0.81, margin 0.05 → threshold 0.76. Below = red.
        s = _mk_status("instagram", n=100, pos=70)  # 70% → below threshold
        _classify_platform(s)
        assert s.verdict == "red"


class TestOverallVerdict:
    """Aggregation: red > amber > green > sparse. Anything red = red."""

    def test_all_green_is_green(self):
        report = RecoveryReport(window_days=7)
        for plat in ("youtube", "twitter", "facebook", "instagram", "threads"):
            s = _mk_status(plat, n=20, pos=20)  # 100% positive → green
            _classify_platform(s)
            report.platforms.append(s)
        assert report.overall_verdict() == "green"

    def test_one_red_makes_overall_red(self):
        report = RecoveryReport(window_days=7)
        # 4 green
        for plat in ("twitter", "facebook", "instagram", "threads"):
            s = _mk_status(plat, n=20, pos=20)
            _classify_platform(s)
            report.platforms.append(s)
        # 1 red — YouTube post-deploy positive_rate below baseline by > margin
        s = _mk_status("youtube", n=20, pos=2)  # 10%, baseline 22%
        _classify_platform(s)
        report.platforms.append(s)
        assert report.overall_verdict() == "red"

    def test_amber_without_red_is_amber(self):
        report = RecoveryReport(window_days=7)
        # 4 green + 1 amber (YouTube recovering but not yet at target)
        for plat in ("twitter", "facebook", "instagram", "threads"):
            s = _mk_status(plat, n=20, pos=20)
            _classify_platform(s)
            report.platforms.append(s)
        s = _mk_status("youtube", n=20, pos=7)  # 35%, between baseline+margin and target
        _classify_platform(s)
        report.platforms.append(s)
        assert report.overall_verdict() == "amber"

    def test_sparse_and_green_is_green(self):
        """A sparse platform doesn't downgrade the overall verdict.
        Missing data is informational, not a failure."""
        report = RecoveryReport(window_days=7)
        for plat in ("twitter", "facebook", "instagram", "youtube"):
            s = _mk_status(plat, n=20, pos=20)
            _classify_platform(s)
            report.platforms.append(s)
        s = _mk_status("threads", n=2, pos=2)  # only 2 samples → sparse
        _classify_platform(s)
        report.platforms.append(s)
        assert report.overall_verdict() == "green"


class TestExitCodes:
    """Systemd-friendly exit codes so runner alerts fire on regression
    but not on 'still recovering' (which is normal)."""

    def test_green_exits_0(self):
        report = RecoveryReport(window_days=7)
        s = _mk_status("youtube", n=20, pos=20)
        _classify_platform(s)
        report.platforms.append(s)
        assert _recovery._exit_code_for(report) == 0

    def test_amber_exits_4_not_failure(self):
        """Amber (recovering) is 4 — SuccessExitStatus in the systemd
        unit includes 4, so this is NOT a failure. Prevents alarm noise
        during the natural 1-2 week recovery window."""
        report = RecoveryReport(window_days=7)
        s = _mk_status("youtube", n=20, pos=7)  # 35% recovering
        _classify_platform(s)
        report.platforms.append(s)
        assert _recovery._exit_code_for(report) == 4

    def test_red_exits_3_fires_alert(self):
        """Red = regression = exit 3. NOT in SuccessExitStatus so systemd
        marks unit failed → OnFailure alert fires → CriticalAlertsBanner
        surfaces to operator."""
        report = RecoveryReport(window_days=7)
        s = _mk_status("youtube", n=20, pos=2)  # 10% below baseline
        _classify_platform(s)
        report.platforms.append(s)
        assert _recovery._exit_code_for(report) == 3


class TestBaselineConstants:
    """Lock in the pre-deploy baseline values so a future editor can't
    silently shift them and mask a regression. If baselines need to
    update (new deploy, new prod state), update THIS test too — makes
    the change visible in code review."""

    def test_youtube_baseline_is_from_audit_query(self):
        # From audit-round-3-2026-07-08.md: YouTube 4/18 positive = 22%.
        assert _BASELINE["youtube"] == pytest.approx(0.22, abs=0.01)

    def test_youtube_target_is_headline_recovery_number(self):
        # The primary success signal for #586 validation is YouTube climbing
        # from 22% → 50%+. Anything lower undersells the recovery.
        assert _TARGETS["youtube"] >= 0.50

    def test_facebook_baseline_matches_audit_query(self):
        # From audit: Facebook 18/96 positive = 19%.
        assert _BASELINE["facebook"] == pytest.approx(0.19, abs=0.01)

    def test_instagram_baseline_matches_audit_query(self):
        # From audit: Instagram 17/21 positive = 81%.
        assert _BASELINE["instagram"] == pytest.approx(0.81, abs=0.01)
