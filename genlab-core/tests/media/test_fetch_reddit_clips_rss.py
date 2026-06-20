"""Regression pin for the 2026-06-21 Reddit RSS pivot.

## Why this exists

Reddit's JSON endpoints (``/r/<sub>/top.json``, ``/r/<sub>/search.json``)
became unreliable from data-center IPs in 2024-2025: the web tier serves
HTML "Blocked" pages with HTTP 200 instead of JSON, and OAuth access now
requires a multi-day Developer Program application + approval. The
public Atom RSS endpoint (``/r/<sub>/top/.rss``) still works from the
same IPs without auth.

The pivot adds an RSS-first path:
  * ``fetch_subreddit_via_rss`` — new function that fetches the Atom
    feed, parses it, normalises entries to the same story-dict shape
    the JSON path produces
  * ``fetch_subreddit`` — modified to try RSS first; falls through to
    JSON only when RSS yields nothing
  * ``video_sourcer._search_reddit`` — falls back to RSS when JSON
    returns non-JSON Content-Type (the IP-block signal from PR #405)

These tests pin both the parser correctness and the integration
behaviour. Sample Atom XML is a real captured response from
``https://www.reddit.com/r/sports/top/.rss?t=day&limit=5`` on
2026-06-21 — kept as a fixture so the tests work offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# Sample Atom feed — trimmed copy of r/sports/top/.rss from 2026-06-21.
# Contains 3 entries: 1 v.redd.it native video, 1 external link (espn),
# 1 v.redd.it video. Used as fixture across all parser tests.
_SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<category term="sports" label="r/sports"/>
<updated>2026-06-20T20:22:30+00:00</updated>
<title>top scoring links : sports</title>
<entry>
<author><name>/u/xThe-Legend-Killerx</name><uri>https://www.reddit.com/user/xThe-Legend-Killerx</uri></author>
<content type="html">&lt;table&gt; &lt;tr&gt;&lt;td&gt; &lt;a href=&quot;https://www.reddit.com/r/sports/comments/1uaf173/the_crowd_in_seattle/&quot;&gt; &lt;img src=&quot;https://external-preview.redd.it/dzk3MXM5aWhhYjhoMSBCR8iqnjYted9KxDZH-cHi5ZE4_VE75wT_M6mbH6Wi.png?width=640&quot; alt=&quot;Seattle crowd singing&quot; /&gt; &lt;/a&gt; &lt;/td&gt;&lt;td&gt; submitted by &lt;a href=&quot;https://www.reddit.com/user/xThe-Legend-Killerx&quot;&gt; /u/xThe-Legend-Killerx &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=&quot;https://v.redd.it/3imabskhab8h1&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; &lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/sports/comments/1uaf173/the_crowd_in_seattle/&quot;&gt;[comments]&lt;/a&gt;&lt;/span&gt; &lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>
<id>t3_1uaf173</id>
<link href="https://www.reddit.com/r/sports/comments/1uaf173/the_crowd_in_seattle/" />
<updated>2026-06-19T21:59:31+00:00</updated>
<published>2026-06-19T21:59:31+00:00</published>
<title>The crowd in Seattle singing Take Me Home, Country Roads following the 2-0 victory by the U.S. over Australia in the 2026 FIFA World Cup</title>
</entry>
<entry>
<author><name>/u/Ok-Soil-5133</name><uri>https://www.reddit.com/user/Ok-Soil-5133</uri></author>
<content type="html">&lt;table&gt;&lt;tr&gt;&lt;td&gt; &lt;a href=&quot;https://www.reddit.com/r/sports/comments/1uadtau/usmnt/&quot;&gt; &lt;img src=&quot;https://external-preview.redd.it/3yP8NnT3TxP9iQ5uIS7RUAPO0je6oC78L5Dhk5ALKDc.jpeg?width=640&quot; alt=&quot;USMNT vs Australia&quot; /&gt; &lt;/a&gt; &lt;/td&gt;&lt;td&gt; submitted by &lt;a href=&quot;https://www.reddit.com/user/Ok-Soil-5133&quot;&gt; /u/Ok-Soil-5133 &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=&quot;https://www.espn.com/soccer/story/_/id/49119325/usa-australia-2026-world-cup&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; &lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>
<id>t3_1uadtau</id>
<link href="https://www.reddit.com/r/sports/comments/1uadtau/usmnt/" />
<published>2026-06-19T21:08:42+00:00</published>
<title>USMNT sees off Australia 2-0 to advance to World Cup knockout stage</title>
</entry>
<entry>
<author><name>/u/blkndblue</name><uri>https://www.reddit.com/user/blkndblue</uri></author>
<content type="html">&lt;table&gt; &lt;tr&gt;&lt;td&gt; &lt;a href=&quot;https://www.reddit.com/r/sports/comments/1ub1oo1/netherlands/&quot;&gt; &lt;img src=&quot;https://external-preview.redd.it/MGwyMDV2NWd5ZzhoMWEtHWWZilI5YiiD8F8hvnXGWEEEhGCXKqd-2I_XllrK.png?width=320&quot; alt=&quot;Netherlands fans Houston&quot; /&gt; &lt;/a&gt; &lt;/td&gt;&lt;td&gt; submitted by &lt;a href=&quot;https://www.reddit.com/user/blkndblue&quot;&gt; /u/blkndblue &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=&quot;https://v.redd.it/snvvksegyg8h1&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; &lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>
<id>t3_1ub1oo1</id>
<link href="https://www.reddit.com/r/sports/comments/1ub1oo1/netherlands/" />
<published>2026-06-20T17:02:53+00:00</published>
<title>Netherlands fans have taken over Houston, singing and dancing to "Links Rechts" at the World Cup.</title>
</entry>
</feed>
"""


