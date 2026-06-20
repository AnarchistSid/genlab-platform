"""Regression test for PR #399 — preloaded bandit arms eliminate
2 redundant `load_all_arms` calls inside PushToBacklog.execute().

Pre-fix: ``_get_bandit_arm_boost`` and ``_get_bandit_arm_n_obs`` each
called ``load_all_arms(proxy, niche_id)`` internally — and the
PushToBacklog stage called BOTH per pipeline run. That's 2 full scans
of the bandit_arms table per niche per run.

Post-fix: both functions accept an ``arms`` kwarg. The PushToBacklog
stage loads arms ONCE and passes the dict to both helpers — 1 scan
per niche per run instead of 2.

Pin both invariants:
  * Behavioural — ``arms`` kwarg overrides the internal fetch, so
    callers can pre-load
  * Algorithmic — when ``arms`` is passed, ``proxy.all()`` is NOT
    invoked from within the function
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.pipeline.stages.push_to_backlog import (
    _get_bandit_arm_boost,
    _get_bandit_arm_n_obs,
)


class TestPreloadedArmsKwarg:
    def test_get_bandit_arm_boost_accepts_preloaded_arms(self):
        """When ``arms`` kwarg is passed, the function does NOT call
        ``proxy.all()`` — it works entirely off the preloaded dict.
        """
        # Mock client whose proxy.all() should never be called when
        # arms kwarg is provided.
        mock_client = MagicMock()
        mock_client.bandit_arms.all = MagicMock(side_effect=AssertionError("must not be called"))
        # Provide arms preloaded
        arms = {"style:funny": (10.0, 5.0), "style:serious": (7.0, 8.0)}
        result = _get_bandit_arm_boost(mock_client, "gaming", arms=arms)
        # Boosts produced for each arm — exact values are random (Thompson)
        # so we only pin the SHAPE, not the value
        assert set(result.keys()) == {"style:funny", "style:serious"}
        for boost in result.values():
            assert 0.7 <= boost <= 1.3  # FLOOR..CEIL range

    def test_get_bandit_arm_n_obs_accepts_preloaded_arms(self):
        """When ``arms`` kwarg is passed, the function does NOT call
        ``proxy.all()`` — it works entirely off the preloaded dict.
        """
        mock_client = MagicMock()
        mock_client.bandit_arms.all = MagicMock(side_effect=AssertionError("must not be called"))
        arms = {
            "style:funny": (10.0, 5.0),  # n_obs = 10+5-2 = 13
            "style:serious": (3.0, 4.0),  # n_obs = 3+4-2 = 5
            "style:cold": (1.0, 1.0),  # n_obs = 1+1-2 = 0
        }
        result = _get_bandit_arm_n_obs(mock_client, "gaming", arms=arms)
        assert result == {"style:funny": 13, "style:serious": 5, "style:cold": 0}

    def test_legacy_no_kwarg_path_still_loads_internally(self):
        """Pin backward compatibility: when ``arms`` is not provided
        (legacy callers), the function still loads internally.

        This ensures any external callers (none in genlab-core today,
        but the function names are public-ish via the _get_arm_boost
        alias at module-level) keep working without modification.
        """
        # Mock proxy returning fake records — load_all_arms will iterate.
        mock_client = MagicMock()
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "arm_funny",
                "fields": {
                    "niche_id": "gaming",
                    "arm_id": "style:funny",
                    "alpha": 5.0,
                    "beta": 3.0,
                },
            }
        ]
        result = _get_bandit_arm_n_obs(mock_client, "gaming")
        # Should have loaded internally and returned n_obs = 5+3-2 = 6
        assert result == {"style:funny": 6}
        # And the proxy.all() WAS called this time (no kwarg)
        assert mock_client.bandit_arms.all.called

    def test_preloaded_empty_arms_returns_empty(self):
        """When preloaded arms is empty dict, return empty result —
        no exception, no fallback fetch.
        """
        mock_client = MagicMock()
        mock_client.bandit_arms.all = MagicMock(side_effect=AssertionError("must not be called"))
        assert _get_bandit_arm_boost(mock_client, "gaming", arms={}) == {}
        assert _get_bandit_arm_n_obs(mock_client, "gaming", arms={}) == {}
