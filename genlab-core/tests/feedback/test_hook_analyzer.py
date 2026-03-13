"""Tests for hook performance analyzer and readiness reporter."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.feedback.hook_analyzer import get_hook_training_data, MIN_EXAMPLES


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.pending_feedback = MagicMock()
    return client


class TestGetHookTrainingData:
    def test_returns_empty_when_no_proxy(self):
        client = MagicMock(spec=[])
        client.pending_feedback = None
        result = get_hook_training_data(client)
        assert result == []

    def test_returns_empty_when_no_completed_items(self, mock_client):
        mock_client.pending_feedback.all.return_value = []
        result = get_hook_training_data(mock_client)
        assert result == []

    def test_filters_items_without_hook_text(self, mock_client):
        mock_client.pending_feedback.all.return_value = [
            {"id": "1", "fields": {"HookText": "", "Reward48h": 0.5}},
            {"id": "2", "fields": {"Reward48h": 0.6}},  # no HookText key
        ]
        result = get_hook_training_data(mock_client)
        assert result == []

    def test_filters_items_without_reward(self, mock_client):
        mock_client.pending_feedback.all.return_value = [
            {"id": "1", "fields": {"HookText": "Great hook", "Reward48h": None}},
        ]
        result = get_hook_training_data(mock_client)
        assert result == []

    def test_returns_valid_training_examples(self, mock_client):
        mock_client.pending_feedback.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "HookText": "You won't believe this AI breakthrough",
                    "HookType": "reaction",
                    "HookLength": 6,
                    "Platform": "instagram",
                    "NicheId": "ai_news",
                    "PostContentType": "reel",
                    "Reward48h": 0.78,
                    "ReelsSkipRate": 0.23,
                },
            },
        ]
        result = get_hook_training_data(mock_client)
        assert len(result) == 1
        assert result[0]["hook_text"] == "You won't believe this AI breakthrough"
        assert result[0]["reward_48h"] == 0.78
        assert result[0]["reels_skip_rate"] == 0.23

    def test_min_examples_constant(self):
        assert MIN_EXAMPLES == 200
