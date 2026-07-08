"""Regression tests for the shared dedup-keys helper.

The :mod:`genlab_core.pipeline.dedup_keys` module is the single source
of truth for what's currently in the dedup set. Both
:class:`PushToBacklog` (post-render) and :class:`PreflightDedup`
(pre-enrich) call into it — these tests pin the helper's behaviour so
the two consumers cannot silently diverge.

The drift-prevention test at the bottom
(``test_extract_matches_push_to_backlog_url_seed_behavior``) is the
most important pin: it asserts that the URL hashes the helper emits
match what the previous inlined push_to_backlog logic produced for the
same input.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from genlab_core.pipeline.dedup_keys import (
    DedupKeys,
    _is_within_url_ttl,
    load_dedup_keys,
    story_is_blocked,
)


def _make_client(blueprints: list[dict]) -> MagicMock:
    client = MagicMock()
    client.blueprints.all.return_value = blueprints
    return client


def _bp(
    *,
    url: str = "https://www.youtube.com/watch?v=ABC123",
    video_id: str = "ABC123",
    title: str = "Some long title for testing",
    status: str = "PUBLISHED",
    created_days_ago: int | None = None,
) -> dict:
    """Build a blueprint row in the shape returned by ``backlog.blueprints.all``."""
    fields = {
        "video_url": url,
        "video_id": video_id,
        "title": title,
        "status": status,
    }
    record: dict = {"fields": fields}
    if created_days_ago is not None:
        # PostgresBackend post-PR-#501 surfaces created_at at TOP LEVEL.
        record["created_at"] = (datetime.now(UTC) - timedelta(days=created_days_ago)).isoformat()
    return record


# ── load_dedup_keys: basic loading ───────────────────────────────────────────


def test_load_dedup_keys_returns_url_hashes() -> None:
    """5 blueprints in blocking states → 5 URL hashes."""
    bps = [
        _bp(url=f"https://example.com/{i}", video_id=f"v{i}", title=f"some title number {i}")
        for i in range(5)
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config={})
    assert len(keys.url_hashes) == 5
    assert len(keys.video_ids) == 5
    assert keys.n_active_blueprints == 5


def test_load_dedup_keys_returns_normalized_titles() -> None:
    """Titles are lower-cased and stripped."""
    bps = [
        _bp(url="https://example.com/1", video_id="v1", title="  Some Long Mixed Case Title  "),
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config={})
    assert "some long mixed case title" in keys.titles_normalized


def test_load_dedup_keys_only_counts_blocking_states() -> None:
    """ARCHIVED / PUBLISH_FAILED don't block; PUBLISHED / DRAFTED / SCORED / VISUAL_READY do."""
    bps = [
        _bp(url="https://example.com/p", video_id="vp", status="PUBLISHED"),
        _bp(url="https://example.com/d", video_id="vd", status="DRAFTED"),
        _bp(url="https://example.com/s", video_id="vs", status="SCORED"),
        _bp(url="https://example.com/v", video_id="vv", status="VISUAL_READY"),
        _bp(url="https://example.com/pi", video_id="vpi", status="PUBLISHING"),
        # Non-blocking
        _bp(url="https://example.com/a", video_id="va", status="ARCHIVED"),
        _bp(url="https://example.com/pf", video_id="vpf", status="PUBLISH_FAILED"),
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config={})
    # PUBLISHED, DRAFTED, SCORED, VISUAL_READY, PUBLISHING = 5 blocking
    assert keys.n_active_blueprints == 5
    assert len(keys.url_hashes) == 5
    archived_hash = sha256(b"https://example.com/a").hexdigest()[:16]
    assert archived_hash not in keys.url_hashes


