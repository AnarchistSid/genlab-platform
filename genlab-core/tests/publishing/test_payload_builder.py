"""Tests for :mod:`genlab_core.publishing.payload_builder`.

Lives next to its module home (paralleling the PR 3/N extraction in the
publish_all_platforms decomposition). The existing
``test_publish_all_platforms.py`` already exercises ``build_payload``
extensively via the integration path; this file focuses on the small
helpers (``parse_visual_paths``, ``parse_json_field``,
``build_platform_specific``) and the regression-prone branches in
``build_payload`` that the integration tests don't isolate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.platforms.models import (
    FacebookSpecific,
    InstagramSpecific,
    PublishPayload,
    ThreadsSpecific,
    TwitterSpecific,
    YouTubeSpecific,
)
from genlab_core.publishing.payload_builder import (
    build_payload,
    build_platform_specific,
    parse_json_field,
    parse_visual_paths,
)

# ---------------------------------------------------------------------------
# parse_visual_paths
# ---------------------------------------------------------------------------


class TestParseVisualPaths:
    def test_json_string_decodes(self) -> None:
        assert parse_visual_paths({"visual_paths": '["a.mp4", "b.png"]'}) == ["a.mp4", "b.png"]

    def test_list_passes_through(self) -> None:
        assert parse_visual_paths({"visual_paths": ["a.mp4", "b.png"]}) == ["a.mp4", "b.png"]

    def test_missing_field_returns_empty_list(self) -> None:
        assert parse_visual_paths({}) == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_visual_paths({"visual_paths": ""}) == []

    def test_none_value_returns_empty_list(self) -> None:
        assert parse_visual_paths({"visual_paths": None}) == []

    def test_malformed_json_returns_empty_list(self) -> None:
        """Silent ``[]`` on malformed input is intentional — the caller
        treats missing media as a fatal condition further down, so this
        defaulting doesn't mask the failure."""
        assert parse_visual_paths({"visual_paths": "not json"}) == []


# ---------------------------------------------------------------------------
# parse_json_field
# ---------------------------------------------------------------------------


class TestParseJsonField:
    def test_empty_string_returns_empty_dict(self) -> None:
        assert parse_json_field("") == {}

    def test_none_returns_empty_dict(self) -> None:
        assert parse_json_field(None) == {}

    def test_json_string_decodes(self) -> None:
        assert parse_json_field('{"a": 1}') == {"a": 1}

    def test_dict_passes_through(self) -> None:
        assert parse_json_field({"a": 1}) == {"a": 1}

    def test_malformed_returns_empty_dict(self) -> None:
        """Used for legacy youtube_content/twitter_content columns — silent
        ``{}`` lets the caller fall through to the modern per-platform
        defaults rather than aborting on a historical bad row."""
        assert parse_json_field("not json") == {}


# ---------------------------------------------------------------------------
# build_platform_specific — one branch per platform + unknown
# ---------------------------------------------------------------------------


