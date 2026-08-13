"""Pin the score-floor hysteresis for starved-niche relaxation.

Live-fire 2026-08-13: gaming's publish blueprint had
priority_score=0.2845 vs floor 0.3 — **0.005 below cutoff** lost
today's slot despite being the only viable candidate for the niche.
Manual score bump unblocked publish.

Hysteresis rule:
  * score >= floor          -> allowed (unchanged)
  * score in [floor - band, floor) AND forward queue starved -> allowed
  * score in [floor - band, floor) AND queue healthy         -> rejected
  * score < floor - band                                     -> rejected

Fail-CLOSED on queue query failure (strict floor stays enforced —
safer than accepting borderline content when we can't verify need).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from genlab_core.platforms.gatekeeper import PublishGatekeeper


def _bp(score, niche_id="gaming"):
    return {"priority_score": score, "niche_id": niche_id}


def _future_rows(n, hours_ahead=24):
    """Backlog rows with scheduled_for `hours_ahead` from now."""
    when = (datetime.now(UTC) + timedelta(hours=hours_ahead)).isoformat()
    return [{"fields": {"scheduled_for": when}} for _ in range(n)]


def _mkgk(*, backlog_rows=None, backlog_raises=False):
    daily_cap = MagicMock()
    daily_cap.can_publish.return_value = True
    backlog = MagicMock()
    if backlog_raises:
        backlog.blueprints.all.side_effect = RuntimeError("db down")
    else:
        backlog.blueprints.all.return_value = backlog_rows or []
    return PublishGatekeeper(
        config={"niche_config": {"publishing": {"score_floor": 0.3}}},
        daily_cap=daily_cap,
        backlog=backlog,
    )


class TestNoHysteresisAtOrAboveFloor:
    def test_at_floor_passes(self):
        gk = _mkgk()
        result = gk._score_floor_gate(_bp(0.3), "instagram")
        assert result.allowed is True

    def test_above_floor_passes(self):
        gk = _mkgk()
        result = gk._score_floor_gate(_bp(0.5), "instagram")
        assert result.allowed is True


class TestHysteresisWhenStarved:
    """0.2845 (0.005 below 0.3 floor) with starved queue -> allow.
    Reproduces exactly the 2026-08-13 gaming case."""

    def test_borderline_score_starved_queue_allowed(self):
        # 0 scheduled next 48h = starved
        gk = _mkgk(backlog_rows=[])
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is True
        assert "hysteresis" in result.reason

    def test_just_above_hysteresis_floor_allowed(self):
        # floor 0.3 - band 0.05 = 0.25. Score 0.25 exactly = allowed
        gk = _mkgk(backlog_rows=[])
        result = gk._score_floor_gate(_bp(0.25), "instagram")
        assert result.allowed is True

    def test_below_hysteresis_floor_rejected(self):
        # score below floor-band -> reject even when starved
        gk = _mkgk(backlog_rows=[])
        result = gk._score_floor_gate(_bp(0.20), "instagram")
        assert result.allowed is False
        assert "below floor" in result.reason


class TestNoHysteresisWhenQueueHealthy:
    """When niche has 2+ upcoming scheduled blueprints, strict floor
    stays enforced — protects content quality when we have alternatives."""

    def test_borderline_score_healthy_queue_rejected(self):
        gk = _mkgk(backlog_rows=_future_rows(3))  # 3 scheduled next 48h
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is False
        assert "below floor" in result.reason

    def test_borderline_score_2_scheduled_still_rejected(self):
        # Threshold is <2 = starved. Exactly 2 = NOT starved -> reject
        gk = _mkgk(backlog_rows=_future_rows(2))
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is False

    def test_borderline_score_1_scheduled_allowed(self):
        gk = _mkgk(backlog_rows=_future_rows(1))
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is True


class TestFailClosedOnBacklogErrors:
    """Queue query failure -> strict floor stays enforced. Safer to
    reject borderline than to accept when we can't verify need."""

    def test_backlog_raises_borderline_rejected(self):
        gk = _mkgk(backlog_raises=True)
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is False

    def test_backlog_none_borderline_rejected(self):
        gk = _mkgk()
        gk._backlog = None
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is False


class TestOnlyFutureCounts:
    """Blueprints with past scheduled_for don't count as forward queue."""

    def test_past_scheduled_ignored(self):
        when = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        rows = [{"fields": {"scheduled_for": when}} for _ in range(5)]
        gk = _mkgk(backlog_rows=rows)  # 5 past = 0 forward
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is True  # starved because all in past

    def test_beyond_lookahead_ignored(self):
        # Lookahead is 48h; schedule 5 days out = out of window
        rows = _future_rows(3, hours_ahead=120)
        gk = _mkgk(backlog_rows=rows)
        result = gk._score_floor_gate(_bp(0.2845), "instagram")
        assert result.allowed is True  # starved because all beyond 48h


class TestCache:
    """Per-instance cache prevents re-querying the backlog for the
    same niche within a single publisher pass."""

    def test_second_call_hits_cache(self):
        gk = _mkgk(backlog_rows=_future_rows(3))
        gk._score_floor_gate(_bp(0.2845), "instagram")
        gk._score_floor_gate(_bp(0.29), "instagram")
        # Only one backlog query for the same niche
        assert gk._backlog.blueprints.all.call_count == 1

    def test_different_niches_query_separately(self):
        gk = _mkgk(backlog_rows=_future_rows(3))
        gk._score_floor_gate(_bp(0.2845, niche_id="gaming"), "instagram")
        gk._score_floor_gate(_bp(0.2845, niche_id="anime"), "instagram")
        assert gk._backlog.blueprints.all.call_count == 2


class TestNoNicheIdEdgeCase:
    def test_missing_niche_id_no_hysteresis(self):
        """Without niche_id, can't check queue depth -> strict floor."""
        gk = _mkgk(backlog_rows=[])
        bp = {"priority_score": 0.2845}  # no niche_id
        result = gk._score_floor_gate(bp, "instagram")
        assert result.allowed is False
