"""Daily Facebook post-survival check.

Audit ref: R-33.

The audit found that the documented "FB 24h survival check
(REMOVED_BY_META)" was unimplemented — zero occurrences in
``genlab-core/src/``, the Sprint-47 "DELETED status + alert" claim
was stale doc-drift. Meta-removed reels were never detected.

This module closes that gap:

  1. Find ``publishing_analytics`` rows where
        platform = 'facebook'
        status   = 'SUCCESS'
        published_at BETWEEN now() - max_age_hours AND now() - min_age_hours
        post_id IS NOT NULL
        AND extra->>'fb_survival_checked' IS NULL (first-pass) OR a re-check window.
  2. For each, call ``FacebookClient.check_post_alive(post_id)``.
        * True  → stamp ``extra.fb_survival_checked`` so we don't re-check daily.
        * False → flip ``status`` to ``REMOVED_BY_META`` + stamp
                  ``extra.removed_at``.
        * None  → log + skip (transient; try again next day).

Run via the ``genlab-fb-survival-check.service`` systemd unit; daily
at 06:00 UTC. The earliest a post can be checked is 24h after publish
(default ``min_age_hours=24``); the latest is 7 days (``max_age_hours=
168``) — far enough back to catch a missed daily run, narrow enough
to avoid full-table scans.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-3

logger = logging.getLogger(__name__)

REMOVED_STATUS = "REMOVED_BY_META"
EXTRA_KEY_CHECKED = "fb_survival_checked"
EXTRA_KEY_REMOVED = "removed_at"

# A published FB row progresses through these statuses as metric_collector
# walks the engagement windows: SUCCESS → INSIGHTS_6H → INSIGHTS_24H →
# INSIGHTS_48H → INSIGHTS_168H. Any of them is still a "live post that
# was successfully published" and is a valid survival-check target — a
# post can be removed at any age.
LIVE_POST_STATUSES = (
    "SUCCESS",
    "INSIGHTS_6H",
    "INSIGHTS_24H",
    "INSIGHTS_48H",
    "INSIGHTS_168H",
)


def find_candidates(
    *,
    dsn: str | None = None,
    min_age_hours: int = 24,
    max_age_hours: int = 168,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Pull FB SUCCESS rows in the survival-check window.

    Skips rows already marked checked (``extra->>'fb_survival_checked'``
    is set) so the daily run is idempotent and cheap.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, post_id, niche_id, published_at, extra
                  FROM publishing_analytics
                 WHERE platform = 'facebook'
                   AND status   = ANY(%s)
                   AND post_id IS NOT NULL
                   AND post_id <> ''
                   AND published_at IS NOT NULL
                   AND published_at <= now() - interval '{min_age_hours} hours'
                   AND published_at >= now() - interval '{max_age_hours} hours'
                   AND (extra->>%s) IS NULL
                 ORDER BY published_at DESC
                 LIMIT %s
                """,
                (list(LIVE_POST_STATUSES), EXTRA_KEY_CHECKED, limit),
            )
            return list(cur.fetchall())


def mark_checked(
    *,
    row_id: str,
    dsn: str | None = None,
) -> None:
    """Stamp ``extra.fb_survival_checked`` so we don't re-check the row tomorrow."""
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    timestamp = datetime.now(UTC).isoformat()
    with pg_connect(dsn, niche_id="all") as conn:
        # NOTE: every jsonb_build_object argument needs an explicit
        # ::text cast — psycopg3 won't infer types through a variadic
        # function, and a missing cast raises IndeterminateDatatype.
        conn.execute(
            """
            UPDATE publishing_analytics
               SET extra = COALESCE(extra, '{}'::jsonb)
                         || jsonb_build_object(%s::text, %s::text),
                   updated_at = now()
             WHERE id = %s
            """,
            (EXTRA_KEY_CHECKED, timestamp, row_id),
        )


