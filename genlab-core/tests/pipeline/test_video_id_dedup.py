"""Pin tests for :mod:`genlab_core.pipeline.video_id_dedup`.

Covers :class:`VideoIdDedupGate` + the module-level :func:`is_blocking`
helper. These pins protect the extraction from :mod:`genlab_core.
pipeline.stages.push_to_backlog` — the inline video_id dedup block
that lived at ``push_to_backlog.py:1738-1778`` before this refactor.

Mirrors the style of ``tests/pipeline/test_dedup_keys.py``: MagicMock
for the BacklogClient, plain-dict story + context, one behaviour per
test.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest
from genlab_core.pipeline.video_id_dedup import (
    VideoIdDedupGate,
    VideoIdDedupResult,
    is_blocking,
)

# ── is_blocking: parametrised over the status taxonomy ──────────────────────


@pytest.mark.parametrize(
    "status,expected",
    [
        # In LIVE_OR_PENDING → blocking.
        ("DRAFTED", True),
        ("VISUAL_READY", True),
        ("PUBLISHED", True),
        ("PUBLISHING", True),
        ("SCORED", True),
        # NOT in LIVE_OR_PENDING → not blocking by status alone.
        ("SCHEDULED", False),  # only blocks when action_taken=approved + scheduled_for
        ("ARCHIVED", False),
        ("PUBLISH_FAILED", False),
        ("REJECTED", False),
        ("REMOVED_BY_META", False),
        ("unknown", False),
        ("", False),
    ],
)
def test_is_blocking_by_status(status: str, expected: bool) -> None:
    """LIVE_OR_PENDING drives the primary blocking gate."""
    assert is_blocking({"fields": {"status": status}}) is expected


def test_is_blocking_committed_to_publish_gate_fires_on_visual_ready() -> None:
    """action_taken=approved + scheduled_for → blocking even for non-LIVE_OR_PENDING status."""
    # VISUAL_READY is already blocking on its own; use SCHEDULED to
    # isolate rule 2. The committed-to-publish gate must fire even
    # when status is outside LIVE_OR_PENDING.
    row = {
        "fields": {
            "status": "SCHEDULED",
            "action_taken": "approved",
            "scheduled_for": "2026-07-09T16:35:00Z",
        }
    }
    assert is_blocking(row) is True


def test_is_blocking_flat_row_shape_supported() -> None:
    """Row without a nested ``fields`` dict falls back to top-level lookup."""
    # The pipeline calls _is_blocking on rows that sometimes carry
    # the ``fields`` wrapping and sometimes are flat (mock inputs vs
    # BacklogClient returns).
    assert is_blocking({"status": "DRAFTED"}) is True
    assert is_blocking({"status": "ARCHIVED"}) is False


def test_is_blocking_archived_short_circuits_over_rule_2() -> None:
    """QB-FIX-05 Y0: ARCHIVED row with approved+scheduled_for must NOT block.

    Prior behavior (fixed in QB-FIX-05 Y0): rule 2 fired regardless of
    status, so archived-approved-scheduled rows kept blocking URL
    re-fetch even though no publisher would ever pick them. X0-a
    surfaced 99 such phantom-blocker rows in prod across 5 niches.

    Regression pin: this shape must return False.
    """
    row = {
        "fields": {
            "status": "ARCHIVED",
            "action_taken": "approved",
            "scheduled_for": "2026-07-30T10:00:00Z",
        }
    }
    assert is_blocking(row) is False

    # Also verify flat shape.
    flat = {"status": "ARCHIVED", "action_taken": "approved", "scheduled_for": "2026-08-01T06:00:00Z"}
    assert is_blocking(flat) is False


# ── VideoIdDedupGate: happy paths + edge cases ──────────────────────────────


def _client_returning(rows: list[dict]) -> MagicMock:
    """Build a MagicMock BacklogClient whose ``blueprints.all`` returns *rows*."""
    client = MagicMock()
    client.blueprints.all.return_value = rows
    return client


def test_gate_empty_video_id_is_not_a_duplicate() -> None:
    """No video_id in story + no clip_index fallback → not a dupe (no way to check)."""
    client = _client_returning([])  # would return dupes if queried; must NOT be queried
    gate = VideoIdDedupGate(client, "gaming")
    story = {"story_id": "s1", "title": "some story"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is False
    assert result.video_id == ""
    assert result.active_dupe_count == 0
    # No BacklogClient query when no video_id — protects the API quota.
    client.blueprints.all.assert_not_called()


def test_gate_video_id_with_no_existing_blueprints_is_not_a_duplicate() -> None:
    """video_id present, backend returns [] → not a dupe."""
    client = _client_returning([])
    gate = VideoIdDedupGate(client, "gaming")
    story = {"story_id": "s1", "video_id": "ABC123", "title": "fresh story"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is False
    assert result.video_id == "ABC123"
    assert result.active_dupe_count == 0
    client.blueprints.all.assert_called_once_with(
        formula="{video_id}='ABC123'",
        niche_id="gaming",
        max_records=5,
    )


def test_gate_existing_drafted_blueprint_is_duplicate() -> None:
    """video_id + existing DRAFTED blueprint → duplicate."""
    client = _client_returning(
        [
            {"fields": {"video_id": "ABC123", "status": "DRAFTED"}},
        ]
    )
    gate = VideoIdDedupGate(client, "gaming")
    story = {"story_id": "s1", "video_id": "ABC123"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is True
    assert result.video_id == "ABC123"
    assert result.active_dupe_count == 1
    # Reason string mirrors the pre-extraction log format — grep-based
    # dashboards may match on this substring.
    assert "Video already blueprinted" in result.reason
    assert "video_id=ABC123" in result.reason
    assert "niche=gaming" in result.reason


def test_gate_existing_published_blueprint_is_duplicate() -> None:
    """video_id + existing PUBLISHED blueprint → duplicate."""
    client = _client_returning(
        [
            {"fields": {"video_id": "PUB999", "status": "PUBLISHED"}},
        ]
    )
    gate = VideoIdDedupGate(client, "sports")
    story = {"story_id": "s1", "video_id": "PUB999"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is True
    assert result.active_dupe_count == 1


def test_gate_only_archived_existing_is_not_a_duplicate() -> None:
    """video_id + existing ARCHIVED-only blueprint → not a dupe (archived doesn't block)."""
    client = _client_returning(
        [
            {"fields": {"video_id": "ARCVID", "status": "ARCHIVED"}},
            {"fields": {"video_id": "ARCVID", "status": "PUBLISH_FAILED"}},
        ]
    )
    gate = VideoIdDedupGate(client, "movies")
    story = {"story_id": "s1", "video_id": "ARCVID"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is False
    assert result.active_dupe_count == 0


def test_gate_clip_index_fallback_resolves_video_id() -> None:
    """No story['video_id'], but context['clip_index'] carries the YouTube URL → parsed."""
    client = _client_returning([])
    gate = VideoIdDedupGate(client, "anime")
    story = {"story_id": "s42", "title": "some story"}
    context = {
        "clip_index": {
            "clips": {
                "s42": {"source_url": "https://www.youtube.com/watch?v=FALLBACK1&t=30"},
            }
        }
    }
    result = gate.check(story, context)
    # video_id resolved via URL parse: v=FALLBACK1&t=30 → "FALLBACK1"
    assert result.video_id == "FALLBACK1"
    assert result.should_skip is False
    client.blueprints.all.assert_called_once_with(
        formula="{video_id}='FALLBACK1'",
        niche_id="anime",
        max_records=5,
    )


def test_gate_malformed_context_none_clip_index_does_not_crash() -> None:
    """context['clip_index']=None + context['clips']=None must not raise."""
    # This is the exact bug shape the ``or {}`` defensive coercions
    # guard against — some fetchers initialise the structure but
    # leave inner dicts unset on early-exit paths.
    client = _client_returning([])
    gate = VideoIdDedupGate(client, "gaming")
    story = {"story_id": "s1", "title": "missing video"}
    context: dict = {"clip_index": None}
    result = gate.check(story, context)
    assert result.should_skip is False
    assert result.video_id == ""

    # Same with clip_index present but clips subdict None:
    context2: dict = {"clip_index": {"clips": None}}
    result2 = gate.check(story, context2)
    assert result2.should_skip is False
    assert result2.video_id == ""


def test_gate_fail_open_on_backend_exception() -> None:
    """BacklogClient.blueprints.all raises → fail-open (should_skip=False)."""
    client = MagicMock()
    client.blueprints.all.side_effect = RuntimeError("Postgres connection lost")
    gate = VideoIdDedupGate(client, "sports")
    story = {"story_id": "s1", "video_id": "FAILVID"}
    context: dict = {}
    result = gate.check(story, context)
    assert result.should_skip is False
    assert result.video_id == "FAILVID"
    assert result.active_dupe_count == 0
    # Reason mentions the error so operators can trace the fail-open.
    assert "Postgres connection lost" in result.reason
    assert "allowing through" in result.reason


def test_gate_max_records_passed_through_to_query() -> None:
    """The ``max_records`` constructor arg is passed to blueprints.all."""
    client = _client_returning([])
    gate = VideoIdDedupGate(client, "gaming", max_records=42)
    story = {"story_id": "s1", "video_id": "MAXVID"}
    gate.check(story, {})
    client.blueprints.all.assert_called_once_with(
        formula="{video_id}='MAXVID'",
        niche_id="gaming",
        max_records=42,
    )


# ── Contract pins: dataclass shape + backwards-compat re-export ─────────────


def test_video_id_dedup_result_is_frozen() -> None:
    """VideoIdDedupResult is a frozen dataclass — mutations must raise.

    Freezing prevents callers from silently mutating the reason string
    or should_skip flag between the gate producing a result and the
    push loop consuming it.
    """
    result = VideoIdDedupResult(should_skip=False, video_id="X", reason="", active_dupe_count=0)
    with pytest.raises(FrozenInstanceError):
        result.should_skip = True  # type: ignore[misc]


def test_push_to_backlog_re_exports_is_blocking() -> None:
    """push_to_backlog._is_blocking is the same callable as video_id_dedup.is_blocking.

    Both symbols must point to ONE definition — the re-export in
    push_to_backlog preserves backwards compat for existing tests
    that import ``_is_blocking`` from the stage module, while the
    new module is the single source of truth.
    """
    from genlab_core.pipeline.stages.push_to_backlog import _is_blocking

    assert _is_blocking is is_blocking


def test_gate_partial_blocking_mixed_with_archived_still_fires() -> None:
    """One blocking + N archived → duplicate (blocking count == 1, not 0)."""
    client = _client_returning(
        [
            {"fields": {"video_id": "MIXVID", "status": "ARCHIVED"}},
            {"fields": {"video_id": "MIXVID", "status": "PUBLISHED"}},
            {"fields": {"video_id": "MIXVID", "status": "PUBLISH_FAILED"}},
        ]
    )
    gate = VideoIdDedupGate(client, "gaming")
    story = {"story_id": "s1", "video_id": "MIXVID"}
    result = gate.check(story, {})
    assert result.should_skip is True
    assert result.active_dupe_count == 1


def test_gate_non_youtube_source_url_does_not_populate_video_id() -> None:
    """clip_index fallback only fires on ``youtube`` in source_url."""
    client = _client_returning([])
    gate = VideoIdDedupGate(client, "movies")
    story = {"story_id": "s1"}
    # TikTok / Vimeo / RSS URL — video_id resolution ladder returns "".
    context = {
        "clip_index": {
            "clips": {"s1": {"source_url": "https://vimeo.com/12345"}},
        }
    }
    result = gate.check(story, context)
    assert result.video_id == ""
    assert result.should_skip is False
    client.blueprints.all.assert_not_called()
