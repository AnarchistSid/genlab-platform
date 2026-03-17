"""DailyCapEnforcer — prevents publishing more than N posts per platform per UTC day.

Caps are declared in genlab-core/config/platform_caps.yaml.
Counts are derived from the Publishing_Analytics SharePoint list at run start,
then maintained in-session via an in-memory counter so that multiple niches
publishing in the same process run don't race on the same cap.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CAPS: dict[str, int] = {
    p: 1 for p in
    ["instagram", "youtube", "facebook", "tiktok", "twitter", "threads"]
}


def _load_caps(config_path: Optional[Path] = None) -> dict[str, int]:
    """Load daily post caps from config/platform_caps.yaml. Falls back to 2/platform."""
    if config_path is None:
        # Walk up from this file to genlab-core root, then config/ subdir
        genlab_root = Path(__file__).resolve().parents[4]
        config_path = genlab_root / "genlab-core" / "config" / "platform_caps.yaml"

    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        caps = cfg.get("daily_post_cap", {})
        if not caps:
            logger.warning("platform_caps.yaml has no daily_post_cap section, using defaults")
            return dict(_DEFAULT_CAPS)
        return {k.lower(): int(v) for k, v in caps.items()}
    except FileNotFoundError:
        logger.warning(
            "platform_caps.yaml not found at %s. Using default cap of 1.", config_path
        )
        return dict(_DEFAULT_CAPS)
    except Exception as e:
        logger.error("Error loading platform caps: %s. Using default cap of 1.", e)
        return dict(_DEFAULT_CAPS)


class DailyCapEnforcer:
    """Enforces a hard cap of N posts per platform per niche per UTC calendar day.

    When ``niche_id`` is provided, counts are scoped to that niche only —
    gaming's Instagram cap is independent of ai_creators' cap.

    Designed to be instantiated once per publish run. Thread-safe for
    sequential use (not concurrent — parallel publishers in different
    processes should rely on the SharePoint count loaded at startup).

    Usage::

        enforcer = DailyCapEnforcer(backlog_client, niche_id="gaming")

        for platform in platforms:
            if not enforcer.can_publish(platform):
                logger.info(f"{platform}: daily cap reached, skipping")
                continue
            result = publish_to(platform, ...)
            if result.success:
                enforcer.record_publish(platform)
    """

    def __init__(self, backlog_client, niche_id: str = "", config_path: Optional[Path] = None):
        self._client = backlog_client
        self._niche_id = niche_id
        self._caps = _load_caps(config_path)
        self._session_counts: dict[str, int] = {}
        self._counts_loaded_for: Optional[date] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def can_publish(self, platform: str) -> bool:
        """Return True if this platform still has capacity today.

        Fails open (returns True) if the cap is unconfigured.
        """
        platform = platform.lower()
        cap = self._caps.get(platform)
        if cap is None:
            logger.warning(
                "No daily cap configured for '%s', allowing publish.", platform
            )
            return True

        current = self._get_counts().get(platform, 0)
        if current >= cap:
            logger.info(
                "Daily cap reached for %s: %d/%d posts today. Skipping.",
                platform, current, cap,
            )
            return False

        logger.debug("%s: %d/%d today — OK to publish.", platform, current, cap)
        return True

    def record_publish(self, platform: str) -> None:
        """Increment the in-session counter immediately after a successful publish.

        Prevents a second publish in the same run from seeing a stale count
        before SharePoint is updated by fetch_insights.
        """
        platform = platform.lower()
        self._get_counts()  # Ensure loaded
        self._session_counts[platform] = self._session_counts.get(platform, 0) + 1
        logger.debug(
            "Recorded publish for %s. Session total today: %d",
            platform, self._session_counts[platform],
        )

    def get_remaining(self, platform: str) -> int:
        """Return how many posts can still go out today for this platform."""
        platform = platform.lower()
        cap = self._caps.get(platform, 1)
        return max(0, cap - self._get_counts().get(platform, 0))

    def log_headroom(self) -> None:
        """Log remaining capacity for all configured platforms. Call at run start."""
        logger.info("Daily publish headroom:")
        for platform in sorted(self._caps):
            remaining = self.get_remaining(platform)
            cap = self._caps[platform]
            current = cap - remaining
            logger.info("  %s: %d/%d used, %d remaining", platform, current, cap, remaining)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _today_utc(self) -> date:
        return datetime.now(timezone.utc).date()

    def _get_counts(self) -> dict[str, int]:
        today = self._today_utc()
        if self._counts_loaded_for != today:
            self._session_counts = self._load_today_counts()
            self._counts_loaded_for = today
        return self._session_counts

    def _load_today_counts(self) -> dict[str, int]:
        """Query the Publishing_Analytics SharePoint list for today's publishes.

        Falls back to empty counts on any error (fail-open for publishing).
        """
        today_str = self._today_utc().isoformat()  # "2026-03-13"
        counts: dict[str, int] = {}

        try:
            items = self._client.publishing_analytics.all(
                formula=f"{{published_at}}>='{today_str}T00:00:00Z'"
            )

            for item in items:
                fields = item.get("fields", item)
                status = str(fields.get("status") or "").strip()
                if status != "SUCCESS":
                    continue
                # Filter by niche_id when set — each channel's cap is independent
                if self._niche_id:
                    item_niche = str(fields.get("niche_id") or "").strip()
                    if item_niche and item_niche != self._niche_id:
                        continue
                # published_at may be a datetime object from Graph SDK
                raw_pub = fields.get("published_at")
                if isinstance(raw_pub, datetime):
                    pub_at = raw_pub.strftime("%Y-%m-%d")
                else:
                    pub_at = str(raw_pub or "").strip()
                if not pub_at.startswith(today_str):
                    continue
                platform = str(fields.get("platform") or "").lower().strip()
                if platform:
                    counts[platform] = counts.get(platform, 0) + 1

        except Exception as e:
            logger.error(
                "Failed to load today's publish counts from SharePoint: %s. "
                "Starting from 0 (fail-open).", e
            )

        niche_label = f" for {self._niche_id}" if self._niche_id else " (global)"
        logger.info("Loaded today's counts%s (%s): %s", niche_label, today_str, counts)
        return counts
