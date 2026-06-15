"""Tests for :mod:`genlab_core.publishing.blueprint_selector`.

Lives next to its module home (paralleling the PR 6d/N extraction in
the publish_all_platforms decomposition).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.publishing.blueprint_selector import select_blueprint


def _bp(bp_id: str, *, niche_id: str = "gaming", priority_score: float = 0.5) -> dict:
    return {
        "id": bp_id,
        "fields": {
            "niche_id": niche_id,
            "priority_score": priority_score,
            "hook": f"hook for {bp_id}",
        },
    }


class TestSelectBlueprint:
    def test_no_blueprints_returns_none(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = []
        assert select_blueprint("gaming", bc) is None

    def test_queries_visual_ready_for_niche(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = []
        select_blueprint("gaming", bc)
        bc.get_blueprints_by_status.assert_called_once_with("VISUAL_READY", niche_id="gaming")

    def test_picks_highest_priority_score(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("low", priority_score=0.2),
            _bp("high", priority_score=0.9),
            _bp("mid", priority_score=0.5),
        ]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(allowed=True)
            result = select_blueprint("gaming", bc)
        assert result["id"] == "high"

    def test_cross_niche_blueprint_skipped(self) -> None:
        """Cross-niche guard: a SharePoint query that accidentally returns
        rows from another niche must be filtered out before the gatekeeper
        runs — prevents cross-channel publish even if the query is broken."""
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("wrong_niche", niche_id="sports"),
            _bp("right_niche", niche_id="gaming"),
        ]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(allowed=True)
            result = select_blueprint("gaming", bc)
        assert result["id"] == "right_niche"

    def test_ai_alias_normalised_for_cross_niche_check(self) -> None:
        """``ai_tech`` and ``ai_news`` aliases collapse to ``ai_creators`` —
        blueprints written under the legacy names must still pass the
        cross-niche check."""
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("legacy", niche_id="ai_tech")]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(allowed=True)
            result = select_blueprint("ai_creators", bc)
        assert result is not None
        assert result["id"] == "legacy"

    def test_gatekeeper_blocked_blueprints_filtered(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("blocked"),
            _bp("allowed", priority_score=0.6),
        ]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            instance = MockGK.return_value
            instance.evaluate.side_effect = [
                MagicMock(allowed=False, gate_name="approval", reason="not approved"),
                MagicMock(allowed=True),
            ]
            result = select_blueprint("gaming", bc)
        assert result["id"] == "allowed"

    def test_all_gatekeeper_blocked_returns_none(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("bp1"), _bp("bp2")]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(
                allowed=False, gate_name="approval", reason="not approved"
            )
            assert select_blueprint("gaming", bc) is None

    def test_priority_score_missing_treated_as_zero(self) -> None:
        """Backlog rows occasionally lack priority_score — they should
        sort to the bottom (zero), not crash the sort."""
        bc = MagicMock()
        bp_no_score = {"id": "no_score", "fields": {"niche_id": "gaming"}}
        bc.get_blueprints_by_status.return_value = [
            bp_no_score,
            _bp("scored", priority_score=0.3),
        ]
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(allowed=True)
            result = select_blueprint("gaming", bc)
        assert result["id"] == "scored"  # 0.3 > 0 (implicit)


class TestScheduledForOrdering:
    """Pin the 2026-06-15 publisher-sort fix.

    Bug: when multiple eligible blueprints existed for a niche (e.g.
    the over-schedule backlog from before PR #220's per-day cap fix),
    the publisher sorted by priority_score alone. Result: the later-
    scheduled but higher-scoring sibling won, leaving the earlier-
    scheduled one stranded VISUAL_READY (verified prod: anime + sports
    on 2026-06-15 — 11:30 IST lost to 12:00 IST, cap then blocked
    11:30 from publishing).

    Fix: sort key = (scheduled_for ASC, priority_score DESC). Earliest
    wins; priority_score is only the tiebreaker.
    """

    def _make_bp(self, bp_id: str, scheduled_for: str | None, score: float = 0.5):

        return {
            "id": bp_id,
            "fields": {
                "niche_id": "gaming",
                "priority_score": score,
                "scheduled_for": scheduled_for,
                "hook": f"hook-{bp_id}",
                "title": f"title-{bp_id}",
            },
        }

    def _run(self, blueprints):
        from unittest.mock import MagicMock, patch

        from genlab_core.publishing.blueprint_selector import select_blueprint

        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = blueprints
        with patch("genlab_core.publishing.blueprint_selector.PublishGatekeeper") as MockGK:
            MockGK.return_value.evaluate.return_value = MagicMock(allowed=True)
            return select_blueprint("gaming", bc)

    def test_earlier_scheduled_wins_even_with_lower_score(self):
        """THE headline pin: 11:30 IST score=0.4 must beat 12:00 IST
        score=0.5. Without this, the 2026-06-15 anime+sports cap-
        misfire pattern recurs."""
        early = self._make_bp("11_30_low_score", "2026-06-15T06:00:00+00:00", score=0.4)
        late = self._make_bp("12_00_high_score", "2026-06-15T06:30:00+00:00", score=0.5)
        result = self._run([late, early])  # query order shouldn't matter
        assert result["id"] == "11_30_low_score", (
            "earlier scheduled_for must win — without this the publisher's "
            "cap fires on the wrong sibling and the early one is stranded"
        )

    def test_priority_score_tiebreaks_within_same_scheduled_for(self):
        """When two blueprints have identical scheduled_for (rare but
        possible — operator drag-and-drops onto same slot), the higher
        score wins."""
        bp_low = self._make_bp("same_time_low", "2026-06-15T06:00:00+00:00", score=0.3)
        bp_high = self._make_bp("same_time_high", "2026-06-15T06:00:00+00:00", score=0.8)
        result = self._run([bp_low, bp_high])
        assert result["id"] == "same_time_high"

    def test_missing_scheduled_for_sorts_last(self):
        """A blueprint with no scheduled_for must not jump ahead of one
        with a real future timestamp. Defensive: an in-flight pre-set
        gap shouldn't promote an unscheduled blueprint to first place."""
        scheduled = self._make_bp("has_sched", "2026-06-15T06:00:00+00:00", score=0.5)
        unscheduled = self._make_bp("no_sched", None, score=0.9)  # higher score!
        result = self._run([unscheduled, scheduled])
        assert result["id"] == "has_sched", (
            "missing scheduled_for must sort LAST (sentinel 9999) — "
            "otherwise a pre-set gap promotes the wrong blueprint"
        )
