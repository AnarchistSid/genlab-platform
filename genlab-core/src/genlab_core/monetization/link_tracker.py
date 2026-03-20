"""Click tracker for affiliate links."""
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def log_click(
    product_id: str,
    niche_id: str,
    network: str,
    affiliate_url: str,
    referrer: str = "",
    country: str = "",
    platform_source: str = "",
) -> None:
    """Log an affiliate click to the database."""
    try:
        from genlab_core.storage.postgres import PostgresBackend
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            logger.warning("[LinkTracker] DATABASE_URL not set")
            return
        pg = PostgresBackend(dsn=dsn)
        pg.create("affiliate_clicks", {
            "niche_id": niche_id,
            "product_id": product_id,
            "network": network,
            "affiliate_url": affiliate_url,
            "referrer": referrer,
            "country": country,
            "platform_source": platform_source,
        })
        pg.close()
        logger.info("[LinkTracker] Click logged: %s/%s via %s", niche_id, product_id, network)
    except Exception as e:
        logger.warning("[LinkTracker] Failed to log click: %s", e)
