"""Layer-1 dedup: same source clip must not create multiple blueprints.

Extracted from :mod:`genlab_core.pipeline.stages.push_to_backlog` (the
inline block that lived at ``push_to_backlog.py:1738-1778`` before
this refactor). Isolates the "look up existing blueprints for this
``video_id`` + gate on blocking state" logic behind a single testable
primitive so it can be exercised in unit tests without spinning up
the full :class:`PushToBacklog` stage.

## Role in the dedup stack (CLAUDE.md, "DEDUP ARCHITECTURE (Sprint 62)")

Three layers prevent duplicates:

1. **video_id dedup** — THIS MODULE. Same clip never creates two
   blueprints. First and cheapest gate; runs per-story inside the
   push loop.
2. **DailyCapEnforcer** with ``niche_id`` — per-channel caps.
3. **PUBLISHED skip** — PushToBacklog won't overwrite
   PUBLISHED/VISUAL_READY blueprints (handled by the candidate_id
   check further down the same push loop; NOT this module).

## Scope

IN:
    * The ``video_id`` resolution ladder (story field → clip_index
      fallback → YouTube URL parse)
    * The ``BacklogClient.blueprints.all`` lookup by video_id
    * The blocking-state gate via :func:`is_blocking` (which mirrors
      the historical ``_is_blocking`` in push_to_backlog)
    * Fail-open on BacklogClient exceptions (matching pre-extraction
      behaviour at push_to_backlog.py:1777)

OUT (kept elsewhere):
    * URL / hook / title dedup — lives in
      :mod:`genlab_core.pipeline.dedup_keys`
    * Candidate_id-level dedup — still inline in push_to_backlog
      because it drives revive vs create branching that this
      primitive intentionally does not model.

## Backwards compatibility

``push_to_backlog.py`` re-exports ``_is_blocking`` from this module
so existing tests and callers keep working. That's the single-source-
of-truth pattern — one definition, imported wherever needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from genlab_core.pipeline.blueprint_status import LIVE_OR_PENDING as _BLOCKING_STATUSES

logger = logging.getLogger(__name__)


def is_blocking(row: dict) -> bool:
    """True if an existing blueprint row should block re-creation of the same content.

    Two independent gates make a blueprint blocking:

    1. ``status`` in ``LIVE_OR_PENDING`` — the original semantics:
       live, publishing, visual_ready, drafted, or scored.

    2. **Committed to publish**: ``action_taken == 'approved'`` AND
       ``scheduled_for`` is populated. This catches the case where
       the operator (via dashboard) or the auto-approver has
       committed the blueprint to a future publish slot, but its
       status hasn't yet migrated into ``LIVE_OR_PENDING`` (some
       code paths set the status to ``SCHEDULED`` or leave it at
       ``VISUAL_READY`` while populating ``scheduled_for`` — either
       way, reviving the row would silently drop the approval AND
       the schedule slot).

    Rule 2 was added 2026-07-06 after a live-fire watch caught the
    07:00 IST ai_creators pipeline demoting a Mon 12:05 IST
    publisher-fire commitment from SCHEDULED → VISUAL_READY,
    clearing ``action_taken='approved'``. Without a fresh manual
    reschedule the channel would have published nothing that day.
    See task #525 + ``cleanup_safety.md`` "Scheduled Posts Are
    Sacred".
    """
    fields = row.get("fields", row)
    if fields.get("status", "") in _BLOCKING_STATUSES:
        return True
    # Committed-to-publish gate: an operator (or the auto-approver
    # in enforce mode) has already promised this blueprint a publish
    # slot. Reviving would break the promise silently.
    if fields.get("action_taken") == "approved" and fields.get("scheduled_for"):
        return True
    return False


@dataclass(frozen=True)
class VideoIdDedupResult:
    """Outcome of one video_id dedup check.

    Fields:
        should_skip: True when the video_id is already blueprinted
            in a blocking state.
        video_id: The video_id used for the check (resolved from
            story dict or clip_index fallback). Empty string when
            no video_id could be resolved.
        reason: Human-readable explanation. Consumed by the push
            loop's logger + counter emission.
        active_dupe_count: How many existing blocking blueprints
            were found (>=1 when should_skip=True; 0 otherwise).
    """

    should_skip: bool
    video_id: str
    reason: str
    active_dupe_count: int


def _resolve_video_id(story: dict, context: dict) -> str:
    """Resolve the ``video_id`` for a story with the clip_index fallback ladder.

    Mirrors the pre-extraction resolution logic at
    ``push_to_backlog.py:1740-1757`` byte-for-byte:

    1. Direct: ``story['video_id']``.
    2. Fallback: look up ``story_id`` in
       ``context['clip_index']['clips']`` and parse the YouTube
       ``v=`` query parameter out of the ``source_url``.

    Defensive ``or {}`` at each ``get()`` step. Both
    ``context['clip_index']`` and ``clip_index['clips']`` can exist
    with value ``None`` (some fetchers initialise the structure but
    leave the inner dict unset on early-exit paths). Without the
    coercion, ``None.get(...)`` would crash mid-PushToBacklog and
    the surrounding try/except would log a generic story-push
    failure — losing the structural signal.
    """
    video_id = story.get("video_id") or ""
    if video_id:
        return video_id

    story_id = story.get("story_id") or ""
    if not story_id:
        return ""

    clip_index = context.get("clip_index") or {}
    clip_entry = (clip_index.get("clips") or {}).get(story_id) or {}
    source_url_for_vid = clip_entry.get("source_url") or ""
    if "youtube" in source_url_for_vid:
        return source_url_for_vid.split("v=")[-1].split("&")[0]
    return ""


class VideoIdDedupGate:
    """Layer-1 dedup: same source clip must not create multiple blueprints.

    Wraps the :class:`BacklogClient` query + the :func:`is_blocking`
    gate so the push loop can call :meth:`check` and get a clean
    result object.

    Fail-open — a :class:`BacklogClient` error returns
    ``should_skip=False`` with a warning-style reason so the story
    still gets a chance to proceed (matching the pre-extraction
    behaviour at ``push_to_backlog.py:1777``).
    """

    def __init__(self, client: Any, niche_id: str, *, max_records: int = 5) -> None:
        """Bind the gate to a specific niche + BacklogClient.

        Parameters
        ----------
        client:
            A :class:`BacklogClient` (or compatible duck-type with
            ``.blueprints.all(formula=..., niche_id=..., max_records=...)``).
        niche_id:
            Niche identifier (``"gaming"``, ``"sports"``, etc.) —
            used both for the SharePoint/Postgres formula filter
            and the log reason strings.
        max_records:
            Cap on how many candidate rows the backend returns for
            the video_id lookup. Historically 5 in the inline
            implementation; a duplicate check only needs one
            blocking match to fire, so a small cap is safe.
        """
        self._client = client
        self._niche_id = niche_id
        self._max_records = max_records

    def check(self, story: dict, context: dict) -> VideoIdDedupResult:
        """Return the dedup decision for one story.

        Resolves ``video_id`` from ``story['video_id']``, falling
        back to ``context['clip_index']`` lookup for the
        ``story_id`` (matches the pre-extraction fallback ladder).
        Empty ``video_id`` → not a duplicate (no way to check).

        On any :class:`BacklogClient` exception the gate returns
        ``should_skip=False`` (fail-open) — the caller may still
        proceed with the story, matching the pre-extraction inline
        behaviour.
        """
        video_id = _resolve_video_id(story, context)
        if not video_id:
            return VideoIdDedupResult(
                should_skip=False,
                video_id="",
                reason="no video_id — dedup check skipped",
                active_dupe_count=0,
            )

        try:
            existing_by_video = self._client.blueprints.all(
                formula=f"{{video_id}}='{video_id}'",
                niche_id=self._niche_id,
                max_records=self._max_records,
            )
        except Exception as exc:  # noqa: BLE001 — fail-OPEN posture
            reason = f"Video dedup check failed: {exc} — allowing through"
            logger.warning("[PUSH] %s", reason)
            return VideoIdDedupResult(
                should_skip=False,
                video_id=video_id,
                reason=reason,
                active_dupe_count=0,
            )

        active_dupes = [bp for bp in existing_by_video if is_blocking(bp)]
        if active_dupes:
            # Reason string mirrors the pre-extraction log format at
            # push_to_backlog.py:1770-1774 verbatim — grep-based
            # dashboards may match on this substring.
            reason = (
                f"Video already blueprinted: video_id={video_id[:20]} "
                f"niche={self._niche_id} — skipping"
            )
            return VideoIdDedupResult(
                should_skip=True,
                video_id=video_id,
                reason=reason,
                active_dupe_count=len(active_dupes),
            )

        return VideoIdDedupResult(
            should_skip=False,
            video_id=video_id,
            reason="",
            active_dupe_count=0,
        )
