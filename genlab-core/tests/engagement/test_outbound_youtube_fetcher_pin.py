"""Pin the 2026-07-17 Layer 4 batch 2 outbound YT fetcher.

## Quota-efficient path invariant

Naive path (search.list): 100 units per creator. With 3 creators × 5
niches × 6 fires/day = 9K units = 90% of daily 10K budget.

Cheap path (this module): 5 units per creator via
`channels.list → playlistItems.list → videos.list (batch) →
commentThreads.list (only for videos passing engagement floor)`.
5 × 3 × 5 × 6 = 450 units/day = <5% of budget.

This pin asserts the fetcher uses the cheap path — a future
"simplification" that swaps back to search.list would quietly burn
the quota budget.

## Fix contract

- `fetch_creator_recent_videos_with_comments` exists + returns []
  gracefully on empty input / missing key / API failure
- Comment fetch is GATED on video comment_count ≥20 (quota-saving)
- Uses channels.list + playlistItems.list, NOT search.list
"""

from __future__ import annotations

from unittest.mock import patch


def test_returns_empty_when_api_key_unset() -> None:
    """Missing YOUTUBE_API_KEY → fail-open, no API calls made."""
    from genlab_core.engagement.outbound_youtube_fetcher import (
        fetch_creator_recent_videos_with_comments,
    )

    with patch.dict("os.environ", {"YOUTUBE_API_KEY": ""}, clear=False):
        result = fetch_creator_recent_videos_with_comments(
            "gaming", ["UC_channel_1", "UC_channel_2"]
        )
    assert result == []


def test_returns_empty_when_creator_list_empty() -> None:
    """Empty input → early return, no API calls."""
    from genlab_core.engagement.outbound_youtube_fetcher import (
        fetch_creator_recent_videos_with_comments,
    )

    with patch.dict("os.environ", {"YOUTUBE_API_KEY": "fake"}, clear=False):
        result = fetch_creator_recent_videos_with_comments("gaming", [])
    assert result == []


def test_fetcher_uses_cheap_path_not_search_list() -> None:
    """The module must NOT use search.list (100 units per call).
    Instead uses channels.list → playlistItems.list → videos.list
    (batch) → commentThreads.list (5 units total per creator).

    Regression scenario: someone "simplifies" the fetcher to use
    search.list?channelId=X&order=date. Would burn 90% of daily
    quota budget."""
    import inspect

    from genlab_core.engagement import outbound_youtube_fetcher

    src = inspect.getsource(outbound_youtube_fetcher)

    # Strip module + function docstrings + comment lines so we only
    # check EXECUTABLE code (docstring can mention "search.list" while
    # explaining why we don't use it).
    code_lines = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        # Toggle docstring state on triple-quote markers
        if stripped.startswith('"""'):
            in_docstring = not in_docstring
            # Handle single-line docstrings ("""x""")
            if stripped.endswith('"""') and len(stripped) > 6:
                in_docstring = False
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code_only = "\n".join(code_lines)

    # The dangerous pattern: an actual API call to /search endpoint.
    # Would burn 100 units per creator per fetch.
    assert '_yt_get("search"' not in code_only, (
        "Fetcher must NOT call /search endpoint (100 units per creator "
        "= 90% of daily YT quota at 3 creators × 5 niches × 6 fires/day). "
        "Use the cheap channels.list → playlistItems.list path instead."
    )
    # Positive assertion: the cheap-path calls ARE present
    assert '_yt_get("channels"' in code_only or '"channels"' in code_only, (
        "Fetcher must use channels.list (1 unit) to get uploads playlist ID"
    )
    assert '_yt_get("playlistItems"' in code_only or '"playlistItems"' in code_only, (
        "Fetcher must use playlistItems.list (1 unit) to get recent uploads"
    )
    assert '_yt_get("commentThreads"' in code_only or '"commentThreads"' in code_only, (
        "Fetcher must use commentThreads.list to fetch top comments"
    )


def test_comment_fetch_gated_on_engagement_floor() -> None:
    """Videos with <20 comments should NOT have their comments fetched
    (quota-saving — such videos would be filtered downstream anyway).

    Regression: dropping the gate would 2× the comment-fetch quota
    spend."""
    import inspect

    from genlab_core.engagement import outbound_youtube_fetcher

    src = inspect.getsource(
        outbound_youtube_fetcher.fetch_creator_recent_videos_with_comments
    )
    # Must check comment_count < 20 before fetching comments
    assert '"comment_count"' in src and "< 20" in src, (
        "Comment fetch must skip videos with <20 comments — saves "
        "quota on low-engagement videos that would be filtered "
        "downstream anyway. See _fetch_top_comments call site."
    )


