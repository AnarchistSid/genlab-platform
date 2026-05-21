"""Tests for genlab_core.media.egress_policies."""

from pathlib import Path

from genlab_core.media.egress_policies import (
    _BUILTIN_POLICIES,
    get_egress_allow,
)


class TestBuiltinPolicies:
    def test_render_is_empty(self):
        assert get_egress_allow("render") == []

    def test_unknown_stage_is_empty(self):
        assert get_egress_allow("nonexistent_stage") == []

    def test_publish_has_platform_domains(self):
        allowed = get_egress_allow("publish")
        assert "graph.facebook.com" in allowed
        assert "oauth2.googleapis.com" in allowed

    def test_fetch_gaming_has_twitch_and_steam(self):
        allowed = get_egress_allow("fetch_gaming")
        assert "api.twitch.tv" in allowed
        assert "store.steampowered.com" in allowed

    def test_tts_has_elevenlabs_and_openai(self):
        allowed = get_egress_allow("tts")
        assert "api.elevenlabs.io" in allowed
        assert "api.openai.com" in allowed

    def test_all_builtin_stages_return_lists(self):
        for stage in _BUILTIN_POLICIES:
            result = get_egress_allow(stage)
            assert isinstance(result, list), f"{stage} returned {type(result)}"


class TestYamlOverlay:
    def test_overlay_adds_domains(self, tmp_path: Path):
        yaml_file = tmp_path / "egress_policies.yaml"
        yaml_file.write_text("render:\n  - custom.example.com\nfetch_gaming:\n  - extra.api.com\n")
        # Reset module cache
        import genlab_core.media.egress_policies as mod

        mod._yaml_overlay = None

        allowed = get_egress_allow("render", config_path=yaml_file)
        assert "custom.example.com" in allowed

        # fetch_gaming should have both builtin + overlay
        gaming = get_egress_allow("fetch_gaming", config_path=yaml_file)
        assert "api.twitch.tv" in gaming  # builtin
        assert "extra.api.com" in gaming  # overlay

        mod._yaml_overlay = None  # cleanup

    def test_overlay_deduplicates(self, tmp_path: Path):
        yaml_file = tmp_path / "egress_policies.yaml"
        yaml_file.write_text(
            "publish:\n  - graph.facebook.com\n"  # already in builtin
        )
        import genlab_core.media.egress_policies as mod

        mod._yaml_overlay = None

        allowed = get_egress_allow("publish", config_path=yaml_file)
        assert allowed.count("graph.facebook.com") == 1

        mod._yaml_overlay = None

    def test_missing_yaml_is_fine(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.yaml"
        import genlab_core.media.egress_policies as mod

        mod._yaml_overlay = None

        allowed = get_egress_allow("render", config_path=missing)
        assert allowed == []

        mod._yaml_overlay = None
