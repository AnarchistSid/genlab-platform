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

# Idempotency stamp keys on publishing_analytics.extra (JSONB). Each
# backfill pass uses its own key so they advance independently:
#
# * STAMP_AGGREGATED — PR #571b legacy aggregated source arms
#   (``source:<source>``). Once set, that row never replays into the
#   aggregated bandit again. Frozen as historical record by the
#   2026-06-30 per-platform split.
#
# * STAMP_PER_PLATFORM — 2026-06-30 per-platform source arms
#   (``source:<source>__<platform>``). Independent of the aggregated
#   stamp: rows backfilled-aggregated before this PR shipped will
#   still be eligible for per-platform backfill on the next run.
STAMP_AGGREGATED = "bandit_backfilled_at"
STAMP_PER_PLATFORM = "platform_backfilled_at"


def _build_query(
    since: str,
    niche_id: str | None,
    *,
    stamp_key: str = STAMP_AGGREGATED,
) -> tuple[str, list]:
    """Compose the JOIN query for replay candidates.

    Returns ``(sql, params)``. The JOIN is INNER on blueprints (we need
    ``source``) and LEFT on analytics (we PREFER analytics-derived
    metrics but fall back to publishing_analytics' likes/comments/etc
    when no analytics row exists).

    ``stamp_key`` selects which idempotency stamp to check:
      * ``STAMP_AGGREGATED`` (default, PR #571b legacy) — replays into
        ``source:<source>`` aggregated arms.
      * ``STAMP_PER_PLATFORM`` (2026-06-30) — replays into
        ``source:<source>__<platform>`` per-platform arms. Independent
        stamp so a row already backfilled-aggregated still picks up
        the per-platform pass.

    Filter clauses:
      * publishing_analytics.status = 'SUCCESS' — only published posts
        contribute reward signal.
      * publishing_analytics.published_at >= :since
      * extra->>'<stamp_key>' IS NULL — idempotency stamp.
      * blueprints.source IS NOT NULL AND blueprints.source <> ''
      * Optional niche_id filter (cross-niche by default).
    """
    where = [
        "pa.status = 'SUCCESS'",
        "pa.published_at IS NOT NULL",
        "pa.published_at >= %s",
        # Stamp key interpolated as a literal — JSONB ->> operator
        # doesn't accept parameters for the key itself. Both keys
        # are module-constant identifiers so no injection surface.
        f"(pa.extra->>'{stamp_key}') IS NULL",
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
            ON pa.blueprint_id = b.id
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


def _process_rows(
    rows: list[dict],
    *,
    apply: bool,
    per_platform: bool = False,
) -> dict:
    """Compute rewards + (optionally) apply them to source arms.

    Returns a summary dict with per-niche counts + per-arm
    reward statistics (for the human-readable summary at end).

    Per-platform mode (2026-06-30)
    ------------------------------
    When ``per_platform=True``, each row is replayed into the
    per-platform arm ``source:<source>__<platform>`` instead of the
    aggregated arm. The existing 4 aggregated arms from PR #571b are
    NEVER mutated in per-platform mode — they sit untouched as
    legacy historical record alongside the new per-platform rows.

    The arm-rewards key changes shape too: ``(niche, source, platform)``
    in per-platform mode vs ``(niche, source, None)`` in legacy mode,
    so the summary printout doesn't conflate the two.
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
    # Per-arm (niche, source, platform_or_None) reward stats — list of
    # rewards we applied. Platform is None in legacy mode so the
    # summary printout keeps the existing 2-column shape for
    # operators familiar with PR #571b's output.
    arm_rewards: dict[tuple[str, str, str | None], list[float]] = defaultdict(list)
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

        # 2026-07-14: RewardShaper.compute_reward now returns None on
        # its own internal exception (was: silently returning 0.0
        # which trained the bandit on synthetic zeros). Skip cleanly
        # here so backfill doesn't feed None → downstream float ops.
        if reward is None:
            logger.warning(
                "[backfill] compute_reward returned None pa_id=%s niche=%s "
                "source=%s — skipping (shaper couldn't produce a real reward)",
                row["pa_id"],
                niche_id,
                source,
            )
            errors += 1
            continue

        # In per-platform mode the arm grouping key includes platform
        # so the printout shows YouTube/Facebook/etc as distinct rows
        # even when they share the same (niche, source). In legacy
        # mode platform=None to match PR #571b's existing summary.
        arm_key = (niche_id, source, platform if per_platform else None)
        arm_rewards[arm_key].append(reward)
        by_niche[niche_id] += 1

        if apply:
            ok = record_source_outcome(
                niche_id=niche_id,
                source=source,
                reward=reward,
                platform=platform if per_platform else None,
            )
            if not ok:
                logger.warning(
                    "[backfill] record_source_outcome returned False pa_id=%s "
                    "niche=%s source=%s platform=%s reward=%.3f",
                    row["pa_id"],
                    niche_id,
                    source,
                    platform if per_platform else "(aggregated)",
                    reward,
                )
                errors += 1

    return {
        "by_niche": dict(by_niche),
        "arm_rewards": {k: v for k, v in arm_rewards.items()},
        "errors": errors,
        "total": sum(by_niche.values()),
    }


def _stamp_rows(
    conn,
    pa_ids: list[str],
    *,
    stamp_key: str = STAMP_AGGREGATED,
) -> int:
    """Stamp ``extra.<stamp_key>`` on each processed pa row.

    Done in a single UPDATE with the IN (...) clause for efficiency.
    Returns the number of rows actually stamped (some may have been
    deleted between SELECT and UPDATE — unlikely but possible).

    ``stamp_key`` selects which idempotency stamp to set. Module-level
    constants ``STAMP_AGGREGATED`` and ``STAMP_PER_PLATFORM`` are the
    only valid keys; passed via parameter so the SQL is reused across
    both backfill passes. Interpolated as literal because JSONB
    ``jsonb_build_object`` requires a literal key string — module
    constants only, no caller-supplied keys, so no injection surface.
    """
    if not pa_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE publishing_analytics
            SET extra = COALESCE(extra, '{{}}'::jsonb)
                || jsonb_build_object('{stamp_key}', NOW()::text)
            WHERE id = ANY(%s::uuid[])
            """,
            (pa_ids,),
        )
        return cur.rowcount


