"""Pin pruna_video_client_models — task #204 (2026-08-18):

Same shape as test_hook_thumbnail_models — deterministic pick,
per-model input schemas, url extraction, flag semantics.
"""
from __future__ import annotations

import pytest

from genlab_core.media.pruna_video_client_models import (
    _REGISTRY,
    _build_kling_input,
    _build_pruna_input,
    _build_wan_input,
    _pruna_model,
    extract_video_url,
    multi_model_enabled,
    pick_model,
)


class TestMultiModelFlag:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled() is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", raising=False,
        )
        assert multi_model_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_on_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", val)
        assert multi_model_enabled() is True


class TestRegistry:
    def test_pruna_is_baseline_tier_zero(self):
        assert _REGISTRY[0].model_id == "pruna-p-video"
        assert _pruna_model().model_id == "pruna-p-video"

    def test_registry_has_all_five_models(self):
        """3 originals + 2 wide-expansion adds (task #209, 2026-08-18)."""
        ids = {m.model_id for m in _REGISTRY}
        assert ids == {
            "pruna-p-video", "alibaba-wan-2-7", "kling-v2-6",
            "seedance-2-0-fast", "veo-3",
        }

    def test_registry_baseline_is_cheapest(self):
        """Diversity math sanity: registered order[0] MUST be the
        cheapest option (it's the fallback baseline when flag off —
        cheap-safe by default)."""
        for m in _REGISTRY[1:]:
            assert _REGISTRY[0].cost_per_5s_usd <= m.cost_per_5s_usd, (
                f"baseline {_REGISTRY[0].model_id} more expensive than "
                f"{m.model_id} — reorder registry"
            )

    def test_expansion_models_have_valid_input_builders(self):
        from genlab_core.media.pruna_video_client_models import _REGISTRY
        expansion_ids = {"seedance-2-0-fast", "veo-3"}
        for m in _REGISTRY:
            if m.model_id not in expansion_ids:
                continue
            inp = m.build_input(
                prompt="test", seed=42, duration_s=5,
                resolution="720p", aspect_ratio="9:16", draft=True,
            )
            assert isinstance(inp, dict) and "prompt" in inp
            assert inp["prompt"] == "test"

    def test_expansion_models_disable_auto_audio(self):
        """seedance + veo can emit their own audio track. Must be
        disabled — we overlay our own TTS voice + music beds and
        double-audio would be jarring."""
        from genlab_core.media.pruna_video_client_models import _REGISTRY
        for m in _REGISTRY:
            if m.model_id not in ("seedance-2-0-fast", "veo-3"):
                continue
            inp = m.build_input(
                prompt="test", seed=42, duration_s=5,
                resolution="720p", aspect_ratio="9:16", draft=True,
            )
            assert inp.get("generate_audio") is False, (
                f"{m.model_id} must set generate_audio=False"
            )


class TestPickModelFlagOff:
    def test_flag_off_always_returns_pruna(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", raising=False,
        )
        for prompt in ("a", "b" * 500):
            for niche in ("anime", "gaming"):
                assert pick_model(prompt, niche).model_id == "pruna-p-video"


class TestPickModelFlagOn:
    def test_same_inputs_same_model(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", "1")
        for _ in range(5):
            m1 = pick_model("anime scene A", "anime").model_id
            m2 = pick_model("anime scene A", "anime").model_id
            assert m1 == m2

    def test_different_prompts_hit_multiple_models(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED", "1")
        picks = {
            pick_model(f"scene {i}", "anime").model_id
            for i in range(30)
        }
        assert len(picks) >= 2


class TestInputBuilders:
    def test_pruna_input_carries_seed_and_draft(self):
        inp = _build_pruna_input(
            prompt="p", seed=42, duration_s=5,
            resolution="720p", aspect_ratio="9:16", draft=True,
        )
        assert inp["prompt"] == "p"
        assert inp["seed"] == 42
        assert inp["draft"] is True
        assert inp["duration"] == 5
        assert inp["resolution"] == "720p"
        assert inp["aspect_ratio"] == "9:16"

    def test_wan_input_uppercases_resolution(self):
        """Wan expects 720P/1080P (uppercase). Verify the builder maps."""
        inp = _build_wan_input(
            prompt="p", seed=1, duration_s=5,
            resolution="720p", aspect_ratio="9:16", draft=True,
        )
        assert inp["resolution"] == "720P"
        assert inp["watermark"] is False
        # Wan doesn't accept aspect_ratio
        assert "aspect_ratio" not in inp
        # Wan doesn't accept draft
        assert "draft" not in inp

    def test_kling_input_clamps_duration_to_5_or_10(self):
        """Kling only accepts 5s or 10s. Verify shorter → 5s, longer → 10s."""
        short = _build_kling_input(
            prompt="p", seed=1, duration_s=3,
            resolution="720p", aspect_ratio="9:16", draft=True,
        )
        assert short["duration"] == 5

        long = _build_kling_input(
            prompt="p", seed=1, duration_s=8,
            resolution="720p", aspect_ratio="9:16", draft=True,
        )
        assert long["duration"] == 10

    def test_kling_input_sound_disabled(self):
        """Sound must default off — we overlay our own TTS audio later
        and Kling's generated sound would clash."""
        inp = _build_kling_input(
            prompt="p", seed=1, duration_s=5,
            resolution="720p", aspect_ratio="9:16", draft=True,
        )
        assert inp["sound"] is False


class TestExtractVideoURL:
    def test_video_string(self):
        assert extract_video_url({"video": "https://x/y.mp4"}) == "https://x/y.mp4"

    def test_video_output_key(self):
        assert extract_video_url({"video_output": "https://q.mp4"}) == "https://q.mp4"

    def test_output_key(self):
        assert extract_video_url({"output": "https://r.mp4"}) == "https://r.mp4"

    def test_videos_list_string(self):
        r = extract_video_url({"videos": ["https://a.mp4", "https://b.mp4"]})
        assert r == "https://a.mp4"

    def test_videos_list_dict_url_key(self):
        r = extract_video_url({"videos": [{"url": "https://x.mp4"}]})
        assert r == "https://x.mp4"

    def test_empty_returns_none(self):
        assert extract_video_url({}) is None
        assert extract_video_url({"unrelated": "x"}) is None
