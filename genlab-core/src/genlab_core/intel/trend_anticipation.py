"""Trend Anticipation Module (Intervention 5, 2026-07-01).

The existing :class:`~genlab_core.intel.google_trends.GoogleTrendsIntel`
answers "what's trending RIGHT NOW?" That's reactive by construction —
we see a topic at peak trending, publish content, and launch into a
saturated market by the time viewers see the reel.

This module answers a different question: "which topics look like
they're accelerating toward a peak in the next 6-24 hours?" Answering
that lets the pipeline pick topics that will PEAK when our content
lands, not after.

The mathematical core is the **second derivative of search velocity**:

* First derivative (velocity) ``d/dt`` is already what "trending"
  means — searches this week vs last week.
* Second derivative (acceleration) ``d²/dt²`` tells us whether the
  trend is speeding up (peak ahead) or slowing down (peak already
  passed). Positive acceleration is the anticipation signal.

Signal Aggregation
==================

The full research-doc design combines four signals:

    signals = {
        'search_velocity': google_trends_2nd_derivative(topic),
        'creator_pickup': count_creator_mentions(topic, days=7),
        'social_velocity': reddit_karma_rate(topic),
        'news_lead':      recent_articles_count(topic, days=3),
    }

Session 1 (this file, 2026-07-01) ships:
  * ``search_velocity`` — first-order signal via pytrends
    ``interest_over_time`` + centred finite difference for the 2nd
    derivative.
  * ``creator_pickup``, ``social_velocity``, ``news_lead`` — stubs
    that always return None. Composite scoring RE-normalises weights
    over available signals so a missing signal doesn't shrink the
    composite toward 0 (same pattern the reward-shaper's
    `_weight_redistribution` uses).

Follow-up sessions (2, 3) wire the remaining three signals; the
weights are per-signal + independent, so each can land without
disturbing what shipped in Session 1.

Composite → Rank
================

Once every candidate topic has an :class:`AnticipationScore`, the
runner ranks by ``composite_score``, persists the top-N to
``.tmp/trend-anticipation/YYYYMMDD-<niche>.json``, and the pipeline
runs the next day consume the artifact (a future PR wires that read
side).

Flag
====

``GENLAB_TREND_ANTICIPATION_ENABLED`` (exact-true) gates DOWNSTREAM
consumption of the artifact. The runner itself always writes the
artifact — the flag guards whether the pipeline reads and acts on
the ranked list. This lets Session 1 ship in observation-only mode:
we accumulate anticipation-score history + validate it against
actual peak times before the pipeline steers on it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Feature flag ────────────────────────────────────────────────────

_ENABLE_ENV_VAR: Final[str] = "GENLAB_TREND_ANTICIPATION_ENABLED"


def _integration_enabled() -> bool:
    """Exact-match feature flag. Same fail-closed pattern as the
    other intelligence-package flags (ensemble, DR, late_reward,
    temporal context)."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


# ── Signal weights ──────────────────────────────────────────────────

# Per-signal weights on the composite score. Chosen by intuition +
# signal maturity for Session 1:
#
#   * search_velocity — 0.60 (only shipped signal today; weight
#     re-normalises against the missing signals so it effectively
#     runs at ~1.0 until the others land)
#   * creator_pickup — 0.20 (highest post-search: creators mentioning
#     a topic on YouTube is a direct upstream leading indicator of
#     what OUR content will compete with)
#   * social_velocity — 0.15 (Reddit karma rate is a proxy for
#     mainstream awareness — trails creator mentions but leads
#     news article count)
#   * news_lead — 0.05 (news articles lag creator mentions +
#     social; only weakly predictive of 6-24h ahead but non-zero)
#
# Sum: 1.00. Adjust in a future session once we have Spearman
# correlations between each signal and observed peak-lag.
_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    "search_velocity": 0.60,
    "creator_pickup": 0.20,
    "social_velocity": 0.15,
    "news_lead": 0.05,
}


# ── Data classes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnticipationScore:
    """One topic's trend-anticipation verdict.

    * ``composite_score`` — [0, 1] weighted aggregate of the available
      signals. Re-normalises over the actually-populated signal subset
      so a missing signal abstains rather than dragging the score
      toward 0.
    * ``signals`` — per-signal float in [0, 1] or None. None means
      the signal source was unreachable / no data / not implemented
      yet (creator_pickup, social_velocity, news_lead in Session 1).
    * ``anticipated_peak_hours_ahead`` — coarse estimate of when the
      topic will peak. Positive int = future peak; 0 = already at
      peak; -N = past peak. Session 1 only sets this for
      search-velocity-driven topics via the sign of the 2nd
      derivative — a real ETA needs the other signals.
    * ``confidence`` — [0, 1] scalar. Session 1 = ``n_signals / 4``
      (naive coverage-fraction). Follow-ups can replace with a real
      calibrated confidence (e.g. variance across signals).
    * ``reasons`` — human-readable audit trail. Surfaced by the CLI's
      table output and the future dashboard reader.
    """

    topic: str
    niche_id: str
    composite_score: float
    signals: dict[str, float | None]
    anticipated_peak_hours_ahead: int | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ── Signal implementations ─────────────────────────────────────────


