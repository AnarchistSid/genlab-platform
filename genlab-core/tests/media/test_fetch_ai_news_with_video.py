"""Pin `fetch_ai_news_with_video` behavior.

Feed→search→StoryCandidate mapping must handle:
  * RSS fetch failure per-feed (others continue)
  * yt-dlp search miss (no orphan text-only story)
  * Duplicate video_id across feeds (dedup)
  * Per-feed limit respected
  * Video-id extraction from various YT URL shapes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestVideoIdExtraction:
    def test_watch_url(self):
        from genlab_core.media.fetch_ai_news_with_video import _extract_video_id
        assert _extract_video_id("https://www.youtube.com/watch?v=abc123XYZ_-") == "abc123XYZ_-"

    def test_shorts_url(self):
        from genlab_core.media.fetch_ai_news_with_video import _extract_video_id
        assert _extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtu_be_via_v_param(self):
        """youtu.be URLs won't match this regex; we rely on webpage_url
        (canonical youtube.com/watch?v=X form)."""
        from genlab_core.media.fetch_ai_news_with_video import _extract_video_id
        # This helper isn't expected to handle youtu.be shortcuts.
        assert _extract_video_id("https://youtu.be/abc123XYZ_-") == ""

    def test_no_video_id_returns_empty(self):
        from genlab_core.media.fetch_ai_news_with_video import _extract_video_id
        assert _extract_video_id("https://example.com/page") == ""


class TestYtDlpCookiesArgs:
    def test_no_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("YT_DLP_COOKIES_FILE", raising=False)
        from genlab_core.media.fetch_ai_news_with_video import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == []

    def test_nonexistent_path_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(tmp_path / "missing.txt"))
        from genlab_core.media.fetch_ai_news_with_video import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == []

    def test_real_file_returns_args(self, monkeypatch, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        from genlab_core.media.fetch_ai_news_with_video import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == ["--cookies", str(cookies)]


class TestFetchForNiche:
    """End-to-end shape of fetch_for_niche."""

    def _fake_rss(self, titles):
        return [
            {
                "title": t,
                "link": f"https://news.example.com/{i}",
                "published": "Wed, 13 Aug 2026 12:00:00 GMT",
                "source_domain": "news.example.com",
            }
            for i, t in enumerate(titles)
        ]

    def test_happy_path(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(mod, "_fetch_rss", return_value=self._fake_rss(["Claude Opus 5 launch"])), \
             patch.object(
                 mod, "_search_youtube_for",
                 return_value=("https://www.youtube.com/watch?v=abcDEF12345", "Claude 5 review"),
             ):
            stories = mod.fetch_for_niche(
                niche_id="ai_creators",
                rss_feeds=[{"url": "https://news.example.com/feed"}],
                per_feed_limit=3,
            )
        assert len(stories) == 1
        s = stories[0]
        assert s["source_url"] == "https://www.youtube.com/watch?v=abcDEF12345"
        assert s["video_id"] == "abcDEF12345"
        assert s["source"] == "ainewsyt:news.example.com"
        assert s["download_url"] == "https://www.youtube.com/watch?v=abcDEF12345"
        assert s["extra"]["article_title"] == "Claude Opus 5 launch"
        assert s["extra"]["search_query"] == "Claude Opus 5 launch"

    def test_per_feed_limit_respected(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        # 5 headlines, limit 2 → only 2 stories from this feed
        titles = [f"Story {i}" for i in range(5)]
        # Return unique video URLs so dedup doesn't collapse
        search_side_effect = [
            (f"https://www.youtube.com/watch?v=vidABCDE{i:03d}", f"Video {i}")
            for i in range(10)
        ]
        with patch.object(mod, "_fetch_rss", return_value=self._fake_rss(titles)), \
             patch.object(mod, "_search_youtube_for", side_effect=search_side_effect):
            stories = mod.fetch_for_niche(
                niche_id="ai_creators",
                rss_feeds=[{"url": "https://news.example.com/feed"}],
                per_feed_limit=2,
            )
        assert len(stories) == 2

    def test_search_miss_drops_story(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(mod, "_fetch_rss", return_value=self._fake_rss(["A", "B", "C"])), \
             patch.object(mod, "_search_youtube_for", return_value=None):
            stories = mod.fetch_for_niche(
                niche_id="ai_creators",
                rss_feeds=[{"url": "https://news.example.com/feed"}],
                per_feed_limit=3,
            )
        assert stories == []

    def test_duplicate_video_id_dedup_across_feeds(self):
        """Two news feeds → same top-search hit → one story."""
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(
            mod, "_fetch_rss",
            side_effect=[
                self._fake_rss(["Story from feed 1"]),
                self._fake_rss(["Story from feed 2"]),
            ],
        ), patch.object(
            mod, "_search_youtube_for",
            return_value=("https://www.youtube.com/watch?v=samevideoID", "Same"),
        ):
            stories = mod.fetch_for_niche(
                niche_id="ai_creators",
                rss_feeds=[
                    {"url": "https://feed1.example.com/rss"},
                    {"url": "https://feed2.example.com/rss"},
                ],
                per_feed_limit=3,
            )
        assert len(stories) == 1

    def test_rss_fetch_failure_per_feed_others_continue(self):
        """One feed returns nothing (as if 500'd); other feeds still fire."""
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(
            mod, "_fetch_rss",
            side_effect=[
                [],
                self._fake_rss(["Working"]),
            ],
        ), patch.object(
            mod, "_search_youtube_for",
            return_value=("https://www.youtube.com/watch?v=workingIDvid", "W"),
        ):
            stories = mod.fetch_for_niche(
                niche_id="ai_creators",
                rss_feeds=[
                    {"url": "https://broken.example.com/rss"},
                    {"url": "https://working.example.com/rss"},
                ],
                per_feed_limit=3,
            )
        assert len(stories) == 1
        assert "workingID" in stories[0]["video_id"]

    def test_empty_feeds_returns_empty(self):
        from genlab_core.media import fetch_ai_news_with_video as mod
        assert mod.fetch_for_niche(niche_id="ai_creators", rss_feeds=[]) == []


class TestSearchYoutubeFor:
    """Cover subprocess wiring + failure modes."""

    def test_success_returns_url_and_title(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "https://www.youtube.com/watch?v=xxx\tExpected Title\n"
        fake_proc.stderr = ""
        with patch.object(mod.subprocess, "run", return_value=fake_proc):
            result = mod._search_youtube_for("Some query")
        assert result == ("https://www.youtube.com/watch?v=xxx", "Expected Title")

    def test_nonzero_exit_returns_none(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = ""
        fake_proc.stderr = "no results"
        with patch.object(mod.subprocess, "run", return_value=fake_proc):
            assert mod._search_youtube_for("query") is None

    def test_timeout_returns_none(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(
            mod.subprocess, "run",
            side_effect=mod.subprocess.TimeoutExpired("yt-dlp", 30),
        ):
            assert mod._search_youtube_for("query") is None

    def test_ytdlp_missing_returns_none(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        with patch.object(mod.subprocess, "run", side_effect=FileNotFoundError):
            assert mod._search_youtube_for("query") is None

    def test_malformed_stdout_returns_none(self):
        from genlab_core.media import fetch_ai_news_with_video as mod

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "no_tab_separator_here"
        fake_proc.stderr = ""
        with patch.object(mod.subprocess, "run", return_value=fake_proc):
            assert mod._search_youtube_for("query") is None
