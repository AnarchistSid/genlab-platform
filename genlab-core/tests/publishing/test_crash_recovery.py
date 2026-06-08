"""Tests for :mod:`genlab_core.publishing.crash_recovery`.

Lives next to its module home (paralleling the PR 6a/N extraction in
the publish_all_platforms decomposition).

Strategy
--------
Both recovery functions are pure I/O over the backlog client — easiest
to test by passing a MagicMock for backlog_client and asserting on the
``update`` calls it receives. We inject ``now`` explicitly so age
comparisons are deterministic (no flakiness from real clock skew).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from genlab_core.publishing.crash_recovery import (
    DEFAULT_ATTEMPT_CAP,
    DEFAULT_PUBLISH_FAILED_COOLDOWN,
    DEFAULT_PUBLISHING_STALE_AFTER,
    recover_publish_failed,
    recover_stuck_publishing,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bp(bp_id: str, *, updated_at: str | None, attempts: int = 0) -> dict:
    """Build a SharePoint-shape blueprint record."""
    fields: dict = {"publish_attempts": attempts}
    if updated_at is not None:
        fields["updated_at"] = updated_at
    return {"id": bp_id, "fields": fields}


_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# recover_stuck_publishing
# ---------------------------------------------------------------------------


class TestRecoverStuckPublishing:
    def test_stale_low_attempts_resets_to_visual_ready(self) -> None:
        old = (_NOW - timedelta(minutes=31)).isoformat()  # 31 min stale
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("abc12345", updated_at=old, attempts=1)]

        recover_stuck_publishing(bc, "gaming", now=_NOW)

        bc.get_blueprints_by_status.assert_called_once_with("PUBLISHING", niche_id="gaming")
        bc.blueprints.update.assert_called_once_with("abc12345", {"status": "VISUAL_READY"})

    def test_stale_at_attempt_cap_marks_publish_failed(self) -> None:
        old = (_NOW - timedelta(hours=2)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("def67890", updated_at=old, attempts=DEFAULT_ATTEMPT_CAP)
        ]

        recover_stuck_publishing(bc, "gaming", now=_NOW)

        bc.blueprints.update.assert_called_once_with("def67890", {"status": "PUBLISH_FAILED"})

    def test_stale_over_attempt_cap_marks_publish_failed(self) -> None:
        old = (_NOW - timedelta(hours=5)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("over9999", updated_at=old, attempts=DEFAULT_ATTEMPT_CAP + 5)
        ]
        recover_stuck_publishing(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_called_once_with("over9999", {"status": "PUBLISH_FAILED"})

    def test_not_stale_is_left_alone(self) -> None:
        """Recently-touched PUBLISHING blueprints might be live in flight —
        recovery MUST NOT mark them as failed prematurely."""
        recent = (_NOW - timedelta(minutes=5)).isoformat()  # only 5 min old
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("live1234", updated_at=recent)]
        recover_stuck_publishing(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_not_called()

    def test_missing_updated_at_skipped(self) -> None:
        """No timestamp → can't tell if stale → skip (better than guessing
        an age and acting on a junk record)."""
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("noTS0001", updated_at=None)]
        recover_stuck_publishing(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_not_called()

    def test_malformed_updated_at_skipped(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("bad00001", updated_at="not a date")]
        recover_stuck_publishing(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_not_called()

    def test_naive_datetime_treated_as_utc(self) -> None:
        """SharePoint sometimes hands us tz-naive ISO strings — they must
        be treated as UTC (not local time) so a stale-detection check
        doesn't silently misfire because of the host's timezone."""
        old_naive = (_NOW - timedelta(minutes=31)).replace(tzinfo=None).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("naive001", updated_at=old_naive)]
        recover_stuck_publishing(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_called_once_with("naive001", {"status": "VISUAL_READY"})

    def test_get_blueprints_failure_is_non_fatal(self) -> None:
        """A SharePoint query failure during recovery MUST NOT block the
        main publish — log + return, never raise."""
        bc = MagicMock()
        bc.get_blueprints_by_status.side_effect = RuntimeError("graph throttled")
        recover_stuck_publishing(bc, "gaming", now=_NOW)  # must not raise
        bc.blueprints.update.assert_not_called()

    def test_one_update_failure_doesnt_block_other_blueprints(self) -> None:
        """A transient SharePoint write error for one blueprint must NOT
        stop recovery of sibling blueprints."""
        old = (_NOW - timedelta(hours=1)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("bp_a", updated_at=old),
            _bp("bp_b", updated_at=old),
            _bp("bp_c", updated_at=old),
        ]

        def side_effect(bp_id, _patch):
            if bp_id == "bp_b":
                raise RuntimeError("transient")

        bc.blueprints.update.side_effect = side_effect

        # Must not raise.
        recover_stuck_publishing(bc, "gaming", now=_NOW)

        # All three were attempted.
        assert bc.blueprints.update.call_count == 3

    def test_passes_niche_id_to_query(self) -> None:
        """Cross-channel isolation — must scope the recovery query by niche."""
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = []
        recover_stuck_publishing(bc, "sports", now=_NOW)
        bc.get_blueprints_by_status.assert_called_once_with("PUBLISHING", niche_id="sports")

    @pytest.mark.parametrize("custom_cap", [1, 5, 10])
    def test_custom_attempt_cap_honoured(self, custom_cap: int) -> None:
        """Caller can override attempt_cap — operational tuning hook."""
        old = (_NOW - timedelta(hours=1)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("ID", updated_at=old, attempts=custom_cap)]
        recover_stuck_publishing(bc, "gaming", attempt_cap=custom_cap, now=_NOW)
        bc.blueprints.update.assert_called_once_with("ID", {"status": "PUBLISH_FAILED"})


# ---------------------------------------------------------------------------
# recover_publish_failed
# ---------------------------------------------------------------------------


class TestRecoverPublishFailed:
    def test_past_cooldown_resets_to_visual_ready_with_zero_attempts(self) -> None:
        old = (_NOW - timedelta(hours=25)).isoformat()  # 25h > 24h cooldown
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("fail0001", updated_at=old, attempts=3)]

        recover_publish_failed(bc, "gaming", now=_NOW)

        bc.get_blueprints_by_status.assert_called_once_with("PUBLISH_FAILED", niche_id="gaming")
        bc.blueprints.update.assert_called_once_with(
            "fail0001",
            {"status": "VISUAL_READY", "publish_attempts": 0},
        )

    def test_within_cooldown_is_left_alone(self) -> None:
        recent = (_NOW - timedelta(hours=3)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("cool0001", updated_at=recent)]
        recover_publish_failed(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_not_called()

    def test_missing_updated_at_skipped(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("no_ts", updated_at=None)]
        recover_publish_failed(bc, "gaming", now=_NOW)
        bc.blueprints.update.assert_not_called()

    def test_get_blueprints_failure_is_non_fatal(self) -> None:
        bc = MagicMock()
        bc.get_blueprints_by_status.side_effect = RuntimeError("graph down")
        recover_publish_failed(bc, "gaming", now=_NOW)  # must not raise
        bc.blueprints.update.assert_not_called()

    def test_one_update_failure_doesnt_block_others(self) -> None:
        old = (_NOW - timedelta(hours=25)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [
            _bp("bp_a", updated_at=old),
            _bp("bp_b", updated_at=old),
        ]

        def side_effect(bp_id, _patch):
            if bp_id == "bp_a":
                raise RuntimeError("transient")

        bc.blueprints.update.side_effect = side_effect

        recover_publish_failed(bc, "gaming", now=_NOW)

        assert bc.blueprints.update.call_count == 2

    def test_custom_cooldown_honoured(self) -> None:
        """Caller can shrink/extend the cooldown for ops tuning."""
        old = (_NOW - timedelta(minutes=20)).isoformat()
        bc = MagicMock()
        bc.get_blueprints_by_status.return_value = [_bp("tuned", updated_at=old)]
        # Short cooldown — recover after just 10 minutes.
        recover_publish_failed(bc, "gaming", cooldown=timedelta(minutes=10), now=_NOW)
        bc.blueprints.update.assert_called_once()


# ---------------------------------------------------------------------------
# Default thresholds match the historical inline values
# ---------------------------------------------------------------------------


class TestDefaultThresholds:
    """Catch an accidental change to the historical defaults — these
    appeared as literals (30 min / 24h / 3) in the orchestrator before
    the extraction, and operators have built mental models around them."""

    def test_stale_after_is_30_min(self) -> None:
        assert DEFAULT_PUBLISHING_STALE_AFTER == timedelta(minutes=30)

    def test_cooldown_is_24h(self) -> None:
        assert DEFAULT_PUBLISH_FAILED_COOLDOWN == timedelta(hours=24)

    def test_attempt_cap_is_3(self) -> None:
        assert DEFAULT_ATTEMPT_CAP == 3
