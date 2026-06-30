"""Bandit validation harness — daily rank-correlation vs engagement.

Why this exists
---------------
The bandit (per-source Beta posteriors via ``source_performance.py`` +
LinUCB contextual selection via ``linucb_picker.py``) ranks candidate
posts at fetch + classify time. But "does the rank correlate with
actual engagement after publish?" was never measured. Without that
loop, the operator has no signal for whether posteriors are
converging to a useful policy, sitting at random-walk noise, or
actively diverging.

This module implements the single test:

    For each niche, over the last N days, take all published posts.
    For each post, compute:
      - bandit_predicted_score: the per-source arm's Beta posterior
        mean at publish time (proxy for "what the bandit thought of
        this candidate")
      - actual_engagement: the 48h-window viral_score from analytics
    Compute Spearman + Pearson correlation. The Spearman number is
    the headline metric — it answers "if the bandit said one source
    is better, did the actual engagement bear that out?"

Outputs the 5 metrics in ``BanditValidationResult``:

    n_posts                     — count in window (low signal < 30)
    spearman_rank_correlation   — rank-based (robust to outliers)
    pearson_correlation         — linear
    top_quartile_lift           — avg engagement of top 25%
                                  ÷ avg engagement of bottom 25%
                                  (1.0 = bandit is useless, >2.0 is
                                  genuinely steering content selection)
    interpretation              — human-readable verdict

Interpretation thresholds (chosen for the prod data scale, NOT
academic standards — at 5-50 posts/niche/2wk, statistical sig is
unreliable; we use coarse buckets):

    n_posts < 30                       → "low signal"
    spearman >= 0.3                    → "bandit useful"
    spearman <= 0.1 AND lift <= 1.2    → "bandit ineffective"
    otherwise                          → "developing"

The thresholds match the
auto_approval_calibration_logger.stats() pattern (30-sample floor
for "ready" verdicts) — operators already have that mental model.

Persistence
-----------
Results are written to a daily ``bandit_validation`` table (one row
per niche per day, indexed on (niche_id, computed_at DESC)). The
``bandit-validation`` systemd timer runs this at 09:00 IST (just
before the operator's morning Mission Control review).

Health-check integration
------------------------
``check_three_day_ineffective`` reads the last 3 days of rows for
a niche; returns True when ALL 3 are 'bandit ineffective'. The
caller (a future alert source in alerts.py) can wire this to a
WARN that surfaces in the dashboard's alert feed.

Failure mode
------------
Every function fail-OPEN: on DB error / missing DSN / empty data,
returns a default that does NOT pollute alerts. The validation
harness MUST NOT block any other pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds tuned for prod data shape: at 5-50 posts/niche/2wk,
# pure statistical significance is unreliable. We use bucket-style
# thresholds that match the operator's mental model.
LOW_SIGNAL_FLOOR = 30
USEFUL_SPEARMAN_THRESHOLD = 0.3
INEFFECTIVE_SPEARMAN_CEILING = 0.1
INEFFECTIVE_LIFT_CEILING = 1.2


@dataclass(frozen=True)
class BanditValidationResult:
    """Headline result of one validation pass for one niche.

    Frozen so consumers can't mutate state mid-evaluation. All numeric
    fields default to 0.0 / 0 — interpretation 'low signal' is the
    canonical cold-start verdict.
    """

    niche_id: str
    n_posts: int = 0
    spearman_rank_correlation: float = 0.0
    pearson_correlation: float = 0.0
    top_quartile_lift: float = 1.0
    interpretation: str = "low signal"


def _connect():
    """Lazy psycopg connect. None on missing DSN / connect error.

    Pattern matches source_performance._connect — fail-OPEN by
    returning None so callers degrade gracefully (return defaults).
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[bandit_validation] DATABASE_URL not set; harness disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[bandit_validation] connection failed: %s", exc)
        return None


def _interpret(n_posts: int, spearman: float, lift: float) -> str:
    """Translate (n, spearman, lift) into a human-readable verdict.

    Pure function — no side effects, no I/O. Tested independently from
    the SQL-driven correlation math.
    """
    if n_posts < LOW_SIGNAL_FLOOR:
        return "low signal"
    if spearman >= USEFUL_SPEARMAN_THRESHOLD:
        return "bandit useful"
    if spearman <= INEFFECTIVE_SPEARMAN_CEILING and lift <= INEFFECTIVE_LIFT_CEILING:
        return "bandit ineffective"
    return "developing"


