"""Pin the top-creator prior read + math module (B.3, 2026-07-08).

Contract:
  1. Flag off → ``load_correlations`` returns None even when the
     artifact exists (observation-off-by-default discipline)
  2. Flag on + missing dir → None
  3. Flag on + malformed JSON → None (fail-open)
  4. Flag on + today's artifact → correlations dict
  5. Flag on + yesterday's artifact (today missing) → correlations
  6. Flag on + 9-day-old artifact → None (beyond fallback window)
  7. ``correlation_to_prior_delta``:
     * r=0.5 → (positive delta_alpha, 0)
     * r=-0.5 → (0, positive delta_beta)
     * r=0 → (0, 0)
     * |r| > 1 → clamped to 1
     * weight=0 → (0, 0) regardless of r
     * delta bounded by ``MAX_DELTA``
  8. ``get_arm_prior_adjustment``:
     * Flag off → None
     * Feature not in map → None
     * Feature present, r=0 → (0, 0) informative zero
     * Feature present, r>0 → (positive, 0)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from genlab_core.learning import top_creator_priors as tcp


def _write_artifact(
    dir_: Path,
    niche_id: str,
    correlations: dict[str, float],
    days_old: int = 0,
) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(UTC) - timedelta(days=days_old)).strftime("%Y%m%d")
    path = dir_ / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": (datetime.now(UTC) - timedelta(days=days_old)).isoformat(),
        "niche_id": niche_id,
        "video_count": 24,
        "correlations": correlations,
    }
    path.write_text(json.dumps(payload))
    return path


class TestLoadCorrelationsFlag:
    def test_flag_off_returns_none_even_with_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.3},
        )
        assert tcp.load_correlations("gaming") is None

    def test_flag_only_matches_exact_true(self, monkeypatch, tmp_path):
        """``"1"`` / ``"yes"`` / ``"on"`` do NOT enable. Same rule
        as every intelligence-package flag."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.3},
        )
        for bad in ("1", "yes", "on", "true ", " true", ""):
            monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", bad)
            assert tcp.load_correlations("gaming") is None, bad


