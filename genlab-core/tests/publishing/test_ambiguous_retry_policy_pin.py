"""Pin the 2026-07-17 ambiguous-retry policy in retry_pass.py.

## What broke pre-fix

`retry_pass._eligible_for_cross_run_retry` had a BLANKET skip on
ambiguous failures — any timeout / broken-pipe / container-expired
error was permanently un-retryable per R-21 (duplicate-post protection).

Empirical result over 30 days (audit round 4):
- 24 Instagram failures across 5 niches
- ALL had empty error strings
- ALL matched `is_ambiguous_failure` (executor TimeoutError:
  "Publish timed out after 600s" → matches `timed?\\s*out` regex)
- ALL permanently skipped
- Follower growth on IG stayed at 0-3 total over 60 days across
  5 niches → algorithmic-reach platform completely dark

## Fix contract (this test locks it)

- attempts=0, ambiguous=True  → ALLOW retry (single first-attempt bump)
- attempts=1, ambiguous=True  → SKIP permanently (bounded 1 max)
- attempts=0, ambiguous=False → ALLOW retry (unchanged)
- attempts=3, ambiguous=False → SKIP (unchanged MAX_RETRY_ATTEMPTS)

## Duplicate-post bound

Worst-case duplicate risk = 1 extra post per blueprint per platform per
lifetime. Given IG success rate was 4/28 (14%) pre-fix + real IG
follower count 0-165 across niches, this bounded risk is dramatically
less costly than the permanent-fail loop it replaces.

Phase-2 (deferred): caption-fingerprint check via /me/media before
retrying — needs new IG client methods + IG-specific retry logic.
"""

from __future__ import annotations

from genlab_core.publishing.retry_pass import _eligible_retry_platforms


def _pps(*, attempts: int, ambiguous_error: bool = False, error_class: str = "TRANSIENT") -> dict:
    """Build a synthetic platform_publish_status dict for testing."""
    last_error = "Publish timed out after 600s for instagram" if ambiguous_error else ""
    return {
        "instagram": {
            "status": "FAILED",
            "attempts": attempts,
            "last_error": last_error,
            "error_class": error_class,
            "ambiguous": ambiguous_error,
        }
    }


def test_first_ambiguous_failure_allows_retry() -> None:
    """attempts=0 + ambiguous → retry allowed (was: permanent skip).

    Regression scenario: someone re-adds blanket ambiguous-skip →
    IG permanent-fail loop returns → 24/24 failures over 30d again.
    """
    eligible = _eligible_retry_platforms(_pps(attempts=0, ambiguous_error=True), daily_cap=None)
    assert eligible == ["instagram"], (
        f"First ambiguous failure MUST allow one retry to avoid "
        f"permanent-fail loop. Got: {eligible}"
    )


def test_second_ambiguous_failure_skips_permanently() -> None:
    """attempts=1 + ambiguous → skip (bounds duplicate-post risk to 1)."""
    eligible = _eligible_retry_platforms(_pps(attempts=1, ambiguous_error=True), daily_cap=None)
    assert eligible == [], (
        "After 1 ambiguous retry, further retries must skip permanently "
        "to preserve R-21 duplicate-post protection. Got: {eligible}"
    )


def test_non_ambiguous_failure_uses_standard_retry_cap() -> None:
    """Pre-send errors (empty error string, DNS failure, connection
    refused) are NOT ambiguous — standard MAX_RETRY_ATTEMPTS applies."""
    for attempts in (0, 1, 2):
        eligible = _eligible_retry_platforms(_pps(attempts=attempts, ambiguous_error=False), daily_cap=None)
        assert eligible == ["instagram"], (
            f"Non-ambiguous failure at attempts={attempts} must retry "
            f"per MAX_RETRY_ATTEMPTS=3. Got: {eligible}"
        )


def test_non_ambiguous_at_max_retries_skips() -> None:
    """attempts=3 hits MAX_RETRY_ATTEMPTS ceiling regardless of ambiguity."""
    eligible = _eligible_retry_platforms(_pps(attempts=3, ambiguous_error=False), daily_cap=None)
    assert eligible == [], "MAX_RETRY_ATTEMPTS still caps non-ambiguous retries"


def test_credential_error_class_never_retries() -> None:
    """CREDENTIAL / QUOTA errors don't retry regardless of attempts count."""
    for ec in ("CREDENTIAL", "AUTH_EXPIRED"):
        eligible = _eligible_retry_platforms(
            _pps(attempts=0, ambiguous_error=False, error_class=ec), daily_cap=None
        )
        assert eligible == [], (
            f"error_class={ec} must not retry — needs operator intervention"
        )
