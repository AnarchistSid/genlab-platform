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

Session 1 (2026-07-01) shipped ``search_velocity`` — first-order
signal via pytrends ``interest_over_time`` + centred finite
difference for the 2nd derivative.

Session 2 (this file, 2026-07-01) wires the two paid signals:

  * ``creator_pickup`` — YouTube Data API v3 ``search.list`` for
    videos mentioning the topic in the last 7 days. Score maps
    ``totalResults`` on a log10 scale so a "10 mentions" vs
    "10,000 mentions" delta stays visible in the [0, 1] range
    (linear normalisation saturates instantly on hot topics).
    Gated behind ``GENLAB_ANTICIPATION_YT_ENABLED`` because
    search.list costs 100 quota units per call — the flag lets
    the operator activate the paid signal without commiting the
    daily 10K quota budget.

  * ``social_velocity`` — Reddit OAuth ``/search`` with karma-per-
    hour aggregate. Gated behind
    ``GENLAB_ANTICIPATION_REDDIT_ENABLED`` for the same rate-limit
    respect + operator-consent reason.

``news_lead`` stays stubbed; Session 3 wires it via RSS.

Composite scoring RE-normalises weights over available signals so
a missing signal doesn't shrink the composite toward 0 (same
pattern the reward-shaper's ``_weight_redistribution`` uses). All
four signal slots remain per-signal + independent so each can
land without disturbing what shipped previously.

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
#
# A.3 (2026-07-08): rebalance to fit ``creator_upload_lead`` as
# a 5th signal. Top-tier hand-curated creators publishing about a
# topic is the strongest LEADING indicator we can observe — it's
# also the narrowest (only ~10 creators total), so weight ~0.10
# rather than the 0.20 that creator_pickup gets. Search velocity
# eases from 0.60 → 0.55; the other three narrow proportionally.
_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    "search_velocity": 0.55,
    "creator_pickup": 0.15,
    "creator_upload_lead": 0.10,
    "social_velocity": 0.13,
    "news_lead": 0.07,
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
        # 2026-07-14 (class-of-bug scan): elevated to WARNING per
        # CLAUDE.md rule #17. Prior state was the exact class-of-bug
        # that made pytrends dormant for months: silent ImportError
        # under DEBUG → operator never knew the anticipation engine
        # was a no-op. Rule #17 mandates WARNING minimum for
        # ImportError fail-opens.
        logger.warning(
            "[trend_anticipation] pytrends not installed — search velocity skipped "
            "(install `pytrends>=4.9` to enable this signal)"
        )
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


_CACHE_TTL_HOURS: Final[int] = 12


def _get_cache():
    """Cache handle for the two paid signals. Same disk-cache pattern
    the trending-video fetcher uses. Kept lazy so importing this
    module doesn't touch the filesystem (matters for the many code
    paths that import ``trend_anticipation`` for the module-level
    constants without ever calling a signal function)."""
    try:
        from genlab_core.cache.disk_cache import Cache

        return Cache(cache_dir=".tmp/cache/trend_anticipation")
    except Exception as exc:
        logger.debug("[trend_anticipation] cache unavailable: %s", exc)
        return None


def _cache_key(prefix: str, topic: str, niche_id: str) -> str:
    """Compose a safe alphanumeric cache key. The disk-cache validates
    keys against ``[a-zA-Z0-9_.-]{1,256}``; slugify unsafe chars from
    the topic so trending phrases like "Zelda: Tears of the Kingdom"
    still fit."""
    import re

    slug = re.sub(r"[^a-zA-Z0-9]", "_", topic.lower())[:80]
    return f"{prefix}_{niche_id}_{slug}"


