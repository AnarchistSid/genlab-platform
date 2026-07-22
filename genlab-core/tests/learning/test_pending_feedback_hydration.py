"""Pins for ``PendingFeedbackStore._from_sharepoint_item`` hydration.

Closes a silent-skip bug that ran in production for months: the
weekly config_updater service read 619 completed feedback records
and then dropped all of them because ``reward_48h`` was never read
out of the row. Every consumer that filtered on ``r.reward_48h is
not None`` got an empty list, including:

  * config_updater (the user-visible symptom — config_updates table
    has been empty since the table existed)
  * The dry-run probe in backfill_bandit_from_pending_feedback
  * Any future consumer that assumes the dataclass field reflects
    the underlying row

These pins lock in the contract that EVERY persisted column on the
DB shape must hydrate back into the Pydantic model — adding new
fields to PendingFeedbackTask without wiring them into
``_from_sharepoint_item`` is the regression shape we're guarding
against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore


class TestRewardHydration:
    def test_reward_48h_from_snake_case_field(self):
        """Postgres column name is ``reward_48h``. Must hydrate."""
        item = {
            "id": "row1",
            "fields": {
                "post_id": "yt:ABC",
                "platform": "youtube",
                "niche_id": "gaming",
                "collection_status": "complete",
                "publish_time": datetime.now(UTC) - timedelta(hours=72),
                "reward_48h": 0.413,
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h == pytest.approx(0.413)

    def test_reward_48h_from_camelcase_field(self):
        """SharePoint variant uses ``Reward48h``. Must hydrate."""
        item = {
            "id": "row2",
            "fields": {
                "PostID": "ig:1234",
                "Platform": "instagram",
                "NicheId": "anime",
                "Status": "complete",
                "PublishedAt": datetime.now(UTC) - timedelta(hours=72),
                "Reward48h": 0.142,
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h == pytest.approx(0.142)

    def test_reward_48h_none_when_absent(self):
        """An awaiting-window row legitimately has no reward yet —
        absence must hydrate to None, not to 0.0 (which would skew
        downstream averages by counting unobserved rows as zero-reward
        observations)."""
        item = {
            "id": "row3",
            "fields": {
                "post_id": "yt:XYZ",
                "platform": "youtube",
                "niche_id": "gaming",
                "collection_status": "awaiting_24h",
                "publish_time": datetime.now(UTC) - timedelta(hours=12),
                # no reward_48h key at all
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h is None

    def test_reward_48h_zero_is_legitimate(self):
        """A zero reward IS a meaningful observation (post bombed) —
        it must hydrate to 0.0 (truthy via ``is not None``), not be
        confused with the unobserved-None case."""
        item = {
            "id": "row4",
            "fields": {
                "post_id": "yt:Z00",
                "platform": "youtube",
                "niche_id": "gaming",
                "collection_status": "complete",
                "publish_time": datetime.now(UTC) - timedelta(hours=72),
                "reward_48h": 0.0,
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h == 0.0
        assert task.reward_48h is not None, (
            "0.0 must round-trip as a real value — the config_updater's "
            "``r.reward_48h is not None`` filter would drop legitimate "
            "early-stop / zero-engagement rows otherwise."
        )

    def test_reward_48h_string_value_coerces(self):
        """Some legacy SharePoint rows may have the value as a string."""
        item = {
            "id": "row5",
            "fields": {
                "post_id": "ig:STR",
                "platform": "instagram",
                "niche_id": "sports",
                "collection_status": "complete",
                "publish_time": datetime.now(UTC) - timedelta(hours=72),
                "reward_48h": "0.275",
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h == pytest.approx(0.275)

    def test_reward_48h_unparseable_string_becomes_none(self):
        """A junk string MUST NOT crash hydration — degrade to None
        and let the row be skipped as if unmeasured. The audit pattern
        we're following is: never let bad data tank the whole batch."""
        item = {
            "id": "row6",
            "fields": {
                "post_id": "yt:JUNK",
                "platform": "youtube",
                "niche_id": "movies",
                "collection_status": "complete",
                "publish_time": datetime.now(UTC) - timedelta(hours=72),
                "reward_48h": "not-a-number",
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.reward_48h is None


class TestConfigUpdaterFilterRoundTrip:
    """End-to-end: the config_updater's two compounded filters
    (``reward_48h is not None`` AND ``"48h" in completed_windows``)
    must accept a complete-status row hydrated from a typical
    Postgres ``proxy.all`` response. The bug we're pinning against
    was these filters rejecting EVERY production row for months."""

    def test_complete_row_passes_both_config_updater_filters(self):
        item = {
            "id": "row7",
            "fields": {
                "post_id": "yt:HAPPY",
                "platform": "youtube",
                "niche_id": "gaming",
                "collection_status": "complete",
                "publish_time": datetime.now(UTC) - timedelta(hours=72),
                "reward_48h": 0.18,
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        # Both filters that gate config_updater MUST accept this row
        assert task.reward_48h is not None
        assert "48h" in task.completed_windows


class TestContentIdRoundTrip:
    """Pin the 2026-07-22 uuid5-seed fix: `_from_sharepoint_item` must
    hydrate ``content_id`` from the candidate_id embedded in ``task_id``
    (shape ``{candidate_id}__{platform}``), NOT from ``post_id`` (the
    platform post ID string).

    Prior bug (production 2026-06→07): read set ``content_id=post_id``
    while write set ``content_id=candidate_id``. Effect on downstream:
    ``metric_collector.record_engagement_window`` used
    ``task_record.content_id`` as the ``blueprint_id`` seed for the
    ``post_decision_trace`` UPSERT. The uuid5 derived from ``post_id``
    (like ``"facebook:433310..."``) never collided with the uuid5 derived
    from ``candidate_id`` (SHA256 hex) that ``push_to_backlog`` wrote via
    ``record_bandit_pick``. Result: 74/181 trace rows in the 30-day
    window ended up all-NULL — the metric collector wrote before metrics
    existed, using an orphaned uuid5 seed no bandit-pick row would ever
    match. bandit_arm_id fill rate stayed at 16-31% masked by this
    disjoint-row bloat.

    These pins lock the round-trip so a future refactor of ``task_id``
    encoding surfaces the shape change before it silently reintroduces
    the orphan-row class-of-bug.
    """

    def test_content_id_hydrates_from_task_id_candidate_prefix(self):
        """The exact fix: task_id split on ``__`` returns candidate_id."""
        sha256_candidate = "4dc42ae0f91aad4d1817292c1c28eeb198c963985b98c740bba4ad10388128bd"
        item = {
            "id": "row-ci-1",
            "fields": {
                "task_id": f"{sha256_candidate}__facebook",
                "post_id": "facebook:4333104963605624",
                "platform": "facebook",
                "niche_id": "gaming",
                "collection_status": "awaiting_24h",
                "publish_time": datetime.now(UTC) - timedelta(hours=6),
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.content_id == sha256_candidate, (
            "content_id must be candidate_id (uuid5 seed for trace-row "
            "collision), not post_id"
        )

    def test_content_id_falls_back_to_post_id_when_task_id_malformed(self):
        """Defensive: legacy rows without the ``__`` separator degrade to
        post_id (matches prior behavior for those rows — they were already
        orphaned; the fix doesn't regress them)."""
        item = {
            "id": "row-ci-2",
            "fields": {
                "task_id": "legacy_no_separator",
                "post_id": "yt:LEGACY_ID",
                "platform": "youtube",
                "niche_id": "gaming",
                "collection_status": "awaiting_6h",
                "publish_time": datetime.now(UTC) - timedelta(hours=2),
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.content_id == "yt:LEGACY_ID"

    def test_content_id_uuid5_matches_push_to_backlog_seed(self):
        """The load-bearing invariant: uuid5(namespace, content_id) MUST
        equal what push_to_backlog would compute for the same candidate_id.
        If this fails, the trace-table ON CONFLICT (blueprint_id) will
        never merge push-decisions with metric-collector-engagement writes,
        and the orphan-row class-of-bug resurfaces."""
        import uuid as _uuid

        sha256_candidate = "02fcbe5ffcfb7e4bc735f890240630c6808d109002a4bfb7105997c4cf557fc1"
        ns = _uuid.UUID("6f8b3e3d-4b7f-4c9c-8e3f-c1b3e5d1a4b2")  # matches _GENLAB_NAMESPACE_UUID
        expected_uuid5 = str(_uuid.uuid5(ns, sha256_candidate))

        item = {
            "id": "row-ci-4",
            "fields": {
                "task_id": f"{sha256_candidate}__instagram",
                "post_id": "ig:reelXYZ",
                "platform": "instagram",
                "niche_id": "ai_creators",
                "collection_status": "awaiting_24h",
                "publish_time": datetime.now(UTC) - timedelta(hours=6),
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)

        # Compute the uuid5 that record_engagement_window WOULD write with
        # after the fix — it must equal what push_to_backlog wrote with.
        actual_uuid5 = str(_uuid.uuid5(ns, task.content_id))
        assert actual_uuid5 == expected_uuid5, (
            "content_id uuid5 seed must round-trip to the candidate_id-based "
            f"uuid5 push_to_backlog uses (expected={expected_uuid5}, "
            f"got={actual_uuid5} from content_id={task.content_id!r})"
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
