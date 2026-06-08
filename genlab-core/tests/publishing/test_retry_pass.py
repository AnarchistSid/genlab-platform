"""Tests for :mod:`genlab_core.publishing.retry_pass`.

Lives next to its module home (paralleling the PR 6c/N extraction in
the publish_all_platforms decomposition).

Strategy
--------
``run_retry_pass`` walks PUBLISHED blueprints and conditionally retries
their failed-platform entries. We pass a MagicMock backlog_client with
pre-built blueprint records, patch the publish-side collaborators on
``retry_pass`` (resolve_client_kwargs / build_payload / get_client /
record_publish), and assert on which platforms got retried, which got
skipped, and what the persisted ``platform_publish_status`` looks like.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import PublishResult
from genlab_core.publishing.retry_pass import (
    MAX_RETRY_ATTEMPTS,
    RETRY_PASS_BATCH_LIMIT,
    _eligible_retry_platforms,
    _media_files_missing,
    _retry_not_yet_due,
    run_retry_pass,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bp(bp_id: str, pps: dict, visual_paths: list | None = None) -> dict:
    """Build a SharePoint-shape PUBLISHED blueprint."""
    fields = {"platform_publish_status": json.dumps(pps)}
    if visual_paths is not None:
        fields["visual_paths"] = json.dumps(visual_paths)
    return {"id": bp_id, "fields": fields}


def _stack_patches():
    return {
        "resolve": patch("genlab_core.publishing.retry_pass.resolve_client_kwargs"),
        "client": patch("genlab_core.publishing.retry_pass.get_client"),
        "build": patch("genlab_core.publishing.retry_pass.build_payload"),
        "record": patch("genlab_core.publishing.retry_pass.record_publish"),
    }


# ---------------------------------------------------------------------------
# _eligible_retry_platforms — pure filter logic
# ---------------------------------------------------------------------------


class TestEligibleRetryPlatforms:
    def test_failed_transient_under_cap_is_eligible(self) -> None:
        pps = {"instagram": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1}}
        assert _eligible_retry_platforms(pps, None) == ["instagram"]

    def test_published_platforms_not_returned(self) -> None:
        pps = {"instagram": "PUBLISHED", "youtube": {"status": "PUBLISHED"}}
        assert _eligible_retry_platforms(pps, None) == []

    @pytest.mark.parametrize("error_class", ["CREDENTIAL", "CONTENT", "PERMANENT"])
    def test_non_retryable_error_class_skipped(self, error_class: str) -> None:
        """Terminal error classes (need human intervention) must NOT be
        auto-retried — that would just burn the attempt budget without
        ever resolving."""
        pps = {"x": {"status": "FAILED", "error_class": error_class, "attempts": 1}}
        assert _eligible_retry_platforms(pps, None) == []

    def test_attempts_at_cap_skipped(self) -> None:
        """A failure that's already used its retry budget must NOT be
        re-attempted (bounded retry = bounded blast radius)."""
        pps = {
            "x": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": MAX_RETRY_ATTEMPTS}
        }
        assert _eligible_retry_platforms(pps, None) == []

    def test_ambiguous_flag_skipped(self) -> None:
        """R-21: a failure marked ``ambiguous`` may have actually landed.
        Auto-retrying would risk a duplicate post; skip until human or
        landing-check resolves it."""
        pps = {
            "x": {
                "status": "FAILED",
                "error_class": "TRANSIENT",
                "attempts": 1,
                "ambiguous": True,
            }
        }
        assert _eligible_retry_platforms(pps, None) == []

    def test_ambiguous_last_error_text_skipped(self) -> None:
        """Even without the explicit ``ambiguous`` flag, a ``last_error``
        text matching the ambiguous-failure heuristic (e.g. 'timeout',
        'broken pipe') is skipped — guards against the timeout-but-
        actually-published double-post case."""
        pps = {
            "x": {
                "status": "FAILED",
                "error_class": "TRANSIENT",
                "attempts": 1,
                "last_error": "Request timed out",
            }
        }
        assert _eligible_retry_platforms(pps, None) == []

    def test_future_next_retry_after_skipped(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        pps = {
            "x": {
                "status": "FAILED",
                "error_class": "TRANSIENT",
                "attempts": 1,
                "next_retry_after": future,
            }
        }
        assert _eligible_retry_platforms(pps, None) == []

    def test_past_next_retry_after_eligible(self) -> None:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        pps = {
            "x": {
                "status": "FAILED",
                "error_class": "TRANSIENT",
                "attempts": 1,
                "next_retry_after": past,
            }
        }
        assert _eligible_retry_platforms(pps, None) == ["x"]

    def test_daily_cap_blocks_retry(self) -> None:
        """The daily cap is a hard limit; retries don't bypass it."""
        pps = {"instagram": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1}}
        cap = MagicMock()
        cap.can_publish.return_value = False
        assert _eligible_retry_platforms(pps, cap) == []


# ---------------------------------------------------------------------------
# _media_files_missing
# ---------------------------------------------------------------------------


