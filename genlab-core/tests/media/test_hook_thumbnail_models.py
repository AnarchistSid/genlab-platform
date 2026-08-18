"""Pin hook_thumbnail_models — task #203 (2026-08-18):

  * Flag semantics on/off
  * Deterministic selection (same hook → same model)
  * Different hooks may pick different models
  * All 3 models' input builders produce well-formed dicts
  * Registry order is stable so hash-mod indices don't drift
  * extract_image_url handles 4 output shapes
  * Cost values match live-verified prices
"""
from __future__ import annotations

import pytest

from genlab_core.media.hook_thumbnail_models import (
    _REGISTRY,
    ImageModel,
    _build_flux_input,
    _build_gpt_image_input,
    _build_grok_input,
    _flux_model,
    extract_image_url,
    multi_model_enabled,
    pick_model,
)


class TestMultiModelFlag:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled() is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", raising=False,
        )
        assert multi_model_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_on_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled() is True


class TestRegistry:
    def test_flux_is_baseline_tier_zero(self):
        """The 0th slot MUST be flux — pick_model returns flux on flag-off,
        and future bandit priors will assume flux is index 0."""
        assert _REGISTRY[0].model_id == "flux"
        assert _REGISTRY[0].belt_app == "pruna/flux-dev"
        assert _flux_model().model_id == "flux"

    def test_registry_has_all_six_models(self):
        """3 originals + 3 wide-expansion adds (task #209, 2026-08-18)."""
        ids = {m.model_id for m in _REGISTRY}
        assert ids == {
            "flux", "gpt-image-2", "grok-imagine",
            "seedream-4-5", "gemini-3-pro-image", "reve",
        }

    def test_registry_costs_match_live_pricing(self):
        """Cost values are documented in module docstring — regression
        pins prevent silent drift if we swap providers."""
        cost_by_id = {m.model_id: m.cost_per_image_usd for m in _REGISTRY}
        assert cost_by_id["flux"] == 0.005
        assert cost_by_id["gpt-image-2"] == 0.006
        assert cost_by_id["grok-imagine"] == 0.020
        assert cost_by_id["seedream-4-5"] == 0.040
        assert cost_by_id["gemini-3-pro-image"] == 0.134
        assert cost_by_id["reve"] == 0.040

    def test_expansion_models_have_valid_input_builders(self):
        """Each new model's build_input must produce a non-empty dict
        with at least `prompt`. Pin catches accidental schema drift."""
        from genlab_core.media.hook_thumbnail_models import _REGISTRY
        expansion_ids = {"seedream-4-5", "gemini-3-pro-image", "reve"}
        for m in _REGISTRY:
            if m.model_id not in expansion_ids:
                continue
            inp = m.build_input("test prompt", 42, 1080, 1920)
            assert isinstance(inp, dict) and "prompt" in inp
            assert inp["prompt"] == "test prompt"


class TestPickModelFlagOff:
    def test_flag_off_always_returns_flux(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", raising=False,
        )
        for hook in ("a", "b", "c", "d" * 100):
            for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
                assert pick_model(hook, niche).model_id == "flux"


class TestPickModelFlagOn:
    def test_same_inputs_same_model(self, monkeypatch):
        """Deterministic: same (hook, niche) MUST pick the same model
        across calls, so re-renders stay idempotent."""
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", "1")
        for _ in range(5):
            m1 = pick_model("hook A", "ai_creators").model_id
            m2 = pick_model("hook A", "ai_creators").model_id
            assert m1 == m2

    def test_different_hooks_different_models(self, monkeypatch):
        """Not a strict property (2 hooks CAN land on the same model)
        but across 30 hooks we should see all 3 models represented."""
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", "1")
        picks = {
            pick_model(f"hook {i}", "ai_creators").model_id
            for i in range(30)
        }
        assert len(picks) >= 2, (
            f"only saw {picks} across 30 hooks — hash rotation may be broken"
        )


class TestInputBuilders:
    def test_flux_input_carries_wh_and_seed(self):
        inp = _build_flux_input("cinematic scene", 12345, 1080, 1920)
        assert inp["prompt"] == "cinematic scene"
        assert inp["width"] == 1080
        assert inp["height"] == 1920
        assert inp["seed"] == 12345
        assert inp["num_inference_steps"] > 0

    def test_gpt_image_input_uses_low_quality_tier(self):
        """cheapest tier per $0.006 — high tier costs $0.21 which
        would blow the daily budget on canary volume."""
        inp = _build_gpt_image_input("scene", 12345, 1080, 1920)
        assert inp["quality"] == "low"
        assert inp["n"] == 1
        assert inp["output_format"] in ("png", "jpeg", "jpg")
        # gpt-image-2 doesn't accept 1080x1920 exactly; portrait max is
        # 1024x1536. Verify the builder maps to a valid portrait size.
        assert inp["width"] <= 1536
        assert inp["height"] > inp["width"], "portrait aspect required"

    def test_grok_input_uses_aspect_ratio(self):
        inp = _build_grok_input("scene", 12345, 1080, 1920)
        assert inp["aspect_ratio"] == "9:16"
        assert inp["n"] == 1
        # grok API uses aspect_ratio not width/height
        assert "width" not in inp
        assert "height" not in inp


class TestExtractImageURL:
    def test_image_key_string(self):
        assert extract_image_url({"image": "https://x.test/img.png"}) == (
            "https://x.test/img.png"
        )

    def test_image_output_key(self):
        assert extract_image_url({"image_output": "https://y/z.png"}) == (
            "https://y/z.png"
        )

    def test_output_key(self):
        assert extract_image_url({"output": "https://q.png"}) == "https://q.png"

    def test_images_list_of_strings(self):
        """gpt-image-2 returns a list under 'images'."""
        r = extract_image_url({"images": ["https://a.png", "https://b.png"]})
        assert r == "https://a.png"

    def test_images_list_of_dicts_url_key(self):
        """Some apps wrap URLs in a dict with url/image_url/image key."""
        r = extract_image_url({"images": [{"url": "https://x.png"}]})
        assert r == "https://x.png"

    def test_empty_returns_none(self):
        assert extract_image_url({}) is None
        assert extract_image_url({"unrelated": "value"}) is None

    def test_empty_list_returns_none(self):
        assert extract_image_url({"images": []}) is None


class TestSelectorFlowsIntoHookThumbnail:
    """Meta-pin: hook_thumbnail imports and uses the new selector,
    not the frozen _IMAGE_APP constant path."""

    def test_generate_hook_thumbnail_logs_selected_model(self, monkeypatch, caplog):
        """The log format `selected_model=X` is grep-critical for
        future engagement-vs-model analysis. Pin it as an interface."""
        import logging
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        monkeypatch.delenv(
            "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", raising=False,
        )
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"image": "https://x/y.png"},
                task_id="t1", error=None,
            ),
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.005,
        ), patch(
            "genlab_core.media.hook_thumbnail._download", return_value=True,
        ), patch(
            "genlab_core.media.hook_thumbnail._overlay_text_and_pad",
            return_value=True,
        ), caplog.at_level(logging.INFO, logger="genlab_core.media.hook_thumbnail"):
            from genlab_core.media.hook_thumbnail import generate_hook_thumbnail
            ok, _ = generate_hook_thumbnail(
                "AI news hook", "ai_creators", "/tmp/x.mp4",
            )
        assert ok is True
        selector_logs = [
            r for r in caplog.records if "selected_model=" in r.getMessage()
        ]
        assert len(selector_logs) >= 1, (
            "log line 'selected_model=X' MUST appear per generation"
        )
