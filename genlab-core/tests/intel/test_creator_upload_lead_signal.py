"""Pin the ``creator_upload_lead`` signal (A.3, 2026-07-08).

Extends Intervention 5's trend_anticipation with a 5th signal that
consumes A.2's YYYYMMDD-<niche>.json artifacts. Contract:

  1. Missing artifact dir → None
  2. Artifact for today or yesterday found; older ignored
  3. Malformed artifact → None (silent, no exception escapes)
  4. Empty ``creators`` list → None (watchlist not populated)
  5. Zero matches on populated watchlist → 0.0 (informative)
  6. N matching creators → weighted score capped at 1.0 for N ≥ 3
  7. Recency weighting: 0-6h → 1.0, 6-24h → linear decay, > 24h → 0
  8. Case-insensitive word-boundary matching
  9. Weight sum for the composite must still be 1.0 after A.3 rebalance
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.intel import trend_anticipation as ta


def _mock_artifact(dir_, niche_id, creators):
    """Helper: write a YYYYMMDD-<niche>.json fixture."""
    dir_.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y%m%d")
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "creators": creators,
    }
    path = dir_ / f"{today}-{niche_id}.json"
    path.write_text(json.dumps(payload))
    return path


class TestArtifactDiscovery:
    def test_missing_dir_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result is None

    def test_finds_todays_artifact(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
                    "label": "x",
                    "uploads": [{"title": "Vision Pro review", "hours_since_publish": 3.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("Vision Pro", "gaming")
        assert result is not None
        assert result > 0.0

    def test_finds_yesterdays_artifact_when_today_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        dir_.mkdir(parents=True)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y%m%d")
        payload = {
            "generated_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "niche_id": "gaming",
            "creators": [
                {
                    "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
                    "label": "x",
                    "uploads": [{"title": "Vision Pro", "hours_since_publish": 5.0}],
                }
            ],
        }
        (dir_ / f"{yesterday}-gaming.json").write_text(json.dumps(payload))
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result is not None
        assert result > 0.0

    def test_malformed_artifact_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        dir_.mkdir(parents=True)
        today = datetime.now(UTC).strftime("%Y%m%d")
        (dir_ / f"{today}-gaming.json").write_text("not valid json {[")
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result is None

    def test_empty_creators_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(tmp_path / "top-creator-uploads", "gaming", [])
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result is None


class TestScoreConstruction:
    def test_zero_matches_returns_zero(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
                    "label": "x",
                    "uploads": [{"title": "unrelated topic", "hours_since_publish": 3.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        # Zero matches on a POPULATED watchlist = 0.0 (informative
        # zero — the topic isn't hot among curated creators)
        assert result == 0.0

    def test_all_creators_matching_hits_saturation(self, monkeypatch, tmp_path):
        """3 creators × recency-1.0 matches → weighted_matches = 3 →
        score = 3 / min(3, 3) = 1.0."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "vision pro review", "hours_since_publish": 2.0}],
                },
                {
                    "channel_id": "UC1234567890123456789013",
                    "label": "b",
                    "uploads": [{"title": "Vision Pro unbox", "hours_since_publish": 4.0}],
                },
                {
                    "channel_id": "UC1234567890123456789014",
                    "label": "c",
                    "uploads": [{"title": "The Vision Pro is here", "hours_since_publish": 1.0}],
                },
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result == 1.0

    def test_partial_match_below_saturation(self, monkeypatch, tmp_path):
        """1 of 3 creators matching, recency 1.0 → weighted_matches=1
        → score = 1 / min(3, 3) = 0.333."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "vision pro review", "hours_since_publish": 2.0}],
                },
                {
                    "channel_id": "UC1234567890123456789013",
                    "label": "b",
                    "uploads": [{"title": "unrelated", "hours_since_publish": 2.0}],
                },
                {
                    "channel_id": "UC1234567890123456789014",
                    "label": "c",
                    "uploads": [{"title": "also unrelated", "hours_since_publish": 2.0}],
                },
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result == pytest.approx(1.0 / 3.0, abs=0.01)


class TestRecencyWeighting:
    def test_stale_upload_zero_weight(self, monkeypatch, tmp_path):
        """> 24h → weight 0 → zero matches → score 0."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "vision pro review", "hours_since_publish": 48.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        # 1 creator, 0 weighted matches → 0/1 → 0.0
        assert result == 0.0

    def test_fresh_upload_full_weight(self, monkeypatch, tmp_path):
        """< 6h → weight 1.0. 1 creator × 1.0 weight → 1.0/1 → 1.0."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "vision pro review", "hours_since_publish": 2.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result == 1.0

    def test_mid_range_upload_partial_weight(self, monkeypatch, tmp_path):
        """12h in the 6-24h window: linear decay from 1.0 → 0.5.
        At 12h → 1.0 - 0.5 * (6/18) = 1.0 - 0.167 ≈ 0.833."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "vision pro review", "hours_since_publish": 12.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result == pytest.approx(0.833, abs=0.01)


class TestWordBoundaryMatching:
    def test_no_substring_false_positive(self, monkeypatch, tmp_path):
        """The topic 'pro' shouldn't match 'provision'."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "provisioning tutorial", "hours_since_publish": 2.0}],
                }
            ],
        )
        result = ta._signal_creator_upload_lead("pro", "gaming")
        # Word boundary blocks it — zero matches on a populated
        # watchlist → 0.0
        assert result == 0.0

    def test_case_insensitive(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _mock_artifact(
            tmp_path / "top-creator-uploads",
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "a",
                    "uploads": [{"title": "VISION PRO Hands-On", "hours_since_publish": 2.0}],
                }
            ],
        )
        # Lower-case topic must still match all-caps title
        result = ta._signal_creator_upload_lead("vision pro", "gaming")
        assert result == 1.0


class TestWeightsInvariant:
    """After A.3 rebalance the 5 signal weights must still sum to 1.0."""

    def test_weight_sum(self):
        assert sum(ta._SIGNAL_WEIGHTS.values()) == pytest.approx(1.0)

    def test_all_5_signals_present(self):
        assert set(ta._SIGNAL_WEIGHTS.keys()) == {
            "search_velocity",
            "creator_pickup",
            "creator_upload_lead",
            "social_velocity",
            "news_lead",
        }

    def test_search_velocity_still_dominant(self):
        """After rebalance, search_velocity should remain the largest
        weight — it's the most-mature signal with the sharpest math
        (2nd derivative of interest-over-time)."""
        weights = ta._SIGNAL_WEIGHTS
        max_weight = max(weights.values())
        assert weights["search_velocity"] == max_weight
