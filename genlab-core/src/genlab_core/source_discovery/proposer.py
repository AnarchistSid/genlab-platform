"""Source-discovery proposer logic.

PR after #586 (2026-06-25). Reads the per-blueprint source_channel_id
shipped in PR #586 + joins with the analytics engagement data already
captured by the metric_collector. Returns ranked channel suggestions
the operator can use to decide which under-tracked source channels
deserve a slot in config/sources.yaml.

## Ranking heuristic

Sort by ``avg_engagement_per_clip`` DESC, then by ``clips_used``
DESC as a tiebreaker (more samples = more confidence in the
average). avg per-clip is the right primary because a noisy
channel with 50 clips at 100 engagement each looks WORSE than
a focused channel with 5 clips at 5,000 each — the focused
channel is what the operator wants to discover.

## min_clips filter

Default 2. A single high-engagement clip from a channel is
indistinguishable from luck. Operators tune via the kwarg. Setting
``min_clips=1`` surfaces every channel; setting ``min_clips=5``
restricts to channels with enough samples for statistical
confidence.

## Fail-OPEN

Returns [] on DB error / missing DSN. Same contract as the rest of
the read-side helpers shipped this session — the dashboard card
renders zero-state rather than crashing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelSuggestion:
    """One proposed source channel for an operator review.

    Frozen so the dashboard layer (or any consumer) can't
    accidentally mutate state mid-render — same safety rationale
    as the Tenant / User / NichePause / HealthSignal DTOs from
    earlier session PRs.
    """

    source_channel_id: str
    clips_used: int
    total_engagement: float
    avg_engagement: float
    last_used_at: datetime | None = None
    # Reserved for a future PR that joins channel metadata
    # (display name, subscriber count) from the YouTube API
    channel_name: str | None = None
    sample_blueprints: list[str] = field(default_factory=list)


def _connect():
    """Lazy-import + connect helper. Returns None on missing DSN
    or connection error — propose_source_channels then fail-OPEN
    with an empty list."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[source_discovery] DATABASE_URL not set; proposer disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open per contract
        logger.warning("[source_discovery] connection failed: %s", exc)
        return None


def propose_source_channels(
    niche_id: str,
    *,
    window_days: int = 30,
    min_clips: int = 2,
    top_n: int = 10,
) -> list[ChannelSuggestion]:
    """Rank source channels by per-clip engagement for one niche.

    Returns at most ``top_n`` ChannelSuggestion objects sorted by
    ``avg_engagement`` DESC, tiebroken by ``clips_used`` DESC.

    Args:
      niche_id    — required, the channel niche to rank within
      window_days — clamped to [1, 180]. Default 30 — covers a
                    typical 'recent trends' look-back without
                    surfacing year-old channels that may have
                    gone quiet.
      min_clips   — minimum blueprints per channel to qualify.
                    Default 2 (single-clip channels are noise).
      top_n       — clamped to [1, 100]. Default 10 — matches the
                    Mission Control card's row budget.
    """
    if not niche_id:
        return []

    # Defensive clamp — same belt-and-suspenders as PR #582's
    # stats_by_niche, also enforced at the endpoint layer
    window_days = max(1, min(180, int(window_days)))
    min_clips = max(1, int(min_clips))
    top_n = max(1, min(100, int(top_n)))

    conn_cm = _connect()
    if conn_cm is None:
        return []

    try:
        with conn_cm as conn:
            # The HAVING clause enforces min_clips at the SQL layer
            # so the wire payload stays small (no client-side
            # filter pass). ORDER BY uses the avg primary +
            # clips_used tiebreaker.
            rows = conn.execute(
                """
                SELECT
                    b.source_channel_id AS source_channel_id,
                    COUNT(DISTINCT b.id) AS clips_used,
                    COALESCE(SUM(a.value), 0)::float AS total_engagement,
                    COALESCE(AVG(a.value), 0)::float AS avg_engagement,
                    MAX(b.created_at) AS last_used_at,
                    ARRAY_AGG(DISTINCT b.candidate_id ORDER BY b.candidate_id)
                        FILTER (WHERE b.candidate_id IS NOT NULL) AS sample_candidates
                FROM blueprints b
                LEFT JOIN analytics a ON a.post_id = b.candidate_id
                WHERE b.niche_id = %s
                  AND b.source_channel_id IS NOT NULL
                  AND b.created_at > NOW() - make_interval(days => %s)
                GROUP BY b.source_channel_id
                HAVING COUNT(DISTINCT b.id) >= %s
                ORDER BY avg_engagement DESC, clips_used DESC
                LIMIT %s
                """,
                (niche_id, window_days, min_clips, top_n),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[source_discovery] propose_source_channels(%r) failed: %s",
            niche_id,
            exc,
        )
        return []

    suggestions: list[ChannelSuggestion] = []
    for row in rows:
        if isinstance(row, dict):
            ch_id = row.get("source_channel_id")
            clips = int(row.get("clips_used") or 0)
            total = float(row.get("total_engagement") or 0.0)
            avg = float(row.get("avg_engagement") or 0.0)
            last_used = row.get("last_used_at")
            samples = row.get("sample_candidates") or []
        else:
            ch_id, clips, total, avg, last_used, samples = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
            )
            clips = int(clips or 0)
            total = float(total or 0.0)
            avg = float(avg or 0.0)
            samples = samples or []

        if not ch_id:
            continue
        suggestions.append(
            ChannelSuggestion(
                source_channel_id=str(ch_id),
                clips_used=clips,
                total_engagement=total,
                avg_engagement=avg,
                last_used_at=last_used,
                # Cap sample list at 5 — UI only needs a few to render
                # tooltips / popovers; full list grows unbounded
                sample_blueprints=[str(s) for s in (samples or [])][:5],
            )
        )

    return suggestions
