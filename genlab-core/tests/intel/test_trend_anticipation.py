"""Tests for Intervention 5 Session 1 (2026-07-01) — trend anticipation.

Coverage:
- Feature flag semantics (exact-true)
- Signal-weight-redistribution math (missing signals abstain, don't
  drag composite toward 0)
- ``_search_velocity_from_series`` correctness:
    * Insufficient data → None
    * Constant series (zero 2nd derivative) → score ~0.5, ETA 0
    * Accelerating series (positive 2nd derivative) → score > 0.5,
      positive ETA
    * Decelerating series (negative 2nd derivative) → score < 0.5,
      negative ETA
- Composite abstain path (all signals None → score=0.0, confidence=0)
- rank_topics ordering
- Persistence artifact shape
- Session-1 stubs: creator_pickup / social_velocity / news_lead all
  return None (guard so a future session doesn't silently flip them
  live without wiring the underlying source)
"""

from __future__ import annotations

import json

import pytest
from genlab_core.intel import trend_anticipation as ta

# ── Feature flag ────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_TREND_ANTICIPATION_ENABLED", raising=False)
        assert not ta._integration_enabled()

    @pytest.mark.parametrize("val", ["true", "TRUE", "True"])
    def test_exact_true_activates(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_TREND_ANTICIPATION_ENABLED", val)
        assert ta._integration_enabled()

    @pytest.mark.parametrize("val", ["1", "yes", "on", "TRUE_", "", "false"])
    def test_non_exact_true_stays_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_TREND_ANTICIPATION_ENABLED", val)
        assert not ta._integration_enabled()


# ── Signal weights sum to 1 ────────────────────────────────────────


def test_signal_weights_sum_to_one():
    """A math pin — if a future PR adds a signal without adjusting
    the existing weights, the composite would systematically shrink
    (weights don't sum to 1 anymore). Catch that here."""
    assert sum(ta._SIGNAL_WEIGHTS.values()) == pytest.approx(1.0)


# ── _search_velocity_from_series correctness ────────────────────────


class TestSearchVelocitySeriesMath:
    def test_insufficient_data_returns_none(self):
        # < 4 points → can't compute a meaningful 2nd derivative
        assert ta._search_velocity_from_series([]) is None
        assert ta._search_velocity_from_series([1.0]) is None
        assert ta._search_velocity_from_series([1.0, 2.0, 3.0]) is None

    def test_constant_series_score_near_half(self):
        """A flat series has 2nd derivative = 0 everywhere. sigmoid(0)
        = 0.5 — the "no acceleration, no info" score."""
        result = ta._search_velocity_from_series([50.0] * 7)
        assert result is not None
        score, hours = result
        assert score == pytest.approx(0.5)
        assert hours == 0  # magnitude < 0.5 → at/near peak

    def test_accelerating_series_score_above_half(self):
        """Quadratically-accelerating series → positive 2nd derivative
        everywhere → score > 0.5 → positive ETA (peak ahead)."""
        # v[i] = i² produces 2nd deriv = 2 constantly.
        values = [float(i * i) for i in range(7)]
        result = ta._search_velocity_from_series(values)
        assert result is not None
        score, hours = result
        assert score > 0.5, f"expected score > 0.5 for accelerating series, got {score}"
        assert hours > 0, f"expected positive ETA for accelerating series, got {hours}"

    def test_decelerating_series_score_below_half(self):
        """Quadratically-decelerating series → negative 2nd
        derivative → score < 0.5 → past-peak ETA (negative hours)."""
        values = [float(-i * i) for i in range(7)]
        result = ta._search_velocity_from_series(values)
        assert result is not None
        score, hours = result
        assert score < 0.5
        assert hours < 0


# ── Composite scoring ─────────────────────────────────────────────


class TestCompositeScoring:
    """The weight-redistribution property: a missing signal abstains
    rather than dragging the composite toward 0. This is what lets
    Session 1 ship with only search_velocity active without
    systematically producing composite scores of ~0.35 (which would
    be the naive weighted average with the other three signals at 0)."""

    def test_only_search_velocity_populated(self, monkeypatch):
        """search_velocity=0.8 → composite should be ~0.8 (not shrunk
        by the 3 abstaining signals)."""
        monkeypatch.setattr(ta, "_signal_search_velocity", lambda t, n: (0.8, 12))
        # Stubs already return None; explicit patch keeps the pin
        # independent of any future signal wire.
        monkeypatch.setattr(ta, "_signal_creator_pickup", lambda t, n: None)
        monkeypatch.setattr(ta, "_signal_social_velocity", lambda t, n: None)
        monkeypatch.setattr(ta, "_signal_news_lead", lambda t, n: None)

        score = ta.compute_anticipation_score("test-topic", "gaming")
        assert score.composite_score == pytest.approx(0.8)
        assert score.anticipated_peak_hours_ahead == 12
        assert score.confidence == pytest.approx(0.25)  # 1 of 4 signals

    def test_all_signals_none_abstains(self, monkeypatch):
        monkeypatch.setattr(ta, "_signal_search_velocity", lambda t, n: None)
        score = ta.compute_anticipation_score("test", "gaming")
        assert score.composite_score == 0.0
        assert score.confidence == 0.0
        assert "No signals available" in score.reasons[0]

    def test_two_signals_weight_redistributes(self, monkeypatch):
        """If search_velocity=0.6 (weight 0.60) and creator_pickup=0.9
        (weight 0.20), the composite renormalises those two weights
        to sum 1: search=0.75, creator=0.25 → composite = 0.6*0.75 +
        0.9*0.25 = 0.675."""
        monkeypatch.setattr(ta, "_signal_search_velocity", lambda t, n: (0.6, 6))
        monkeypatch.setattr(ta, "_signal_creator_pickup", lambda t, n: 0.9)
        score = ta.compute_anticipation_score("test", "gaming")
        expected = 0.6 * (0.60 / 0.80) + 0.9 * (0.20 / 0.80)
        assert score.composite_score == pytest.approx(expected)
        assert score.confidence == pytest.approx(0.5)  # 2 of 4


# ── Signal opt-in guards (Session 2, 2026-07-01) ──────────────────


class TestSignalOptInFlags:
    """Session 2 wires creator_pickup + social_velocity behind their
    own opt-in env flags so the ordinary daily run doesn't spend
    YouTube API quota / Reddit rate limit until the operator
    consciously enables them. Session 3 will do the same for
    news_lead. These pins guard the guardrails — a future PR that
    accidentally flips one of them ON by default fails here."""

    def test_creator_pickup_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANTICIPATION_YT_ENABLED", raising=False)
        assert ta._signal_creator_pickup("test", "gaming") is None

    def test_creator_pickup_off_when_api_key_missing(self, monkeypatch):
        """Flag on but YOUTUBE_API_KEY absent → still None (fail-open)."""
        monkeypatch.setenv("GENLAB_ANTICIPATION_YT_ENABLED", "true")
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        assert ta._signal_creator_pickup("test", "gaming") is None

    def test_social_velocity_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANTICIPATION_REDDIT_ENABLED", raising=False)
        assert ta._signal_social_velocity("test", "gaming") is None

    def test_social_velocity_off_when_reddit_creds_missing(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANTICIPATION_REDDIT_ENABLED", "true")
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        assert ta._signal_social_velocity("test", "gaming") is None

    def test_news_lead_returns_none(self):
        """Still Session-3 territory — the stub guard stays here as a
        drift-catcher until Session 3 replaces it with real logic."""
        assert ta._signal_news_lead("test", "gaming") is None


# ── rank_topics ordering ──────────────────────────────────────────


class TestRankTopics:
    def test_orders_by_composite_desc(self, monkeypatch):
        scores = {"low": 0.2, "hi": 0.9, "mid": 0.5}
        monkeypatch.setattr(
            ta,
            "_signal_search_velocity",
            lambda t, n: (scores[t], 0),
        )
        ranked = ta.rank_topics(["low", "hi", "mid"], "gaming", top_n=3)
        assert [s.topic for s in ranked] == ["hi", "mid", "low"]

    def test_top_n_truncates(self, monkeypatch):
        monkeypatch.setattr(ta, "_signal_search_velocity", lambda t, n: (0.5, 0))
        ranked = ta.rank_topics(["a", "b", "c", "d"], "gaming", top_n=2)
        assert len(ranked) == 2


# ── Session 2 signals (creator_pickup + social_velocity) ──────────


class _FakeResponse:
    """Minimal requests.Response stand-in. status_code + .json()
    are the only fields the signals read."""

    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class TestCreatorPickupSignal:
    """Guardrails + score-mapping for the YouTube-backed signal."""

    def _prep_env(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANTICIPATION_YT_ENABLED", "true")
        monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")
        # Skip caching for these unit tests — force a fresh HTTP hit
        # so we're actually testing the score-mapping math, not the
        # cache read path.
        monkeypatch.setattr(ta, "_get_cache", lambda: None)

    @pytest.mark.parametrize(
        "total_results,expected_lo,expected_hi",
        [
            (0, 0.0, 0.001),  # log10(1) = 0 → 0
            (10, 0.3, 0.4),  # log10(11)/3 ≈ 0.347
            (100, 0.65, 0.7),  # log10(101)/3 ≈ 0.669
            (10_000, 1.0, 1.0),  # saturates at 1.0
        ],
    )
    def test_score_mapping_log_scale(self, monkeypatch, total_results, expected_lo, expected_hi):
        """The log-scale mapping is what keeps this signal
        informative across the huge range of YouTube search
        totalResults. Linear normalisation would saturate at 1.0
        for anything ≥ ~50 results, losing discrimination between
        "10 videos" and "10,000 videos" — this pin catches a
        future edit that reverts to linear."""
        self._prep_env(monkeypatch)

        def fake_get(*args, **kwargs):
            return _FakeResponse(200, {"pageInfo": {"totalResults": total_results}})

        monkeypatch.setattr("requests.get", fake_get)
        score = ta._signal_creator_pickup("test topic", "gaming")
        assert score is not None
        assert expected_lo <= score <= expected_hi, (
            f"total_results={total_results} → score={score:.3f}, "
            f"expected in [{expected_lo}, {expected_hi}]"
        )

    def test_http_error_returns_none(self, monkeypatch):
        """Non-200 response → None (fail-open)."""
        self._prep_env(monkeypatch)

        def fake_get(*args, **kwargs):
            return _FakeResponse(403, {})

        monkeypatch.setattr("requests.get", fake_get)
        assert ta._signal_creator_pickup("t", "gaming") is None

    def test_exception_returns_none(self, monkeypatch):
        """Network / parse error → None."""
        self._prep_env(monkeypatch)

        def raiser(*args, **kwargs):
            raise RuntimeError("simulated network error")

        monkeypatch.setattr("requests.get", raiser)
        assert ta._signal_creator_pickup("t", "gaming") is None


class TestSocialVelocitySignal:
    """Guardrails + score-mapping for the Reddit-backed signal."""

    def _prep_env(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANTICIPATION_REDDIT_ENABLED", "true")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "fake-id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "fake-secret")
        monkeypatch.setattr(ta, "_get_cache", lambda: None)
        # Stub the OAuth token dance — this test is about the score
        # math on the search response, not the token endpoint.
        monkeypatch.setattr(
            "genlab_core.intel.reddit_fetcher._get_access_token",
            lambda cid, cs: "fake-token",
        )

    def test_viral_karma_saturates_at_one(self, monkeypatch):
        """A post with 1000 karma at 1h old → 1000 karma/h; mapping
        clamps to 1.0. Reddit signal never overshoots [0, 1]."""
        self._prep_env(monkeypatch)
        now = 1_700_000_000.0

        def fake_get(url, **kwargs):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "score": 1000,
                                    "created_utc": now - 3600,
                                }
                            }
                        ]
                    }
                },
            )

        import time as _t

        monkeypatch.setattr(_t, "time", lambda: now)
        monkeypatch.setattr("requests.get", fake_get)
        assert ta._signal_social_velocity("t", "gaming") == pytest.approx(1.0)

    def test_middling_karma_hits_target_score(self, monkeypatch):
        """25 karma/h → 0.5 by the linear-until-50 mapping. Two
        posts averaging 25 karma/h should hit exactly 0.5."""
        self._prep_env(monkeypatch)
        now = 1_700_000_000.0

        def fake_get(url, **kwargs):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "children": [
                            {"data": {"score": 25, "created_utc": now - 3600}},
                            {"data": {"score": 25, "created_utc": now - 3600}},
                        ]
                    }
                },
            )

        import time as _t

        monkeypatch.setattr(_t, "time", lambda: now)
        monkeypatch.setattr("requests.get", fake_get)
        assert ta._signal_social_velocity("t", "gaming") == pytest.approx(0.5)

    def test_empty_results_returns_none(self, monkeypatch):
        """0 posts matching → None (no signal) rather than 0 (which
        would be indistinguishable from "signal is bearish")."""
        self._prep_env(monkeypatch)

        def fake_get(url, **kwargs):
            return _FakeResponse(200, {"data": {"children": []}})

        monkeypatch.setattr("requests.get", fake_get)
        assert ta._signal_social_velocity("t", "gaming") is None

    def test_http_error_returns_none(self, monkeypatch):
        self._prep_env(monkeypatch)

        def fake_get(url, **kwargs):
            return _FakeResponse(429, {})

        monkeypatch.setattr("requests.get", fake_get)
        assert ta._signal_social_velocity("t", "gaming") is None


# ── Persistence ────────────────────────────────────────────────────


class TestPersistence:
    def test_writes_artifact_with_expected_shape(self, tmp_path):
        score = ta.AnticipationScore(
            topic="test",
            niche_id="gaming",
            composite_score=0.75,
            signals={
                "search_velocity": 0.75,
                "creator_pickup": None,
                "social_velocity": None,
                "news_lead": None,
            },
            anticipated_peak_hours_ahead=6,
            confidence=0.25,
            reasons=["stubbed"],
        )
        path = ta.persist_ranking("gaming", [score], output_dir=tmp_path)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["niche_id"] == "gaming"
        assert "generated_at" in payload
        assert "flag_enabled" in payload  # bool
        assert len(payload["ranking"]) == 1
        assert payload["ranking"][0]["topic"] == "test"
        assert payload["ranking"][0]["composite_score"] == pytest.approx(0.75)

    def test_empty_ranking_still_writes(self, tmp_path):
        """Empty ranking = "runner fired but no candidate topics had
        signal" — a valid observable state, not skip-worthy."""
        path = ta.persist_ranking("gaming", [], output_dir=tmp_path)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["ranking"] == []
