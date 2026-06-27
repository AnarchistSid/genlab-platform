"""Shared dedup-keys loader for push_to_backlog + preflight stages.

ONE source of truth for "what's currently in the dedup set" — the set of
URL hashes / normalised titles / video_ids that would block a new
blueprint from being created by :class:`PushToBacklog`.

## Why this exists (history)

For most of 2026 the dedup logic lived inlined inside
``push_to_backlog.py`` (~260 lines starting at the
``# Load content_memory hashes + active-blueprint URLs for cross-run dedup``
comment). That worked when push_to_backlog was the only consumer — but it
made the dedup set unobservable until *after* enrich + render had spent
~280 seconds producing content that was then discarded.

PR #621 patched this by raising ``filter_top_n`` from 5 → 7 (a cheap
stop-gap that gives dedup 2 extra survivors). This module is the
structural fix: extract the dedup logic so a NEW preflight stage
(:class:`PreflightDedup`) can drop already-blocked stories BEFORE the
expensive enrich+render path, while push_to_backlog stays as the final
authority on what actually persists.

## Drift prevention

Both consumers — :class:`PreflightDedup` (pre-enrich) and
:class:`PushToBacklog` (post-render) — call into this module. If the
dedup rules change (new state added to ``LIVE_OR_PENDING``, title
similarity threshold tweaked, TTL semantics modified), both stages
automatically pick up the change together. A regression test pins the
behaviour against a representative input so the two-consumer divergence
risk stays visible to CI.

## Scope (what's IN scope vs OUT)

IN:
    * Blueprint-derived URL hashes (sha256[:16] of ``video_url``)
    * Blueprint-derived normalised titles (lower-cased, stripped)
    * Blueprint-derived ``video_id`` set
    * URL TTL filter from ``niche_config.pipeline.url_dedup_ttl_days``
    * Title TTL filter from ``niche_config.pipeline.title_dedup_days``
    * Blocking-status filter via :data:`LIVE_OR_PENDING`

OUT (intentionally kept in push_to_backlog.py):
    * Existing-hooks dedup (hook-level, not story-level)
    * ``content_memory`` cross-niche dedup (separate proxy)
    * ``content_pool`` cross-niche title check (separate SQL query)
    * Cross-niche source-prefix guard (``_FORBIDDEN_SOURCE_PREFIXES``)
    * ``_skip_llm`` no-hook drops (post-writing-stage signal)

The preflight stage runs BEFORE writing stages, so it cannot see
``_skip_llm`` or post-render-only signals. The OUT-of-scope checks
stay in push_to_backlog as the safety net.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from genlab_core.pipeline.blueprint_status import LIVE_OR_PENDING as _BLOCKING_STATUSES
from genlab_core.storage.record_helpers import record_created_at_dt

logger = logging.getLogger(__name__)


# Title fuzzy-match threshold + min-length constants are lifted verbatim
# from the push_to_backlog implementation. Centralising them here means
# a tweak applies to both consumers atomically.
_TITLE_MIN_LEN_FOR_DEDUP = 10
_TITLE_MIN_WORDS_FOR_DEDUP = 3
_TITLE_JACCARD_THRESHOLD = 0.65


def _is_blocking_row(row: dict) -> bool:
    """True if this blueprint should block re-creation of the same content."""
    fields = row.get("fields", row)
    return fields.get("status", "") in _BLOCKING_STATUSES


def _is_within_url_ttl(bp: dict, cutoff: datetime | None) -> bool:
    """True if the blueprint is recent enough to still block URL dedup.

    Mirrors the same helper in ``pre_download_dedup.py`` and the inlined
    helper that previously lived in ``push_to_backlog.py``. When
    ``cutoff`` is None (no TTL configured), every blueprint blocks
    forever. Missing or unparseable ``created_at`` → conservative: keep
    in the dedup set (don't risk re-publishing unknown-age content).
    """
    if cutoff is None:
        return True
    created = record_created_at_dt(bp)
    if created is None:
        return True
    return created >= cutoff


def _normalise_title(value: str) -> str:
    """Lower-case + strip — the canonical form for title comparisons."""
    return (value or "").strip().lower()


@dataclass
class DedupKeys:
    """The set of URL / title / video_id signatures that would block a new blueprint.

    Carries counts alongside the sets so callers can log "we loaded N
    URLs from M active blueprints" without re-counting.
    """

    url_hashes: set[str] = field(default_factory=set)
    titles_normalized: set[str] = field(default_factory=set)
    video_ids: set[str] = field(default_factory=set)
    n_active_blueprints: int = 0  # blueprints that survived blocking + TTL filters
    n_existing_stories: int = 0  # for logging context (informational)


def load_dedup_keys(
    *,
    niche_id: str,
    backlog_client: Any,
    niche_config: dict[str, Any] | None = None,
) -> DedupKeys:
    """Load all dedup signatures for the given niche.

    Mirrors the logic that lives in ``push_to_backlog.py`` (the
    ``# Load content_memory hashes + active-blueprint URLs`` block
    starting ~line 1189). Extracted here so both push_to_backlog AND
    :class:`PreflightDedup` use IDENTICAL logic — a refactor in one
    stays in lock-step with the other via the shared call into this
    function plus the regression test in
    ``tests/pipeline/test_dedup_keys.py::test_extract_match_push_to_backlog_behavior``.

    Parameters
    ----------
    niche_id:
        Niche identifier (``"gaming"``, ``"sports"``, etc.) — used both
        for the SharePoint/Postgres formula filter and the log lines.
    backlog_client:
        A :class:`BacklogClient` (or compatible duck-type with
        ``.blueprints.all(formula=..., max_records=...)``). The caller
        is responsible for fail-open semantics if construction fails —
        this function expects a working client.
    niche_config:
        The niche.yaml dict (or empty). Reads
        ``pipeline.url_dedup_ttl_days`` (TTL filter for URL/video_id
        sets) and ``pipeline.title_dedup_days`` (TTL for title set;
        default 7 matches push_to_backlog historical default).

    Returns
    -------
    DedupKeys
        Populated dataclass. On any backend exception the returned
        object has all-empty sets — the caller is expected to treat
        "no dedup keys loaded" as "let everything through" (fail-OPEN),
        same posture as the inlined push_to_backlog logic.
    """
    keys = DedupKeys()
    if niche_config is None:
        niche_config = {}

    pipeline_cfg = niche_config.get("pipeline", {}) if isinstance(niche_config, dict) else {}

    # URL TTL — sticky-source niches (gaming, sports) opt-in via
    # url_dedup_ttl_days to let older blueprints fall out of the dedup
    # set. None / missing / <=0 → no TTL, dedup forever.
    url_ttl_days = pipeline_cfg.get("url_dedup_ttl_days")
    url_cutoff = (
        datetime.now(UTC) - timedelta(days=url_ttl_days)
        if url_ttl_days and url_ttl_days > 0
        else None
    )

    # Title TTL — weekly-recurring titles (anime show names, sports
    # team names) opt-in via title_dedup_days to a shorter window.
    # Default 7 matches push_to_backlog historical behaviour.
    title_ttl_days = pipeline_cfg.get("title_dedup_days", 7)
    title_cutoff = datetime.now(UTC) - timedelta(days=title_ttl_days)

    try:
        recent_bps = backlog_client.blueprints.all(
            formula=f"{{niche_id}}='{niche_id}'",
            max_records=2000,
        )
    except Exception as exc:  # noqa: BLE001 — fail-OPEN posture
        logger.warning(
            "[dedup_keys] Blueprint load failed for niche=%s — returning empty keys: %s",
            niche_id,
            exc,
        )
        return keys

    active_bps = [
        bp for bp in recent_bps if _is_blocking_row(bp) and _is_within_url_ttl(bp, url_cutoff)
    ]
    keys.n_active_blueprints = len(active_bps)

    for bp in active_bps:
        fields = bp.get("fields", bp)
        url = (fields.get("video_url") or "").strip()
        if url:
            keys.url_hashes.add(sha256(url.encode()).hexdigest()[:16])
        vid = (fields.get("video_id") or "").strip()
        if vid:
            keys.video_ids.add(vid)

        # Title set obeys the same blocking-status filter as URLs but
        # uses its OWN cutoff (title_dedup_days). A blueprint whose
        # URL has aged out of the URL set may still be contributing
        # its title — and vice versa.
        title = _normalise_title(fields.get("title") or "")
        if title and len(title) > _TITLE_MIN_LEN_FOR_DEDUP:
            created = record_created_at_dt(bp)
            if created is None or created >= title_cutoff:
                keys.titles_normalized.add(title)

    logger.info(
        "[dedup_keys] niche=%s: %d URL hashes / %d titles / %d video_ids from %d active blueprints",
        niche_id,
        len(keys.url_hashes),
        len(keys.titles_normalized),
        len(keys.video_ids),
        keys.n_active_blueprints,
    )
    return keys


def story_is_blocked(story: dict[str, Any], keys: DedupKeys) -> tuple[bool, str]:
    """Check if a story matches any current dedup signature.

    Returns
    -------
    tuple[bool, str]
        ``(blocked, reason)`` where reason is one of:
        ``'url'``, ``'video_id'``, ``'title_near_dupe'``, or ``''``
        (not blocked).

    Match order matches push_to_backlog's historical order: URL first
    (cheapest, most specific), video_id second, title fuzzy last
    (most expensive, fuzzy). The first match wins — we don't accumulate
    multiple reasons.
    """
    source_url = (story.get("source_url") or "").strip()
    if source_url:
        url_hash = sha256(source_url.encode()).hexdigest()[:16]
        if url_hash in keys.url_hashes:
            return True, "url"

    video_id = (story.get("video_id") or "").strip()
    if video_id and video_id in keys.video_ids:
        return True, "video_id"

    title = _normalise_title(story.get("title") or "")
    if title and len(title) > _TITLE_MIN_LEN_FOR_DEDUP and keys.titles_normalized:
        title_words = set(title.split())
        if len(title_words) > _TITLE_MIN_WORDS_FOR_DEDUP:
            for existing in keys.titles_normalized:
                existing_words = set(existing.split())
                if len(existing_words) > _TITLE_MIN_WORDS_FOR_DEDUP:
                    intersection = len(title_words & existing_words)
                    union = len(title_words | existing_words)
                    if union > 0 and intersection / union > _TITLE_JACCARD_THRESHOLD:
                        return True, "title_near_dupe"

    return False, ""