def _search_velocity_from_series(values: list[float]) -> tuple[float, int] | None:
    """Compute the search-velocity signal from a raw interest-over-time
    series. Returns ``(score, hours_ahead)`` or ``None`` for
    insufficient data.

    Math:

    * Take the last 7 datapoints (pytrends default weekly resolution
      returns daily values; taking 7 catches the last week).
    * First derivative ≈ ``v[i+1] - v[i]`` (per-step change).
    * Second derivative ≈ ``v[i+1] - 2*v[i] + v[i-1]`` (centred
      finite difference — the standard discrete 2nd derivative).
    * Score = ``sigmoid(2 * mean_2nd_derivative)`` — clamped to
      [0, 1]. The factor 2 makes typical 2nd-derivative magnitudes
      (a few points per day per day at the 0-100 pytrends scale)
      produce meaningful score separation instead of clustering
      near 0.5.
    * Peak-ETA sign flip: positive mean 2nd derivative → peak
      ahead (return positive hours). Negative → peak past (return
      negative). Very small magnitude → 0 (at/near peak).

    Kept pure so it can be unit-tested without a live pytrends
    connection.
    """
    if len(values) < 4:
        # Need at least 4 points to compute a centred 2nd derivative
        # from the last 3 windows.
        return None

    tail = values[-7:] if len(values) >= 7 else values
    if len(tail) < 3:
        return None

    second_deriv = []
    for i in range(1, len(tail) - 1):
        second_deriv.append(tail[i + 1] - 2 * tail[i] + tail[i - 1])
    if not second_deriv:
        return None

    mean_2d = sum(second_deriv) / len(second_deriv)

    # Sigmoid squashes to [0, 1]. The 2× multiplier maps typical
    # pytrends magnitudes (2nd derivative in the ~±3 range on a
    # 0-100 scale) to score separation roughly [0.2, 0.8].
    import math

    score = 1.0 / (1.0 + math.exp(-2.0 * mean_2d))

    # Peak-ETA heuristic: positive acceleration ≈ 6-24h ahead
    # depending on magnitude. This is coarse — a real ETA needs
    # the derivative time-scale from the data (weekly vs daily
    # pytrends resolution changes the answer). Session 1 uses a
    # sign-only proxy the operator can trust as direction, not
    # exact time.
    if abs(mean_2d) < 0.5:
        hours_ahead = 0
    elif mean_2d > 0:
        hours_ahead = 12 if mean_2d < 2 else 6
    else:
        hours_ahead = -12 if mean_2d > -2 else -24

    return score, hours_ahead


