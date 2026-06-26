"""Source-level pin for scripts/backfill_engagement_rate.sql.

Ensures the v2 jsonb-aware SQL is not silently reverted to the v1
wide-column shape (which fails on prod with `column "engagement"
does not exist`). Verified-on-prod scope: ~33 rows updated on
2026-06-26.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "scripts" / "backfill_engagement_rate.sql"


def _read_sql() -> str:
    assert SQL_PATH.exists(), f"missing {SQL_PATH}"
    return SQL_PATH.read_text()


def test_scopes_to_composite_metric_type():
    """Won't touch insights/window rows — composite only."""
    sql = _read_sql()
    assert "metric_type = 'composite'" in sql


def test_uses_jsonb_extraction_for_engagement():
    """Proves SQL reads from extra jsonb, not a wide-column."""
    sql = _read_sql()
    assert "extra->>'engagement'" in sql


def test_does_not_use_wide_column_engagement_predicate():
    """Guards against accidental revert to v1 wide-column shape."""
    sql = _read_sql()
    assert "WHERE engagement = 0" not in sql


def test_filters_to_bug_onset_date():
    """Bug introduced 2026-06-09 (commit f32b6189); backfill scoped accordingly."""
    sql = _read_sql()
    assert "'2026-06-09'" in sql
