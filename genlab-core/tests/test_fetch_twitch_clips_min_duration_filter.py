"""Regression test for the Twitch clip min-duration filter.

Background — 2026-07-15:
    Investigating a ``stale_drafted`` health-monitor warning surfaced
    two gaming blueprints stuck at DRAFTED for 1-6 days, both failing
    with ``render:validation_failed:too_short:5.0s``. Root cause: the
    Twitch fetcher didn't filter by clip duration, so 5-second Twitch
    clips (Sheepy, Granny) flowed through fetch → score → filter →
    blueprint compose → render → validate_videos, only to be rejected
    at the 15-second SPEC.min_duration floor.

    Wasted work per short clip: ~1 render pass + a permanently-stuck
    DRAFTED row (retries forever, feeds no signal to the bandit,
    consumes health-monitor attention).

Fix: reject at ingestion. ``_filter_clips_by_min_duration`` is called
    inside ``FetchTwitchClips.execute`` right after the API pull.

Pins:
    - clips shorter than the floor are dropped
    - clips >= the floor are kept, in order
    - missing/None ``duration`` treated as 0 → dropped (defensive)
    - the default floor equals validate_videos.SPEC.min_duration
"""

from __future__ import annotations

from genlab_core.pipeline.stages.fetch_twitch_clips import (
    _MIN_CLIP_DURATION_SECONDS,
    _filter_clips_by_min_duration,
)


class TestFilterClipsByMinDuration:
    def test_below_floor_dropped(self):
        """Sheepy + Granny 5s clips would fail at validate_videos → drop early."""
        clips = [
            {"clip_url": "sheepy", "duration": 5.0, "title": "Sheepy"},
            {"clip_url": "granny", "duration": 5.072, "title": "Granny"},
            {"clip_url": "fortnite", "duration": 60.0, "title": "Fortnite clutch"},
        ]
        kept, dropped = _filter_clips_by_min_duration(clips)
        assert len(kept) == 1
        assert kept[0]["clip_url"] == "fortnite"
        assert sorted(dropped) == [5.0, 5.072]

    def test_exactly_at_floor_kept(self):
        """15.0s exactly meets the platform floor — must be kept.

        SPEC.min_duration is 15.0 and the comparison is ``>= 15.0``.
        A 15s clip renders to exactly 15s and passes validate_videos.
        """
        clips = [{"clip_url": "boundary", "duration": 15.0, "title": "border"}]
        kept, dropped = _filter_clips_by_min_duration(clips)
        assert kept == clips
        assert dropped == []

    def test_missing_duration_treated_as_zero(self):
        """A clip without a duration key is treated as 0 → dropped.

        Rationale: unknown-duration is more likely a scraper edge-case
        than a real short clip we want to preserve. Fail-safe: drop.
        """
        clips = [{"clip_url": "unknown", "title": "no duration key"}]
        kept, dropped = _filter_clips_by_min_duration(clips)
        assert kept == []
        assert dropped == [0.0]

    def test_none_duration_treated_as_zero(self):
        """duration=None must not raise TypeError; treated as 0."""
        clips = [{"clip_url": "null_dur", "duration": None, "title": "n"}]
        kept, dropped = _filter_clips_by_min_duration(clips)
        assert kept == []
        assert dropped == [0.0]

    def test_order_preserved(self):
        """The Twitch pipeline sorts later by view_count — but until then,
        the ingestion order should be preserved so downstream code doesn't
        get surprised by a reshuffle inside a filter helper.
        """
        clips = [
            {"clip_url": "a", "duration": 30.0},
            {"clip_url": "short_b", "duration": 4.0},
            {"clip_url": "c", "duration": 45.0},
            {"clip_url": "d", "duration": 60.0},
        ]
        kept, _ = _filter_clips_by_min_duration(clips)
        assert [c["clip_url"] for c in kept] == ["a", "c", "d"]

    def test_configurable_threshold(self):
        """A caller can pass a stricter threshold (e.g. 20s for extra
        compositor headroom). The default is the platform floor, but
        an operator tuning via sources.yaml `twitch_clips.min_clip_
        duration_seconds` should be able to raise it.
        """
        clips = [
            {"clip_url": "short", "duration": 16.0},
            {"clip_url": "long", "duration": 45.0},
        ]
        # Default (15.0) keeps both
        kept_default, _ = _filter_clips_by_min_duration(clips)
        assert len(kept_default) == 2
        # Stricter (20.0) keeps only the long one
        kept_strict, dropped_strict = _filter_clips_by_min_duration(
            clips, min_duration_seconds=20.0
        )
        assert [c["clip_url"] for c in kept_strict] == ["long"]
        assert dropped_strict == [16.0]

    def test_default_matches_validate_videos_spec(self):
        """Pin: the fetcher's default MUST equal ``validate_videos.SPEC.min_duration``.

        If a future PR raises SPEC.min_duration but forgets to update
        the fetcher default, short clips would slip through and fail
        the same way Sheepy + Granny did. This pin fails loud in that
        case, forcing both edits to land in the same PR.
        """
        from genlab_core.pipeline.stages.validate_videos import SPEC

        assert _MIN_CLIP_DURATION_SECONDS == SPEC["min_duration"], (
            "Twitch fetcher's min-duration default must equal "
            "validate_videos.SPEC.min_duration. If you raised SPEC.min_duration, "
            "update _MIN_CLIP_DURATION_SECONDS in fetch_twitch_clips.py to match."
        )
