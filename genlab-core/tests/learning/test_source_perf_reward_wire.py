"""PR #571b (2026-06-25) — _update_source_arm_reward wire pins.

Pins the helper that fires at 48h reward-window close:

  * Looks up blueprint via BacklogClient
  * Extracts `source` from blueprint.fields
  * Calls record_source_outcome with niche + source + reward
  * Best-effort: every failure → False + DEBUG log, never raises

The helper is lazy-imported inside the function body so the
metric_collector module load doesn't pay for BacklogClient
unless this wire actually fires.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ── happy path ─────────────────────────────────────────────────────


def test_helper_calls_record_source_outcome_with_correct_args():
    """Source pulled from blueprint.fields.source → forwarded to
    record_source_outcome with the right (niche, source, reward)."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "youtube_trending"},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.7,
        )
    assert result is True
    mock_record.assert_called_once_with(
        niche_id="gaming",
        source="youtube_trending",
        reward=0.7,
        platform=None,
    )


# ── fail-OPEN paths ────────────────────────────────────────────────


def test_helper_returns_false_when_blueprint_id_empty():
    """Defensive: empty blueprint_id → False, no BacklogClient call."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    with patch("genlab_core.http.backlog_client.BacklogClient") as mock_client:
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="",
            reward=0.5,
        )
    assert result is False
    mock_client.assert_not_called()


def test_helper_returns_false_when_blueprint_not_found():
    """Blueprint lookup returns None (missing row) → False."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = None
    with patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-missing",
            reward=0.5,
        )
    assert result is False


def test_helper_returns_false_when_source_missing():
    """Blueprint exists but `source` field is empty/None → False.
    Legacy blueprints OR niches whose fetcher doesn't populate
    source column yet. Silent skip (no WARN)."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": ""},  # empty
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch("genlab_core.learning.source_performance.record_source_outcome") as mock_record,
    ):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    assert result is False
    # record_source_outcome NOT called when source is missing —
    # don't pollute the bandit with empty-source rows
    mock_record.assert_not_called()


def test_helper_returns_false_when_record_outcome_returns_false():
    """record_source_outcome returns False (DSN missing / DB error)
    → wire returns False too, propagating the failure signal."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "youtube_trending"},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=False,
        ),
    ):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    assert result is False


def test_helper_swallows_backlog_client_exception():
    """BacklogClient instantiation or .get raises → False, no
    propagation. Critical: the reward loop must NEVER fail because
    of a per-source-arm side-effect."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    with patch(
        "genlab_core.http.backlog_client.BacklogClient",
        side_effect=RuntimeError("simulated client init failure"),
    ):
        # Must not raise; returns False
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    assert result is False


def test_helper_swallows_record_outcome_exception():
    """record_source_outcome raising (shouldn't happen — it's already
    fail-OPEN to False — but defense in depth) → wire returns False."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "youtube_trending"},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            side_effect=RuntimeError("simulated record failure"),
        ),
    ):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    assert result is False


# ── shape pins ─────────────────────────────────────────────────────


def test_helper_handles_non_dict_blueprint_safely():
    """Defensive: BacklogClient may return a non-dict (older API
    shape, error from backend). Don't crash; treat as no-source."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = "not a dict"
    with patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client):
        result = _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    assert result is False


def test_helper_strips_whitespace_from_source():
    """Source field with leading/trailing whitespace → stripped
    before forwarding. Pin via record_source_outcome's bound source."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "  youtube_trending  "},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        _update_source_arm_reward(
            niche_id="gaming",
            blueprint_id="bp-1",
            reward=0.5,
        )
    mock_record.assert_called_once_with(
        niche_id="gaming",
        source="youtube_trending",
        reward=0.5,
        platform=None,
    )


# ── source pin: hookpoint in main reward path ─────────────────────


def test_source_pin_wire_called_at_reward_window_close():
    """The wire MUST be called from the 48h reward-window block
    in metric_collector. Pin via source-text search so a future
    refactor that moves the call out of the reward loop surfaces."""
    from pathlib import Path

    import genlab_core.learning.metric_collector as mod

    src = Path(mod.__file__).read_text()
    assert "_update_source_arm_reward(" in src
    assert "PR #571b" in src
    # rindex finds the LAST occurrence — that's the call site, not
    # the function definition. Both functions have one def + one
    # call; defs come first in the file, calls come later in the
    # reward-window-close section.
    rca_call_idx = src.rindex("_check_post_bomb_signal(")
    wire_call_idx = src.rindex("_update_source_arm_reward(")
    assert wire_call_idx > rca_call_idx, (
        "_update_source_arm_reward should fire AFTER _check_post_bomb_signal "
        "(post-bomb signal observability before learning-loop update)"
    )
