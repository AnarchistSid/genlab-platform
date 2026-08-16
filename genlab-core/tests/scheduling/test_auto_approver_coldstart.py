"""Pin auto-approver cold-start adaptive min_confidence (2026-08-16).

## Context

Auto-approver's ``min_confidence`` gate has a chicken-and-egg:
niches with high configured threshold (0.85) never get gate scores
above it (real gate output 0.75-0.84) → no approvals → no outcome
calibration → tuner never lowers threshold → forever stuck. Result:
14 manual-unblocks on 2026-08-15, 6 more on 2026-08-16.

Fix: when outcome-source calibration count < 5 for a niche,
clamp effective min_confidence to 0.70 so exploration happens.
Once samples accumulate, configured threshold applies.

Flag-gated per niche via GENLAB_AUTO_APPROVER_COLDSTART_NICHES —
same canary pattern as persona_writer_hint, cross_channel_footer.

## What this test locks

  * Flag off → configured threshold applies (no cold-start)
  * Flag on + cold-start samples (<5) → clamps to 0.70
  * Flag on + adequate samples (>=5) → configured applies
  * Configured already <=0.70 → return configured (no upgrade)
  * Calibration lookup error → fail-open to configured
  * Canary isolation (anime enabled doesn't affect gaming)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from genlab_core.scheduling.auto_approver import (
    _COLDSTART_MIN_CONF,
    _COLDSTART_MIN_SAMPLES,
    _coldstart_enabled_for,
    _effective_min_confidence,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", val)
        assert _coldstart_enabled_for("gaming") is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_AUTO_APPROVER_COLDSTART_NICHES", raising=False,
        )
        assert _coldstart_enabled_for("gaming") is False

    def test_wildcard(self, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert _coldstart_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "gaming,movies",
        )
        assert _coldstart_enabled_for("gaming") is True
        assert _coldstart_enabled_for("movies") is True
        assert _coldstart_enabled_for("sports") is False


class TestEffectiveMinConfidence:
    def test_flag_off_returns_configured(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_AUTO_APPROVER_COLDSTART_NICHES", raising=False,
        )
        assert _effective_min_confidence("gaming", 0.85) == 0.85

    def test_coldstart_clamps_to_floor(self, monkeypatch):
        """Cold-start (<5 outcome samples) clamps 0.85 → 0.70."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        fake_stats = SimpleNamespace(sample_count=2)
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=fake_stats,
        ):
            result = _effective_min_confidence("gaming", 0.85)
        assert result == _COLDSTART_MIN_CONF
        assert result == 0.70

    def test_adequate_samples_returns_configured(self, monkeypatch):
        """>=5 outcome samples → configured threshold applies."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        fake_stats = SimpleNamespace(sample_count=5)  # exactly at floor
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=fake_stats,
        ):
            result = _effective_min_confidence("gaming", 0.85)
        assert result == 0.85

    def test_configured_below_coldstart_floor_unchanged(self, monkeypatch):
        """When configured is already below cold-start floor (e.g.
        ai_creators at 0.745), don't upgrade to 0.70. That would
        RAISE the threshold and break something. Return configured."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        # No calibration_logger patch needed — early return before lookup
        result = _effective_min_confidence("ai_creators", 0.65)
        assert result == 0.65

    def test_configured_exactly_at_floor_unchanged(self, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        result = _effective_min_confidence("gaming", 0.70)
        assert result == 0.70

    def test_lookup_error_fails_open_to_configured(self, monkeypatch):
        """Any calibration lookup error → don't accidentally lower
        threshold. Return configured unchanged."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            side_effect=Exception("db down"),
        ):
            result = _effective_min_confidence("gaming", 0.85)
        assert result == 0.85

    def test_non_canary_niche_unchanged_even_with_zero_samples(
        self, monkeypatch,
    ):
        """gaming NOT in canary list → configured applies regardless
        of sample count. Prevents accidental cross-niche activation."""
        monkeypatch.setenv(
            "GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "anime",
        )
        # No mock — non-canary short-circuits before lookup
        result = _effective_min_confidence("gaming", 0.85)
        assert result == 0.85

    def test_source_filter_is_outcome(self, monkeypatch):
        """Cold-start relies on OUTCOME calibration (post-hoc reward
        signal), not operator dashboard clicks. If we accidentally
        query operator-source (which is often 0 anyway), we'd get
        false-positive cold-start on niches with plenty of outcome
        data."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVER_COLDSTART_NICHES", "all")
        captured = {}

        def _fake_stats(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(sample_count=10)

        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            side_effect=_fake_stats,
        ):
            _effective_min_confidence("gaming", 0.85)
        assert captured.get("source_filter") == "outcome"


class TestConstants:
    def test_coldstart_floor_below_typical_gate_output(self):
        """Real gate output for cold-start niches is ~0.75-0.84.
        Cold-start floor must be BELOW that band or exploration
        still doesn't happen."""
        assert _COLDSTART_MIN_CONF < 0.75

    def test_min_samples_matches_tuner_floor(self):
        """Sample threshold aligns with calibration_tuner._MIN_SAMPLES
        (both are 5 after 2026-08-15 tuner floor drop). Keeps the
        two systems' floor consistent."""
        from genlab_core.scheduling.calibration_tuner import _MIN_SAMPLES
        assert _COLDSTART_MIN_SAMPLES == _MIN_SAMPLES
