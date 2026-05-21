"""Tests for crispy_scorer.py -- model loading security and correctness.

Covers:
  (a) Loading a valid .npz file succeeds
  (b) Missing model file raises FileNotFoundError with clear message
  (c) np.load is called with allow_pickle=False (security)
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from niches.gaming.tools.crispy_scorer import CrispyScorer


@pytest.fixture
def scorer_config():
    return {
        "enable_highlight_detection": True,
        "fallback_score": 0.5,
        "models": {
            "valorant": {
                "weights_path": "models/crispy/valorant.npz",
                "game_names": ["Valorant", "VALORANT"],
                "kill_feed_region": {
                    "box_x": 100,
                    "box_y": 0,
                    "box_width": 120,
                    "box_height": 60,
                },
                "preprocessing": "valorant_edge_mask",
                "confidence_threshold": 0.90,
                "input_resolution": [1920, 1080],
            },
        },
    }


@pytest.fixture
def scorer(scorer_config):
    return CrispyScorer(scorer_config, "/tmp/test_project")


class TestModelLoading:
    def test_missing_model_raises_file_not_found(self, scorer):
        """Missing .npz file must raise FileNotFoundError with the path."""
        with pytest.raises(FileNotFoundError, match="Model weights not found"):
            scorer._load_model("valorant")

    def test_valid_npz_loads_successfully(self, scorer, tmp_path):
        """A valid .npz file with layer arrays loads correctly."""
        npz_path = tmp_path / "valorant.npz"
        layer_0 = np.random.randn(10, 5).astype(np.float64)
        layer_1 = np.random.randn(2, 10).astype(np.float64)
        np.savez(npz_path, layer_0=layer_0, layer_1=layer_1)

        scorer.config["models"]["valorant"]["weights_path"] = str(npz_path)
        scorer.project_root = ""

        weights = scorer._load_model("valorant")
        assert len(weights) == 2
        np.testing.assert_array_equal(weights[0], layer_0)
        np.testing.assert_array_equal(weights[1], layer_1)

    def test_np_load_called_with_no_unsafe_deserialization(self, scorer, tmp_path):
        """np.load must be called with allow_pickle=False to prevent RCE."""
        npz_path = tmp_path / "valorant.npz"
        np.savez(npz_path, layer_0=np.array([1.0]))

        scorer.config["models"]["valorant"]["weights_path"] = str(npz_path)
        scorer.project_root = ""

        with patch("niches.gaming.tools.crispy_scorer.np.load", wraps=np.load) as mock_load:
            scorer._load_model("valorant")
            mock_load.assert_called_once()
            _, kwargs = mock_load.call_args
            assert kwargs.get("allow_pickle") is False, (
                "np.load must use allow_pickle=False to prevent arbitrary code execution"
            )

    def test_npy_extension_swapped_to_npz(self, scorer, tmp_path):
        """Config paths ending in .npy must be swapped to .npz."""
        npz_path = tmp_path / "valorant.npz"
        np.savez(npz_path, layer_0=np.array([1.0]))

        scorer.config["models"]["valorant"]["weights_path"] = str(tmp_path / "valorant.npy")
        scorer.project_root = ""

        weights = scorer._load_model("valorant")
        assert len(weights) == 1

    def test_model_caching(self, scorer, tmp_path):
        """Loaded model should be cached -- second call returns same object."""
        npz_path = tmp_path / "valorant.npz"
        np.savez(npz_path, layer_0=np.array([1.0]))

        scorer.config["models"]["valorant"]["weights_path"] = str(npz_path)
        scorer.project_root = ""

        w1 = scorer._load_model("valorant")
        w2 = scorer._load_model("valorant")
        assert w1 is w2


class TestGameSupport:
    def test_supports_known_game(self, scorer):
        assert scorer.supports_game("Valorant") is True
        assert scorer.supports_game("VALORANT") is True

    def test_rejects_unknown_game(self, scorer):
        assert scorer.supports_game("Unknown Game") is False

    def test_disabled_scorer_returns_fallback(self, scorer):
        scorer.config["enable_highlight_detection"] = False
        assert scorer.score_video("/nonexistent.mp4", "Valorant") == 0.5
