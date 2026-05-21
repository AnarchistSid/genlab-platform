"""Tests for genlab_core.publishing.error_classifier."""
from __future__ import annotations

from genlab_core.publishing.error_classifier import (
    classify,
    retry_delay_seconds,
    should_retry,
)

# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

def test_classify_timeout():
    assert classify("Read timed out after 30s") == "TRANSIENT"


def test_classify_timeout_variant():
    assert classify("Request timeout connecting to Instagram") == "TRANSIENT"


def test_classify_401():
    assert classify("HTTP 401 Unauthorized") == "CREDENTIAL"


def test_classify_unauthorized():
    assert classify("unauthorized: token missing") == "CREDENTIAL"


def test_classify_token_expired():
    assert classify("OAuthException: token expired") == "CREDENTIAL"


def test_classify_quota():
    assert classify("quota exceeded for this account") == "QUOTA"


def test_classify_rate_limit():
    assert classify("rate limit reached, retry later") == "QUOTA"


def test_classify_429():
    assert classify("429 Too Many Requests") == "QUOTA"


def test_classify_file_not_found():
    assert classify("file not found: /tmp/video.mp4") == "CONTENT"


def test_classify_video_too_large():
    assert classify("video too large for upload") == "CONTENT"


def test_classify_format_rejected():
    assert classify("format not supported by platform") == "CONTENT"


def test_classify_caption_too_long():
    assert classify("caption too long (max 2200 chars)") == "CONTENT"


def test_classify_500():
    assert classify("Internal Server Error 500") == "TRANSIENT"


def test_classify_connection_reset():
    assert classify("connection reset by peer") == "TRANSIENT"


def test_classify_empty_string():
    assert classify("") == "TRANSIENT"


def test_classify_none_like_empty():
    # Explicitly pass empty string — should be TRANSIENT default
    assert classify("") == "TRANSIENT"


def test_classify_unknown():
    # Unrecognized error falls back to TRANSIENT
    assert classify("some totally unknown platform error XYZ-9999") == "TRANSIENT"


def test_classify_cdn_failed():
    assert classify("CDN upload failed after 3 retries") == "TRANSIENT"


def test_classify_reel_publish_failed():
    assert classify("Reel publish failed, please try again") == "TRANSIENT"


def test_classify_broken_pipe():
    assert classify("Broken pipe during upload") == "TRANSIENT"


# ---------------------------------------------------------------------------
# should_retry()
# ---------------------------------------------------------------------------

def test_should_retry_transient():
    assert should_retry("TRANSIENT") is True


def test_should_retry_quota():
    assert should_retry("QUOTA") is True


def test_should_retry_credential():
    assert should_retry("CREDENTIAL") is False


def test_should_retry_content():
    assert should_retry("CONTENT") is False


def test_should_retry_permanent():
    assert should_retry("PERMANENT") is False


# ---------------------------------------------------------------------------
# retry_delay_seconds()
# ---------------------------------------------------------------------------

def test_retry_delay_quota():
    assert retry_delay_seconds("QUOTA", 0) == 86400


def test_retry_delay_quota_any_attempt():
    # QUOTA always returns 24h regardless of attempt
    assert retry_delay_seconds("QUOTA", 5) == 86400


def test_retry_delay_exponential_attempt_0():
    assert retry_delay_seconds("TRANSIENT", 0) == 3600


def test_retry_delay_exponential_attempt_1():
    assert retry_delay_seconds("TRANSIENT", 1) == 14400


def test_retry_delay_exponential_attempt_2():
    assert retry_delay_seconds("TRANSIENT", 2) == 43200


def test_retry_delay_exponential_clamps_at_max():
    # Beyond index 2, should clamp to max delay
    assert retry_delay_seconds("TRANSIENT", 99) == 43200


# ---------------------------------------------------------------------------
# Priority ordering — CREDENTIAL beats QUOTA beats CONTENT beats TRANSIENT
# ---------------------------------------------------------------------------

def test_priority_credential_over_transient():
    # "401" matches CREDENTIAL; "timeout" matches TRANSIENT — CREDENTIAL wins
    assert classify("401 timeout error") == "CREDENTIAL"


def test_priority_quota_over_transient():
    # "quota" matches QUOTA; "500" matches TRANSIENT — QUOTA wins
    assert classify("quota exceeded 500 error") == "QUOTA"


def test_priority_content_over_transient():
    # "file not found" matches CONTENT; "try again" matches TRANSIENT — CONTENT wins
    assert classify("file not found, try again") == "CONTENT"
