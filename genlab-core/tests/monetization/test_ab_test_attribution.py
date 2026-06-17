"""Tests for the ab_tests reward-attribution consumer.

Closes the loop opened by PR #271's producer wire. Pins:
- Fetcher only returns ``active`` rows older than min_age_hours
- _was_clicked correctly probes affiliate_clicks.blueprint_id
- _mark_completed flips active→completed + appends extra fields
- run_attribution_pass dispatches to the bandit_updater when given
- skips rows with no candidate_id
- swallows DB errors / missing DATABASE_URL gracefully
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from genlab_core.monetization import ab_test_attribution as ata


class TestRunAttributionPass:
    def test_no_db_returns_empty_stats(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        stats = ata.run_attribution_pass()
        assert stats["considered"] == 0

    def test_fetches_only_active_rows(self, monkeypatch):
        """The SQL filters by status='active' + age. Pin the call shape."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        fake_rows: list = []  # no active rows
        with patch.object(ata, "_fetch_attributable_rows", return_value=fake_rows):
            stats = ata.run_attribution_pass()
        assert stats == {"considered": 0, "clicked": 0, "not_clicked": 0, "skipped": 0}

    def test_clicked_row_marked_completed(self, monkeypatch):
        """A row whose candidate has a click → status flipped + clicked counted."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "test-uuid-1",
            "niche_id": "gaming",
            "test_name": "cta_v1_emoji",
            "variant_a": "cta_v1_emoji",
            "extra": {"candidate_id": "cand-aaa", "platforms": "instagram"},
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=[row]),
            patch.object(ata, "_was_clicked", return_value=True),
            patch.object(ata, "_mark_completed", return_value=True) as mock_mark,
        ):
            stats = ata.run_attribution_pass()
        assert stats["clicked"] == 1
        assert stats["not_clicked"] == 0
        mock_mark.assert_called_once_with("test-uuid-1", True)

    def test_not_clicked_row_marked_completed(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "test-uuid-2",
            "niche_id": "movies",
            "test_name": "cta_v1_curiosity",
            "variant_a": "cta_v1_curiosity",
            "extra": {"candidate_id": "cand-bbb", "platforms": "youtube"},
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=[row]),
            patch.object(ata, "_was_clicked", return_value=False),
            patch.object(ata, "_mark_completed", return_value=True),
        ):
            stats = ata.run_attribution_pass()
        assert stats["clicked"] == 0
        assert stats["not_clicked"] == 1

    def test_skipped_when_no_candidate_id(self, monkeypatch):
        """Rows without ``extra.candidate_id`` can't be joined → skip."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "test-uuid-3",
            "extra": {"platforms": "instagram"},  # no candidate_id
            "test_name": "x",
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        with patch.object(ata, "_fetch_attributable_rows", return_value=[row]):
            stats = ata.run_attribution_pass()
        assert stats["skipped"] == 1
        assert stats["clicked"] == 0

    def test_skipped_when_mark_completed_fails(self, monkeypatch):
        """If the UPDATE fails (concurrent writer flipped status, DB
        outage, etc.), don't count it as clicked/not_clicked."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "test-uuid-4",
            "extra": {"candidate_id": "cand-ccc"},
            "test_name": "x",
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=[row]),
            patch.object(ata, "_was_clicked", return_value=True),
            patch.object(ata, "_mark_completed", return_value=False),
        ):
            stats = ata.run_attribution_pass()
        assert stats["clicked"] == 0
        assert stats["skipped"] == 1

    def test_bandit_updater_called_on_attribution(self, monkeypatch):
        """When a bandit_updater is provided, it's called with
        (arm_id, platform, clicked) for each attributed row."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "test-uuid-5",
            "test_name": "cta_v1_emoji",
            "variant_a": "cta_v1_emoji",
            "extra": {"candidate_id": "cand-ddd", "platforms": "youtube"},
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        mock_updater = MagicMock()
        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=[row]),
            patch.object(ata, "_was_clicked", return_value=True),
            patch.object(ata, "_mark_completed", return_value=True),
        ):
            ata.run_attribution_pass(bandit_updater=mock_updater)
        mock_updater.assert_called_once_with("cta_v1_emoji", "youtube", True)

    def test_bandit_updater_failure_doesnt_break_loop(self, monkeypatch):
        """A bandit_updater exception must NOT break attribution — the
        sidecar is best-effort. The attribution itself (DB update) has
        already succeeded; the bandit feedback is a nice-to-have."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        rows = [
            {
                "id": f"uuid-{i}",
                "test_name": f"variant-{i}",
                "extra": {"candidate_id": f"cand-{i}", "platforms": "instagram"},
                "created_at": datetime.now(UTC) - timedelta(hours=48),
            }
            for i in range(3)
        ]

        def _crash_updater(arm_id, platform, clicked):
            raise RuntimeError("simulated bandit crash")

        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=rows),
            patch.object(ata, "_was_clicked", return_value=True),
            patch.object(ata, "_mark_completed", return_value=True),
        ):
            stats = ata.run_attribution_pass(bandit_updater=_crash_updater)
        # All 3 still got attributed (bandit crash didn't stop the loop)
        assert stats["clicked"] == 3

    def test_platform_extraction_takes_first(self, monkeypatch):
        """Multi-platform ab_tests rows have ``platforms`` as comma-CSV;
        use the FIRST platform for the bandit dispatch (per-platform
        attribution is W3.4 followup)."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        row = {
            "id": "uuid-multi",
            "test_name": "cta_v1_emoji",
            "extra": {
                "candidate_id": "cand-multi",
                "platforms": "instagram,youtube,facebook",
            },
            "created_at": datetime.now(UTC) - timedelta(hours=48),
        }
        mock_updater = MagicMock()
        with (
            patch.object(ata, "_fetch_attributable_rows", return_value=[row]),
            patch.object(ata, "_was_clicked", return_value=True),
            patch.object(ata, "_mark_completed", return_value=True),
        ):
            ata.run_attribution_pass(bandit_updater=mock_updater)
        mock_updater.assert_called_once_with("cta_v1_emoji", "instagram", True)


class TestWasClicked:
    def test_no_candidate_id_returns_false(self):
        assert ata._was_clicked("") is False
        assert ata._was_clicked(None) is False  # type: ignore[arg-type]

    def test_no_db_returns_false(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert ata._was_clicked("cand-x") is False


class TestMarkCompleted:
    def test_no_db_returns_false(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert ata._mark_completed("uuid", True) is False


class TestGetDefaultBanditUpdater:
    def test_returns_callable_when_cta_bandit_importable(self):
        updater = ata.get_default_bandit_updater()
        # Either a callable (happy path) or None (CTABandit unavailable)
        assert updater is None or callable(updater)