class TestBuildPlatformSpecific:
    def test_facebook_returns_empty_facebook_specific(self) -> None:
        assert isinstance(build_platform_specific({}, "facebook", "", ""), FacebookSpecific)

    def test_threads_returns_empty_threads_specific(self) -> None:
        assert isinstance(build_platform_specific({}, "threads", "", ""), ThreadsSpecific)

    def test_unknown_platform_returns_none(self) -> None:
        """Unknown platforms get ``None``; the strict clients reject it,
        the loose ones accept it. No silent default to FB/Threads."""
        assert build_platform_specific({}, "myspace", "", "") is None

    def test_instagram_picks_first_image_as_cover(self) -> None:
        fields = {"visual_paths": '["a.png", "b.png", "c.mp4"]'}
        result = build_platform_specific(fields, "instagram", "cap", "hook")
        assert isinstance(result, InstagramSpecific)
        assert result.cover_url == "a.png"
        # mp4 entries are filtered out of cover candidates.
        assert "c.mp4" not in result.cover_url
        assert result.share_to_feed is True

    def test_instagram_no_images_empty_cover(self) -> None:
        """All-mp4 visual_paths means no image candidate; cover_url
        falls back to empty string rather than crashing on index[0]."""
        fields = {"visual_paths": '["a.mp4"]'}
        result = build_platform_specific(fields, "instagram", "cap", "hook")
        assert result.cover_url == ""

    def test_youtube_title_from_hook(self) -> None:
        result = build_platform_specific(
            {"youtube_content": "Some description text"},
            "youtube",
            "cap",
            "Catchy Hook Here",
        )
        assert isinstance(result, YouTubeSpecific)
        assert result.shorts_title == "Catchy Hook Here"
        assert result.community_post_text == "Some description text"

    def test_youtube_legacy_json_dict_supported(self) -> None:
        """Historical rows store youtube_content as JSON ``{title, description}``
        — build_platform_specific must still extract both. Without this
        branch, every pre-Sprint-X blueprint would publish with empty
        description."""
        fields = {"youtube_content": '{"title": "Legacy Title", "description": "Legacy desc"}'}
        result = build_platform_specific(fields, "youtube", "cap", "")
        # Hook is empty, so title falls through to legacy.
        assert result.shorts_title == "Legacy Title"
        assert result.community_post_text == "Legacy desc"

    def test_youtube_shorts_title_truncated_to_100(self) -> None:
        long_hook = "x" * 200
        result = build_platform_specific({}, "youtube", "", long_hook)
        assert len(result.shorts_title) == 100

    def test_twitter_single_routing_default(self) -> None:
        fields = {"twitter_content": '{"tweet_text": "Hello world"}'}
        result = build_platform_specific(fields, "twitter", "cap", "")
        assert isinstance(result, TwitterSpecific)
        assert result.routing == "single"
        # PR #567 (2026-06-25): AI disclosure auto-appended for twitter
        # (budget-aware truncation in apply_ai_disclosure). The original
        # tweet_text is preserved + ' AI-assisted. #ai' suffix added.
        assert result.tweet_text.startswith("Hello world")
        assert result.tweet_text.endswith("AI-assisted. #ai")

    def test_twitter_thread_routing_via_routing_key(self) -> None:
        fields = {
            "twitter_content": (
                '{"routing": "thread", "tweet_text": "Lead", "thread_tweets": ["a", "b"]}'
            )
        }
        result = build_platform_specific(fields, "twitter", "cap", "")
        assert result.routing == "thread"
        assert result.thread_tweets == ["a", "b"]

    def test_twitter_thread_routing_via_legacy_strategy_key(self) -> None:
        """Older rows use ``strategy`` instead of ``routing`` for the
        single/thread switch. Both keys must work; otherwise pre-Sprint-X
        blueprints publish as singles (silent thread loss)."""
        fields = {"twitter_content": '{"strategy": "thread", "tweet_text": "Lead"}'}
        result = build_platform_specific(fields, "twitter", "cap", "")
        assert result.routing == "thread"

    def test_twitter_tweet_text_truncated_to_280(self) -> None:
        long = "x" * 500
        fields = {"twitter_content": "{}"}  # noqa: Q000 — JSON string, single quotes intentional
        result = build_platform_specific(fields, "twitter", long, "")
        assert len(result.tweet_text) == 280

    def test_twitter_unknown_routing_defaults_to_single(self) -> None:
        """An unknown routing value must NOT silently become "thread" —
        publishing a single-tweet payload as a thread mid-flight would
        confuse the X client and likely 4xx mid-thread."""
        fields = {"twitter_content": '{"routing": "carousel", "tweet_text": "Hi"}'}
        result = build_platform_specific(fields, "twitter", "cap", "")
        assert result.routing == "single"


# ---------------------------------------------------------------------------
# build_payload — focused regression branches
# ---------------------------------------------------------------------------


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    """A real on-disk mp4 (well, fake bytes — size > 10KB so the filter
    accepts it) so build_payload's existence + size checks pass."""
    f = tmp_path / "real.mp4"
    f.write_bytes(b"x" * 20_000)
    return f


def _patch_transcode_passthrough():
    """Patch transcode_for_platform to return the source path unchanged
    so the test doesn't actually invoke ffmpeg."""
    return patch(
        "genlab_core.publishing.payload_builder.transcode_for_platform",
        side_effect=lambda p, _platform: p,
    )


class TestBuildPayloadMediaValidation:
    def test_missing_files_are_filtered(self, tmp_path: Path) -> None:
        """Files that don't exist on disk get filtered out before publish.
        Otherwise the platform client would 4xx on missing-media uploads."""
        real = tmp_path / "real.mp4"
        real.write_bytes(b"x" * 20_000)
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{real}", "{tmp_path}/ghost.mp4"]',
                    "format": "reel",
                },
                "instagram",
            )
        assert payload.media_paths == [real]

    def test_tiny_files_are_filtered(self, tmp_path: Path) -> None:
        tiny = tmp_path / "tiny.mp4"
        tiny.write_bytes(b"x" * 100)  # below 10KB threshold
        big = tmp_path / "big.mp4"
        big.write_bytes(b"x" * 20_000)
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{tiny}", "{big}"]',
                    "format": "reel",
                },
                "instagram",
            )
        assert tiny not in payload.media_paths
        assert big in payload.media_paths

    def test_video_format_with_no_media_raises(self, tmp_path: Path) -> None:
        """Without this guard the publisher would post nothing and report
        success — exactly the silent-fail mode the docstring warns against."""
        with pytest.raises(ValueError, match="No valid media files for video publish"):
            build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": "[]",
                    "format": "video",
                },
                "instagram",
            )


