"""Tests for the canonical BlueprintStatus enum + the LIVE / LIVE_OR_PENDING
subsets.

These guard two things:

1. The enum members serialise as strings (StrEnum behaviour) — required so
   existing DB rows, YAML configs, and tests that use raw strings keep
   working unchanged.
2. The subset relationship ``LIVE ⊂ LIVE_OR_PENDING`` is the policy: anything
   live blocks at both stages; anything in-flight blocks only at push time.
   PreDownloadDedup uses LIVE; PushToBacklog uses LIVE_OR_PENDING. The class
   of bug PR #66 fixed (96 sports DRAFTED stranded by an over-broad pre-download
   block) cannot recur without this subset relationship breaking.
"""

from __future__ import annotations

from genlab_core.pipeline.blueprint_status import (
    LIVE,
    LIVE_OR_PENDING,
    BlueprintStatus,
)


class TestStrEnumBehaviour:
    """Direct string comparison + `in` checks must keep working — otherwise
    every existing caller that holds a raw ``status`` string from the DB
    would silently break."""

    def test_member_equals_its_string_value(self):
        assert BlueprintStatus.PUBLISHED == "PUBLISHED"
        assert BlueprintStatus.DRAFTED == "DRAFTED"
        assert BlueprintStatus.VISUAL_READY == "VISUAL_READY"

    def test_raw_string_membership_in_named_subset(self):
        # The classic check pattern: `row["status"] in LIVE_OR_PENDING`.
        # The row's status is a plain string out of the DB; the set holds
        # enum members. StrEnum makes this work.
        assert "PUBLISHED" in LIVE_OR_PENDING
        assert "DRAFTED" in LIVE_OR_PENDING
        assert "PUBLISHED" in LIVE
        assert "DRAFTED" not in LIVE  # the load-bearing subset semantics

    def test_member_string_passes_into_dict_payloads_unchanged(self):
        # When we write a record dict the status value must be a plain string
        # (the storage backend doesn't deserialise enums). StrEnum members
        # are str instances, so this Just Works.
        payload = {"status": BlueprintStatus.VISUAL_READY}
        assert payload["status"] == "VISUAL_READY"
        assert isinstance(payload["status"], str)


class TestSubsetRelationship:
    """The policy ``LIVE ⊂ LIVE_OR_PENDING`` is the whole point of the
    refactor — encoding the relationship in code is what makes the
    PreDownloadDedup-vs-PushToBacklog divergence intentional."""

    def test_live_is_a_proper_subset_of_live_or_pending(self):
        assert LIVE < LIVE_OR_PENDING
        assert LIVE != LIVE_OR_PENDING

    def test_live_contains_only_live_states(self):
        assert LIVE == {
            BlueprintStatus.PUBLISHED,
            BlueprintStatus.PUBLISHING,
            BlueprintStatus.VISUAL_READY,
        }

    def test_pending_states_are_in_or_pending_but_not_live(self):
        for s in (BlueprintStatus.DRAFTED, BlueprintStatus.SCORED):
            assert s in LIVE_OR_PENDING, (
                f"{s} should be in LIVE_OR_PENDING (push-time overwrite protect)"
            )
            assert s not in LIVE, (
                f"{s} must NOT be in LIVE (pre-download must allow retry — see PR #66)"
            )

    def test_terminal_non_blocking_states_are_in_neither(self):
        # ARCHIVED and PUBLISH_FAILED are explicitly not blocking — the
        # whole point of retry is to revive them. Catch any future drift
        # that adds them silently.
        for s in (BlueprintStatus.ARCHIVED, BlueprintStatus.PUBLISH_FAILED):
            assert s not in LIVE
            assert s not in LIVE_OR_PENDING


class TestStageHelpersUseTheCanonicalSets:
    """Regression guards on the two stage helpers — `_is_blocking` (push) and
    `_blocks_pre_download` (pre-download). Pre-refactor, each stage carried
    its own ``frozenset[str]`` constant; post-refactor they import from
    :mod:`blueprint_status`. The behaviour MUST be unchanged."""

    def test_push_to_backlog_blocks_on_live_or_pending_only(self):
        from genlab_core.pipeline.stages.push_to_backlog import _is_blocking

        for s in LIVE_OR_PENDING:
            assert _is_blocking({"fields": {"status": str(s)}}) is True, s
        for s in (BlueprintStatus.ARCHIVED, BlueprintStatus.PUBLISH_FAILED):
            assert _is_blocking({"fields": {"status": str(s)}}) is False, s

    def test_pre_download_blocks_on_live_only(self):
        from genlab_core.pipeline.stages.pre_download_dedup import (
            _blocks_pre_download,
        )

        for s in LIVE:
            assert _blocks_pre_download({"fields": {"status": str(s)}}) is True, s
        # Pending states must NOT block pre-download — this is the PR #66 invariant.
        for s in (BlueprintStatus.DRAFTED, BlueprintStatus.SCORED):
            assert _blocks_pre_download({"fields": {"status": str(s)}}) is False, s
        for s in (BlueprintStatus.ARCHIVED, BlueprintStatus.PUBLISH_FAILED):
            assert _blocks_pre_download({"fields": {"status": str(s)}}) is False, s

    def test_empty_or_missing_status_does_not_block(self):
        from genlab_core.pipeline.stages.pre_download_dedup import (
            _blocks_pre_download,
        )
        from genlab_core.pipeline.stages.push_to_backlog import _is_blocking

        assert _is_blocking({"fields": {"status": ""}}) is False
        assert _is_blocking({"fields": {}}) is False
        assert _blocks_pre_download({"fields": {"status": ""}}) is False
        assert _blocks_pre_download({"fields": {}}) is False