def test_load_dedup_keys_respects_url_ttl() -> None:
    """url_dedup_ttl_days=7 → blueprints older than 7d excluded from URL hashes."""
    bps = [
        _bp(url="https://stale.com/x", video_id="stale", created_days_ago=10),
        _bp(url="https://fresh.com/x", video_id="fresh", created_days_ago=3),
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(
        niche_id="gaming",
        backlog_client=client,
        niche_config={"pipeline": {"url_dedup_ttl_days": 7}},
    )
    assert len(keys.url_hashes) == 1
    fresh_hash = sha256(b"https://fresh.com/x").hexdigest()[:16]
    assert fresh_hash in keys.url_hashes


def test_load_dedup_keys_url_ttl_none_means_no_filter() -> None:
    """No url_dedup_ttl_days → blueprints block forever (legacy behaviour)."""
    bps = [_bp(url="https://ancient.com/x", video_id="anc", created_days_ago=365)]
    client = _make_client(bps)
    keys = load_dedup_keys(
        niche_id="anime",
        backlog_client=client,
        niche_config={},  # no TTL
    )
    assert len(keys.url_hashes) == 1


def test_load_dedup_keys_url_ttl_zero_means_no_filter() -> None:
    """TTL=0 must be treated as no-TTL (legacy block-forever)."""
    bps = [_bp(url="https://x.com/y", video_id="y", created_days_ago=100)]
    client = _make_client(bps)
    keys = load_dedup_keys(
        niche_id="gaming",
        backlog_client=client,
        niche_config={"pipeline": {"url_dedup_ttl_days": 0}},
    )
    assert len(keys.url_hashes) == 1


def test_load_dedup_keys_respects_title_ttl() -> None:
    """title_dedup_days=3 → titles from blueprints older than 3d excluded."""
    bps = [
        _bp(
            url="https://x.com/1",
            video_id="v1",
            title="old title that should fall out",
            created_days_ago=10,
        ),
        _bp(
            url="https://x.com/2",
            video_id="v2",
            title="new title that should stay in",
            created_days_ago=1,
        ),
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(
        niche_id="anime",
        backlog_client=client,
        niche_config={"pipeline": {"title_dedup_days": 3}},
    )
    assert "old title that should fall out" not in keys.titles_normalized
    assert "new title that should stay in" in keys.titles_normalized


def test_load_dedup_keys_fail_open_on_backend_exception() -> None:
    """Backend exception → empty keys, no raise (fail-OPEN posture)."""
    client = MagicMock()
    client.blueprints.all.side_effect = RuntimeError("Postgres connection lost")
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config={})
    assert keys.url_hashes == set()
    assert keys.titles_normalized == set()
    assert keys.video_ids == set()


def test_load_dedup_keys_default_title_ttl_is_seven_days() -> None:
    """Missing title_dedup_days → default 7d matches push_to_backlog history."""
    bps = [
        _bp(title="title from 5d ago should stay", created_days_ago=5),
        _bp(
            url="https://x.com/old",
            video_id="vold",
            title="title from 10d ago should drop",
            created_days_ago=10,
        ),
    ]
    client = _make_client(bps)
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config={})
    assert "title from 5d ago should stay" in keys.titles_normalized
    assert "title from 10d ago should drop" not in keys.titles_normalized


# ── story_is_blocked: per-story matching ────────────────────────────────────


def test_story_is_blocked_url_match_returns_url_reason() -> None:
    keys = DedupKeys(url_hashes={sha256(b"https://x.com/match").hexdigest()[:16]})
    story = {"source_url": "https://x.com/match", "title": "anything"}
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is True
    assert reason == "url"


def test_story_is_blocked_video_id_match_returns_video_id_reason() -> None:
    keys = DedupKeys(video_ids={"VIDX"})
    story = {"source_url": "https://different.com/u", "video_id": "VIDX", "title": "anything"}
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is True
    assert reason == "video_id"


def test_story_is_blocked_title_near_dupe_returns_title_reason() -> None:
    keys = DedupKeys(titles_normalized={"overwatch new season 11 launches today"})
    story = {
        "source_url": "https://new.com/u",
        "video_id": "newvid",
        "title": "Overwatch new season 11 launches today now",
    }
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is True
    assert reason == "title_near_dupe"


def test_story_is_blocked_no_match_returns_false() -> None:
    keys = DedupKeys(
        url_hashes={sha256(b"https://other.com/u").hexdigest()[:16]},
        titles_normalized={"completely different words here"},
        video_ids={"OTHER"},
    )
    story = {
        "source_url": "https://fresh.com/x",
        "video_id": "FRESH",
        "title": "Sand Raiders launches with new map dlc",
    }
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is False
    assert reason == ""


def test_story_is_blocked_url_takes_priority_over_video_id() -> None:
    """When both match, URL wins (cheapest + most specific check)."""
    keys = DedupKeys(
        url_hashes={sha256(b"https://x.com/match").hexdigest()[:16]},
        video_ids={"MATCH"},
    )
    story = {"source_url": "https://x.com/match", "video_id": "MATCH", "title": "anything"}
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is True
    assert reason == "url"


def test_story_is_blocked_skips_short_title() -> None:
    """Titles ≤10 chars are too generic for fuzzy match — must not fire."""
    keys = DedupKeys(titles_normalized={"short ttl"})
    story = {"source_url": "", "video_id": "", "title": "short ttl"}
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is False


