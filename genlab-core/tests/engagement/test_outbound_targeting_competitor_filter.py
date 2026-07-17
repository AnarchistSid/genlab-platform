"""Pin: outbound_targeting skips videos whose channel is on competitor blocklist.

Layer 4 audit round 4 policy (2026-07-17). Videos on blocked channels
must be filtered BEFORE any per-creator cap or comment-level check —
otherwise a blocked channel with 3 candidate comments would consume 3
slots against per_creator_count.

## Coverage

- Empty/None blocklist: no-op, all videos considered (backward compat)
- Blocklist matches channel_id: entire video skipped (no targets emitted)
- Blocklist niche-isolated: gaming blocklist doesn't affect sports run
- Non-blocked video in same run still produces targets
"""

from __future__ import annotations

from genlab_core.engagement.outbound_targeting import discover_youtube_targets


def _video(vid_id: str, channel_id: str, comment_ids: list[str]):
    """Build a video dict shape matching discover_youtube_targets contract."""
    return {
        "video_id": vid_id,
        "channel_id": channel_id,
        "title": "Test",
        "view_count": 100000,
        "comment_count": 100,
        # 2 days ago — passes video-recency filter (7d cap)
        "published_at": "2026-07-15T00:00:00Z",
        "comments": [
            {
                "comment_id": cid,
                "author_channel_id": f"UC_author_{cid}",
                "author_display_name": f"User{cid}",
                "text": "This was an incredibly detailed breakdown that helped a lot",
                "like_count": 50,
            }
            for cid in comment_ids
        ],
    }


class TestCompetitorBlocklistFilter:
    def test_no_blocklist_all_videos_considered(self) -> None:
        """Backward compat: existing callers that don't pass a blocklist
        should see the same behavior as before this feature landed."""
        videos = [_video("v1", "UC_channel_a", ["c1", "c2", "c3", "c4", "c5"])]
        targets = discover_youtube_targets("gaming", videos)
        assert len(targets) > 0, "expected at least one target from unblocked video"

    def test_empty_blocklist_all_videos_considered(self) -> None:
        videos = [_video("v1", "UC_channel_a", ["c1", "c2", "c3", "c4", "c5"])]
        targets = discover_youtube_targets("gaming", videos, competitor_blocklist={})
        assert len(targets) > 0

    def test_blocked_channel_produces_no_targets(self) -> None:
        videos = [_video("v1", "UC_channel_a", ["c1", "c2", "c3", "c4", "c5"])]
        blocklist = {"gaming": {"UC_channel_a"}}
        targets = discover_youtube_targets("gaming", videos, competitor_blocklist=blocklist)
        assert targets == [], (
            "blocked channel must produce zero targets — otherwise "
            "we're commenting on competitor videos anyway"
        )

    def test_niche_isolation(self) -> None:
        """A gaming blocklist must NOT affect sports discovery — the
        same channel_id might appear on both watchlists (cross-niche
        creators) but each niche's competitor list is independent."""
        videos = [_video("v1", "UC_channel_a", ["c1", "c2", "c3", "c4", "c5"])]
        # Blocked in gaming, but this run is sports
        blocklist = {"gaming": {"UC_channel_a"}, "sports": set()}
        targets = discover_youtube_targets("sports", videos, competitor_blocklist=blocklist)
        assert len(targets) > 0, "sports run should be unaffected by gaming blocklist"

    def test_partial_blocklist_only_blocks_matched(self) -> None:
        """When some videos are on blocked channels and others aren't,
        only the matched ones are filtered. Non-matched continue through."""
        videos = [
            _video("v1", "UC_blocked_channel", ["c1", "c2", "c3", "c4", "c5"]),
            _video("v2", "UC_ok_channel", ["c6", "c7", "c8", "c9", "c10"]),
        ]
        blocklist = {"gaming": {"UC_blocked_channel"}}
        targets = discover_youtube_targets("gaming", videos, competitor_blocklist=blocklist)
        assert len(targets) > 0
        # None of the targets should reference the blocked channel
        assert all(t.video_channel_id != "UC_blocked_channel" for t in targets)

    def test_blocked_channel_doesnt_consume_per_creator_budget(self) -> None:
        """Regression: if a blocked channel counted against per_creator_count,
        the subsequent OK channel might hit budget prematurely. Confirm
        that blocking is done BEFORE per_creator_count check."""
        videos = [
            _video("v1", "UC_blocked", ["c1", "c2", "c3", "c4", "c5"]),
            _video("v2", "UC_ok", ["c6", "c7", "c8", "c9", "c10"]),
        ]
        blocklist = {"gaming": {"UC_blocked"}}
        targets = discover_youtube_targets(
            "gaming",
            videos,
            competitor_blocklist=blocklist,
            max_targets_per_creator=3,
        )
        # UC_ok should get its full 3 slots
        ok_targets = [t for t in targets if t.video_channel_id == "UC_ok"]
        assert len(ok_targets) == 3, (
            "unblocked channel should get its full per_creator_count "
            "budget — blocked channel's rejection must not consume slots"
        )