def _signal_creator_pickup(topic: str, niche_id: str) -> float | None:
    """Creator-pickup — how many YouTube creators posted about the
    topic in the last 7 days.

    Session 2 (2026-07-01) wires this via YouTube Data API v3
    ``search.list``. One search costs 100 quota units — daily total
    if we score top-5 per niche × 5 niches = 25 topics = 2500 units
    (25% of the 10K daily budget). Cached 12h to smooth reruns.

    Score mapping: ``min(1.0, log10(1 + total_results) / 3.0)``.
    Rationale: totalResults returned by YouTube search is unbounded,
    so a linear normaliser saturates instantly on hot topics. Log-
    scale means:

      * 0 results        → 0.000  (cold)
      * 10 results       → 0.347  (emerging)
      * 100 results      → 0.667  (warming)
      * 1000+ results    → 1.000  (hot / already picked-up)

    Because ``compute_anticipation_score`` prizes ACCELERATION (via
    the search-velocity 2nd derivative), a HIGH creator_pickup +
    HIGH search-velocity is the "trend already viral, don't publish
    here" signal, and a LOW creator_pickup + HIGH search-velocity is
    the "trend accelerating but no creators yet" signal — exactly
    what the anticipation intent is looking for.

    Guardrails:

    * ``GENLAB_ANTICIPATION_YT_ENABLED`` — separate opt-in flag so
      an operator can activate the paid signal without flipping the
      pipeline-consume flag. Costs quota only when set to ``true``.
    * Missing ``YOUTUBE_API_KEY`` → None (fail-open).
    * Any HTTP / parse error → None (silent, DEBUG-logged).
    """
    if os.environ.get("GENLAB_ANTICIPATION_YT_ENABLED", "") not in ("true", "TRUE", "True"):
        return None

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return None

    cache = _get_cache()
    key = _cache_key("cp", topic, niche_id)
    if cache is not None:
        cached = cache.get(key, ttl_hours=_CACHE_TTL_HOURS)
        if cached is not None and isinstance(cached, (int, float)):
            return float(cached)

    try:
        import math
        from datetime import timedelta

        import requests

        published_after = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # search.list — 100 quota units, one call per topic per 12h.
        # ``type=video``, ``publishedAfter`` = 7 days ago. The
        # ``pageInfo.totalResults`` is a coarse UPPER BOUND (YouTube
        # doesn't guarantee exactness at scale) but consistent enough
        # for a normalised score.
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "id",
                "q": topic,
                "type": "video",
                "publishedAfter": published_after,
                "maxResults": 1,  # We only need the count, not the results
                "key": api_key,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[trend_anticipation] YT search returned %d for %r",
                resp.status_code,
                topic,
            )
            return None
        total = int(resp.json().get("pageInfo", {}).get("totalResults", 0))
    except Exception as exc:
        logger.debug(
            "[trend_anticipation] YT search failed for %r (%s): %s",
            topic,
            niche_id,
            exc,
        )
        return None

    score = min(1.0, math.log10(1 + max(0, total)) / 3.0)
    if cache is not None:
        try:
            cache.set(key, score)
        except Exception:
            pass
    return score


