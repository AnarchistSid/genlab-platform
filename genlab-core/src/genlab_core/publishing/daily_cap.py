"""DailyCapEnforcer — prevents publishing more than N posts per platform per UTC day.

Caps are declared in genlab-core/config/platform_caps.yaml.
Counts are derived from the Publishing_Analytics SharePoint list at run start,
then maintained in-session via an in-memory counter so that multiple niches
publishing in the same process run don't race on the same cap.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CAPS: dict[str, int] = {
    p: 1 for p in ["instagram", "youtube", "facebook", "tiktok", "twitter", "threads"]
}


def _load_caps_config(config_path: Path | None = None) -> dict:
    """Load the full platform_caps.yaml. Returns ``{}`` on failure.

    Separated from :func:`_load_caps` (which returns the legacy
    per-platform dict) so multi_publish_gate can see the full config
    structure including ``multi_publish`` opt-in + ``max_per_day_ceiling``.
    """
    if config_path is None:
        genlab_root = Path(__file__).resolve().parents[4]
        config_path = genlab_root / "genlab-core" / "config" / "platform_caps.yaml"
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, Exception) as exc:  # noqa: BLE001
        logger.debug("[daily_cap] platform_caps.yaml unavailable: %s", exc)
        return {}


def _load_caps(config_path: Path | None = None) -> dict[str, int]:
    """Load daily post caps from config/platform_caps.yaml. Falls back to 1/platform."""
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
        logger.warning("platform_caps.yaml not found at %s. Using default cap of 1.", config_path)
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

    def __init__(self, backlog_client, niche_id: str = "", config_path: Path | None = None):
        self._client = backlog_client
        self._niche_id = niche_id
        self._caps = _load_caps(config_path)
        # Full yaml config (used to read multi_publish opt-in + ceiling).
        # Held separately so the legacy dict semantics of `self._caps`
        # stay byte-stable for any code path that introspects it.
        self._caps_config: dict = _load_caps_config(config_path)
        self._session_counts: dict[str, int] = {}
        self._counts_loaded_for: date | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def can_publish(self, platform: str) -> bool:
        """Return True if this platform still has capacity today.

        Fails CLOSED (returns False) if the cap is unconfigured for
        the platform — the audit (R-09) called out the historical
        fail-open behaviour as the most direct over-publish hazard.
        A typo or stale ``platform_caps.yaml`` should NEVER unblock
        publishing; the explicit "1 reel per channel per day" rule
        means a missing config is a misconfiguration, not a license.
        """
        platform = platform.lower()
        cap = self._caps.get(platform)
        if cap is None:
            logger.warning(
                "[daily_cap] No cap configured for '%s' — failing CLOSED "
                "(audit R-09). Add it to platform_caps.yaml to enable "
                "publishing on this platform.",
                platform,
            )
            return False

        # Task #33b (2026-06-13): compose effective cap from yaml +
        # optimal-time learner recommendation. When the operator opts
        # in via `multi_publish.enabled: true` in platform_caps.yaml AND
        # the learner has 2+ high-confidence hours for (niche, platform),
        # the effective cap rises from the conservative `daily_post_cap`
        # toward the operator's `max_per_day_ceiling`. Without the
        # opt-in, behavior is unchanged from R-09's hard 1/day rule.
        effective = self._effective_cap(platform, cap)

        current = self._get_counts().get(platform, 0)
        if current >= effective:
            logger.info(
                "Daily cap reached for %s: %d/%d posts today. Skipping.",
                platform,
                current,
                effective,
            )
            return False

        logger.debug("%s: %d/%d today — OK to publish.", platform, current, effective)
        return True

    def _effective_cap(self, platform: str, daily_post_cap: int) -> int:
        """Resolve the effective cap for (niche, platform) today.

        Defensively wrapped — a learner or gate failure must NEVER raise
        the effective cap above the yaml-declared one. The current rule
        "1 reel per channel per day" is a guarantee per CLAUDE.md.
        """
        try:
            from genlab_core.scheduling.multi_publish_gate import (
                effective_cap,
                is_multi_publish_enabled,
            )

            enabled = is_multi_publish_enabled(self._caps_config, platform=platform)
            ceiling = int(
                (self._caps_config.get("max_per_day_ceiling") or {}).get(platform, daily_post_cap)
            )
            return effective_cap(
                niche_id=self._niche_id,
                platform=platform,
                daily_post_cap=daily_post_cap,
                max_per_day_ceiling=ceiling,
                multi_publish_enabled=enabled,
            )
        except Exception as exc:
            logger.warning(
                "[daily_cap] multi_publish_gate failed (falling back to daily_post_cap=%d): %s",
                daily_post_cap,
                exc,
            )
            return daily_post_cap

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
            platform,
            self._session_counts[platform],
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
        return datetime.now(UTC).date()

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
                "Starting from 0 (fail-open).",
                e,
            )

        niche_label = f" for {self._niche_id}" if self._niche_id else " (global)"
        logger.info("Loaded today's counts%s (%s): %s", niche_label, today_str, counts)
        return counts
