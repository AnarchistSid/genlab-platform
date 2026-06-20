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
    _load_linucb_arms,
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


class TestLoadLinucbArmsExtendedKwarg:
    """PR #400 — _load_linucb_arms accepts preloaded `extended` dict.

    Pre-PR #400 it did its own proxy.all() — that was the 3rd full scan
    of bandit_arms per PushToBacklog.execute(). With the kwarg, all 3
    consumers (boost, n_obs, linucb) share one scan.
    """

    def test_extended_kwarg_avoids_proxy_call(self):
        """When `extended` is passed, proxy.all() is NOT invoked."""
        mock_client = MagicMock()
        mock_client.bandit_arms.all = MagicMock(side_effect=AssertionError("must not be called"))
        extended = {
            "style:funny": {"alpha": 5.0, "beta": 3.0, "linucb_state": None},
            "style:serious": {"alpha": 7.0, "beta": 2.0, "linucb_state": None},
        }
        result = _load_linucb_arms(mock_client, "gaming", extended=extended)
        # Each arm gets a LinUCBArm; with linucb_state=None they're fresh
        # default-init arms (n_obs = 0).
        assert set(result.keys()) == {"style:funny", "style:serious"}
        for arm_id, lucb_arm in result.items():
            assert lucb_arm.n_obs == 0, f"{arm_id} should have fresh LinUCB state"

    def test_extended_kwarg_with_persisted_state(self):
        """When `extended` carries a persisted ``linucb_state``, the
        LinUCBArm is rebuilt from it via ``from_dict`` — no re-parse
        from JSON, no proxy fetch.
        """
        import numpy as np
        from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm

        # Build a real LinUCBArm with some observations, serialize it,
        # and pass the serialized dict via the extended kwarg.
        original = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        original.update(np.array([0.5] * CONTEXT_DIM), reward=0.4)
        original.update(np.array([0.3] * CONTEXT_DIM), reward=0.7)
        state_dict = original.to_dict()

        mock_client = MagicMock()
        mock_client.bandit_arms.all = MagicMock(side_effect=AssertionError("must not be called"))
        extended = {
            "style:trained": {"alpha": 3.0, "beta": 1.0, "linucb_state": state_dict},
        }
        result = _load_linucb_arms(mock_client, "gaming", extended=extended)
        assert "style:trained" in result
        # The restored arm has the same observation count
        assert result["style:trained"].n_obs == 2

    def test_legacy_no_kwarg_path_still_loads_internally(self):
        """Pin backward compatibility: when `extended` is not provided,
        the function still fetches via proxy.all() (legacy path).
        """
        mock_client = MagicMock()
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "niche_id": "gaming",
                    "arm_id": "style:legacy",
                    "linucb_state": "",  # empty → fresh LinUCBArm
                },
            }
        ]
        result = _load_linucb_arms(mock_client, "gaming")
        assert "style:legacy" in result
        assert mock_client.bandit_arms.all.called  # legacy path WAS used