def test_story_is_blocked_empty_url_doesnt_match_empty_hash() -> None:
    """A story without source_url must not match anything via URL hash."""
    keys = DedupKeys(url_hashes={sha256(b"").hexdigest()[:16]})  # contrived
    story = {"source_url": "", "title": "some unrelated story longer than ten chars"}
    blocked, reason = story_is_blocked(story, keys)
    assert blocked is False


# ── _is_within_url_ttl: pure helper edge cases ──────────────────────────────


def test_is_within_url_ttl_none_cutoff_short_circuits() -> None:
    """cutoff=None → True regardless of created_at."""
    bp = _bp(created_days_ago=365)
    assert _is_within_url_ttl(bp, None) is True


def test_is_within_url_ttl_missing_created_at_is_conservative() -> None:
    """Conservative posture — keep blueprint in dedup set on unknown age."""
    bp_no_date = {"fields": {"video_url": "x", "status": "PUBLISHED"}}
    cutoff = datetime.now(UTC) - timedelta(days=7)
    assert _is_within_url_ttl(bp_no_date, cutoff) is True


def test_is_within_url_ttl_recent_passes() -> None:
    bp = _bp(created_days_ago=3)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    assert _is_within_url_ttl(bp, cutoff) is True


def test_is_within_url_ttl_stale_excluded() -> None:
    bp = _bp(created_days_ago=10)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    assert _is_within_url_ttl(bp, cutoff) is False


# ── Drift-prevention test: helper output == push_to_backlog historical output ─


def test_extract_matches_push_to_backlog_url_seed_behavior() -> None:
    """Golden test: the helper's URL-hash output must match the inlined
    push_to_backlog logic for an identical input.

    If push_to_backlog and the helper ever diverge in what they consider
    "blocking" (e.g. a new state added to LIVE_OR_PENDING that the helper
    doesn't pick up), this test fires. The drift would otherwise be
    invisible — the two consumers would silently produce different
    survivor sets.

    Mirrors the seed loop that lived in push_to_backlog.py:1268-1280
    before this refactor.
    """
    from datetime import UTC, datetime, timedelta
    from hashlib import sha256 as _sha256

    from genlab_core.pipeline.stages.push_to_backlog import _is_blocking

    bps = [
        _bp(url="https://a.com/1", video_id="a1", status="PUBLISHED", created_days_ago=2),
        _bp(url="https://a.com/2", video_id="a2", status="DRAFTED", created_days_ago=4),
        _bp(
            url="https://a.com/3", video_id="a3", status="ARCHIVED", created_days_ago=1
        ),  # not blocking
        _bp(
            url="https://a.com/4", video_id="a4", status="PUBLISHED", created_days_ago=20
        ),  # past TTL
        _bp(url="https://a.com/5", video_id="a5", status="VISUAL_READY", created_days_ago=6),
    ]

    # The "old" inlined logic, reproduced verbatim
    url_ttl_days = 7
    url_cutoff = datetime.now(UTC) - timedelta(days=url_ttl_days)

    def _is_within_url_ttl_inlined(bp: dict) -> bool:
        if url_cutoff is None:
            return True
        from genlab_core.storage.record_helpers import record_created_at_dt

        created = record_created_at_dt(bp)
        if created is None:
            return True
        return created >= url_cutoff

    active_bps_inlined = [bp for bp in bps if _is_blocking(bp) and _is_within_url_ttl_inlined(bp)]
    expected_hashes: set[str] = set()
    for bp in active_bps_inlined:
        fields = bp.get("fields", bp)
        url = (fields.get("video_url") or "").strip()
        if url:
            expected_hashes.add(_sha256(url.encode()).hexdigest()[:16])

    # The "new" helper output
    client = _make_client(bps)
    keys = load_dedup_keys(
        niche_id="gaming",
        backlog_client=client,
        niche_config={"pipeline": {"url_dedup_ttl_days": 7}},
    )

    assert keys.url_hashes == expected_hashes, (
        "Helper output must match push_to_backlog's historical inlined logic. "
        "If this test fails, the two dedup paths have drifted apart — fix the "
        "helper or update the test only after auditing the divergence."
    )


# ── Source-pin tests (analogous to test_push_to_backlog_url_dedup_ttl) ──────


def test_helper_source_pins_url_dedup_ttl_key_path() -> None:
    """The url_dedup_ttl_days config key path must remain reachable.

    If a refactor moves the key (e.g. flattens niche_config), the
    regression would be invisible until a sticky-URL niche hits the
    dedup-saturation outage shape again. This pin catches the rename.
    """
    import genlab_core.pipeline.dedup_keys as mod

    src = Path(mod.__file__).read_text()
    assert 'niche_config.get("pipeline", {})' in src
    assert 'pipeline_cfg.get("url_dedup_ttl_days")' in src
    assert 'pipeline_cfg.get("title_dedup_days", 7)' in src


