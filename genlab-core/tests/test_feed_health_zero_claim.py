"""2026-06-14 zero-claim-rate auto-disable for shared ingestion.

The 2026-06-14 audit found 119 sources with 0% claim rate over 14 days
(5,769 wasted fetches; ~574/day). Pattern: text-only RSS + Reddit
listings have no video; the video-first pipeline can never claim them.

Fix: extend the existing _FeedHealthTracker (URL-keyed, 5-strike
disable for fetch failures) with a parallel name-keyed mechanism that
refreshes from a SQL query at pipeline-startup and short-circuits the
fetch in _fetch_single_feed when the source_name is in the
zero-claim-rate set.

These tests pin the contract without requiring a live Postgres: we
mock psycopg.connect and assert on the SQL shape, the set population,
and the fetch-loop integration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.pipeline.shared_ingestion import _FeedHealthTracker


def _mock_psycopg(rows: list[tuple[str]]):
    """psycopg.connect mock whose cursor returns the given rows for one
    fetchall call. Mirrors the connection-as-context-manager + cursor-
    as-context-manager pattern used by refresh_zero_claim_disables."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda self, *a: None

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *a: None
    return conn, cur


# ────────────────────────────────────────────────────────────────────────────
# refresh_zero_claim_disables — populates the disable set from a SQL query
# ────────────────────────────────────────────────────────────────────────────


def test_refresh_populates_zero_claim_set_from_db():
    """After refresh, source_names returned by the SQL query are
    flagged as disabled-by-claim-rate."""
    tracker = _FeedHealthTracker()
    conn, _ = _mock_psycopg(
        [
            ("Push Square",),
            ("Nintendo Life",),
            ("ComicBook.com",),
        ]
    )

    with patch("psycopg.connect", return_value=conn):
        count = tracker.refresh_zero_claim_disables("postgresql://x")

    assert count == 3
    assert tracker.is_disabled_by_claim_rate("Push Square")
    assert tracker.is_disabled_by_claim_rate("Nintendo Life")
    assert tracker.is_disabled_by_claim_rate("ComicBook.com")
    # Not in the set → not disabled. (Pins the "self-managing" property:
    # a source that's NOT in the SQL result set is treated as active.)
    assert not tracker.is_disabled_by_claim_rate("IGN")


def test_refresh_sql_uses_configurable_days_and_min_fetched():
    """SQL params must be plumbed through — defaults are 14/10, but a
    caller (tests, a future cron job) may want a different window."""
    tracker = _FeedHealthTracker()
    conn, cur = _mock_psycopg([])

    with patch("psycopg.connect", return_value=conn):
        tracker.refresh_zero_claim_disables("postgresql://x", days=7, min_fetched=25)

    # Post-Tier-5 (PR #311, 2026-06-17): the pg_connect shim adds a
    # ``SET app.niche_id`` cursor call before the caller's SQL. Find
    # the SELECT by content rather than fixed index so the pin
    # survives future shim setup-call additions.
    select_calls = [
        c
        for c in cur.execute.call_args_list
        if "content_pool" in c[0][0] or "source_name" in c[0][0]
    ]
    assert select_calls, "expected a SELECT against content_pool"
    _sql, params = select_calls[0].args
    assert params == (7, 25)


def test_refresh_rebuilds_set_each_call_so_recovered_sources_unflag():
    """Critical "self-managing" property: a source that produced 0
    claims at one refresh but later produces a claim must be removed
    from the disable set on the next refresh — not stuck forever."""
    tracker = _FeedHealthTracker()

    # First refresh: Push Square is flagged.
    conn1, _ = _mock_psycopg([("Push Square",)])
    with patch("psycopg.connect", return_value=conn1):
        tracker.refresh_zero_claim_disables("postgresql://x")
    assert tracker.is_disabled_by_claim_rate("Push Square")

    # Second refresh: Push Square produced something, no longer in
    # the 0%-result-set. Must be un-flagged.
    conn2, _ = _mock_psycopg([])
    with patch("psycopg.connect", return_value=conn2):
        tracker.refresh_zero_claim_disables("postgresql://x")
    assert not tracker.is_disabled_by_claim_rate("Push Square")


# ────────────────────────────────────────────────────────────────────────────
# Failure modes — must be fail-open so a DB hiccup never breaks ingestion
# ────────────────────────────────────────────────────────────────────────────


def test_refresh_with_empty_db_url_is_noop():
    """No DATABASE_URL configured (dev box, tests) — return the current
    set size and don't touch psycopg. SharedIngestion calls this on
    every run; it must not error in env-incomplete cases."""
    tracker = _FeedHealthTracker()
    assert tracker.refresh_zero_claim_disables("") == 0
    # And calling it shouldn't have populated anything.
    assert not tracker.is_disabled_by_claim_rate("anything")


def test_refresh_on_db_error_preserves_previous_set():
    """If psycopg.connect raises, we keep whatever was in the disable
    set from a prior successful refresh. Never block ingestion on a
    cache-lookup failure — the WORST case is we re-fetch a 0%-source
    for one more pipeline run, which is recoverable."""
    tracker = _FeedHealthTracker()
    # Seed the tracker with a previous successful refresh.
    conn_seed, _ = _mock_psycopg([("Push Square",)])
    with patch("psycopg.connect", return_value=conn_seed):
        tracker.refresh_zero_claim_disables("postgresql://x")
    assert tracker.is_disabled_by_claim_rate("Push Square")

    # Now: DB error. Previous set must remain intact.
    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        count = tracker.refresh_zero_claim_disables("postgresql://x")
    assert count == 1  # the previously-flagged source still in the set
    assert tracker.is_disabled_by_claim_rate("Push Square")


# ────────────────────────────────────────────────────────────────────────────
# is_disabled_by_claim_rate — name-keyed, decoupled from URL-keyed
# ────────────────────────────────────────────────────────────────────────────


def test_is_disabled_by_claim_rate_is_decoupled_from_url_disable():
    """Both disable mechanisms must coexist. A source name in the
    claim-rate set doesn't affect URL-keyed health (and vice versa)."""
    tracker = _FeedHealthTracker()
    conn, _ = _mock_psycopg([("Push Square",)])
    with patch("psycopg.connect", return_value=conn):
        tracker.refresh_zero_claim_disables("postgresql://x")

    # Claim-rate disabled, but URL is NOT in the URL-disable cache.
    assert tracker.is_disabled_by_claim_rate("Push Square")
    assert not tracker.is_disabled("https://www.pushsquare.com/feed/")

    # And the inverse — record a URL failure, verify it doesn't flag
    # the name in the claim-rate set or vice versa.
    for _ in range(5):
        tracker.record_failure("https://example.com/feed")
    assert tracker.is_disabled("https://example.com/feed")
    assert not tracker.is_disabled_by_claim_rate("https://example.com/feed")
