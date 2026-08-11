"""Multi-window reward re-evaluation — PR Intervention-1.

Captures the late-tail engagement signal the 48h reward window currently
discards. YouTube tutorials + Facebook shares often accumulate over
days-weeks; the bandit's 48h snapshot misses that.

Design:
- ``recompute_late_reward(blueprint_id, window_days=7)`` fetches metrics
  at the extended window and computes a "would-be" reward using the same
  RewardShaper the 48h path uses.
- Logs (reward_48h, reward_7d, delta) per blueprint into a new
  ``late_reward_deltas`` audit trail so we can measure per-arm late-tail
  lift before we start feeding it back into bandit posteriors.
- Flag-off default: ``GENLAB_MULTI_WINDOW_REWARD_ENABLED`` controls
  whether we ALSO push a delta-only Beta update into the bandit.
  Without the flag we're pure telemetry; with it, arms that show
  late-tail lift get incremental posterior weight.

Wired via ``scripts/recompute_late_rewards.py`` from a systemd timer
firing daily at 04:00 UTC (after nightly reward window processing).

Fail-closed everywhere. If metric fetch or persister fails, we log +
skip that blueprint. Never blocks the pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Feature flag governing whether late reward updates ALSO push into the
# bandit posterior. Without the flag we're log-only — the operator can
# read late_reward_deltas rows and decide whether the lift is real.
_ENABLED_ENV = "GENLAB_MULTI_WINDOW_REWARD_ENABLED"


def _integration_enabled() -> bool:
    """Exact-'true' feature flag, mirroring strategy_phase pattern."""
    return os.environ.get(_ENABLED_ENV, "").lower() == "true"


@dataclass
class LateRewardDelta:
    """One blueprint's late-tail reward measurement."""

    blueprint_id: str
    niche_id: str
    arm_id: str
    platform: str
    reward_48h: float
    reward_late: float
    window_days: int
    delta: float
    delta_pct: float  # (reward_late - reward_48h) / reward_48h, or 0 if base=0
    measured_at: datetime


# Absolute-delta threshold for significance when reward_48h == 0.
# Rationale: reward is in [0, 1]; a 0.05 late reward from a zero base
# means the post accumulated 5% of "top-tier" engagement in the late
# window despite bombing at 48h. That's a real signal the bandit
# would miss if we only gated on delta_pct (which is 0 when base=0).
# Threshold picked to match the ~20% relative-lift signal magnitude
# (0.05 absolute out of ~0.25 typical top-tier reward ≈ 20%).
_SIGNIFICANT_ABSOLUTE_LIFT_FROM_ZERO_BASE = 0.05


def _is_significant_lift(delta: LateRewardDelta) -> bool:
    """Return True iff the late-reward measurement crosses the "worth
    pushing to bandit" bar.

    Two signals:
      * ``abs(delta_pct) > 0.20`` — the primary criterion. 20% relative
        lift (positive or negative) is material.
      * ``reward_48h == 0`` AND ``abs(delta) >= 0.05`` — the base-zero
        fallback. When the 48h reward is 0, delta_pct is defined as 0
        (division by zero), so the primary criterion is blind. Every
        bombed-at-48h-recovered-at-7d post falls into this bucket —
        prior to 2026-07-14 the gate NEVER triggered for these posts,
        even though they carry the strongest late-tail-lift signal in
        the dataset.
    """
    if abs(delta.delta_pct) > 0.20:
        return True
    if delta.reward_48h == 0.0 and abs(delta.delta) >= _SIGNIFICANT_ABSOLUTE_LIFT_FROM_ZERO_BASE:
        return True
    return False


