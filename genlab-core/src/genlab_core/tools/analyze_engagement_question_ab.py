"""Analyze engagement-question A/B lift.

Compares comment-rate between the two A/B buckets:

  * `with_q` — LLM-generated engagement question in pinned first comment
  * `without_q` — empty first comment (control)

Bucket assignment is deterministic per blueprint via the ROLLOUT_PCT
env var (see `monetization/cta_engine._engagement_question_ab_bucket`).
Persisted as `{platform}_first_comment__ab_bucket` in blueprints.extra
JSONB.

Reads bucket assignments from Postgres (blueprints.extra) and joins
with publishing_analytics for actual comment counts. Computes:

  * n per bucket
  * mean comments per bucket
  * comment rate lift (with_q / without_q - 1)
  * Welch's t-test for two-sample-unequal-variance statistical
    significance

Usage
-----

    # Full report (all niches, all 3 platforms)
    python -m genlab_core.tools.analyze_engagement_question_ab

    # Single niche
    python -m genlab_core.tools.analyze_engagement_question_ab \\
        --niche gaming

    # Single platform + shorter window
    python -m genlab_core.tools.analyze_engagement_question_ab \\
        --platform youtube --window-days 7

    # Machine-readable output
    python -m genlab_core.tools.analyze_engagement_question_ab \\
        --format json > report.json

## When to run

* After operator sets `GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT=50` on
  prod and waits ≥1 week for buckets to fill.
* Weekly rollup thereafter — comment-rate drifts as niche audience
  matures.

## Interpreting output

* `n < 30` per bucket -> insufficient data, run again in 1 week
* `p_value > 0.05` -> lift is not statistically significant at 95% CI
* `p_value < 0.05` AND lift > 0.2 -> flip ROLLOUT_PCT to 100 (
  question is winning)
* `p_value < 0.05` AND lift < -0.2 -> flip ROLLOUT_PCT to 0 (
  question is actively hurting)
* Anywhere in between: keep A/B running

## Fail-open

Any DB error prints "no data" for that (niche, platform) and moves on.
Never raises.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

_PLATFORMS: tuple[str, ...] = ("youtube", "instagram", "threads")
_NICHES: tuple[str, ...] = ("ai_creators", "gaming", "sports", "movies", "anime")


@dataclass
class BucketStats:
    n: int
    mean: float
    variance: float


@dataclass
class ABResult:
    niche: str
    platform: str
    window_days: int
    with_q: BucketStats
    without_q: BucketStats
    lift: float  # (with_q.mean - without_q.mean) / without_q.mean
    t_stat: float
    p_value: float
    sufficient_data: bool
    verdict: str


def welch_t_test(a: BucketStats, b: BucketStats) -> tuple[float, float]:
    """Welch's t-test (two-sample, unequal variance).

    Returns (t_stat, p_value). p_value is a rough two-tailed
    approximation via `erfc(|t|/sqrt(2))` — good enough for
    operator-level decisions (accept/reject flip). For rigorous
    p-values, use scipy.stats.ttest_ind.
    """
    if a.n < 2 or b.n < 2:
        return 0.0, 1.0
    if a.variance <= 0 and b.variance <= 0:
        return 0.0, 1.0
    se = math.sqrt((a.variance / a.n) + (b.variance / b.n))
    if se == 0:
        return 0.0, 1.0
    t = (a.mean - b.mean) / se
    # Two-tailed normal approximation (sufficient for large n)
    p = math.erfc(abs(t) / math.sqrt(2))
    return t, p


def compute_bucket_stats(rows: list[dict[str, Any]]) -> BucketStats:
    """Compute n, mean, sample-variance for a list of {comments: N} rows."""
    n = len(rows)
    if n == 0:
        return BucketStats(n=0, mean=0.0, variance=0.0)
    vals = [float(r.get("comments") or 0) for r in rows]
    mean = sum(vals) / n
    if n < 2:
        return BucketStats(n=n, mean=mean, variance=0.0)
    variance = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return BucketStats(n=n, mean=mean, variance=variance)


def _verdict(result_partial: dict) -> str:
    """Verdict string based on n, p_value, and lift magnitude."""
    n_min = min(result_partial["with_q"].n, result_partial["without_q"].n)
    if n_min < 30:
        return f"insufficient_data (min_n={n_min})"
    p = result_partial["p_value"]
    lift = result_partial["lift"]
    if p > 0.05:
        return f"not_significant (p={p:.3f})"
    if lift > 0.20:
        return f"question_wins (lift={lift:+.1%}, p={p:.3f}) -> ROLLOUT_PCT=100"
    if lift < -0.20:
        return f"question_hurts (lift={lift:+.1%}, p={p:.3f}) -> ROLLOUT_PCT=0"
    return f"marginal (lift={lift:+.1%}, p={p:.3f}) -> keep_running"


def query_ab_data(
    conn,
    *,
    niche: str,
    platform: str,
    window_days: int,
) -> tuple[list[dict], list[dict]]:
    """Query with_q + without_q comment counts for one (niche, platform).

    Returns (with_q_rows, without_q_rows). Each row: {"comments": int}.
    Fail-open: any error returns ([], []).
    """
    bucket_key = f"{platform}_first_comment__ab_bucket"
    sql = """
        SELECT
            pa.comments AS comments,
            b.extra->>%s AS bucket
        FROM blueprints b
        INNER JOIN publishing_analytics pa
            ON pa.blueprint_id = b.id
           AND pa.niche_id = b.niche_id
           AND pa.platform = %s
        WHERE b.niche_id = %s
          AND pa.published_at > NOW() - make_interval(days => %s)
          AND b.extra->>%s IS NOT NULL
          AND pa.comments IS NOT NULL
    """
    try:
        rows = conn.execute(
            sql,
            (bucket_key, platform, niche, window_days, bucket_key),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[analyze_ab] query failed niche=%s platform=%s: %s",
            niche, platform, exc,
        )
        return [], []

    with_q = [dict(r) for r in rows if (r.get("bucket") if hasattr(r, "get") else r[1]) == "with_q"]
    without_q = [dict(r) for r in rows if (r.get("bucket") if hasattr(r, "get") else r[1]) == "without_q"]
    return with_q, without_q


def analyze_one(conn, *, niche: str, platform: str, window_days: int) -> ABResult:
    with_q_rows, without_q_rows = query_ab_data(
        conn, niche=niche, platform=platform, window_days=window_days,
    )
    with_q_stats = compute_bucket_stats(with_q_rows)
    without_q_stats = compute_bucket_stats(without_q_rows)

    lift = 0.0
    if without_q_stats.mean > 0:
        lift = (with_q_stats.mean - without_q_stats.mean) / without_q_stats.mean

    t_stat, p_value = welch_t_test(with_q_stats, without_q_stats)

    verdict = _verdict({
        "with_q": with_q_stats,
        "without_q": without_q_stats,
        "lift": lift,
        "p_value": p_value,
    })

    return ABResult(
        niche=niche,
        platform=platform,
        window_days=window_days,
        with_q=with_q_stats,
        without_q=without_q_stats,
        lift=lift,
        t_stat=t_stat,
        p_value=p_value,
        sufficient_data=min(with_q_stats.n, without_q_stats.n) >= 30,
        verdict=verdict,
    )


def _format_table(results: list[ABResult]) -> str:
    lines: list[str] = []
    lines.append(
        f"{'niche':<15} {'platform':<10} {'w_n':>5} {'wo_n':>5} "
        f"{'w_mean':>8} {'wo_mean':>8} {'lift':>8} {'p':>8} verdict"
    )
    lines.append("-" * 100)
    for r in results:
        lines.append(
            f"{r.niche:<15} {r.platform:<10} "
            f"{r.with_q.n:>5} {r.without_q.n:>5} "
            f"{r.with_q.mean:>8.2f} {r.without_q.mean:>8.2f} "
            f"{r.lift:>+7.1%} {r.p_value:>8.3f} {r.verdict}"
        )
    return "\n".join(lines)


def _to_json(results: list[ABResult]) -> str:
    return json.dumps([asdict(r) for r in results], indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze engagement-question A/B lift from prod data",
    )
    parser.add_argument(
        "--niche",
        choices=[*_NICHES, "all"],
        default="all",
        help="Restrict to one niche (default: all 5)",
    )
    parser.add_argument(
        "--platform",
        choices=[*_PLATFORMS, "all"],
        default="all",
        help="Restrict to one platform (default: all 3)",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=14,
        help="Rolling window in days (default 14)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("ERROR: psycopg not installed", file=sys.stderr)
        return 2

    niches = _NICHES if args.niche == "all" else (args.niche,)
    platforms = _PLATFORMS if args.platform == "all" else (args.platform,)

    results: list[ABResult] = []
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            for niche in niches:
                for platform in platforms:
                    result = analyze_one(
                        conn, niche=niche, platform=platform,
                        window_days=args.window_days,
                    )
                    results.append(result)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: DB error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(_to_json(results))
    else:
        print(_format_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