def _print_summary(summary: dict, *, apply: bool, pass_label: str = "AGGREGATED") -> None:
    """Human-readable run summary.

    ``pass_label`` distinguishes the aggregated vs per-platform pass
    in the printout so operators running both can see which numbers
    belong to which pass.
    """
    mode = "APPLY" if apply else "DRY-RUN"
    logger.info("=" * 60)
    logger.info("BACKFILL SUMMARY — %s (%s)", pass_label, mode)
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
    for arm_key, rewards in sorted(summary["arm_rewards"].items()):
        if not rewards:
            continue
        # arm_key shape is (niche, source, platform_or_None); print
        # the platform column only when it's set so legacy aggregated
        # summaries keep their compact 3-column layout.
        niche_id, source, platform = arm_key
        mean = sum(rewards) / len(rewards)
        arm_label = source if platform is None else f"{source}__{platform}"
        logger.info(
            "  %-15s %-40s mean=%.3f n=%d",
            niche_id,
            arm_label,
            mean,
            len(rewards),
        )


def _run_pass(
    conn,
    *,
    since: str,
    niche_id: str | None,
    apply: bool,
    yes_large_batch: bool,
    stamp_key: str,
    per_platform: bool,
    pass_label: str,
) -> int:
    """Run one backfill pass (aggregated OR per-platform).

    Each pass queries publishing_analytics filtered by ``stamp_key
    IS NULL`` (so already-processed rows for THIS pass skip), replays
    the rewards into the corresponding arm shape (per-platform or
    aggregated), and stamps the rows on success.

    Returns 0 on success or no rows, 2 on the large-batch sanity
    refusal. Exceptions propagate to the outer ``main()`` per-pass
    error handler (a failed aggregated pass should NOT skip the
    per-platform pass — both pass through ``main()`` independently).
    """
    sql, params = _build_query(since, niche_id, stamp_key=stamp_key)
    logger.info(
        "[%s] Querying replay candidates since %s%s ...",
        pass_label,
        since,
        f" (niche={niche_id})" if niche_id else "",
    )

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    if not rows:
        logger.info("[%s] No rows eligible for backfill — done.", pass_label)
        return 0

    logger.info("[%s] Found %d candidate rows", pass_label, len(rows))

    if apply and len(rows) > MAX_APPLY_WITHOUT_FORCE and not yes_large_batch:
        logger.error(
            "[%s] Refusing to --apply %d rows (>%d). Re-run with --yes-large-batch if intended.",
            pass_label,
            len(rows),
            MAX_APPLY_WITHOUT_FORCE,
        )
        return 2

    summary = _process_rows(rows, apply=apply, per_platform=per_platform)
    _print_summary(summary, apply=apply, pass_label=pass_label)

    if apply:
        pa_ids = [str(r["pa_id"]) for r in rows]
        # Only stamp rows we actually processed (i.e. didn't error
        # on compute_reward). The errors counter is post-hoc so we
        # over-stamp here by at most ``summary['errors']`` rows; in
        # practice errors are <<1% and re-stamping the failed row
        # would just skip it next time anyway.
        stamped = _stamp_rows(conn, pa_ids, stamp_key=stamp_key)
        conn.commit()
        logger.info("[%s] Stamped %d rows with %s", pass_label, stamped, stamp_key)
    else:
        logger.info("[%s] (dry-run — no changes written)", pass_label)

    return 0


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
    parser.add_argument(
        "--skip-aggregated",
        action="store_true",
        help=(
            "Skip the legacy aggregated source-arm backfill pass. "
            "Use when you only want to populate per-platform arms "
            "(e.g. all rows are already stamped bandit_backfilled_at)."
        ),
    )
    parser.add_argument(
        "--skip-per-platform",
        action="store_true",
        help=(
            "Skip the 2026-06-30 per-platform source-arm backfill pass. "
            "Use to restore PR #571b's single-pass behaviour."
        ),
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

    # Two-pass strategy (2026-06-30):
    #   1. AGGREGATED — legacy source:<source> arms (PR #571b stamp).
    #      Pre-existing rows already stamped will skip. Stays idempotent.
    #   2. PER-PLATFORM — new source:<source>__<platform> arms with
    #      separate stamp so pre-existing rows backfilled-aggregated
    #      still get picked up here on the next invocation.
    #
    # Both passes share connection + share the large-batch sanity gate
    # per pass. Each pass's stamp is independent so a partial run
    # (e.g. aggregated succeeds, per-platform fails mid-way) leaves the
    # DB consistent: the next invocation resumes whichever stamp is
    # still NULL.
    exit_code = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if not args.skip_aggregated:
            rc = _run_pass(
                conn,
                since=args.since,
                niche_id=args.niche_id,
                apply=args.apply,
                yes_large_batch=args.yes_large_batch,
                stamp_key=STAMP_AGGREGATED,
                per_platform=False,
                pass_label="AGGREGATED",
            )
            exit_code = exit_code or rc

        if not args.skip_per_platform:
            rc = _run_pass(
                conn,
                since=args.since,
                niche_id=args.niche_id,
                apply=args.apply,
                yes_large_batch=args.yes_large_batch,
                stamp_key=STAMP_PER_PLATFORM,
                per_platform=True,
                pass_label="PER-PLATFORM",
            )
            exit_code = exit_code or rc

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
