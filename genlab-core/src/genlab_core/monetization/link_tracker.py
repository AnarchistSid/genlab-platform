"""Click tracker for affiliate links.

Uses a module-level singleton for database connection pooling instead of
creating a new PostgresBackend per click.

L3 PR 7 (2026-07-07) additions:
* ``blueprint_id`` sanitized empty-string → None. The migration
  ``a8w9x0y1z2a3`` (L3 PR 1) promoted this column to UUID with FK to
  blueprints, so an empty string can no longer INSERT — it must be
  NULL. Legacy callers still pass ``""`` when the reel didn't carry a
  ``?bp=`` param; we convert here so callers don't need to change.
* ``commission_pct`` snapshot argument. The catalog's commission rate
  at CLICK TIME is denormalized onto the click row so a retroactive
  catalog rate change (e.g. Amazon associates cutting rates from 4%
  to 3%) doesn't retroactively invalidate the reward attribution
  math. If a caller doesn't have the commission handy (legacy) the
  field is omitted — DB column stays NULL.
"""

import logging
import os
import re
import threading
import uuid as _uuid_module

logger = logging.getLogger(__name__)

_pg_lock = threading.Lock()
_pg_instance = None

# Match the canonical UUID hex-with-hyphens shape (case-insensitive).
# The bio-link hub redirect passes ``bp=<value>`` unchecked; a
# malicious caller could supply any string. Rejecting non-UUID
# strings here prevents INSERT failures on the FK column AND
# blocks probe-fuzzing that would otherwise spam the WARN log with
# psycopg InvalidTextRepresentation errors.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _sanitize_blueprint_id(value: str | None) -> str | None:
    """Return a valid UUID string or None.

    L3 PR 7: the ``affiliate_clicks.blueprint_id`` column was promoted
    to UUID with an FK to blueprints (L3 PR 1 migration). Historically
    the click handler passed empty string when the URL had no ``?bp=``
    param; that would fail INSERT after the migration.

    Empty/whitespace/None → None. Malformed UUID → None with debug log
    (not warn — an attacker can't spam WARN logs by hitting
    /links/go/<slug>?bp=garbage). Well-formed UUID → passed through
    unchanged.
    """
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if _UUID_RE.match(stripped):
        return stripped
    logger.debug("[LinkTracker] rejected non-UUID blueprint_id=%r", stripped[:40])
    return None


def _get_pg():
    """Return a shared PostgresBackend singleton (thread-safe)."""
    global _pg_instance
    if _pg_instance is not None:
        return _pg_instance
    with _pg_lock:
        if _pg_instance is not None:
            return _pg_instance
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return None
        from genlab_core.storage.postgres import PostgresBackend

        _pg_instance = PostgresBackend(dsn=dsn)
        return _pg_instance


def log_click(
    product_id: str,
    niche_id: str,
    network: str,
    affiliate_url: str,
    referrer: str = "",
    country: str = "",
    platform_source: str = "",
    blueprint_id: str = "",
    channel_id: str = "",
    commission_pct: float | None = None,
) -> None:
    """Log an affiliate click to the database.

    Args:
        product_id: Slug (e.g. "ps5-console"). Written to
            ``affiliate_clicks.product_id`` — the JOIN key with
            ``blueprints.product_slug`` in the L3 PR 8 reward wire.
        niche_id: One of the 5 niche IDs.
        network: Network name matched at click time
            (``amazon`` / ``amazon_us`` / ``earnkaro`` / ...).
        affiliate_url: The final redirect URL after UTM append.
        referrer: HTTP Referer header (used for platform detection).
        country: Detected country code (from CF-IPCountry / X-Country).
        platform_source: One of instagram/youtube/facebook/twitter/threads.
        blueprint_id: UUID of the reel that surfaced the link.
            L3 PR 7: empty string / non-UUID → None (protects the UUID
            FK column added in L3 PR 1).
        channel_id: The channel handle (e.g. "criticalrush").
        commission_pct: L3 PR 7 (2026-07-07). Commission rate at
            click time from the catalog network. Snapshotted so a
            retroactive rate change doesn't invalidate historical
            attribution. None omits the column — DB stores NULL.
    """
    try:
        pg = _get_pg()
        if pg is None:
            logger.warning("[LinkTracker] DATABASE_URL not set")
            return

        sanitized_bp = _sanitize_blueprint_id(blueprint_id)

        record = {
            "niche_id": niche_id,
            "product_id": product_id,
            "network": network,
            "affiliate_url": affiliate_url,
            "referrer": referrer,
            "country": country,
            "platform_source": platform_source,
            "channel_id": channel_id,
        }
        # Omit blueprint_id entirely when sanitized to None — the
        # column is nullable and omission is simpler than writing
        # NULL through PROMOTED_COLUMNS. Same for commission_pct.
        if sanitized_bp is not None:
            record["blueprint_id"] = sanitized_bp
        if commission_pct is not None:
            record["commission_pct"] = float(commission_pct)

        pg.create("affiliate_clicks", record)
        logger.info(
            "[LinkTracker] Click logged: %s/%s via %s bp=%s commission=%s",
            niche_id,
            product_id,
            network,
            sanitized_bp or "-",
            commission_pct if commission_pct is not None else "-",
        )
    except Exception as e:
        logger.warning("[LinkTracker] Failed to log click: %s", e)


# ── Exported test hook ─────────────────────────────────────────────
# Re-exported for pin tests that verify the UUID validation contract.
sanitize_blueprint_id = _sanitize_blueprint_id
_ = _uuid_module  # kept for future imports; currently we validate via regex
