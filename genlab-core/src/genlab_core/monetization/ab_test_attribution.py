"""ab_tests reward-attribution consumer.

Closes the loop opened by PR #271's producer wire. The producer writes
``ab_tests`` rows whenever ``inject_cta`` assigns a CTA variant to a
blueprint. This module reads those active rows, joins them to
``affiliate_clicks`` via the ``candidate_id`` carried in
``ab_tests.extra``, computes a binary clicked/not-clicked outcome, and:

1. Flips ``ab_tests.status`` from ``'active'`` to ``'completed'``
2. Feeds the outcome into ``CTABandit.update(arm_id, platform, clicked)``
   so the Thompson sampling posterior actually learns from real clicks

Why click vs. impression/view? Anthropic-style binary outcome maps
cleanly to the Beta-Binomial posterior CTABandit uses. A future
refinement could weight by revenue (a $30 click > a $0.30 click) but
the binary signal is the right MVP — it's what the bandit's update()
already accepts.

Run periodically (e.g. via a systemd timer every hour). The
``run_attribution_pass()`` function is idempotent (only acts on
``status='active'`` rows) so re-running is safe.

Best-effort: any DB error or bandit failure is logged at WARNING and
swallowed. This is an analytics-sidecar; it must NEVER break publishing.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-3

logger = logging.getLogger(__name__)

# How old an ``active`` ab_tests row must be before we attribute. The
# delay lets affiliate clicks accumulate (typical conversion window is
# 24-48h post-publish). Rows newer than this are skipped — they'll be
# picked up by a future run.
_MIN_AGE_HOURS: int = 24


def _get_dsn() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def _fetch_attributable_rows(min_age_hours: int = _MIN_AGE_HOURS) -> list[dict[str, Any]]:
    """Return ab_tests rows ready for attribution.

    A row is ready when:
    - status = 'active' (not already attributed)
    - created_at <= NOW() - INTERVAL '24 hours' (gives clicks time to land)
    - extra->>candidate_id is present (the analytics join key)
    """
    dsn = _get_dsn()
    if not dsn:
        logger.debug("[abAttribution] DATABASE_URL unset, skipping")
        return []
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause
        from psycopg.rows import dict_row
    except ImportError:
        logger.debug("[abAttribution] psycopg not installed, skipping")
        return []
    try:
        with pg_connect(dsn, connect_timeout=5, niche_id="all") as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # SET app.niche_id = 'all' bypasses the per-tenant RLS so
                # this batch job sees every niche's rows. Same pattern used
                # in metric_collector + auto_approver.
                cur.execute("SET app.niche_id = 'all'")
                cur.execute(
                    """
                    SELECT id, niche_id, test_name, variant_a, extra, created_at
                    FROM ab_tests
                    WHERE status = 'active'
                      AND created_at <= NOW() - make_interval(hours => %s)
                      AND extra ? 'candidate_id'
                    ORDER BY created_at ASC
                    """,
                    (min_age_hours,),
                )
                return list(cur.fetchall())
    except Exception as exc:
        logger.warning("[abAttribution] fetch failed: %s", exc)
        return []


def _was_clicked(candidate_id: str, platform: str = "") -> bool:
    """Return True if at least one affiliate_clicks row references this
    candidate_id (via the ``utm_content`` field that cta_engine appends
    or the dedicated ``blueprint_id`` column).

    Note: candidate_id is the dedup hash that doubles as the analytics
    join key (per push_to_backlog's ab_test_recorder call). It's
    persisted in BOTH ``ab_tests.extra.candidate_id`` AND
    ``affiliate_clicks.blueprint_id`` (the column name is historical;
    it actually carries the candidate_id from cta_engine's
    ``_tracked_url`` builder).
    """
    if not candidate_id:
        return False
    dsn = _get_dsn()
    if not dsn:
        return False
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause
    except ImportError:
        return False
    try:
        with pg_connect(dsn, connect_timeout=5, niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute("SET app.niche_id = 'all'")
                # platform filter is optional — when present, narrows to
                # clicks from THAT platform's referrer; when absent,
                # counts any click for the candidate (sums across IG/
                # YT/FB/etc.)
                if platform:
                    cur.execute(
                        """
                        SELECT 1 FROM affiliate_clicks
                        WHERE blueprint_id = %s
                          AND (platform_source = %s OR platform_source = '')
                        LIMIT 1
                        """,
                        (candidate_id, platform),
                    )
                else:
                    cur.execute(
                        "SELECT 1 FROM affiliate_clicks WHERE blueprint_id = %s LIMIT 1",
                        (candidate_id,),
                    )
                return cur.fetchone() is not None
    except Exception as exc:
        logger.debug("[abAttribution] click-check failed for %s: %s", candidate_id, exc)
        return False


def _mark_completed(ab_test_id: str, clicked: bool) -> bool:
    """Flip ab_tests.status active → completed + record the outcome
    in extra.attributed_at + extra.clicked. Returns True on success."""
    dsn = _get_dsn()
    if not dsn:
        return False
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause
    except ImportError:
        return False
    try:
        now_iso = datetime.now(UTC).isoformat()
        with pg_connect(dsn, connect_timeout=5, niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute("SET app.niche_id = 'all'")
                cur.execute(
                    """
                    UPDATE ab_tests
                    SET status = 'completed',
                        updated_at = NOW(),
                        extra = extra ||
                                jsonb_build_object('attributed_at', %s::text,
                                                   'clicked', %s::boolean)
                    WHERE id = %s::uuid AND status = 'active'
                    """,
                    (now_iso, clicked, ab_test_id),
                )
                return cur.rowcount == 1
    except Exception as exc:
        logger.warning(
            "[abAttribution] update failed for ab_test_id=%s: %s",
            ab_test_id,
            exc,
        )
        return False


def run_attribution_pass(
    *,
    min_age_hours: int = _MIN_AGE_HOURS,
    bandit_updater: Any = None,
) -> dict[str, int]:
    """Run one attribution pass over the active ab_tests rows.

    Args:
        min_age_hours: Skip rows newer than this. Default 24h gives
            clicks time to land.
        bandit_updater: Optional callable
            ``(arm_id: str, platform: str, clicked: bool) -> None``. When
            provided, called for each attributed row to drive the
            CTABandit. When None, attribution updates the DB but doesn't
            feed the bandit (useful for backfill runs that don't want to
            mutate posterior state).

    Returns:
        Stats dict ``{"considered", "clicked", "not_clicked", "skipped"}``.
        Useful for logging + alerts.
    """
    rows = _fetch_attributable_rows(min_age_hours)
    stats = {"considered": len(rows), "clicked": 0, "not_clicked": 0, "skipped": 0}

    for row in rows:
        extra = row.get("extra") or {}
        if not isinstance(extra, dict):
            stats["skipped"] += 1
            continue
        candidate_id = extra.get("candidate_id", "") or ""
        if not candidate_id:
            stats["skipped"] += 1
            continue

        # platform extraction: cta_engine stores comma-separated platforms
        # in extra.platforms when multi-platform; first wins for
        # attribution purposes (more rigorous would attribute per-platform,
        # but we don't have per-platform clicks here yet).
        platforms_csv = extra.get("platforms", "") or ""
        platform = platforms_csv.split(",")[0].strip() if platforms_csv else ""

        clicked = _was_clicked(candidate_id, platform)
        if _mark_completed(row["id"], clicked):
            if clicked:
                stats["clicked"] += 1
            else:
                stats["not_clicked"] += 1
            # Optional bandit feedback
            if bandit_updater is not None:
                try:
                    arm_id = row.get("test_name", "") or row.get("variant_a", "")
                    if arm_id:
                        bandit_updater(arm_id, platform or "instagram", clicked)
                except Exception as exc:
                    logger.warning(
                        "[abAttribution] bandit_updater failed for %s: %s",
                        candidate_id,
                        exc,
                    )
        else:
            stats["skipped"] += 1

    logger.info(
        "[abAttribution] pass complete: considered=%d clicked=%d not_clicked=%d skipped=%d",
        stats["considered"],
        stats["clicked"],
        stats["not_clicked"],
        stats["skipped"],
    )
    return stats


def get_default_bandit_updater() -> Any:
    """Return a callable that feeds attribution into the per-niche
    CTABandit. Lazy import so this module stays importable without
    the bandit's torch/numpy dependency tree."""
    try:
        from genlab_core.monetization.cta_bandit import CTABandit
    except ImportError:
        return None

    _bandits: dict[str, Any] = {}

    def _updater(arm_id: str, platform: str, clicked: bool) -> None:
        # Bandit state is per-niche; for now use a 'shared' default.
        # When per-niche bandit state lands (W3.4 in the autonomy plan),
        # callers can override this to dispatch by niche.
        if "shared" not in _bandits:
            _bandits["shared"] = CTABandit()
        _bandits["shared"].update(arm_id, platform, clicked)

    return _updater


if __name__ == "__main__":  # pragma: no cover
    import logging as _logging
    import sys

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    bandit = get_default_bandit_updater()
    stats = run_attribution_pass(bandit_updater=bandit)
    sys.exit(0 if stats["considered"] >= 0 else 1)