class TestMediaFilesMissing:
    def test_empty_visual_paths_returns_false(self) -> None:
        """No declared media → can't be 'missing' → don't skip."""
        assert _media_files_missing({"visual_paths": "[]"}) is False
        assert _media_files_missing({}) is False

    def test_paths_listed_but_all_missing_returns_true(self) -> None:
        assert (
            _media_files_missing(
                {"visual_paths": json.dumps(["/nonexistent/a.mp4", "/nonexistent/b.mp4"])}
            )
            is True
        )

    def test_at_least_one_existing_returns_false(self, tmp_path) -> None:
        real = tmp_path / "real.mp4"
        real.write_bytes(b"x")
        assert (
            _media_files_missing({"visual_paths": json.dumps([str(real), "/nonexistent/b.mp4"])})
            is False
        )


# ---------------------------------------------------------------------------
# _retry_not_yet_due
# ---------------------------------------------------------------------------


class TestRetryNotYetDue:
    def test_no_next_retry_returns_false(self) -> None:
        """No backoff timestamp → no waiting → due now."""
        assert _retry_not_yet_due({}) is False

    def test_future_returns_true(self) -> None:
        future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        assert _retry_not_yet_due({"next_retry_after": future}) is True

    def test_past_returns_false(self) -> None:
        past = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        assert _retry_not_yet_due({"next_retry_after": past}) is False

    def test_malformed_returns_false(self) -> None:
        """Bad timestamp → assume due (better to attempt than wait
        forever on a corrupted column)."""
        assert _retry_not_yet_due({"next_retry_after": "not a date"}) is False


# ---------------------------------------------------------------------------
# run_retry_pass — integration over the blueprint loop
# ---------------------------------------------------------------------------


def _success_result(plat: str = "instagram") -> PublishResult:
    return PublishResult(platform=plat, success=True, post_id="p1", post_url="https://x/p/1")


def _failure_result(plat: str = "instagram", error: str = "boom") -> PublishResult:
    return PublishResult(platform=plat, success=False, error=error)


class TestRunRetryPass:
    def test_no_published_blueprints_is_noop(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = []
        run_retry_pass("gaming", bc, None)
        bc.blueprints.update.assert_not_called()

    def test_blueprint_with_no_failed_platforms_is_noop(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("bp1", {"instagram": "PUBLISHED", "youtube": "PUBLISHED"})
        ]
        run_retry_pass("gaming", bc, None)
        bc.blueprints.update.assert_not_called()

    def test_retries_eligible_platform_and_persists_success(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp(
                "bp1",
                {
                    "instagram": "PUBLISHED",
                    "facebook": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1},
                },
            )
        ]
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _success_result("facebook")

            run_retry_pass("gaming", bc, None)

        # The retry should have updated platform_publish_status with FB = PUBLISHED.
        update_call = bc.blueprints.update.call_args
        persisted = json.loads(update_call.args[1]["platform_publish_status"])
        assert persisted["facebook"] == "PUBLISHED"
        assert persisted["instagram"] == "PUBLISHED"  # untouched

    def test_failed_retry_increments_attempts_with_backoff_timestamp(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp(
                "bp1",
                {"facebook": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1}},
            )
        ]
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _failure_result("facebook", "503")

            run_retry_pass("gaming", bc, None)

        persisted = json.loads(bc.blueprints.update.call_args.args[1]["platform_publish_status"])
        fb = persisted["facebook"]
        assert fb["status"] == "FAILED"
        assert fb["attempts"] == 2  # incremented from 1
        assert "next_retry_after" in fb  # backoff timestamp set

    def test_missing_creds_skips_platform_silently(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp(
                "bp1",
                {"facebook": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1}},
            )
        ]
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["record"],
        ):
            resolve.return_value = None  # no creds
            run_retry_pass("gaming", bc, None)
            get_client_mock.assert_not_called()

    def test_query_failure_swallowed(self) -> None:
        """A SharePoint hiccup during the retry pass MUST NOT block
        the orchestrator's caller — log and return."""
        bc = MagicMock()
        bc.get_blueprints_by_status.side_effect = RuntimeError("graph down")
        run_retry_pass("gaming", bc, None)  # must not raise

    def test_uses_batch_limit(self) -> None:
        """The retry scan is bounded to ``RETRY_PASS_BATCH_LIMIT`` blueprints
        per niche per run — keeps the pass cheap on niches with long
        publish histories."""
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = []
        run_retry_pass("gaming", bc, None)
        bc.get_blueprints_by_status.assert_called_once_with(
            "PUBLISHED", niche_id="gaming", max_records=RETRY_PASS_BATCH_LIMIT
        )


# ---------------------------------------------------------------------------
# Defaults guard
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_max_retry_attempts_is_3(self) -> None:
        assert MAX_RETRY_ATTEMPTS == 3

    def test_batch_limit_is_50(self) -> None:
        assert RETRY_PASS_BATCH_LIMIT == 50