class TestLoadCorrelationsArtifact:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "true")

    def test_missing_dir_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert tcp.load_correlations("gaming") is None

    def test_todays_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.3, "title_ends_with_question": -0.15},
        )
        result = tcp.load_correlations("gaming")
        assert result == {"title_length_chars": 0.3, "title_ends_with_question": -0.15}

    def test_yesterdays_artifact_fallback(self, monkeypatch, tmp_path):
        """Weekly cadence means most days don't have today's artifact
        — fall back to the most recent within the 8-day window."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.2},
            days_old=1,
        )
        result = tcp.load_correlations("gaming")
        assert result == {"title_length_chars": 0.2}

    def test_beyond_fallback_window_returns_none(self, monkeypatch, tmp_path):
        """9+ day-old artifact is too stale to steer on."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.2},
            days_old=10,
        )
        assert tcp.load_correlations("gaming") is None

    def test_malformed_json_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-priors"
        dir_.mkdir(parents=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        (dir_ / f"{stamp}-gaming.json").write_text("not json {[")
        assert tcp.load_correlations("gaming") is None

    def test_missing_correlations_key_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-priors"
        dir_.mkdir(parents=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        (dir_ / f"{stamp}-gaming.json").write_text(json.dumps({"niche_id": "gaming"}))
        assert tcp.load_correlations("gaming") is None

    def test_non_numeric_correlation_dropped(self, monkeypatch, tmp_path):
        """Defensive: non-numeric values in the map are silently
        dropped; the rest survive."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"good": 0.5, "bad": "not_a_number"},  # type: ignore[dict-item]
        )
        result = tcp.load_correlations("gaming")
        assert result == {"good": 0.5}


class TestCorrelationToPriorDelta:
    def test_positive_r_bumps_alpha(self):
        da, db = tcp.correlation_to_prior_delta(0.5)
        assert da > 0
        assert db == 0.0

    def test_negative_r_bumps_beta(self):
        da, db = tcp.correlation_to_prior_delta(-0.5)
        assert da == 0.0
        assert db > 0

    def test_zero_r_zero_delta(self):
        assert tcp.correlation_to_prior_delta(0.0) == (0.0, 0.0)

    def test_r_clamped_to_range(self):
        """|r| > 1 shouldn't happen but if it does, clamp cleanly."""
        da, _ = tcp.correlation_to_prior_delta(5.0)
        da_max, _ = tcp.correlation_to_prior_delta(1.0)
        assert da == da_max

    def test_weight_zero_returns_zero(self):
        assert tcp.correlation_to_prior_delta(0.9, weight=0.0) == (0.0, 0.0)

    def test_weight_scales_magnitude(self):
        """Higher weight → larger delta."""
        da_low, _ = tcp.correlation_to_prior_delta(0.5, weight=0.2)
        da_high, _ = tcp.correlation_to_prior_delta(0.5, weight=0.8)
        assert da_low < da_high

    def test_delta_bounded_by_max_delta(self):
        """Even at r=1.0, weight=1.0 the delta is capped."""
        da, _ = tcp.correlation_to_prior_delta(1.0, weight=1.0)
        assert da == tcp.MAX_DELTA

    def test_max_delta_is_weak(self):
        """The cap is deliberately weak — real observed reward (α += 1
        per observation) overwhelms after ~2 observations. Pin that."""
        assert tcp.MAX_DELTA <= 2.0


class TestGetArmPriorAdjustment:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "true")

    def test_flag_off_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.5},
        )
        # Override the fixture: flag off overrides fixture-on for this test
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        assert tcp.get_arm_prior_adjustment("gaming", "title_length_chars") is None

    def test_no_artifact_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert tcp.get_arm_prior_adjustment("gaming", "title_length_chars") is None

    def test_unknown_feature_returns_none(self, monkeypatch, tmp_path):
        """Feature not in the map → None. Distinguishes from
        informative-zero (feature IS in map but correlation=0)."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.5},
        )
        result = tcp.get_arm_prior_adjustment("gaming", "unknown_feature")
        assert result is None

    def test_feature_present_positive_correlation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_ends_with_question": 0.6},
        )
        adj = tcp.get_arm_prior_adjustment("gaming", "title_ends_with_question")
        assert adj is not None
        da, db = adj
        assert da > 0
        assert db == 0.0

    def test_feature_present_zero_correlation_is_informative(self, monkeypatch, tmp_path):
        """Feature IS in map, r=0 → (0, 0) — the feature was measured
        and found uncorrelated. Distinguished from None (feature
        unknown)."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.0},
        )
        result = tcp.get_arm_prior_adjustment("gaming", "title_length_chars")
        assert result == (0.0, 0.0)

    def test_feature_present_negative_correlation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_emoji_count": -0.4},
        )
        adj = tcp.get_arm_prior_adjustment("gaming", "title_emoji_count")
        assert adj is not None
        da, db = adj
        assert da == 0.0
        assert db > 0

    def test_weight_flows_through(self, monkeypatch, tmp_path):
        """Non-default weight should scale the delta accordingly."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path / "top-creator-priors",
            "gaming",
            {"title_length_chars": 0.5},
        )
        low = tcp.get_arm_prior_adjustment("gaming", "title_length_chars", weight=0.1)
        high = tcp.get_arm_prior_adjustment("gaming", "title_length_chars", weight=0.9)
        assert low is not None
        assert high is not None
        assert low[0] < high[0]


class TestObservationOnlyDiscipline:
    """The scope memo pins that B.3 ships the READ + math only. The
    consumer wire at arm-instantiation-site is deferred. Assert that
    the module exposes exactly the READ surface and no side-effecting
    ``apply_*_to_arm`` helper — that boundary is intentional."""

    def test_public_surface_is_read_only(self):
        exported = set(tcp.__all__)
        assert exported == {
            "MAX_DELTA",
            "correlation_to_prior_delta",
            "get_arm_prior_adjustment",
            "load_correlations",
        }

    def test_no_side_effect_helper_exists(self):
        """No ``apply_prior_to_arm`` or ``update_arm_alpha_beta`` —
        those would be the consumer wire. Pin their absence so a
        future PR can't quietly add one without updating this test +
        the scope memory."""
        assert not hasattr(tcp, "apply_prior_to_arm")
        assert not hasattr(tcp, "update_arm_alpha_beta")
