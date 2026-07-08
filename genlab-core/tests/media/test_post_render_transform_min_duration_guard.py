"""Pin the 2026-07-07 min-duration guard on post_render_transform.

Live-fire on 2026-07-07 caught: every render across all 5 niches
producing 13.056s output because HighlightMomentConfig.window_seconds
defaults to 8 and intro/outro concat adds only ~3s each. ValidateVideos
then rejects with ``too_short:13.1s`` (SPEC.min_duration=15.0). Result:
100% of DRAFTED blueprints stuck since transformation orchestrator
activation on 2026-07-06 (see PR #705).

The guard: after apply_transformations produces the transformed file,
probe its duration. If below SPEC.min_duration (15.0s), fall back to
the base composite. Transformation is value-add; never worth losing
publish for.

If this pin regresses we're back to every render being rejected by
validate_videos, blocking all content generation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _visuals_yaml(tmp_path):
    """Write a minimal visuals.yaml with intelligent_transform.enabled=true."""
    yaml_path = tmp_path / "visuals.yaml"
    yaml_path.write_text(
        "intelligent_transform:\n"
        "  enabled: true\n"
        "  dimensions:\n"
        "    music_mood:\n"
        "      enabled: true\n"
        "      arms: [energetic]\n"
        "    highlight_moment:\n"
        "      enabled: true\n"
        "      arms: [audio_peak]\n"
        "      window_seconds: 8\n"
    )
    return yaml_path


def _make_stub_result(applied=None, skipped=None, arm_ids=None):
    """Fake TransformationResult returned by mocked apply_transformations.

    Task #581 (2026-07-08): the real ``TransformationResult`` carries
    ``arm_ids_by_dimension`` (see
    ``genlab_core.media.transformation_orchestrator.TransformationResult``).
    Stub needs it too so ``dict(result.arm_ids_by_dimension)`` doesn't
    coerce a MagicMock into an empty dict silently (which would obscure
    the arm-attribution assertion).
    """
    result = MagicMock()
    result.stages_applied = applied or ["highlight_moment", "music_mood"]
    result.stages_skipped = skipped or []
    result.arm_ids_by_dimension = arm_ids or {
        "highlight_moment": "transform__highlight_moment__audio_peak",
        "music_mood": "transform__music_mood__energetic",
    }
    return result


class TestMinDurationGuard:
    """When transformed output is <15s, fall back to base composite."""

    @patch("genlab_core.media.post_render_transform._probe_duration_seconds")
    @patch("genlab_core.media.transformation_orchestrator.apply_transformations")
    def test_short_transformed_falls_back_to_base(
        self, mock_apply, mock_probe, tmp_path, monkeypatch, _visuals_yaml
    ):
        """13.056s output → guard skips rename → returns base composite path."""
        from genlab_core.media import post_render_transform as prt

        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        composite = tmp_path / "reel.mp4"
        composite.write_bytes(b"base-composite-bytes")
        transformed = tmp_path / "reel_transformed.mp4"

        # Simulate apply_transformations writing the transformed side-file
        def _fake_apply(**kwargs):
            transformed.write_bytes(b"transformed-13s-bytes")
            return _make_stub_result()

        mock_apply.side_effect = _fake_apply
        mock_probe.return_value = 13.056  # actual observed value on 2026-07-07

        out, arm_ids = prt.apply_post_render_transformations(
            str(composite),
            niche_id="gaming",
            niche_root=tmp_path,
            visuals_yaml_path=str(_visuals_yaml),
            blueprint_context={},
            video_duration_s=28.0,
        )

        # Guard returns base composite unchanged
        assert out == str(composite)
        # Base composite must NOT have been overwritten by the transformed
        assert composite.read_bytes() == b"base-composite-bytes"

    @patch("genlab_core.media.post_render_transform._probe_duration_seconds")
    @patch("genlab_core.media.transformation_orchestrator.apply_transformations")
    def test_long_transformed_still_replaces_base(
        self, mock_apply, mock_probe, tmp_path, monkeypatch, _visuals_yaml
    ):
        """18s output → guard passes → transformed replaces base."""
        from genlab_core.media import post_render_transform as prt

        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        composite = tmp_path / "reel.mp4"
        composite.write_bytes(b"base-composite-bytes")
        transformed = tmp_path / "reel_transformed.mp4"

        def _fake_apply(**kwargs):
            transformed.write_bytes(b"transformed-18s-bytes")
            return _make_stub_result()

        mock_apply.side_effect = _fake_apply
        mock_probe.return_value = 18.5  # well above the 15s threshold

        out, arm_ids = prt.apply_post_render_transformations(
            str(composite),
            niche_id="gaming",
            niche_root=tmp_path,
            visuals_yaml_path=str(_visuals_yaml),
            blueprint_context={},
            video_duration_s=28.0,
        )

        assert out == str(composite)
        # Base composite MUST have been overwritten
        assert composite.read_bytes() == b"transformed-18s-bytes"

    @patch("genlab_core.media.post_render_transform._probe_duration_seconds")
    @patch("genlab_core.media.transformation_orchestrator.apply_transformations")
    def test_exact_min_duration_passes(
        self, mock_apply, mock_probe, tmp_path, monkeypatch, _visuals_yaml
    ):
        """15.0s output → guard passes (strict `<` comparison)."""
        from genlab_core.media import post_render_transform as prt

        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        composite = tmp_path / "reel.mp4"
        composite.write_bytes(b"base")
        transformed = tmp_path / "reel_transformed.mp4"

        def _fake_apply(**kwargs):
            transformed.write_bytes(b"transformed-15s")
            return _make_stub_result(applied=["music_mood"])

        mock_apply.side_effect = _fake_apply
        mock_probe.return_value = 15.0

        out, arm_ids = prt.apply_post_render_transformations(
            str(composite),
            niche_id="movies",
            niche_root=tmp_path,
            visuals_yaml_path=str(_visuals_yaml),
            blueprint_context={},
            video_duration_s=25.0,
        )

        assert out == str(composite)
        assert composite.read_bytes() == b"transformed-15s"

    @patch("genlab_core.media.post_render_transform._probe_duration_seconds")
    @patch("genlab_core.media.transformation_orchestrator.apply_transformations")
    def test_probe_returns_none_treated_as_ok(
        self, mock_apply, mock_probe, tmp_path, monkeypatch, _visuals_yaml
    ):
        """ffprobe fails → probe returns None → guard's condition
        (``probe_dur is not None AND probe_dur < 15``) fails → transformed
        still replaces base. Fail-open discipline: if we can't probe, don't
        second-guess apply_transformations' output."""
        from genlab_core.media import post_render_transform as prt

        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        composite = tmp_path / "reel.mp4"
        composite.write_bytes(b"base-bytes")
        transformed = tmp_path / "reel_transformed.mp4"

        def _fake_apply(**kwargs):
            transformed.write_bytes(b"transformed-unknowable")
            return _make_stub_result(applied=["highlight_moment"])

        mock_apply.side_effect = _fake_apply
        mock_probe.return_value = None  # ffprobe failure

        out, arm_ids = prt.apply_post_render_transformations(
            str(composite),
            niche_id="anime",
            niche_root=tmp_path,
            visuals_yaml_path=str(_visuals_yaml),
            blueprint_context={},
            video_duration_s=22.0,
        )

        # None doesn't trigger the guard — transformed replaces base
        assert out == str(composite)
        assert composite.read_bytes() == b"transformed-unknowable"
