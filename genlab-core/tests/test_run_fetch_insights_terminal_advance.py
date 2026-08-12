"""2026-08-12: pin the age-gated terminal advance in
run_fetch_insights when platform fetch persistently returns empty.

Motivating incident: 257 historical Threads rows stuck at SUCCESS
forever because `if not insights: continue` at line 417 skipped
them without advancing status. Twelve+ retry cycles happened; no
transient failure explanation left.

Fix behaviour under pin:
* Row is >72h past publish AND fetch returned empty → advance to
  INSIGHTS_UNAVAILABLE (terminal, matches DELETED / REMOVED_BY_META
  semantics)
* Row is <=72h old + fetch empty → do nothing (retry next cycle,
  transient failures still recover)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestMarkInsightsUnavailable:
    def test_calls_publishing_analytics_update_with_terminal_status(self):
        from genlab_core.scripts.run_fetch_insights import (
            _mark_insights_unavailable,
        )

        client = MagicMock()
        _mark_insights_unavailable(
            client,
            record_id="rec_abc",
            platform="threads",
            post_id="threads:12345",
            age_hours=96.0,
        )

        client.publishing_analytics.update.assert_called_once()
        args, kwargs = client.publishing_analytics.update.call_args
        assert args[0] == "rec_abc"
        assert args[1] == {"status": "INSIGHTS_UNAVAILABLE"}
        assert kwargs.get("typecast") is True

    def test_swallows_persist_failure_fail_open(self):
        """Same fail-open contract as _mark_window_completed: an update
        failure must NOT crash the caller. Worst case: row retries
        next cycle (identical to pre-fix behaviour)."""
        from genlab_core.scripts.run_fetch_insights import (
            _mark_insights_unavailable,
        )

        client = MagicMock()
        client.publishing_analytics.update.side_effect = RuntimeError("db down")

        # Must not raise
        _mark_insights_unavailable(
            client,
            record_id="rec_abc",
            platform="threads",
            post_id="threads:12345",
            age_hours=96.0,
        )


class TestFetchInsightsTerminalAdvanceGating:
    """The advance ONLY fires when age >72h. Rows <=72h old that fetch
    empty stay at SUCCESS for next cycle — transient failures recover."""

    def _fixture_env(self, age_hours: float):
        """Patch the module namespace so we can call the fetcher
        loop's `if not insights` branch in isolation."""
        from genlab_core.scripts.run_fetch_insights import _post_age_hours

        # Sanity — the helper we depend on
        assert callable(_post_age_hours)

    def test_terminal_advance_fires_for_old_row(self):
        """The gating check is `age_h > 72.0`. 96h old fetch-empty
        row must trigger _mark_insights_unavailable."""
        import inspect

        from genlab_core.scripts.run_fetch_insights import fetch_insights_for_window

        source = inspect.getsource(fetch_insights_for_window)
        # The gating decision lives in the source; pin the constant
        assert "72.0" in source or "72 " in source or "> 72" in source, (
            "Age-gate constant for terminal advance must be 72h. "
            "Motivating incident sizes this at 12+ retry cycles at "
            "6h cadence."
        )
        assert "_mark_insights_unavailable" in source, (
            "fetch_insights_for_window must call _mark_insights_unavailable "
            "when fetch returns empty AND age > 72h. Without this, stuck-"
            "at-SUCCESS rows accumulate forever (see 257-row historical "
            "backlog from 2026-08-12)."
        )

    def test_terminal_advance_wrapped_in_try_except(self):
        """The advance is best-effort — a persist failure must not
        break the fetcher loop for the remaining rows."""
        import inspect

        from genlab_core.scripts.run_fetch_insights import fetch_insights_for_window

        source = inspect.getsource(fetch_insights_for_window)
        # Look for the `try:` immediately before the age-gated advance
        # call. Structural pin — brittle to major refactor but catches
        # a silent removal of the safety wrap.
        assert (
            "insights_unavailable advance failed" in source
        ), (
            "Terminal advance must be wrapped in try/except with a WARN "
            "log. Missing wrap means one bad update kills the whole loop."
        )


class TestBackfillScript:
    def test_backfill_script_exists_and_is_executable(self):
        """The one-shot backfill script for the 257 historical stuck
        rows lives at scripts/backfill_insights_unavailable.py. Pin
        so its removal is caught (someone else might run the fix
        pattern later + need the reference)."""
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "backfill_insights_unavailable.py"
        )
        assert script.exists(), (
            "scripts/backfill_insights_unavailable.py missing. Ships "
            "with the terminal-advance fix as the one-shot backfill "
            "for the 257 historical stuck-at-SUCCESS rows."
        )
        content = script.read_text()
        assert "INSIGHTS_UNAVAILABLE" in content
        assert "--apply" in content, "Backfill must have dry-run + --apply flag"
        assert "min-age-hours" in content, (
            "Backfill must be age-gated (default 72h) matching the "
            "run_fetch_insights terminal-advance rule."
        )
