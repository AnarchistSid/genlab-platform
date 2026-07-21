"""Tests for genlab_core.publishing.error_classifier."""

from __future__ import annotations

from genlab_core.publishing.error_classifier import (
    classify,
    is_ambiguous_failure,
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


# TRANSIENT backoff schedule is [600, 3600, 14400] = 10m, 1h, 4h
# (see retry_delay_seconds + its comment). Tests previously asserted
# the older 1h/4h/12h scale; the schedule was shortened so transient
# failures retry sooner.
def test_retry_delay_exponential_attempt_0():
    assert retry_delay_seconds("TRANSIENT", 0) == 600


def test_retry_delay_exponential_attempt_1():
    assert retry_delay_seconds("TRANSIENT", 1) == 3600


def test_retry_delay_exponential_attempt_2():
    assert retry_delay_seconds("TRANSIENT", 2) == 14400


def test_retry_delay_exponential_clamps_at_max():
    # Beyond index 2, should clamp to max delay (14400 = 4h)
    assert retry_delay_seconds("TRANSIENT", 99) == 14400


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


# ---------------------------------------------------------------------------
# is_ambiguous_failure() — R-21: failures that may have actually landed
# ---------------------------------------------------------------------------


def test_ambiguous_timeout_may_have_landed():
    # A timeout after the request was sent: the post might already exist.
    assert is_ambiguous_failure("Read timed out") is True
    assert is_ambiguous_failure("Publish timed out after 600s") is True
    assert is_ambiguous_failure("Request timeout") is True


def test_ambiguous_post_send_errors():
    assert is_ambiguous_failure("Broken pipe") is True
    assert is_ambiguous_failure("Max retries exceeded with url") is True
    assert is_ambiguous_failure("IG container 2207026 expired") is True
    assert is_ambiguous_failure("container processing error") is True
    assert is_ambiguous_failure("Reel publish failed") is True


def test_clearly_pre_send_failures_not_ambiguous():
    # These never reached the platform -> safe to auto-retry, not ambiguous.
    assert is_ambiguous_failure("connection refused") is False
    assert is_ambiguous_failure("NameResolutionError: Failed to resolve host") is False
    assert is_ambiguous_failure("SSLError: certificate verify failed") is False
    assert is_ambiguous_failure("HTTP 503 Service Unavailable") is False
    assert is_ambiguous_failure("401 unauthorized") is False


def test_empty_error_not_ambiguous():
    # Preserve the "prefer retry" default for vague/empty pre-send errors.
    assert is_ambiguous_failure("") is False


# ---------------------------------------------------------------------------
# MISSING_RENDER — 2026-07-01 disk-cleanup-cascade class
#
# The exact error string payload_builder.py raises:
#   "No valid media files for video publish (niche=movies, format=reel,
#    original_paths=['/opt/genlab/.tmp/runs/movies_20260618_082904/...'])"
# ---------------------------------------------------------------------------


def test_classify_no_valid_media_files_is_missing_render():
    """The exact payload_builder.py error string classifies as MISSING_RENDER,
    NOT CONTENT — so parallel_publish downshifts to SKIPPED not FAILED."""
    err = (
        "No valid media files for video publish (niche=movies, format=reel, "
        "original_paths=['/opt/genlab/.tmp/runs/movies_20260618_082904/visuals/..."
        "/reel.mp4'])"
    )
    assert classify(err) == "MISSING_RENDER"


def test_classify_missing_render_beats_content_pattern():
    """Even though CONTENT has 'media.*not.*found' and 'No media paths', the
    specific 'No valid media files' phrase MUST classify as MISSING_RENDER
    (priority ordering ensures this)."""
    assert classify("No valid media files") == "MISSING_RENDER"


def test_classify_priority_missing_render_over_transient():
    """A missing-render error with a timeout mention still goes MISSING_RENDER."""
    assert classify("No valid media files, connection timed out during upload") == "MISSING_RENDER"


def test_should_retry_missing_render():
    """MISSING_RENDER is NOT retryable — no platform recovery will make the
    deleted files exist. Blueprint needs re-render, not another publish attempt."""
    assert should_retry("MISSING_RENDER") is False


def test_missing_render_distinct_from_content_generic_file_not_found():
    """A generic 'file not found' from a platform (e.g. Instagram rejecting an
    upload) is still CONTENT — it's platform-side, not the disk-cleanup class.
    Only the specific 'No valid media files' phrase from payload_builder.py
    is MISSING_RENDER."""
    assert classify("file not found: /tmp/video.mp4") == "CONTENT"


# 2026-07-21 — POLICY_BLOCK class of bug (Meta code=368 temporary block).
def test_classify_policy_block_fb_code_368():
    """Pin the POLICY_BLOCK class from Meta's FB `code=368` message that
    hit 6 times in the last 14 days across 4 niches (gaming, sports,
    anime, ai_creators). Prior to elevation, this got mis-classified as
    TRANSIENT (matched 'temporarily'), causing retry-pass to burn budget
    on doomed re-attempts."""
    msg = (
        "code=368: You're temporarily blocked from using this feature "
        "because you shared something that isn't allowed on Facebook. "
        "Learn More."
    )
    assert classify(msg) == "POLICY_BLOCK"


def test_should_retry_policy_block_is_false():
    """POLICY_BLOCK must never be retried. Meta lifts the block on their
    own timeline; retrying can escalate. Operator must appeal via Meta
    Business Suite."""
    assert should_retry("POLICY_BLOCK") is False


def test_policy_block_priority_over_transient():
    """The FB code=368 message contains 'temporarily' which matches the
    TRANSIENT 'temporary' pattern. POLICY_BLOCK is checked first per
    the priority order — if this regresses, retry-pass would burn
    budget on Meta blocks."""
    assert classify("code=368: temporarily blocked") == "POLICY_BLOCK"


def test_policy_block_content_violation_variant():
    """Meta also returns 'Content violates our policies' for hard
    content strikes. Same handling — operator appeal, no retry."""
    assert classify("Content violates our policies") == "POLICY_BLOCK"