def _signal_social_velocity(topic: str, niche_id: str) -> float | None:
    """Social-velocity — Reddit karma per hour on posts mentioning
    the topic across the last week.

    Uses the same OAuth token dance as
    ``genlab_core.intel.reddit_fetcher`` — same client credentials,
    same user-agent. ``/api/search`` with the topic + ``t=week``
    returns recent posts across all subreddits; we filter to the
    niche-relevant subs later (Session 3 wire) and for now compute
    the aggregate across the whole result set.

    Score mapping: ``min(1.0, karma_per_hour / 50.0)``. Rationale:

      * 0 karma/h      → 0.00
      * 25 karma/h     → 0.50 (a real trending post)
      * 50+ karma/h    → 1.00 (viral)

    50 karma/h is roughly the boundary between "regular Reddit post"
    and "you'll see this on r/all in the next 6 hours." Tuneable
    per-niche in a future session once we have observed vs mapped
    data.

    Guardrails:

    * ``GENLAB_ANTICIPATION_REDDIT_ENABLED`` opt-in flag — respects
      Reddit rate limits; caller must consciously enable it.
    * Missing ``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET`` → None.
    * Cached 12h.
    """
    if os.environ.get("GENLAB_ANTICIPATION_REDDIT_ENABLED", "") not in (
        "true",
        "TRUE",
        "True",
    ):
        return None

    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    cache = _get_cache()
    key = _cache_key("sv", topic, niche_id)
    if cache is not None:
        cached = cache.get(key, ttl_hours=_CACHE_TTL_HOURS)
        if cached is not None and isinstance(cached, (int, float)):
            return float(cached)

    try:
        import time as _time_mod

        import requests

        # Reuse the token helper from reddit_fetcher. Lazy import
        # keeps this module loadable without the reddit_fetcher
        # module being import-clean (a defensive-imports policy the
        # intel package follows across files).
        from genlab_core.intel.reddit_fetcher import (
            _OAUTH_BASE,
            _USER_AGENT,
            _get_access_token,
        )

        token = _get_access_token(client_id, client_secret)
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT}
        resp = requests.get(
            f"{_OAUTH_BASE}/search",
            headers=headers,
            params={
                "q": topic,
                "t": "week",
                "sort": "relevance",
                "limit": 25,
                "restrict_sr": "false",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[trend_anticipation] Reddit search %d for %r",
                resp.status_code,
                topic,
            )
            return None

        now = _time_mod.time()
        karma_per_hour_sum = 0.0
        n = 0
        for child in resp.json().get("data", {}).get("children", []):
            post = child.get("data", {})
            score = float(post.get("score", 0) or 0)
            created = float(post.get("created_utc", 0) or 0)
            if created <= 0:
                continue
            hours = max(0.5, (now - created) / 3600.0)
            karma_per_hour_sum += score / hours
            n += 1
        if n == 0:
            return None
        mean_kph = karma_per_hour_sum / n
    except Exception as exc:
        logger.debug(
            "[trend_anticipation] Reddit search failed for %r (%s): %s",
            topic,
            niche_id,
            exc,
        )
        return None

    signal = min(1.0, max(0.0, mean_kph / 50.0))
    if cache is not None:
        try:
            cache.set(key, signal)
        except Exception:
            pass
    return signal


def _signal_creator_upload_lead(topic: str, niche_id: str) -> float | None:
    """Creator-upload-lead — fraction of hand-curated top-tier creators
    who published about the topic in the last 24h.

    A.3 (2026-07-08). Consumes the JSON artifacts written by A.2's
    ``watch_top_creator_uploads.py`` runner at
    ``$GENLAB_TMP/top-creator-uploads/YYYYMMDD-<niche>.json``.

    The signal is more targeted than ``creator_pickup`` (which counts
    YouTube search results): here we only look at operator-curated
    top-tier channels. Coverage is narrow (~10 creators total), but
    when the signal fires it's a strong leading indicator — top
    creators typically publish 6-24h before Google Trends catches
    the topic.

    Score construction:

        Let N = total creators polled in the artifact for this niche
        Let M = creators whose most-recent upload title matches the
                topic (case-insensitive word-boundary)
        Let R = recency weight: uploads within 6h → 1.0, 6-24h → 0.5,
                > 24h → 0.0

        score = min(1.0, weighted_matches / min(N, 3))

    The ``min(N, 3)`` cap treats "3 top creators mentioned it" as
    saturation — after that we're confident this is a real trend.
    This is a MODEST prior compared to the other signals; matches
    the "narrow but sharp" character of the source.

    Fail-open contract:
    * Missing artifact directory → None (no data yet — expected on
      day 0 before the runner has fired)
    * No YYYYMMDD-<niche>.json for TODAY or YESTERDAY → None
    * Malformed JSON → None (log at DEBUG, don't warn — the runner
      writes the file, we don't need to alert on transient reads)
    * Empty creators list → None (watchlist not populated yet)
    * Zero matches → 0.0 (this IS informative; the topic isn't hot
      among the curated creators)

    Case-insensitive word-boundary match: the topic ``"vision pro"``
    matches titles like ``"Apple Vision Pro Hands-On"`` but NOT
    ``"provision"``.
    """
    import re
    from datetime import timedelta

    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    dir_ = root / "top-creator-uploads"
    if not dir_.is_dir():
        return None

    # Look for today's artifact first, then yesterday's. Older files
    # aren't leading indicators anymore.
    today = datetime.now(UTC).strftime("%Y%m%d")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y%m%d")
    candidates = [
        dir_ / f"{today}-{niche_id}.json",
        dir_ / f"{yesterday}-{niche_id}.json",
    ]
    payload: dict | None = None
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
            break
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "[trend_anticipation] creator_upload_lead read %s: %s",
                path.name,
                exc,
            )
            continue

    if payload is None:
        return None

    creators = payload.get("creators") or []
    if not creators:
        return None

    # Case-insensitive word-boundary regex for the topic. Escape any
    # regex meta chars in the topic string.
    escaped = re.escape(topic.strip().lower())
    if not escaped:
        return None
    matcher = re.compile(r"\b" + escaped + r"\b", re.IGNORECASE)

    # Recency weight decays over 24h. Uploads > 24h old contribute 0.
    def _recency_weight(hours: float) -> float:
        if hours < 0 or hours > 24:
            return 0.0
        if hours <= 6:
            return 1.0
        # 6-24h: linear decay 1.0 → 0.5
        return 1.0 - 0.5 * ((hours - 6.0) / 18.0)

    weighted_matches = 0.0
    for creator in creators:
        best_match_weight = 0.0
        for upload in creator.get("uploads") or []:
            title = str(upload.get("title") or "")
            if not title:
                continue
            if matcher.search(title):
                weight = _recency_weight(float(upload.get("hours_since_publish") or 0))
                if weight > best_match_weight:
                    best_match_weight = weight
        weighted_matches += best_match_weight

    # Saturation cap: 3 matching creators (weighted) → 1.0. This
    # keeps the signal MODEST relative to the other four.
    n = min(len(creators), 3)
    if n == 0:
        return None
    return min(1.0, weighted_matches / n)