def test_batch_get_video_details_returns_correct_shape() -> None:
    """The dict shape must match what outbound_targeting.discover_*
    consumes. This is a contract pin between the two modules."""
    from unittest.mock import patch

    from genlab_core.engagement.outbound_youtube_fetcher import (
        _batch_get_video_details,
    )

    fake_response = {
        "items": [
            {
                "id": "vid_abc",
                "snippet": {
                    "channelId": "UC_creator1",
                    "title": "How I Doubled My Followers",
                    "publishedAt": "2026-07-15T10:00:00Z",
                },
                "statistics": {
                    "viewCount": "125000",
                    "commentCount": "342",
                },
            }
        ]
    }
    with patch(
        "genlab_core.engagement.outbound_youtube_fetcher._yt_get",
        return_value=fake_response,
    ):
        result = _batch_get_video_details(["vid_abc"])

    assert len(result) == 1
    v = result[0]
    assert v["video_id"] == "vid_abc"
    assert v["channel_id"] == "UC_creator1"
    assert v["title"] == "How I Doubled My Followers"
    assert v["view_count"] == 125000
    assert v["comment_count"] == 342
    assert v["published_at"] == "2026-07-15T10:00:00Z"
    assert v["comments"] == []  # populated separately by _fetch_top_comments


def test_fetch_top_comments_normalizes_shape() -> None:
    """commentThreads.list response → normalize to targeting-layer shape."""
    from genlab_core.engagement.outbound_youtube_fetcher import _fetch_top_comments

    fake_response = {
        "items": [
            {
                "snippet": {
                    "topLevelComment": {
                        "id": "cmt_1",
                        "snippet": {
                            "authorChannelId": {"value": "UC_alice"},
                            "authorDisplayName": "Alice",
                            "textDisplay": "This was a really useful breakdown",
                            "likeCount": 42,
                        },
                    }
                }
            }
        ]
    }
    with patch(
        "genlab_core.engagement.outbound_youtube_fetcher._yt_get",
        return_value=fake_response,
    ):
        result = _fetch_top_comments("vid_abc")

    assert len(result) == 1
    c = result[0]
    assert c["comment_id"] == "cmt_1"
    assert c["author_channel_id"] == "UC_alice"
    assert c["author_display_name"] == "Alice"
    assert c["text"] == "This was a really useful breakdown"
    assert c["like_count"] == 42


def test_end_to_end_wire_with_all_layers_mocked() -> None:
    """Integration-shape test: verify the fetcher chains its steps
    correctly. All 4 HTTP calls mocked; assert the returned shape
    is directly consumable by discover_youtube_targets."""
    from genlab_core.engagement.outbound_targeting import (
        discover_youtube_targets,
    )
    from genlab_core.engagement.outbound_youtube_fetcher import (
        fetch_creator_recent_videos_with_comments,
    )

    responses = {
        "channels": {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU_abc"}}}]
        },
        "playlistItems": {
            "items": [
                {"contentDetails": {"videoId": "vid_1"}},
                {"contentDetails": {"videoId": "vid_2"}},
            ]
        },
        "videos": {
            "items": [
                {
                    "id": f"vid_{i}",
                    "snippet": {
                        "channelId": "UC_creator1",
                        "title": f"Video {i}",
                        "publishedAt": "2026-07-16T10:00:00Z",  # 1 day ago
                    },
                    "statistics": {"viewCount": "100000", "commentCount": "50"},
                }
                for i in (1, 2)
            ]
        },
        "commentThreads": {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": f"cmt_{i}",
                            "snippet": {
                                "authorChannelId": {"value": f"UC_person_{i}"},
                                "authorDisplayName": f"Person{i}",
                                "textDisplay": "This was a genuinely useful post that made me think",
                                "likeCount": 100 - i,
                            },
                        }
                    }
                }
                for i in range(6)
            ]
        },
    }

    def _dispatch(path, params):
        return responses.get(path, {"items": []})

    with (
        patch.dict("os.environ", {"YOUTUBE_API_KEY": "fake"}, clear=False),
        patch(
            "genlab_core.engagement.outbound_youtube_fetcher._yt_get",
            side_effect=_dispatch,
        ),
    ):
        videos = fetch_creator_recent_videos_with_comments(
            "gaming", ["UC_creator1"]
        )

    assert len(videos) == 2
    assert all(len(v["comments"]) == 6 for v in videos)

    # And the fetcher output is directly consumable by targeting
    targets = discover_youtube_targets("gaming", videos)
    assert len(targets) > 0
    assert targets[0].platform == "youtube"
