"""Email sender for affiliate subscriber notifications.

Uses Resend (https://resend.com) for transactional email.
Set RESEND_API_KEY in .env to enable.
"""

import logging
import os

_SENDER_DOMAIN = os.environ.get("GENLAB_SENDER_DOMAIN", "localhost")

logger = logging.getLogger(__name__)

_RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")


def is_configured() -> bool:
    return bool(_RESEND_API_KEY)


def send_deal_notification(
    to_email: str,
    channel_name: str,
    product_name: str,
    product_url: str,
    discount_text: str = "",
) -> bool:
    """Send a deal notification email to a subscriber."""
    if not is_configured():
        logger.debug("Email not configured (RESEND_API_KEY not set)")
        return False

    try:
        import resend

        resend.api_key = _RESEND_API_KEY

        subject = f"Deal Alert: {product_name}" + (f" -- {discount_text}" if discount_text else "")

        html = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
            <h2 style="color: #fff; background: #09090b; padding: 16px; border-radius: 8px; text-align: center;">
                {channel_name}
            </h2>
            <div style="padding: 16px; border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 16px;">
                <h3>{product_name}</h3>
                {f'<p style="color: #ef4444; font-weight: bold;">{discount_text}</p>' if discount_text else ""}
                <a href="{product_url}" style="display: block; text-align: center; padding: 12px; background: #6366f1; color: #fff; text-decoration: none; border-radius: 6px; margin-top: 12px;">
                    Get Deal
                </a>
            </div>
            <p style="color: #6b7280; font-size: 12px; margin-top: 24px; text-align: center;">
                This email contains affiliate links. We may earn a small commission at no extra cost to you.
                <br><a href="{{unsubscribe_url}}" style="color: #6b7280;">Unsubscribe</a>
            </p>
        </div>
        """

        result = resend.Emails.send(
            {
                "from": f"{channel_name} Deals <deals@{_SENDER_DOMAIN}>",
                "to": to_email,
                "subject": subject,
                "html": html,
            }
        )
        logger.info("Email sent to %s: %s", to_email, result.get("id", "?"))
        return True
    except Exception as e:
        logger.warning("Failed to send email to %s: %s", to_email, e)
        return False


# Channel slug -> display name mapping
_CHANNEL_DISPLAY_NAMES: dict[str, str] = {
    "blackboxbrief": "BlackboxBrief",
    "criticalrush": "CriticalRush",
    "clutchwire": "ClutchWire",
    "splicereel": "SpliceReel",
    "framedrift": "FrameDrift",
}


def send_batch_deals(
    channel_slug: str,
    product_name: str,
    product_url: str,
    discount_text: str = "",
) -> int:
    """Send deal notification to all active subscribers of a channel."""
    if not is_configured():
        return 0

    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "dbname=genlab")
    sent = 0

    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT email FROM email_subscribers WHERE channel_slug = %s AND is_active = true",
                (channel_slug,),
            ).fetchall()

        channel_name = _CHANNEL_DISPLAY_NAMES.get(channel_slug, channel_slug)

        for row in rows:
            if send_deal_notification(
                row["email"], channel_name, product_name, product_url, discount_text
            ):
                sent += 1
    except Exception as e:
        logger.warning("Batch send failed: %s", e)

    return sent