def _signal_news_lead(topic: str, niche_id: str) -> float | None:
    """News-lead — count of Google News articles about the topic in
    the last 3 days.

    Session 3 (2026-07-01) wires this via Google News RSS. That feed
    is zero-cost, no API key required, unratelimited in practice —
    the simplest of the four signal sources. The URL shape is
    ``https://news.google.com/rss/search?q=<topic>+when:3d``.

    Score mapping: ``min(1.0, log10(1 + article_count) / 2.0)``.
    Same log rationale as ``_signal_creator_pickup`` (news article
    counts range from 0 to hundreds). Denominator 2.0 (vs 3.0 for
    YouTube) because 10-100 news articles is already "hot news
    story" — the news pipeline is small enough that saturation
    hits sooner than YouTube search:

      * 0 articles       → 0.00
      * 5 articles       → 0.39
      * 20 articles      → 0.66
      * 100+ articles    → 1.00

    Guardrails:

    * ``GENLAB_ANTICIPATION_NEWS_ENABLED`` opt-in flag. Zero API
      cost + no auth, so this flag is more about "don't spam Google
      News on every daily run before we've validated the score
      mapping" than quota — flip it after Session 3 ships.
    * Missing ``feedparser`` → None (fail-open).
    * Cached 12h per (topic, niche).
    """
    if os.environ.get("GENLAB_ANTICIPATION_NEWS_ENABLED", "") not in (
        "true",
        "TRUE",
        "True",
    ):
        return None

    cache = _get_cache()
    key = _cache_key("nl", topic, niche_id)
    if cache is not None:
        cached = cache.get(key, ttl_hours=_CACHE_TTL_HOURS)
        if cached is not None and isinstance(cached, (int, float)):
            return float(cached)

    try:
        import math
        import urllib.parse

        import feedparser

        # ``when:3d`` in the Google News query limits results to the
        # last 3 days. URL-encoding the topic string keeps phrases
        # with spaces / punctuation from breaking the request.
        encoded = urllib.parse.quote_plus(f"{topic} when:3d")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US"
        # feedparser has no built-in timeout — the network fetch is
        # blocking. Use a short-timeout urlopen wrapper via requests
        # then hand feedparser the bytes; matches how rss_parser
        # already reads (defensive against hangs on flaky sources).
        import requests

        resp = requests.get(url, timeout=15, headers={"User-Agent": "GenLab/1.0"})
        if resp.status_code != 200:
            logger.debug(
                "[trend_anticipation] Google News returned %d for %r",
                resp.status_code,
                topic,
            )
            return None
        parsed = feedparser.parse(resp.content)
        n_articles = len(parsed.get("entries", []))
    except Exception as exc:
        logger.debug(
            "[trend_anticipation] news_lead failed for %r (%s): %s",
            topic,
            niche_id,
            exc,
        )
        return None

    if n_articles == 0:
        return None
    score = min(1.0, math.log10(1 + n_articles) / 2.0)
    if cache is not None:
        try:
            cache.set(key, score)
        except Exception:
            pass
    return score


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
        "creator_upload_lead": _signal_creator_upload_lead(topic, niche_id),
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


# ── Read side (pipeline consumers, Session 3) ──────────────────────