def recompute_late_reward(
    blueprint_id: str,
    window_days: int = 7,
    *,
    conn: Any = None,
    shaper: Any = None,
    fetch_platform_metrics_fn: Any = None,
    platform: str = "",
) -> LateRewardDelta | None:
    """Re-fetch metrics at the extended window and compute the late reward.

    Args:
        blueprint_id: UUID of the blueprint to re-evaluate.
        window_days: Extended window (default 7d).
        conn: Optional psycopg connection (real caller passes prod conn;
            tests inject mocks).
        shaper: Optional RewardShaper instance. Constructed by caller
            OR test-injected.
        fetch_platform_metrics_fn: Injectable metric fetcher.
        platform: Optional platform filter (2026-07-23 add). When empty,
            the SQL's ``LIMIT 1`` picks whichever platform's row the DB
            returns first — historically Facebook, because it happens to
            sort first in Postgres insertion order. Result: only FB got
            late_reward_delta rows despite blueprints publishing to
            IG/YT/Threads too. When non-empty, filters to that specific
            platform (still LIMIT 1 — one row per bp × platform pair).
            Caller (``process_late_reward_batch``) iterates over all
            (blueprint_id, platform) combos to get full coverage.

    Returns:
        LateRewardDelta on success; None on any failure (fail-closed).
        Caller decides whether to persist / act on the delta.
    """
    if conn is None:
        try:
            import psycopg
            from psycopg.rows import dict_row

            dsn = os.environ.get("DATABASE_URL", "").strip()
            if not dsn:
                logger.warning("late_reward: no DATABASE_URL — skip %s", blueprint_id)
                return None
            conn = psycopg.connect(dsn, row_factory=dict_row)
            own_conn = True
        except Exception as exc:
            logger.warning("late_reward: connect failed err=%s", exc)
            return None
    else:
        own_conn = False

    # 2026-07-02 SQL fix: the original query referenced two non-existent
    # columns and silently no-op'd Intervention 1 for 7+ weeks in prod.
    # (Details in [[late-reward-sql-bug-2026-07-02]] — kept out of this
    # comment so the pin test that checks for the broken column names
    # doesn't false-positive on this docstring.)
    #
    # The pending_feedback.post_id shape is not perfectly consistent
    # with publishing_analytics.post_id (facebook has a legacy double-
    # prefix: ``facebook:facebook:<id>`` vs ``facebook:<id>``), so we
    # match on suffix via ``LIKE '%' || pa.post_id`` — safest predicate
    # that handles both shapes. YouTube / Instagram / TikTok match
    # exactly under this LIKE too.
    #
    # Verified 2026-07-02 against prod's 6-8d window: 10/10 blueprints
    # resolve; 9 with reward_48h would fire the persist path that had
    # been silently dead. See ``[[late-reward-sql-bug-2026-07-02]]`` for
    # the discovery trail.
    # 2026-07-23: when platform filter passed, add strict equality to the
    # WHERE clause so the LIMIT 1 picks the requested platform's row.
    # Empty platform preserves pre-fix behaviour (whichever row Postgres
    # returns first — historically FB).
    _platform_filter_sql = " AND pa.platform = %s" if platform else ""
    _query_params: tuple = (blueprint_id, platform) if platform else (blueprint_id,)

    try:
        row = conn.execute(
            f"""
            SELECT b.id, b.niche_id, b.arm_id, pa.platform,
                   pa.post_id AS platform_post_id,
                   pa.published_at,
                   p.reward_48h
            FROM blueprints b
            JOIN publishing_analytics pa ON pa.blueprint_id = b.id
            LEFT JOIN pending_feedback p
                   ON p.platform = pa.platform
                  AND p.post_id = pa.post_id
            WHERE b.id = %s::uuid
              AND pa.status IN (
                  'SUCCESS',
                  'INSIGHTS_6H',
                  'INSIGHTS_24H',
                  'INSIGHTS_48H',
                  'INSIGHTS_168H'
              ){_platform_filter_sql}
            LIMIT 1
            -- 2026-07-14 (learning-wire audit F3): switched from
            -- `LIKE '%%' || pa.post_id` to strict equality. The LIKE
            -- was a legacy workaround for the pre-#748 double-prefix
            -- corruption ("facebook:facebook:123..." on write, single-
            -- strip on read → "facebook:123..." mismatch on JOIN).
            -- #748 backfilled 297 rows on 2026-07-09 + fixed the
            -- normalize idempotency, so both sides now share the
            -- canonical shape. The LIKE was ALSO matching unrelated
            -- post_ids where one was a suffix of another (e.g.
            -- pa.post_id='123' matched p.post_id='xyz-9123') —
            -- silently attributing wrong reward_48h to unrelated
            -- pending_feedback rows.
            """,
            _query_params,
        ).fetchone()
    except Exception as exc:
        logger.warning("late_reward: DB read failed bp=%s err=%s", blueprint_id, exc)
        if own_conn:
            conn.close()
        return None

    if not row:
        if own_conn:
            conn.close()
        return None

    platform = row.get("platform") if hasattr(row, "get") else row[3]
    platform_post_id = row.get("platform_post_id") if hasattr(row, "get") else row[4]
    niche_id = row.get("niche_id") if hasattr(row, "get") else row[1]
    arm_id = row.get("arm_id") if hasattr(row, "get") else row[2]
    reward_48h = row.get("reward_48h") if hasattr(row, "get") else row[6]
    # 2026-08-11 Task B: reward_48h may be NULL because Task A returned
    # None (premature-fetch signal on IG/Threads all-zero metrics). Rather
    # than skipping such blueprints entirely, use the 168h late reward as
    # the FIRST authoritative reward: backfill it into pending_feedback
    # so the bandit posterior picks it up on next backfill cycle. This
    # closes the "IG delay bias" leak — sports IG posts that eventually
    # accumulate views now train the bandit with correct data.
    reward_48h_was_null = reward_48h is None

    # Fetch late-window metrics (injectable for tests)
    if fetch_platform_metrics_fn is None:
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        fetch_platform_metrics_fn = fetch_platform_metrics

    # Use a "late" window key that fetch_platform_metrics supports —
    # the collector already has 168h (7d) semantics; we pass str form.
    try:
        metrics = fetch_platform_metrics_fn(platform, platform_post_id, "168h", niche_id=niche_id)
    except Exception as exc:
        logger.warning(
            "late_reward: metric fetch failed bp=%s platform=%s err=%s",
            blueprint_id,
            platform,
            exc,
        )
        if own_conn:
            conn.close()
        return None
    if not metrics:
        if own_conn:
            conn.close()
        return None

    # Compute late reward via same shaper the 48h path uses. Injectable.
    #
    # Intervention 10 (2026-07-01): must pass ``percentile_targets_fn``
    # to keep the late-window reward comparable to the 48h reward. Prod
    # metric_collector wires percentile targets on the 48h compute (see
    # ``learning/metric_collector.py:1218``). If this shaper omitted it,
    # reward_late would use hardcoded targets (e.g. YT views=200) while
    # reward_48h uses percentile-relative — the ``delta = reward_late -
    # reward_48h`` would then measure the target-shape delta, not the
    # actual engagement lift, making late_reward_deltas ~meaningless.
    if shaper is None:
        from genlab_core.learning.metric_collector import (
            get_channel_metrics as _channel_fn,
        )
        from genlab_core.learning.percentile_targets import get_percentile_target
        from genlab_core.learning.reward_shaper import RewardShaper

        shaper = RewardShaper(
            channel_metrics_fn=_channel_fn,
            percentile_targets_fn=get_percentile_target,
            niche_id=niche_id or "",
        )
    try:
        reward_late = shaper.compute_reward(platform=platform, metrics=metrics)
    except Exception as exc:
        logger.warning("late_reward: shaper failed bp=%s err=%s", blueprint_id, exc)
        if own_conn:
            conn.close()
        return None

    # 2026-07-14: RewardShaper.compute_reward can now return None
    # (was 0.0 on exception). None means "shaper couldn't produce a
    # reward" — skip this delta measurement rather than treating it
    # as 0.0 late reward (which would be indistinguishable from a
    # bombed 7d post).
    if reward_late is None:
        logger.warning(
            "late_reward: shaper returned None bp=%s platform=%s — skipping delta measurement",
            blueprint_id,
            platform,
        )
        if own_conn:
            conn.close()
        return None

    # 2026-08-11 Task B: backfill path — reward_48h was NULL (from
    # Task A's premature-fetch signal), and we now have a real
    # reward_late from 168h. Write it as the first authoritative
    # reward_48h for this blueprint/platform so the standard bandit
    # backfill script (backfill_bandit_from_pending_feedback.py) picks
    # it up on next fire. No delta to record (nothing to compare
    # against), so return None here after the write.
    if reward_48h_was_null:
        try:
            conn.execute(
                """
                UPDATE pending_feedback
                SET reward_48h = %s,
                    extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                        'reward_backfilled_from_168h', now()::text,
                        'reward_backfilled_reason', 'task_b_premature_fetch_recovery'
                    )
                WHERE post_id = %s
                  AND platform = %s
                  AND reward_48h IS NULL
                """,
                (float(reward_late), platform_post_id, platform),
            )
            conn.commit()
            logger.info(
                "late_reward.backfilled bp=%s platform=%s reward_late=%.3f "
                "— no prior 48h reward (Task A premature-fetch recovery); "
                "reward_late written as authoritative reward_48h for bandit "
                "backfill",
                blueprint_id,
                platform,
                reward_late,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "late_reward.backfill_failed bp=%s platform=%s err=%s",
                blueprint_id,
                platform,
                exc,
            )
        if own_conn:
            conn.close()
        # Return None — no LateRewardDelta to record (we don't have a
        # 48h baseline to measure delta against; the write above IS the
        # signal we care about).
        return None

    delta = float(reward_late) - float(reward_48h)
    delta_pct = (delta / float(reward_48h)) if reward_48h else 0.0

    result = LateRewardDelta(
        blueprint_id=blueprint_id,
        niche_id=niche_id or "",
        arm_id=arm_id or "",
        platform=platform,
        reward_48h=float(reward_48h),
        reward_late=float(reward_late),
        window_days=window_days,
        delta=delta,
        delta_pct=delta_pct,
        measured_at=datetime.now(UTC),
    )
    logger.info(
        "late_reward.measured bp=%s niche=%s arm=%s platform=%s "
        "48h=%.3f late=%.3f delta=%.3f delta_pct=%.2f",
        blueprint_id,
        niche_id,
        arm_id,
        platform,
        reward_48h,
        reward_late,
        delta,
        delta_pct * 100,
    )

    if own_conn:
        conn.close()
    return result


