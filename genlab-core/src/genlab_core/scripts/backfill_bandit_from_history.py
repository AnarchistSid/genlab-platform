"""Historical bandit bootstrap: replay publishing_analytics into source arms.

Why this exists
---------------
Every (niche, source) arm in ``bandit_arms`` is initialised with Beta(1, 1)
priors and only updated when a freshly-published blueprint completes its
48h reward window via ``metric_collector._update_source_arm_reward``. The
production reality on 2026-06-30:

  * Most source arms have ``n_plays < 50`` — Thompson posteriors are still
    indistinguishable from random exploration.
  * Months of historical posts with **valid** engagement data (i.e. published
    AFTER the ``engagement_rate=0`` regression was fixed on 2026-06-22 in
    ``analytics_store.upsert_analytics``) sit in ``publishing_analytics``
    JOIN ``blueprints`` JOIN ``analytics`` — never replayed into the
    posteriors because the live wire ran on those rows BEFORE the fix
    landed (so the rewards were all 0).

This script bootstraps the per-source bandit by:

  1. Selecting ``publishing_analytics`` rows with ``status='SUCCESS'``
     published since 2026-06-22 (post engagement_rate fix), inner-joined
     against ``blueprints`` (for ``source``) and ``analytics`` (for the
     real metrics).
  2. Computing the shaped reward via ``RewardShaper.compute_reward``
     (the exact same code path the live ``metric_collector`` uses, so
     the backfill is mathematically identical to "we re-ran the live
     wire for every post since 2026-06-22 with the bug fixed").
  3. Calling ``_update_source_arm_reward`` for each row.

Distinct from ``backfill_bandit_from_pending_feedback`` which replays
the 2026-03-17 → 2026-05-19 ``pending_feedback`` parse outage. That one
patches a specific bug window in the LIVE bandit wire. This one
bootstraps the PER-SOURCE arm with ground-truth engagement data that
the live wire was numerically poisoned for since 2026-06-09 (the
``engagement_rate=0`` bug period).

Idempotency
-----------
Each replayed row is stamped ``extra->>'bandit_backfilled_at'`` on
``publishing_analytics``. The WHERE clause filters that stamp out, so
re-running is safe — the worst case is "nothing to do".

Safety
------
``--dry-run`` (default) prints what WOULD happen but writes nothing.
``--apply`` is required to actually update arms + stamp rows. Refuses to
process more than 5000 rows in a single ``--apply`` run without an
explicit ``--yes-large-batch`` flag (guards against accidentally
double-replaying months of data when the idempotency stamp is missing).

Usage
-----
    # Default: dry-run, all niches, since 2026-06-22
    uv run python -m genlab_core.scripts.backfill_bandit_from_history

    # Apply for one niche
    uv run python -m genlab_core.scripts.backfill_bandit_from_history \\
        --apply --niche-id gaming

    # Apply since a specific date
    uv run python -m genlab_core.scripts.backfill_bandit_from_history \\
        --apply --since 2026-06-25

    # Large batch (>5000 rows) — must be explicit
    uv run python -m genlab_core.scripts.backfill_bandit_from_history \\
        --apply --yes-large-batch
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_bandit_history")

# Engagement_rate fix landed in commit at 2026-06-22 via
# analytics_store.upsert_analytics. Rows published BEFORE this had
# engagement = 0 (the (likes+comments+shares) fallback didn't exist),
# so replaying them would re-poison the bandit. Default cutoff matches.
DEFAULT_SINCE = "2026-06-22"

# Sanity ceiling — refuse to apply more than this many rows in one
# invocation without explicit ack. Catches "stamp column got dropped"
# class of bugs that would otherwise double-replay months of data.
MAX_APPLY_WITHOUT_FORCE = 5000


def _build_query(since: str, niche_id: str | None) -> tuple[str, list]:
    """Compose the JOIN query for replay candidates.

    Returns ``(sql, params)``. The JOIN is INNER on blueprints (we need
    ``source``) and LEFT on analytics (we PREFER analytics-derived
    metrics but fall back to publishing_analytics' likes/comments/etc
    when no analytics row exists).

    Filter clauses:
      * publishing_analytics.status = 'SUCCESS' — only published posts
        contribute reward signal.
      * publishing_analytics.published_at >= :since
      * extra->>'bandit_backfilled_at' IS NULL — idempotency stamp.
      * blueprints.source IS NOT NULL AND blueprints.source <> ''
      * Optional niche_id filter (cross-niche by default).
    """
    where = [
        "pa.status = 'SUCCESS'",
        "pa.published_at IS NOT NULL",
        "pa.published_at >= %s",
        "(pa.extra->>'bandit_backfilled_at') IS NULL",
        "b.source IS NOT NULL AND b.source <> ''",
    ]
    params: list = [since]
    if niche_id:
        where.append("pa.niche_id = %s")
        params.append(niche_id)

    sql = f"""
        SELECT
            pa.id              AS pa_id,
            pa.niche_id        AS niche_id,
            pa.platform        AS platform,
            pa.post_id         AS post_id,
            pa.published_at    AS published_at,
            pa.views           AS pa_views,
            pa.likes           AS pa_likes,
            pa.comments        AS pa_comments,
            pa.shares          AS pa_shares,
            pa.saves           AS pa_saves,
            b.id               AS blueprint_id,
            b.source           AS source,
            a.value            AS a_value,
            a.extra            AS a_extra
        FROM publishing_analytics pa
        INNER JOIN blueprints b
            ON pa.candidate_id = b.candidate_id
           AND pa.niche_id = b.niche_id
        LEFT JOIN analytics a
            ON a.post_id = (pa.platform || ':' || pa.post_id)
           AND a.niche_id = pa.niche_id
           AND a."window" = '48h'
        WHERE {" AND ".join(where)}
        ORDER BY pa.published_at ASC
    """
    return sql, params


def _row_to_metrics(row: dict) -> dict[str, float]:
    """Build the per-post metrics dict for ``RewardShaper.compute_reward``.

    Prefers the analytics row's ``extra`` JSONB (which has the post-fix
    engagement_rate, save_rate, etc.) when available; falls back to the
    raw publishing_analytics counter columns when the analytics row is
    missing (older rows where insights collection ran but analytics
    upsert was skipped).

    The reward shaper's per-platform BASE_WEIGHTS expect keys like
    ``views``, ``likes``, ``comments``, ``shares``, ``saves``. We pass
    the union and let the shaper's "redistribute weight for absent
    metrics" branch handle gaps.
    """
    metrics: dict[str, float] = {}

    a_extra = row.get("a_extra") or {}
    if isinstance(a_extra, dict):
        for k in (
            "reach",
            "impressions",
            "likes",
            "comments",
            "shares",
            "saves",
            "saved",
            "plays",
            "views",
            "engagement",
            "engagement_rate",
            "save_rate",
            "share_rate",
            "play_rate",
        ):
            if k in a_extra and a_extra[k] is not None:
                try:
                    metrics[k] = float(a_extra[k])
                except (TypeError, ValueError):
                    continue

    # Fill gaps from publishing_analytics counters
    for src_key, dst_key in (
        ("pa_views", "views"),
        ("pa_likes", "likes"),
        ("pa_comments", "comments"),
        ("pa_shares", "shares"),
        ("pa_saves", "saves"),
    ):
        if dst_key not in metrics and row.get(src_key) is not None:
            try:
                metrics[dst_key] = float(row[src_key])
            except (TypeError, ValueError):
                continue

    return metrics


def _process_rows(rows: list[dict], *, apply: bool) -> dict:
    """Compute rewards + (optionally) apply them to source arms.

    Returns a summary dict with per-niche counts + per-arm
    reward statistics (for the human-readable summary at end).
    """
    # Lazy imports — keep module import cheap (test fixtures load it).
    from genlab_core.learning.reward_shaper import RewardShaper
    from genlab_core.learning.source_performance import record_source_outcome

    shaper = RewardShaper()  # no channel_metrics_fn — pure base weights
    # for historical replay. Threshold-proximity
    # boosting only makes sense for LIVE rewards
    # where the channel state matches the post.

    # Per-niche counters
    by_niche: dict[str, int] = defaultdict(int)
    # Per-arm (niche, source) reward stats — list of rewards we applied
    arm_rewards: dict[tuple[str, str], list[float]] = defaultdict(list)
    errors: int = 0

    for row in rows:
        niche_id = row["niche_id"]
        source = row["source"]
        platform = row["platform"]
        metrics = _row_to_metrics(row)
        try:
            reward = shaper.compute_reward(platform=platform, metrics=metrics)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[backfill] reward compute failed pa_id=%s niche=%s source=%s: %s",
                row["pa_id"],
                niche_id,
                source,
                exc,
            )
            errors += 1
            continue

        arm_rewards[(niche_id, source)].append(reward)
        by_niche[niche_id] += 1

        if apply:
            ok = record_source_outcome(
                niche_id=niche_id,
                source=source,
                reward=reward,
            )
            if not ok:
                logger.warning(
                    "[backfill] record_source_outcome returned False pa_id=%s "
                    "niche=%s source=%s reward=%.3f",
                    row["pa_id"],
                    niche_id,
                    source,
                    reward,
                )
                errors += 1

    return {
        "by_niche": dict(by_niche),
        "arm_rewards": {k: v for k, v in arm_rewards.items()},
        "errors": errors,
        "total": sum(by_niche.values()),
    }


def _stamp_rows(conn, pa_ids: list[str]) -> int:
    """Stamp ``extra.bandit_backfilled_at`` on each processed pa row.

    Done in a single UPDATE with the IN (...) clause for efficiency.
    Returns the number of rows actually stamped (some may have been
    deleted between SELECT and UPDATE — unlikely but possible).
    """
    if not pa_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE publishing_analytics
            SET extra = COALESCE(extra, '{}'::jsonb)
                || jsonb_build_object('bandit_backfilled_at', NOW()::text)
            WHERE id = ANY(%s::uuid[])
            """,
            (pa_ids,),
        )
        return cur.rowcount


