"""Tests for Session 3b — anticipation accuracy measurement.

Coverage:
- Feature-flag semantics (exact-true)
- ``spearman_rank_correlation`` correctness on canonical inputs
- ``measure_niche_accuracy`` behaviour:
    * flag off → empty measurement with reason
    * no artifact → empty measurement with reason
    * artifact with 1 topic → n_topics<2 → r=None
    * happy path with mocked peak-fetch
- Persistence round-trip + read_latest_measurement
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.intel import anticipation_accuracy as aa

# ── Feature flag ────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", raising=False)
        assert not aa._integration_enabled()

    @pytest.mark.parametrize("val", ["true", "TRUE", "True"])
    def test_exact_true_activates(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", val)
        assert aa._integration_enabled()

    @pytest.mark.parametrize("val", ["1", "yes", "on", "TRUE_", "", "false"])
    def test_non_exact_true_stays_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", val)
        assert not aa._integration_enabled()


# ── Spearman rank correlation ─────────────────────────────────────


class TestSpearman:
    def test_perfect_positive_correlation(self):
        """Identical rank order → r = +1."""
        pred = [1.0, 2.0, 3.0, 4.0, 5.0]
        obs = [10.0, 20.0, 30.0, 40.0, 50.0]
        r, _p = aa.spearman_rank_correlation(pred, obs)
        assert r == pytest.approx(1.0)

    def test_perfect_negative_correlation(self):
        """Reverse-ranked → r = -1. If a real measurement returns
        this, the composite is scoring things BACKWARDS — the
        operator's action item is to invert or debug, not to
        activate steering."""
        pred = [1.0, 2.0, 3.0, 4.0, 5.0]
        obs = [50.0, 40.0, 30.0, 20.0, 10.0]
        r, _p = aa.spearman_rank_correlation(pred, obs)
        assert r == pytest.approx(-1.0)

    def test_zero_variance_input_returns_none(self):
        """Constant input has undefined rank correlation; return
        None rather than a NaN that dashboard would render as ''."""
        pred = [0.5] * 5
        obs = [0.1, 0.2, 0.3, 0.4, 0.5]
        r, p = aa.spearman_rank_correlation(pred, obs)
        assert r is None
        assert p is None

    def test_too_short_returns_none(self):
        r, _ = aa.spearman_rank_correlation([1.0], [2.0])
        assert r is None
        r, _ = aa.spearman_rank_correlation([], [])
        assert r is None

    def test_mismatched_lengths_returns_none(self):
        r, _ = aa.spearman_rank_correlation([1.0, 2.0], [1.0, 2.0, 3.0])
        assert r is None


# ── measure_niche_accuracy ────────────────────────────────────────


class TestMeasureNicheAccuracy:
    """Uses the injectable ``now`` + fabricated artifacts on tmp_path
    so we can test the runner without touching pytrends. The
    peak-fetch is monkeypatched to return deterministic values."""

    def _write_artifact(self, tmp_path, niche_id: str, when: datetime, rows: list):
        (tmp_path / "trend-anticipation").mkdir(parents=True, exist_ok=True)
        stamp = when.strftime("%Y%m%d")
        path = tmp_path / "trend-anticipation" / f"{stamp}-{niche_id}.json"
        path.write_text(
            json.dumps(
                {
                    "generated_at": when.isoformat(),
                    "niche_id": niche_id,
                    "flag_enabled": True,
                    "ranking": rows,
                }
            )
        )

    def test_flag_off_returns_empty_measurement(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        m = aa.measure_niche_accuracy("gaming")
        assert m.spearman_r is None
        assert m.n_topics == 0
        assert "not set to 'true'" in m.reasons[0]

    def test_missing_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", "true")
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        m = aa.measure_niche_accuracy("gaming")
        assert m.spearman_r is None
        assert "no artifact" in m.reasons[0]

    def test_happy_path_perfect_correlation(self, tmp_path, monkeypatch):
        """Predicted rank exactly matches peak rank → r=+1.
        The dashboard operator's ideal outcome."""
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", "true")
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        now = datetime(2026, 7, 8, 5, 0, tzinfo=UTC)
        artifact_dt = now - timedelta(days=7)

        # Write 5 topics with known composites; peak-fetch returns
        # perfectly-ranked values.
        rows = [{"topic": f"topic_{i}", "composite_score": 0.9 - i * 0.1} for i in range(5)]
        self._write_artifact(tmp_path, "gaming", artifact_dt, rows)

        # Peak-fetch mock: topic_0 → 100, topic_1 → 80, etc. —
        # same rank as composite.
        peak_map = {f"topic_{i}": 100.0 - i * 20 for i in range(5)}
        monkeypatch.setattr(
            aa,
            "_fetch_observed_peak",
            lambda topic, ws, we: peak_map.get(topic),
        )

        m = aa.measure_niche_accuracy("gaming", now=now)
        assert m.n_topics == 5
        assert m.spearman_r == pytest.approx(1.0)
        assert not m.is_underpowered

    def test_underpowered_flag_when_few_topics(self, tmp_path, monkeypatch):
        """Below the meaningful-N threshold, r is emitted but the
        dashboard should show it as "underpowered" rather than
        treating it as a signal to act on."""
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", "true")
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        now = datetime(2026, 7, 8, 5, 0, tzinfo=UTC)
        artifact_dt = now - timedelta(days=7)
        rows = [
            {"topic": f"topic_{i}", "composite_score": 0.9 - i * 0.1}
            for i in range(3)  # 3 < 5 (_MIN_TOPICS_FOR_MEANINGFUL_R)
        ]
        self._write_artifact(tmp_path, "gaming", artifact_dt, rows)
        monkeypatch.setattr(
            aa,
            "_fetch_observed_peak",
            lambda topic, ws, we: {
                "topic_0": 100.0,
                "topic_1": 60.0,
                "topic_2": 30.0,
            }.get(topic),
        )

        m = aa.measure_niche_accuracy("gaming", now=now)
        assert m.n_topics == 3
        assert m.spearman_r is not None
        assert m.is_underpowered
        assert any("underpowered" in r for r in m.reasons)

    def test_peak_fetch_failures_dropped_but_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_ANTICIPATION_ACCURACY_ENABLED", "true")
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        now = datetime(2026, 7, 8, 5, 0, tzinfo=UTC)
        artifact_dt = now - timedelta(days=7)
        rows = [{"topic": f"t{i}", "composite_score": 0.9 - i * 0.1} for i in range(5)]
        self._write_artifact(tmp_path, "gaming", artifact_dt, rows)

        # Two topics fail to fetch → dropped, reason recorded.
        monkeypatch.setattr(
            aa,
            "_fetch_observed_peak",
            lambda topic, ws, we: {
                "t0": 100.0,
                "t1": None,  # fetch failed
                "t2": 60.0,
                "t3": None,
                "t4": 20.0,
            }.get(topic),
        )

        m = aa.measure_niche_accuracy("gaming", now=now)
        assert m.n_topics == 3
        assert any("2 topics dropped" in r for r in m.reasons)


# ── Persistence round-trip ────────────────────────────────────────


class TestPersistence:
    def test_persist_and_read(self, tmp_path):
        m = aa.AccuracyMeasurement(
            niche_id="gaming",
            spearman_r=0.65,
            p_value=0.02,
            n_topics=6,
            artifact_date="20260701",
            measured_at="2026-07-08T05:00:00+00:00",
            reasons=[],
        )
        aa.persist_measurement(m, output_dir=tmp_path)
        got = aa.read_latest_measurement("gaming", output_dir=tmp_path)
        assert got is not None
        assert got.spearman_r == pytest.approx(0.65)
        assert got.n_topics == 6
        assert got.artifact_date == "20260701"

    def test_read_missing_returns_none(self, tmp_path):
        got = aa.read_latest_measurement("anime", output_dir=tmp_path)
        assert got is None
