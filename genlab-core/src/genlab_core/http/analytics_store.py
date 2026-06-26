"""Analytics + publishing-analytics persistence, extracted from BacklogClient.

The BacklogClient god class (1,494 LOC pre-Tier-2) mixed storage routing
with domain-specific virality-score computation and analytics upserts.
This module owns the analytics surface: writing per-publish records to
``Publishing_Analytics`` and the per-post engagement composite to
``Analytics``. BacklogClient retains thin one-line delegators so the
public API stays byte-stable.

Tier 2 / audit S-2 — the first real BacklogClient extraction. Pattern
mirrors the publish_all_platforms decomposition (refactor #9): each
extracted module gets its own focused test file; BacklogClient becomes
a coordinator that delegates to small purpose-built stores.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from genlab_core.http.circuit_breaker import CircuitOpenError

logger = logging.getLogger(__name__)


# Fields on the Analytics row that some SharePoint deployments don't have
# yet — the upsert falls back to a stripped payload on UNKNOWN_FIELD_NAME /
# columnNotFound errors so a schema drift between dev/prod doesn't break
# the metric collector. Mirrors the legacy in-class set.
_ANALYTICS_OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        "platform",
        "candidate_id",
        "published_at",
        "format",
        "fetch_window",
        "story_title",
        "niche_id",
    }
)


def _esc(value: str) -> str:
    """Escape a single quote inside a Graph/Airtable formula literal.

    Mirrors the BacklogClient-private helper so callers don't need to
    import it from the legacy location.
    """
    return value.replace("'", r"\'")


class AnalyticsStore:
    """CRUD for ``Publishing_Analytics`` + ``Analytics`` tables.

    The constructor takes injectable dependencies (the SP-call helper
    and the backend-lookup function) so the store stays testable
    without spinning up the whole BacklogClient.
    """

    def __init__(
        self,
        *,
        sp_call: Callable[..., Any],
        backend: Callable[[str], Any],
    ) -> None:
        self._sp_call = sp_call
        self._backend = backend

    # ── Publishing_Analytics surface ────────────────────────────────

    def log_publish_result(
        self,
        candidate_id: str,
        platform: str,
        status: str,
        post_id: str = "",
        platform_format: str = "",
        time_to_publish_seconds: float = 0.0,
        error_message: str = "",
        file_size_bytes: int = 0,
        blueprint_record_id: str = "",
        niche_id: str = "",
    ) -> str | None:
        """Upsert a per-publish record into Publishing_Analytics.

        The ``analytics_id`` is a stable SHA-256 of ``candidate_id:platform``
        so re-running a publish updates the same row (idempotent). Returns
        the record id, or ``None`` on circuit-open or any other failure
        — analytics logging is best-effort and must NEVER unwind the
        publish path.
        """
        raw = f"{candidate_id}:{platform}"
        analytics_id = hashlib.sha256(raw.encode()).hexdigest()

        fields: dict[str, Any] = {
            "analytics_id": analytics_id,
            "candidate_id": candidate_id,
            "platform": platform,
            "status": status,
        }

        if post_id:
            fields["post_id"] = post_id
        if platform_format:
            fields["platform_format"] = platform_format
        if time_to_publish_seconds > 0:
            fields["time_to_publish_seconds"] = round(time_to_publish_seconds, 1)
        if error_message:
            fields["error_message"] = error_message[:2000]
        if file_size_bytes > 0:
            fields["file_size_bytes"] = file_size_bytes
        if status == "SUCCESS":
            fields["published_at"] = datetime.now(UTC).isoformat()
        if blueprint_record_id:
            fields["blueprint_link"] = str(blueprint_record_id)
        if niche_id:
            fields["niche_id"] = niche_id

        be = self._backend("Publishing_Analytics")
        try:
            existing = self._sp_call(
                be.find,
                "Publishing_Analytics",
                formula=f"{{analytics_id}}='{_esc(analytics_id)}'",
                max_records=1,
            )
            if existing:
                self._sp_call(
                    be.update,
                    "Publishing_Analytics",
                    existing[0]["id"],
                    fields,
                    typecast=True,
                )
                return existing[0]["id"]
            record = self._sp_call(
                be.create,
                "Publishing_Analytics",
                fields,
                typecast=True,
            )
            return record["id"]
        except CircuitOpenError:
            logger.warning("Publishing analytics log skipped — SharePoint circuit open")
            return None
        except Exception as exc:
            logger.warning("Publishing analytics log failed: %s", exc)
            return None

    def get_publishing_analytics(
        self,
        platform: str | None = None,
        status: str | None = None,
        *,
        niche_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Read Publishing_Analytics rows, optionally filtered by platform/status.

        ``niche_id`` filter is injected by the backend's ``find()`` (the
        cross-channel guard lives there), not constructed in the formula.
        """
        parts: list[str] = []
        if platform:
            parts.append(f"{{platform}}='{_esc(platform)}'")
        if status:
            parts.append(f"{{status}}='{_esc(status)}'")
        formula = f"AND({', '.join(parts)})" if parts else ""
        return self._sp_call(
            self._backend("Publishing_Analytics").find,
            "Publishing_Analytics",
            formula=formula or None,
            niche_id=niche_id,
            max_records=limit,
        )

    # ── Analytics surface (per-post composite scores) ───────────────

    def upsert_analytics(
        self,
        post_id: str,
        platform: str,
        insights: dict,
        blueprint_record_id: str = "",
        candidate_id: str = "",
        published_at: str = "",
        content_format: str = "",
        fetch_window: str = "",
        story_title: str = "",
        viral_score: float | None = None,
        niche_id: str = "",
    ) -> str | None:
        """Upsert a per-post engagement composite into Analytics.

        Computes engagement / save / share / play rates from the
        per-platform insight keys (with the IG ``saved`` / X
        ``retweets`` legacy alias handling). When ``viral_score`` is
        not provided, falls back to a generic weighted formula
        ``engagement_rate * 0.25 + share_rate * 0.40 + save_rate * 0.35``.

        Schema-drift defence: on ``UNKNOWN_FIELD_NAME`` / ``columnNotFound``,
        strips the optional fields and retries — keeps dev/prod schema
        drift from breaking the metric collector.

        Returns the record id (string), or ``None`` on any failure.
        """
        # Avoid double-prefixing: post_id may already be 'youtube:ABC123'.
        composite_id = post_id if post_id.startswith(f"{platform}:") else f"{platform}:{post_id}"

        reach = insights.get("reach", 0) or insights.get("impressions", 0) or 0
        likes = insights.get("likes", 0) or 0
        comments = insights.get("comments", 0) or 0
        saves = insights.get("saves", 0) or insights.get("saved", 0) or 0
        shares = insights.get("shares", 0) or insights.get("retweets", 0) or 0
        plays = insights.get("plays", 0) or insights.get("views", 0) or 0
        impressions = insights.get("impressions", 0) or reach
        # BUGFIX (post-2026-06-25 audit): platform metric fetchers in
        # learning/metrics/{youtube,instagram,facebook,x_twitter,tiktok,threads}.py
        # NEVER return an "engagement" key — they return likes/comments/shares as
        # separate fields. The prior `insights.get("engagement", 0) or 0` always
        # evaluated to 0, making engagement_rate = 0 / reach = 0.0 for every
        # record since commit f32b6189 (2026-06-09). Top Performers eng% on the
        # Analytics dashboard showed 0.0% across all rows as a consequence.
        # Fix: fall back to (likes + comments + shares) when no caller-provided
        # engagement field is present. Backward-compatible — callers (like the
        # unit tests) that DO pass "engagement" still take precedence.
        engagement = insights.get("engagement", 0) or (likes + comments + shares)

        # Only compute rates when reach > 0 — dividing by 1 when reach=0
        # inflates rates.
        engagement_rate = round(engagement / reach, 4) if reach > 0 else 0.0
        save_rate = round(saves / reach, 4) if reach > 0 else 0.0
        share_rate = round(shares / reach, 4) if reach > 0 else 0.0
        play_rate = round(plays / reach, 4) if reach > 0 else 0.0

        # Clamp rates to [0.0, 1.0]. Real-world engagement_rate cannot exceed
        # 100%; values above that come from tiny-audience posts where loyal
        # followers re-engage faster than reach grows (e.g. reach=4, engagement=8
        # yielded 200% on prod 2026-06-26 before this clamp). The clamp protects
        # downstream consumers (Top Performers card, bandit reward shaping,
        # viral_score formula) from misleading > 100% values.
        engagement_rate = min(1.0, engagement_rate)
        save_rate = min(1.0, save_rate)
        share_rate = min(1.0, share_rate)
        play_rate = min(1.0, play_rate)

        # Caller can pre-compute a config-driven score; otherwise fall back
        # to the generic weighted formula.
        if viral_score is not None:
            viral_score = round(viral_score, 4)
        else:
            viral_score = round(engagement_rate * 0.25 + share_rate * 0.40 + save_rate * 0.35, 4)

        fields: dict[str, Any] = {
            "post_id": composite_id,
            "platform": platform,
            "metric_type": "composite",
            "value": float(plays or reach or engagement or 0),
            "collected_at": datetime.now(UTC).isoformat(),
            "window": fetch_window or "adhoc",
            "impressions": impressions,
            "reach": reach,
            "engagement": engagement,
            "likes": likes,
            "comments": comments,
            "saved": saves,
            "shares": shares,
            "plays": plays,
            "viral_score": viral_score,
            "engagement_rate": engagement_rate,
            "save_rate": save_rate,
            "share_rate": share_rate,
            "play_rate": play_rate,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        if blueprint_record_id:
            fields["blueprint_link"] = str(blueprint_record_id)
        if candidate_id:
            fields["candidate_id"] = candidate_id
        if published_at:
            fields["published_at"] = published_at
        if content_format:
            fields["format"] = content_format
        if fetch_window:
            fields["fetch_window"] = fetch_window
        if story_title:
            fields["story_title"] = story_title
        if niche_id:
            fields["niche_id"] = niche_id

        be = self._backend("Analytics")
        try:
            existing = be.find(
                "Analytics",
                formula=f"{{post_id}}='{_esc(composite_id)}'",
                max_records=1,
            )
            if existing:
                try:
                    be.update("Analytics", existing[0]["id"], fields, typecast=True)
                except Exception as e:
                    if "UNKNOWN_FIELD_NAME" in str(e) or "columnNotFound" in str(e):
                        for f_name in _ANALYTICS_OPTIONAL_FIELDS:
                            fields.pop(f_name, None)
                        be.update("Analytics", existing[0]["id"], fields, typecast=True)
                    else:
                        raise
                return existing[0]["id"]
            try:
                record = be.create("Analytics", fields, typecast=True)
            except Exception as e:
                if "UNKNOWN_FIELD_NAME" in str(e) or "columnNotFound" in str(e):
                    for f_name in _ANALYTICS_OPTIONAL_FIELDS:
                        fields.pop(f_name, None)
                    record = be.create("Analytics", fields, typecast=True)
                else:
                    raise
            # Postgres create() returns a UUID string; SharePoint returns a dict.
            return record["id"] if isinstance(record, dict) else str(record)
        except Exception as exc:
            logger.warning("Analytics upsert failed for %s: %s", composite_id, exc)
            return None
