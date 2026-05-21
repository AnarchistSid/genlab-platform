"""Tests for Steam spike detection algorithm."""

from niches.gaming.flows.spike_detector_flow import (
    MIN_OBSERVATIONS,
    PCT_CHANGE_THRESHOLD,
    RANK_VELOCITY_THRESHOLD,
    Z_SCORE_THRESHOLD,
    _z_score,
    detect_spikes,
)


class TestZScore:
    def test_empty_history(self):
        assert _z_score(100, []) == 0.0

    def test_single_observation(self):
        assert _z_score(100, [50]) == 0.0

    def test_normal_value(self):
        history = [100, 100, 100, 100, 100]
        z = _z_score(100, history)
        assert abs(z) < 0.01

    def test_spike_value(self):
        history = [100, 100, 100, 100, 100]
        z = _z_score(300, history)
        assert z > 2.0

    def test_dip_value(self):
        history = [100, 100, 100, 100, 100]
        z = _z_score(0, history)
        assert z < -2.0


class TestDetectSpikes:
    def _make_state_with_history(self, app_id, players_history, rank_history):
        """Build a state dict with pre-populated history."""
        return {
            "games": {
                app_id: {
                    "name": "TestGame",
                    "history": list(players_history),
                    "rank_history": list(rank_history),
                }
            },
            "triggered": {},
        }

    def test_no_spike_when_stable(self):
        """Stable player counts should not trigger a spike."""
        state = self._make_state_with_history(
            "123",
            [1000] * 10,  # stable history
            [5] * 10,  # stable rank
        )
        current = {"123": {"name": "TestGame", "players": 1050, "rank": 5}}
        spikes, _ = detect_spikes(current, state)
        assert len(spikes) == 0

    def test_spike_on_massive_increase(self):
        """A massive player count increase should trigger a spike."""
        state = self._make_state_with_history(
            "456",
            [1000] * 10,
            [8] * 10,
        )
        # 5x player count + rank jump from 8 to 1
        current = {"456": {"name": "SpikyGame", "players": 5000, "rank": 1}}
        spikes, _ = detect_spikes(current, state)
        assert len(spikes) == 1
        assert spikes[0]["name"] == "SpikyGame"
        assert spikes[0]["z_score"] > Z_SCORE_THRESHOLD

    def test_insufficient_history_no_spike(self):
        """With fewer than MIN_OBSERVATIONS, no spike should be detected."""
        state = {
            "games": {
                "789": {
                    "name": "NewGame",
                    "history": [100, 100],  # only 2 observations
                    "rank_history": [3, 3],
                }
            },
            "triggered": {},
        }
        current = {"789": {"name": "NewGame", "players": 10000, "rank": 1}}
        spikes, _ = detect_spikes(current, state)
        assert len(spikes) == 0

    def test_cooldown_prevents_retrigger(self):
        """A recently triggered game should not re-trigger within cooldown."""
        import time

        state = self._make_state_with_history(
            "111",
            [1000] * 10,
            [8] * 10,
        )
        state["triggered"]["111"] = time.time()  # just triggered
        current = {"111": {"name": "CooldownGame", "players": 5000, "rank": 1}}
        spikes, _ = detect_spikes(current, state)
        assert len(spikes) == 0

    def test_state_updated_after_detection(self):
        """State should include new observations after detection."""
        state = self._make_state_with_history(
            "222",
            [1000] * 10,
            [5] * 10,
        )
        current = {"222": {"name": "UpdateGame", "players": 1100, "rank": 5}}
        _, updated = detect_spikes(current, state)
        # History should have 11 entries now
        assert len(updated["games"]["222"]["history"]) == 11


class TestThresholdConstants:
    def test_thresholds_are_reasonable(self):
        assert Z_SCORE_THRESHOLD > 1.0
        assert 0 < PCT_CHANGE_THRESHOLD < 2.0
        assert RANK_VELOCITY_THRESHOLD > 0
        assert MIN_OBSERVATIONS >= 3