def mark_removed(
    *,
    row_id: str,
    dsn: str | None = None,
) -> None:
    """Flip ``status`` → REMOVED_BY_META and stamp the removal timestamp.

    Both the status flip and the JSONB stamp happen in the same
    ``UPDATE`` so an interrupted run can't leave half-state.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    timestamp = datetime.now(UTC).isoformat()
    with pg_connect(dsn, niche_id="all") as conn:
        # Every jsonb_build_object argument needs an explicit ::text cast
        # (see mark_checked note). All four go through.
        conn.execute(
            """
            UPDATE publishing_analytics
               SET status = %s,
                   extra  = COALESCE(extra, '{}'::jsonb)
                          || jsonb_build_object(
                                 %s::text, %s::text,
                                 %s::text, %s::text),
                   updated_at = now()
             WHERE id = %s
            """,
            (
                REMOVED_STATUS,
                EXTRA_KEY_CHECKED,
                timestamp,
                EXTRA_KEY_REMOVED,
                timestamp,
                row_id,
            ),
        )


class RemovalRateExceeded(RuntimeError):
    """Raised when the survival check would flip too many rows as removed.

    A second-layer defense against a buggy classifier (or a wrong
    post_id schema, or a Graph-API behaviour change) silently
    nuking the analytics table. ``run_check`` short-circuits when
    the running removal ratio exceeds ``max_removal_rate``; nothing
    gets written, the caller sees the exception, an alert can fire.
    """


def run_check(
    *,
    client: Any | None = None,
    dsn: str | None = None,
    min_age_hours: int = 24,
    max_age_hours: int = 168,
    limit: int = 200,
    max_removal_rate: float = 0.30,
    min_examined_for_rate_check: int = 10,
) -> dict[str, int]:
    """Execute one pass of the survival check.

    Args:
        client: a ``FacebookClient`` (or anything with
            ``check_post_alive(post_id) -> bool | None``). Injected
            so tests don't need real credentials; defaults to a
            freshly-constructed ``FacebookClient`` reading env vars.
        dsn: Postgres connection. Defaults to ``DATABASE_URL``.
        min_age_hours/max_age_hours/limit: see :func:`find_candidates`.
        max_removal_rate: sanity guard. If the running removal ratio
            (``removed / examined``) exceeds this AND we've examined
            at least ``min_examined_for_rate_check`` rows, abort with
            :class:`RemovalRateExceeded` BEFORE writing the offending
            row. Default 0.30 — Meta's real removal rate is realistically
            <1% historically; anything above 30% is almost certainly a
            classifier or schema bug (see prod incident 2026-06-10
            where a post_id-prefix mismatch caused 89% false-positives).
        min_examined_for_rate_check: don't trip the guard on a single
            confirmed-removed row in a 1-row sample. Default 10.

    Returns:
        Dict with ``examined``, ``alive``, ``removed``, ``ambiguous``,
        and ``error`` counts. Sent to the systemd journal so an alert
        can fire on a sudden spike in removals.

    Raises:
        RemovalRateExceeded: when the rate guard trips.
    """
    if client is None:
        # Lazy import — keeps the module importable without
        # ``META_ACCESS_TOKEN`` (e.g. in unit tests).
        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient()

    counts = {
        "examined": 0,
        "alive": 0,
        "removed": 0,
        "ambiguous": 0,
        "error": 0,
    }

    candidates = find_candidates(
        dsn=dsn,
        min_age_hours=min_age_hours,
        max_age_hours=max_age_hours,
        limit=limit,
    )
    logger.info(
        "[fb_survival_check] examining %d FB rows (window: %dh..%dh ago)",
        len(candidates),
        min_age_hours,
        max_age_hours,
    )

    for row in candidates:
        counts["examined"] += 1
        post_id = row["post_id"]
        try:
            alive = client.check_post_alive(post_id)
        except Exception as exc:
            counts["error"] += 1
            logger.warning("[fb_survival_check] client error for %s: %s", post_id, exc)
            continue

        if alive is True:
            counts["alive"] += 1
            mark_checked(row_id=row["id"], dsn=dsn)
        elif alive is False:
            # Sanity guard: tentatively count, check rate, only mark.
            tentative_removed = counts["removed"] + 1
            if (
                counts["examined"] >= min_examined_for_rate_check
                and (tentative_removed / counts["examined"]) > max_removal_rate
            ):
                # Refuse to write. The whole batch is suspect.
                counts["removed"] = tentative_removed
                msg = (
                    f"[fb_survival_check] ABORT: removal rate "
                    f"{tentative_removed}/{counts['examined']} = "
                    f"{tentative_removed / counts['examined']:.0%} > "
                    f"{max_removal_rate:.0%}. Refusing to write. "
                    f"Investigate the classifier / post_id schema."
                )
                logger.error(msg)
                raise RemovalRateExceeded(msg)
            counts["removed"] = tentative_removed
            mark_removed(row_id=row["id"], dsn=dsn)
            logger.warning(
                "[fb_survival_check] REMOVED_BY_META: niche=%s post=%s (published %s)",
                row.get("niche_id"),
                post_id,
                row.get("published_at"),
            )
        else:
            counts["ambiguous"] += 1
            # Don't stamp checked — try again next run.
            logger.debug(
                "[fb_survival_check] ambiguous: post=%s — retrying next run",
                post_id,
            )

    logger.info(
        "[fb_survival_check] done: %s",
        json.dumps(counts, separators=(",", ":")),
    )
    return counts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m genlab_core.monitoring.fb_survival_check",
        description=(
            "Check whether 24h-old Facebook posts are still live; "
            "flip status to REMOVED_BY_META when Meta has taken them down."
        ),
    )
    p.add_argument(
        "--min-age-hours",
        type=int,
        default=24,
        help="Skip posts younger than this (default 24).",
    )
    p.add_argument(
        "--max-age-hours",
        type=int,
        default=168,
        help="Skip posts older than this (default 168 = 7 days).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max rows examined per run (default 200).",
    )
    p.add_argument("--dsn", help="Override DATABASE_URL.")
    p.add_argument(
        "--max-removal-rate",
        type=float,
        default=0.30,
        help=(
            "Abort + emit non-zero exit if removed/examined exceeds "
            "this ratio (default 0.30). The sanity guard added after "
            "the 2026-06-10 prod incident where a post_id-prefix "
            "mismatch caused 89%% false-positives."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        counts = run_check(
            dsn=args.dsn,
            min_age_hours=args.min_age_hours,
            max_age_hours=args.max_age_hours,
            limit=args.limit,
            max_removal_rate=args.max_removal_rate,
        )
    except RemovalRateExceeded as exc:
        # Non-zero exit so systemd surfaces the failure for alerting.
        print(json.dumps({"error": "removal_rate_exceeded", "message": str(exc)}))
        return 2
    print(json.dumps(counts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(main())
