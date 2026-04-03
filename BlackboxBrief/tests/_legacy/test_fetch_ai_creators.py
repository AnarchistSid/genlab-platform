"""Unit tests for fetch_ai_creators.

Covers: creator-only source loading, connector dispatch routing,
and creator showcase entry filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import yaml

import execution.fetch_ai_creators as fetch_mod
from execution.fetch_ai_creators import (
    _apply_entry_filters,
    _is_channel_or_profile_url,
    _is_creator_showcase_entry,
    load_sources,
)

# ── Creator-only source loading ──────────────────────────────

class TestLoadSourcesCreatorMode:
    def test_creator_only_filters_to_showcase(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": True,
            "sources": [
                {"url": "https://example.com/showcase-1", "enabled": True, "content_type": "showcase"},
                {"url": "https://example.com/news-1", "enabled": True},
                {"url": "https://example.com/showcase-2", "enabled": True, "content_type": "showcase"},
                {"url": "https://example.com/showcase-disabled", "enabled": False, "content_type": "showcase"},
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        urls = [s["url"] for s in loaded]
        assert urls == [
            "https://example.com/showcase-1",
            "https://example.com/showcase-2",
        ]

    def test_non_creator_mode_keeps_all_enabled(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": False,
            "sources": [
                {"url": "https://example.com/showcase-1", "enabled": True, "content_type": "showcase"},
                {"url": "https://example.com/news-1", "enabled": True},
                {"url": "https://example.com/disabled", "enabled": False},
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        urls = [s["url"] for s in loaded]
        assert urls == [
            "https://example.com/showcase-1",
            "https://example.com/news-1",
        ]

    def test_creator_video_only_marks_sources(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": True,
            "creator_video_only_mode": True,
            "sources": [
                {"url": "https://example.com/showcase-1", "enabled": True, "content_type": "showcase"},
                {"url": "https://example.com/news-1", "enabled": True},
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        assert len(loaded) == 1
        assert loaded[0]["url"] == "https://example.com/showcase-1"
        assert loaded[0]["video_only_mode"] is True

    def test_creator_media_only_marks_sources(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": True,
            "creator_media_only_mode": True,
            "sources": [
                {"url": "https://example.com/showcase-1", "enabled": True, "content_type": "showcase"},
                {"url": "https://example.com/news-1", "enabled": True},
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        assert len(loaded) == 1
        assert loaded[0]["url"] == "https://example.com/showcase-1"
        assert loaded[0]["media_only_mode"] is True

    def test_creator_direct_only_keeps_explicit_creator_sources(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": True,
            "creator_direct_only_mode": True,
            "sources": [
                {
                    "url": "https://example.com/direct-creator",
                    "enabled": True,
                    "content_type": "showcase",
                    "creator_source": True,
                },
                {
                    "url": "https://example.com/aggregator-showcase",
                    "enabled": True,
                    "content_type": "showcase",
                    "creator_source": False,
                },
                {
                    "url": "https://example.com/legacy-showcase",
                    "enabled": True,
                    "content_type": "showcase",
                },
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        urls = [s["url"] for s in loaded]
        assert urls == ["https://example.com/direct-creator"]

    def test_creator_lookback_days_applies_to_creator_sources(self, tmp_path: Path):
        cfg_path = tmp_path / "sources.yaml"
        cfg = {
            "creator_only_mode": True,
            "creator_video_only_mode": True,
            "creator_lookback_days": 60,
            "sources": [
                {
                    "url": "https://example.com/direct-creator",
                    "enabled": True,
                    "content_type": "showcase",
                    "creator_source": True,
                },
            ],
        }
        cfg_path.write_text(yaml.safe_dump(cfg))
        loaded = load_sources(cfg_path)
        assert len(loaded) == 1
        assert loaded[0]["video_only_mode"] is True
        assert loaded[0]["creator_lookback_days"] == 60


# ── Connector dispatch ───────────────────────────────────────

class _NoopLimiter:
    def wait(self, _url: str) -> float:
        return 0.0


class TestConnectorDispatch:
    def test_fetch_single_source_uses_connector(self, monkeypatch) -> None:
        called: Dict[str, Any] = {}

        def _fake_fetch_via_connector(src: Dict[str, Any]) -> Dict[str, Any]:
            called["url"] = src["url"]
            return {
                "url": src["url"],
                "fetch_method": "connector:youtube",
                "fetch_status": "success",
                "entry_count": 1,
                "entries": [
                    {
                        "title": "AI demo",
                        "link": "https://youtube.com/watch?v=abc",
                        "summary": "Media: https://img.youtube.com/vi/abc/hqdefault.jpg",
                        "published": "2026-02-20T00:00:00+00:00",
                        "author": "creator",
                    }
                ],
            }

        monkeypatch.setattr(fetch_mod, "fetch_via_connector", _fake_fetch_via_connector)

        src = {
            "source_id": "SRC_YT_TEST",
            "platform": "youtube",
            "url": "https://www.googleapis.com/youtube/v3/search",
            "type": "api",
            "connector": "youtube",
            "content_type": "showcase",
            "media_only_mode": True,
            "media_required": True,
            "priority": 0.9,
        }

        result = fetch_mod._fetch_single_source(
            src=src,
            cache=None,
            cache_ttl=24,
            limiter=_NoopLimiter(),
        )

        assert called["url"] == src["url"]
        assert result["source_id"] == "SRC_YT_TEST"
        assert result["platform"] == "youtube"
        assert result["fetch_status"] == "success"
        assert result["entry_count"] == 1
        assert result["items_count_raw"] == 1
        assert result["items_count_filtered"] == 1

    def test_fetch_single_source_classifies_connector_errors(self, monkeypatch) -> None:
        def _fake_fetch_via_connector(src: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "url": src["url"],
                "entry_count": 0,
                "entries": [],
                "fetch_method": "connector:x",
                "error": "429 Too Many Requests",
            }

        monkeypatch.setattr(fetch_mod, "fetch_via_connector", _fake_fetch_via_connector)

        src = {
            "url": "https://api.x.com/2/tweets/search/recent",
            "type": "api",
            "connector": "x",
            "content_type": "showcase",
            "media_only_mode": True,
            "media_required": True,
        }

        result = fetch_mod._fetch_single_source(
            src=src,
            cache=None,
            cache_ttl=24,
            limiter=_NoopLimiter(),
        )

        assert result["fetch_status"] == "rate_limited"
        assert result["entry_count"] == 0


# ── Creator showcase entry filtering ─────────────────────────

class TestCreatorShowcaseFiltering:
    def test_keeps_media_backed_showcase(self):
        entry = {
            "title": "Guardian Nadia (AI-generated)",
            "summary": '<img src="https://preview.redd.it/abc123.png" />',
            "link": "https://www.reddit.com/r/aiArt/comments/example/guardian_nadia/",
        }
        assert _is_creator_showcase_entry(entry) is True

    def test_drops_discussion_without_showcase_signal(self):
        entry = {
            "title": '[Discussion] Midjourney is getting too "perfect" and losing soul?',
            "summary": "General discussion thread with no media preview.",
            "link": "https://www.reddit.com/r/midjourney/comments/example/discussion/",
        }
        assert _is_creator_showcase_entry(entry) is False

    def test_showcase_keeps_creator_posts_only(self):
        src = {
            "url": "https://www.reddit.com/r/aiArt/top/.rss?t=day",
            "content_type": "showcase",
            "ai_filter": False,
        }
        result = {
            "entries": [
                {
                    "title": "The Koshkas in their Dystopian World",
                    "summary": '<img src="https://preview.redd.it/koshkas.png" />',
                    "link": "https://www.reddit.com/r/aiArt/comments/example1/",
                },
                {
                    "title": "[Discussion] Is Midjourney losing its soul?",
                    "summary": "No media, discussion only.",
                    "link": "https://www.reddit.com/r/midjourney/comments/example2/",
                },
                {
                    "title": "Need help with prompts?",
                    "summary": "Question thread.",
                    "link": "https://www.reddit.com/r/aiArt/comments/example3/",
                },
            ],
            "entry_count": 3,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert [e["title"] for e in result["entries"]] == [
            "The Koshkas in their Dystopian World",
        ]

    def test_ai_filter_runs_before_showcase_filter(self):
        src = {
            "url": "https://example.com/showcase-feed",
            "content_type": "showcase",
            "ai_filter": True,
        }
        result = {
            "entries": [
                {
                    "title": "AI marble sculpture made with Midjourney",
                    "summary": '<img src="https://preview.redd.it/marble.png" />',
                    "link": "https://example.com/a1",
                },
                {
                    "title": "Weekend gardening photos",
                    "summary": '<img src="https://example.com/garden.png" />',
                    "link": "https://example.com/a2",
                },
            ],
            "entry_count": 2,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "AI marble sculpture made with Midjourney"

    def test_video_only_mode_requires_video_hints(self):
        src = {
            "url": "https://www.reddit.com/r/aiArt/top/.rss?t=day",
            "content_type": "showcase",
            "video_only_mode": True,
        }
        result = {
            "entries": [
                {
                    "title": "Image-only showcase",
                    "summary": '<img src="https://preview.redd.it/image_only.png" />',
                    "link": "https://www.reddit.com/r/aiArt/comments/example_img/",
                },
                {
                    "title": "Video showcase",
                    "summary": '<video src="https://v.redd.it/example_video"></video>',
                    "link": "https://www.reddit.com/r/aivideo/comments/example_vid/",
                },
            ],
            "entry_count": 2,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "Video showcase"

    def test_video_only_mode_drops_title_only_video_claim(self):
        src = {
            "url": "https://www.reddit.com/r/aiArt/top/.rss?t=day",
            "content_type": "showcase",
            "video_only_mode": True,
        }
        result = {
            "entries": [
                {
                    "title": "My new AI video",
                    "summary": '<img src="https://preview.redd.it/still_image.png" />',
                    "link": "https://www.reddit.com/r/aiArt/comments/example_img/",
                },
            ],
            "entry_count": 1,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 0

    def test_media_only_mode_drops_text_only_posts(self):
        src = {
            "url": "https://www.reddit.com/r/aiArt/new/.rss",
            "content_type": "showcase",
            "media_only_mode": True,
            "media_required": True,
        }
        result = {
            "entries": [
                {
                    "title": "Question about prompting",
                    "summary": "How do I improve faces in my generations?",
                    "link": "https://www.reddit.com/r/aiArt/comments/q1/",
                },
                {
                    "title": "Creator output image",
                    "summary": '<img src="https://preview.redd.it/demo.png" />',
                    "link": "https://www.reddit.com/r/aiArt/comments/q2/",
                },
            ],
            "entry_count": 2,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "Creator output image"

    def test_showcase_gate_still_applies_when_media_required_false(self):
        src = {
            "url": "https://www.reddit.com/r/aiArt/new/.rss",
            "content_type": "showcase",
            "media_only_mode": True,
            "media_required": False,
        }
        result = {
            "entries": [
                {
                    "title": "Question about prompting",
                    "summary": "How do I improve faces in my generations?",
                    "link": "https://www.reddit.com/r/aiArt/comments/q1/",
                },
            ],
            "entry_count": 1,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 0

    def test_creator_lookback_keeps_only_recent_entries(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=14)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        old = (now - timedelta(days=75)).strftime("%a, %d %b %Y %H:%M:%S +0000")

        src = {
            "url": "https://www.reddit.com/r/aivideo/top/.rss?t=year",
            "content_type": "showcase",
            "video_only_mode": True,
            "creator_lookback_days": 60,
        }
        result = {
            "entries": [
                {
                    "title": "Recent creator video",
                    "summary": '<video src="https://v.redd.it/recent"></video>',
                    "link": "https://www.reddit.com/r/aivideo/comments/recent/",
                    "published": recent,
                },
                {
                    "title": "Old creator video",
                    "summary": '<video src="https://v.redd.it/old"></video>',
                    "link": "https://www.reddit.com/r/aivideo/comments/old/",
                    "published": old,
                },
            ],
            "entry_count": 2,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "Recent creator video"

    def test_creator_lookback_drops_missing_published_date(self):
        src = {
            "url": "https://www.reddit.com/r/aivideo/top/.rss?t=year",
            "content_type": "showcase",
            "video_only_mode": True,
            "creator_lookback_days": 60,
        }
        result = {
            "entries": [
                {
                    "title": "Video with missing date",
                    "summary": '<video src="https://v.redd.it/recent"></video>',
                    "link": "https://www.reddit.com/r/aivideo/comments/recent/",
                },
            ],
            "entry_count": 1,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 0


# ── Channel/profile URL detection ────────────────────────────

class TestChannelProfileUrlDetection:
    def test_detects_channel_profiles(self) -> None:
        assert _is_channel_or_profile_url("https://www.youtube.com/@creatorname") is True
        assert _is_channel_or_profile_url("https://www.youtube.com/channel/UC12345") is True
        assert _is_channel_or_profile_url("https://www.tiktok.com/@creator") is True
        assert _is_channel_or_profile_url("https://x.com/creator") is True

    def test_keeps_direct_post_links(self) -> None:
        assert _is_channel_or_profile_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
        assert _is_channel_or_profile_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") is False
        assert _is_channel_or_profile_url("https://www.tiktok.com/@creator/video/7489384756") is False
        assert _is_channel_or_profile_url("https://x.com/creator/status/1234567890") is False

    def test_showcase_drops_profile_urls_even_with_media_summary(self) -> None:
        src = {
            "url": "https://example.com/source",
            "content_type": "showcase",
            "video_only_mode": True,
        }
        result = {
            "entries": [
                {
                    "title": "Creator profile page",
                    "summary": '<video src="https://v.redd.it/abc123"></video>',
                    "link": "https://www.youtube.com/@creatorname",
                },
                {
                    "title": "Creator short clip",
                    "summary": "Short demo clip",
                    "link": "https://www.youtube.com/shorts/dQw4w9WgXcQ",
                },
            ],
            "entry_count": 2,
        }
        _apply_entry_filters(src, result)
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "Creator short clip"
