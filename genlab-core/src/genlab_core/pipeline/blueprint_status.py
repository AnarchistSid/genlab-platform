"""Canonical blueprint statuses + the named subsets each pipeline stage cares
about.

Replaces two parallel `frozenset[str]` constants that lived inside individual
stages (`push_to_backlog._BLOCKING_STATUSES`, `pre_download_dedup._PRE_DOWNLOAD_BLOCKING`)
with one source of truth. The subset relationship between them — `LIVE ⊂
LIVE_OR_PENDING` — was previously implicit; making it a named container
documents the design intent and lets new stages opt into the right set
without restating the strings.

Status taxonomy at a glance::

    DRAFTED          # render failed / pending retry
    SCORED           # in flight before draft
    VISUAL_READY     # rendered + awaiting publish
    PUBLISHING       # publish in progress
    PUBLISHED        # live on at least one platform
    ARCHIVED         # demoted / superseded / auto-cleaned
    PUBLISH_FAILED   # terminal publish failure (retried separately)

Each status is a :class:`StrEnum` member so direct string comparisons keep
working (``BlueprintStatus.PUBLISHED == "PUBLISHED"``), serialisation stays
trivial, and existing DB records / YAML configs continue to round-trip.
"""

from __future__ import annotations

from enum import StrEnum


class BlueprintStatus(StrEnum):
    """Every status a blueprint row may carry.

    Using :class:`StrEnum` means members serialise and compare as their
    string value, so callers don't need to know whether they're holding an
    enum or a raw string — both work identically in ``==`` and ``in`` checks.
    """

    DRAFTED = "DRAFTED"
    SCORED = "SCORED"
    VISUAL_READY = "VISUAL_READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


# "Already live or about to be live" — what PreDownloadDedup uses to block
# re-downloading content that's already shipped (or imminently will). DRAFTED
# and SCORED are deliberately NOT here: a stuck DRAFTED means render failed
# and needs to retry next run; including it permanently stranded 96 sports
# blueprints mid-2026 before PR #66 fixed it.
LIVE: frozenset[BlueprintStatus] = frozenset(
    {
        BlueprintStatus.PUBLISHED,
        BlueprintStatus.PUBLISHING,
        BlueprintStatus.VISUAL_READY,
    }
)


# "Live or committed-in-progress" — what PushToBacklog uses for
# overwrite-protection. DRAFTED + SCORED are in flight (render failed,
# retry pending; or scored but not yet drafted) and PushToBacklog must
# *update* those rows rather than creating duplicates — so they need to
# look "active" to the push-time dedup, even though PreDownloadDedup
# correctly excludes them to allow retries.
#
# The subset relationship `LIVE ⊂ LIVE_OR_PENDING` encodes the policy:
# anything live blocks at both stages; anything in-flight blocks only at
# push time.
LIVE_OR_PENDING: frozenset[BlueprintStatus] = LIVE | frozenset(
    {
        BlueprintStatus.DRAFTED,
        BlueprintStatus.SCORED,
    }
)