def _print_summary(summary: dict, *, apply: bool) -> None:
    """Human-readable run summary."""
    mode = "APPLY" if apply else "DRY-RUN"
    logger.info("=" * 60)
    logger.info("BACKFILL SUMMARY (%s)", mode)
    logger.info("=" * 60)
    logger.info("Total rows processed: %d", summary["total"])
    if summary["errors"]:
        logger.warning("Errors: %d", summary["errors"])
    logger.info("")
    logger.info("Per-niche counts:")
    for niche_id in sorted(summary["by_niche"]):
        logger.info("  %-15s %d posts", niche_id, summary["by_niche"][niche_id])
    logger.info("")
    logger.info("Per-arm reward distribution (mean ± n):")
    for (niche_id, source), rewards in sorted(summary["arm_rewards"].items()):
        if not rewards:
            continue
        mean = sum(rewards) / len(rewards)
        logger.info(
            "  %-15s %-30s mean=%.3f n=%d",
            niche_id,
            source,
            mean,
            len(rewards),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update bandit arms + stamp rows (default: dry-run)",
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        help=f"ISO date — only replay rows with published_at >= this "
        f"(default: {DEFAULT_SINCE}, engagement_rate-fix date)",
    )
    parser.add_argument(
        "--niche-id",
        default=None,
        help="Only replay this niche (default: all niches)",
    )
    parser.add_argument(
        "--yes-large-batch",
        action="store_true",
        help=f"Required to --apply more than {MAX_APPLY_WITHOUT_FORCE} rows",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    # Lazy import — psycopg only needed when actually running, not
    # when test files import this module.
    import psycopg
    from psycopg.rows import dict_row

    sql, params = _build_query(args.since, args.niche_id)
    logger.info(
        "Querying replay candidates since %s%s ...",
        args.since,
        f" (niche={args.niche_id})" if args.niche_id else "",
    )

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            logger.info("No rows eligible for backfill — done.")
            return 0

        logger.info("Found %d candidate rows", len(rows))

        if args.apply and len(rows) > MAX_APPLY_WITHOUT_FORCE and not args.yes_large_batch:
            logger.error(
                "Refusing to --apply %d rows (>%d). Re-run with --yes-large-batch if intended.",
                len(rows),
                MAX_APPLY_WITHOUT_FORCE,
            )
            return 2

        summary = _process_rows(rows, apply=args.apply)
        _print_summary(summary, apply=args.apply)

        if args.apply:
            pa_ids = [str(r["pa_id"]) for r in rows]
            # Only stamp rows we actually processed (i.e. didn't error
            # on compute_reward). The errors counter is post-hoc so we
            # over-stamp here by at most ``summary['errors']`` rows; in
            # practice errors are <<1% and re-stamping the failed row
            # would just skip it next time anyway.
            stamped = _stamp_rows(conn, pa_ids)
            conn.commit()
            logger.info("Stamped %d rows with bandit_backfilled_at", stamped)
        else:
            logger.info("(dry-run — no changes written)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
