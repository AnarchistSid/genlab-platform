"""Pin L3 PR 7 additions to genlab_core.monetization.link_tracker.

The migration ``a8w9x0y1z2a3`` (L3 PR 1) promoted
``affiliate_clicks.blueprint_id`` from a legacy JSONB-extra field to a
proper UUID column with an FK to blueprints. Without the sanitization
added in L3 PR 7, every click without a ``?bp=`` param would fail
INSERT after that migration deploys — INTs and empty strings can't
land in a UUID column.

Additionally, L3 PR 1 added ``affiliate_clicks.commission_pct``. The
snapshot must be populated at click time so retroactive catalog rate
changes don't invalidate historical reward attribution (L3 PR 8's
reward wire reads this column, not the current catalog).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSanitizeBlueprintId:
    """The private helper the tracker uses to guard UUID FK inserts."""

    def test_valid_uuid_passthrough(self):
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        good = "c9d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"
        assert sanitize_blueprint_id(good) == good

    def test_valid_uuid_uppercase_passthrough(self):
        """UUIDs are case-insensitive by spec — accept upper-case hex."""
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        good = "C9D4E5F6-A7B8-4C9D-0E1F-2A3B4C5D6E7F"
        assert sanitize_blueprint_id(good) == good

    def test_empty_string_returns_none(self):
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        assert sanitize_blueprint_id("") is None

    def test_whitespace_returns_none(self):
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        assert sanitize_blueprint_id("   ") is None
        assert sanitize_blueprint_id("\t") is None

    def test_none_returns_none(self):
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        assert sanitize_blueprint_id(None) is None

    def test_arbitrary_string_returns_none(self):
        """A malformed UUID or arbitrary string must return None. This
        blocks probe fuzzing — an attacker hitting
        /links/go/ps5-console?bp=garbage cannot spam INSERT failures."""
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        assert sanitize_blueprint_id("garbage") is None
        assert sanitize_blueprint_id("42") is None
        assert sanitize_blueprint_id("not-a-uuid-at-all") is None

    def test_uuid_without_hyphens_returns_none(self):
        """The DB column is UUID which strictly requires hyphenated
        form. A 32-hex string without hyphens is not a valid UUID
        literal for psycopg."""
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        # 32 hex chars but no hyphens
        assert sanitize_blueprint_id("c9d4e5f6a7b84c9d0e1f2a3b4c5d6e7f") is None

    def test_leading_trailing_whitespace_stripped_then_validated(self):
        """A UUID passed with surrounding whitespace should be accepted."""
        from genlab_core.monetization.link_tracker import sanitize_blueprint_id

        good = "c9d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"
        assert sanitize_blueprint_id(f"  {good}  ") == good


class TestLogClickL3Additions:
    """Pin the L3 PR 7 wire: empty bp omitted, commission_pct written."""

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_empty_bp_omitted_from_insert(self, mock_get_pg):
        """Historic caller passes blueprint_id="". The tracker must
        omit it entirely from the record dict so the UUID column stays
        NULL (empty string would fail INSERT after L3 PR 1)."""
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
            blueprint_id="",
        )
        record = mock_pg.create.call_args[0][1]
        assert "blueprint_id" not in record

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_valid_bp_written(self, mock_get_pg):
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg
        good_uuid = "c9d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
            blueprint_id=good_uuid,
        )
        record = mock_pg.create.call_args[0][1]
        assert record.get("blueprint_id") == good_uuid

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_malformed_bp_omitted(self, mock_get_pg):
        """A malformed bp (e.g. attacker probe) is omitted."""
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
            blueprint_id="garbage-not-a-uuid",
        )
        record = mock_pg.create.call_args[0][1]
        assert "blueprint_id" not in record

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_commission_pct_written(self, mock_get_pg):
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
            commission_pct=3.0,
        )
        record = mock_pg.create.call_args[0][1]
        assert record.get("commission_pct") == pytest.approx(3.0)

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_commission_pct_none_omitted(self, mock_get_pg):
        """Legacy callers not passing commission_pct → column omitted,
        DB stores NULL. Backward-compat safety."""
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
        )
        record = mock_pg.create.call_args[0][1]
        assert "commission_pct" not in record

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_commission_pct_int_coerced_to_float(self, mock_get_pg):
        """Catalog may parse int commission (e.g. 3) — coerce to float
        so DB float column always sees the same type."""
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5-console",
            niche_id="gaming",
            network="amazon_us",
            affiliate_url="https://amzn.to/x",
            commission_pct=3,  # int, not float
        )
        record = mock_pg.create.call_args[0][1]
        assert isinstance(record["commission_pct"], float)
        assert record["commission_pct"] == pytest.approx(3.0)


class TestPromotedColumns:
    """The `commission_pct` column must be in PROMOTED_COLUMNS so
    ``_split_fields`` routes it to a real column instead of dumping it
    into ``extra`` JSONB."""

    def test_commission_pct_promoted_for_affiliate_clicks(self):
        from genlab_core.storage.postgres import PROMOTED_COLUMNS

        assert "commission_pct" in PROMOTED_COLUMNS["affiliate_clicks"]
