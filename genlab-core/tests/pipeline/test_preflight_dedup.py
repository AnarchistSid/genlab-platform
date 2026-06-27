"""Regression tests for the PreflightDedup pipeline stage.

PreflightDedup runs between FilterXStories and the expensive enrich+render
path. Its job: drop stories that PushToBacklog would dedup-reject anyway,
so we don't burn ~280s of render CPU on candidates destined to be
discarded. PushToBacklog stays as the final authority — this stage is an
OPTIMIZATION, not a replacement.

Prod evidence (2026-06-27): gaming's 04:00 UTC run rendered 3 videos
in 279s, then PushToBacklog blocked all 3 (Overwatch + LoL URLs already
published; Sand:Raiders title near-dupe). This stage would have caught
them BEFORE the render.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from genlab_core.pipeline.stages.preflight_dedup import PreflightDedup


def _make_stage(blueprints: list[dict]) -> PreflightDedup:
    """Build a PreflightDedup with a MagicMock backlog_client pre-loaded."""
    stage = PreflightDedup()
    client = MagicMock()
    client.blueprints.all.return_value = blueprints
    stage._client = client
    return stage


def _bp(
    *,
    url: str = "https://www.youtube.com/watch?v=BLOCKED",
    video_id: str = "BLOCKED",
    title: str = "An existing blueprint title for matching",
    status: str = "PUBLISHED",
    created_days_ago: int | None = None,
) -> dict:
    fields = {
        "video_url": url,
        "video_id": video_id,
        "title": title,
        "status": status,
    }
    record: dict = {"fields": fields}
    if created_days_ago is not None:
        record["created_at"] = (datetime.now(UTC) - timedelta(days=created_days_ago)).isoformat()
    return record


# ── Core blocking behaviour ──────────────────────────────────────────────────


def test_blocks_story_with_url_in_active_blueprints() -> None:
    """Story whose source_url matches a PUBLISHED blueprint URL is dropped."""
    stage = _make_stage(
        [_bp(url="https://www.youtube.com/watch?v=DUP", video_id="DUP", status="PUBLISHED")]
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "fresh candidate to keep",
                "source_url": "https://www.youtube.com/watch?v=NEW",
                "video_id": "NEW",
            },
            {
                "title": "already published do not enrich",
                "source_url": "https://www.youtube.com/watch?v=DUP",
                "video_id": "DUP",
            },
        ],
    }
    stage.execute(ctx)
    titles = [s["title"] for s in ctx["stories"]]
    assert titles == ["fresh candidate to keep"]
    stats = ctx["run_stats"]["preflight_dedup"]
    assert stats["input"] == 2
    assert stats["survivors"] == 1
    assert stats["dropped_count"] == 1
    assert stats["dropped_reasons"] == ["url"]


def test_blocks_story_with_title_near_dupe() -> None:
    """Story title that fuzzy-matches an existing blueprint title is dropped."""
    stage = _make_stage(
        [
            _bp(
                url="https://x.com/different",
                video_id="vdiff",
                title="overwatch new season eleven launches today",
                status="PUBLISHED",
            )
        ]
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "Overwatch new season eleven launches today now",
                "source_url": "https://x.com/SOMETHING_ELSE",
                "video_id": "different_clip",
            },
        ],
    }
    stage.execute(ctx)
    assert ctx["stories"] == []
    assert ctx["run_stats"]["preflight_dedup"]["dropped_reasons"] == ["title_near_dupe"]


def test_blocks_story_with_video_id_match() -> None:
    """Same video_id, different URL → still dropped on video_id."""
    stage = _make_stage([_bp(url="https://x.com/old", video_id="VIDX", status="PUBLISHED")])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "same video different url somewhere",
                "source_url": "https://different-host.example/wherever",
                "video_id": "VIDX",
            },
        ],
    }
    stage.execute(ctx)
    assert ctx["stories"] == []
    assert ctx["run_stats"]["preflight_dedup"]["dropped_reasons"] == ["video_id"]


def test_allows_story_with_unique_url_and_title() -> None:
    """Unique story passes through; survivor count + zero drops recorded."""
    stage = _make_stage([_bp(url="https://other.com/x", video_id="OTHER")])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "this candidate is completely unique compared to backlog",
                "source_url": "https://fresh.com/new",
                "video_id": "FRESH",
            }
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1
    assert ctx["run_stats"]["preflight_dedup"]["dropped_count"] == 0
    assert ctx["run_stats"]["preflight_dedup"]["survivors"] == 1


# ── TTL respect ──────────────────────────────────────────────────────────────


def test_respects_url_dedup_ttl_days() -> None:
    """TTL=7 + blueprint 10d old → story with same URL passes through."""
    stage = _make_stage(
        [
            _bp(
                url="https://www.twitch.tv/directory/game/Overwatch",
                video_id="ow_old",
                status="PUBLISHED",
                created_days_ago=10,
            )
        ]
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"pipeline": {"url_dedup_ttl_days": 7}},
        "stories": [
            {
                "title": "Overwatch trending again should be allowed",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "ow_today",
            },
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1
    assert ctx["run_stats"]["preflight_dedup"]["dropped_count"] == 0


def test_recent_blueprint_within_ttl_still_blocks() -> None:
    """TTL=7 + blueprint 3d old → URL still blocks."""
    stage = _make_stage(
        [
            _bp(
                url="https://www.twitch.tv/directory/game/Overwatch",
                video_id="ow_recent",
                status="PUBLISHED",
                created_days_ago=3,
            )
        ]
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"pipeline": {"url_dedup_ttl_days": 7}},
        "stories": [
            {
                "title": "Overwatch trending today within ttl window",
                "source_url": "https://www.twitch.tv/directory/game/Overwatch",
                "video_id": "ow_now",
            },
        ],
    }
    stage.execute(ctx)
    assert ctx["stories"] == []


# ── Fail-OPEN semantics ──────────────────────────────────────────────────────


def test_fail_open_when_no_backlog_client_init_fails(monkeypatch) -> None:
    """If BacklogClient() raises, all stories pass through unchanged."""
    stage = PreflightDedup()
    # Force _get_client to raise
    monkeypatch.setattr(
        "genlab_core.pipeline.stages.preflight_dedup.BacklogClient",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no credentials")),
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "story one full title text",
                "source_url": "https://a.com/1",
                "video_id": "a1",
            },
            {
                "title": "story two full title text",
                "source_url": "https://a.com/2",
                "video_id": "a2",
            },
        ],
    }
    stage.execute(ctx)
    # All stories pass through; no run_stats entry
    assert len(ctx["stories"]) == 2
    assert "preflight_dedup" not in ctx.get("run_stats", {})


def test_fail_open_on_dedup_keys_exception() -> None:
    """If load_dedup_keys raises, all stories pass through."""
    stage = PreflightDedup()
    client = MagicMock()
    client.blueprints.all.side_effect = RuntimeError("backend down")
    stage._client = client
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [{"title": "x" * 30, "source_url": "https://x.com/y", "video_id": "y"}],
    }
    stage.execute(ctx)
    # load_dedup_keys catches internally and returns empty keys → all
    # stories pass through; run_stats IS recorded (the empty-keys path
    # is fail-open at a layer below this one).
    assert len(ctx["stories"]) == 1


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_stories_pass_through_without_run_stats() -> None:
    """No stories → no work; no run_stats entry (parity with PreDownloadDedup)."""
    stage = _make_stage([])
    ctx = {"niche_id": "gaming", "niche_config": {}, "stories": []}
    stage.execute(ctx)
    assert ctx["stories"] == []
    assert "preflight_dedup" not in ctx.get("run_stats", {})


def test_missing_niche_id_passes_through() -> None:
    """No niche_id → fail-OPEN."""
    stage = _make_stage([])
    ctx = {"niche_config": {}, "stories": [{"title": "x" * 30}]}
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1


def test_logs_dropped_titles_and_reasons() -> None:
    """run_stats.preflight_dedup carries dropped_titles + dropped_reasons."""
    stage = _make_stage(
        [
            _bp(url="https://a.com/1", video_id="a1"),
            _bp(url="https://b.com/2", video_id="b2"),
        ]
    )
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "dropped one full title text",
                "source_url": "https://a.com/1",
                "video_id": "a1",
            },
            {
                "title": "dropped two full title text",
                "source_url": "https://b.com/2",
                "video_id": "b2",
            },
            {"title": "survivor passes through", "source_url": "https://c.com/3", "video_id": "c3"},
        ],
    }
    stage.execute(ctx)
    stats = ctx["run_stats"]["preflight_dedup"]
    assert stats["dropped_count"] == 2
    assert stats["survivors"] == 1
    assert "dropped one full title text" in stats["dropped_titles"]
    assert "dropped two full title text" in stats["dropped_titles"]
    assert stats["dropped_reasons"] == ["url", "url"]


def test_dropped_titles_truncated_at_ten() -> None:
    """Only first 10 dropped titles are surfaced in run_stats (telemetry cap)."""
    bps = [_bp(url=f"https://x.com/{i}", video_id=f"v{i}") for i in range(15)]
    stage = _make_stage(bps)
    stories = [
        {
            "title": f"dropped story number {i:02d} long enough",
            "source_url": f"https://x.com/{i}",
            "video_id": f"v{i}",
        }
        for i in range(15)
    ]
    ctx = {"niche_id": "gaming", "niche_config": {}, "stories": stories}
    stage.execute(ctx)
    stats = ctx["run_stats"]["preflight_dedup"]
    assert stats["dropped_count"] == 15
    assert len(stats["dropped_titles"]) == 10
    # Reasons keeps all 15 — small payload, useful for telemetry rollups
    assert len(stats["dropped_reasons"]) == 15


def test_does_not_modify_other_context_keys() -> None:
    """Only stories + run_stats.preflight_dedup mutated; other keys untouched."""
    stage = _make_stage([_bp(url="https://x.com/y", video_id="y")])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {"foo": "bar"},
        "stories": [
            {"title": "blocked story title here", "source_url": "https://x.com/y", "video_id": "y"}
        ],
        "blueprints": ["unrelated"],
        "run_stats": {"other_stage": {"foo": 1}},
    }
    stage.execute(ctx)
    # stories filtered
    assert ctx["stories"] == []
    # other stage stats preserved
    assert ctx["run_stats"]["other_stage"] == {"foo": 1}
    # preflight_dedup added
    assert "preflight_dedup" in ctx["run_stats"]
    # untouched keys remain
    assert ctx["blueprints"] == ["unrelated"]
    assert ctx["niche_config"] == {"foo": "bar"}


def test_archived_blueprints_do_not_block() -> None:
    """ARCHIVED status is non-blocking — story with same URL passes through."""
    stage = _make_stage([_bp(url="https://archived.com/x", video_id="ARCHX", status="ARCHIVED")])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "republish of archived blueprint should be allowed",
                "source_url": "https://archived.com/x",
                "video_id": "ARCHX",
            }
        ],
    }
    stage.execute(ctx)
    assert len(ctx["stories"]) == 1


def test_drafted_blueprints_do_block() -> None:
    """DRAFTED IS in LIVE_OR_PENDING — same story shouldn't re-enter the pipeline.

    Differs from PreDownloadDedup (which uses the narrower LIVE set so
    stuck DRAFTEDs can re-download). Push-side dedup IS strict, and
    PreflightDedup mirrors that — push_to_backlog's upsert path will
    handle the existing-DRAFTED row anyway, so re-enriching is wasted.
    """
    stage = _make_stage([_bp(url="https://drafted.com/x", video_id="DR1", status="DRAFTED")])
    ctx = {
        "niche_id": "gaming",
        "niche_config": {},
        "stories": [
            {
                "title": "would have produced same draft please dedup",
                "source_url": "https://drafted.com/x",
                "video_id": "DR1",
            }
        ],
    }
    stage.execute(ctx)
    assert ctx["stories"] == []


# ── Wire pin: gaming niche.yaml ──────────────────────────────────────────────


def test_gaming_niche_yaml_wires_preflight_dedup_between_filter_and_enrich() -> None:
    """Pin: the gaming pipeline must list PreflightDedup between
    FilterGamingStories and EnrichWithIGDB.

    Reordering this stage (or removing it) silently restores the
    280s-of-wasted-render-CPU outage shape from 2026-06-27. CI fires
    if the order shifts.
    """
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "CriticalRush/niches/gaming/config/niche.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    stage_classes = [s["class"] if isinstance(s, dict) else s for s in cfg["pipeline"]["stages"]]
    filter_idx = stage_classes.index(
        "niches.gaming.stages.filter_gaming_stories.FilterGamingStories"
    )
    enrich_idx = stage_classes.index("niches.gaming.stages.enrich_with_igdb.EnrichWithIGDB")
    preflight_idx = stage_classes.index(
        "genlab_core.pipeline.stages.preflight_dedup.PreflightDedup"
    )
    assert filter_idx < preflight_idx < enrich_idx, (
        f"PreflightDedup must sit between filter ({filter_idx}) and enrich "
        f"({enrich_idx}); currently at index {preflight_idx}"
    )
