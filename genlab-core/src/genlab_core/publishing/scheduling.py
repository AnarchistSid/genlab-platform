"""Scheduling utilities shared by all niche publishers.

Provides is_due() for checking schedule readiness and build_caption()
for assembling platform-safe captions with hashtags.
"""

from datetime import UTC, datetime


def is_due(
    scheduled_for: str,
    timezone_str: str = "UTC",
) -> bool:
    """Check whether a blueprint's scheduled time has arrived.

    R-82: the default for interpreting a *naive* ``scheduled_for`` is UTC — the
    same single tz authority the publish gatekeeper uses. It previously defaulted
    to Asia/Kolkata, which disagreed with the gatekeeper (and the UTC daily-cap)
    by +5:30; a naive timestamp would be judged "due" 5.5h apart by the two
    helpers. (This function is not on any live path today, but the divergence was
    a latent trap.)

    Args:
        scheduled_for: ISO 8601 datetime string (e.g. from backlog).
        timezone_str: IANA timezone for interpreting a naive ``scheduled_for``.

    Returns:
        True if the current time >= the scheduled time.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    scheduled_dt = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))

    # Ensure scheduled_dt is timezone-aware
    if scheduled_dt.tzinfo is None:
        tz = ZoneInfo(timezone_str)
        scheduled_dt = scheduled_dt.replace(tzinfo=tz)

    now = datetime.now(UTC)
    return now >= scheduled_dt


def build_caption(
    caption: str,
    hashtags: str,
    source_credit: str = "",
    max_length: int = 2200,
) -> str:
    """Combine caption text, hashtags, and optional source credit.

    Args:
        caption: Main caption body.
        hashtags: Space-separated hashtag string (e.g. "#AI #Tech").
        source_credit: Optional source attribution.
        max_length: Instagram caption character limit.

    Returns:
        Final caption string, truncated to max_length if needed.
    """
    parts = []

    if caption:
        parts.append(caption.strip())

    if hashtags and hashtags.strip():
        parts.append(hashtags.strip())

    full = "\n\n".join(parts)

    # Reserve space for source credit before truncating body
    source_suffix = ""
    if source_credit and source_credit.strip():
        source_suffix = f"\n\nSource: {source_credit.strip()}"

    body_limit = max_length - len(source_suffix)
    if len(full) > body_limit:
        # Truncate body at last space before limit to avoid splitting words/emoji
        truncated = full[:body_limit]
        last_space = truncated.rfind(" ")
        if last_space > body_limit * 0.8:  # Only if space is reasonably close
            full = truncated[:last_space]
        else:
            full = truncated

    full += source_suffix

    return full
