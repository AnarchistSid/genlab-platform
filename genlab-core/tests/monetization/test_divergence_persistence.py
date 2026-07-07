"""Pin the divergence persistence side (L3 PR 12a, 2026-07-07).

L3 PR 5 wired ``_log_selector_divergence`` to journalctl. This PR
adds a DB write for analyzability. Both sinks fire on the SAME
event; if the DB write fails, journalctl still records the row
and the matcher path is unaffected.

Contract:
  1. When DATABASE_URL is unset, DB write is a no-op (no exception).
  2. When conversions selector agrees with matcher, agreed=True.
  3. When they diverge, agreed=False.
  4. Slugs stored are CANONICAL (via slugify_product_name), not
     display names.
  5. Any DB exception is swallowed at DEBUG level (no propagation
     to the caller's fail-open chain).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPersistDivergence:
    """The private helper the writer invokes. Test in isolation."""

    def test_missing_database_url_is_noop(self, monkeypatch):
        """No DATABASE_URL → early return, no exception."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from genlab_core.monetization.affiliate_matcher import _persist_divergence

        # Must not raise
        _persist_divergence(
            niche_id="gaming",
            matcher_name="PS5 Console",
            selector_name="Xbox Game Pass Ultimate",
            selector_score=0.85,
            agreed=False,
        )

    def test_writes_canonical_slugs_not_display_names(self, monkeypatch):
        """The INSERT must use slugify_product_name output so the
        analyzer can JOIN back to bandit_arms via product__<slug>.
        Storing raw display names would re-fork the JOIN key set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        from genlab_core.monetization.affiliate_matcher import _persist_divergence

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            _persist_divergence(
                niche_id="gaming",
                matcher_name="PS5 Console",
                selector_name="Xbox Game Pass Ultimate",
                selector_score=0.85,
                agreed=False,
            )

        assert mock_cursor.execute.call_count == 1
        # Second arg is the values tuple
        values = mock_cursor.execute.call_args[0][1]
        # niche_id, matcher_slug, selector_slug, score, agreed
        assert values[0] == "gaming"
        assert values[1] == "ps5-console"
        assert values[2] == "xbox-game-pass-ultimate"
        assert values[3] == 0.85
        assert values[4] is False

    def test_empty_matcher_name_is_noop(self, monkeypatch):
        """Empty name → empty slug → skip write. Empty slugs would
        insert 'product__' rows that don't reflect real products."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        from genlab_core.monetization.affiliate_matcher import _persist_divergence

        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            _persist_divergence(
                niche_id="gaming",
                matcher_name="",
                selector_name="Xbox",
                selector_score=0.5,
                agreed=False,
            )

        # Never opened a connection because we early-returned on
        # empty slug — the ContextManager __enter__ was called if
        # we entered the with block, but no cursor was called.
        # Simpler assertion: mock_ctx.__enter__ was NEVER called.
        mock_ctx.__enter__.assert_not_called()

    def test_db_exception_swallowed_at_debug(self, monkeypatch):
        """A DB error must not propagate — journalctl log already
        captured the divergence; DB write is best-effort."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        from genlab_core.monetization.affiliate_matcher import _persist_divergence

        with patch(
            "psycopg.connect",
            side_effect=RuntimeError("db unreachable"),
        ):
            # Must not raise
            _persist_divergence(
                niche_id="gaming",
                matcher_name="PS5 Console",
                selector_name="Xbox Game Pass Ultimate",
                selector_score=0.85,
                agreed=False,
            )

    def test_agreement_flag_stored_verbatim(self, monkeypatch):
        """agreed=True must land in the INSERT as bool True (not
        1 or 'true')."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        from genlab_core.monetization.affiliate_matcher import _persist_divergence

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            _persist_divergence(
                niche_id="gaming",
                matcher_name="PS5 Console",
                selector_name="PS5 Console",
                selector_score=0.75,
                agreed=True,
            )

        values = mock_cursor.execute.call_args[0][1]
        assert values[4] is True


class TestSelectorDivergencesTableRegistered:
    """The new table must be in the storage backend's allowlist so
    future code paths that use BacklogClient don't get rejected."""

    def test_valid_tables_includes_selector_divergences(self):
        from genlab_core.storage.postgres import _VALID_TABLES

        assert "selector_divergences" in _VALID_TABLES

    def test_valid_tables_includes_affiliate_conversions(self):
        """L3 PR 10 also created affiliate_conversions but never added
        it to _VALID_TABLES. This PR fills that gap too."""
        from genlab_core.storage.postgres import _VALID_TABLES

        assert "affiliate_conversions" in _VALID_TABLES
