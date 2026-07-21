"""Tests for :mod:`genlab_core.publishing.parallel_publish`.

Lives next to its module home (paralleling the PR 6b/N extraction in
the publish_all_platforms decomposition). The orchestrator-level
``test_publish_all_platforms`` exercises this code via the
integration path; this file isolates the dispatcher's contracts.

Strategy
--------
``execute_parallel_publish`` orchestrates real ``ThreadPoolExecutor``
fan-out, so we patch every collaborator (resolve_client_kwargs,
build_payload, get_client, post_affiliate_reply, record_publish) and
assert on the ``ParallelPublishOutcome`` returned. The patches target
the call-site bindings on ``parallel_publish`` (not the source
modules) — that's the lesson from PRs 4 and 6b.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import PublishResult
from genlab_core.publishing.parallel_publish import (
    DEFAULT_PUBLISH_TIMEOUT_SECONDS,
    ParallelPublishOutcome,
    execute_parallel_publish,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _success(platform: str = "instagram", post_id: str = "p1") -> PublishResult:
    return PublishResult(
        platform=platform,
        success=True,
        post_id=post_id,
        post_url=f"https://{platform}/p/{post_id}",
    )


def _failure(platform: str = "instagram", error: str = "boom") -> PublishResult:
    return PublishResult(platform=platform, success=False, error=error)


def _stack_patches():
    """Patch every collaborator parallel_publish reaches into. Returns
    the patch objects as a dict so each test can override behaviour."""
    targets = {
        "resolve": "genlab_core.publishing.parallel_publish.resolve_client_kwargs",
        "client": "genlab_core.publishing.parallel_publish.get_client",
        "build": "genlab_core.publishing.parallel_publish.build_payload",
        "affiliate": "genlab_core.publishing.parallel_publish.post_affiliate_reply",
        "record": "genlab_core.publishing.parallel_publish.record_publish",
    }
    return {k: patch(v) for k, v in targets.items()}


def _run(**overrides) -> ParallelPublishOutcome:
    """Run execute_parallel_publish with safe defaults; overrides fill in
    per-test specifics."""
    base = {
        "platforms_to_publish": ["instagram"],
        "prior_published": set(),
        "niche_id": "gaming",
        "fields": {"hook": "h"},
        "record_id": "rec1",
        "candidate_id": "cand1",
        "backlog_client": MagicMock(),
        "daily_cap": None,
    }
    base.update(overrides)
    return execute_parallel_publish(**base)


# ---------------------------------------------------------------------------
# Happy-path single-platform publish
# ---------------------------------------------------------------------------


class TestSuccessfulPublish:
    def test_returns_outcome_with_published_status_and_post_id(self) -> None:
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"] as build,
            patches["affiliate"] as affiliate,
            patches["record"] as record,
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _success("instagram", "ig123")
            build.return_value = MagicMock()

            outcome = _run(platforms_to_publish=["instagram"])

        assert outcome.platform_status == {"instagram": "PUBLISHED"}
        assert outcome.any_success is True
        assert outcome.successful_post_ids == {"instagram": "ig123"}
        affiliate.assert_called_once()
        record.assert_called_once()
        # Analytics status is SUCCESS for successful publishes.
        assert record.call_args.kwargs["status"] == "SUCCESS"

    def test_daily_cap_recorded_on_success(self) -> None:
        cap = MagicMock()
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _success("instagram", "ig1")

            _run(platforms_to_publish=["instagram"], daily_cap=cap)

        cap.record_publish.assert_called_once_with("instagram")

    def test_mid_publish_state_persists_immediately(self) -> None:
        """Each success must write platform_publish_status to SharePoint
        BEFORE the orchestrator's final update — protects against the
        double-publish trap on a mid-publish crash."""
        bc = MagicMock()
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _success("instagram", "ig1")
            _run(platforms_to_publish=["instagram"], backlog_client=bc)
        # The per-success state update was called.
        update_calls = bc.blueprints.update.call_args_list
        assert any(
            "platform_publish_status" in (call.args[1] if len(call.args) > 1 else call.kwargs)
            for call in update_calls
        )


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestFailedPublish:
    def test_missing_credentials_returns_failed_skipped_status(self) -> None:
        """resolve_client_kwargs returns None → no platform-client call, the
        outcome's platform_status entry is FAILED, and analytics status is
        SKIPPED (credential failures don't drown out real outages)."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"] as record,
        ):
            resolve.return_value = None

            outcome = _run(platforms_to_publish=["instagram"])

        # get_client must NOT be called when creds are absent.
        get_client_mock.assert_not_called()
        assert outcome.any_success is False
        assert outcome.successful_post_ids == {}
        # Persisted status is a dict with FAILED and CREDENTIAL error class.
        ig_status = outcome.platform_status["instagram"]
        assert isinstance(ig_status, dict)
        assert ig_status["status"] == "FAILED"
        # Analytics records SKIPPED, not FAILED — distinguishes "no creds"
        # from a real platform outage.
        assert record.call_args.kwargs["status"] == "SKIPPED"

    def test_client_exception_returns_failed_outcome(self) -> None:
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"] as affiliate,
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.side_effect = RuntimeError("network")

            outcome = _run(platforms_to_publish=["instagram"])

        assert outcome.any_success is False
        assert outcome.successful_post_ids == {}
        affiliate.assert_not_called()
        # Attempt counter starts at 1 for a fresh failure.
        assert outcome.platform_status["instagram"]["attempts"] == 1


