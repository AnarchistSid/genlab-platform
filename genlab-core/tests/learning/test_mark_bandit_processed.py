"""Pins for ``PendingFeedbackStore.mark_bandit_processed`` + the
metric_collector wiring that calls it after a successful live
bandit update.

Closes the "75 positive rewards, 4 in bandit_arms" gap surfaced
on prod 2026-06-14 — the live updater path in metric_collector
didn't stamp rows it processed, so the daily backfill (which uses
the same flag to identify zombies) couldn't distinguish "already
done by live path" from "live path never ran for this row." The
contract is: every successful live bandit_updater call MUST stamp
the row, so the backfill timer can safely run with
--include-post-fix without double-updating bandit_arms.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask


@pytest.fixture
def mock_proxy():
    proxy = MagicMock()
    proxy.create.return_value = {"id": "sp_001", "fields": {}}
    proxy.all.return_value = []
    proxy.update.return_value = {"id": "sp_001", "fields": {}}
    return proxy


@pytest.fixture
def mock_client(mock_proxy):
    client = MagicMock()
    client.pending_feedback = mock_proxy
    return client


@pytest.fixture
def store(mock_client):
    return PendingFeedbackStore(mock_client)


def _make_task(**overrides) -> PendingFeedbackTask:
    defaults = {
        "content_id": "story_001",
        "platform": "instagram",
        "niche_id": "gaming",
        "published_at": datetime.now(UTC) - timedelta(hours=50),
        "platform_post_id": "ig_12345",
        "bandit_arm": "gameplay_clip",
        "bandit_context": {"hook_type": "question", "duration": "short"},
        "sharepoint_id": "sp_001",
    }
    defaults.update(overrides)
    return PendingFeedbackTask(**defaults)


class TestMarkBanditProcessed:
    def test_writes_iso_timestamp_to_extra(self, store, mock_proxy):
        """The flag key must be exactly ``bandit_backfilled_at`` (the
        same key the one-shot backfill script reads via
        ``extra->>'bandit_backfilled_at'``). Value must be an ISO
        timestamp so the backfill's WHERE clause's truthy check
        sees it. Using a DIFFERENT key name would silently break
        the contract — the backfill timer would see the row as a
        zombie and re-replay it, double-updating bandit_arms."""
        task = _make_task()
        store.mark_bandit_processed(task)

        mock_proxy.update.assert_called_once()
        record_id, fields = mock_proxy.update.call_args[0]
        assert record_id == "sp_001"
        assert "bandit_backfilled_at" in fields, (
            "Must use the same flag key the backfill script reads. "
            "Any other key (e.g. 'bandit_processed_at') breaks the "
            "double-update contract."
        )
        # Round-trip: should be parseable as an ISO timestamp
        try:
            parsed = datetime.fromisoformat(fields["bandit_backfilled_at"])
            assert parsed.tzinfo is not None, "must be timezone-aware"
        except (ValueError, TypeError) as e:
            pytest.fail(f"bandit_backfilled_at not a parseable ISO timestamp: {e}")

    def test_falls_back_to_find_when_sharepoint_id_missing(self, store, mock_proxy):
        """Some legacy rows have no sharepoint_id captured. The method
        must still try to find them by post_id formula match — same
        fallback pattern as update_window."""
        task = _make_task(sharepoint_id="")
        mock_proxy.all.return_value = [{"id": "found_001", "fields": {}}]

        store.mark_bandit_processed(task)

        mock_proxy.all.assert_called_once()
        formula = mock_proxy.all.call_args.kwargs.get("formula", "")
        assert "ig_12345" in formula, "fallback must use platform_post_id in the formula match"
        record_id, _ = mock_proxy.update.call_args[0]
        assert record_id == "found_001"

    def test_silent_when_record_id_unresolvable(self, store, mock_proxy):
        """A row with no sharepoint_id AND no match in the table:
        log + skip, never raise. We can't stamp a row that doesn't
        exist; this is a noop, not a crash."""
        task = _make_task(sharepoint_id="")
        mock_proxy.all.return_value = []  # no formula matches

        # Must not raise
        store.mark_bandit_processed(task)
        mock_proxy.update.assert_not_called()

    def test_proxy_update_failure_does_not_raise(self, store, mock_proxy):
        """The CRITICAL non-functional requirement: stamp failure MUST
        NOT propagate. The live bandit update already happened by the
        time this is called — losing the stamp risks a double-update
        on the next backfill timer (annoying), but raising here would
        undo a real bandit_arms write that already landed (worse)."""
        task = _make_task()
        mock_proxy.update.side_effect = RuntimeError("transient DB blip")

        # Must not raise
        store.mark_bandit_processed(task)


class TestStampKeySharedWithBackfillScript:
    """The pin that ties this together with the backfill script.
    If anyone renames the flag in one place, the other place's
    consumer/writer becomes silently incorrect."""

    def test_backfill_script_reads_same_key_we_write(self):
        """The backfill script's WHERE clause must filter on the same
        JSONB key we set here. Anything else means the contract is
        broken in one direction (zombies pile up) or the other
        (double-updates)."""
        from pathlib import Path

        script_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "genlab_core"
            / "scripts"
            / "backfill_bandit_from_pending_feedback.py"
        )
        src = script_path.read_text()
        # The script filters with this exact JSONB expression — pin
        # both ours and theirs to the same key.
        assert "extra->>'bandit_backfilled_at'" in src, (
            "backfill script doesn't filter on extra->>'bandit_backfilled_at' — "
            "either it changed (update the live stamp key to match) "
            "OR our writer key drifted (update both together)."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
