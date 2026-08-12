"""2026-08-12: pin that gaming's clip_sourcer duration floor is aligned
with the render pipeline's validate_videos SPEC.min_duration.

Motivating incident: Rainbow Six Siege blueprint c72737f2 (2026-08-12
01:24 IST). Trace:

* thin-context fix (b46ef2de) unblocked gaming's writer
* Twitch clip source returned a 6.9s Rainbow Six Siege clip
* clip_sourcer.min_duration_seconds default was 5 -> clip passed
* RenderGamingVideo ran (36s of wasted work) -> produced 6.9s MP4
* ValidateVideos hard-rejected: `SPEC.min_duration = 15.0`
* blueprint stuck at DRAFTED forever

Root cause: two duration floors for the same "min clip length"
constraint had drifted out of alignment (5 vs 15). The core Twitch
fetcher (`fetch_twitch_clips._MIN_CLIP_DURATION_SECONDS = 15`)
correctly matched validate_videos SPEC, but the niche-specific
`clip_sourcer.ClipSourcerConfig.min_duration_seconds` defaulted to 5.

This pin ensures any future drift between these two floors is caught
at CI, not at a 5-day-later "gaming is publishing nothing again" audit.
"""

from __future__ import annotations


class TestClipSourcerDurationAlignment:
    def test_clip_sourcer_default_matches_render_pipeline_floor(self):
        """The clip_sourcer default must be >= validate_videos SPEC.min_duration.

        A lower value wastes render work on clips that will hard-fail
        at ValidateVideos. Sibling test at
        `genlab-core/tests/test_fetch_twitch_clips_min_duration_filter.py
        ::test_default_matches_validate_videos_spec` pins the same
        contract for the core Twitch fetcher.
        """
        from genlab_core.pipeline.stages.validate_videos import SPEC
        from niches.gaming.tools.clip_sourcer import ClipSourcerConfig

        spec_floor = float(SPEC["min_duration"])
        default_config = ClipSourcerConfig()

        assert default_config.min_duration_seconds >= spec_floor, (
            f"clip_sourcer default min_duration_seconds="
            f"{default_config.min_duration_seconds} is below "
            f"validate_videos.SPEC.min_duration={spec_floor}. "
            "Clips that pass this filter will still hard-fail at "
            "ValidateVideos, wasting a render pass. Raise the default."
        )

    def test_gaming_sources_yaml_min_duration_matches_spec(self):
        """The gaming niche's sources.yaml override MUST also be >=
        SPEC.min_duration. This pins the explicit config value so a
        careless edit of the yaml doesn't silently break the loop
        even if the module default is still correct."""
        from pathlib import Path

        import yaml

        from genlab_core.pipeline.stages.validate_videos import SPEC

        yaml_path = (
            Path(__file__).resolve().parents[2]
            / "niches"
            / "gaming"
            / "config"
            / "sources.yaml"
        )
        assert yaml_path.exists(), f"missing config: {yaml_path}"
        cfg = yaml.safe_load(yaml_path.read_text())
        clip_cfg = cfg.get("clip_sourcer", {})
        yaml_floor = clip_cfg.get("min_duration_seconds")

        assert yaml_floor is not None, (
            "sources.yaml clip_sourcer.min_duration_seconds missing "
            "— add it back with value >= validate_videos.SPEC.min_duration"
        )
        assert float(yaml_floor) >= float(SPEC["min_duration"]), (
            f"sources.yaml sets clip_sourcer.min_duration_seconds="
            f"{yaml_floor} but validate_videos.SPEC.min_duration="
            f"{SPEC['min_duration']}. Clips below the SPEC floor will "
            "hard-fail at ValidateVideos even if this filter passes them."
        )