class TestAtomFeedParser:
    """Pin ``_parse_atom_feed`` — the Atom XML to entry-dict mapping."""

    def test_extracts_three_entries(self):
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert len(entries) == 3, (
            f"Expected 3 <entry> elements; got {len(entries)}. "
            "If parser missed entries, the Atom namespace handling regressed."
        )

    def test_position_is_zero_indexed_in_feed_order(self):
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert entries[0]["position"] == 0
        assert entries[1]["position"] == 1
        assert entries[2]["position"] == 2

    def test_extracts_native_v_redd_it_video_url(self):
        """Entry 1 has <a href="https://v.redd.it/3imabskhab8h1"> in the
        content HTML. Parser must extract this URL — it's the direct
        video link yt-dlp will download."""
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert entries[0]["video_url"] == "https://v.redd.it/3imabskhab8h1"

    def test_external_link_entry_has_no_video_url(self):
        """Entry 2 links to espn.com (non-video host). Parser should
        return None for the video_url — _normalise_rss_entry will then
        drop the story (no playable video)."""
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert entries[1]["video_url"] is None, (
            f"Entry 2 links to espn.com (non-video). Got: {entries[1]['video_url']!r}"
        )

    def test_extracts_post_id_without_t3_prefix(self):
        """``<id>t3_1uaf173</id>`` → ``"1uaf173"`` (strip the ``t3_`` prefix)."""
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert entries[0]["post_id"] == "1uaf173"
        assert entries[2]["post_id"] == "1ub1oo1"

    def test_extracts_published_datetime_utc(self):
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        # Entry 1 published 2026-06-19T21:59:31+00:00
        assert entries[0]["published_at"] == datetime(2026, 6, 19, 21, 59, 31, tzinfo=UTC)

    def test_extracts_thumbnail_url(self):
        """First <img src> in the content HTML becomes the thumbnail."""
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert "external-preview.redd.it" in entries[0]["thumbnail_url"]

    def test_extracts_permalink_to_comments(self):
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        entries = _parse_atom_feed(_SAMPLE_ATOM)
        assert entries[0]["permalink"] == (
            "https://www.reddit.com/r/sports/comments/1uaf173/the_crowd_in_seattle/"
        )

    def test_malformed_xml_returns_empty(self):
        """Parser must fail-soft on invalid XML — return [] not raise."""
        from genlab_core.media.fetch_reddit_clips import _parse_atom_feed

        assert _parse_atom_feed("<<not-xml>>") == []
        assert _parse_atom_feed("") == []