def _compute_correlations(
    predicted: list[float], actual: list[float]
) -> tuple[float, float, float]:
    """Compute (spearman, pearson, top_quartile_lift) for two paired vectors.

    Returns zeros when len < 4 (too few for quartiles) or when scipy is
    unavailable. The caller's interpretation function uses n_posts to
    decide whether to trust the numbers.

    Pure function — testable with synthetic vectors of known correlation.
    """
    n = len(predicted)
    if n != len(actual) or n < 4:
        return 0.0, 0.0, 1.0
    try:
        from scipy import stats
    except ImportError:
        logger.warning("[bandit_validation] scipy unavailable — returning zeros")
        return 0.0, 0.0, 1.0

    # Spearman + Pearson — robust to NaN by filtering.
    spearman_r, _ = stats.spearmanr(predicted, actual)
    pearson_r, _ = stats.pearsonr(predicted, actual)
    # NaN guard: if both vectors are constant, the correlation is
    # undefined. Report 0.0 so the interpretation correctly says
    # "ineffective" (no info content).
    spearman_r = 0.0 if spearman_r != spearman_r else float(spearman_r)
    pearson_r = 0.0 if pearson_r != pearson_r else float(pearson_r)

    # Top-quartile lift: rank by predicted, then compare avg actual
    # of top 25% to bottom 25%. 1.0 = bandit useless, >1 = lifting.
    paired = sorted(zip(predicted, actual, strict=False), key=lambda p: p[0])
    quartile_size = max(1, n // 4)
    bottom_actual = [a for _, a in paired[:quartile_size]]
    top_actual = [a for _, a in paired[-quartile_size:]]
    bottom_avg = sum(bottom_actual) / len(bottom_actual)
    top_avg = sum(top_actual) / len(top_actual)
    # Division-by-zero guard: bottom-25% zero engagement → cap lift
    # at the magnitude of top-avg (avoids inf, preserves direction).
    if bottom_avg <= 1e-9:
        lift = top_avg if top_avg > 0 else 1.0
    else:
        lift = top_avg / bottom_avg
    return spearman_r, pearson_r, lift


def _fetch_paired_data(conn, niche_id: str, days: int) -> tuple[list[float], list[float]]:
    """SELECT (bandit_predicted_score, actual_engagement) pairs for
    posts published in the last ``days`` for ``niche_id``.

    bandit_predicted_score is the per-source Beta posterior mean for
    the post's source AT QUERY TIME — a strict simplification of "at
    publish time" (would require historical posterior snapshots, which
    we don't have). The simplification understates the bandit's
    historical predictive power but doesn't bias it in either
    direction, so correlation trends still mean what they say.

    actual_engagement is the 48h analytics window's viral_score; we
    use viral_score (not engagement_rate alone) because it's the
    composite the bandit reward shaper is ultimately optimising for.

    Returns (predicted_scores, actual_engagements) — empty pair when
    no eligible rows exist.
    """
    sql = """
        SELECT
            ba.alpha / NULLIF(ba.alpha + ba.beta, 0) AS predicted_score,
            COALESCE((a.extra->>'viral_score')::float, 0.0) AS actual_engagement
        FROM publishing_analytics pa
        INNER JOIN blueprints b
            ON pa.candidate_id = b.candidate_id
           AND pa.niche_id = b.niche_id
        INNER JOIN bandit_arms ba
            ON ba.niche_id = pa.niche_id
           AND ba.arm_id = 'source:' || b.source
        LEFT JOIN analytics a
            ON a.post_id = (pa.platform || ':' || pa.post_id)
           AND a.niche_id = pa.niche_id
           AND a."window" = '48h'
        WHERE pa.status = 'SUCCESS'
          AND pa.niche_id = %s
          AND pa.published_at >= NOW() - (%s::int * INTERVAL '1 day')
          AND b.source IS NOT NULL
          AND b.source <> ''
    """
    with conn.cursor() as cur:
        cur.execute(sql, (niche_id, days))
        rows = cur.fetchall()
    predicted = [float(r["predicted_score"]) for r in rows if r["predicted_score"] is not None]
    actual = [float(r["actual_engagement"]) for r in rows if r["predicted_score"] is not None]
    return predicted, actual


def compute_bandit_engagement_correlation(niche_id: str, days: int = 14) -> BanditValidationResult:
    """Run one validation pass for one niche.

    Returns a fully-populated BanditValidationResult. ALL failure
    modes (DSN missing, DB error, scipy missing, zero rows) return a
    valid result with sensible defaults — never raises. Caller can
    rely on .interpretation alone for downstream alerting logic.
    """
    if not niche_id:
        return BanditValidationResult(niche_id="")

    conn_cm = _connect()
    if conn_cm is None:
        return BanditValidationResult(niche_id=niche_id)

    try:
        with conn_cm as conn:
            predicted, actual = _fetch_paired_data(conn, niche_id, days)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[bandit_validation] fetch failed for %s: %s", niche_id, exc)
        return BanditValidationResult(niche_id=niche_id)

    n = len(predicted)
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    interpretation = _interpret(n, spearman, lift)

    return BanditValidationResult(
        niche_id=niche_id,
        n_posts=n,
        spearman_rank_correlation=spearman,
        pearson_correlation=pearson,
        top_quartile_lift=lift,
        interpretation=interpretation,
    )


def persist_result(result: BanditValidationResult) -> bool:
    """Write a validation result to the ``bandit_validation`` table.

    Returns True on success, False fail-OPEN on DSN missing / DB error.
    The cron caller logs the False case but does NOT fail the systemd
    unit — surfacing the failure via systemctl status is sufficient.
    """
    if not result.niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            conn.execute(
                """
                INSERT INTO bandit_validation (
                    niche_id, computed_at, n_posts,
                    spearman, pearson, top_quartile_lift, interpretation
                ) VALUES (%s, NOW(), %s, %s, %s, %s, %s)
                """,
                (
                    result.niche_id,
                    result.n_posts,
                    result.spearman_rank_correlation,
                    result.pearson_correlation,
                    result.top_quartile_lift,
                    result.interpretation,
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[bandit_validation] persist failed for %s: %s", result.niche_id, exc)
        return False


def check_three_day_ineffective(niche_id: str) -> bool:
    """Read last 3 days' rows for niche; True iff ALL are 'bandit ineffective'.

    Three days chosen for sensitivity-vs-noise: 1 day of bad
    correlation is normal noise; 3 consecutive is a signal worth
    investigating. Matches the alerts.py pattern (consecutive-window
    detection) for compliance + token-health.

    Fail-OPEN: returns False on any error so a broken validation
    table doesn't spam the alert feed.
    """
    if not niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT interpretation
                FROM bandit_validation
                WHERE niche_id = %s
                  AND computed_at >= NOW() - INTERVAL '3 days'
                ORDER BY computed_at DESC
                LIMIT 3
                """,
                (niche_id,),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[bandit_validation] 3-day check failed for %s: %s", niche_id, exc)
        return False

    if len(rows) < 3:
        return False  # not enough history
    return all(r["interpretation"] == "bandit ineffective" for r in rows)


# Default niche list for the cron CLI. Matches the 5 niches in
# CLAUDE.md. Kept here (not pulled from niches_registry.yaml) so the
# validation module doesn't add a config-loader dependency.
DEFAULT_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")


def run_for_all_niches(niches: tuple[str, ...] = DEFAULT_NICHES) -> int:
    """CLI entry point — compute + persist for each niche.

    Returns process exit code: 0 on full success, 1 if any niche
    failed to persist (the systemd unit treats non-zero as a failure
    signal in journalctl). Each niche's compute is independent, so
    one bad niche does NOT skip the others.
    """
    failures = 0
    for niche_id in niches:
        try:
            result = compute_bandit_engagement_correlation(niche_id)
            ok = persist_result(result)
            logger.info(
                "[bandit_validation] %s: n=%d spearman=%.3f lift=%.2f → %s%s",
                niche_id,
                result.n_posts,
                result.spearman_rank_correlation,
                result.top_quartile_lift,
                result.interpretation,
                "" if ok else " [PERSIST FAILED]",
            )
            if not ok:
                failures += 1
        except Exception as exc:  # noqa: BLE001 — best-effort per niche
            logger.error("[bandit_validation] %s: exception: %s", niche_id, exc)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sys.exit(run_for_all_niches())