def _signal_search_velocity(topic: str, niche_id: str) -> tuple[float, int] | None:
    """Search-velocity signal — fetches pytrends interest_over_time
    for the topic + delegates to :func:`_search_velocity_from_series`.

    Fail-open: any failure (pytrends missing, rate-limited, network
    error, empty series) returns None. The composite reweighs.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.debug("[trend_anticipation] pytrends not installed — search velocity skipped")
        return None

    try:
        pt = TrendReq(hl="en-US", tz=330)
        # 7d window at daily resolution. `now 7-d` is pytrends' timeframe
        # syntax for the last 7 days of daily-resolution data.
        pt.build_payload([topic], timeframe="now 7-d", geo="US")
        df = pt.interest_over_time()
    except Exception as exc:
        logger.debug(
            "[trend_anticipation] pytrends query failed for %r (%s): %s",
            topic,
            niche_id,
            exc,
        )
        return None

    if df is None or df.empty or topic not in df.columns:
        return None
    values = [float(v) for v in df[topic].tolist() if v is not None]
    return _search_velocity_from_series(values)


def _signal_creator_pickup(topic: str, niche_id: str) -> float | None:
    """Creator-pickup signal — counts YouTube creator mentions of
    the topic in the last 7 days.

    Session 1: stub returning None. Session 2 will wire the YouTube
    Data API v3 search endpoint (already used by
    ``TrendingVideoFetcher``); scope is bounded by the 10K/day quota
    so the signal fires only for the top-N candidate topics per niche.
    """
    return None


def _signal_social_velocity(topic: str, niche_id: str) -> float | None:
    """Social-velocity signal — Reddit karma rate on posts mentioning
    the topic.

    Session 1: stub. Session 2 will use the existing PRAW integration
    in ``genlab_core.intel.reddit_fetcher`` to query the niche-relevant
    subreddits + compute karma velocity over a rolling window.
    """
    return None


def _signal_news_lead(topic: str, niche_id: str) -> float | None:
    """News-lead signal — count of news articles about the topic in
    the last 3 days.

    Session 1: stub. Session 3 will wire a news-search source (likely
    the existing RSS parser in ``genlab_core.intel.rss_parser`` +
    per-niche allowlist of news feeds from ``sources.yaml``).
    """
    return None


# ── Composite scoring ──────────────────────────────────────────────


def compute_anticipation_score(topic: str, niche_id: str) -> AnticipationScore:
    """Score one topic across all available signals.

    Weight re-normalisation semantics: if 2 of 4 signals return None,
    the composite is computed over the remaining 2 with weights
    scaled up so they sum to 1. This mirrors the reward-shaper's
    weight-redistribution logic (see
    :mod:`~genlab_core.learning.reward_shaper`) — a missing signal
    abstains rather than shrinking the composite toward 0.
    """
    sv = _signal_search_velocity(topic, niche_id)
    if sv is not None:
        search_velocity, hours_ahead = sv
    else:
        search_velocity, hours_ahead = None, None

    signals: dict[str, float | None] = {
        "search_velocity": search_velocity,
        "creator_pickup": _signal_creator_pickup(topic, niche_id),
        "social_velocity": _signal_social_velocity(topic, niche_id),
        "news_lead": _signal_news_lead(topic, niche_id),
    }

    # Weight re-normalise over populated signals.
    populated = {k: v for k, v in signals.items() if v is not None}
    if not populated:
        return AnticipationScore(
            topic=topic,
            niche_id=niche_id,
            composite_score=0.0,
            signals=signals,
            anticipated_peak_hours_ahead=None,
            confidence=0.0,
            reasons=["No signals available — all sources returned None"],
        )

    total_weight = sum(_SIGNAL_WEIGHTS[k] for k in populated)
    weighted = sum((_SIGNAL_WEIGHTS[k] / total_weight) * v for k, v in populated.items())

    # Naive confidence = fraction of signals we have. When all 4
    # signals land in future sessions this becomes 1.0; Session 1
    # with only search_velocity active gives 0.25.
    confidence = len(populated) / len(signals)

    reasons = [f"{k}={v:.3f} (weight {_SIGNAL_WEIGHTS[k]:.2f})" for k, v in populated.items()]
    reasons.append(f"composite={weighted:.3f} confidence={confidence:.2f}")

    return AnticipationScore(
        topic=topic,
        niche_id=niche_id,
        composite_score=weighted,
        signals=signals,
        anticipated_peak_hours_ahead=hours_ahead,
        confidence=confidence,
        reasons=reasons,
    )


def rank_topics(
    topics: list[str],
    niche_id: str,
    *,
    top_n: int = 5,
) -> list[AnticipationScore]:
    """Score every candidate topic + return the top-N by composite.

    The runner passes the ``GoogleTrendsIntel.get_trending_topics()``
    output here; those are the CURRENTLY trending topics. Ranking by
    anticipation score reorders them so the ones with positive
    acceleration surface first — even if their absolute trending
    volume today is lower than a topic already at peak.
    """
    scored = [compute_anticipation_score(t, niche_id) for t in topics]
    scored.sort(key=lambda s: s.composite_score, reverse=True)
    return scored[:top_n]


# ── Persistence ────────────────────────────────────────────────────


def _resolve_output_dir() -> Path:
    """``$GENLAB_TMP/trend-anticipation`` or
    ``./.tmp/trend-anticipation`` — same fallback pattern as
    :mod:`scripts.run_counterfactual_replay`."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "trend-anticipation"


def persist_ranking(
    niche_id: str,
    ranking: list[AnticipationScore],
    *,
    output_dir: Path | None = None,
) -> Path:
    """Write the ranked list to a JSON artifact.

    Layout: ``$GENLAB_TMP/trend-anticipation/YYYYMMDD-<niche>.json``.
    Overwrites any existing same-day file — the runner is idempotent
    per (day, niche).

    Downstream consumers (Session 2+ pipeline wire, dashboard reader)
    watch this directory. Absence of a file for today's date is
    the "runner hasn't fired yet" state; presence with an empty list
    is "runner fired but no candidate topics had signal."
    """
    out_dir = output_dir or _resolve_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = out_dir / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "flag_enabled": _integration_enabled(),
        "ranking": [s.to_json() for s in ranking],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


__all__ = [
    "AnticipationScore",
    "compute_anticipation_score",
    "rank_topics",
    "persist_ranking",
    "_signal_search_velocity",
    "_search_velocity_from_series",
    "_SIGNAL_WEIGHTS",
]
