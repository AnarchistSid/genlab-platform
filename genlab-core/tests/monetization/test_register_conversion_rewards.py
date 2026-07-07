"""Pin the conversion-aware reward runner (L3 PR 11, 2026-07-07).

The critical invariants:

  1. **SQL structure** — same JOIN key as L3 PR 8
     ('product__' || product_id), filter on arm_type='product',
     empty product_id excluded, updated_at bumped, RETURNING clause.

  2. **Bernoulli semantics** — when affiliate_conversions has a row
     for (niche, product_slug):
       alpha = 1 + n_purchases
       beta = 1 + max(0, n_clicks - n_purchases)
     This means: purchases are successes, clicks-without-purchase
     are failures. Return items subtract from purchases via
     `SUM(GREATEST(0, items_shipped - items_returned))`.

  3. **Fallback semantics** — when NO conversions exist for a slug:
       alpha = 1 + n_clicks
       beta = 1 (unchanged)
     This is byte-identical to L3 PR 8's click-only reward, so
     the runner is a strict superset — no regression from adopting.

  4. **Idempotence** — formulas are pure recomputes from truth;
     no `alpha = alpha + delta` incremental writes.

  5. **CLI** — dry-run reads without writing; apply writes; missing
     DATABASE_URL exits 1; SQL exception exits 2.

Uses mock psycopg to avoid a live DB.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "register_conversion_rewards.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("register_conversion_rewards", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


import pytest


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


class TestSharedInvariants:
    """Invariants shared with L3 PR 8. These MUST hold — the L3 PR 11
    runner is a strict superset of L3 PR 8 (identical when
    affiliate_conversions has no rows for a slug)."""

    def test_uses_composite_arm_id_join(self, script_module):
        assert "'product__' || cc.product_id" in script_module._REWARD_SQL

    def test_filters_on_product_arm_type(self, script_module):
        assert "arm_type = 'product'" in script_module._REWARD_SQL

    def test_filters_empty_product_id(self, script_module):
        sql = script_module._REWARD_SQL
        assert "product_id IS NOT NULL" in sql
        assert "product_id != ''" in sql

    def test_n_plays_matches_click_count(self, script_module):
        """n_plays tracks n_clicks regardless of whether we're on
        Bernoulli or click-only branch — clicks are the "plays"."""
        assert "n_plays = cc.n_clicks" in script_module._REWARD_SQL

    def test_updated_at_bumped(self, script_module):
        assert "updated_at = NOW()" in script_module._REWARD_SQL

    def test_returning_clause_for_stats(self, script_module):
        assert "RETURNING" in script_module._REWARD_SQL


class TestBernoulliBranch:
    """When conversions exist for a slug, use Bernoulli reward:
    alpha = 1 + n_purchases
    beta  = 1 + max(0, n_clicks - n_purchases)
    """

    def test_alpha_prefers_purchases_when_available(self, script_module):
        """COALESCE picks n_purchases when non-null, else n_clicks —
        so alpha = 1 + n_purchases in Bernoulli branch."""
        assert "1.0 + COALESCE(cv.n_purchases, cc.n_clicks)" in script_module._REWARD_SQL

    def test_beta_uses_max_zero_diff_in_bernoulli(self, script_module):
        """GREATEST(0, n_clicks - n_purchases) prevents negative β when
        Amazon reports more returns than ships in a window (rare but
        happens across month boundaries)."""
        assert "GREATEST(0, cc.n_clicks - cv.n_purchases)" in script_module._REWARD_SQL

    def test_return_items_subtract_from_purchases(self, script_module):
        """Item returns don't count as purchases. Aggregate uses
        SUM(GREATEST(0, items_shipped - items_returned))."""
        assert "SUM(GREATEST(0, items_shipped - items_returned))" in script_module._REWARD_SQL

    def test_joins_affiliate_conversions(self, script_module):
        assert "affiliate_conversions" in script_module._REWARD_SQL

    def test_joins_on_niche_and_slug(self, script_module):
        """The conversion JOIN key must be BOTH (niche_id, product_slug)
        — matching on slug alone would leak reward across niches
        (a PS5 click in gaming and one in movies share the slug but
        must NOT share the arm's posterior)."""
        sql = script_module._REWARD_SQL
        assert "cv.niche_id = cc.niche_id" in sql
        assert "cv.product_slug = cc.product_id" in sql


class TestClickOnlyFallback:
    """When conversions don't exist for a slug, fall back to L3 PR 8's
    click-only reward. The CASE expression handles the branch."""

    def test_beta_stays_1_when_no_conversions(self, script_module):
        """Fallback branch: `WHEN cv.n_purchases IS NULL THEN 1.0`."""
        assert "WHEN cv.n_purchases IS NULL THEN 1.0" in script_module._REWARD_SQL

    def test_uses_left_join_for_optional_conversions(self, script_module):
        """LEFT JOIN so arms without conversions still get updated
        (falls through the CASE to click-only branch)."""
        assert "LEFT JOIN" in script_module._REWARD_SQL

    def test_click_only_alpha_matches_l3_pr8(self, script_module):
        """When n_purchases is NULL, COALESCE picks n_clicks — so
        alpha = 1 + n_clicks, exactly what L3 PR 8 shipped."""
        # Same COALESCE expression proven correct above; this
        # test documents the L3 PR 8 compatibility semantic.
        assert "1.0 + COALESCE(cv.n_purchases, cc.n_clicks)" in script_module._REWARD_SQL


class TestIdempotence:
    """Pure recomputes — safe to rerun."""

    def test_no_incremental_updates(self, script_module):
        """`alpha = alpha + delta` would double-count on rerun.
        We use `alpha = expression` (pure set)."""
        sql = script_module._REWARD_SQL
        assert "bandit_arms.alpha +" not in sql
        assert "ba.alpha +" not in sql

    def test_no_incremental_beta(self, script_module):
        sql = script_module._REWARD_SQL
        assert "bandit_arms.beta +" not in sql
        assert "ba.beta +" not in sql


class TestPreviewSQL:
    """Preview must be SELECT-only. A misplaced UPDATE here would
    write on dry-run mode."""

    def test_preview_no_update_delete_insert(self, script_module):
        sql = script_module._PREVIEW_SQL
        assert "SELECT" in sql
        # Some SELECT queries use 'UPDATE' in comments; ours doesn't,
        # but the invariant is no UPDATE keyword.
        assert "UPDATE" not in sql
        assert "INSERT" not in sql
        assert "DELETE" not in sql

    def test_preview_shows_before_after_alpha_beta(self, script_module):
        sql = script_module._PREVIEW_SQL
        assert "current_alpha" in sql
        assert "would_set_alpha" in sql
        assert "current_beta" in sql
        assert "would_set_beta" in sql

    def test_preview_surfaces_bernoulli_indicator(self, script_module):
        """Preview must expose `n_purchases` so operator can see
        which arms are on Bernoulli vs click-only."""
        assert "cv.n_purchases" in script_module._PREVIEW_SQL


class TestCLI:
    """Entry-point behavior — missing DB, dry-run vs apply, SQL error."""

    def test_missing_database_url_returns_1(self, script_module, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr("sys.argv", ["register_conversion_rewards.py"])
        result = script_module.main()
        assert result == 1

    def test_dry_run_uses_preview_sql(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_conversion_rewards.py", "--dry-run"])

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            result = script_module.main()

        assert result == 0
        assert mock_cursor.execute.call_count == 1
        called_sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE" not in called_sql
        assert "SELECT" in called_sql

    def test_apply_uses_update_sql_and_commits(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_conversion_rewards.py", "--apply"])

        mock_cursor = MagicMock()
        # Simulate one Bernoulli row + one click-only row
        mock_cursor.fetchall.return_value = [
            ("gaming", "product__ps5-console", 5, 3.0, 3.0, 2),  # Bernoulli
            ("movies", "product__amazon-prime-video", 12, 13.0, 1.0, None),  # click-only
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            result = script_module.main()

        assert result == 0
        assert mock_cursor.execute.call_count == 1
        called_sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE" in called_sql
        mock_conn.commit.assert_called_once()

    def test_sql_error_returns_2(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_conversion_rewards.py", "--apply"])

        with patch("psycopg.connect", side_effect=RuntimeError("boom")):
            result = script_module.main()

        assert result == 2

    def test_apply_and_dry_run_together_is_dry_run(self, script_module, monkeypatch):
        """Safer default when both flags passed."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr(
            "sys.argv",
            ["register_conversion_rewards.py", "--apply", "--dry-run"],
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            script_module.main()

        called_sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE" not in called_sql
