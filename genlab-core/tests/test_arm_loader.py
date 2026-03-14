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
