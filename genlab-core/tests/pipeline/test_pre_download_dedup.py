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