def process_late_reward_batch(
    days_ago_min: int = 6,
    days_ago_max: int = 8,
    *,
    conn: Any = None,
    push_to_bandit: bool | None = None,
) -> dict[str, int]:
    """Iterate over blueprints published 6-8 days ago; compute late reward
    for each and log delta.

    ``push_to_bandit`` defaults to the feature-flag setting. When True,
    also push a delta-only Beta update into the bandit for arms showing
    material late-tail lift (|delta_pct| > 20%).
    """
    if push_to_bandit is None:
        push_to_bandit = _integration_enabled()

    counters = {"scanned": 0, "measured": 0, "significant_lift": 0, "errors": 0}

    if conn is None:
        try:
            import psycopg
            from psycopg.rows import dict_row

            dsn = os.environ.get("DATABASE_URL", "").strip()
            if not dsn:
                logger.warning("late_reward.batch: DATABASE_URL not set — skipping batch")
                return counters
            conn = psycopg.connect(dsn, row_factory=dict_row)
            own_conn = True
        except Exception as exc:
            # 2026-07-14 class-of-bug fix: was silent ``return counters``.
            # DB connect failure is a real operational issue — must
            # surface at WARNING minimum. Silent swallow here would
            # mask a token/network/DB outage from the operator.
            logger.warning(
                "late_reward.batch: DB connect failed — skipping batch: %s",
                exc,
            )
            return counters
    else:
        own_conn = False

    cutoff_min = datetime.now(UTC) - timedelta(days=days_ago_max)
    cutoff_max = datetime.now(UTC) - timedelta(days=days_ago_min)
    try:
        # 2026-07-23: query per-(blueprint × platform) pairs instead of
        # per-blueprint. Pre-fix path returned only blueprint_ids and
        # recompute_late_reward's LIMIT 1 picked one platform's row —
        # historically Facebook (first-inserted). Result: 14-day
        # late_reward_deltas table had ~50 FB rows + 1 non-FB row despite
        # blueprints publishing to 4-5 platforms each. IG/YT/Threads
        # long-tail growth patterns went completely un-measured.
        # New shape gets 4-5× the row coverage with same query cost.
        rows = conn.execute(
            """
            SELECT DISTINCT pa.blueprint_id::text AS blueprint_id, pa.platform
            FROM publishing_analytics pa
            WHERE pa.published_at BETWEEN %s AND %s
              -- 2026-08-11 Bug 1 fix: was `status = 'SUCCESS'` alone,
              -- but posts transition through INSIGHTS_6H/24H/48H/168H
              -- within 48h of publish. By the 6-8-days-ago window this
              -- batch scans, NO row has status='SUCCESS' anymore. The
              -- old filter matched 0 rows → late_reward silently dead
              -- since 2026-07-22 (20-day regression, invisible because
              -- systemd exit was 0 with no output). Excluding
              -- REMOVED_BY_META / FAILED / PARTIAL keeps the "was
              -- successfully published" invariant.
              AND pa.status IN (
                  'SUCCESS',
                  'INSIGHTS_6H',
                  'INSIGHTS_24H',
                  'INSIGHTS_48H',
                  'INSIGHTS_168H'
              )
            """,
            (cutoff_min, cutoff_max),
        ).fetchall()
    except Exception as exc:
        logger.warning("late_reward.batch_query_failed err=%s", exc)
        if own_conn:
            conn.close()
        return counters

    for row in rows or []:
        counters["scanned"] += 1
        if hasattr(row, "get"):
            bp_id = row.get("blueprint_id")
            row_platform = row.get("platform") or ""
        else:
            bp_id = row[0]
            row_platform = row[1] or ""
        try:
            delta = recompute_late_reward(bp_id, conn=conn, platform=row_platform)
        except Exception as exc:
            logger.warning(
                "late_reward.blueprint_error bp=%s platform=%s err=%s",
                bp_id, row_platform, exc,
            )
            counters["errors"] += 1
            continue
        if delta is None:
            continue
        counters["measured"] += 1
        is_significant = _is_significant_lift(delta)
        if is_significant:
            counters["significant_lift"] += 1
        # Persist audit row unconditionally so the operator can measure.
        _persist_delta_row(conn, delta)
        # Only push to bandit when flag is on AND lift is material.
        if push_to_bandit and is_significant:
            _push_delta_to_bandit(conn, delta)

    if own_conn:
        conn.close()
    logger.info("late_reward.batch_complete counters=%s", counters)
    return counters