def test_dedupkeys_dataclass_default_counts() -> None:
    """Default-constructed DedupKeys carries zero counts + empty sets."""
    keys = DedupKeys()
    assert keys.url_hashes == set()
    assert keys.titles_normalized == set()
    assert keys.video_ids == set()
    assert keys.n_active_blueprints == 0
    assert keys.n_existing_stories == 0


@pytest.mark.parametrize(
    "ttl_value,expected_active",
    [
        (None, 2),  # no TTL → both block
        (7, 1),  # 7-day TTL → 3-day-old blocks, 10-day-old falls out
        (30, 2),  # generous TTL → both block
    ],
)
def test_load_dedup_keys_ttl_parametrised(ttl_value: int | None, expected_active: int) -> None:
    bps = [
        _bp(created_days_ago=3),
        _bp(url="https://x.com/old", video_id="vold", created_days_ago=10),
    ]
    client = _make_client(bps)
    cfg = {"pipeline": {"url_dedup_ttl_days": ttl_value}} if ttl_value is not None else {}
    keys = load_dedup_keys(niche_id="gaming", backlog_client=client, niche_config=cfg)
    assert keys.n_active_blueprints == expected_active


# ── dedup_keys._is_blocking_row / video_id_dedup.is_blocking parity ─────────
# Regression pin for the divergence discovered during DEV-2 (2026-07-08 audit).
# Before this fix, _is_blocking_row implemented only rule 1 (status in
# LIVE_OR_PENDING) while video_id_dedup.is_blocking implemented rule 1 AND
# rule 2 (committed-to-publish gate). The URL/title/video_id dedup set was
# populated from the smaller predicate — a SCHEDULED-approved blueprint's
# URL would fall OUT of the dedup set even though the video_id path would
# still block it. Silently created two behaviours for the same "should
# this content be considered a duplicate?" question.


def test_is_blocking_row_delegates_to_video_id_dedup_is_blocking() -> None:
    """_is_blocking_row MUST return whatever video_id_dedup.is_blocking
    returns for the same row. Locks in the single-source-of-truth.

    If a future edit re-inlines the check here without also updating
    video_id_dedup, this test fires on the FIRST row that exercises the
    divergent semantics."""
    from genlab_core.pipeline.dedup_keys import _is_blocking_row
    from genlab_core.pipeline.video_id_dedup import is_blocking

    rows = [
        # Rule 1: LIVE_OR_PENDING status
        {"fields": {"status": "PUBLISHED"}},
        {"fields": {"status": "SCHEDULED"}},
        {"fields": {"status": "VISUAL_READY"}},
        {"fields": {"status": "DRAFTED"}},
        # Rule 2: committed-to-publish gate — SCHEDULED-approved shape
        {
            "fields": {
                "status": "VISUAL_READY",
                "action_taken": "approved",
                "scheduled_for": "2026-07-09T06:30:00+00:00",
            }
        },
        # NOT blocking: archived
        {"fields": {"status": "ARCHIVED"}},
        # NOT blocking: rejected without commitment
        {"fields": {"status": "REJECTED", "action_taken": "rejected"}},
        # Malformed shapes
        {},
        {"fields": {}},
        {"fields": {"status": None}},
    ]
    for row in rows:
        assert _is_blocking_row(row) is is_blocking(row), (
            "_is_blocking_row diverges from video_id_dedup.is_blocking on "
            f"row {row!r}. The two predicates MUST return the same value for "
            "every input or the URL/title/video_id dedup set will drift "
            "from the video_id dedup gate — the exact class-of-bug found "
            "during the DEV-2 extraction (2026-07-08 audit)."
        )


def test_is_blocking_row_catches_scheduled_approved_case() -> None:
    """The specific case the pre-fix code missed: a VISUAL_READY blueprint
    with action_taken='approved' + scheduled_for populated. Prior to the
    2026-07-08 fix this returned False (rule 1 only), meaning the URL
    would silently fall out of the block set."""
    from genlab_core.pipeline.dedup_keys import _is_blocking_row

    row = {
        "fields": {
            "status": "VISUAL_READY",  # NOT in LIVE_OR_PENDING per default
            "action_taken": "approved",
            "scheduled_for": "2026-07-09T06:30:00+00:00",
        }
    }
    # If this fails, someone re-inlined _is_blocking_row without carrying
    # over rule 2. Restore the delegation to video_id_dedup.is_blocking.
    assert _is_blocking_row(row) is True