class TestRSSEntryNormalisation:
    """Pin ``_normalise_rss_entry`` — RSS entry to pipeline story dict."""

    def _entry(self, **overrides):
        """Build a minimal valid entry dict."""
        base = {
            "title": "Sample Reddit Post Title",
            "video_url": "https://v.redd.it/abc123",
            "post_id": "1uaf173",
            "permalink": "https://www.reddit.com/r/sports/comments/1uaf173/",
            "published_at": datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC),
            "position": 0,
            "thumbnail_url": "https://i.redd.it/thumb.png",
        }
        base.update(overrides)
        return base

    def test_entry_without_video_url_returns_none(self):
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        e = self._entry(video_url=None)
        assert _normalise_rss_entry(e, "sports", "sports", 10) is None

    def test_entry_without_title_returns_none(self):
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        e = self._entry(title="")
        assert _normalise_rss_entry(e, "sports", "sports", 10) is None

    def test_top_position_gets_high_synth_score(self):
        """position=0 → score=1000. Top-of-feed entries should rank
        highest in cross-source scoring."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(position=0), "sports", "sports", 10)
        assert story["reddit_score"] == 1000

    def test_lower_position_gets_decayed_score(self):
        """position=5 → score=1000 - 5*36 = 820. Linear decay."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(position=5), "sports", "sports", 10)
        assert story["reddit_score"] == 1000 - 5 * 36

    def test_score_floored_at_100(self):
        """Bottom-of-feed (position=30+) still clears the 100-score floor
        — preserves the existing ``score >= 100`` filter behaviour."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(position=50), "sports", "sports", 50)
        assert story["reddit_score"] >= 100

    def test_story_shape_matches_json_path(self):
        """Critical: RSS-derived stories must have all the same keys
        the JSON path's ``_normalise_post`` produces. Downstream stages
        depend on this shape contract."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(), "sports", "sports", 10)
        required_keys = {
            "story_id",
            "title",
            "source",
            "source_url",
            "canonical_url",
            "published_date",
            "published_at",
            "fetched_at",
            "summary",
            "channel_name",
            "view_count",
            "view_velocity",
            "duration_seconds",
            "thumbnail_url",
            "tags",
            "niche_id",
            "video_source",
            "video_id",
            "is_official_channel",
            "source_mention_count",
            "_trending_video",
            "reddit_score",
            "reddit_num_comments",
            "reddit_upvote_ratio",
        }
        missing = required_keys - set(story.keys())
        assert not missing, (
            f"RSS story missing keys: {missing}. Downstream stages assume "
            "the JSON path's shape contract; RSS path must match."
        )

    def test_rss_attribution_marker_present(self):
        """The ``_reddit_source_path: 'rss'`` flag lets ops attribute
        whether a Reddit-sourced story came via the RSS or JSON path
        (relevant for tuning + understanding pipeline behaviour)."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(), "sports", "sports", 10)
        assert story["_reddit_source_path"] == "rss"
        assert story["_rss_position"] == 0

    def test_source_is_namespaced_reddit_prefix(self):
        """``source`` field must be ``reddit:<subreddit>`` — gaming-
        pipeline FilterGamingStories uses the prefix to identify Reddit
        sources for trust-list bypass."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        story = _normalise_rss_entry(self._entry(), "gaming", "GamingClips", 10)
        assert story["source"] == "reddit:GamingClips"

    def test_view_velocity_decays_with_age(self):
        """Older posts get lower view_velocity (views/hr proxy)."""
        from genlab_core.media.fetch_reddit_clips import _normalise_rss_entry

        # 1-hour-old story
        recent = self._entry(published_at=datetime.now(UTC).replace(microsecond=0))
        old = self._entry(published_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC))

        recent_story = _normalise_rss_entry(recent, "sports", "sports", 10)
        old_story = _normalise_rss_entry(old, "sports", "sports", 10)
        assert recent_story["view_velocity"] > old_story["view_velocity"]


