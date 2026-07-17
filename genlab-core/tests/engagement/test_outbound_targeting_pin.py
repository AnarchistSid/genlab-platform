"""Pin the 2026-07-17 Layer 4 outbound target discovery.

## Growth-mechanic context

Audit round 4 identified outbound reply-to-top-comments on OTHER
creators' videos as the single highest-impact growth mechanic on
IG + YT for 2024-26. A witty reply on a 200K-view video
historically nets 500-2000 followers overnight — 1000× current
0.5/day baseline for ai_creators FB, repeatable daily per channel.

## Fix contract (this test locks it)

- Discovery is PURE — no HTTP, no DB, testable with synthetic input
- Filters chain deterministically:
  - Comment ≥20 chars
  - Not owner-of-video's own comment
  - Not already-replied (via caller-provided set)
  - Video ≤7 days old
  - Video comment_count ≥20
- Max 3 targets per creator (spam-flag protection)
- Max 15 targets per niche per run (rate-cap)
- Ranks by comment like_count, skips positions 0-1 (usually pinned)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from genlab_core.engagement.outbound_targeting import (
    OutboundTarget,
    discover_youtube_targets,
)


def _make_video(
    *,
    video_id: str = "v1",
    channel_id: str = "chan1",
    title: str = "Video Title",
    view_count: int = 100_000,
    comment_count: int = 50,
    age_days: int = 1,
    comments: list[dict] | None = None,
) -> dict:
    """Build a synthetic YouTube video dict matching the fetcher output shape."""
    published = datetime.now(UTC) - timedelta(days=age_days)
    return {
        "video_id": video_id,
        "channel_id": channel_id,
        "title": title,
        "view_count": view_count,
        "comment_count": comment_count,
        "published_at": published.isoformat(),
        "comments": comments or [],
    }


def _make_comment(
    *,
    comment_id: str,
    author_channel_id: str = "commenter_abc",
    text: str = "This was actually really useful, thanks for sharing.",
    like_count: int = 10,
    author_name: str = "Alice",
) -> dict:
    return {
        "comment_id": comment_id,
        "author_channel_id": author_channel_id,
        "author_display_name": author_name,
        "text": text,
        "like_count": like_count,
    }


def test_returns_targets_for_qualifying_video() -> None:
    """Baseline: video with 5 comments ranked 3-5 by likes should yield
    3 targets (skips top 2 pinned, targets 3-5, capped at 3 per creator)."""
    video = _make_video(
        comments=[
            _make_comment(comment_id=f"c{i}", like_count=100 - i * 10)
            for i in range(6)
        ]
    )
    targets = discover_youtube_targets("gaming", [video])
    assert len(targets) == 3
    # Ranked 2-5 (0-indexed) after top-2 skip = c2, c3, c4
    assert [t.comment_id for t in targets] == ["c2", "c3", "c4"]


def test_skips_video_owner_own_comments() -> None:
    """Video owner replying to their own video → skip (looks weird
    if we chime in on the owner's own comment)."""
    video = _make_video(
        channel_id="creator_alice",
        comments=[
            _make_comment(comment_id="c1", author_channel_id="commenter1"),
            _make_comment(comment_id="c2", author_channel_id="commenter2"),
            # This one is the owner replying to themselves — skip
            _make_comment(comment_id="c3", author_channel_id="creator_alice"),
            _make_comment(comment_id="c4", author_channel_id="commenter4"),
            _make_comment(comment_id="c5", author_channel_id="commenter5"),
        ],
    )
    targets = discover_youtube_targets("gaming", [video])
    assert "c3" not in {t.comment_id for t in targets}


def test_respects_already_replied_set() -> None:
    """Idempotency — caller passes set of comment_ids we've already
    replied to; discovery skips them."""
    video = _make_video(
        comments=[
            _make_comment(comment_id=f"c{i}", like_count=100 - i)
            for i in range(6)
        ]
    )
    targets = discover_youtube_targets(
        "gaming", [video], already_replied_comment_ids={"c2", "c3"}
    )
    # c2, c3 skipped → next in rank order is c4, c5
    ids = {t.comment_id for t in targets}
    assert "c2" not in ids and "c3" not in ids
    assert "c4" in ids


def test_skips_short_comments() -> None:
    """Comments under 20 chars (emoji, "first!", "🔥") → skip."""
    video = _make_video(
        comments=[
            _make_comment(comment_id="c1", text="🔥🔥🔥", like_count=100),
            _make_comment(comment_id="c2", text="first!", like_count=90),
            _make_comment(comment_id="c3", text="lol", like_count=80),
            _make_comment(
                comment_id="c4",
                text="This actually made me rethink the whole approach — great point",
                like_count=70,
            ),
            _make_comment(
                comment_id="c5",
                text="Would love to see a follow-up covering the edge cases here",
                like_count=60,
            ),
            _make_comment(
                comment_id="c6",
                text="The part about caching was gold — saved me hours",
                like_count=50,
            ),
        ]
    )
    targets = discover_youtube_targets("gaming", [video])
    # c1, c2, c3 short → skip. Ranked 2-5 = c4, c5, c6 (c1/c2/c3 removed
    # from ranking before the position-2 skip? Actually the rank happens
    # first, so top 6 by likes = c1, c2, c3, c4, c5, c6. Skip top 2 →
    # target c3, c4, c5, c6. Filter drops c3 (short). Should yield
    # c4, c5, c6.
    ids = [t.comment_id for t in targets]
    assert "c1" not in ids
    assert "c2" not in ids
    assert "c3" not in ids
    assert "c4" in ids and "c5" in ids


def test_skips_old_videos() -> None:
    """Videos older than 7 days don't drive discovery — the algo
    has stopped promoting them, our reply wouldn't gain visibility."""
    old_video = _make_video(
        age_days=10,
        comments=[_make_comment(comment_id="c1", like_count=100)],
    )
    targets = discover_youtube_targets("gaming", [old_video])
    assert targets == []


def test_skips_low_engagement_videos() -> None:
    """Videos with fewer than 20 comments → skip (our reply wouldn't
    reach a meaningful audience there)."""
    low_video = _make_video(
        comment_count=5,
        comments=[_make_comment(comment_id="c1")],
    )
    targets = discover_youtube_targets("gaming", [low_video])
    assert targets == []


def test_enforces_max_targets_per_creator() -> None:
    """Max 3 replies per creator per run (spam-flag concentration)."""
    video = _make_video(
        comments=[
            _make_comment(comment_id=f"c{i}", like_count=100 - i)
            for i in range(10)
        ]
    )
    targets = discover_youtube_targets("gaming", [video])
    assert len(targets) == 3


def test_enforces_max_targets_per_niche() -> None:
    """Global cap of 15 per niche per run."""
    videos = [
        _make_video(
            video_id=f"v{i}",
            channel_id=f"chan{i}",
            comments=[
                _make_comment(comment_id=f"c{i}_{j}", like_count=100 - j)
                for j in range(6)
            ],
        )
        for i in range(10)
    ]
    targets = discover_youtube_targets("gaming", videos)
    assert len(targets) == 15


def test_returns_outbound_target_dataclass() -> None:
    """Return type shape — the poller consumes OutboundTarget attributes."""
    video = _make_video(
        video_id="myvid",
        channel_id="mychan",
        title="Test Title",
        view_count=500_000,
        comments=[
            _make_comment(comment_id=f"c{i}", like_count=100 - i)
            for i in range(6)
        ],
    )
    targets = discover_youtube_targets("ai_creators", [video])
    t = targets[0]
    assert isinstance(t, OutboundTarget)
    assert t.platform == "youtube"
    assert t.niche_id == "ai_creators"
    assert t.video_id == "myvid"
    assert t.video_channel_id == "mychan"
    assert t.video_view_count == 500_000
    assert t.video_title == "Test Title"
