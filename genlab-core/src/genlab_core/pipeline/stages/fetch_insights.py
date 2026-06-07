"""Pipeline stage: Fetch post-publish engagement metrics.

Queries SharePoint Publishing_Analytics for PREVIOUSLY published posts
(6h-7d ago) and fetches engagement metrics from platform APIs.

Uses multi-window strategy:
  - FRESH: 6-48h after publish (first snapshot)
  - WARM:  2-7 days (growth tracking)

Writes metrics into context['run_stats']['insights'] and marks fetched
records in SharePoint via metrics_fetched timestamp.

Non-fatal: API failures are logged per-platform, never crash pipeline.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from genlab_core.config.tuning import get_tuning_config
from genlab_core.http.circuit_breaker import (
    CircuitOpenError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)

_TUNING = get_tuning_config().fetch_insights
# Minimum hours after publish before fetching (API data delay)
MIN_DELAY_HOURS: int = _TUNING.min_delay_hours
# Maximum age for "warm" window
MAX_WARM_DAYS: int = _TUNING.max_warm_days


def normalize_publishing_metrics(metrics: dict[str, Any]) -> dict[str, int]:
    """Collapse per-platform metric keys onto the publishing_analytics columns.

    The platform fetchers return inconsistent keys: IG uses ``reach``/``saved``,
    Facebook uses ``reactions``, X uses ``impressions``/``retweets``. The
    ``publishing_analytics`` table has canonical ``views/likes/comments/
    shares/saves`` columns, so map each platform's vocabulary onto them. Used
    to persist raw metrics back into publishing_analytics (previously only the
    composite landed in the ``analytics`` table, leaving these columns at 0).
    """

    def _int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "views": _int(metrics.get("views") or metrics.get("reach") or metrics.get("impressions")),
        "likes": _int(metrics.get("likes") or metrics.get("reactions")),
        "comments": _int(metrics.get("comments") or metrics.get("replies")),
        "shares": _int(metrics.get("shares") or metrics.get("retweets")),
        "saves": _int(metrics.get("saves") or metrics.get("saved")),
    }


class FetchInsights:
    """Fetch post-publish engagement metrics from platform APIs.

    Queries SharePoint Publishing_Analytics for posts published 6h-7d ago
    that haven't had metrics collected yet (metrics_fetched is empty).

    Reads: context['backlog_client'], context['niche_id'], context['niche_config']
    Writes: context['run_stats']['insights']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")
        client = context.get("backlog_client")
        config = context.get("niche_config", {})

        if not client:
            logger.info("[FetchInsights] No backlog_client — skipping")
            return context

        now = datetime.now(UTC)
        fetched = 0
        skipped = 0
        errors = 0
        platform_stats: dict[str, dict[str, int]] = {}

        # Query Publishing_Analytics for posts in this niche
        try:
            formula = f"AND({{niche_id}}='{niche_id}')"
            records = client.publishing_analytics.all(formula=formula)
        except Exception:
            logger.exception("[FetchInsights] Failed to query Publishing_Analytics")
            context.setdefault("run_stats", {})["insights"] = {
                "fetched": 0,
                "skipped": 0,
                "errors": 1,
                "platforms": {},
            }
            return context

        for record in records:
            fields = record.get("fields", {})
            post_id = fields.get("post_id", "")
            platform = fields.get("platform", "")
            published_at = fields.get("published_at", "")
            metrics_fetched = fields.get("metrics_fetched", "")

            # Skip already fetched
            if metrics_fetched:
                skipped += 1
                continue

            # Skip if no post_id or platform
            if not post_id or not platform:
                skipped += 1
                continue

            # Parse publish time and check age
            try:
                if isinstance(published_at, str):
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                else:
                    pub_dt = published_at
            except (ValueError, TypeError):
                skipped += 1
                continue

            age_hours = (now - pub_dt).total_seconds() / 3600
            if age_hours < MIN_DELAY_HOURS:
                skipped += 1
                continue
            if age_hours > MAX_WARM_DAYS * 24:
                skipped += 1
                continue

            # Fetch metrics
            stats = platform_stats.setdefault(
                platform,
                {"fetched": 0, "errors": 0},
            )
            try:
                fetch_config = {**config, "niche_id": niche_id}
                metrics = self._fetch_platform(platform, post_id, fetch_config)
                if metrics:
                    # Write metrics to analytics table (strip platform prefix for clean ID)
                    clean_id = post_id.split(":", 1)[1] if ":" in post_id else post_id
                    try:
                        client.upsert_analytics(
                            post_id=clean_id,
                            platform=platform,
                            insights=metrics,
                            niche_id=niche_id,
                            fetch_window="pipeline",
                        )
                    except Exception:
                        logger.warning(
                            "[FetchInsights] Analytics upsert failed for %s",
                            post_id,
                        )
                    # Persist raw metrics + fetched timestamp into
                    # publishing_analytics. Previously only the composite was
                    # written (to the analytics table) and these columns stayed
                    # at 0, so no post could be traced to its real engagement.
                    try:
                        client.publishing_analytics.update(
                            record["id"],
                            {
                                "metrics_fetched": now.isoformat(),
                                **normalize_publishing_metrics(metrics),
                            },
                        )
                    except Exception:
                        logger.warning(
                            "[FetchInsights] Failed to write metrics for %s",
                            post_id,
                        )
                    stats["fetched"] += 1
                    fetched += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "[FetchInsights] %s fetch failed for post %s",
                    platform,
                    post_id,
                )
                stats["errors"] += 1
                errors += 1

        logger.info(
            "[FetchInsights] %d fetched, %d skipped, %d errors | %s",
            fetched,
            skipped,
            errors,
            {k: v for k, v in platform_stats.items() if v["fetched"] or v["errors"]},
        )
        context.setdefault("run_stats", {})["insights"] = {
            "fetched": fetched,
            "skipped": skipped,
            "errors": errors,
            "platforms": platform_stats,
        }
        return context

    def _fetch_platform(
        self,
        platform: str,
        post_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Dispatch to platform-specific fetcher through circuit breaker."""
        fetchers = {
            "instagram": self._fetch_instagram,
            "youtube": self._fetch_youtube,
            "facebook": self._fetch_facebook,
            "x": self._fetch_twitter,
            "twitter": self._fetch_twitter,
        }
        fetcher = fetchers.get(platform)
        if not fetcher:
            logger.debug("[FetchInsights] No fetcher for platform: %s", platform)
            return None

        # Strip platform prefix from composite post_id (e.g. "instagram:123" → "123")
        raw_id = post_id
        if ":" in post_id:
            raw_id = post_id.split(":", 1)[1]

        cb = get_circuit_breaker(platform)
        if cb is not None:
            try:
                return cb.call(fetcher, raw_id, config)
            except CircuitOpenError:
                logger.warning(
                    "[FetchInsights] %s circuit open — skipping %s",
                    platform,
                    post_id,
                )
                return None
        return fetcher(raw_id, config)

    @staticmethod
    def _fetch_instagram(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch IG metrics — delegates to the canonical implementation.

        Adds the legacy ``saved`` alias on top of the canonical ``saves`` so
        downstream consumers (notably ``normalize_publishing_metrics`` and any
        analytics composite-score reader expecting the older key) see a
        byte-stable shape.
        """
        from genlab_core.platforms.metrics import fetch_instagram as _canonical

        niche_id = config.get("niche_id", "")
        metrics = _canonical(post_id, niche_id=niche_id)
        if metrics is None:
            return None
        out: dict[str, Any] = dict(metrics)
        if "saves" in out:
            out["saved"] = out["saves"]
        return out

    @staticmethod
    def _fetch_youtube(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch YT metrics — thin delegate to the canonical implementation."""
        from genlab_core.platforms.metrics import fetch_youtube as _canonical

        metrics = _canonical(post_id)
        return dict(metrics) if metrics is not None else None

    @staticmethod
    def _fetch_facebook(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch FB metrics via Graph API.

        DESIGN NOTE — this fetcher intentionally does NOT delegate to
        :func:`genlab_core.platforms.metrics.fetch_facebook`. The canonical
        fetcher uses ``/{post_id}/video_insights`` (Reels-specific); this
        pipeline-stage call uses the general ``/{post_id}?fields=reactions...``
        surface that works for any FB post type (not just Reels). The two
        endpoints return different metric sets and aren't interchangeable.
        A future canonical companion (``fetch_facebook_post``) would let
        this delegate cleanly.
        """
        niche_id = config.get("niche_id", "")
        from genlab_core.publishing.niche_credentials import resolve_fb_credentials

        token, _page_id = resolve_fb_credentials(niche_id)
        if not token:
            return None

        try:
            import requests

            url = f"https://graph.facebook.com/v21.0/{post_id}"
            params = {
                "fields": "shares,reactions.summary(total_count),comments.summary(total_count)",
                "access_token": token,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return {
                "shares": data.get("shares", {}).get("count", 0),
                "reactions": data.get("reactions", {}).get("summary", {}).get("total_count", 0),
                "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
            }
        except Exception:
            logger.exception("[FetchInsights] FB fetch error for %s", post_id)
            return None

    @staticmethod
    def _fetch_twitter(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch X metrics — delegates to the canonical implementation.

        Adds the legacy ``retweets`` / ``replies`` aliases on top of the
        canonical ``shares`` / ``comments`` so downstream consumers (notably
        ``normalize_publishing_metrics`` which reads either form) and any
        analytics composite-score reader expecting the older keys see a
        byte-stable shape.
        """
        from genlab_core.platforms.metrics import fetch_twitter as _canonical

        metrics = _canonical(post_id)
        if metrics is None:
            return None
        out: dict[str, Any] = dict(metrics)
        if "shares" in out:
            out["retweets"] = out["shares"]
        if "comments" in out:
            out["replies"] = out["comments"]
        return out
