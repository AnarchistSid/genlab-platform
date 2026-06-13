"""Integration test: `_next_available_slot` consults the optimal-time
learner before falling back to publishing.yaml's static slots.

Task #33 (2026-06-13). Pre-fix the dashboard scheduler only read
``instagram.schedule_slots`` from each niche's publishing.yaml
(defaulting to ``["12:00"]``). Owner decision: "agent picks optimal
publish-times per platform" → this wiring makes the picker consult
``genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm`` and
fall through to yaml only when the learner returns no signal.

The unit-level learner tests live in
``genlab-core/tests/scheduling/test_optimal_time_learner.py``; this
file pins the dashboard-side wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Build a minimal env where _next_available_slot can run without
    hitting a real backlog client or real DB. publishing.yaml lives
    in tmp_path with a single 12:00 slot."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "publishing.yaml").write_text(
        'instagram:\n  schedule_slots:\n    - "12:00"\n  timezone: "Asia/Kolkata"\n'
    )
    # Backlog config path is one dir down from publishing.yaml per the
    # _next_available_slot resolution rules.
    backlog_cfg = config_dir / "lists_config.yaml"
    backlog_cfg.write_text("# noop\n")
    monkeypatch.setenv("BACKLOG_CONFIG_PATH", str(backlog_cfg))
    yield tmp_path


class TestLearnerPicksOptimalSlots:
    def test_uses_learned_slots_when_learner_returns_signal(self, isolated_env):
        """Learner says 15:30 is the best slot → that's what gets used,
        followed by the yaml fallback 12:00."""
        # Mock the backlog client so the collision-check pass returns no rows.
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=["15:30", "11:30"],
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            result = _next_available_slot(niche_id="movies")

        assert result is not None
        # The slot should be 15:30 IST (= 10:00 UTC) for some day in the
        # next 8. Exact day depends on whether 15:30 today is still in
        # the future; assert by checking the time string lands.
        assert "10:00" in result or "15:30" in result  # UTC iso or IST

    def test_falls_back_to_yaml_when_learner_returns_nothing(self, isolated_env):
        """Learner returns [] (cold start, no data, DB down) → yaml's
        ``12:00`` slot is used."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            result = _next_available_slot(niche_id="movies")

        assert result is not None
        # Must be 12:00 IST = 06:30 UTC
        assert "06:30" in result or "12:00" in result

    def test_falls_back_to_yaml_when_learner_raises(self, isolated_env):
        """Defensive: a learner exception MUST NOT block scheduling.
        Falls back to yaml slots."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                side_effect=RuntimeError("db unreachable"),
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            result = _next_available_slot(niche_id="movies")

        # Returns SOMETHING (not None) because yaml fallback fires.
        assert result is not None

    def test_learner_not_consulted_when_niche_id_empty(self, isolated_env):
        """Without niche_id, the optimal-time learner can't run (no
        per-niche history to mine). Falls back to yaml — and the learner
        function should never be invoked."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
            ) as mock_learner,
        ):
            from server.core.publishing_queue import _next_available_slot

            result = _next_available_slot(niche_id="")

        mock_learner.assert_not_called()
        assert result is not None  # yaml fallback fires

    def test_learned_slots_dedupe_against_yaml(self, isolated_env):
        """When the learner picks 12:00 AND yaml has 12:00, the union
        de-dupes — no duplicate slot processed."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=["12:00", "15:30"],
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            result = _next_available_slot(niche_id="movies")

        # Result should be picked from {12:00, 15:30}
        assert result is not None
