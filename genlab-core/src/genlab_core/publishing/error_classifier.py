"""Classify publish errors to drive retry behavior.

Categories:
  TRANSIENT — retry with backoff (timeout, 500, connection error)
  QUOTA — retry next day (rate limit, quota exceeded)
  CREDENTIAL — don't retry, needs human (token expired, 401)
  CONTENT — don't retry, content issue (too large, format rejected)
  MISSING_RENDER — visual files missing on disk (2026-07-01 disk cascade class).
    Distinguished from CONTENT because it is a RENDER-side failure (files
    got cleaned up, blueprint references stale paths, DR-restored bp lost
    its media) — NOT a content-format issue. Downshifted to SKIPPED in
    publishing_analytics because the loud "FAILED" signal would drown out
    genuine platform breakage, and the retry pass shouldn't waste attempts
    on a blueprint whose files simply do not exist.
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
        ]
    ],
    # 2026-07-01: split "No valid media files" from CONTENT into its own
    # class. Downstream (parallel_publish) treats this as SKIPPED (like
    # CREDENTIAL/QUOTA) instead of FAILED — the disk-cleanup-cascade
    # incident showed that FAILED-marking these publishes wasted retry
    # attempts and polluted the health dashboard for a data-side issue
    # (missing renders) that no platform recovery can fix.
    "MISSING_RENDER": [
        re.compile(p, re.I)
        for p in [
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

    Returns one of: TRANSIENT, QUOTA, CREDENTIAL, CONTENT, MISSING_RENDER,
    PERMANENT. Checks patterns in priority order
    (CREDENTIAL > QUOTA > MISSING_RENDER > CONTENT > TRANSIENT). MISSING_RENDER
    is checked before CONTENT so the specific "No valid media files" pattern
    isn't accidentally caught by CONTENT's broader "media.*not.*found" pattern.
    Falls back to TRANSIENT for unrecognized errors (prefer retry over abandon).
    """
    if not error_message:
        return "TRANSIENT"

    for category in ("CREDENTIAL", "QUOTA", "MISSING_RENDER", "CONTENT", "TRANSIENT"):
        for pattern in _PATTERNS[category]:
            if pattern.search(error_message):
                return category

    # Unrecognized error — default to TRANSIENT (prefer retry)
    return "TRANSIENT"


# Errors that occur AFTER the request may have reached the platform — the post
# might have actually landed before the response was lost. Auto-retrying these
# risks a duplicate post (R-21), so the cross-run retry pass must NOT blindly
# re-publish them; they need a "did it land" check or human review. This is a
# subset of TRANSIENT; the other transients (DNS failure, connection refused,
# SSL handshake, pre-send 50x) clearly never landed and stay safely retryable.
_AMBIGUOUS_AFTER_SEND: list[re.Pattern] = [
    re.compile(p, re.I)
    for p in [
        r"timed?\s*out",
        r"timeout",
        r"read timed out",
        r"publish timed out",
        r"broken pipe",
        r"max retries exceeded",
        r"reel.*publish.*failed",
        r"container.*(?:expired|timed?\s*out|processing.*error)",
        r"2207026",  # IG container expired (after upload)
    ]
]


def is_ambiguous_failure(error_message: str) -> bool:
    """True if the failure may have landed despite the error (timeout / broken
    pipe / container-expired). Such errors must not be blindly auto-retried —
    doing so risks a duplicate post (R-21). An empty/unknown error is treated
    as NOT ambiguous, preserving the "prefer retry" default for clear pre-send
    failures with vague messages.
    """
    if not error_message:
        return False
    return any(p.search(error_message) for p in _AMBIGUOUS_AFTER_SEND)


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
