"""PR D (2026-06-27) — quota-guard analytics classification pins.

The YouTube quota gate at ``platforms/youtube.py:399-416`` returns
``PublishResult(success=False, error="YouTube quota at hard-stop (...)")``
when ``can_afford("upload")`` is False. That's a deliberate restraint,
not a failure — the gate exists to keep the 10K daily quota intact
across multi-niche runs.

Before this PR, ``parallel_publish`` wrote those as status='FAILED'
to publishing_analytics. Prod evidence (2026-06-27): 13 of 26 YouTube
"failures" in the last 60 days were quota-guards mislabeled as FAILED,
inflating the failure rate and burying real outages in the same way
the existing CREDENTIAL→SKIPPED handling already protects against.

This file pins:
  1. Quota errors now write SKIPPED (the new behavior).
  2. Credential errors STILL write SKIPPED (regression pin).
  3. Other failure classes (TRANSIENT, CONTENT) STILL write FAILED.
  4. The classifier itself correctly tags quota-shaped messages as QUOTA.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.platforms.models import PublishResult
from genlab_core.publishing.error_classifier import classify
from genlab_core.publishing.parallel_publish import execute_parallel_publish

# ── Helpers ───────────────────────────────────────────────────────


def _stack_patches():
    """Patch every collaborator parallel_publish reaches into.

    Patterned after test_parallel_publish.py — the patches target the
    call-site bindings on ``parallel_publish`` (not the source modules).
    """
    targets = {
        "resolve": "genlab_core.publishing.parallel_publish.resolve_client_kwargs",
        "client": "genlab_core.publishing.parallel_publish.get_client",
        "build": "genlab_core.publishing.parallel_publish.build_payload",
        "affiliate": "genlab_core.publishing.parallel_publish.post_affiliate_reply",
        "record": "genlab_core.publishing.parallel_publish.record_publish",
    }
    return {k: patch(v) for k, v in targets.items()}


def _run_with_error(error_message: str, platform: str = "youtube"):
    """Run execute_parallel_publish where the platform client returns a
    failing PublishResult with the given error message. Returns the
    ``record_publish`` mock so the test can assert on its call kwargs."""
    patches = _stack_patches()
    with (
        patches["resolve"] as resolve,
        patches["client"] as get_client_mock,
        patches["build"],
        patches["affiliate"],
        patches["record"] as record,
    ):
        resolve.return_value = {"access_token": "tok"}
        get_client_mock.return_value.publish.return_value = PublishResult(
            platform=platform,
            success=False,
            error=error_message,
        )

        execute_parallel_publish(
            platforms_to_publish=[platform],
            prior_published=set(),
            niche_id="anime",
            fields={"hook": "h"},
            record_id="rec1",
            candidate_id="cand1",
            backlog_client=MagicMock(),
            daily_cap=None,
        )

    return record


# ── Behavior pins ──────────────────────────────────────────────────


def test_quota_error_writes_skipped_not_failed() -> None:
    """The flagship fix: a quota-guard error from the YouTube client now
    writes SKIPPED to publishing_analytics, not FAILED.

    The exact error string is the one produced by ``platforms/youtube.py``
    when ``can_afford("upload")`` returns False — verbatim so the regex
    in error_classifier.py matches the real prod payload.
    """
    record = _run_with_error(
        "YouTube quota at hard-stop (9750/9800 units, 99.5%). "
        "Next upload would exceed budget — wait for Pacific midnight reset.",
        platform="youtube",
    )
    assert record.call_args.kwargs["status"] == "SKIPPED", (
        "Quota-guard errors must write SKIPPED, not FAILED — they're "
        "deliberate budget protection, not real failures."
    )


def test_credential_error_still_skipped() -> None:
    """Regression pin: extending the SKIPPED branch to include QUOTA
    must NOT break the existing CREDENTIAL→SKIPPED behavior. If this
    breaks, the long-standing "no creds doesn't drown out real outages"
    invariant is broken too.
    """
    record = _run_with_error(
        "401 Unauthorized: OAuth token expired",
        platform="youtube",
    )
    assert record.call_args.kwargs["status"] == "SKIPPED"


def test_other_failure_still_failed() -> None:
    """TRANSIENT and CONTENT errors must stay FAILED — they are real
    platform-level outages or genuine bad-payload events that the
    operator needs to see in the dashboard's failure stats.
    """
    # TRANSIENT — network timeout
    record = _run_with_error(
        "Read timed out after 30 seconds",
        platform="youtube",
    )
    assert record.call_args.kwargs["status"] == "FAILED", (
        "TRANSIENT errors are real outages — must stay FAILED."
    )

    # CONTENT — payload-shape rejection
    record = _run_with_error(
        "title too long (max 100 chars)",
        platform="youtube",
    )
    assert record.call_args.kwargs["status"] == "FAILED", (
        "CONTENT errors are genuine bad-payload events — must stay FAILED."
    )


def test_classifier_tags_quota_correctly() -> None:
    """Direct test of error_classifier.classify() for the quota-shaped
    messages that this PR depends on. If the classifier ever stops
    tagging these as QUOTA, the analytics_status would silently drop
    back to FAILED — pin the contract.
    """
    # Prod-exact YouTube quota-gate message.
    assert (
        classify(
            "YouTube quota at hard-stop (9750/9800 units, 99.5%)",
            platform="youtube",
        )
        == "QUOTA"
    )

    # Google API-style quota error.
    assert classify("quotaExceeded: daily upload limit", platform="youtube") == "QUOTA"

    # Generic rate-limit.
    assert classify("429 Too Many Requests", platform="instagram") == "QUOTA"

    # Upload-specific quota.
    assert classify("uploadLimitExceeded for project", platform="youtube") == "QUOTA"
