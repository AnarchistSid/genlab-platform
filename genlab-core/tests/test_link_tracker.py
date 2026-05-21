"""Tests for genlab_core.monetization.link_tracker."""

import unittest
from unittest.mock import MagicMock, patch


class TestLogClick(unittest.TestCase):
    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_log_click_success(self, mock_get_pg):
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_get_pg.return_value = mock_pg

        log_click(
            product_id="ps5",
            niche_id="gaming",
            network="amazon",
            affiliate_url="https://amzn.to/ps5",
        )

        mock_pg.create.assert_called_once()
        call_args = mock_pg.create.call_args[0]
        assert call_args[0] == "affiliate_clicks"
        assert call_args[1]["product_id"] == "ps5"
        assert call_args[1]["niche_id"] == "gaming"

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_log_click_no_db(self, mock_get_pg):
        from genlab_core.monetization.link_tracker import log_click

        mock_get_pg.return_value = None
        # Should not raise
        log_click(product_id="ps5", niche_id="gaming", network="amazon", affiliate_url="")

    @patch("genlab_core.monetization.link_tracker._get_pg")
    def test_log_click_db_error(self, mock_get_pg):
        from genlab_core.monetization.link_tracker import log_click

        mock_pg = MagicMock()
        mock_pg.create.side_effect = Exception("DB error")
        mock_get_pg.return_value = mock_pg

        # Should not raise — errors are caught internally
        log_click(product_id="ps5", niche_id="gaming", network="amazon", affiliate_url="")


if __name__ == "__main__":
    unittest.main()
