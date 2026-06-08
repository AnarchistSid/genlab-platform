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
