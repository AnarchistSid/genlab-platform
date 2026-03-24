"""Click tracker for affiliate links.

Uses a module-level singleton for database connection pooling instead of
creating a new PostgresBackend per click.
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

_pg_lock = threading.Lock()
_pg_instance = None


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
) -> None:
    """Log an affiliate click to the database."""
    try:
        pg = _get_pg()
        if pg is None:
            logger.warning("[LinkTracker] DATABASE_URL not set")
            return
        pg.create("affiliate_clicks", {
            "niche_id": niche_id,
            "product_id": product_id,
            "network": network,
            "affiliate_url": affiliate_url,
            "referrer": referrer,
            "country": country,
            "platform_source": platform_source,
            "blueprint_id": blueprint_id,
            "channel_id": channel_id,
        })
        logger.info("[LinkTracker] Click logged: %s/%s via %s", niche_id, product_id, network)
    except Exception as e:
        logger.warning("[LinkTracker] Failed to log click: %s", e)