# ---------------------------------------------------------------------------
# 2026-07-21 policy-block learning loop L1 —
#   POLICY_BLOCK failures must emit a compliance_events row so RCA
#   modules have durable ground truth to learn from. Non-POLICY_BLOCK
#   failures MUST NOT emit (audit-log noise degrades learning signal).
# ---------------------------------------------------------------------------


class TestPolicyBlockAuditLog:
    """The policy-block learning loop starts at this call site — every
    POLICY_BLOCK error must land in compliance_events with the
    (blueprint content features, platform error) pair that downstream
    RCA + writer-avoid-pattern modules will train on.
    """

    def test_policy_block_writes_compliance_event(self) -> None:
        """Meta code=368 → error_classifier POLICY_BLOCK → helper writes
        a `platform_policy_block` compliance_events row carrying the
        blueprint's hook, caption, hashtag count, video_url presence,
        and the error snippet."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
            patch(
                "genlab_core.compliance.events.log_compliance_event"
            ) as log_event,
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _failure(
                "facebook",
                error=(
                    "Publish error (code=368, subcode=1404078): You're "
                    "temporarily blocked from using this feature because "
                    "you shared something that isn't allowed."
                ),
            )
            log_event.return_value = True

            _run(
                platforms_to_publish=["facebook"],
                fields={
                    "hook": "This clip breaks the internet",
                    "caption_ig": "Wild scene! Watch till end. #anime #viral #mustsee",
                    "video_url": "https://youtu.be/abc123",
                },
            )

        # Exactly one write, correct event_type + decision + platform.
        log_event.assert_called_once()
        kwargs = log_event.call_args.kwargs
        assert kwargs["event_type"] == "platform_policy_block"
        assert kwargs["decision"] == "block"
        assert kwargs["platform"] == "facebook"
        assert kwargs["niche_id"] == "gaming"
        # Content features RCA will train on — all present + truncated.
        md = kwargs["metadata"]
        assert md["hook"] == "This clip breaks the internet"
        assert "Wild scene" in md["caption_fragment"]
        assert md["hashtag_count"] == 3
        assert md["has_video_url"] is True
        assert "code=368" in md["error_snippet"]

    def test_non_policy_block_failure_does_not_write(self) -> None:
        """TRANSIENT / CREDENTIAL / QUOTA failures MUST NOT emit a
        policy-block audit row — the learning signal degrades if noise
        floods the table. Only genuine platform policy blocks land."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
            patch(
                "genlab_core.compliance.events.log_compliance_event"
            ) as log_event,
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _failure(
                "instagram",
                error="Read timed out",  # TRANSIENT
            )

            _run(platforms_to_publish=["instagram"])

        log_event.assert_not_called()

    def test_audit_write_failure_never_breaks_publish(self) -> None:
        """Rule: audit-log write is observability, not enforcement.
        A raise from log_compliance_event MUST be swallowed — the
        publish state machine already recorded FAILED, breaking it
        again with an audit exception would cascade into orchestrator
        error paths."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
            patch(
                "genlab_core.compliance.events.log_compliance_event",
                side_effect=RuntimeError("db down"),
            ),
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _failure(
                "facebook",
                error="code=368: temporarily blocked",
            )

            # Must NOT raise — the outer publish flow keeps running.
            outcome = _run(platforms_to_publish=["facebook"])

        assert outcome.any_success is False
        assert outcome.platform_status["facebook"]["error_class"] == "POLICY_BLOCK"


# ---------------------------------------------------------------------------
# R-29 prior-published pre-seed
# ---------------------------------------------------------------------------


class TestPriorPublishedPreseed:
    def test_prior_published_seeded_into_outcome(self) -> None:
        """R-29: platforms already PUBLISHED in a prior run must appear in
        the outcome's platform_status as PUBLISHED, even though they aren't
        in platforms_to_publish (caller already filtered them out). This is
        what keeps platform_publish_status carrying the full picture across
        retries."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _success("threads", "th1")

            outcome = _run(
                platforms_to_publish=["threads"],
                prior_published={"youtube", "facebook"},
            )

        # All three appear in platform_status.
        assert outcome.platform_status["youtube"] == "PUBLISHED"
        assert outcome.platform_status["facebook"] == "PUBLISHED"
        assert outcome.platform_status["threads"] == "PUBLISHED"
        # any_success tracks only THIS run's successes.
        assert outcome.any_success is True
        # But the successful_post_ids dict only carries this run's post_ids
        # (the prior run's post_ids were lost across the run boundary).
        assert outcome.successful_post_ids == {"threads": "th1"}

    def test_any_success_is_false_when_only_prior_published_no_new(self) -> None:
        """``any_success`` deliberately tracks only THIS run — a still-
        partial blueprint with a prior success and zero new ones must NOT
        flip the orchestrator's any_success branch (it would prematurely
        mark PUBLISHED and skip the next retry)."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.return_value.publish.return_value = _failure("threads", "boom")

            outcome = _run(
                platforms_to_publish=["threads"],
                prior_published={"youtube"},
            )

        # youtube is PUBLISHED (seeded), threads is FAILED, no new successes.
        assert outcome.platform_status["youtube"] == "PUBLISHED"
        assert outcome.platform_status["threads"]["status"] == "FAILED"
        assert outcome.any_success is False


# ---------------------------------------------------------------------------
# Multi-platform fan-out
# ---------------------------------------------------------------------------


class TestMultiPlatformFanOut:
    def test_three_platforms_all_succeed(self) -> None:
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"] as record,
        ):
            resolve.return_value = {"access_token": "tok"}
            get_client_mock.side_effect = lambda registry_id, **kw: MagicMock(
                publish=lambda payload: _success(registry_id, f"p_{registry_id}")
            )

            outcome = _run(platforms_to_publish=["instagram", "youtube", "facebook"])

        assert outcome.any_success is True
        assert set(outcome.platform_status.keys()) == {"instagram", "youtube", "facebook"}
        assert all(s == "PUBLISHED" for s in outcome.platform_status.values())
        assert len(outcome.successful_post_ids) == 3
        # Each platform got an analytics record.
        assert record.call_count == 3

    def test_partial_failure_keeps_successes(self) -> None:
        """When one platform fails and others succeed, the outcome carries
        both the success and the failure detail — this is what lets the
        orchestrator return to VISUAL_READY and retry only the failed one."""
        patches = _stack_patches()
        with (
            patches["resolve"] as resolve,
            patches["client"] as get_client_mock,
            patches["build"],
            patches["affiliate"],
            patches["record"],
        ):
            resolve.return_value = {"access_token": "tok"}

            def factory(registry_id, **kw):
                client = MagicMock()
                if registry_id == "facebook":
                    client.publish.return_value = _failure("facebook", "fb down")
                else:
                    client.publish.return_value = _success(registry_id, f"p_{registry_id}")
                return client

            get_client_mock.side_effect = factory

            outcome = _run(platforms_to_publish=["instagram", "facebook"])

        assert outcome.any_success is True
        assert outcome.platform_status["instagram"] == "PUBLISHED"
        assert outcome.platform_status["facebook"]["status"] == "FAILED"
        assert "instagram" in outcome.successful_post_ids
        assert "facebook" not in outcome.successful_post_ids


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEmptyEdgeCases:
    def test_empty_platforms_to_publish_returns_empty_outcome(self) -> None:
        """ThreadPoolExecutor requires max_workers >= 1, so a caller MUST
        not pass an empty platforms_to_publish list. This test guards the
        boundary: a future refactor that accidentally calls execute_parallel_
        publish with [] gets a clear failure, not a hang."""
        with pytest.raises(ValueError):
            _run(platforms_to_publish=[])

    def test_prior_published_only_seeds_outcome_without_dispatch(self) -> None:
        """Only prior_published + empty platforms_to_publish would also raise.
        Tested via the previous case; this confirms that even with prior
        successes, dispatch still requires at least one platform.

        This guards the caller's responsibility: filter ``platforms_to_publish``
        down to NOT-yet-published platforms BEFORE invoking execute_parallel_
        publish (the orchestrator already does this in step 5)."""
        with pytest.raises(ValueError):
            _run(platforms_to_publish=[], prior_published={"youtube"})


# ---------------------------------------------------------------------------
# Default timeout
# ---------------------------------------------------------------------------


class TestDefaultTimeout:
    def test_default_is_900s(self) -> None:
        """2026-07-17 (audit round 4): bumped 600 → 900 to give IG's
        `_TOTAL_PUBLISH_BUDGET_SECONDS=420` a 480s slippage margin
        (previously 60s). Prior state: Meta's async container-polling
        occasionally exceeded 540s → executor killed IG mid-poll at 600s
        → TimeoutError → ambiguous mark → permanently skipped by
        retry_pass.py:177. 24/24 IG publishes over 30d failed this way.

        MUST stay coupled with instagram._TOTAL_PUBLISH_BUDGET_SECONDS —
        the invariant is `executor_timeout > ig_budget + 60s margin`.
        See strategic-analysis-2026-07-17-growth-targets memory.
        """
        assert DEFAULT_PUBLISH_TIMEOUT_SECONDS == 900, (
            "Executor timeout must give IG's TOTAL_PUBLISH_BUDGET_SECONDS "
            "a substantial slippage margin. If reducing, first read the "
            "audit-round-4 memo on the permanent-fail loop this fixed."
        )

    def test_executor_timeout_exceeds_ig_budget_with_margin(self) -> None:
        """Enforce the coupling: executor timeout ≥ IG budget + 60s.
        This is the load-bearing invariant that prevents the ambiguous-
        skip permanent-fail loop from returning."""
        from genlab_core.platforms.instagram import _TOTAL_PUBLISH_BUDGET_SECONDS

        margin = DEFAULT_PUBLISH_TIMEOUT_SECONDS - _TOTAL_PUBLISH_BUDGET_SECONDS
        assert margin >= 60, (
            f"executor_timeout={DEFAULT_PUBLISH_TIMEOUT_SECONDS}s minus "
            f"IG budget={_TOTAL_PUBLISH_BUDGET_SECONDS}s = {margin}s margin. "
            "Must be ≥60s or IG's slow-poll cases get killed mid-poll → "
            "ambiguous → permanently skipped. See audit round 4."
        )
