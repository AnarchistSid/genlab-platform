"""Per-(niche, base_arm) per-platform bandit-divergence aggregator (PR AG, 2026-06-29).

Foundation for the Mission Control ``BanditPlatformDivergenceCard``. PR #641
(shipped + activated 2026-06-29) writes per-platform bandit arms
(``content_type__platform``) alongside the base ``content_type`` arm whenever
``GENLAB_PER_PLATFORM_BANDIT_ENABLED=1``. Starting today, every
metric_collector reward update writes BOTH the base arm AND the platform-
specific variant.

After ~2 weeks of accumulation, the per-platform variants should DIVERGE
from the base — that's the WHOLE point of the per-platform split:
the bandit learns that sports prefers different hook types on YT vs IG,
ai_creators prefers different hook types on YT vs IG, etc.

Without this surface, the operator has no way to see whether the per-
platform mechanism is producing meaningful signal vs collapsing back to
the base arm (which would mean the split is wasted complexity).

## Why an aggregator, not a flat per-arm endpoint

  * The dashboard's question is "for each (niche, base_arm), do the
    per-platform variants diverge from the base?" — that's a JOIN-shaped
    question, not a "give me all arms" question. Aggregating server-side
    keeps the wire payload + frontend logic small.
  * ``parse_platform_arm_id`` from PR #641's :mod:`bandit_platform_split`
    is the canonical grouping primitive — keep one parser, not two.

## "Mean" definition

  The Thompson-sampled posterior mean for a Beta(alpha, beta) arm is
  ``alpha / (alpha + beta)``. We use that for all three numbers per row
  (base_mean, instagram_mean, youtube_mean). It's the same number the
  bandit uses at decision time — so what the operator sees is what
  drives behaviour.

## Cold-start filter

  Rows where ALL three (base, IG variant, YT variant) have ``n_plays <
  min_n_plays`` are filtered out — they're too cold for any divergence
  to be interpretable. The default ``min_n_plays=5`` is intentionally
  loose: it admits arms with as few as ~5 publishes per platform variant.
  Operator-meaningful "is this real?" thresholds live in the FRONTEND
  card (5% delta star) and the API ``thresholds`` block.

  Rows where SOME but not all variants are cold are kept — the frontend
  marks the cold variants with "(cold)" so the operator can see partial
  divergence emerging.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# Surfaced in the API response so the frontend doesn't hardcode these.
# If we ever tune (say, raise the meaningful delta to 7% once arms have
# accumulated more observations), the card picks it up automatically.
THRESHOLDS = {
    # |IG mean - YT mean| ≥ this → star the row as "operator-meaningful"
    "meaningful_delta_pct": 5.0,
    # Per-variant n_plays below this → row variant marked "(cold)"
    "cold_start_below_n": 5,
}


def fetch_per_platform_divergence(
    *,
    niche_id: str | None = None,
    min_n_plays: int = 5,
) -> list[dict[str, Any]]:
    """Return per-(niche, base_arm) rows showing base vs per-platform posteriors.

    Reads the ``bandit_arms`` table, uses :func:`parse_platform_arm_id`
    from :mod:`genlab_core.learning.bandit_platform_split` to group
    platform-variants under their base arm, and computes posterior
    means + n_plays for the base + each per-platform variant.

    Args:
      niche_id: Optional filter — if set, only rows for that niche are
        returned. Set 'app.niche_id' to 'all' inside the connection so
        we can FETCH all niches even when caller passes one (RLS would
        otherwise scope us prematurely). Filter applied in Python so
        the SR-F endpoint layer post-filters on the same dict shape.
      min_n_plays: Cold-start floor. Rows where ALL three variants
        (base, IG, YT) have ``n_plays < min_n_plays`` are excluded.
        Default 5 — matches :data:`THRESHOLDS["cold_start_below_n"]`.

    Returns:
      List of dicts, one per (niche, base_arm) pair. Each dict::

        {
          "niche_id": "sports",
          "base_arm_id": "gameplay_clip",
          "base_mean": 0.42, "base_n_plays": 87,
          "instagram_mean": 0.51, "instagram_n_plays": 23,
          "youtube_mean": 0.34, "youtube_n_plays": 19,
          "ig_yt_delta": 0.17,           # |instagram_mean - youtube_mean|
          "ig_delta_from_base": 0.09,    # instagram_mean - base_mean
          "yt_delta_from_base": -0.08,   # youtube_mean - base_mean
        }

      Variants that have NO row in the DB (e.g. arm was never written
      because the niche never published to that platform) get
      ``<plat>_mean=None`` and ``<plat>_n_plays=0``. Delta fields
      involving a None variant are None too — frontend renders "—".

    Fail-OPEN: on DB error or no DSN, returns ``[]``. The card renders
    its zero-state ("No arms have ≥N obs per platform yet — come back
    after the 2-week accumulation window") rather than vanishing or
    erroring out — same convention as
    :func:`server.core.publishing_health_per_niche.fetch_per_niche_platform_health`.
    """
    # Defence: clamp min_n_plays. Negative or zero would make the
    # cold-start filter a no-op, which is fine but probably not what
    # the caller meant. Clamp upper bound at 1000 so a typo (50000)
    # doesn't accidentally hide the whole table.
    min_n_plays = max(1, min(1000, int(min_n_plays)))

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("DATABASE_URL not configured — returning empty divergence list")
        return []

    # Lazy import — keeps the module importable even when genlab_core
    # isn't on PYTHONPATH (e.g. unit tests that mock pg_connect but
    # don't need the parser).
    try:
        from genlab_core.learning.bandit_platform_split import (
            list_split_platforms,
            parse_platform_arm_id,
        )
    except ImportError as exc:  # pragma: no cover — defensive
        logger.error("bandit_platform_split unavailable: %s", exc)
        return []

    try:
        # Pull all arms regardless of niche_id filter — the endpoint
        # layer may apply SR-F over the full set. niche_id arg is a
        # display-shape filter, not a security boundary.
        sql = """
            SELECT niche_id, arm_id, alpha, beta, n_plays
            FROM bandit_arms
            ORDER BY niche_id, arm_id
        """
        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            # RLS bypass: we need the full table to do the JOIN-style
            # group-by-base-arm aggregation in Python. The SR-F filter
            # at the endpoint layer scopes results back to the
            # operator's allowlist.
            conn.execute("SELECT set_config('app.niche_id', 'all', true)")
            rows = conn.execute(sql).fetchall()
    except Exception as exc:  # pragma: no cover — defensive fail-OPEN
        logger.error("bandit_arms divergence query failed: %s", exc, exc_info=True)
        return []

    # Group rows by (niche_id, base_arm_id). Each group holds the
    # base arm + every per-platform variant that's been written.
    # Key: (niche_id, base_arm_id) → dict mapping platform-or-None to
    # (alpha, beta, n_plays).
    grouped: dict[tuple[str, str], dict[str | None, tuple[float, float, int]]] = {}
    for r in rows:
        arm_id = r["arm_id"]
        base, platform = parse_platform_arm_id(arm_id)
        key = (r["niche_id"], base)
        if key not in grouped:
            grouped[key] = {}
        # alpha/beta may be NULL on legacy rows — coalesce to the
        # Beta(1,1) prior. n_plays NULL → 0.
        alpha = float(r["alpha"] or 1.0)
        beta = float(r["beta"] or 1.0)
        n_plays = int(r["n_plays"] or 0)
        grouped[key][platform] = (alpha, beta, n_plays)

    split_platforms = list_split_platforms()  # ("instagram", "youtube")

    result: list[dict[str, Any]] = []
    for (niche, base_arm), variants in grouped.items():
        # niche_id display filter (applied post-fetch — see docstring).
        if niche_id is not None and niche != niche_id:
            continue

        # Skip groups that have NO per-platform variants at all. The
        # card is specifically a divergence view — base-only groups
        # have nothing to compare. (LearningLoopCard already surfaces
        # base-only arm posteriors elsewhere.)
        has_any_platform_variant = any(p in variants for p in split_platforms)
        if not has_any_platform_variant:
            continue

        # Compute means + n_plays per slot. Missing slots → None
        # (n_plays=0). The base slot may also be missing if the bandit
        # was reset to per-platform-only, but typically both coexist
        # during the PR #641 transition window.
        slots: dict[str | None, tuple[float | None, int]] = {}
        for slot in (None, *split_platforms):
            if slot in variants:
                alpha, beta, n_plays = variants[slot]
                total = alpha + beta
                mean = round(alpha / total, 4) if total > 0 else None
                slots[slot] = (mean, n_plays)
            else:
                slots[slot] = (None, 0)

        base_mean, base_n = slots[None]
        ig_mean, ig_n = slots.get("instagram", (None, 0))
        yt_mean, yt_n = slots.get("youtube", (None, 0))

        # Cold-start filter: drop the row only if ALL THREE slots are
        # below the floor. Partial-cold is OK — the frontend will mark
        # cold variants for the operator.
        max_n_seen = max(base_n, ig_n, yt_n)
        if max_n_seen < min_n_plays:
            continue

        # Deltas. None-propagating: if either side is missing, delta
        # is None and the frontend renders "—".
        if ig_mean is not None and yt_mean is not None:
            ig_yt_delta = round(abs(ig_mean - yt_mean), 4)
        else:
            ig_yt_delta = None

        if ig_mean is not None and base_mean is not None:
            ig_delta_from_base = round(ig_mean - base_mean, 4)
        else:
            ig_delta_from_base = None

        if yt_mean is not None and base_mean is not None:
            yt_delta_from_base = round(yt_mean - base_mean, 4)
        else:
            yt_delta_from_base = None

        result.append(
            {
                "niche_id": niche,
                "base_arm_id": base_arm,
                "base_mean": base_mean,
                "base_n_plays": base_n,
                "instagram_mean": ig_mean,
                "instagram_n_plays": ig_n,
                "youtube_mean": yt_mean,
                "youtube_n_plays": yt_n,
                "ig_yt_delta": ig_yt_delta,
                "ig_delta_from_base": ig_delta_from_base,
                "yt_delta_from_base": yt_delta_from_base,
            }
        )

    # Stable sort: by |IG↔YT delta| desc — operator sees the most-
    # divergent (most "interesting") arms first. None deltas sort
    # last (treated as -1 so they fall below all real deltas).
    result.sort(
        key=lambda r: -(r["ig_yt_delta"] if r["ig_yt_delta"] is not None else -1.0),
    )

    return result
