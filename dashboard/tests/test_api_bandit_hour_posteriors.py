"""Tests for /api/v1/bandit/hour-posteriors.

Returns per-hour aggregate Beta posteriors for the optimal-time
bandit (PR #487). Drives the Mission Control hour-heatmap card —
operator's feedback loop for deciding when to flip
GENLAB_OPTIMAL_TIME_BANDIT=1.

Pins:
  Validation (3):
    - missing niche → 400
    - unknown niche → 404
    - all 5 canonical niches accepted

  Aggregation (4):
    - hours array always 24 entries (0..23) even with zero data
    - sum alphas + betas across platforms per hour
    - n_obs_est = alpha+beta-2 baseline (rounded)
    - mean = alpha / (alpha+beta), 0.5 for cold-start

  Arm filtering (2):
    - only ``hour:`` prefix + ``:{niche}`` suffix arms counted
    - cross-platform arms ALL contribute (aggregation invariant)

  Activation readiness (2):
    - total_observations < 30 → ready_for_activation=False
    - total_observations ≥ 30 → ready_for_activation=True

  Cold-start (1):
    - no arms exist → 200 + all 24 hours with zeros + ready=False
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestValidation:
    def test_missing_niche_returns_400(self, client):
        resp = client.get("/api/v1/bandit/hour-posteriors")
        assert resp.status_code == 400

    def test_unknown_niche_returns_404(self, client):
        resp = client.get("/api/v1/bandit/hour-posteriors?niche=not_real")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "niche_id",
        ["ai_creators", "gaming", "sports", "movies", "anime"],
    )
    def test_all_canonical_niches_accepted(self, niche_id, client):
        # Stub BacklogClient init failure → endpoint returns the
        # cold-start shape (empty arms, all 24 hours with zeros).
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            side_effect=RuntimeError("no DSN"),
        ):
            resp = client.get(f"/api/v1/bandit/hour-posteriors?niche={niche_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == niche_id


class TestAggregation:
    def _fake_client_with_arms(self, arms: dict):
        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        def patches():
            return (
                patch(
                    "genlab_core.http.backlog_client.BacklogClient",
                    return_value=fake_client,
                ),
                patch(
                    "genlab_core.learning.arm_loader.load_all_arms",
                    return_value=arms,
                ),
            )

        return patches

    def test_hours_array_always_24_entries(self, client):
        """Pin: response carries exactly 24 hour entries (0..23) even
        when most are empty. Frontend renders the full daily strip."""
        arms = {"hour:14:youtube:gaming": (5.0, 2.0)}
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        assert len(data["hours"]) == 24
        # Ordered 0..23
        assert [h["hour"] for h in data["hours"]] == list(range(24))

    def test_aggregates_across_platforms_per_hour(self, client):
        """Pin: arms for hour 14 across multiple platforms get summed.
        Operator sees the niche-wide hour signal, not per-platform."""
        arms = {
            "hour:14:youtube:gaming": (10.0, 5.0),
            "hour:14:instagram:gaming": (8.0, 3.0),
            "hour:14:facebook:gaming": (2.0, 1.0),
        }
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        hour_14 = data["hours"][14]
        # 10 + 8 + 2 = 20
        assert hour_14["alpha_sum"] == 20.0
        # 5 + 3 + 1 = 9
        assert hour_14["beta_sum"] == 9.0
        # mean = 20 / 29 ≈ 0.6897
        assert abs(hour_14["mean"] - 20.0 / 29.0) < 0.01

    def test_n_obs_est_baseline(self, client):
        """Pin: n_obs_est = alpha+beta - 2 (Beta(1,1) prior baseline).
        Single platform with one arm summing to alpha+beta=7 → n=5."""
        arms = {"hour:9:youtube:gaming": (3.0, 4.0)}
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        hour_9 = data["hours"][9]
        # alpha+beta = 7, minus 2 prior = 5
        assert hour_9["n_obs_est"] == 5

    def test_cold_start_mean_is_0_5(self, client):
        """Pin: hours with zero data have mean=0.5 (Beta(1,1) prior
        mean). Frontend renders these as neutral / empty."""
        arms = {"hour:14:youtube:gaming": (5.0, 5.0)}
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        hour_0 = data["hours"][0]  # no data
        assert hour_0["mean"] == 0.5
        assert hour_0["n_obs_est"] == 0


class TestArmFiltering:
    def _fake_client_with_arms(self, arms: dict):
        return TestAggregation()._fake_client_with_arms(arms)

    def test_only_hour_prefix_and_niche_suffix_counted(self, client):
        """Pin: arms missing the ``hour:`` prefix or the niche suffix
        are NOT counted. Without this filter, hook-style arms +
        cross-niche bleed would inflate the bandit's apparent
        learning state."""
        arms = {
            "hour:14:youtube:gaming": (10.0, 5.0),  # COUNTS
            "style:gaming:question": (20.0, 1.0),  # SKIP (style)
            "hour:14:youtube:movies": (50.0, 1.0),  # SKIP (wrong niche)
            "content:gaming:trailer": (5.0, 2.0),  # SKIP (no hour prefix)
        }
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        hour_14 = data["hours"][14]
        # Only the gaming/hour:14 arm contributes
        assert hour_14["alpha_sum"] == 10.0
        assert hour_14["beta_sum"] == 5.0


class TestActivationReadiness:
    def _fake_client_with_arms(self, arms: dict):
        return TestAggregation()._fake_client_with_arms(arms)

    def test_below_30_obs_not_ready(self, client):
        """Pin: total_observations < 30 → ready_for_activation=False.
        Below this threshold the Beta posteriors haven't moved far
        enough from the prior to be meaningfully different from random."""
        # Build arms summing to ~10 observations
        arms = {"hour:14:youtube:gaming": (8.0, 4.0)}  # n_obs_est=10
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        assert data["total_observations"] < 30
        assert data["ready_for_activation"] is False

    def test_at_or_above_30_obs_ready(self, client):
        """Pin: total_observations ≥ 30 → ready_for_activation=True.
        Operator's signal to flip GENLAB_OPTIMAL_TIME_BANDIT=1."""
        # Build arms summing to ~32 observations
        arms = {
            "hour:6:youtube:gaming": (10.0, 4.0),  # 12 obs
            "hour:14:youtube:gaming": (15.0, 5.0),  # 18 obs
            "hour:18:youtube:gaming": (2.0, 2.0),  # 2 obs (skipped)
        }
        client_p, loader_p = self._fake_client_with_arms(arms)()
        with client_p, loader_p:
            resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        data = json.loads(resp.data)["data"]
        assert data["total_observations"] >= 30
        assert data["ready_for_activation"] is True


class TestColdStart:
    def test_no_arms_returns_empty_strip(self, client):
        """Pin: zero arms → 200 + all 24 hours with zeros + ready=False.
        Cold-start UI surface — frontend renders empty strip, not 404
        or empty response."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={},
            ):
                resp = client.get("/api/v1/bandit/hour-posteriors?niche=gaming")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["hours"]) == 24
        assert all(h["n_obs_est"] == 0 for h in data["hours"])
        assert all(h["mean"] == 0.5 for h in data["hours"])
        assert data["total_observations"] == 0
        assert data["ready_for_activation"] is False
