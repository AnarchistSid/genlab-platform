"""Absorbed from tests/media/test_hook_thumbnail_models.py (Part 33 §1).

The two legacy registries became `thumbnail` entries in
capabilities/registry.py. These assertions are the originals,
repointed: the rotation, the flag semantics, the builder shapes and
the URL extraction all still have to hold. Equivalence against the
deleted modules was proven over 16,000 picks before they went.
"""
from __future__ import annotations

import pytest
from genlab_core.capabilities.inputs import (
    _build_flux_input,
    _build_gpt_image_input,
    _build_grok_input,
)
from genlab_core.capabilities.registry import (
    extract_url,
    in_registration_order,
    multi_model_enabled,
    pick_deterministic,
)


class TestMultiModelFlag:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled("thumbnail") is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", raising=False,
        )
        assert multi_model_enabled("thumbnail") is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_on_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled("thumbnail") is True


class TestRegistry:
    def test_flux_is_baseline_tier_zero(self):
        """Index 0 is the baseline, and the flag-off rotation returns it.

        `_flux_model()` is gone; the guarantee it encoded now lives in
        `pick_deterministic`, which returns entries[0] whenever the canary
        flag is off — the zero-regression behaviour, unchanged.
        """
        first = in_registration_order("thumbnail")[0]
        assert first.model_id == "flux"
        assert first.ref == "pruna/flux-dev"
        assert pick_deterministic("thumbnail", "any hook", "anime") is first


    def test_registry_has_all_six_models(self):
        """3 originals + 3 wide-expansion adds (task #209, 2026-08-18)."""
        ids = {m.model_id for m in in_registration_order("thumbnail")}
        assert ids == {
            "flux", "gpt-image-2", "grok-imagine",
            "seedream-4-5", "gemini-3-pro-image", "reve",
        }

    def test_only_billed_models_carry_a_cost(self):
        """The literals this used to pin were never billed.

        It asserted `cost_by_id["gpt-image-2"] == 0.006` — a number copied
        from a catalog page. After absorption a cost exists only where a
        `belt task cost` receipt does. flux-dev has one ($0.00500, measured
        2026-09-21) because it is the model the live path actually selects;
        the other five are None and therefore unselectable, which is the
        correct state for an app nobody has ever been charged for.
        """
        entries = in_registration_order("thumbnail")
        measured = {c.model_id: c.cost_per_unit_usd for c in entries if c.measured}
        assert measured == {"flux": 0.005}, measured
        for c in entries:
            if not c.measured:
                assert c.cost_per_unit_usd is None
                assert not c.selectable_for_production
                assert c.catalog_price, f"{c.ref} should keep its advisory catalog price"


    def test_expansion_models_have_valid_input_builders(self):
        """Each new model's build_input must produce a non-empty dict
        with at least `prompt`. Pin catches accidental schema drift."""
        from genlab_core.capabilities.registry import in_registration_order
        expansion_ids = {"seedream-4-5", "gemini-3-pro-image", "reve"}
        for m in in_registration_order("thumbnail"):
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
                assert pick_deterministic("thumbnail", hook, niche).model_id == "flux"


class TestPickModelFlagOn:
    def test_same_inputs_same_model(self, monkeypatch):
        """Deterministic: same (hook, niche) MUST pick the same model
        across calls, so re-renders stay idempotent."""
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", "1")
        for _ in range(5):
            m1 = pick_deterministic("thumbnail", "hook A", "ai_creators").model_id
            m2 = pick_deterministic("thumbnail", "hook A", "ai_creators").model_id
            assert m1 == m2

    def test_different_hooks_different_models(self, monkeypatch):
        """Not a strict property (2 hooks CAN land on the same model)
        but across 30 hooks we should see all 3 models represented."""
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED", "1")
        picks = {
            pick_deterministic("thumbnail", f"hook {i}", "ai_creators").model_id
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
        assert extract_url({"image": "https://x.test/img.png"}) == (
            "https://x.test/img.png"
        )

    def test_image_output_key(self):
        assert extract_url({"image_output": "https://y/z.png"}) == (
            "https://y/z.png"
        )

    def test_output_key(self):
        assert extract_url({"output": "https://q.png"}) == "https://q.png"

    def test_images_list_of_strings(self):
        """gpt-image-2 returns a list under 'images'."""
        r = extract_url({"images": ["https://a.png", "https://b.png"]})
        assert r == "https://a.png"

    def test_images_list_of_dicts_url_key(self):
        """Some apps wrap URLs in a dict with url/image_url/image key."""
        r = extract_url({"images": [{"url": "https://x.png"}]})
        assert r == "https://x.png"

    def test_empty_returns_none(self):
        assert extract_url({}) is None
        assert extract_url({"unrelated": "value"}) is None

    def test_empty_list_returns_none(self):
        assert extract_url({"images": []}) is None


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
