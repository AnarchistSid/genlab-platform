"""Pin the click → arm reward runner (L3 PR 8).

Two angles:
  1. **SQL structure invariants** — the reward SQL must:
     * Use `bandit_arms.arm_id = 'product__' || affiliate_clicks.product_id`
       (the load-bearing JOIN key from PRs 2, 3, 7)
     * Filter on `arm_type = 'product'` — never accidentally touch
       source/style/transformation arms
     * Filter on `product_id IS NOT NULL AND product_id != ''`
       — an empty product_id would match `product__` (invalid arm_id
       shape) with unpredictable results
     * Set `alpha = 1.0 + n_clicks, beta = 1.0, n_plays = n_clicks`
       (v1: click = success; L3 PR 11 escalates to conversions)

  2. **CLI behavior** — dry-run reads without writing; apply writes
     the update; DATABASE_URL missing returns 1 non-crashing.

Uses mock psycopg to avoid a real DB in unit tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "register_click_rewards.py"


def _load_script_module():
    """Load the CLI script as a module so we can inspect its constants
    + call its `main()`.

    Same pattern as `test_register_product_arms.py`."""
    spec = importlib.util.spec_from_file_location("register_click_rewards", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


class TestRewardSQL:
    """The core UPDATE SQL is what closes the reward loop. Pin its
    critical pieces so a well-meaning refactor can't silently break
    attribution."""

    def test_uses_composite_arm_id_join(self, script_module):
        """The load-bearing JOIN: bandit_arms.arm_id must be composed
        via 'product__' || affiliate_clicks.product_id. Any drift here
        makes every arm miss and rewards silently vanish."""
        sql = script_module._REWARD_SQL
        assert "'product__' || cc.product_id" in sql

    def test_filters_on_product_arm_type(self, script_module):
        """Must never touch source/style/transformation arms."""
        sql = script_module._REWARD_SQL
        assert "arm_type = 'product'" in sql

    def test_filters_empty_product_id(self, script_module):
        """Empty product_id would match arm_id='product__' — invalid
        composite shape that no arm carries. Filter it out at source."""
        sql = script_module._REWARD_SQL
        assert "product_id IS NOT NULL" in sql
        assert "product_id != ''" in sql

    def test_beta_stays_at_1_v1(self, script_module):
        """v1 reward semantics: click = success (α += n), no negative
        signal (β stays at 1). L3 PR 11 will change this once purchase
        signals are wired. Pin the v1 formula so a refactor doesn't
        accidentally shift semantics."""
        sql = script_module._REWARD_SQL
        assert "beta   = 1.0" in sql or "beta = 1.0" in sql

    def test_alpha_is_1_plus_click_count(self, script_module):
        """α = 1.0 (uniform prior) + n_clicks (observed successes).
        The '1.0 +' is critical: a fresh Beta(1,1) with 0 obs stays
        at (1,1); with 5 obs becomes (6,1)."""
        sql = script_module._REWARD_SQL
        assert "1.0 + cc.n_clicks" in sql

    def test_n_plays_matches_click_count(self, script_module):
        """n_plays should mirror the successful reward count so
        LinUCB's cold-start heuristic (n_plays >= _MIN_PLAYS_FOR_LINUCB)
        activates as clicks accumulate."""
        sql = script_module._REWARD_SQL
        assert "n_plays = cc.n_clicks" in sql

    def test_updates_updated_at(self, script_module):
        """Set updated_at = NOW() so Mission Control cards can show
        'last reward attribution' timestamps."""
        sql = script_module._REWARD_SQL
        assert "updated_at = NOW()" in sql

    def test_returning_clause_for_stats(self, script_module):
        """RETURNING lets the runner log which arms were updated
        without an extra SELECT."""
        sql = script_module._REWARD_SQL
        assert "RETURNING" in sql


class TestPreviewSQL:
    """Preview must NOT contain UPDATE — it's a read-only projection
    used in --dry-run mode. A misplaced UPDATE here would write on
    dry-run, which is the WORST kind of surprise."""

    def test_preview_is_select_only(self, script_module):
        sql = script_module._PREVIEW_SQL
        assert "SELECT" in sql
        assert "UPDATE" not in sql
        assert "INSERT" not in sql
        assert "DELETE" not in sql

    def test_preview_shows_before_after_values(self, script_module):
        """Operator running --dry-run needs to see current vs would-be
        posterior — pin that the output SELECT includes both."""
        sql = script_module._PREVIEW_SQL
        assert "current_n_plays" in sql
        assert "would_set_n_plays" in sql
        assert "current_alpha" in sql
        assert "would_set_alpha" in sql

    def test_preview_uses_same_join(self, script_module):
        """Preview must join on the SAME key as the UPDATE, else the
        dry-run output would mislead — showing X arms that would be
        updated but the real run only touches Y."""
        preview = script_module._PREVIEW_SQL
        real = script_module._REWARD_SQL
        assert "'product__' || cc.product_id" in preview
        assert "'product__' || cc.product_id" in real
        assert "arm_type = 'product'" in preview
        assert "arm_type = 'product'" in real


class TestMainCLI:
    """The `main()` entry point's error paths."""

    def test_missing_database_url_returns_1(self, script_module, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr("sys.argv", ["register_click_rewards.py"])
        result = script_module.main()
        assert result == 1

    def test_dry_run_calls_preview_sql(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_click_rewards.py", "--dry-run"])

        # Mock psycopg.connect to return a mock cursor
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            result = script_module.main()

        assert result == 0
        # cursor.execute called once — with the PREVIEW sql, not the UPDATE
        assert mock_cursor.execute.call_count == 1
        called_sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE" not in called_sql
        assert "SELECT" in called_sql

    def test_apply_calls_update_sql(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_click_rewards.py", "--apply"])

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("gaming", "product__ps5-console", 5),
            ("movies", "product__amazon-prime-video", 12),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            result = script_module.main()

        assert result == 0
        # cursor.execute called once with the UPDATE sql
        assert mock_cursor.execute.call_count == 1
        called_sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE" in called_sql
        # commit was called
        mock_conn.commit.assert_called_once()

    def test_sql_error_returns_2(self, script_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr("sys.argv", ["register_click_rewards.py", "--apply"])

        with patch("psycopg.connect", side_effect=RuntimeError("db unreachable")):
            result = script_module.main()

        assert result == 2

    def test_apply_and_dry_run_together_is_dry_run(self, script_module, monkeypatch):
        """If both flags passed, dry_run wins (safer default)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
        monkeypatch.setattr(
            "sys.argv",
            ["register_click_rewards.py", "--apply", "--dry-run"],
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
        # Preview SQL was used (dry_run won)
        assert "UPDATE" not in called_sql


class TestIdempotence:
    """The script must be safe to re-run — recomputing state from
    ``affiliate_clicks`` truth means the second run doesn't
    double-count clicks."""

    def test_reward_formula_is_pure_function_of_click_count(self, script_module):
        """Pin that the update sets α = 1 + n_clicks EXPLICITLY —
        not α = α + delta. The latter would double-count on rerun."""
        sql = script_module._REWARD_SQL
        # These would indicate incremental (non-idempotent) update:
        assert "bandit_arms.alpha +" not in sql
        assert "ba.alpha +" not in sql
        assert "alpha + 1" not in sql
        # Explicit set to computed value = idempotent
        assert "alpha  = 1.0 + cc.n_clicks" in sql or "alpha = 1.0 + cc.n_clicks" in sql
