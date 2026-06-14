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


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