def read_todays_anticipation(
    niche_id: str,
    *,
    output_dir: Path | None = None,
) -> list[AnticipationScore] | None:
    """Load today's anticipation artifact for a niche, or None.

    Pipeline consumers call this when they want to reorder their
    candidate topic list by anticipation score. Semantics:

    * Returns ``None`` when the flag ``GENLAB_TREND_ANTICIPATION_ENABLED``
      is off — this is deliberate. The flag is the sole switch that
      turns pipeline-side steering on; the runner + dashboard read
      the artifact unconditionally, but the pipeline only acts on it
      when the operator has explicitly opted in.
    * Returns ``None`` when today's artifact doesn't exist yet
      (runner hasn't fired, cold-start day, or systemd unit
      disabled).
    * Returns ``None`` when the artifact is malformed. Never raises.
    * Otherwise returns the ranking as a list of
      :class:`AnticipationScore` (deserialised — the dashboard reads
      raw JSON, but pipeline code prefers the typed dataclass).

    Callers should treat ``None`` as "no anticipation signal
    available — proceed with the default candidate list." Same
    fail-open pattern the reward-shaper's percentile_targets_fn
    uses.
    """
    if not _integration_enabled():
        return None

    out_dir = output_dir or _resolve_output_dir()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = out_dir / f"{stamp}-{niche_id}.json"
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug(
            "[trend_anticipation] read failed for %s/%s: %s",
            stamp,
            niche_id,
            exc,
        )
        return None

    raw = payload.get("ranking") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return None

    scored: list[AnticipationScore] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            scored.append(
                AnticipationScore(
                    topic=str(row.get("topic", "")),
                    niche_id=str(row.get("niche_id", niche_id)),
                    composite_score=float(row.get("composite_score", 0.0)),
                    signals=row.get("signals", {}) or {},
                    anticipated_peak_hours_ahead=(
                        int(row["anticipated_peak_hours_ahead"])
                        if row.get("anticipated_peak_hours_ahead") is not None
                        else None
                    ),
                    confidence=float(row.get("confidence", 0.0)),
                    reasons=list(row.get("reasons") or []),
                )
            )
        except (TypeError, ValueError) as exc:
            logger.debug(
                "[trend_anticipation] skipping malformed ranking row: %s",
                exc,
            )
    return scored


def reorder_by_anticipation(
    candidates: list[str],
    niche_id: str,
    *,
    output_dir: Path | None = None,
) -> list[str]:
    """Reorder a candidate topic list by today's anticipation ranking.

    Convenience wrapper around :func:`read_todays_anticipation` for
    pipeline call sites that want a same-shape output (list[str] in,
    list[str] out). When the anticipation artifact is unavailable
    (flag off / cold start / stale), returns the input unchanged —
    the pipeline decision path stays byte-identical to today's
    behaviour.

    Ranking logic:

    * Candidates that appear in today's anticipation ranking surface
      FIRST, ordered by anticipation composite_score descending.
    * Candidates that don't appear in the ranking preserve their
      original order and follow.

    This is the minimally-invasive shape a pipeline stage can adopt:
    the anticipation module doesn't remove any candidates or inject
    new ones — it only reorders WHICH of the caller's candidates
    the pipeline picks first.
    """
    ranking = read_todays_anticipation(niche_id, output_dir=output_dir)
    if not ranking:
        return list(candidates)

    # Case-insensitive match — pytrends topics use varied casing.
    score_by_topic: dict[str, float] = {s.topic.strip().lower(): s.composite_score for s in ranking}

    scored_candidates = [
        (score_by_topic.get(c.strip().lower(), None), i, c) for i, c in enumerate(candidates)
    ]

    # Anticipated topics first (highest composite score → lowest);
    # unscored preserve original order via the tuple's index tiebreak.
    def sort_key(item: tuple[float | None, int, str]):
        score, idx, _ = item
        # None → last; score → sort descending
        if score is None:
            return (1, idx, 0.0)
        return (0, 0, -score)

    scored_candidates.sort(key=sort_key)
    return [c for _, _, c in scored_candidates]


__all__ = [
    "AnticipationScore",
    "compute_anticipation_score",
    "rank_topics",
    "persist_ranking",
    "read_todays_anticipation",
    "reorder_by_anticipation",
    "_signal_search_velocity",
    "_search_velocity_from_series",
    "_SIGNAL_WEIGHTS",
]
