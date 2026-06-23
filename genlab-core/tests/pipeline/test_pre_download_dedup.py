"""Regression tests for PreDownloadDedup stage.

This stage filters stories against the active-blueprint dedup set BEFORE
DownloadTopVideos, so we don't waste 5+ min rendering content that will
be deduped at push time anyway. Rejected/archived blueprints must NOT
block — they'll be revived by PushToBacklog if reached.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.pipeline.stages.pre_download_dedup import PreDownloadDedup


def _make_stage_with_bps(blueprints: list[dict]) -> tuple[PreDownloadDedup, MagicMock]:
    stage = PreDownloadDedup()
    client = MagicMock()
    client.blueprints.all.return_value = blueprints
    stage._client = client
    return stage, client


def test_drops_story_matching_visual_ready_url() -> None:
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=LIVE",
                    "video_id": "LIVE",
                    "status": "VISUAL_READY",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "test",
        "stories": [
            {
                "title": "fresh",
                "source_url": "https://www.youtube.com/watch?v=NEW",
                "video_id": "NEW",
            },
            {
                "title": "active",
                "source_url": "https://www.youtube.com/watch?v=LIVE",
                "video_id": "LIVE",
            },
        ],
    }
    stage.execute(ctx)
    titles = [s["title"] for s in ctx["stories"]]
    assert titles == ["fresh"]
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_url"] == 1


def test_rejected_blueprints_do_not_block() -> None:
    """Rejected/archived blueprints must NOT stop re-creation.

    The revive path in PushToBacklog will resurrect them, so we need to
    let their source stories through this stage.
    """
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=REJECTED",
                    "video_id": "REJ",
                    "status": "ARCHIVED",
                    "action_taken": "rejected",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "test",
        "stories": [
            {
                "title": "rejected-again",
                "source_url": "https://www.youtube.com/watch?v=REJECTED",
                "video_id": "REJ",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1
    assert ctx["stories"][0]["title"] == "rejected-again"


def test_auto_archived_blueprints_do_not_block() -> None:
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=AUTOARCH",
                    "video_id": "AA",
                    "status": "ARCHIVED",
                    "action_taken": "auto_archived_missing_media",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "test",
        "stories": [
            {
                "title": "retry-auto",
                "source_url": "https://www.youtube.com/watch?v=AUTOARCH",
                "video_id": "AA",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1


def test_publish_failed_blueprints_do_not_block() -> None:
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=FAILED",
                    "video_id": "FAIL",
                    "status": "PUBLISH_FAILED",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "test",
        "stories": [
            {
                "title": "retry-publish",
                "source_url": "https://www.youtube.com/watch?v=FAILED",
                "video_id": "FAIL",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1


def test_video_id_dedup_independent_of_url() -> None:
    """Same video_id but different URL should still be caught."""
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=VIDX",
                    "video_id": "VIDX",
                    "status": "VISUAL_READY",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "test",
        "stories": [
            {
                "title": "same video different url",
                "source_url": "https://example.com/different/url",
                "video_id": "VIDX",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 0
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_video_id"] == 1


def test_empty_stories_pass_through() -> None:
    stage, _ = _make_stage_with_bps([])
    ctx = {"niche_id": "test", "stories": []}
    result = stage.execute(ctx)
    assert result["stories"] == []


def test_missing_niche_id_bails_out_safely() -> None:
    stage, _ = _make_stage_with_bps([])
    ctx = {"stories": [{"title": "x"}]}  # no niche_id
    result = stage.execute(ctx)
    # Should pass through unchanged
    assert len(result["stories"]) == 1


def test_drafted_stories_pass_through_for_retry() -> None:
    """DRAFTED blueprints (render failed) must NOT block re-download.

    Regression: previously the pre-download dedup reused
    push_to_backlog._BLOCKING_STATUSES which includes DRAFTED, so a stuck
    DRAFTED blueprint (failed render) permanently blocked every retry of its
    own video — 96 sports DRAFTED were stranded mid-2026 by this exact bug.
    """
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=STUCK",
                    "video_id": "STUCK",
                    "status": "DRAFTED",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "sports",
        "stories": [
            {
                "title": "the stuck one wants to retry",
                "source_url": "https://www.youtube.com/watch?v=STUCK",
                "video_id": "STUCK",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_url"] == 0
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_video_id"] == 0


def test_scored_stories_pass_through_for_retry() -> None:
    """SCORED is a pre-draft transient state; never block re-download."""
    stage, _ = _make_stage_with_bps(
        [
            {
                "fields": {
                    "video_url": "https://www.youtube.com/watch?v=PARTIAL",
                    "video_id": "PARTIAL",
                    "status": "SCORED",
                }
            },
        ]
    )
    ctx = {
        "niche_id": "sports",
        "stories": [
            {
                "title": "scored but not drafted",
                "source_url": "https://www.youtube.com/watch?v=PARTIAL",
                "video_id": "PARTIAL",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1


def test_live_states_still_block() -> None:
    """PUBLISHED / PUBLISHING / VISUAL_READY remain blocking (don't republish)."""
    for status in ("PUBLISHED", "PUBLISHING", "VISUAL_READY"):
        stage, _ = _make_stage_with_bps(
            [
                {
                    "fields": {
                        "video_url": "https://www.youtube.com/watch?v=LIVE",
                        "video_id": "LIVE",
                        "status": status,
                    }
                },
            ]
        )
        ctx = {
            "niche_id": "sports",
            "stories": [
                {
                    "title": "already live",
                    "source_url": "https://www.youtube.com/watch?v=LIVE",
                    "video_id": "LIVE",
                },
            ],
        }
        stage.execute(ctx)
        assert len(ctx["stories"]) == 0, f"status={status} should have blocked"


# ---------------------------------------------------------------------------
# URL-dedup TTL regression block (added 2026-06-23 with PR url-dedup-ttl).
#
# Live-diagnosed root cause: gaming + sports were producing 0 blueprints on
# 2026-06-23 because their `video_url` fields point at sticky source URLs
# (Twitch directory pages, Steam store pages, ScoreBat match URLs) that recur
# in upstream trending data. Once published once, the dedup set blocked every
# subsequent day's fetch forever. The TTL lets these republish after N days.
# ---------------------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from genlab_core.pipeline.stages.pre_download_dedup import _is_within_url_ttl  # noqa: E402


def _aged_bp(days_ago: int, status: str = "PUBLISHED") -> dict:
    """Build a blueprint dict with ``created_at`` ``days_ago`` days back."""
    return {
        "fields": {
            "video_url": "https://www.twitch.tv/directory/game/Overwatch",
            "video_id": "ow_id",
            "status": status,
            "created_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        }
    }


def test_ttl_excludes_old_blueprint_from_dedup_set() -> None:
    """TTL=7 with blueprint 10 days old → fresh URL story passes through."""
    stage, _ = _make_stage_with_bps([_aged_bp(days_ago=10)])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"pipeline": {"url_dedup_ttl_days": 7}},
        "stories": [
            {
                "title": "Overwatch trending again",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "different_clip",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1, "10-day-old blueprint must not block under 7-day TTL"
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_url"] == 0


def test_ttl_keeps_recent_blueprint_blocking() -> None:
    """TTL=7 with blueprint 3 days old → fresh URL story still blocked."""
    stage, _ = _make_stage_with_bps([_aged_bp(days_ago=3)])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"pipeline": {"url_dedup_ttl_days": 7}},
        "stories": [
            {
                "title": "Overwatch trending today",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "todays_clip",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 0, "3-day-old blueprint must still block under 7-day TTL"
    assert ctx["run_stats"]["pre_download_dedup"]["dropped_url"] == 1


def test_no_ttl_config_preserves_old_behavior() -> None:
    """Niches without url_dedup_ttl_days config get the old forever-block."""
    stage, _ = _make_stage_with_bps([_aged_bp(days_ago=100)])  # ancient
    ctx = {
        "niche_id": "anime",
        # No niche_config → no TTL → old behaviour
        "stories": [
            {
                "title": "still blocked despite age",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "x",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 0, "Without TTL, old blueprints still block forever"


def test_ttl_zero_treated_as_no_ttl() -> None:
    """url_dedup_ttl_days: 0 (or negative) → no TTL filtering."""
    stage, _ = _make_stage_with_bps([_aged_bp(days_ago=100)])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"pipeline": {"url_dedup_ttl_days": 0}},
        "stories": [
            {
                "title": "should still block",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "x",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 0, "TTL=0 must be treated as no-TTL (block forever)"


def test_helper_keeps_missing_created_at_in_dedup_set() -> None:
    """Conservative: blueprint lacking created_at stays in the dedup set."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    bp_no_date = {"fields": {"video_url": "x", "status": "PUBLISHED"}}
    assert _is_within_url_ttl(bp_no_date, cutoff) is True


def test_helper_keeps_unparseable_created_at_in_dedup_set() -> None:
    """Malformed date string also defaults to conservative (kept)."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    bp_bad_date = {
        "fields": {
            "video_url": "x",
            "status": "PUBLISHED",
            "created_at": "not-a-date-at-all",
        }
    }
    assert _is_within_url_ttl(bp_bad_date, cutoff) is True


def test_helper_none_cutoff_short_circuits() -> None:
    """``cutoff=None`` (no TTL set) → always True; helper short-circuits."""
    bp = {"fields": {"video_url": "x", "status": "PUBLISHED", "created_at": "anything"}}
    assert _is_within_url_ttl(bp, None) is True


def test_source_pins_url_dedup_ttl_key_path() -> None:
    """Source-level pin: the config key path must remain reachable.

    Mirrors test_push_to_backlog_title_dedup_window.py — a refactor that
    moves the key would otherwise silently regress without breaking tests.
    """
    import genlab_core.pipeline.stages.pre_download_dedup as mod

    src = Path(mod.__file__).read_text()
    assert 'context.get("niche_config", {})' in src
    assert '.get("pipeline", {})' in src
    assert '.get("url_dedup_ttl_days")' in src


# Pathlib needed for the source pin above — imported here so the new block
# is self-contained and doesn't disturb the imports at the top of the file.
from pathlib import Path  # noqa: E402, I001
