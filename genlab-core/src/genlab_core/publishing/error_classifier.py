"""Classify publish errors to drive retry behavior.

Categories:
  TRANSIENT — retry with backoff (timeout, 500, connection error)
  QUOTA — retry next day (rate limit, quota exceeded)
  CREDENTIAL — don't retry, needs human (token expired, 401)
  CONTENT — don't retry, content issue (too large, format rejected)
  PERMANENT — don't retry ever (account suspended)
"""

from __future__ import annotations

import re

_PATTERNS: dict[str, list[re.Pattern]] = {
    "CREDENTIAL": [
        re.compile(p, re.I)
        for p in [
            r"\b401\b",
            r"unauthorized",
            r"token.*(?:invalid|expired|revoked)",
            r"invalid.?grant",
            r"OAuthException",
            r"No.*credentials",
            r"token.*missing",
            r"authentication.*fail",
        ]
    ],
    "QUOTA": [
        re.compile(p, re.I)
        for p in [
            r"quota",
            r"rate.?limit",
            r"\b429\b",
            r"too many requests",
            r"daily.*limit",
            r"quota near limit",
            r"quotaExceeded",
            r"uploadLimitExceeded",
        ]
    ],
    "CONTENT": [
        re.compile(p, re.I)
        for p in [
            r"caption.*too.*long",
            r"title.*too.*long",
            r"video.*too.*(?:large|small|long|short)",
            r"media.*(?:not.*found|not.*supported)",
            r"format.*(?:not.*supported|invalid|rejected)",
            r"Video required",
            r"file.*not.*found",
            r"No media paths",
            r"media paths provided",
            r"visual_paths.*(?:empty|null|missing)",
            # Instagram-specific
            r"36000",  # Video file not suitable
            r"not suitable",
            r"publishing to a story",
            # YouTube-specific
            r"videoNotFound",
            r"processingFailure",
            # Facebook-specific
            r"\(#100\)",  # Invalid parameter
            r"param.*video_url",
            # X/Twitter-specific
            r"DuplicateContent",
            r"duplicate.*content",
            # No valid media
            r"No valid media files",
        ]
    ],
    "TRANSIENT": [
        re.compile(p, re.I)
        for p in [
            r"timed?\s*out",
            r"timeout",
            r"connection.*(?:reset|refused|error|closed)",
            r"\b50[0-3]\b",
            r"temporary",
            r"try again",
            r"CDN.*(?:unavailable|failed|upload.*failed)",
            r"Reel.*publish.*failed",
            r"Broken pipe",
            r"Read timed out",
            r"SSLError",
            r"certificate.*verify.*failed",
            r"JSONDecodeError",
            r"2207026",  # IG container expired
            r"2207076",  # IG "video file not supported" — transient Meta processor issue
            r"2207077",  # IG "media upload failed" — transient CDN/processor issue
            r"container.*(?:expired|timed?\s*out|processing.*error)",
            r"Publish timed out",
            r"NameResolutionError",  # DNS failure — transient
            r"nodename nor servname",  # macOS DNS failure
            r"Failed to resolve",  # DNS failure
            r"Max retries exceeded",  # urllib3 retry exhaustion — transient
        ]
    ],
}


def classify(error_message: str, platform: str = "") -> str:
    """Classify a publish error message.

    Returns one of: TRANSIENT, QUOTA, CREDENTIAL, CONTENT, PERMANENT.
    Checks patterns in priority order (CREDENTIAL > QUOTA > CONTENT > TRANSIENT).
    Falls back to TRANSIENT for unrecognized errors (prefer retry over abandon).
    """
    if not error_message:
        return "TRANSIENT"

    for category in ("CREDENTIAL", "QUOTA", "CONTENT", "TRANSIENT"):
        for pattern in _PATTERNS[category]:
            if pattern.search(error_message):
                return category

    # Unrecognized error — default to TRANSIENT (prefer retry)
    return "TRANSIENT"


def should_retry(error_class: str) -> bool:
    """Whether this error class should be retried."""
    return error_class in ("TRANSIENT", "QUOTA")


def retry_delay_seconds(error_class: str, attempt: int) -> int:
    """Delay before next retry attempt."""
    if error_class == "QUOTA":
        return 86400  # 24 hours
    # Exponential backoff: 10m, 1h, 4h
    delays = [600, 3600, 14400]
    return delays[min(attempt, len(delays) - 1)]
