"""Tests for the SharePoint→Postgres migration SQL builder (R-63).

Focus: the ON CONFLICT target must match the actual unique constraint per table.
Migration i9d0e1f2g3h4 dropped bandit_arms' single-column UNIQUE(arm_id) for a
composite UNIQUE(niche_id, arm_id); emitting `ON CONFLICT (arm_id)` against that
schema raises Postgres 42P10, so the builder must use the composite key.
"""

from __future__ import annotations

from genlab_core.storage.migrate_table import _build_insert_sql, _unique_key_cols


class TestUniqueKeyCols:
    def test_composite_key_for_bandit_arms(self):
        assert _unique_key_cols("bandit_arms") == ("niche_id", "arm_id")

    def test_single_key_normalized_to_tuple(self):
        assert _unique_key_cols("stories") == ("story_id",)
        assert _unique_key_cols("blueprints") == ("candidate_id",)

    def test_no_key_returns_none(self):
        assert _unique_key_cols("analytics") is None
        assert _unique_key_cols("pending_engagement") is None


class TestOnConflictTarget:
    def test_bandit_arms_uses_composite_conflict(self):
        sql, _ = _build_insert_sql(
            "bandit_arms",
            {"niche_id": "gaming", "arm_id": "style:gaming:hype", "alpha": 1.0, "beta": 1.0},
        )
        assert "ON CONFLICT (niche_id, arm_id) DO NOTHING" in sql
        # The defunct single-column target must NOT be emitted.
        assert "ON CONFLICT (arm_id)" not in sql

    def test_single_key_conflict_preserved(self):
        sql, _ = _build_insert_sql(
            "stories", {"niche_id": "gaming", "story_id": "s1", "title": "t"}
        )
        assert "ON CONFLICT (story_id) DO NOTHING" in sql

    def test_no_key_falls_back_to_pk(self):
        sql, _ = _build_insert_sql("analytics", {"niche_id": "gaming", "sp_id": "x"})
        assert "ON CONFLICT (id) DO NOTHING" in sql

    def test_composite_falls_back_when_a_key_column_is_absent(self):
        # If the record lacks one of the composite key columns, the builder must
        # not emit a partial ON CONFLICT — it falls back to the PK.
        sql, _ = _build_insert_sql("bandit_arms", {"arm_id": "style:gaming:hype", "alpha": 1.0})
        assert "ON CONFLICT (id) DO NOTHING" in sql
