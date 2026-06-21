"""Pin: drift detector catches arm regressions (Lever L).

Pre-2026-06-21 the bandit had no concept of "this arm was working
last month but stopped working this week". A hook style that scored
0.7 reward could decay to 0.2 over weeks (algorithm change, content
saturation, audience shift) and the bandit kept exploiting it for
days because the LIFETIME alpha/beta barely moved.

Lever L reads the daily snapshot CSVs (already produced by
``scripts/bandit_arms_snapshot.sh``) and computes a z-score of
recent (7-day) vs baseline (30-day) reward rates. Significant
regressions (|z| > 2.0) become DriftSignals operators can act on.

These pins lock in:

  Math (5):
  1. Cold-start (<2 snapshots) → None
  2. Insufficient obs (<10 in either window) → None
  3. Stable arm (recent ≈ baseline) → |z| small, not significant
  4. Regressed arm (recent << baseline) → negative z, is_regression=True
  5. Improved arm (recent >> baseline) → positive z, is_regression=False

  I/O (3):
  6. load_snapshots returns empty on missing dir
  7. load_snapshots parses real CSV format + keys by (niche, arm)
  8. detect_all_drift sorts results ascending by z (worst first)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _snapshot(alpha: float, beta: float, n_plays: int = 100) -> dict:
    """Build one synthetic snapshot row."""
    return {"alpha": alpha, "beta": beta, "n_plays": n_plays}


class TestComputeArmDriftMath:
    def test_cold_start_returns_none(self):
        from genlab_core.learning.drift_detector import compute_arm_drift

        # Only one snapshot — nothing to compare
        result = compute_arm_drift("arm_x", "gaming", [_snapshot(2.0, 1.0)])
        assert result is None

    def test_insufficient_obs_returns_none(self):
        """Both windows must have ≥10 obs for the binomial variance
        approximation to be meaningful."""
        from genlab_core.learning.drift_detector import compute_arm_drift

        # 8 days of snapshots, but only 5 total observations across the
        # 7-day window → below floor.
        snapshots = [
            _snapshot(1.0, 1.0),  # day 0
            _snapshot(1.5, 1.0),  # day 1
            _snapshot(2.0, 1.5),  # day 2
            _snapshot(2.5, 2.0),  # day 3
            _snapshot(3.0, 2.5),  # day 4
            _snapshot(3.0, 2.5),  # day 5
            _snapshot(3.0, 2.5),  # day 6
            _snapshot(3.0, 2.5),  # day 7 (today)
        ]
        # 7-day delta: alpha+=2.0, beta+=1.5 → only 3.5 obs total
        result = compute_arm_drift("arm_x", "gaming", snapshots)
        assert result is None

    def test_stable_arm_low_z_score(self):
        """Arm with same recent vs baseline win rate → |z| small.

        Per-day growth scaled to 10× so the 7-day window clears the
        _MIN_OBS_FOR_DRIFT=10 floor (7 days × ~10 obs/day = 70 obs).
        """
        from genlab_core.learning.drift_detector import compute_arm_drift

        # 30 days of stable 0.7 win rate, ~10 obs/day so windows clear floor
        snapshots = []
        for day in range(31):
            alpha = 1.0 + 7.0 * day
            beta = 1.0 + 3.0 * day
            snapshots.append(_snapshot(alpha, beta, n_plays=day * 10))

        result = compute_arm_drift("stable_arm", "gaming", snapshots)
        assert result is not None, "Expected drift signal with 70+ obs/window"
        # Recent 7-day rate ≈ baseline 30-day rate ≈ 0.7
        assert abs(result.recent_rate - 0.7) < 0.05
        assert abs(result.baseline_rate - 0.7) < 0.05
        # z-score should be small (close to 0)
        assert abs(result.z_score) < 1.0, f"Expected low z for stable arm; got z={result.z_score}"
        assert not result.is_significant
        assert not result.is_regression

    def test_regressed_arm_high_negative_z(self):
        """Arm with recent rate << baseline → strongly negative z,
        is_regression=True. Per-day deltas scaled 10× so windows
        clear the _MIN_OBS_FOR_DRIFT=10 floor."""
        from genlab_core.learning.drift_detector import compute_arm_drift

        # 23 days at 0.8 win rate (~10 obs/day = 230 obs baseline)
        snapshots = []
        for day in range(23):
            alpha = 1.0 + 8.0 * day
            beta = 1.0 + 2.0 * day
            snapshots.append(_snapshot(alpha, beta, n_plays=day * 10))
        # Last 7 days: 0.1 win rate (~10 obs/day = 70 obs recent)
        last_alpha = snapshots[-1]["alpha"]
        last_beta = snapshots[-1]["beta"]
        for day in range(1, 8):
            snapshots.append(
                _snapshot(
                    last_alpha + 1.0 * day,
                    last_beta + 9.0 * day,
                    n_plays=220 + day * 10,
                )
            )

        result = compute_arm_drift("regressed_arm", "gaming", snapshots)
        assert result is not None, "Expected drift signal with 70+ obs/window"
        # z-score must be strongly negative
        assert result.z_score < -2.0, f"Expected z < -2 for regressed arm; got {result.z_score}"
        assert result.is_significant
        assert result.is_regression  # THE pin — operators need this signal

    def test_improved_arm_high_positive_z(self):
        """Arm with recent rate >> baseline → positive z, NOT a
        regression (informational only). Per-day deltas scaled 10×."""
        from genlab_core.learning.drift_detector import compute_arm_drift

        # 23 days at 0.2 win rate (~10 obs/day = 230 obs baseline)
        snapshots = []
        for day in range(23):
            alpha = 1.0 + 2.0 * day
            beta = 1.0 + 8.0 * day
            snapshots.append(_snapshot(alpha, beta, n_plays=day * 10))
        # Last 7 days: 0.9 win rate (~10 obs/day = 70 obs recent)
        last_alpha = snapshots[-1]["alpha"]
        last_beta = snapshots[-1]["beta"]
        for day in range(1, 8):
            snapshots.append(
                _snapshot(
                    last_alpha + 9.0 * day,
                    last_beta + 1.0 * day,
                    n_plays=220 + day * 10,
                )
            )

        result = compute_arm_drift("improved_arm", "gaming", snapshots)
        assert result is not None
        # z-score must be strongly positive
        assert result.z_score > 2.0
        assert result.is_significant
        assert not result.is_regression  # improving ≠ regression


class TestLoadSnapshotsIO:
    def test_load_snapshots_missing_dir_returns_empty(self, tmp_path):
        from genlab_core.learning.drift_detector import load_snapshots

        result = load_snapshots(tmp_path / "does-not-exist")
        assert result == {}

    def test_load_snapshots_parses_csv_and_keys_by_niche_arm(self, tmp_path):
        from genlab_core.learning.drift_detector import load_snapshots

        # Build 3 days of fake snapshots
        for offset in range(3):
            date = (datetime.now(UTC) - timedelta(days=2 - offset)).strftime("%Y%m%d")
            content = (
                "niche_id,arm_id,alpha,beta,n_plays\n"
                f"gaming,style:gaming:question,{2.0 + offset},{1.0 + offset},{10 + offset}\n"
                f"sports,style:sports:bold_claim,{3.0 + offset},{1.5 + offset},{15 + offset}\n"
            )
            (tmp_path / f"bandit_arms_{date}.csv").write_text(content)

        result = load_snapshots(tmp_path)

        # Two (niche, arm) keys present
        assert ("gaming", "style:gaming:question") in result
        assert ("sports", "style:sports:bold_claim") in result
        # 3 snapshots each, in chronological order
        gaming_history = result[("gaming", "style:gaming:question")]
        assert len(gaming_history) == 3
        # First (oldest) → alpha=2.0; last (today) → alpha=4.0
        assert float(gaming_history[0]["alpha"]) == 2.0
        assert float(gaming_history[-1]["alpha"]) == 4.0

    def test_load_snapshots_ignores_unrelated_files(self, tmp_path):
        from genlab_core.learning.drift_detector import load_snapshots

        # Junk files that should be ignored
        (tmp_path / "README.md").write_text("not a snapshot")
        (tmp_path / "bandit_arms_invalid_date.csv").write_text("garbage")
        # One valid snapshot
        date = datetime.now(UTC).strftime("%Y%m%d")
        (tmp_path / f"bandit_arms_{date}.csv").write_text(
            "niche_id,arm_id,alpha,beta,n_plays\ngaming,arm_x,2.0,1.0,5\n"
        )

        result = load_snapshots(tmp_path)
        assert ("gaming", "arm_x") in result


class TestDetectAllDrift:
    def test_returns_empty_on_no_snapshots(self, tmp_path):
        from genlab_core.learning.drift_detector import detect_all_drift

        result = detect_all_drift(snapshot_dir=tmp_path)
        assert result == []

    def test_sorts_signals_by_z_score_ascending(self, tmp_path):
        """Worst-regressed arms first — operators see what needs
        attention without scrolling."""
        from genlab_core.learning.drift_detector import detect_all_drift

        # Build 30 days of snapshots with 2 arms — one stable, one regressed.
        # Per-day deltas scaled 10× so windows clear _MIN_OBS_FOR_DRIFT=10.
        # The regressed arm should appear FIRST in the result list.
        for offset in range(31):
            date = (datetime.now(UTC) - timedelta(days=30 - offset)).strftime("%Y%m%d")
            if offset < 23:
                # First 23 days: both arms win at 0.7 rate (10 obs/day)
                regressed_alpha = 1.0 + 7.0 * offset
                regressed_beta = 1.0 + 3.0 * offset
            else:
                # Last 7 days: regressed arm wins at 0.1 rate (10 obs/day)
                base_alpha = 1.0 + 7.0 * 22
                base_beta = 1.0 + 3.0 * 22
                regressed_alpha = base_alpha + 1.0 * (offset - 22)
                regressed_beta = base_beta + 9.0 * (offset - 22)
            stable_alpha = 1.0 + 7.0 * offset
            stable_beta = 1.0 + 3.0 * offset

            content = (
                "niche_id,arm_id,alpha,beta,n_plays\n"
                f"gaming,regressed_arm,{regressed_alpha},{regressed_beta},{offset * 10}\n"
                f"gaming,stable_arm,{stable_alpha},{stable_beta},{offset * 10}\n"
            )
            (tmp_path / f"bandit_arms_{date}.csv").write_text(content)

        signals = detect_all_drift(snapshot_dir=tmp_path)
        # Only the regressed arm should clear |z|>=2.0
        regressed_signals = [s for s in signals if s.arm_id == "regressed_arm"]
        assert len(regressed_signals) >= 1, (
            f"Expected regressed_arm in signals; got arms: {[s.arm_id for s in signals]}"
        )
        # First entry (lowest z) should be the regressed arm
        assert signals[0].arm_id == "regressed_arm"
        assert signals[0].z_score < -2.0


class TestCLIEntry:
    """Smoke test the CLI argparse — ensures the module is invokable
    via ``python -m`` without import errors."""

    def test_cli_module_imports(self):
        # Just import the module to verify the `if __name__ == "__main__"`
        # block doesn't have syntax errors
        from genlab_core.learning import drift_detector

        # Verify the public surface
        assert hasattr(drift_detector, "compute_arm_drift")
        assert hasattr(drift_detector, "load_snapshots")
        assert hasattr(drift_detector, "detect_all_drift")
        assert hasattr(drift_detector, "DriftSignal")
