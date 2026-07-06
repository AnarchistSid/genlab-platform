"""Pins the "Scheduled Posts Are Sacred" rule at the push_to_backlog
revive layer (task #525, 2026-07-06 hotfix).

Live-fire discovery
-------------------
On 2026-07-06 08:00 IST the ai_creators pipeline's push_to_backlog
stage revived a blueprint (Mon 12:05 IST publisher-fire commitment)
from SCHEDULED → VISUAL_READY and cleared ``action_taken='approved'``.
Without a fresh manual re-schedule the channel would have published
nothing that day.

Root cause: ``_is_blocking`` only checked ``status`` against
``_BLOCKING_STATUSES`` (LIVE_OR_PENDING = {PUBLISHED, PUBLISHING,
VISUAL_READY, DRAFTED, SCORED}). Blueprints with status='SCHEDULED'
were treated as non-blocking and got revived by the pipeline's
URL-match dedup path. A blueprint with ``action_taken='approved'``
and ``scheduled_for`` populated is a "committed to publish" row,
regardless of its status label.

Fix: `_is_blocking` now also returns True when
``action_taken == 'approved'`` AND ``scheduled_for`` is populated.

If this pin regresses, an approved blueprint scheduled for tomorrow
will silently get demoted the next time the same URL flows through
the pipeline — and the operator won't notice until publisher fires
with nothing to publish. See ``cleanup_safety.md`` and memory
``[[session-2026-07-05-sprint-activation-plus-9-follow-ups]]``.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import _is_blocking


# ── original status-only blocking behavior still holds ──────────────


def test_status_visual_ready_is_blocking():
    assert _is_blocking({"status": "VISUAL_READY"}) is True


def test_status_published_is_blocking():
    assert _is_blocking({"status": "PUBLISHED"}) is True


def test_status_drafted_is_blocking():
    """Retry-pending drafted rows are still blocking — matches the
    existing docstring / dedup intent."""
    assert _is_blocking({"status": "DRAFTED"}) is True


def test_status_archived_is_NOT_blocking():
    """Archived rows are revive-eligible by design — the whole
    ``non_blocking_match`` path relies on this."""
    assert _is_blocking({"status": "ARCHIVED"}) is False


def test_status_publish_failed_is_NOT_blocking():
    """PUBLISH_FAILED rows are retry-eligible."""
    assert _is_blocking({"status": "PUBLISH_FAILED"}) is False


# ── the new "committed to publish" gate ─────────────────────────────


def test_approved_and_scheduled_is_blocking_even_when_status_is_visual_ready():
    """The dashboard's ``_execute_review_action`` sets
    ``action_taken='approved'`` + ``scheduled_for=<slot>`` while
    LEAVING status at VISUAL_READY. That row was already blocking
    by the status rule, so this is a sanity check that the new gate
    doesn't accidentally regress the base case."""
    assert (
        _is_blocking(
            {
                "status": "VISUAL_READY",
                "action_taken": "approved",
                "scheduled_for": "2026-07-06T06:00:00+00:00",
            }
        )
        is True
    )


def test_approved_and_scheduled_is_blocking_even_when_status_is_SCHEDULED():
    """The real live-fire bug — status='SCHEDULED' isn't in
    _BLOCKING_STATUSES, but action_taken='approved' + scheduled_for
    populated is a committed-to-publish signal. Must block revive.
    """
    assert (
        _is_blocking(
            {
                "status": "SCHEDULED",
                "action_taken": "approved",
                "scheduled_for": "2026-07-06T06:00:00+00:00",
            }
        )
        is True
    )


def test_approved_without_scheduled_for_is_NOT_blocking():
    """action_taken=approved WITHOUT scheduled_for is a broken/dead
    row — no slot claimed. Fine to revive."""
    assert (
        _is_blocking(
            {
                "status": "ARCHIVED",
                "action_taken": "approved",
                "scheduled_for": None,
            }
        )
        is False
    )


def test_scheduled_for_without_approved_is_NOT_blocking():
    """scheduled_for populated but action_taken='rejected' means
    the operator explicitly killed a scheduled row — that's a legit
    revive-eligible state (e.g. auto-cleanup queued for a rejection)."""
    assert (
        _is_blocking(
            {
                "status": "ARCHIVED",
                "action_taken": "rejected",
                "scheduled_for": "2026-07-06T06:00:00+00:00",
            }
        )
        is False
    )


def test_scheduled_for_alone_is_NOT_blocking():
    """Legacy blueprint carrying a stale scheduled_for date but no
    action_taken can still be revived — the new gate requires BOTH
    approved + scheduled_for."""
    assert (
        _is_blocking(
            {
                "status": "ARCHIVED",
                "action_taken": None,
                "scheduled_for": "2020-01-01T00:00:00+00:00",
            }
        )
        is False
    )


# ── row-shape robustness ────────────────────────────────────────────


def test_wrapped_in_fields_dict_still_blocks_committed_to_publish():
    """The pipeline calls _is_blocking on rows that sometimes carry
    a ``fields`` dict wrapper (SharePoint list-item shape) and
    sometimes don't (Postgres row shape). Both must work."""
    wrapped = {
        "id": "abc",
        "fields": {
            "status": "SCHEDULED",
            "action_taken": "approved",
            "scheduled_for": "2026-07-06T06:00:00+00:00",
        },
    }
    assert _is_blocking(wrapped) is True