class TestBuildPayloadCaptionSelection:
    # PR #567 (2026-06-25): build_payload now appends platform AI
    # disclosure to the final caption (YouTube/Meta/TikTok 2024
    # policy compliance). These tests pin the SOURCE caption is
    # selected correctly + the disclosure SUFFIX is appended. The
    # disclosure itself is unit-tested in compliance/test_disclosure_apply.

    def test_threads_uses_threads_content_when_present(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "caption": "generic caption",
                    "threads_content": "threads-specific caption",
                },
                "threads",
            )
        # Source caption selected from threads_content + disclosure appended
        assert payload.caption.startswith("threads-specific caption")
        assert "AI-assisted" in payload.caption

    def test_facebook_uses_facebook_content_when_present(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "caption": "generic caption",
                    "facebook_content": "fb-specific caption",
                },
                "facebook",
            )
        assert payload.caption.startswith("fb-specific caption")
        assert "AI-assisted" in payload.caption

    def test_threads_falls_back_to_caption_when_empty(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "caption": "generic caption",
                    "threads_content": "",
                },
                "threads",
            )
        assert payload.caption.startswith("generic caption")
        assert "AI-assisted" in payload.caption


class TestBuildPayloadHashtags:
    def test_string_hashtags_split_on_whitespace(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "hashtags": "gaming esports clutch",
                },
                "instagram",
            )
        assert payload.hashtags == ["#gaming", "#esports", "#clutch"]

    def test_list_hashtags_preserved(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "hashtags": ["gaming", "#esports"],
                },
                "instagram",
            )
        assert payload.hashtags == ["#gaming", "#esports"]

    def test_existing_hash_prefix_not_doubled(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "hashtags": "#gaming #esports",
                },
                "instagram",
            )
        assert payload.hashtags == ["#gaming", "#esports"]
        assert all(not t.startswith("##") for t in payload.hashtags)


class TestBuildPayloadAffiliateFirstComment:
    """R-46 regression: ``twitter`` and ``x_twitter`` MUST both route to the
    same affiliate self-reply field. The old code only matched ``x_twitter``
    and silently dropped the entire X affiliate payload because the default
    platform list uses ``twitter``."""

    def test_twitter_legacy_name_resolves_first_comment(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "twitter_first_comment": "🔗 affiliate",
                },
                "twitter",
            )
        assert payload.first_comment_text == "🔗 affiliate"

    def test_x_twitter_canonical_name_resolves_first_comment(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "twitter_first_comment": "🔗 affiliate",
                },
                "x_twitter",
            )
        assert payload.first_comment_text == "🔗 affiliate"

    def test_facebook_uses_facebook_first_comment(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "facebook_first_comment": "🔗 fb affiliate",
                },
                "facebook",
            )
        assert payload.first_comment_text == "🔗 fb affiliate"

    def test_instagram_has_no_first_comment(self, video_file: Path) -> None:
        """IG affiliate flow uses pinned-comment-after-publish (handled
        elsewhere), not the first_comment_text field. Setting first_comment_text
        for IG would cause double-post."""
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "twitter_first_comment": "🔗 affiliate",
                    "facebook_first_comment": "🔗 fb affiliate",
                },
                "instagram",
            )
        assert payload.first_comment_text == ""


class TestBuildPayloadTranscodeDispatch:
    """``build_payload`` must call ``transcode_for_platform`` once per mp4
    in media_paths — that's how each platform gets the right ffmpeg variant.
    Non-mp4 paths skip transcode entirely."""

    def test_mp4_files_go_through_transcode(self, tmp_path: Path) -> None:
        mp4 = tmp_path / "a.mp4"
        mp4.write_bytes(b"x" * 20_000)
        png = tmp_path / "b.png"
        png.write_bytes(b"x" * 20_000)
        with patch(
            "genlab_core.publishing.payload_builder.transcode_for_platform",
            side_effect=lambda p, _platform: p,
        ) as mock_transcode:
            build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{mp4}", "{png}"]',
                    "format": "reel",
                },
                "instagram",
            )
        # transcode called once (mp4 only), not twice — png is filtered out.
        assert mock_transcode.call_count == 1
        assert mock_transcode.call_args.args == (mp4, "instagram")


class TestBuildPayloadInvalidNiche:
    def test_unknown_niche_raises(self, video_file: Path) -> None:
        """build_payload validates the niche via niche_validation —
        unknown niches must hard-fail at the entry to the publish path,
        not silently default to ai_creators (the cross-channel guard
        Sprint 62 was built around)."""
        with pytest.raises(ValueError, match="unknown niche_id 'fashion'"):
            build_payload(
                {
                    "niche_id": "fashion",
                    "visual_paths": f'["{video_file}"]',
                },
                "instagram",
            )


class TestBuildPayloadReturnsPublishPayload:
    def test_return_type(self, video_file: Path) -> None:
        with _patch_transcode_passthrough():
            payload = build_payload(
                {
                    "niche_id": "gaming",
                    "visual_paths": f'["{video_file}"]',
                    "format": "reel",
                },
                "instagram",
            )
        assert isinstance(payload, PublishPayload)