def _persist_delta_row(conn: Any, delta: LateRewardDelta) -> None:
    """Best-effort insert into a lightweight audit table.

    Uses INSERT ... ON CONFLICT DO NOTHING so re-running the batch is safe.

    2026-08-11 (Bug 1b): removed the eager CREATE TABLE IF NOT EXISTS.
    Prod runs as ``genlab_app`` (BYPASSRLS=false per Audit A credential-
    rotation), which lacks CREATE privilege on schema public. Every
    late_reward fire since the role split has thrown `permission denied
    for schema public` on this DDL, which then poisoned the transaction
    so the subsequent INSERT also failed — the WHOLE persist path was
    silent-dead. Table already exists in prod (72 all-time rows); the
    IF NOT EXISTS was defensive against fresh installs, not needed at
    runtime. Fresh installs should create the table via a migration.
    """
    try:
        conn.execute(
            """
            INSERT INTO late_reward_deltas (
              blueprint_id, niche_id, arm_id, platform,
              reward_48h, reward_late, window_days, delta, delta_pct, measured_at
            ) VALUES (
              %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) ON CONFLICT (blueprint_id, platform, window_days) DO NOTHING
            """,
            (
                delta.blueprint_id,
                delta.niche_id,
                delta.arm_id or None,
                delta.platform,
                delta.reward_48h,
                delta.reward_late,
                delta.window_days,
                delta.delta,
                delta.delta_pct,
                delta.measured_at,
            ),
        )
        conn.commit()
    except Exception as exc:
        # 2026-07-02: bumped from ``logger.debug`` to WARNING because
        # the DEBUG-level suppression is what let a 7-week-old SQL bug
        # (blueprint_id / platform_post_id column references) stay
        # silent in prod. If persist ever fails we want to know.
        logger.warning("late_reward.persist_delta_failed err=%s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _push_delta_to_bandit(conn: Any, delta: LateRewardDelta) -> None:
    """Push a delta-only Beta update into the bandit posterior for the arm.

    Only fires when GENLAB_MULTI_WINDOW_REWARD_ENABLED=true AND delta_pct
    exceeds the 20% material-lift threshold. Uses the same
    ``_update_source_arm_reward`` code path as the live 48h wire so the
    math stays consistent.

    Delta-only means: alpha += (delta if positive) or 0; beta += (|delta|
    if negative) or 0. This never re-counts the 48h contribution — it
    ONLY adds the late-tail delta.
    """
    if not delta.arm_id:
        return
    if delta.delta > 0:
        alpha_add = delta.delta
        beta_add = 0.0
    else:
        alpha_add = 0.0
        beta_add = abs(delta.delta)
    try:
        conn.execute(
            """
            UPDATE bandit_arms
            SET alpha = alpha + %s,
                beta = beta + %s
            WHERE niche_id = %s AND arm_id = %s
            """,
            (alpha_add, beta_add, delta.niche_id, delta.arm_id),
        )
        conn.commit()
        logger.info(
            "late_reward.pushed_to_bandit niche=%s arm=%s dAlpha=%.3f dBeta=%.3f",
            delta.niche_id,
            delta.arm_id,
            alpha_add,
            beta_add,
        )
    except Exception as exc:
        logger.warning(
            "late_reward.bandit_update_failed niche=%s arm=%s err=%s",
            delta.niche_id,
            delta.arm_id,
            exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
