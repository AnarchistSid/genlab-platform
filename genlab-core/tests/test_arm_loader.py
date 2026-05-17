"""Tests for genlab_core.learning.arm_loader.

Verifies arm loading/saving against mock proxies (no SharePoint calls).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.learning.arm_loader import BANDIT_LIST_NAMES, load_all_arms

# ---------------------------------------------------------------------------
# 1. load_all_arms returns {arm_id: (alpha, beta)} from proxy records
# ---------------------------------------------------------------------------


class TestLoadAllArms:
    def test_load_all_arms_returns_dict(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = [
            {
                "id": "rec1",
                "fields": {"Title": "game_launch_hype__youtube", "Alpha": 3.5, "Beta": 2.0},
            },
            {
                "id": "rec2",
                "fields": {"Title": "controversy__instagram", "Alpha": 7.0, "Beta": 4.0},
            },
        ]

        arms = load_all_arms(mock_proxy, "gaming")

        assert len(arms) == 2
        assert arms["game_launch_hype__youtube"] == (3.5, 2.0)
        assert arms["controversy__instagram"] == (7.0, 4.0)
        mock_proxy.all.assert_called_once()


# ---------------------------------------------------------------------------
# 2. load_all_arms handles missing / erroring list gracefully
# ---------------------------------------------------------------------------


class TestLoadAllArmsError:
    def test_load_all_arms_handles_missing_list(self):
        mock_proxy = MagicMock()
        mock_proxy.all.side_effect = ValueError("List not found")

        arms = load_all_arms(mock_proxy, "nonexistent")

        assert arms == {}


# ---------------------------------------------------------------------------
# 3. BANDIT_LIST_NAMES covers all known niches
# ---------------------------------------------------------------------------


class TestBanditListNames:
    def test_bandit_list_names_covers_all_niches(self):
        expected = {"gaming", "ai_creators", "sports", "movies", "anime"}
        assert set(BANDIT_LIST_NAMES.keys()) == expected

        # Each value should be a non-empty string ending with _BanditArms
        for niche_id, list_name in BANDIT_LIST_NAMES.items():
            assert isinstance(list_name, str)
            assert list_name.endswith("_BanditArms"), f"{niche_id}: {list_name}"
            assert len(list_name) > len("_BanditArms")


class TestLoadAllArmsNicheFilter:
    """PG-backed deployment shares one bandit_arms table across all
    niches. load_all_arms must filter in Python so callers get only
    their niche's arms, even when RLS isn't set on the session.
    """

    def test_drops_rows_for_other_niches(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = [
            {"id": "1", "fields": {"arm_id": "gameplay_clip",
                                   "niche_id": "gaming",
                                   "alpha": 3.0, "beta": 5.0}},
            {"id": "2", "fields": {"arm_id": "cast_reveal",
                                   "niche_id": "movies",
                                   "alpha": 2.0, "beta": 4.0}},
            {"id": "3", "fields": {"arm_id": "patch_news",
                                   "niche_id": "gaming",
                                   "alpha": 1.5, "beta": 1.5}},
        ]
        arms = load_all_arms(mock_proxy, "gaming")
        assert set(arms) == {"gameplay_clip", "patch_news"}
        assert arms["gameplay_clip"] == (3.0, 5.0)

    def test_keeps_rows_without_niche_id_legacy(self):
        """SharePoint-backed proxies don't carry niche_id on the row —
        the proxy itself is per-niche. Those rows must still load."""
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = [
            {"id": "1", "fields": {"arm_id": "legacy_arm",
                                   "alpha": 2.0, "beta": 3.0}},
        ]
        arms = load_all_arms(mock_proxy, "gaming")
        assert "legacy_arm" in arms