class TestFetchSubredditViaRSS:
    """Pin ``fetch_subreddit_via_rss`` — the network-layer RSS fetcher."""

    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_happy_path_returns_video_stories(self, mock_get):
        """200 + valid Atom XML with Content-Type atom+xml → list of
        story dicts (one per parseable video entry)."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit_via_rss

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/atom+xml; charset=UTF-8"}
        mock_resp.text = _SAMPLE_ATOM
        mock_get.return_value = mock_resp

        stories = fetch_subreddit_via_rss(subreddit="sports", niche_id="sports", limit=10)
        # Entry 1 + Entry 3 have v.redd.it videos; Entry 2 is espn link (not video host)
        assert len(stories) == 2
        for story in stories:
            assert story["source_url"].startswith("https://v.redd.it/")
            assert story["niche_id"] == "sports"

    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_429_returns_empty(self, mock_get):
        """Rate-limited responses fail-soft — return [] so caller can
        try other subreddits or fall through to JSON path."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit_via_rss

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}
        mock_resp.text = ""
        mock_get.return_value = mock_resp

        assert fetch_subreddit_via_rss("sports", "sports") == []

    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_html_block_page_returns_empty(self, mock_get):
        """Reddit serves HTML "Blocked" pages with HTTP 200 + text/html
        Content-Type for IP-blocked data-center IPs (the 2026-06-20 R7
        finding). RSS path detects via Content-Type and returns []."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit_via_rss

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=UTF-8"}
        mock_resp.text = "<!doctype html><title>Blocked</title>"
        mock_get.return_value = mock_resp

        assert fetch_subreddit_via_rss("sports", "sports") == []

    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_network_exception_returns_empty(self, mock_get):
        """Any request exception fails-soft → []."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit_via_rss

        mock_get.side_effect = ConnectionError("network down")
        assert fetch_subreddit_via_rss("sports", "sports") == []

    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_uses_top_rss_endpoint_with_time_window_param(self, mock_get):
        """Request URL must hit ``/r/<sub>/top/.rss`` with ``t=`` param."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit_via_rss

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/atom+xml"}
        mock_resp.text = _SAMPLE_ATOM
        mock_get.return_value = mock_resp

        fetch_subreddit_via_rss("sports", "sports", time_window="week", limit=20)
        called_url = mock_get.call_args.args[0]
        called_params = mock_get.call_args.kwargs.get("params", {})
        assert called_url == "https://www.reddit.com/r/sports/top/.rss"
        assert called_params["t"] == "week"
        assert called_params["limit"] == 20


class TestFetchSubredditIntegration:
    """Pin that ``fetch_subreddit`` tries RSS first and falls through to
    JSON only when RSS is unavailable (2026-06-21 pivot integration)."""

    @patch("genlab_core.media.fetch_reddit_clips.fetch_subreddit_via_rss")
    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_rss_first_skips_json_when_rss_yields(self, mock_json_get, mock_rss):
        """Happy path: RSS returns stories → JSON is NEVER called."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit

        # Pre-baked RSS-derived stories
        mock_rss.return_value = [
            {"story_id": "s1", "title": "Story 1", "source_url": "https://v.redd.it/aaa"},
            {"story_id": "s2", "title": "Story 2", "source_url": "https://v.redd.it/bbb"},
        ]

        result = fetch_subreddit("sports", "sports", listing="top")
        assert len(result) == 2
        mock_rss.assert_called_once()
        (
            mock_json_get.assert_not_called(),
            (
                "When RSS yields stories, JSON path must NOT be hit — the "
                "RSS-first pivot's whole point is bypassing the IP-blocked "
                "JSON endpoint."
            ),
        )

    @patch("genlab_core.media.fetch_reddit_clips.fetch_subreddit_via_rss")
    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_rss_empty_falls_through_to_json(self, mock_json_get, mock_rss):
        """When RSS returns [] (rate-limited / blocked), the JSON path
        runs as fallback. Preserves pre-pivot behaviour for dev machines
        not affected by data-center IP blocks."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit

        mock_rss.return_value = []
        mock_json_resp = MagicMock()
        mock_json_resp.status_code = 200
        mock_json_resp.json.return_value = {"data": {"children": []}}
        mock_json_get.return_value = mock_json_resp

        fetch_subreddit("sports", "sports", listing="top")
        mock_rss.assert_called_once()
        mock_json_get.assert_called_once()
        called_url = mock_json_get.call_args.args[0]
        assert "/top.json" in called_url, (
            f"Fall-through must hit the JSON endpoint. Got: {called_url}"
        )

    @patch("genlab_core.media.fetch_reddit_clips.fetch_subreddit_via_rss")
    @patch("genlab_core.media.fetch_reddit_clips.requests.get")
    def test_non_top_listing_skips_rss(self, mock_json_get, mock_rss):
        """RSS only exposes the ``top`` variant. For listing=hot/new,
        skip RSS and go directly to JSON. Pin this so callers using
        non-top listings don't hit the RSS path uselessly."""
        from genlab_core.media.fetch_reddit_clips import fetch_subreddit

        mock_json_resp = MagicMock()
        mock_json_resp.status_code = 200
        mock_json_resp.json.return_value = {"data": {"children": []}}
        mock_json_get.return_value = mock_json_resp

        fetch_subreddit("sports", "sports", listing="hot")
        mock_rss.assert_not_called()
        mock_json_get.assert_called_once()
